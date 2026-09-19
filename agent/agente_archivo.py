"""
agent/agente_archivo.py — FAMILIARES EN EL ARCHIVO VASCO CON IA (BLOQUE 5).

QUÉ HACE (la opción 1.3 del menú)
---------------------------------
Coge la gente conocida de la línea de Álava y busca FAMILIARES en el ÚNICO
sitio donde están de verdad: el buscador de registros sacramentales del Archivo
Histórico Diocesano de Vitoria (artxibo.euskadi.eus, Álava, 1481-1900). Ni
Tavily ni web abierta: ahí no hay partidas del siglo XIX (medido en este
proyecto: 449 extracciones de la web, 0 antepasados).

CÓMO BUSCA (el truco, que aportó el usuario y funciona)
-------------------------------------------------------
1. Se busca por el PRIMER APELLIDO ('Saenz de Navarrete'), NO por el nombre
   completo: el buscador devuelve la lista larga... y ahí están los familiares
   (hermanos, tíos, primos) que con una búsqueda estrecha no saldrían.
2. De cada fila se sacan TODOS los apellidos que aparecen —los del nacido y los
   de sus PADRES— y se vuelven a buscar: así entran las líneas de las MUJERES
   (Dopico, Guzmán, Tellaeche…), que son la mitad del árbol.
3. De los registros que tocan a la familia se abre la FICHA (la página del
   registro): es la que dice el nombre del nacido y el de sus padres, y trae el
   enlace al libro digitalizado.
4. Cada tanda se le pasa a la IA (deepseek v4.1-flash, el modelo fino que ya
   usa el proyecto) para que diga QUÉ filas son familiares y de quién, y QUÉ
   apellidos buscar después.

QUÉ NO HACE / REGLAS QUE NO SE RELAJAN
--------------------------------------
- La IA NO escribe nombres: señala el NÚMERO de la fila que ha leído, y el
  nombre, el año y la cita los copia el programa de la fila REAL. Un número que
  no existe y un apellido que no aparece en la lista se descartan solos.
- La IA propone, las reglas certifican: cada familiar lleva el sello de siempre
  (≥2 datos independientes). Ver `sello()`.
- No toca el árbol (`familia_conocida.json`) ni los `arbol_*`. No sale de Álava.
- Topes de IA, consultas y fichas (config.AGENTE_*): se para solo y guarda.

El estado (`agente_alava.json`) es REANUDABLE y el informe
(`familiares_archivo_vasco.md`) es la lista de familiares para leer. Los dos
fuera de git: son datos de familia en un repo público.
"""

from __future__ import annotations

import json
import random
import time
from datetime import datetime
from pathlib import Path

from config import (AGENTE_DELAY, AGENTE_FICHAS_POR_APELLIDO, AGENTE_FILAS_POR_LLAMADA,
                    AGENTE_INFORME, AGENTE_MAX_CONSULTAS, AGENTE_MAX_FICHAS,
                    AGENTE_MAX_LLM, AGENTE_MAX_TOKENS_SALIDA, AGENTE_VENTANA,
                    AGENTE_VENTANA_MARGEN, APELLIDOS_COMUNES, BASE_DIR,
                    MODELO_FASE2, escribir_con_backup, mismo_apellido,
                    mismo_nombre, normalizar)
from utils import ui

# Papel de cada persona en la lista
PAPEL_LINEA = "línea"            # antepasado directo
PAPEL_COLATERAL = "colateral"    # hermano/a, tío/a, primo/a: pariente, no línea
# Sello de cada familiar (lo que dicen los DATOS, no la IA)
SELLO_PARTIDA = "partida"        # la propia partida lo dice (nombra a un conocido)
SELLO_RESERVAS = "reservas"      # ≥2 datos independientes, sin la partida
SELLO_PISTA = "pista"            # 1 dato (o solo lo que dice la IA)
NIVEL_ARBOL = "arbol"            # ya lo sabíamos (del árbol): no es un hallazgo
ETIQUETA_SELLO = {
    SELLO_PARTIDA: "compatible — lo dice la partida",
    SELLO_RESERVAS: "compatible con reservas — 2 datos",
    SELLO_PISTA: "pista — 1 dato",
}
_ORDEN_SELLO = {SELLO_PARTIDA: 0, SELLO_RESERVAS: 1, SELLO_PISTA: 2}

# Tipos de sacramento que se consultan por apellido. El BAUTISMO trae al nacido
# con sus padres; el MATRIMONIO trae a los dos cónyuges (y engancha familias).
TIPOS_BUSQUEDA = ("bautismo", "matrimonio")


# ============================ PIEZAS PURAS =================================

def apellido_util(apellido: str) -> bool:
    """¿Sirve este apellido para buscar? (fuera los vacíos, los de 1-2 letras
    y los comunes: 'García' sale en miles de filas y no identifica a nadie)."""
    apellido = (apellido or "").strip()
    return len(apellido) > 2 and normalizar(apellido) not in APELLIDOS_COMUNES


def clave_apellido(apellido: str) -> str:
    return normalizar(apellido)


def clave_persona(nombre: str, apellido1: str = "", anio=None,
                  id_=None) -> str:
    """Clave estable de una persona de la lista (nombre + primer apellido +
    año; el id del registro solo si no hay año, para no pisar a dos homónimos)."""
    base = normalizar(f"{nombre} {apellido1}".strip())
    if anio:
        return f"{base}::{anio}"
    if id_:
        return f"{base}::{id_}"
    return base


def apellidos_de_registro(fila: dict) -> list[str]:
    """TODOS los apellidos que aparecen en una fila, empezando por los de la
    madre (es lo que abre las líneas de las mujeres, que es lo que se busca).

    Se cogen de la persona del registro, de sus padres y del cónyuge, y se
    devuelven sin repetir y solo los que sirven para buscar.
    """
    salida: list[str] = []
    for rol in ("madre", "padre", "persona", "conyuge"):
        persona = fila.get(rol) or {}
        for clave in ("apellido1", "apellido2"):
            apellido = (persona.get(clave) or "").strip()
            if not apellido_util(apellido):
                continue
            if clave_apellido(apellido) not in {clave_apellido(a) for a in salida}:
                salida.append(apellido)
    return salida


