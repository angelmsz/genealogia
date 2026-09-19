"""
scrapers/artxibo.py — Conector del buscador de registros sacramentales de
artxibo.euskadi.eus (Dokuklik / IRARGI), el portal que publica el Archivo
Histórico Diocesano de Vitoria (AHDV-GEAH): bautismos, matrimonios y
defunciones de Álava indexados nominalmente de 1481 a 1900. Acceso libre,
gratuito, sin credenciales.

Por qué un conector nuevo si ya existe SIGA (scrapers/archivos.py):
  * SIGA devuelve HTML y su índice parte los apellidos compuestos en tokens.
  * artxibo devuelve JSON (la tabla DataTables del buscador llama a una API
    interna: ``busquedaBautismo`` / ``busquedaMatrimonio`` /
    ``busquedaDefuncion``) y ACEPTA EL APELLIDO COMPUESTO COMPLETO. Medido el
    2026-09-19 sobre el mismo fondo documental:
        'Saenz de Navarrete' (completo, 1850-1900) .....     79 filas
        'Saenz'            (token suelto) ...............  7.009 filas
    Es decir: fragmentar el apellido multiplica el ruido por 89. Por eso aquí
    se busca SIEMPRE el compuesto primero y solo se fragmenta si devuelve 0
    filas, marcando entonces el resultado como de CONFIANZA BAJA.
  * Cada fila trae folio, signatura, fondo y diócesis: la cita documental sale
    completa sin necesidad de abrir la ficha.

Uso:
    filas, confianza = buscar_apellido("Saenz de Navarrete", anio_ini=1850,
                                       anio_fin=1900)
    datos = ficha("bautismo", 6210597)     # HTML de la ficha -> campos

Todo el módulo es OFFLINE en los tests: la sesión se inyecta y las respuestas
salen de ``tests/fixtures/artxibo_*``.
"""

from __future__ import annotations

import random
import re
import time

from bs4 import BeautifulSoup

from config import (ARTXIBO_ANIO_MAX, ARTXIBO_ANIO_MIN, ARTXIBO_ARCHIVO_VITORIA,
                    ARTXIBO_BUSQUEDA_URL, ARTXIBO_FICHA_URL,
                    ARTXIBO_FILAS_POR_DOC, ARTXIBO_MAX_FILAS,
                    ARTXIBO_SACRAMENTALES_URL, ARTXIBO_TIPOS,
                    CONECTOR_MAX_CONSULTAS, DELAY_DESCARGAS,
                    MARCA_CONFIANZA_BAJA, SESSION, _conector_en_cooldown,
                    _consulta_conector_hecha, _marcar_conector,
                    _marcar_conector_fallo, _tokens_apellido, normalizar,
                    sin_tildes)
from utils import ui
from utils.llm import PresupuestoExcedido

CONFIANZA_ALTA = "alta"
CONFIANZA_BAJA = "baja"

# Filas máximas que se piden por token cuando hay que fragmentar. El índice
# devuelve miles (7.009 para 'Saenz'): no se pagina a propósito — el objetivo
# es dar una pista marcada como poco fiable, no descargar el índice entero.
MAX_FILAS_TOKEN = 40

# Cuántas filas devuelve el portal en UNA petición (verificado en vivo: 100).
FILAS_POR_PETICION = 100

_PREFIJO_ID = {"bautismo": "bauid", "matrimonio": "matid", "defuncion": "defid"}

# Nombre del fichero oculto que el buscador publica en el action del
# formulario: sin él, la API interna responde con la página de error.
_RE_JSESSIONID = re.compile(r"jsessionid=([^?\"';]+)")

