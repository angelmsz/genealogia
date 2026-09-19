"""
agent/ramas.py — Análisis por RAMAS FAMILIARES (v10.4.2, BLOQUE 3).

PARA QUÉ SIRVE
El menú tiene dos opciones por rama familiar:
  * paterna: Álava / Vitoria (la línea Sáenz de Navarrete · Pérez de Palomares).
  * materna: Palencia / Zamora (Pelaz · Merino · Merillas · Panero · López …).

Este módulo es el que hace el trabajo, y NO escribe NUNCA en el árbol
(`familia_conocida.json`) ni en `arbol_*`: solo lee, analiza y prepara trámites.

1. `personas_de_rama()` elige las personas de la rama cruzando el árbol
   (`familia_conocida.json`) con la cola de investigación
   (`estado_investigacion.json`): el árbol solo tiene provincia en algunas
   fichas, así que la frontera aporta el municipio y la prioridad del resto.
2. `apellidos_de_rama()` saca los apellidos a buscar. Para la rama paterna son
   APELLIDOS COMPUESTOS ('Saenz de Navarrete', 'Perez de Palomares'): se buscan
   enteros, nunca troceados (ver scrapers/artxibo.py).
3. `evaluar_compatibilidad()` aplica la regla de >=2 DATOS INDEPENDIENTES (la
   misma del clasificador de evidencia) a cada partida encontrada:
     - identidad: nombre + los dos apellidos = 2 datos; solo los apellidos = 1;
       un solo apellido = 0,5;
     - fecha coherente (±5 años) = 1 dato;
     - lugar (municipio/parroquia) = 1 dato.
   Con >=2 datos la partida es candidata; con un CHOQUE (apellidos distintos,
   fecha a más de 25 años u otra generación —más de 60—) es INCOMPATIBLE; y si
   hay algún desajuste que no rompe (la fecha baila, el municipio del árbol no
   es el de la partida, el árbol no tiene año) queda COMPATIBLE CON RESERVAS.
   Ese "con reservas" es el estado honesto: la partida encaja, pero el vínculo
   no está probado por dos fuentes.
4. `solicitudes_de_rama()` redacta lo que hay que PEDIR: copia literal al
   archivo diocesano (con la cita: fondo, signatura, folio) y certificado
   GRATUITO al Registro Civil cuando el nacimiento es de 1871 en adelante.
5. `registrar_solicitudes()` las apunta en `estado_investigacion.json` con
   estado "pendiente_envio", dejando `.bak` del fichero antes de escribir
   (config.escribir_con_backup).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from config import (ARCHIVOS_CONTACTOS, APELLIDOS_COMUNES, BASE_DIR,
                    ESTADO_PATH, FAMILIA_JSON_PATH, REGISTRO_CIVIL_CONTACTOS,
                    escribir_con_backup, normalizar)

RAMA_ALAVA = "alava"          # línea PATERNA DE LA MADRE (Sáenz de Navarrete)
RAMA_ZAMORA = "zamora"        # línea DEL PADRE (Merillas · López)
RAMA_PALENCIA = "palencia"    # línea MATERNA DE LA MADRE (Pelaz · Merino)
RAMAS = {
    RAMA_ALAVA: {
        "titulo": "Línea paterna de tu madre — Álava/Vitoria "
                  "(Sáenz de Navarrete)",
        "provincias": {"alava", "araba"},
        "municipios": {"vitoria", "vitoria-gasteiz"},
    },
    RAMA_ZAMORA: {
        "titulo": "Línea de tu padre — Zamora (Merillas · López)",
        "provincias": {"zamora"},
        "municipios": set(),
    },
    RAMA_PALENCIA: {
        "titulo": "Línea materna de tu madre — Palencia (Pelaz · Merino)",
        "provincias": {"palencia"},
        "municipios": set(),
    },
}
# Nombres que tuvo una rama antes de llamarse por su provincia (v10.4.2, para
# no romper el historial de comandos de nadie).
ALIAS_RAMAS = {"paterna": RAMA_ALAVA}
TITULO = {clave: datos["titulo"] for clave, datos in RAMAS.items()}


def resolver_rama(nombre: str) -> str:
    """Clave de rama a partir de lo que escriba el usuario (o error claro)."""
    clave = normalizar(nombre or "")
    if clave in RAMAS:
        return clave
    if clave in ALIAS_RAMAS:
        return ALIAS_RAMAS[clave]
    raise ValueError(
        f"rama desconocida: {nombre!r} (usa {' / '.join(sorted(RAMAS))})")


# Años de desfase que ya son una contradicción (no un simple desajuste).
TOLERANCIA_ANIO = 25
# Años que separan generaciones: si la partida cae más lejos, es de otra
# familia/época y no puede ser la persona del árbol.
TOLERANCIA_GENERACION = 60
# Desfase de fecha que solo es una RESERVA (se apunta, no invalida).
AJUSTE_ANIO = 5
# Tope de solicitudes por rama (para no generar 40 cartas de golpe).
MAX_SOLICITUDES = 12

VEREDICTO_COMPATIBLE = "COMPATIBLE"
VEREDICTO_RESERVAS = "COMPATIBLE CON RESERVAS"
VEREDICTO_INCOMPATIBLE = "INCOMPATIBLE"
VEREDICTO_SIN_DATOS = "SIN DATOS SUFICIENTES"
ESTADO_PENDIENTE = "pendiente_envio"

_RANKING = {VEREDICTO_COMPATIBLE: 0, VEREDICTO_RESERVAS: 1,
            VEREDICTO_SIN_DATOS: 2, VEREDICTO_INCOMPATIBLE: 3}


# ============================== LECTURA ====================================

def _leer_json(ruta: Path) -> dict:
    try:
        datos = json.loads(Path(ruta).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return datos if isinstance(datos, dict) else {}


def cargar_familia(base: Path | None = None) -> dict:
    base = base if base is not None else BASE_DIR
    return _leer_json(base / FAMILIA_JSON_PATH)


def cargar_frontera(base: Path | None = None) -> dict:
    base = base if base is not None else BASE_DIR
    return _leer_json(base / ESTADO_PATH)


def _anio(texto) -> int | None:
    """Primer año de 4 cifras de un texto ('1936', 'hacia 1895-1905')."""
    m = re.search(r"\b(1[5-9]\d{2}|20\d{2})\b", str(texto or ""))
    return int(m.group(1)) if m else None


def _anio_de_los_hijos(familia: dict, ficha: dict) -> int | None:
    """Año del hijo mayor (hijo mayor − 28) de una ficha del árbol.

    Sirve para deducir el año de un antepasado cuando su ficha no lo trae: si
    su hijo nació en 1927, él nació hacia 1899. Es una ESTIMACIÓN, y así se
    marca. Mira en las DOS direcciones, porque el árbol no siempre está
    completo en el mismo sentido: la lista `hijos` del padre y, si esa está
    vacía, quién declara a esta persona como `padre`/`madre` en su ficha.
    """
    nombre = normalizar(ficha.get("nombre", ""))
    por_nombre = {normalizar(f.get("nombre", "")): f
                  for f in familia.get("personas", [])}
    anios = []
    for nombre_hijo in ficha.get("hijos") or []:
        hijo = por_nombre.get(normalizar(nombre_hijo)) or {}
        anio, _ = _anio_estimado(hijo, familia=None)
        if anio:
            anios.append(anio)
    if not anios and nombre:
        for otra in familia.get("personas", []):
            if nombre in (normalizar(otra.get("padre", "")),
                          normalizar(otra.get("madre", ""))):
                anio, _ = _anio_estimado(otra, familia=None)
                if anio:
                    anios.append(anio)
    return min(anios) - 28 if anios else None


def _anio_estimado(persona: dict, familia: dict | None = None) -> tuple:
    """(año, ¿es estimación?) de una ficha del árbol.

    Orden: el campo `fecha_aproximada` (dato); las notas ('Nacimiento estimado
    hacia 1895-1905'); y, si no hay nada, **los hijos** (hijo mayor − 28 años).
    Las dos últimas son ESTIMACIONES y así se marcan: sirven para calcular la
    ventana de búsqueda, no para certificar nada.
    """
    nac = persona.get("nacimiento") or {}
    anio = _anio(nac.get("fecha_aproximada", ""))
    if anio is not None:
        return anio, False
    anio = _anio(persona.get("notas", ""))
    if anio is not None:
        return anio, True
    if familia is not None:
        anio = _anio_de_los_hijos(familia, persona)
        if anio is not None:
            return anio, True
    return None, True


def _es_de_rama(provincia: str, municipio: str, rama: str) -> bool:
    datos = RAMAS.get(resolver_rama(rama))
    prov = normalizar(provincia or "")
    mun = normalizar(municipio or "")
    if prov and prov in datos["provincias"]:
        return True
    return bool(mun) and any(mun.startswith(m) for m in datos["municipios"])


def personas_de_rama(rama: str, familia: dict | None = None,
                     frontera: dict | None = None,
                     base: Path | None = None) -> list[dict]:
    """Personas de la rama (del árbol y de la frontera), sin duplicados.

    El árbol solo trae provincia/municipio en algunas fichas; la frontera
    (la cola de investigación) sí tiene el municipio de cada ancla y su
    prioridad, así que las dos fuentes se funden por nombre.
    """
    familia = familia if familia is not None else cargar_familia(base)
    frontera = frontera if frontera is not None else cargar_frontera(base)
    rama = resolver_rama(rama)
    personas: dict[str, dict] = {}
    # Índice por nombre de TODAS las fichas del árbol: la frontera solo trae el
    # municipio y la prioridad, así que el año estimado y los padres salen de
    # la ficha del árbol aunque su provincia venga vacía.
    por_nombre = {normalizar(f.get("nombre", "")): f
                  for f in familia.get("personas", [])}

    def _clave(nombre: str) -> str:
        return normalizar(nombre)

    def _anadir_del_arbol(ficha: dict, origen: str) -> None:
        """Mete (o refresca) una ficha del árbol dentro de la rama."""
        nac = ficha.get("nacimiento") or {}
        anio, estimado = _anio_estimado(ficha, familia)
        personas[_clave(ficha.get("nombre", ""))] = {
            "nombre": ficha.get("nombre", ""),
            "origen": origen,
            "id": ficha.get("id", ""),
            "municipio": nac.get("municipio", ""),
            "provincia": nac.get("provincia", ""),
            "anio": anio,
            "anio_estimado": estimado,
            "apellido_paterno": ficha.get("apellido_paterno", ""),
            "apellido_materno": ficha.get("apellido_materno", ""),
            "padres": [p for p in (ficha.get("padre"), ficha.get("madre")) if p],
            "prioridad": None,
            "notas": ficha.get("notas", ""),
        }

    for ficha in familia.get("personas", []):
        nac = ficha.get("nacimiento") or {}
        if not _es_de_rama(nac.get("provincia", ""), nac.get("municipio", ""),
                           rama):
            continue
        _anadir_del_arbol(ficha, "arbol")
    for entrada in frontera.get("frontera", []):
        if not _es_de_rama(entrada.get("provincia", ""),
                           entrada.get("municipio", ""), rama):
            continue
        clave = _clave(entrada.get("ancla", ""))
        # La ficha del árbol es la que trae el año ESTIMADO (en las notas) y los
        # padres, aunque su provincia venga vacía: se usa para completar.
        ficha_arbol = por_nombre.get(clave) or {}
        anio_ficha, estimado = _anio_estimado(ficha_arbol, familia)
        previa = personas.get(clave)
        if previa is not None:
            previa["origen"] = "arbol+frontera"
            previa["prioridad"] = entrada.get("prioridad")
            if not previa["municipio"]:
                previa["municipio"] = entrada.get("municipio", "")
            if not previa["provincia"]:
                previa["provincia"] = entrada.get("provincia", "")
            continue
        personas[clave] = {
            "nombre": entrada.get("ancla", ""),
            "origen": "frontera",
            "id": ficha_arbol.get("id", ""),
            "municipio": entrada.get("municipio", ""),
            "provincia": entrada.get("provincia", ""),
            "anio": anio_ficha or _anio(entrada.get("motivo", "")),
            "anio_estimado": estimado if anio_ficha is not None else True,
            "apellido_paterno": entrada.get("apellido", "")
                                or ficha_arbol.get("apellido_paterno", ""),
            "apellido_materno": ficha_arbol.get("apellido_materno", ""),
            "padres": list(entrada.get("padres") or []) or
                      [p for p in (ficha_arbol.get("padre"),
                                   ficha_arbol.get("madre")) if p],
            "prioridad": entrada.get("prioridad"),
            "notas": entrada.get("motivo", ""),
        }
    # SUBIR POR EL ÁRBOL. En este árbol la provincia/municipio casi nunca están
    # rellenos, así que una rama se quedaría en la única ficha que los trae (el
    # abuelo de Vitoria) y el rastreo no tendría de dónde tirar. Por eso se
    # añaden los ASCENDIENTES (padre/madre) de quien ya está dentro, con su año
    # deducido de los hijos si hace falta. Se corta al salir de la rama: si el
    # ascendiente declara provincia de otra línea (Zamora/Palencia), no entra.
    for _ in range(4):
        nuevos = []
        for persona in list(personas.values()):
            ficha = por_nombre.get(_clave(persona["nombre"])) or {}
            for clave_padre in ("padre", "madre"):
                nombre_asc = ficha.get(clave_padre) or ""
                if not nombre_asc or _clave(nombre_asc) in personas:
                    continue
                ascendiente = por_nombre.get(_clave(nombre_asc))
                if not ascendiente:
                    continue
                nac_asc = ascendiente.get("nacimiento") or {}
                if not _es_de_rama(nac_asc.get("provincia", ""),
                                   nac_asc.get("municipio", ""), rama) and \
                        (nac_asc.get("provincia") or nac_asc.get("municipio")):
                    continue      # declara otra provincia: es de otra rama
                nuevos.append(ascendiente)
        if not nuevos:
            break
        for ficha in nuevos:
            if _clave(ficha.get("nombre", "")) not in personas:
                personas[_clave(ficha.get("nombre", ""))] = {}
            _anadir_del_arbol(ficha, "arbol (ascendiente)")
    return sorted(personas.values(),
                  key=lambda p: (-(p.get("prioridad") or -99),
                                 normalizar(p["nombre"])))


def personas_sin_anio(personas: list[dict]) -> list[str]:
    """Nombres de las personas de la rama sin año: no se pueden rastrear.

    No se buscan (sin año no hay ventana que consultar), pero se LISTAN para
    que no desaparezcan en silencio: son justo las que hay que datar a mano.
    """
    return [p.get("nombre", "") for p in personas if not p.get("anio")]


def semillas_de_linaje(personas: list[dict]) -> list[dict]:
    """Convierte las personas de la rama en SEMILLAS del rastreo del linaje.

    De la ficha del árbol solo se aprovecha lo fiable: el nombre de pila, los
    apellidos y el año (estimado: se marca como tal). El municipio va como
    pista para puntuar, nunca como filtro: la familia puede estar en otro
    pueblo (ya pasó: el árbol decía Vitoria y era Navaridas).
    """
    from agent import linaje
    semillas = []
    for persona in personas:
        anio = persona.get("anio")
        if not anio:
            continue          # sin año no hay ventana: no se puede buscar
        pila = (persona.get("nombre") or "").split()
        if not pila:
            continue
        semillas.append(linaje.nueva_persona(
            nombre=pila[0],
            apellido1=persona.get("apellido_paterno", ""),
            apellido2=persona.get("apellido_materno", ""),
            anio=anio, anio_estimado=bool(persona.get("anio_estimado")),
            ventana=linaje.ventana_semilla(anio), generacion=0,
            rol=linaje.ROL_LINEA, municipio=persona.get("municipio", ""),
            notas=f"del árbol ({persona.get('origen', '')})"))
    return semillas


def apellidos_de_rama(personas: list[dict],
                      incluir_maternos: bool = False) -> list[str]:
    """Apellidos a buscar en los índices, los COMPUESTOS primero.

    - El primer apellido de cada persona y el apellido del ancla de la frontera.
    - `incluir_maternos=True` añade los segundos apellidos (por defecto NO: el
      segundo apellido ya es de la línea anterior y ensancha la búsqueda).
    - Se descartan los apellidos muy comunes (no identifican a nadie).
    """
    apellidos: list[str] = []
    for persona in personas:
        candidatos = [persona.get("apellido_paterno", "")]
        if incluir_maternos:
            candidatos.append(persona.get("apellido_materno", ""))
        for apellido in candidatos:
            apellido = (apellido or "").strip()
            if len(apellido) <= 2:
                continue
            if normalizar(apellido) in APELLIDOS_COMUNES:
                continue
            if apellido not in apellidos:
                apellidos.append(apellido)
    # Los compuestos (con partícula o con espacio) primero y más largos antes:
    # son más específicos y los que NO hay que trocear.
    return sorted(apellidos, key=lambda a: (-len(a.split()), -len(a)))


# ==================== COMPATIBILIDAD (regla de >=2 datos) ==================

def _mismo_apellido(a: str, b: str) -> bool:
    """Igualdad tolerante de apellidos (Sáenz == Saenz; 'Saenz de Navarrete'
    contiene a 'Saenz de Navarrete')."""
    a, b = normalizar(a or ""), normalizar(b or "")
    if not a or not b:
        return False
    if a == b:
        return True
    return len(a) >= 4 and len(b) >= 4 and (a in b or b in a)


def evaluar_compatibilidad(fila: dict, persona: dict) -> dict:
    """Coteja una partida del índice con una persona del árbol.

    Devuelve {"datos", "veredicto", "coincidencias", "reservas", "conflictos"}.
    La regla es la del proyecto: hacen falta >=2 datos INDEPENDIENTES, y un
    choque (apellidos distintos, fecha imposible, otra generación) descarta.
    """
    suyo = fila.get("persona") or {}
    ape1_f = suyo.get("apellido1", "")
    ape2_f = suyo.get("apellido2", "")
    ape1_p = persona.get("apellido_paterno", "")
    ape2_p = persona.get("apellido_materno", "")
    coincidencias: list[str] = []
    reservas: list[str] = []
    conflictos: list[str] = []
    datos = 0.0

    # --- 1. identidad ---
    pila_persona = (persona.get("nombre") or "").split()
    pila_persona = normalizar(pila_persona[0]) if pila_persona else ""
    pila_fila = normalizar(suyo.get("nombre", ""))
    nombre_igual = bool(pila_fila) and pila_fila == pila_persona
    apellidos_fila = [a for a in (ape1_f, ape2_f) if a]
    apellidos_persona = [a for a in (ape1_p, ape2_p) if a]
    comunes = sum(1 for a in apellidos_persona
                  if any(_mismo_apellido(a, b) for b in apellidos_fila))
    if apellidos_persona and comunes == 0:
        conflictos.append(
            f"los apellidos de la partida ({', '.join(apellidos_fila) or '—'}) "
            f"no son los del árbol ({', '.join(apellidos_persona)})")
    elif nombre_igual and apellidos_persona and comunes == len(apellidos_persona):
        datos += 2
        coincidencias.append(
            f"nombre y apellidos completos ({suyo.get('completo', '')})")
    elif apellidos_persona and comunes >= 2:
        datos += 1
        coincidencias.append(
            f"los dos apellidos ({', '.join(apellidos_fila)}) pero el nombre "
            f"de pila no coincide ({suyo.get('nombre', '') or '—'})")
    elif comunes == 1:
        datos += 0.5
        coincidencias.append(f"un apellido de la partida ({', '.join(apellidos_fila)})")

    # --- 2. fecha ---
    anio_fila = fila.get("anio")
    anio_persona = persona.get("anio")
    if anio_fila and anio_persona:
        desfase = abs(anio_fila - anio_persona)
        if desfase <= AJUSTE_ANIO:
            datos += 1
            coincidencias.append(
                f"fecha coherente ({anio_fila} frente a ~{anio_persona})")
        elif desfase > TOLERANCIA_GENERACION:
            conflictos.append(
                f"es de otra generación: {anio_fila} frente a ~{anio_persona} "
                f"({desfase} años)")
        elif desfase > TOLERANCIA_ANIO:
            conflictos.append(
                f"la fecha no encaja: {anio_fila} frente a ~{anio_persona} "
                f"({desfase} años)")
        else:
            reservas.append(
                f"la fecha baila {desfase} años ({anio_fila} frente a "
                f"~{anio_persona}, estimación del árbol)")
    elif anio_fila and not anio_persona:
        reservas.append("el árbol no tiene año con el que comparar la partida")

    # --- 3. lugar ---
    lugar_fila = {normalizar(fila.get(k, "")) for k in
                  ("municipio", "localidad", "parroquia")} - {""}
    mun_persona = normalizar(persona.get("municipio", ""))
    if lugar_fila and mun_persona:
        if any(mun_persona in lugar for lugar in lugar_fila):
            datos += 1
            coincidencias.append(f"lugar: {fila.get('localidad') or
                                              fila.get('municipio')}")
        else:
            reservas.append(
                f"el municipio del árbol ({persona.get('municipio')}) no es el "
                f"de la partida ({fila.get('localidad') or fila.get('municipio')})")
    elif lugar_fila and not mun_persona:
        reservas.append("el árbol no tiene municipio con el que comparar")

    # --- veredicto ---
    if conflictos:
        veredicto = VEREDICTO_INCOMPATIBLE
    elif datos >= 2:
        veredicto = VEREDICTO_RESERVAS if reservas else VEREDICTO_COMPATIBLE
    else:
        veredicto = VEREDICTO_SIN_DATOS
    return {"datos": datos, "veredicto": veredicto,
            "coincidencias": coincidencias, "reservas": reservas,
            "conflictos": conflictos}


def analizar_filas(filas: list[dict], personas: list[dict]) -> list[dict]:
    """Cada partida con la persona del árbol con la que MÁS encaja."""
    analisis = []
    for fila in filas:
        mejor = None
        for persona in personas:
            evaluacion = evaluar_compatibilidad(fila, persona)
            candidato = {"fila": fila, "persona": persona,
                         "evaluacion": evaluacion}
            if mejor is None or _mejor_que(candidato, mejor):
                mejor = candidato
        if mejor is not None:
            analisis.append(mejor)
    return sorted(analisis, key=lambda c: (
        _RANKING.get(c["evaluacion"]["veredicto"], 9),
        -c["evaluacion"]["datos"],
        c["fila"].get("anio") or 0))


def _mejor_que(candidato: dict, actual: dict) -> bool:
    clave_c = (_RANKING.get(candidato["evaluacion"]["veredicto"], 9),
               -candidato["evaluacion"]["datos"])
    clave_a = (_RANKING.get(actual["evaluacion"]["veredicto"], 9),
               -actual["evaluacion"]["datos"])
    return clave_c < clave_a


# ============================ SOLICITUDES =================================

def _nombre_archivo_diocesano(provincia: str) -> tuple[str, dict] | None:
    datos = ARCHIVOS_CONTACTOS.get(normalizar(provincia or ""))
    return (datos["archivo"], datos) if datos else None


def _contacto_civil(municipio: str) -> dict | None:
    clave = normalizar(municipio or "")
    if not clave:
        return None
    if clave in REGISTRO_CIVIL_CONTACTOS:
        return REGISTRO_CIVIL_CONTACTOS[clave]
    for nombre, datos in REGISTRO_CIVIL_CONTACTOS.items():
        if nombre and (nombre in clave or clave in nombre):
            return datos
    return None


def _fecha_hoy() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _solicitud(rama: str, persona: str, archivo: str, contacto: str,
               tipo: str, referencia: str, asunto: str, cuerpo: str,
               notas: str = "", url_tramite: str = "",
               como_se_pide: str = "") -> dict:
    return {"fecha": _fecha_hoy(), "via": "email", "persona": persona,
            "archivo": archivo, "contacto": contacto, "rama": rama,
            "tipo": tipo, "referencia": referencia, "estado": ESTADO_PENDIENTE,
            "asunto": asunto, "cuerpo": cuerpo, "notas": notas,
            # DÓNDE se pide (verificado en vivo): cada archivo tiene su método
            # (formulario propio, sede electrónica o email).
            "url_tramite": url_tramite, "como_se_pide": como_se_pide}


def _rotulo_anio(persona: dict) -> str:
    """Año para las cartas, marcado como estimación cuando lo es."""
    anio = persona.get("anio")
    if not anio:
        return "[RELLENAR: año aprox.]"
    return f"hacia {anio}" + (" (estimado)" if persona.get("anio_estimado")
                              else "")


def _carta_diocesana(persona: dict, datos_archivo: dict, tipo: str) -> str:
    return (
        f"Estimados señores:\n\n"
        f"Me dirijo al {datos_archivo['archivo']} para solicitar una búsqueda "
        f"y copia literal de {tipo} de:\n\n"
        f"  - Persona: {persona['nombre']}\n"
        f"  - Localidad/parroquia: {persona.get('municipio') or '[RELLENAR]'}"
        f"{' (' + persona['provincia'] + ')' if persona.get('provincia') else ''}\n"
        f"  - Fecha aproximada: {_rotulo_anio(persona)}\n"
        + (f"  - Padres: {', '.join(persona['padres'])}\n"
           if persona.get("padres") else "")
        + f"\nMotivo: investigación genealógica familiar.\n\n"
        f"Quedo a su disposición para abonar la tasa que corresponda: "
        f"indíquenme el importe y la forma de pago.\n\n"
        f"Atentamente,\n\n"
        f"  Nombre y apellidos: [RELLENAR]\n"
        f"  DNI/NIE: [RELLENAR]\n"
        f"  Dirección postal: [RELLENAR]\n"
        f"  Teléfono y correo: [RELLENAR]\n")


def _carta_civil(persona: dict, datos_civil: dict) -> str:
    return (
        f"Estimados señores:\n\n"
        f"Me dirijo al {datos_civil['registro']} para solicitar, por la vía "
        f"gratuita que establece la Ley del Registro Civil, certificación "
        f"literal de nacimiento de:\n\n"
        f"  - Persona: {persona['nombre']}\n"
        f"  - Lugar del hecho: {datos_civil.get('municipio', persona.get('municipio'))}\n"
        f"  - Fecha: {_rotulo_anio(persona)}\n"
        + (f"  - Padres: {', '.join(persona['padres'])}\n"
           if persona.get("padres") else "")
        + f"\nSi el hecho consta en el Juzgado de Paz de "
        f"{datos_civil.get('juzgado_paz', 'la localidad')}, les agradecería "
        f"que me indiquen cómo dirigirme a él.\n\n"
        f"Adjunto copia de mi DNI.\n\n"
        f"Atentamente,\n\n"
        f"  Nombre y apellidos: [RELLENAR]\n"
        f"  DNI/NIE: [RELLENAR]\n"
        f"  Teléfono y correo: [RELLENAR]\n")


def _coincide_el_nombre(fila: dict, persona: dict) -> bool:
    """¿Coincide el nombre de pila (no solo los apellidos)?

    Importa para el dinero: en un pueblo donde el apellido se repite, la
    partida de un HERMANO encaja por apellidos y fecha y también sale como
    candidata. Eso no es un error (es información), pero hay que decirlo, no
    que el usuario lo descubra pagando una copia.
    """
    pila_persona = (persona.get("nombre") or "").split()
    pila_persona = normalizar(pila_persona[0]) if pila_persona else ""
    return bool(pila_persona) and \
        normalizar((fila.get("persona") or {}).get("nombre", "")) == pila_persona


def carta_al_ahdv(fila: dict, persona: dict) -> dict:
    """Solicitud de copia literal de UNA partida concreta del índice, con su
    cita completa (fondo, signatura, folio) y la ficha del portal."""
    archivo, datos = _nombre_archivo_diocesano("alava")
    parroquia = fila.get("parroquia") or "[RELLENAR]"
    localidad = fila.get("localidad") or fila.get("municipio") or "[RELLENAR]"
    padre = (fila.get("padre") or {}).get("completo", "")
    madre = (fila.get("madre") or {}).get("completo", "")
    referencia = (f"registro {fila.get('id')} · fondo {fila.get('fondo')} · "
                  f"sig. {fila.get('signatura')} · folio {fila.get('folio')}")
    cuerpo = (
        f"Estimados señores:\n\n"
        f"Me dirijo al Archivo Histórico Diocesano de Vitoria para solicitar "
        f"copia literal (o reproducción digital certificada) de esta partida, "
        f"localizada en su buscador de registros sacramentales:\n\n"
        f"  - Bautizado: {fila.get('persona', {}).get('completo', '')}\n"
        f"  - Fecha del sacramento: {fila.get('fecha')}\n"
        + (f"  - Padre: {padre}\n" if padre else "")
        + (f"  - Madre: {madre}\n" if madre else "")
        + f"  - Parroquia: {parroquia}, {localidad} (Álava)\n"
        f"  - Diócesis: {fila.get('diocesis') or 'Vitoria'}\n"
        f"  - Fondo: {fila.get('fondo')}\n"
        f"  - Signatura: {fila.get('signatura')}\n"
        f"  - Folio: {fila.get('folio')}\n"
        f"  - Código de referencia: {fila.get('cod_referencia')}\n"
        f"  - Registro informático: {fila.get('id')}\n"
        f"  - Ficha del portal: {fila.get('url')}\n\n"
        f"Motivo: investigación genealógica familiar. La partida tiene más de "
        f"100 años, por lo que entiendo que no le afecta la normativa de "
        f"protección de datos.\n\n"
        f"Les agradecería que la copia incluya, si el asiento lo recoge, los "
        f"nombres de los abuelos paternos y maternos, los padrinos y cualquier "
        f"nota marginal (dispensa, legitimación, enmienda).\n\n"
        f"Quedo a su disposición para abonar la tasa que corresponda.\n\n"
        f"Muchas gracias por su trabajo.\n\n"
        f"Atentamente,\n\n"
        f"  Nombre y apellidos: [RELLENAR]\n"
        f"  DNI/NIE: [RELLENAR]\n"
        f"  Dirección postal: [RELLENAR]\n"
        f"  Teléfono y correo: [RELLENAR]\n"
        f"  Parentesco: [RELLENAR: p. ej. bisnieto del bautizado]\n")
    escrito = (fila.get("persona", {}).get("completo", ""))
    return _solicitud(
        RAMA_ALAVA, escrito,
        f"{archivo} (AHDV-GEAH)", datos["email"],
        "copia_literal_bautismo", referencia,
        f"Solicitud de copia literal de partida de bautismo ({fila.get('fecha')}"
        f") — {parroquia}, {localidad}", cuerpo,
        notas=(f"compatibilidad: {persona.get('nombre', '')} "
               f"({persona.get('origen', '')}) · "
               + ("el NOMBRE DE PILA coincide: es la partida de esa persona"
                  if _coincide_el_nombre(fila, persona) else
                  "OJO: coinciden los apellidos y la fecha, pero el nombre de "
                  "pila es distinto (probable hermano: pedir esta copia solo "
                  "si la del nombre exacto no aparece)")),
        url_tramite=datos.get("url_tramite", ""),
        como_se_pide=datos.get("como_se_pide", ""))


def solicitudes_de_rama(rama: str, personas: list[dict],
                        analisis: list[dict] | None = None) -> list[dict]:
    """Solicitudes que hay que enviar para una rama.

    ÁLAVA (paterna de la madre): una copia literal al AHDV por cada partida
    COMPATIBLE o COMPATIBLE CON RESERVAS (las INCOMPATIBLES y las que no
    identifican a nadie no se piden: gastarían tasa sin poder encajarlas).
    ZAMORA y PALENCIA: solo hay archivo diocesano y Registro Civil, así que se
    redacta una carta diocesana por persona con localidad conocida y, si el
    nacimiento es de 1871 en adelante y el año es un DATO, el certificado
    GRATUITO del Registro Civil.
    """
    rama = resolver_rama(rama)
    if rama == RAMA_ALAVA:
        solicitudes = []
        for candidato in analisis or []:
            if candidato["evaluacion"]["veredicto"] not in (VEREDICTO_COMPATIBLE,
                                                            VEREDICTO_RESERVAS):
                continue
            solicitudes.append(carta_al_ahdv(candidato["fila"],
                                             candidato["persona"]))
        return solicitudes[:MAX_SOLICITUDES]

    solicitudes = []
    for persona in personas:
        if not persona.get("municipio"):
            continue          # sin localidad el archivo no puede buscar
        if persona.get("prioridad") is not None and persona["prioridad"] < 0:
            continue          # la frontera ya la descartó
        encontrado = _nombre_archivo_diocesano(persona.get("provincia", ""))
        if encontrado:
            archivo, datos = encontrado
            tipo = ("partida de bautismo o matrimonio"
                    if persona.get("anio") is None or persona["anio"] < 1871
                    else "partida de bautismo y, si consta, de matrimonio")
            solicitudes.append(_solicitud(
                rama, persona["nombre"], archivo, datos["email"],
                "copia_literal_diocesana",
                f"{persona.get('municipio')} · ~{persona.get('anio') or 'año?'}",
                f"Solicitud de partida ({persona.get('municipio')}) — "
                f"{persona['nombre']}",
                _carta_diocesana(persona, datos, tipo),
                notas=datos.get("aviso", ""),
                url_tramite=datos.get("url_tramite", ""),
                como_se_pide=datos.get("como_se_pide", "")))
        if persona.get("anio") and persona["anio"] >= 1871 and \
                not persona.get("anio_estimado"):
            # Solo se pide el certificado GRATUITO del Registro Civil cuando el
            # año es un DATO: con una estimación de las notas, el registro no
            # tiene por dónde buscar (y en el siglo XX hay protección de datos).
            civil = _contacto_civil(persona.get("municipio", ""))
            if civil:
                solicitudes.append(_solicitud(
                    rama, persona["nombre"], civil["registro"],
                    civil["email"], "certificado_nacimiento_civil",
                    f"{civil.get('municipio')} · ~{persona['anio']}",
                    f"Certificado literal de nacimiento (~{persona['anio']}) — "
                    f"{persona['nombre']}",
                    _carta_civil(persona, civil),
                    notas=f"{civil.get('tasas', 'GRATIS')}; "
                          f"juzgado de paz: {civil.get('juzgado_paz', '—')}",
                    url_tramite=civil.get("url_tramite", ""),
                    como_se_pide=civil.get("como_se_pide", "")))
        if len(solicitudes) >= MAX_SOLICITUDES:
            break
    return solicitudes[:MAX_SOLICITUDES]


# ============== REGISTRO EN estado_investigacion.json (con .bak) ===========

def _clave_solicitud(solicitud: dict) -> tuple:
    return (normalizar(solicitud.get("persona", "")),
            solicitud.get("tipo", ""),
            solicitud.get("referencia", ""))


def registrar_solicitudes(solicitudes: list[dict],
                          base: Path | None = None) -> dict:
    """Apunta las solicitudes en `estado_investigacion.json`.

    - NO duplica: si ya estaba (misma persona + tipo + referencia) se conserva
      la entrada antigua (con su estado real: puede estar ya enviada).
    - Deja `.bak` del fichero ANTES de escribir (config.escribir_con_backup,
      que además rota las tres copias).
    """
    base = base if base is not None else BASE_DIR
    ruta = base / ESTADO_PATH
    estado = _leer_json(ruta)
    existentes = estado.get("solicitudes")
    if not isinstance(existentes, list):
        existentes = []
    vistas = {_clave_solicitud(s) for s in existentes if isinstance(s, dict)}
    nuevas = [s for s in solicitudes if _clave_solicitud(s) not in vistas]
    if not nuevas:
        return {"nuevas": 0, "ya_estaban": len(solicitudes),
                "total": len(existentes), "ruta": str(ruta), "bak": None}
    estado["solicitudes"] = existentes + nuevas
    estado["actualizado"] = datetime.now().isoformat(timespec="seconds")
    respaldo = escribir_con_backup(
        ruta, json.dumps(estado, ensure_ascii=False, indent=2))
    return {"nuevas": len(nuevas), "ya_estaban": len(solicitudes) - len(nuevas),
            "total": len(estado["solicitudes"]), "ruta": str(ruta),
            "bak": respaldo}


# ============================== INFORME ====================================

def redactar_markdown(rama: str, personas: list[dict],
                      analisis: list[dict],
                      solicitudes: list[dict]) -> str:
    """Informe de la rama, listo para leer y para enviar (con las cartas)."""
    lineas = [f"# {TITULO[rama]}",
              "",
              f"Generado: {datetime.now():%Y-%m-%d %H:%M} · "
              f"rama **{rama}** · {len(personas)} persona(s) del árbol",
              ""]
    lineas += ["## Personas de la rama", ""]
    for persona in personas:
        prioridad = (f"{persona['prioridad']:+.1f}"
                     if persona.get("prioridad") is not None else "—")
        lineas.append(
            f"- **{persona['nombre']}** — {persona.get('municipio') or '¿?'}"
            f" ({persona.get('provincia') or '¿?'}), "
            f"~{persona.get('anio') or '¿?'}"
            f"{' (estimado)' if persona.get('anio_estimado') else ''}"
            f" · origen: {persona.get('origen')}"
            f" · prioridad: {prioridad}")
    lineas.append("")
    if analisis:
        candidatas = [c for c in analisis
                      if c["evaluacion"]["veredicto"] in (VEREDICTO_COMPATIBLE,
                                                          VEREDICTO_RESERVAS)]
        descartadas = [c for c in analisis if c not in candidatas]
        lineas += ["## Partidas encontradas en el índice y cotejo con el árbol",
                   "",
                   "Regla del proyecto: hacen falta **≥2 datos independientes** "
                   "para dar por buena una partida. De "
                   f"{len(analisis)} partida(s), **{len(candidatas)}** pasan el "
                   f"corte y {len(descartadas)} quedan fuera.", ""]
        for candidato in candidatas:
            fila = candidato["fila"]
            ev = candidato["evaluacion"]
            lineas.append(
                f"### {fila.get('fecha')} — {fila.get('persona', {}).get('completo')}"
                f"  ·  {ev['veredicto']}  ({ev['datos']:g} dato(s))")
            lineas.append(f"- Parroquia: {fila.get('parroquia')}, "
                          f"{fila.get('localidad')} ({fila.get('municipio')})")
            if fila.get("padre") or fila.get("madre"):
                lineas.append(f"- Hijo de {(fila.get('padre') or {}).get('completo')}"
                              f" y {(fila.get('madre') or {}).get('completo')}")
            lineas.append(f"- Cita: fondo {fila.get('fondo')}, "
                          f"sig. {fila.get('signatura')}, folio {fila.get('folio')}"
                          f" · {fila.get('url')}")
            lineas.append(f"- Cotejo con **{candidato['persona']['nombre']}**:")
            for texto in ev["coincidencias"]:
                lineas.append(f"  - casa: {texto}")
            for texto in ev["reservas"]:
                lineas.append(f"  - reserva: {texto}")
            lineas.append("")
        if descartadas:
            lineas += ["### Descartadas (para que se vea qué se ha mirado)", ""]
            for candidato in descartadas[:60]:
                fila = candidato["fila"]
                ev = candidato["evaluacion"]
                motivo = (ev["conflictos"] or
                          [f"solo {ev['datos']:g} dato(s)"])[0]
                lineas.append(
                    f"- {fila.get('fecha')} · "
                    f"{fila.get('persona', {}).get('completo')} · "
                    f"{fila.get('localidad')} — {ev['veredicto']}: {motivo}")
            if len(descartadas) > 60:
                lineas.append(f"- … y {len(descartadas) - 60} más (todas "
                              f"descartadas por el mismo motivo: apellido "
                              f"endémico del pueblo, sin 2 datos).")
            lineas.append("")
    if solicitudes:
        lineas += [f"## Solicitudes a enviar ({len(solicitudes)})", "",
                   "Estado: **pendiente_envio**. Ya registradas en "
                   f"`{ESTADO_PATH}`.", ""]
        # DÓNDE se pide cada cosa: cada archivo tiene su método. Se agrupa por
        # sitio para que no haya que buscarlo veinte veces.
        sitios: dict[str, dict] = {}
        for sol in solicitudes:
            clave = sol["archivo"]
            sitios.setdefault(clave, {"url_tramite": sol.get("url_tramite", ""),
                                      "contacto": sol["contacto"],
                                      "como_se_pide": sol.get("como_se_pide", ""),
                                      "cuantas": 0})
            sitios[clave]["cuantas"] += 1
        lineas += ["### Dónde se pide (enlaces verificados)", "",
                   "| Archivo | Cuántas | Enlace para pedir | Cómo |",
                   "|---|---:|---|---|"]
        for archivo, datos_sitio in sitios.items():
            enlace = (f"[abrir]({datos_sitio['url_tramite']})"
                      if datos_sitio["url_tramite"] else "—")
            como = datos_sitio["como_se_pide"] or f"email a {datos_sitio['contacto']}"
            lineas.append(f"| {archivo} | {datos_sitio['cuantas']} | {enlace} | "
                          f"{como} |")
        lineas.append("")
        for i, sol in enumerate(solicitudes, 1):
            lineas += [f"### {i}. {sol['tipo']} — {sol['persona']}",
                       f"- **Dónde se pide**: {sol.get('url_tramite') or '(por email)'}",
                       f"- **Cómo**: {sol.get('como_se_pide') or 'por email'}",
                       f"- **Para**: {sol['contacto']}",
                       f"- **Archivo**: {sol['archivo']}",
                       f"- **Asunto**: {sol['asunto']}",
                       f"- **Referencia interna**: {sol['referencia']}"]
            if sol.get("notas"):
                lineas.append(f"- **Notas**: {sol['notas']}")
            lineas += ["", "```", sol["cuerpo"].rstrip(), "```", ""]
    else:
        lineas += ["## Solicitudes a enviar", "",
                   "Ninguna: en esta rama no hay partidas compatibles que "
                   "pedir o ninguna persona con localidad conocida.", ""]
    return "\n".join(lineas)