def apellidos_conocidos(estado: dict) -> set:
    """Apellidos de la familia ya identificada (incluidos los de los padres de
    cada uno): es contra esto contra lo que se mide si una fila es de los
    nuestros o un homónimo."""
    vistos: set = set()
    for persona in estado.get("personas", {}).values():
        for apellido in (persona.get("apellidos") or []):
            vistos.add(clave_apellido(apellido))
    return vistos


def anios_conocidos(estado: dict) -> list[int]:
    return [int(p["anio"]) for p in estado.get("personas", {}).values()
            if p.get("anio")]


def ventana_familia(estado: dict) -> tuple[int | None, int | None]:
    """Ventana de años en la que vive la familia conocida: (el más antiguo − 90,
    el más moderno + 20). Buscar un apellido en TODA la ventana 1481-1900 trae
    siglos de homónimos (y el portal ordena ASCENDENTE: los 200 primeros serían
    del siglo XVI). Se busca primero por la zona de la familia."""
    anios = anios_conocidos(estado)
    if not anios:
        return None, None
    margen_antiguo, margen_moderno = AGENTE_VENTANA_MARGEN
    return min(anios) - margen_antiguo, max(anios) + margen_moderno


def perfil_para_la_ia(estado: dict, maximo: int = 30) -> str:
    """La familia ya identificada, en texto, para dársela a la IA como contexto."""
    personas = sorted(estado.get("personas", {}).values(),
                      key=lambda p: (-(p.get("anio") or 0), p.get("nombre", "")))
    if not personas:
        return ("FAMILIA IDENTIFICADA: (todavía no hay nadie identificado; "
                "empieza por las filas que te doy).")
    lineas = []
    for persona in personas[:maximo]:
        padres = " y ".join(p for p in (persona.get("padres_texto") or []) if p)
        trozo = (f"- {persona.get('nombre', '')} "
                 f"{persona.get('apellido1', '')} "
                 f"{persona.get('apellido2', '')}").strip()
        if persona.get("anio"):
            trozo += f", {persona['anio']}"
        if persona.get("parroquia") or persona.get("municipio"):
            trozo += (f" ({', '.join(p for p in (persona.get('parroquia'),
                                                 persona.get('municipio'))
                                    if p)})")
        if padres:
            trozo += f". Hijo/a de {padres}"
        if persona.get("parentesco"):
            trozo += f". Parentesco: {persona['parentesco']}"
        lineas.append(trozo)
    return "FAMILIA IDENTIFICADA:\n" + "\n".join(lineas)


def lineas_para_la_ia(filas: list[dict]) -> str:
    """Las filas del buscador numeradas, con su cita, para la IA.

    Se usa el texto que ya construye el conector (`fila['texto']`), que lleva la
    fecha, el nombre, los padres, la parroquia y la cita del archivo.
    """
    partes = []
    for i, fila in enumerate(filas, 1):
        partes.append(f"{i}. {fila.get('texto') or _texto_de_fila(fila)}")
    return "\n".join(partes)


def _texto_de_fila(fila: dict) -> str:
    """Texto mínimo de una fila (para los tests y por si el conector no lo trae)."""
    quien = (fila.get("persona") or {}).get("completo", "")
    return (f"{fila.get('tipo', 'registro')} {fila.get('fecha') or 'sin fecha'}. "
            f"{quien}.")


def _persona_de(fila: dict, rol: str) -> dict:
    return fila.get(rol) or {}


def _cuadra_con(persona: dict, nombre: str, apellido: str) -> bool:
    return (mismo_nombre(persona.get("nombre", ""), nombre or "")
            and mismo_apellido(persona.get("apellido1", ""), apellido or ""))


def enlaza_con_la_familia(fila: dict, estado: dict) -> tuple[str, str]:
    """¿La propia fila NOMBRA a alguien ya identificado? Devuelve (a quién, en
    qué papel). Es el dato más fuerte que hay: lo dice el documento, no una
    corazonada. Un hermano entra por aquí (su partida nombra a los mismos
    padres), y también los hijos de un conocido.

    Además del padre/madre, se mira:
      * que la fila sea de un HIJO de un conocido (el conocido sale de padre);
      * que la fila sea de un MATRIMONIO en el que el conocido es un cónyuge.
    """
    for rol, etiqueta in (("padre", "padre"), ("madre", "madre"),
                          ("conyuge", "cónyuge")):
        persona = _persona_de(fila, rol)
        if not persona.get("nombre"):
            continue
        for conocida in estado.get("personas", {}).values():
            if _cuadra_con(persona, conocida.get("nombre", ""),
                           conocida.get("apellido1", "")):
                return (f"{persona.get('completo') or persona.get('nombre')} "
                        f"({etiqueta} de la familia, ya identificado)", etiqueta)
    return "", ""