# Columnas que la propia página envía a DataTables (una por tipo). Solo afectan
# al orden/búsqueda del lado servidor: no hay que inventarlas, están en
# sacramentales.js (coldef / coldefMat / coldefDef).
_COLUMNAS = {
    "bautismo": ["baudfecsacra", "baubautizadonom", "bupadrenom",
                 "baumadrenom", "baumunicipio", "baulocalidad",
                 "bauparroquia", "baubautizadonom"],
    "matrimonio": ["matfecsacra", "matesposonom", "matesposanom",
                   "matmunicipio", "matlocalidad", "matparroquia",
                   "matesposanom"],
    "defuncion": ["deffecsacra", "defdifuntonom", "defmunicipio",
                  "deflocalidad", "defparroquia", "defdifuntonom"],
}

# Mapa campo-normalizado -> clave real de la fila JSON. Copiado de una
# respuesta REAL del portal (tests/fixtures/artxibo_busqueda_bautismo_*.json).
_ESQUEMA = {
    "bautismo": {
        "id": "bauid", "fecha": "baudfecsacra",
        "nombre": "baubautizadonom", "ape1": "baubautizadoape1",
        "ape2": "baubautizadoape2",
        "padre_nom": "baupadrenom", "padre_ape1": "baupadreape1",
        "padre_ape2": "baupadreape2",
        "madre_nom": "baumadrenom", "madre_ape1": "baumadreape1",
        "madre_ape2": "baumadreape2",
        "municipio": "baumunicipio", "municipio_oficial": "baumunioficial",
        "localidad": "baulocalidad", "parroquia": "bauparroquia",
        "diocesis": "baudiocesis", "territorio": "bauterritorio",
        "fondo": "baufondo", "signatura": "bausigna",
        "signatura_digital": "bausignadigital",
        "signatura_micro": "bausignamicro", "folio": "baufoliorv",
        "num_partida": "baunumpar", "cod_referencia": "baucodref",
        "titulo": "bautitulo", "observaciones": "bauobserva",
        "anio_libro_ini": "baufecacumini", "anio_libro_fin": "baufecacumfin",
        "id_original": "bauidoriginal",
    },
    "matrimonio": {
        "id": "matid", "fecha": "matfecsacra",
        "nombre": "matesposonom", "ape1": "matesposoape1",
        "ape2": "mathesposope2",
        "conyuge_nom": "matesposanom", "conyuge_ape1": "matesposaape1",
        "conyuge_ape2": "matesposaape2",
        "municipio": "matmunicipio", "municipio_oficial": "matmunioficial",
        "localidad": "matlocalidad", "parroquia": "matparroquia",
        "diocesis": "matdiocesis", "territorio": "matterritorio",
        "fondo": "matfondo", "signatura": "matsigna",
        "signatura_digital": "matsignadigital",
        "signatura_micro": "matsignamicro", "folio": "matfoliorv",
        "num_partida": "matnumpar", "cod_referencia": "matcodref",
        "titulo": "mattitulo", "observaciones": "matobserva",
        "anio_libro_ini": "matfecacumini", "anio_libro_fin": "matfecacumfin",
        "id_original": "matidoriginal",
    },
    "defuncion": {
        "id": "defid", "fecha": "deffecsacra",
        "nombre": "defdifuntonom", "ape1": "defdifuntoape1",
        "ape2": "defdifuntoape2",
        "municipio": "defmunicipio", "municipio_oficial": "defmunioficial",
        "localidad": "deflocalidad", "parroquia": "defparroquia",
        "diocesis": "defdiocesis", "territorio": "defterritorio",
        "fondo": "deffondo", "signatura": "defsigna",
        "signatura_digital": "defsignadigital",
        "signatura_micro": "defsignamicro", "folio": "deffoliorv",
        "num_partida": "defnumpar", "cod_referencia": "defcodref",
        "titulo": "deftitulo", "observaciones": "defobserva",
        "anio_libro_ini": "deffecacumini", "anio_libro_fin": "deffecacumfin",
        "id_original": "defidoriginal",
    },
}

ETIQUETA_EVENTO = {"bautismo": "Bautismo", "matrimonio": "Matrimonio",
                   "defuncion": "Defunción"}


# ============================ SESIÓN Y JSESSIONID ==========================

# id(sesion) -> (sesion, jsessionid). Se guarda TAMBIÉN la sesión para que el
# recolector de basura no reutilice su id() en otra sesión (el mismo fallo que
# obligó a arreglar la caché de sesiones por hilo).
_JSID: dict[int, tuple[object, str]] = {}


def _sesion(sesion=None):
    return sesion if sesion is not None else SESSION


def jsessionid(sesion=None, forzar: bool = False) -> str:
    """jsessionid que el buscador incrusta en el action del formulario.

    Sin él, la API interna contesta con la página de error en vez de JSON.
    Se cachea por sesión; ``forzar=True`` lo vuelve a pedir (la sesión caduca).
    """
    s = _sesion(sesion)
    clave = id(s)
    guardado = _JSID.get(clave)
    if guardado is not None and not forzar and guardado[0] is s:
        return guardado[1]
    r = s.get(ARTXIBO_SACRAMENTALES_URL, params={"locale": "es"}, timeout=30)
    r.raise_for_status()
    m = _RE_JSESSIONID.search(r.text or "")
    jsid = m.group(1) if m else ""
    _JSID[clave] = (s, jsid)
    return jsid


def _url_con_jsid(url: str, jsid: str) -> str:
    return f"{url};jsessionid={jsid}" if jsid else url


# ============================== BÚSQUEDA ===================================

def _limpio(valor) -> str:
    """Valor de una casilla del portal -> texto utilizable.

    El portal rellena los huecos con '--' / '-----' y devuelve None en los
    campos vacíos."""
    if valor is None:
        return ""
    texto = str(valor).strip()
    if re.fullmatch(r"-{2,}", texto):
        return ""
    return texto


def _persona(cruda: dict, esq: dict, prefijo: str = "") -> dict:
    """Nombre/apellidos de una persona de la fila (``prefijo`` vacío = la
    persona principal; 'padre', 'madre' o 'conyuge' para el resto)."""
    claves = (("nombre", "ape1", "ape2") if not prefijo
              else (f"{prefijo}_nom", f"{prefijo}_ape1", f"{prefijo}_ape2"))
    partes = [_limpio(cruda.get(esq.get(clave, ""))) for clave in claves]
    return {"nombre": partes[0], "apellido1": partes[1], "apellido2": partes[2],
            "completo": " ".join(p for p in partes if p)}


def _anio(fecha: str):
    m = re.match(r"(\d{4})", fecha or "")
    return int(m.group(1)) if m else None


def _linea(fila: dict) -> str:
    """Una fila del índice como línea de corpus (con la cita dentro)."""
    partes = [f"{ETIQUETA_EVENTO.get(fila['tipo'], 'Registro')} "
              f"{fila.get('fecha') or 'sin fecha'}."]
    quien = fila.get("persona", {}).get("completo", "")
    if quien:
        partes.append(f"{quien}.")
    padre = (fila.get("padre") or {}).get("completo", "")
    madre = (fila.get("madre") or {}).get("completo", "")
    if padre or madre:
        partes.append("Hijo de " + " y ".join(p for p in (padre, madre) if p) + ".")
    if fila.get("conyuge", {}).get("completo"):
        partes.append(f"Cónyuge: {fila['conyuge']['completo']}.")
    lugar = [p for p in (fila.get("parroquia"), fila.get("localidad"),
                         fila.get("municipio"), fila.get("territorio")) if p]
    if lugar:
        partes.append("Parroquia " + ", ".join(lugar) + ".")
    if fila.get("diocesis"):
        partes.append(f"Diócesis de {fila['diocesis']}.")
    cita = []
    if fila.get("fondo"):
        cita.append(f"fondo {fila['fondo']}")
    if fila.get("signatura"):
        cita.append(f"sig. {fila['signatura']}")
    if fila.get("folio"):
        cita.append(f"folio {fila['folio']}")
    if fila.get("num_partida"):
        cita.append(f"nº partida {fila['num_partida']}")
    if cita:
        partes.append("Índice del AHDV (" + ", ".join(cita) + ").")
    if fila.get("url"):
        partes.append(f"Ficha: {fila['url']}")
    return " ".join(partes)