def sello(fila: dict, estado: dict) -> tuple[str, float, list[str]]:
    """Qué se puede decir de una fila SOLO con datos: (sello, nº de datos, por qué).

    La regla de siempre (≥2 datos INDEPENDIENTES):
      * La partida nombra a alguien ya identificado ............... 2 datos
      * Comparte los DOS apellidos con la familia ................ 1 dato
      * Comparte uno ............................................. 0,5 datos
      * El año cae en la ventana de la familia ................... 1 dato
      * Misma parroquia o municipio que los conocidos ............ 1 dato
    """
    datos = 0.0
    motivos: list[str] = []
    enlace, etiqueta = enlaza_con_la_familia(fila, estado)
    if enlace:
        datos += 2
        motivos.append(f"la partida nombra a {enlace}")
    conocidos = apellidos_conocidos(estado)
    suyos = [clave_apellido((_persona_de(fila, "persona").get(clave) or ""))
             for clave in ("apellido1", "apellido2")]
    suyos = [a for a in suyos if a]
    casan = [a for a in suyos if a in conocidos]
    if len(casan) >= 2:
        datos += 1
        motivos.append("comparte los dos apellidos con la familia")
    elif casan:
        datos += 0.5
        motivos.append(f"comparte el apellido {casan[0]}")
    anio = fila.get("anio")
    ini, fin = ventana_familia(estado)
    if anio and ini and fin and ini <= int(anio) <= fin:
        datos += 1
        motivos.append(f"el año {anio} cae en la ventana de la familia")
    lugar = {normalizar(fila.get("parroquia", "")),
             normalizar(fila.get("municipio", ""))}
    lugar.discard("")
    for conocida in estado.get("personas", {}).values():
        suyo = {normalizar(conocida.get("parroquia", "")),
                normalizar(conocida.get("municipio", ""))}
        suyo.discard("")
        if lugar & suyo:
            datos += 1
            motivos.append(f"mismo sitio que {conocida.get('nombre', '')} "
                           f"({', '.join(sorted(lugar & suyo))})")
            break
    if enlace:
        return SELLO_PARTIDA, datos, motivos
    if datos >= 2:
        return SELLO_RESERVAS, datos, motivos
    return SELLO_PISTA, datos, motivos


def interpretar_respuesta(resp: dict, lote: list[dict],
                          estado: dict) -> dict:
    """Respuesta de la IA → familiares REALES y apellidos que SÍ existen.

    Esto es el cortafuegos contra los inventos: la IA solo aporta el número de
    fila, el parentesco, su confianza y el motivo. El nombre, el año, la cita y
    el enlace salen de la fila de verdad. Un número fuera de la lista o un
    apellido que no esté en las filas se tira y se cuenta.
    """
    resp = resp if isinstance(resp, dict) else {}
    parientes: list[dict] = []
    descartados = 0
    for item in resp.get("parientes") or []:
        if not isinstance(item, dict):
            descartados += 1
            continue
        try:
            numero = int(item.get("fila"))
        except (TypeError, ValueError):
            descartados += 1
            continue
        if not 1 <= numero <= len(lote):
            descartados += 1
            continue
        fila = lote[numero - 1]
        nivel, datos, motivos = sello(fila, estado)
        motivos.append(f"la IA: {str(item.get('por_que') or '')[:200]}")
        parientes.append({
            "fila": fila,
            "parentesco": str(item.get("parentesco") or "")[:120],
            "de_quien": str(item.get("de_quien") or "")[:120],
            "confianza_ia": str(item.get("confianza") or "")[:16],
            "nivel": nivel, "datos": datos, "motivos": motivos,
        })
    vistos = {clave_apellido(a) for fila in lote
              for a in apellidos_de_registro(fila)}
    por_clave = {clave_apellido(a): a for fila in lote
                 for a in apellidos_de_registro(fila)}
    apellidos: list[str] = []
    for apellido in resp.get("apellidos") or []:
        clave = clave_apellido(str(apellido))
        if clave in vistos and apellido_util(str(apellido)):
            if clave not in {clave_apellido(a) for a in apellidos}:
                apellidos.append(por_clave.get(clave, str(apellido).strip()))
        else:
            descartados += 1
    return {"parientes": parientes, "apellidos": apellidos,
            "descartados": descartados,
            "por_que_esos": str(resp.get("por_que_esos_apellidos") or "")[:300]}


# ============================== ESTADO =====================================

def estado_vacio(rama: str = "alava") -> dict:
    ahora = datetime.now().isoformat(timespec="seconds")
    return {
        "rama": rama,
        "creado": ahora,
        "actualizado": ahora,
        "personas": {},          # clave -> persona de la familia
        "apellidos": {},         # clave -> {"apellido", "estado", ...}
        "cola": [],              # claves de apellidos pendientes
        "consultas": 0,
        "fichas": 0,
        "llamadas_llm": 0,
        "coste_llm": 0.0,
        "buscados": 0,
        "siguientes_ia": [],
        "notas": [],
    }


def cargar_estado(base: Path | None = None, rama: str = "alava") -> dict:
    """Estado guardado, o uno nuevo. Si el fichero está roto, se empieza de
    cero (no se pierde nada: el informe se reescribe entero cada vez)."""
    base = base if base is not None else BASE_DIR
    ruta = Path(base) / AGENTE_VENTANA
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return estado_vacio(rama)
    if not isinstance(datos, dict) or "personas" not in datos:
        return estado_vacio(rama)
    for clave, valor in estado_vacio(rama).items():
        datos.setdefault(clave, valor)
    return datos


def guardar_estado(estado: dict, base: Path | None = None) -> str | None:
    base = base if base is not None else BASE_DIR
    estado["actualizado"] = datetime.now().isoformat(timespec="seconds")
    return escribir_con_backup(Path(base) / AGENTE_VENTANA,
                               json.dumps(estado, ensure_ascii=False, indent=2))


def apuntar_apellido(estado: dict, apellido: str, de_quien: str = "") -> bool:
    """Añade un apellido a la cola de búsqueda. True si es nuevo.

    Se guarda DE DÓNDE sale ('de una fila de 1885', 'lo propone la IA'): así el
    informe puede explicar por qué se buscó lo que se buscó.
    """
    if not apellido_util(apellido):
        return False
    clave = clave_apellido(apellido)
    existente = estado["apellidos"].get(clave)
    if existente is not None:
        if de_quien and de_quien not in (existente.get("de") or []):
            existente.setdefault("de", []).append(de_quien)
        return False
    estado["apellidos"][clave] = {"apellido": apellido.strip(),
                                  "estado": "pendiente", "de": [de_quien] if
                                  de_quien else []}
    estado["cola"].append(clave)
    return True