def parsear_resultados(datos, tipo: str = "bautismo") -> list[dict]:
    """Respuesta JSON del buscador -> filas normalizadas (función pura)."""
    if not isinstance(datos, dict):
        return []
    esq = _ESQUEMA[tipo]
    salida = []
    for cruda in datos.get("data") or []:
        if not isinstance(cruda, dict):
            continue
        fila = {"tipo": tipo, "confianza": CONFIANZA_ALTA}
        for campo, clave in esq.items():
            if campo in ("nombre", "ape1", "ape2"):
                continue        # los pone _persona()
            valor = cruda.get(clave)
            if campo in ("id", "id_original"):
                try:
                    valor = int(valor)
                except (TypeError, ValueError):
                    valor = None if campo == "id" else ""
            else:
                valor = _limpio(valor)
            fila[campo] = valor
        if fila.get("id") is None:
            continue
        fila["persona"] = _persona(cruda, esq)
        fila["anio"] = _anio(fila.get("fecha", ""))
        if tipo == "bautismo":
            fila["padre"] = _persona(cruda, esq, "padre")
            fila["madre"] = _persona(cruda, esq, "madre")
        elif tipo == "matrimonio":
            fila["conyuge"] = _persona(cruda, esq, "conyuge")
        fila["url"] = (f"{ARTXIBO_FICHA_URL[tipo]}"
                       f"?{_PREFIJO_ID[tipo]}={fila['id']}")
        fila["texto"] = _linea(fila)
        salida.append(fila)
    return salida


def construir_payload(tipo: str = "bautismo", apellido1: str = "",
                      apellido2: str = "", nombre: str = "",
                      anio_ini=None, anio_fin=None,
                      archivo: str = ARTXIBO_ARCHIVO_VITORIA,
                      municipio=None, localidad=None, parroquia=None,
                      start: int = 0, length: int = 100) -> dict:
    """Cuerpo JSON que espera la API interna (protocolo de DataTables + los
    campos del formulario simple). Las claves ``bautismoHijo*`` las usa el
    propio JS de la página para los TRES sacramentos (verificado en
    sacramentales.js: el DataTable de matrimonios reenvía esas mismas claves).
    """
    if tipo not in ARTXIBO_TIPOS:
        raise ValueError(f"tipo de sacramento desconocido: {tipo!r}")
    return {
        "draw": 1,
        "columns": [{"data": c, "name": "", "searchable": True,
                     "orderable": True,
                     "search": {"value": "", "regex": False}}
                    for c in _COLUMNAS[tipo]],
        "order": [{"column": 0, "dir": "asc"}],
        "start": start, "length": length,
        "search": {"value": "", "regex": False},
        "archivosDiocesanos": [archivo] if archivo else [],
        "municipio": list(municipio or []),
        "localidad": list(localidad or []),
        "parroquia": list(parroquia or []),
        "anioInicial": str(anio_ini or ""),
        "anioFinal": str(anio_fin or ""),
        "bautismoHijoNombre": nombre or "",
        "bautismoHijoApellido1": apellido1 or "",
        "bautismoHijoApellido2": apellido2 or "",
    }


def buscar_sacramentales(tipo: str = "bautismo", apellido1: str = "",
                         apellido2: str = "", nombre: str = "",
                         anio_ini=ARTXIBO_ANIO_MIN,
                         anio_fin=ARTXIBO_ANIO_MAX,
                         archivo: str = ARTXIBO_ARCHIVO_VITORIA,
                         municipio=None, parroquia=None,
                         max_filas: int = ARTXIBO_MAX_FILAS,
                         sesion=None) -> list[dict]:
    """Una búsqueda en el buscador sacramental de artxibo (una sola petición).

    Devuelve filas normalizadas (lista vacía si no hay resultados). Si el
    portal no responde, LANZA la excepción para que el llamador NO cachee la
    consulta como hecha (mismo criterio que siga_buscar).
    """
    s = _sesion(sesion)
    jsid = jsessionid(s)
    payload = construir_payload(
        tipo=tipo, apellido1=apellido1, apellido2=apellido2, nombre=nombre,
        anio_ini=anio_ini, anio_fin=anio_fin, archivo=archivo,
        municipio=municipio, parroquia=parroquia,
        length=min(max(int(max_filas), 1), FILAS_POR_PETICION))
    r = s.post(_url_con_jsid(ARTXIBO_BUSQUEDA_URL[tipo], jsid), json=payload,
               timeout=60, headers={"X-Requested-With": "XMLHttpRequest"})
    r.raise_for_status()
    try:
        crudo = r.json()
    except ValueError as exc:
        # El portal contesta la página de error (HTML) si la sesión caducó.
        raise RuntimeError(
            "artxibo: la búsqueda no devolvió JSON (sesión caducada o "
            "cambio de formato del portal)") from exc
    filas = parsear_resultados(crudo, tipo)
    # La API no filtra de verdad por municipio/parroquia con texto libre: si el
    # llamador los pidió, se recorta aquí para no colar filas de otro pueblo.
    if municipio:
        quiero = {normalizar(m) for m in municipio}
        filas = [f for f in filas
                 if normalizar(f.get("municipio", "")) in quiero
                 or normalizar(f.get("municipio_oficial", "")) in quiero]
    if parroquia:
        quiero = {normalizar(p) for p in parroquia}
        filas = [f for f in filas if normalizar(f.get("parroquia", "")) in quiero]
    return filas[:max_filas]


def buscar_apellido(apellido: str, tipo: str = "bautismo",
                    anio_ini=ARTXIBO_ANIO_MIN, anio_fin=ARTXIBO_ANIO_MAX,
                    sesion=None, **extra) -> tuple[list[dict], str]:
    """El ARREGLO de la fragmentación de apellidos compuestos.

    1. Busca el apellido COMPLETO ('Saenz de Navarrete' tal cual): medido en
       vivo, devuelve 79 filas donde el token suelto devuelve 7.009.
    2. Solo si el compuesto devuelve CERO se prueban los tokens sueltos; esos
       resultados vuelven con ``confianza='baja'`` y con la marca
       MARCA_CONFIANZA_BAJA dentro de su línea de corpus, para que no pesen
       igual que los de confianza alta.
    """
    completo = (apellido or "").strip()
    if not completo:
        return [], CONFIANZA_ALTA
    filas = buscar_sacramentales(tipo=tipo, apellido1=completo,
                                 anio_ini=anio_ini, anio_fin=anio_fin,
                                 sesion=sesion, **extra)
    if filas:
        return filas, CONFIANZA_ALTA
    tokens = _tokens_apellido(completo)
    if len(tokens) <= 1:
        return [], CONFIANZA_ALTA      # apellido simple: nada que fragmentar
    vistas: dict = {}
    for tok in tokens:
        for forma in dict.fromkeys((tok, sin_tildes(tok))):
            for fila in buscar_sacramentales(
                    tipo=tipo, apellido1=forma, anio_ini=anio_ini,
                    anio_fin=anio_fin, sesion=sesion,
                    max_filas=MAX_FILAS_TOKEN, **extra):
                vistas.setdefault(fila["id"], fila)
    for fila in vistas.values():
        fila["confianza"] = CONFIANZA_BAJA
        fila["texto"] = f"[{MARCA_CONFIANZA_BAJA}] {fila['texto']}"
    if vistas:
        ui.log_warn(
            f"artxibo: el apellido completo '{completo}' no dio resultados; "
            f"se ha fragmentado en {tokens} ({len(vistas)} filas marcadas de "
            f"CONFIANZA BAJA).")
    return list(vistas.values()), (CONFIANZA_BAJA if vistas else CONFIANZA_ALTA)