def anotar_persona(estado: dict, fila: dict, *, parentesco: str = "",
                   nivel: str = "", datos: float = 0.0,
                   motivos: list[str] | None = None, papel: str = PAPEL_LINEA,
                   de_quien: str = "", confianza_ia: str = "",
                   ficha: dict | None = None) -> str:
    """Apunta (o completa) una persona de la familia. Devuelve su clave."""
    persona_fila = _persona_de(fila, "persona")
    nombre = persona_fila.get("nombre", "")
    apellido1 = persona_fila.get("apellido1", "")
    apellido2 = persona_fila.get("apellido2", "")
    anio = fila.get("anio")
    clave = clave_persona(nombre, apellido1, anio, fila.get("id"))
    previa = estado["personas"].get(clave)
    padres_texto = [p.get("completo") or p.get("nombre")
                    for p in (_persona_de(fila, "padre"),
                              _persona_de(fila, "madre")) if p.get("nombre")]
    apellidos = list(dict.fromkeys(apellidos_de_registro(fila)))
    nueva = {
        "nombre": nombre, "apellido1": apellido1, "apellido2": apellido2,
        "anio": anio, "fecha": fila.get("fecha", ""),
        "tipo": fila.get("tipo", ""), "id": fila.get("id"),
        "url": fila.get("url", ""),
        "parroquia": fila.get("parroquia", ""),
        "municipio": fila.get("municipio") or fila.get("localidad", ""),
        "diocesis": fila.get("diocesis", ""),
        "padres_texto": padres_texto, "apellidos": apellidos,
        "conyuge": (_persona_de(fila, "conyuge").get("completo") or ""),
        "cita": ", ".join(trozo for trozo in (
            f"fondo {fila.get('fondo')}" if fila.get("fondo") else "",
            f"sig. {fila.get('signatura')}" if fila.get("signatura") else "",
            f"folio {fila.get('folio')}" if fila.get("folio") else "") if trozo),
        "folio": fila.get("folio", ""), "signatura": fila.get("signatura", ""),
        "parentesco": parentesco, "papel": papel, "de_quien": de_quien,
        "nivel": nivel or SELLO_PISTA, "datos": datos,
        "motivos": motivos or [], "confianza_ia": confianza_ia,
        "ficha_url": (ficha or {}).get("url_ahdv", ""),
        "ficha_campos": {k: v for k, v in ((ficha or {}).get("campos") or {}).items()
                         if k in ("Hijo", "Padre", "Madre", "Fecha del sacramento",
                                  "Esposo", "Esposa", "Difunto", "Parroquia",
                                  "Libro", "Folio")},
        "origen": "buscador" + (" + ficha" if ficha else ""),
    }
    if previa is None:
        estado["personas"][clave] = nueva
        return clave
    # Ya estaba: se completa lo que falte y se queda el sello MÁS FUERTE
    # (si una búsqueda posterior lo confirma con la partida, sube).
    for campo, valor in nueva.items():
        if not previa.get(campo) and valor:
            previa[campo] = valor
    if _ORDEN_SELLO.get(nivel, 9) < _ORDEN_SELLO.get(previa.get("nivel"), 9):
        previa["nivel"] = nivel
        previa["datos"] = datos
        previa["motivos"] = motivos or previa.get("motivos")
    if parentesco and not previa.get("parentesco"):
        previa["parentesco"] = parentesco
    return clave


def anotar_conocido(estado: dict, *, nombre: str, apellido1: str = "",
                    apellido2: str = "", anio=None, municipio: str = "",
                    parroquia: str = "", parentesco: str = "",
                    papel: str = PAPEL_LINEA, origen: str = "árbol") -> str:
    """Apunta a alguien que YA sabemos (del árbol), con el sello `arbol`.

    Sirve para dos cosas: que la lista empiece por lo que ya se sabía (y no
    parezca que el archivo "descubre" a tu bisabuelo) y que la IA tenga el
    contexto de quién es quién. No cuenta como familiar encontrado: no tiene
    partida en el índice.
    """
    clave = clave_persona(nombre, apellido1, anio)
    if clave in estado["personas"]:
        return clave
    apellidos = list(dict.fromkeys(a for a in (apellido1, apellido2)
                                   if apellido_util(a)))
    estado["personas"][clave] = {
        "nombre": nombre, "apellido1": apellido1, "apellido2": apellido2,
        "anio": anio, "fecha": "", "tipo": "", "id": None, "url": "",
        "parroquia": parroquia, "municipio": municipio, "diocesis": "",
        "padres_texto": [], "apellidos": apellidos, "conyuge": "",
        "cita": "", "folio": "", "signatura": "", "parentesco": parentesco,
        "papel": papel, "de_quien": "", "nivel": NIVEL_ARBOL, "datos": 0,
        "motivos": [], "confianza_ia": "", "ficha_url": "",
        "ficha_campos": {}, "origen": origen,
    }
    return clave


def resumen(estado: dict) -> dict:
    personas = list(estado.get("personas", {}).values())
    # Los del árbol (nivel 'arbol') son el punto de partida, NO un hallazgo: no
    # se cuentan como familiares encontrados, pero sí marcan los años.
    encontrados = [p for p in personas if p.get("nivel") != NIVEL_ARBOL]
    anios = [int(p["anio"]) for p in personas if p.get("anio")]
    cuenta = {sello: sum(1 for p in encontrados if p.get("nivel") == sello)
              for sello in (SELLO_PARTIDA, SELLO_RESERVAS, SELLO_PISTA)}
    return {
        "personas": len(encontrados),
        "arbol": len(personas) - len(encontrados),
        "linea": sum(1 for p in encontrados if p.get("papel") == PAPEL_LINEA),
        "partida": cuenta[SELLO_PARTIDA], "reservas": cuenta[SELLO_RESERVAS],
        "pistas": cuenta[SELLO_PISTA],
        "anio_min": min(anios) if anios else None,
        "anio_max": max(anios) if anios else None,
        "apellidos": len(estado.get("apellidos", {})),
        "buscados": estado.get("buscados", 0),
        "pendientes": len(estado.get("cola", [])),
        "consultas": estado.get("consultas", 0),
        "fichas": estado.get("fichas", 0),
        "llamadas_llm": estado.get("llamadas_llm", 0),
        "coste_llm": estado.get("coste_llm", 0.0),
    }


# ============================== LA IA ======================================

SYSTEM_PROMPT = """Eres el archivero del proyecto de genealogía de la familia \
Merillas. Trabajas SOLO con las filas del índice de registros sacramentales \
del Archivo Histórico Diocesano de Vitoria (Álava, 1481-1900) que se te dan. \
No conoces ni usas ninguna otra fuente.

Te doy dos cosas: (1) la familia que ya está identificada y (2) las filas que \
ha devuelto el buscador al pedir un apellido.

Tu trabajo:
- Decir CUÁLES de esas filas son familiares y de quién son. Para señalarlas usa \
el NÚMERO que aparece delante de cada fila. NUNCA escribas nombres que no \
estén en las filas que te he dado.
- Proponer qué APELLIDOS buscar después (los que aparecen en las filas dadas, \
sobre todo los de las MADRES: son las líneas de las mujeres y son la mitad \
del árbol).

Cómo decidir:
- Compartir un apellido NO basta: hay miles de homónimos. Una fila es de la \
familia si comparte apellidos y además el año y el pueblo cuadran.
- Es MUCHO más fuerte (y lo más importante) que la fila NOMBRE como padre, \
madre o cónyuge a alguien que ya está identificado: eso lo dice el documento.
- Los hermanos comparten los DOS apellidos, son del mismo pueblo y de años \
parecidos (una familia suele bautizar un hijo cada 2-3 años).
- Los padres de un conocido no se adivinan: aparecen NOMBRADOS en la fila de \
ese conocido.
- Si una fila no tiene nada que ver, NO la incluyas.
- "confianza": alta (lo dice la fila, o cuadran apellidos + año + pueblo), \
media (dos cosas), baja (solo comparte un apellido).
- "de_quien": con qué persona de la familia emparentada está (su nombre).
- "parentesco": hermano/a, padre, madre, tío/a, abuelo/a, primo/a, hijo/a, \
cónyuge, suegro/a (lo que se pueda deducir de las filas; si no está claro, \
"familiar sin parentesco claro")."""


JSON_SCHEMA_FAMILIA: dict = {
    "type": "object",
    "properties": {
        "parientes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "fila": {"type": "integer"},
                    "parentesco": {"type": "string"},
                    "de_quien": {"type": "string"},
                    "confianza": {"type": "string",
                                  "enum": ["alta", "media", "baja"]},
                    "por_que": {"type": "string"},
                },
                "required": ["fila", "parentesco", "de_quien", "confianza",
                             "por_que"],
                "additionalProperties": False,
            },
        },
        "apellidos": {"type": "array", "items": {"type": "string"}},
        "por_que_esos_apellidos": {"type": "string"},
    },
    "required": ["parientes", "apellidos", "por_que_esos_apellidos"],
    "additionalProperties": False,
}


def preguntar_ia(estado: dict, lote: list[dict], apellido: str = "",
                 modelo: str = "") -> dict:
    """Una llamada a la IA con la familia conocida y el lote de filas.

    Se importa `chat_json` AQUÍ (perezoso) para que los tests puedan parchear
    `utils.llm.chat_json` sin red, y para que importar este módulo no exija
    `.env`.
    """
    from utils.llm import chat_json
    usuario = (f"{perfil_para_la_ia(estado)}\n\n"
               f"FILAS DEL BUSCADOR (búsqueda por el apellido "
               f"'{apellido}' en el índice del AHDV, Álava 1481-1900):\n"
               f"{lineas_para_la_ia(lote)}\n\n"
               f"Devuelve solo el JSON: qué filas son familiares (por su "
               f"número) y qué apellidos hay que buscar después.")
    resp = chat_json(modelo or MODELO_FASE2, SYSTEM_PROMPT, usuario,
                     json_schema=JSON_SCHEMA_FAMILIA,
                     schema_name="familiares_archivo",
                     max_tokens=AGENTE_MAX_TOKENS_SALIDA, intentos=2)
    return resp if isinstance(resp, dict) else {}


# ============================== EL BUCLE ===================================