# ================================ FICHAS ===================================

def parsear_ficha(html: str, tipo: str = "bautismo") -> dict:
    """Ficha de artxibo (HTML) -> campos estructurados (función pura).

    La ficha trae las filas ``div.tabla-titulo`` / ``div.tabla-texto`` dentro
    del bloque ``div.ficha-resultados.sacramentales``; el resto de la página
    (avisos, formulario de sugerencias) se ignora.

    OJO: la ficha NO trae parroquia, abuelos ni padrinos. La parroquia sale de
    la fila del buscador y los demás, de la copia literal del libro.
    """
    sopa = BeautifulSoup(html or "", "html.parser")
    datos: dict = {"tipo": tipo, "campos": {}, "personas": {}}
    bloque = sopa.select_one("div.ficha-resultados.sacramentales") or sopa
    for fila in bloque.select("div.row"):
        tit = fila.select_one("div.tabla-titulo")
        cont = fila.select_one("div.tabla-texto")
        if tit is None or cont is None:
            continue
        etiqueta = re.sub(r"\s+", " ", tit.get_text()).strip()
        valor = re.sub(r"\s+", " ", cont.get_text()).strip().rstrip(",").strip()
        if etiqueta and etiqueta not in datos["campos"]:
            datos["campos"][etiqueta] = valor
    for oculto in sopa.find_all("input", type="hidden"):
        if oculto.get("id") in ("idsacra", "bauid", "matid", "defid"):
            try:
                datos["id"] = int(oculto.get("value"))
            except (TypeError, ValueError):
                pass
    # 'Hijo'/'Padre'/'Madre' llegan como "Nombre, Apellido1, Apellido2" (con
    # '--' cuando el índice no tenía el dato).
    for etiqueta, clave in (("Hijo", "persona"), ("Esposo", "persona"),
                            ("Difunto", "persona"), ("Padre", "padre"),
                            ("Madre", "madre"), ("Esposa", "conyuge")):
        valor = datos["campos"].get(etiqueta)
        if not valor:
            continue
        piezas = [p for p in (_limpio(x) for x in valor.split(",")) if p]
        datos["personas"][clave] = {
            "nombre": piezas[0] if piezas else "",
            "apellido1": piezas[1] if len(piezas) > 1 else "",
            "apellido2": piezas[2] if len(piezas) > 2 else "",
            "completo": " ".join(piezas),
        }
    fecha = datos["campos"].get("Fecha del sacramento", "")
    m = re.fullmatch(r"(\d{1,2})-(\d{1,2})-(\d{4})", fecha)
    datos["fecha"] = (f"{m.group(3)}-{m.group(2).zfill(2)}-{m.group(1).zfill(2)}"
                      if m else "")
    for enlace in sopa.find_all("a", href=True):
        if "ahdv-geah" in enlace["href"] and "id_" in enlace["href"]:
            datos["url_ahdv"] = enlace["href"]
    return datos


def ficha(tipo: str = "bautismo", id_=None, sesion=None) -> dict:
    """Descarga y parsea la ficha de un registro (bauid / matid / defid)."""
    if id_ in (None, ""):
        raise ValueError("ficha(): hace falta el id del registro")
    s = _sesion(sesion)
    url = f"{ARTXIBO_FICHA_URL[tipo]}?{_PREFIJO_ID[tipo]}={id_}"
    r = s.get(url, timeout=45)
    r.raise_for_status()
    datos = parsear_ficha(r.text, tipo)
    datos.setdefault("id", id_)
    datos["url"] = url
    return datos


# ============================== RECOLECTOR =================================

def _agrupar_docs(filas: list[dict], etiqueta: str, apellido: str,
                  confianza: str) -> list[dict]:
    """Filas del índice agrupadas en documentos del corpus (como SIGA: una
    llamada de fase 2 por cada pocas filas, no una por fila)."""
    out = []
    for i in range(0, len(filas), ARTXIBO_FILAS_POR_DOC):
        grupo = filas[i:i + ARTXIBO_FILAS_POR_DOC]
        if not grupo:
            continue
        bajo = confianza == CONFIANZA_BAJA
        aviso = (f" {MARCA_CONFIANZA_BAJA}: estos resultados salen de partir "
                 f"'{apellido}' en trozos, así que muchos serán homónimos de "
                 f"otras familias." if bajo else "")
        out.append({
            "origen": "artxibo",
            "url": grupo[0].get("url", ""),
            "titulo": (f"artxibo/AHDV — {etiqueta} (filas {i + 1}-"
                       f"{i + len(grupo)})" + (" [CONFIANZA BAJA]" if bajo else "")),
            "confianza": confianza,
            "texto": ("Índice de registros sacramentales del Archivo Histórico "
                      "Diocesano de Vitoria publicado en artxibo.euskadi.eus "
                      f"(Álava, 1481-1900). Búsqueda: {etiqueta}." + aviso
                      + "\n" + "\n".join(f"- {f['texto']}" for f in grupo)),
        })
    return out


def recolector_artxibo(objetivo: dict, conn=None) -> list[dict]:
    """Registros sacramentales de artxibo para el objetivo (solo Álava/Araba).

    Estrategia:
      1. Apellido COMPLETO primero; los tokens solo si el compuesto devuelve 0
         filas, y entonces los documentos van marcados como CONFIANZA BAJA.
      2. Búsqueda PROVINCIAL: a propósito NO se filtra por el municipio del
         objetivo. El árbol daba por hecho 'Vitoria' para la rama Sáenz de
         Navarrete y el índice la sitúa en Navaridas; filtrar por el municipio
         del objetivo sería tirar justo la partida que buscamos.
    """
    provs = {normalizar(p) for p in (objetivo.get("provincias", [])
                                     or [objetivo.get("provincia", "")])}
    if not provs & {"alava", "araba"}:
        return []
    apellidos = [a.strip() for a in (objetivo.get("apellido_paterno"),
                                     objetivo.get("apellido_materno")) if a]
    if not apellidos:
        return []
    docs: list[dict] = []
    consultas = 0
    for ap in apellidos:
        if consultas >= CONECTOR_MAX_CONSULTAS:
            break
        clave = f"artxibo::bautismo::{normalizar(ap)}"
        if _conector_en_cooldown(conn, clave):
            continue
        if _consulta_conector_hecha(conn, clave):
            continue
        try:
            with ui.Indicador(f"artxibo: '{ap}'", nivel="search"):
                filas, confianza = buscar_apellido(ap, tipo="bautismo")
        except PresupuestoExcedido:
            raise
        except Exception as e:
            # Fallo transitorio (o sesión caducada): cooldown, NO cachear como
            # hecha, y se reintenta tras el TTL.
            _marcar_conector_fallo(conn, clave)
            _JSID.clear()
            ui.log_warn(f"artxibo no respondió ({ap!r}): {str(e)[:100]} "
                        f"(queda en cooldown; reintento tras el TTL)")
            continue
        _marcar_conector(conn, clave)
        consultas += 1
        if not filas:
            ui.log_warn(f"artxibo: sin resultados para '{ap}' en el índice de "
                        f"Álava 1481-1900.")
            continue
        docs += _agrupar_docs(filas, f"bautismos '{ap}' (toda Álava)", ap,
                              confianza)
        time.sleep(random.uniform(*DELAY_DESCARGAS))
    if docs:
        ui.log_ok(f"artxibo: {len(docs)} documentos de Álava "
                  f"({consultas} consultas)")
    return docs