def investigar(estado: dict, buscar, abrir_ficha=None, preguntar=None, *,
               max_llm: int = AGENTE_MAX_LLM,
               max_consultas: int = AGENTE_MAX_CONSULTAS,
               max_fichas: int = AGENTE_MAX_FICHAS,
               filas_por_llamada: int = AGENTE_FILAS_POR_LLAMADA,
               fichas_por_apellido: int = AGENTE_FICHAS_POR_APELLIDO,
               avisar=None, pausa: tuple = AGENTE_DELAY, usar_ia: bool = True,
               base: Path | None = None, guardar_cada: int = 5) -> dict:
    """El trabajo: apellido a apellido, fila a fila, con la IA leyendo.

    `buscar(apellido, tipo, anio_ini, anio_fin)` devuelve filas del índice.
    `abrir_ficha(tipo, id)` devuelve la ficha (la página del registro).
    `preguntar(estado, lote, apellido)` devuelve el JSON de la IA.
    Los dos últimos son inyectables para poder probar todo esto SIN red y SIN
    gastar dinero.

    `usar_ia=False` (cuando no hay clave de OpenRouter): se busca igual en el
    archivo y entra lo que certifica la partida, pero nadie lee los resultados.
    """
    def di(texto: str) -> None:
        if avisar:
            avisar(texto)
        else:
            ui.log(texto)

    preguntar = preguntar or preguntar_ia
    # Los topes son POR TANDA (cada vez que se lanza la opción), no de por vida:
    # el estado guarda el total acumulado para el informe y el gasto.
    consultas_base = estado.get("consultas", 0)
    llm_base = estado.get("llamadas_llm", 0)
    while estado["cola"]:
        if estado["consultas"] - consultas_base >= max_consultas:
            estado.setdefault("notas", []).append(
                f"Parada: {max_consultas} consultas al archivo alcanzadas en "
                f"esta tanda.")
            di(f"Tope de {max_consultas} consultas al archivo en esta tanda: se "
               f"para (se puede seguir en otra tanda).")
            break
        if estado["llamadas_llm"] - llm_base >= max_llm:
            estado.setdefault("notas", []).append(
                f"Parada: {max_llm} llamadas a la IA alcanzadas en esta tanda.")
            di(f"Tope de {max_llm} llamadas a la IA en esta tanda: se para (se "
               f"puede seguir en otra tanda).")
            break
        clave_ap = estado["cola"].pop(0)
        ficha_ap = estado["apellidos"].get(clave_ap) or {}
        apellido = ficha_ap.get("apellido", "")
        estado["buscados"] = estado.get("buscados", 0) + 1
        di(f"Buscando el apellido «{apellido}» en el buscador del archivo...")

        ini, fin = ventana_familia(estado)
        lote: list[dict] = []
        vistos: set = set()
        for tipo in TIPOS_BUSQUEDA:
            filas = _intentar_buscar(estado, buscar, apellido, tipo, ini, fin,
                                     di, ficha_ap)
            if not filas and (ini or fin):
                # El portal ordena ASCENDENTE: si el apellido sale mucho, los
                # primeros cientos de filas son del siglo XVI. Por eso se busca
                # primero por la zona de la familia y solo si no hay nada se
                # abre a todo el índice.
                filas = _intentar_buscar(estado, buscar, apellido, tipo, None,
                                         None, di, ficha_ap)
            for fila in filas:
                # La misma partida puede salir en dos búsquedas (el portal
                # contesta a veces lo mismo por bautismo y por matrimonio): se
                # ve una sola vez.
                if fila.get("id") in vistos:
                    continue
                vistos.add(fila.get("id"))
                lote.append(fila)
            time.sleep(random.uniform(*pausa))
        ficha_ap["estado"] = "hecho"
        ficha_ap["filas"] = len(lote)
        if not lote:
            di(f"   sin resultados para «{apellido}».")
            _guardar_de_vez_en_cuando(estado, base, guardar_cada, di)
            continue

        # FICHAS: la página del registro dice el nombre del nacido y el de sus
        # padres. Se abren las de los registros que tocan a la familia.
        candidatas = [(sello(f, estado), f) for f in lote]
        candidatas = [(s, f) for s, f in candidatas if s[0] != SELLO_PISTA]
        candidatas.sort(key=lambda par: (par[0][0] != SELLO_PARTIDA,
                                         -(par[1].get("anio") or 0)))
        # Los apellidos que se van a buscar después salen SOLO de los registros
        # que tocan a la familia: buscando 'Saenz de Navarrete' en Álava salen
        # decenas de familias homónimas (medido: 82 filas) y coger los apellidos
        # de todas llenaba la cola de apellidos que no son de nadie de los
        # nuestros (Martínez de Baños, Muro, Saenz de Olano…). De una fila de la
        # familia, en cambio, salen los apellidos que SÍ son la línea: los de la
        # madre y los de la mujer (Dopico, Guzmán, Tellaeche, Aguirre…).
        for _sello, fila in candidatas:
            for nuevo in apellidos_de_registro(fila):
                if apuntar_apellido(estado, nuevo,
                                    f"aparece en una fila de {apellido}"):
                    di(f"   apellido nuevo para buscar: «{nuevo}»")
        abiertas = 0
        for _sello, fila in candidatas:
            if abiertas >= fichas_por_apellido or estado["fichas"] >= max_fichas:
                break
            if not abrir_ficha or not fila.get("id"):
                break
            datos = _intentar_ficha(abrir_ficha, fila, di)
            if not datos:
                continue
            estado["fichas"] += 1
            abiertas += 1
            fila["ficha"] = datos
            for apellido_ficha in _apellidos_de_ficha(datos):
                apuntar_apellido(estado, apellido_ficha, "aparece en una ficha")
            time.sleep(random.uniform(*pausa))
        if abiertas:
            di(f"   {abiertas} ficha(s) abiertas (dicen el nombre y los padres).")

        # LO QUE DICE LA PARTIDA ENTRA SIN DEPENDER DE LA IA. Si la fila nombra
        # como padre/madre/cónyuge a alguien ya identificado, ese pariente está
        # certificado por el documento: se apunta aunque la IA no lo mencione
        # (y aunque la IA falle). Los que solo "cuadran" (2 datos) los decide la
        # IA: si no, una búsqueda por apellido llenaría la lista de homónimos.
        for nivel_fila, fila in candidatas:
            if nivel_fila[0] != SELLO_PARTIDA:
                continue
            enlace, etiqueta = enlaza_con_la_familia(fila, estado)
            parentesco = ("hijo/a" if etiqueta in ("padre", "madre")
                          else "cónyuge")
            quien = _persona_de(fila, "persona")
            ya_estaba = clave_persona(quien.get("nombre", ""),
                                      quien.get("apellido1", ""),
                                      fila.get("anio"),
                                      fila.get("id")) in estado["personas"]
            clave = anotar_persona(estado, fila,
                                   parentesco=f"{parentesco} — {enlace}",
                                   nivel=nivel_fila[0], datos=nivel_fila[1],
                                   motivos=list(nivel_fila[2]),
                                   papel=PAPEL_COLATERAL, de_quien=etiqueta,
                                   ficha=fila.get("ficha"))
            if ya_estaba:
                continue
            persona = estado["personas"].get(clave) or {}
            di(f"   + {persona.get('nombre')} {persona.get('apellido1')} "
               f"({persona.get('anio') or '¿?'}) · {parentesco} · "
               f"{ETIQUETA_SELLO[SELLO_PARTIDA]}")

        # LA IA lee el lote: qué filas son familiares y qué apellidos seguir.
        # Sin clave de OpenRouter (`usar_ia=False`) se sigue igual: lo que
        # certifica la partida ya ha entrado arriba.
        if usar_ia:
            _llamar_a_la_ia(estado, lote, apellido, preguntar, di,
                            filas_por_llamada, ficha_ap)
        _guardar_de_vez_en_cuando(estado, base, guardar_cada, di)
    return estado


def _guardar_de_vez_en_cuando(estado: dict, base, cada: int, di) -> None:
    if base is None or not cada:
        return
    if estado.get("buscados", 0) % cada:
        return
    guardar_estado(estado, base=base)
    di("   (estado guardado: si se corta, se sigue por aquí)")


def _intentar_buscar(estado: dict, buscar, apellido, tipo, ini, fin, di,
                     ficha_ap) -> list[dict]:
    """Una consulta al buscador del archivo, contada SIEMPRE (aunque falle: el
    portal se ha tocado igual). Un fallo de red no tira la tanda."""
    estado["consultas"] = estado.get("consultas", 0) + 1
    try:
        return buscar(apellido, tipo, ini, fin) or []
    except Exception as e:              # noqa: BLE001 (fallo de red del portal)
        ficha_ap.setdefault("errores", []).append(f"{tipo}: {str(e)[:100]}")
        di(f"   [!] el buscador no respondió para «{apellido}» ({tipo}): "
           f"{str(e)[:80]}")
        return []


def _intentar_ficha(abrir_ficha, fila, di) -> dict:
    try:
        return abrir_ficha(fila.get("tipo", "bautismo"), fila.get("id")) or {}
    except Exception as e:              # noqa: BLE001
        di(f"   [!] no se pudo abrir la ficha {fila.get('id')}: {str(e)[:80]}")
        return {}


def _apellidos_de_ficha(datos: dict) -> list[str]:
    """Apellidos que aparecen en una ficha (los de las personas que nombra)."""
    salida: list[str] = []
    for persona in (datos.get("personas") or {}).values():
        for clave in ("apellido1", "apellido2"):
            apellido = (persona or {}).get(clave, "")
            if apellido_util(apellido) and apellido not in salida:
                salida.append(apellido)
    return salida


def _llamar_a_la_ia(estado: dict, lote: list[dict], apellido: str, preguntar,
                    di, filas_por_llamada: int, ficha_ap: dict) -> None:
    """Una llamada a la IA por apellido (con el lote recortado a lo que se le
    puede enseñar de una vez)."""
    from utils.llm import GASTO, PresupuestoExcedido
    recorte = lote[:filas_por_llamada]
    if len(lote) > len(recorte):
        di(f"   (el lote tenía {len(lote)} filas: se le enseñan las "
           f"{len(recorte)} más cercanas a la familia)")
    coste_antes = GASTO.coste
    try:
        respuesta = preguntar(estado, recorte, apellido)
    except PresupuestoExcedido as e:
        estado.setdefault("notas", []).append(f"Presupuesto agotado: {e}")
        di(f"Presupuesto agotado ({e}): se para y se guarda lo hecho.")
        raise
    except Exception as e:              # noqa: BLE001 (red/LLM caído)
        estado.setdefault("notas", []).append(
            f"La IA falló con «{apellido}»: {str(e)[:120]}")
        di(f"   [!] la IA no contestó con «{apellido}»: {str(e)[:90]} "
           f"(las filas quedan guardadas para otra tanda)")
        return
    estado["llamadas_llm"] += 1
    estado["coste_llm"] = round(estado.get("coste_llm", 0.0)
                                + (GASTO.coste - coste_antes), 6)
    lectura = interpretar_respuesta(respuesta, recorte, estado)
    ficha_ap["ia"] = {"parientes": len(lectura["parientes"]),
                      "descartados": lectura["descartados"]}
    if lectura["por_que_esos"]:
        estado["siguientes_ia"] = ([lectura["por_que_esos"]]
                                   + (estado.get("siguientes_ia") or []))[:5]
    for pariente in lectura["parientes"]:
        fila = pariente["fila"]
        papel = (PAPEL_LINEA
                 if pariente["parentesco"].lower().startswith(
                     ("padre", "madre", "abuel"))
                 else PAPEL_COLATERAL)
        quien = _persona_de(fila, "persona")
        ya_estaba = clave_persona(quien.get("nombre", ""),
                                  quien.get("apellido1", ""),
                                  fila.get("anio"),
                                  fila.get("id")) in estado["personas"]
        clave = anotar_persona(
            estado, fila, parentesco=pariente["parentesco"], nivel=pariente["nivel"],
            datos=pariente["datos"], motivos=pariente["motivos"], papel=papel,
            de_quien=pariente["de_quien"], confianza_ia=pariente["confianza_ia"],
            ficha=fila.get("ficha"))
        if ya_estaba:
            continue
        persona = estado["personas"].get(clave) or {}
        di(f"   + {persona.get('nombre')} {persona.get('apellido1')} "
           f"({persona.get('anio') or '¿?'}) · {pariente['parentesco']} · "
           f"{ETIQUETA_SELLO.get(pariente['nivel'], '')}")
    for nuevo in lectura["apellidos"]:
        if apuntar_apellido(estado, nuevo, "lo propone la IA"):
            di(f"   apellido nuevo (IA) para buscar: «{nuevo}»")
    # Y de las filas que la IA ha dado por familiares también salen apellidos
    # (los de los padres que nombra el registro).
    for pariente in lectura["parientes"]:
        for nuevo in apellidos_de_registro(pariente["fila"]):
            if apuntar_apellido(estado, nuevo, "aparece en una fila de familia"):
                di(f"   apellido nuevo para buscar: «{nuevo}»")
    if lectura["descartados"]:
        di(f"   ({lectura['descartados']} propuesta(s) de la IA descartada(s) "
           f"por no existir en las filas)")


# ============================== INFORME ====================================

def _apartado(personas: list[dict], titulo: str) -> list[str]:
    if not personas:
        return []
    lineas = [f"## {titulo} ({len(personas)})", ""]
    lineas.append("| año | persona | padres (lo que dice el registro) | "
                  "parroquia/municipio | parentesco | cita del archivo |")
    lineas.append("|---|---|---|---|---|---|")
    for persona in personas:
        nombre = (f"{persona.get('nombre', '')} {persona.get('apellido1', '')} "
                  f"{persona.get('apellido2', '')}").strip()
        padres = " y ".join(p for p in (persona.get("padres_texto") or []) if p) or "—"
        lugar = ", ".join(p for p in (persona.get("parroquia"),
                                      persona.get("municipio")) if p) or "—"
        cita = persona.get("cita") or ""
        enlace = persona.get("ficha_url") or persona.get("url") or ""
        if enlace:
            cita = f"{cita} · [ficha]({enlace})" if cita else f"[ficha]({enlace})"
        lineas.append(
            f"| {persona.get('anio') or '¿?'} | **{nombre}** | {padres} | "
            f"{lugar} | {persona.get('parentesco') or '—'} | {cita or '—'} |")
    lineas.append("")
    return lineas


def informe(estado: dict) -> str:
    """La lista de familiares posibles, en markdown, para leer."""
    datos = resumen(estado)
    personas = sorted(estado.get("personas", {}).values(),
                      key=lambda p: (_ORDEN_SELLO.get(p.get("nivel"), 9),
                                     p.get("anio") or 0))
    del_arbol = [p for p in personas if p.get("nivel") == NIVEL_ARBOL]
    personas = [p for p in personas if p.get("nivel") != NIVEL_ARBOL]
    lineas = [
        "# Familiares posibles en el archivo vasco (AHDV, Álava 1481-1900)",
        "",
        "Búsqueda hecha **solo** en el buscador de registros sacramentales del "
        "Archivo Histórico Diocesano de Vitoria (artxibo.euskadi.eus), con la "
        "IA leyendo los resultados: primero por el **primer apellido** (la "
        "lista larga, donde están los familiares), abriendo las **fichas** "
        "(que dicen el nombre del nacido y el de sus padres) y repitiendo la "
        "búsqueda con **todos los apellidos que aparecen**, incluidos los de "
        "las madres.",
        "",
        f"- Apellidos buscados: **{datos['buscados']}** de {datos['apellidos']} "
        f"(quedan {datos['pendientes']} para otra tanda)",
        f"- Consultas al archivo: **{datos['consultas']}** (gratis) · fichas "
        f"abiertas: **{datos['fichas']}**",
        f"- Llamadas a la IA: **{datos['llamadas_llm']}** "
        f"(coste ${datos['coste_llm']:.4f})",
        f"- Familiares en la lista: **{datos['personas']}** "
        f"({datos['partida']} lo dice la partida · {datos['reservas']} con 2 "
        f"datos · {datos['pistas']} pistas) · punto de partida: "
        f"{datos['arbol']} que ya sabías",
        f"- Años cubiertos: {datos['anio_min'] or '¿?'} – "
        f"{datos['anio_max'] or '¿?'}",
        "",
        "**Cómo se lee**: *lo dice la partida* = el registro nombra a alguien "
        "ya identificado (es el dato más fuerte) · *con 2 datos* = comparte "
        "apellidos y cuadran año y pueblo · *pista* = solo un dato (o solo lo "
        "que dice la IA): se apunta, no se da por bueno.",
        "",
    ]
    for sello in (SELLO_PARTIDA, SELLO_RESERVAS, SELLO_PISTA):
        grupo = [p for p in personas if p.get("nivel") == sello]
        lineas += _apartado(grupo, ETIQUETA_SELLO[sello])
    lineas += _apartado(del_arbol, "Punto de partida: lo que ya sabías "
                                   "(no está en el índice)")
    lineas += ["## Apellidos por los que se ha buscado", "",
               "| apellido | filas | familiares | de dónde salió |", "|---|---|---|---|"]
    for ficha_ap in sorted(estado.get("apellidos", {}).values(),
                           key=lambda a: a.get("apellido", "")):
        if ficha_ap.get("estado") != "hecho":
            continue
        ia = ficha_ap.get("ia") or {}
        lineas.append(f"| {ficha_ap.get('apellido')} | "
                      f"{ficha_ap.get('filas', 0)} | {ia.get('parientes', 0)} | "
                      f"{'; '.join(ficha_ap.get('de') or []) or '—'} |")
    pendientes = [estado["apellidos"].get(c, {}).get("apellido", "")
                  for c in estado.get("cola", [])]
    if pendientes:
        lineas += ["", "## Pendientes para la próxima tanda", "",
                   ", ".join(p for p in pendientes if p)]
    if estado.get("siguientes_ia"):
        lineas += ["", "## Por dónde seguir, según la IA", ""]
        lineas += [f"- {t}" for t in estado["siguientes_ia"]]
    if estado.get("notas"):
        lineas += ["", "## Notas de la tanda", ""]
        lineas += [f"- {n}" for n in estado["notas"]]
    lineas += ["", "---", "",
               "Los nombres, los años y las citas salen de las filas REALES del "
               "buscador: la IA solo dice qué fila es cada quien y qué buscar "
               "después. Nada de esto se ha escrito en el árbol."]
    return "\n".join(lineas)


def escribir_informe(estado: dict, base: Path | None = None) -> Path:
    base = base if base is not None else BASE_DIR
    ruta = Path(base) / AGENTE_INFORME
    escribir_con_backup(ruta, informe(estado))
    return ruta
