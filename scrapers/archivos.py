"""
scrapers/archivos.py — Conectores directos con buscadores nominales de
archivos diocesanos y estatales españoles.

Tres conectores verificados el 2026-09-07 contra los sitios reales:
  - SIGA (AHDV-GEAH, Álava): sacramentales 1481-1900.
  - ADDO (Archivo Diocesano de Palencia): mejor esfuerzo, sitio volátil.
  - PARES — Catastro de Ensenada (1749-1756).

Las filas de índice de estos buscadores son datos estructurados y entran
DIRECTO al corpus, sin pasar por el filtro de Fase 1 (ahorro de tokens).

Helpers de apellidos:
  - con_comodin(): sustituye vocales acentuadas por % (comodín de SIGA).
  - _tokens_apellido(): parte compuestos ('Saenz de Navarrete' -> tokens).
  - _formas_siga(): formas de búsqueda para SIGA (tokens sueltos primero).
"""

from __future__ import annotations

import random
import time
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from config import (ADDO_BUSQUEDA, ADDO_URL, CONECTOR_MAX_CONSULTAS,
                    DELAY_DESCARGAS, DB_LOCK, MUNICIPIOS_EQUIVALENTES,
                    PAGINAS_CONECTOR, PARES_CATASTRO, PROVINCIAS_SIN_ENSENADA,
                    SESSION, SIGA_FILAS_POR_DOC, SIGA_URL,
                    _conector_en_cooldown, _consulta_conector_hecha,
                    _marcar_conector, _marcar_conector_fallo,
                    normalizar, sin_tildes, _tokens_apellido)
from utils import ui
from utils.llm import PresupuestoExcedido

# v9.1: conectores nuevos (FamilySearch catálogo, HISPAGEN). Import
# perezoso dentro de recolectar() para que este módulo no arrastre
# dependencias nuevas en los import-time de tests que no los usan.


# ============================== v4.3 — FALLO TRANSITORIO ==================

class ParesNoDisponible(Exception):
    """v4.3: PARES respondió pero con un error de SERVIDOR (la típica
    página 'Problema encontrado: Ha ocurrido un error al conectar con la
    Base de Datos') o la conexión falló. NO es un 'no hay resultados':
    tratarlo como tal envenenaba la investigación con NOTAS NEGATIVAS
    falsas. El llamador debe guardarlo como fallo transitorio (cooldown)
    y reintentar más tarde, nunca como consulta hecha."""


def _pares_error_servidor(html: str) -> bool:
    """True si el HTML de PARES es la página de error del servidor (error
    de conexión a su base de datos), verificada contra el sitio real."""
    h = (html or "").lower()
    return ("problema encontrado" in h
            or "error al conectar con la base de datos" in h)


# ============================== SIGA — MAPA DE LOCALIDADES =================
# Copiado del formulario real de internet.ahdv-geah.org. Si añaden
# localidades, actualízalo con --probar-conectores.

SIGA_LOCALIDADES = {
    "añaña": 12, "alegria-dulantzi": 2, "amurrio": 3, "aramaio": 4,
    "armiñón": 5, "arraia-maeztu": 6, "arrazua-ubarrundia": 7,
    "artziniega": 8, "asparrena": 10, "ayala": 11, "baños de ebro": 14,
    "barrundia": 13, "berantevilla": 15, "bernedo": 16, "campezo": 17,
    "elburgo": 18, "elciego": 19, "elvillar": 20, "iruña de oca": 22,
    "iruraiz-gauna": 21, "kripan": 23, "kuartango": 24, "labastida": 25,
    "lagrán": 26, "laguardia": 27, "lanciego": 28, "lantaron": 29,
    "lapuebla de labarca": 30, "legutiano": 31, "leza": 32, "llodio": 33,
    "moreda de álava": 34, "navaridas": 36, "okondo": 37, "orduña": 38,
    "oyon-oion": 39, "peñacerrada-urizaharra": 40, "ribera alta": 42,
    "ribera baja": 43, "salvatierra": 44, "samaniego": 45, "san millán": 47,
    "treviño": 48, "urkabustaiz": 49, "valdegovía": 51, "valle de arana": 53,
    "villabuena de álava": 54, "vitoria-gasteiz": 55, "yécora": 56,
    "zalduondo": 57, "zambrana": 58, "zigoitia": 59, "zuia": 60,
}

SIGA_EVENTO = {"bautismo": "Bautismo", "matrimonio": "Matrimonio",
               "difunto": "Defunción"}


# ============================== HELPERS DE APELLIDOS ======================

def con_comodin(apellido: str) -> str:
    """Sustituye cada vocal acentuada por % (comodín de SIGA que encaja con
    cualquier carácter): 'Sáenz' -> 'S%enz' encuentra Sáenz/Saenz/Saénz."""
    return "".join("%" if ch.lower() in "áéíóúü" else ch
                   for ch in (apellido or "").strip())


def _formas_siga(apellido: str) -> list[str]:
    """Formas de búsqueda de un apellido en SIGA, por orden de rendimiento:
    primero los tokens sueltos (lo que el índice guarda de verdad), después
    el compuesto completo por si acaso. Sin tildes y con comodín si aplica.
    """
    formas: list[str] = []
    for tok in _tokens_apellido(apellido):
        base = sin_tildes(tok)
        formas.append(base)
        comodin = con_comodin(tok)
        if comodin != tok:
            formas.append(sin_tildes(comodin))
    completo = sin_tildes((apellido or "").strip())
    if completo:
        formas.append(completo)
        comodin = con_comodin(apellido)
        if comodin != (apellido or "").strip():
            formas.append(sin_tildes(comodin))
    return list(dict.fromkeys(f for f in formas if f))


# Alias explícitos v10.0: formas coloquiales de municipios del mapa
# ('Vitoria' es como el usuario escribe 'vitoria-gasteiz'). ANTES de la
# v10.0, _siga_localidad devolvía 55 (Vitoria) para CUALQUIER municipio
# desconocido: las búsquedas se hacían en la localidad EQUIVOCADA y la
# caché de conectores las marcaba como hechas para siempre.
_SIGA_ALIASES = {"vitoria": "vitoria-gasteiz"}


def _siga_localidad(municipio: str) -> int | None:
    """id de localidad de SIGA para un municipio dado, o None si el
    municipio NO está en el mapa de localidades de SIGA (solo cubre
    municipios de Álava).

    v10.0 — SIN fallback a Vitoria: antes devolvía 55 para cualquier
    municipio desconocido y las búsquedas iban a la localidad equivocada
    (cacheadas como hechas: búsquedas muertas para siempre). Vitoria se
    devuelve SOLO si el municipio ES Vitoria (se acepta 'Vitoria' como
    alias de 'vitoria-gasteiz').
    """
    n = normalizar(municipio or "")
    n = _SIGA_ALIASES.get(n, n)
    return SIGA_LOCALIDADES.get(n)


def _siga_datos(apellido1: str = "", sacramento: str = "",
                id_localidad: int = 55, apellido2: str = "",
                esposa_apellido1: str = "", nombre: str = "") -> dict:
    """Payload exacto que espera el formulario de SIGA (verificado con F12)."""
    return {
        "accion": "buscar", "auxiliar": "", "sacramento": sacramento,
        "id_sacramento": "", "nombre": nombre,
        "apellido1": apellido1, "apellido2": apellido2, "sexo": "",
        "id_localidad": str(id_localidad), "div_loc": "",
        "fecha_form_ini": "", "fecha_form_fin": "",
        "esposo_nom": "", "esposo_apellido1": apellido1,
        "esposo_apellido2": "",
        "esposa_nom": "", "esposa_apellido1": esposa_apellido1,
        "esposa_apellido2": "",
    }


def _siga_url_query(datos: dict) -> str:
    """URL GET que reproduce la búsqueda (para citarla como fuente)."""
    return SIGA_URL + "?" + urlencode(datos)


def _siga_parse(html: str, datos: dict, sacramento: str) -> list[dict]:
    """Convierte la tabla de resultados de SIGA en documentos sintéticos.
    La cabecera con los nombres de columna se localiza dinámicamente
    buscando la fila que contiene 'Fecha'."""
    if "NO SE ENCONTRARON RESULTADOS" in html.upper():
        return []
    s = BeautifulSoup(html, "html.parser")
    docs = []
    for tabla in s.find_all("table"):
        filas = tabla.find_all("tr")
        if len(filas) < 3:
            continue
        i_cab, cabecera = None, []
        for i, tr in enumerate(filas[:6]):
            celdas_c = [c.get_text(strip=True).lower()
                        for c in tr.find_all(["td", "th"])]
            if any("fecha" in c for c in celdas_c):
                i_cab, cabecera = i, celdas_c
                break
        if i_cab is None:
            continue
        i_fecha = next(i for i, c in enumerate(cabecera) if "fecha" in c)
        i_fondo = next((i for i, c in enumerate(cabecera) if "fondo" in c),
                       None)
        i_loc = next((i for i, c in enumerate(cabecera)
                      if "localidad" in c), None)
        i_mun = next((i for i, c in enumerate(cabecera)
                      if "municipio" in c), None)
        i_sexo = next((i for i, c in enumerate(cabecera) if "sexo" in c),
                      None)
        for tr in filas[i_cab + 1:]:
            celdas = [c.get_text(strip=True) for c in tr.find_all("td")]
            if len(celdas) < max(i_fecha + 1, 3):
                continue
            persona = celdas[2] if len(celdas) > 2 else ""
            if not persona or not celdas[i_fecha]:
                continue
            persona = (persona.replace("[esposo]", "Esposo: ")
                       .replace("[esposa]", "Esposa: ")
                       .replace("-----", "apellidos-completar"))
            evento = SIGA_EVENTO.get(sacramento, "Registro sacramental")
            fecha = celdas[i_fecha]
            id_reg = celdas[1] if len(celdas) > 1 else ""
            partes = [f"{evento} (SIGA — Archivo Histórico Diocesano de "
                      f"Vitoria, registros 1481-1900).",
                      f"Fecha: {fecha}.", f"Persona: {persona}."]
            if i_sexo is not None and len(celdas) > i_sexo and celdas[i_sexo]:
                partes.append(f"Sexo: {celdas[i_sexo]}.")
            if i_fondo is not None and len(celdas) > i_fondo and celdas[i_fondo]:
                partes.append(f"Parroquia/fondo: {celdas[i_fondo]}.")
            if i_loc is not None and len(celdas) > i_loc and celdas[i_loc]:
                partes.append(f"Localidad: {celdas[i_loc]}.")
            if i_mun is not None and len(celdas) > i_mun and celdas[i_mun]:
                partes.append(f"Municipio: {celdas[i_mun]}.")
            partes.append(f"ID de registro: {id_reg}.")
            docs.append({
                "origen": "siga",
                "url": _siga_url_query(datos),
                "titulo": f"SIGA: {evento.lower()} — {persona.split(',')[0]}",
                "texto": " ".join(partes),
            })
        break  # solo la primera tabla de resultados
    return docs


def siga_buscar(apellido1: str, sacramento: str = "", id_localidad: int = 55,
                apellido2: str = "", esposa_apellido1: str = "", nombre: str = "",
                max_paginas: int = PAGINAS_CONECTOR) -> list[dict]:
    """Una búsqueda completa en SIGA (POST + paginación GET).

    OJO (v3.1): el índice guarda los apellidos POR SEPARADO; la cadena
    compuesta completa ("Saenz de Navarrete") devuelve 0 filas. Usar
    _formas_siga() para partir compuestos. id_localidad='' busca en TODA
    la provincia (imprescindible: p. ej. los Dopico están en Navaridas,
    no en Vitoria). En caso de error de red se LANZA la excepción para
    que el llamador no cachee la consulta como hecha (se reintentará).

    v4.3: la paginación DEDUPLICA filas (la página N puede repetir el
    contenido de la 1 si el parámetro de página no aplica a esa búsqueda)
    y CORTA en cuanto una página no aporta filas nuevas: antes se
    devolvían los mismos registros repetidos que ocupaban hueco en el
    corpus y disparaban hallazgos duplicados en fase 2.
    """
    datos = _siga_datos(apellido1=apellido1, sacramento=sacramento,
                        id_localidad=id_localidad, apellido2=apellido2,
                        esposa_apellido1=esposa_apellido1, nombre=nombre)
    docs: list[dict] = []
    vistos: set[str] = set()   # v4.3: dedup de filas entre páginas

    def _nuevas(filas: list[dict]) -> list[dict]:
        out = []
        for d in filas:
            k = d.get("texto", "")
            if k in vistos:
                continue
            vistos.add(k)
            out.append(d)
        return out

    try:
        ambito = f"loc. {id_localidad}" if id_localidad else "toda Álava"
        with ui.Indicador(f"SIGA: {sacramento or 'todos'} '{apellido1}' "
                          f"({ambito})", nivel="search"):
            r = SESSION.post(SIGA_URL, data=datos, timeout=30, verify=False)
        r.raise_for_status()
        docs += _nuevas(_siga_parse(r.text, datos, sacramento))
        for pagina in range(2, max_paginas + 1):
            if len(docs) >= 90:
                break  # suficiente material para una fase 2
            with ui.Indicador(f"SIGA: página {pagina}", nivel="info"):
                r = SESSION.get(SIGA_URL, params={**datos, "page": pagina},
                                timeout=30, verify=False)
            r.raise_for_status()
            nuevas = _nuevas(_siga_parse(r.text, datos, sacramento))
            if not nuevas:
                break   # v4.3: página sin filas nuevas: fin de la paginación
            docs += nuevas
            time.sleep(random.uniform(*DELAY_DESCARGAS))
    except PresupuestoExcedido:
        raise
    except Exception as e:
        ui.log_warn(f"SIGA no respondió ({apellido1!r}): {str(e)[:100]}")
        raise  # que el llamador NO marque la consulta como hecha
    return docs


def _agrupar_docs_siga(docs: list[dict], etiqueta: str) -> list[dict]:
    """Agrupa filas sueltas de SIGA en documentos de SIGA_FILAS_POR_DOC.
    Cada fila es un hecho; agrupadas mantienen la fase 2 barata y rápida
    (1 llamada por cada ~5 documentos en vez de 1 por fila) y el tamaño
    corto evita el efecto 'lost in the middle'."""
    out = []
    for i in range(0, len(docs), SIGA_FILAS_POR_DOC):
        grupo = docs[i:i + SIGA_FILAS_POR_DOC]
        if not grupo:
            continue
        out.append({
            "origen": "siga",
            "url": grupo[0]["url"],
            "titulo": f"SIGA — {etiqueta} (filas {i + 1}-{i + len(grupo)})",
            "texto": ("Índice de registros sacramentales del Archivo Histórico "
                      "Diocesano de Vitoria (SIGA, 1481-1900). Búsqueda: "
                      f"{etiqueta}. Cada línea es un registro independiente:\n"
                      + "\n".join(f"- {d['texto']}" for d in grupo)),
        })
    return out


# Re-exporta los helpers de caché del conector desde config (definidos en
# config para evitar dependencias circulares con scrapers).
# (Ya están importados arriba; esto es para claridad del lector.)

def recolector_siga(objetivo: dict, conn=None) -> list[dict]:
    """Registros de SIGA para el objetivo (solo Álava, 1481-1900).

    Estrategia (v3.1+v4.0):
    1. Parte apellidos compuestos en tokens (índice los guarda por separado).
    2. Fallback PROVINCIAL (id_localidad=''): si el token aparece poco en
       el municipio, se busca en toda Álava (caso real: Dopico en
       Navaridas, no Vitoria).
    3. Búsqueda de precisión con nombre de pila + primer apellido,
       provincial (localiza a la persona o tocayos).
    4. Matrimonios por pareja usando tokens.
    """
    provs = {normalizar(p) for p in (objetivo.get("provincias", [])
                                     or [objetivo.get("provincia", "")])}
    if not provs & {"alava", "araba"}:
        return []
    id_loc = _siga_localidad(objetivo.get("municipio", ""))
    if id_loc is None:
        # v10.0 — SIN fallback silencioso a Vitoria: un municipio fuera del
        # mapa NO se busca en la localidad equivocada. Consulta OMITIDA con
        # aviso explícito y NO cacheada como hecha (si el municipio es
        # correcto y alavés, hay que añadirlo a SIGA_LOCALIDADES).
        ui.log_warn(
            f"SIGA: el municipio '{objetivo.get('municipio', '')}' NO está "
            f"en el mapa de localidades de SIGA (solo municipios de Álava)."
            f" Consulta OMITIDA y NO cacheada: no se busca en Vitoria por "
            f"defecto. Si el municipio es correcto y alavés, añádelo a "
            f"SIGA_LOCALIDADES en scrapers/archivos.py (--probar-"
            f"conectores ayuda a actualizar el mapa).")
        return []
    docs: list[dict] = []
    consultas = 0

    def lanzar(ap, sacramento, idloc, clave, **extra):
        nonlocal consultas
        if consultas >= CONECTOR_MAX_CONSULTAS:
            return []
        if _conector_en_cooldown(conn, clave):
            return []   # v4.3: SIGA falló hace poco; reintento tras TTL
        if _consulta_conector_hecha(conn, clave):
            return []
        consultas += 1
        try:
            filas = siga_buscar(ap, sacramento=sacramento, id_localidad=idloc,
                                 **extra)
        except PresupuestoExcedido:
            raise
        except Exception:
            # v4.3: fallo transitorio -> cooldown (antes se reintentaba en
            # CADA objetivo de la misma ejecución, con su timeout de 30 s
            # cada vez).
            _marcar_conector_fallo(conn, clave)
            return []  # ya avisó siga_buscar
        _marcar_conector(conn, clave)
        time.sleep(random.uniform(*DELAY_DESCARGAS))
        nuevas = [f for f in filas if f["texto"] not in filas_vistas]
        filas_vistas.update(f["texto"] for f in nuevas)
        ambito = "toda Álava" if idloc == "" else f"loc. {idloc}"
        etiqueta = f"{sacramento} '{ap}' ({ambito})"
        if extra.get("nombre"):
            etiqueta += f" nombre={extra['nombre']}"
        if extra.get("esposa_apellido1"):
            etiqueta += f" x esposa '{extra['esposa_apellido1']}'"
        return _agrupar_docs_siga(nuevas, etiqueta)

    ap_p = (objetivo.get("apellido_paterno") or "").strip()
    ap_m = (objetivo.get("apellido_materno") or "").strip()
    nombre_pila = ((objetivo.get("nombre") or "").split() or [""])[0]
    filas_vistas: set = set()  # dedup de filas repetidas entre consultas

    for ap in [a for a in (ap_p, ap_m) if a]:
        # 1) formas del apellido (tokens sueltos primero) en el municipio
        for forma in _formas_siga(ap):
            docs += lanzar(forma, "bautismo", id_loc,
                           f"siga::bautismo::{forma}::{id_loc}")
        # 2) fallback provincial para tokens con poca presencia local
        for tok in _tokens_apellido(ap):
            tok_sin = sin_tildes(tok)
            presentes = sum(1 for d in docs if tok_sin in normalizar(d["texto"]))
            if presentes < 10:
                docs += lanzar(tok_sin, "bautismo", "",
                               f"siga::bautismo::{tok_sin}::provincia")
                docs += lanzar(tok_sin, "matrimonio", "",
                               f"siga::matrimonio::{tok_sin}::provincia")

    # 3) nombre de pila + primer apellido paterno, provincial (precisión)
    tokens_p = _tokens_apellido(ap_p)
    if nombre_pila and tokens_p:
        docs += lanzar(sin_tildes(tokens_p[0]), "bautismo", "",
                       f"siga::bautismo::{sin_tildes(tokens_p[0])}"
                       f"::nombre::{nombre_pila}",
                       nombre=sin_tildes(nombre_pila))

    # 4) matrimonios por pareja: tokens del cónyuge
    conyuge = (objetivo.get("conyuge") or "").strip()
    if tokens_p and conyuge:
        palabras = [p for p in conyuge.split()
                    if normalizar(p) not in
                    {"de", "del", "la", "las", "el", "los", "y", "e",
                     "da", "do", "di", "du", "d", "von", "van"}
                    and len(p) > 2]
        esposas = list(dict.fromkeys(
            sin_tildes(p) for p in (palabras[1:2] + palabras[-1:]) if p))
        for esposa in esposas:
            docs += lanzar(sin_tildes(tokens_p[0]), "matrimonio", id_loc,
                           f"siga::matrimonio::{sin_tildes(tokens_p[0])}"
                           f"::{esposa}::{id_loc}",
                           esposa_apellido1=esposa)
    if docs:
        ui.log_ok(f"SIGA: {len(docs)} documentos de Álava "
                  f"({consultas} consultas)")
    return docs


# ============================== ADDO (Palencia) ============================

def recolector_addo(objetivo: dict, conn=None) -> list[dict]:
    """ADDO (Archivo Diocesano de Palencia): buscador nominal. Sitio volátil:
    se prueban las rutas típicas de WordPress y si fallan se avisa (ajusta
    ADDO_BUSQUEDA en el .env con la URL que veas en F12 -> Network).

    v4.3: un fallo de red (sitio caído, timeout) ya NO se marca como
    'consulta hecha': queda en COOLDOWN (fail::) y se reintenta tras el
    TTL. Antes se marcaba ANTES de la petición y un ADDO caído quedaba
    'investigado' para siempre en la caché."""
    provs = {normalizar(p) for p in (objetivo.get("provincias", [])
                                     or [objetivo.get("provincia", "")])}
    if not provs & {"palencia"}:
        return []
    ap = sin_tildes(objetivo.get("apellido_paterno", "") or "")
    if not ap:
        return []
    urls = ([ADDO_BUSQUEDA] if ADDO_BUSQUEDA
            else [f"{ADDO_URL}/?s=", f"{ADDO_URL}/busqueda?query="])
    docs = []
    for base in urls:
        clave = f"addo::{ap}::{base}"
        if _conector_en_cooldown(conn, clave):
            continue
        if _consulta_conector_hecha(conn, clave):
            continue
        try:
            with ui.Indicador(f"ADDO: '{ap}'", nivel="search"):
                r = SESSION.get(base + ap, timeout=30, verify=False)
            r.raise_for_status()
        except PresupuestoExcedido:
            raise
        except Exception as e:
            # v4.3: fallo transitorio -> cooldown (reintento tras TTL).
            _marcar_conector_fallo(conn, clave)
            ui.log_warn(f"ADDO ({base}{ap}) falló: {str(e)[:80]} "
                        f"(queda en cooldown; reintento tras el TTL)")
            time.sleep(random.uniform(*DELAY_DESCARGAS))
            continue
        _marcar_conector(conn, clave)   # v4.3: solo tras respuesta real
        try:
            texto_total = BeautifulSoup(r.text, "html.parser").get_text()
            lineas = [l.strip() for l in texto_total.splitlines()]
            utiles = [l for l in lineas
                      if ap.lower() in l.lower() and 30 < len(l) < 300][:40]
            if utiles:
                docs.append({
                    "origen": "addo", "url": r.url,
                    "titulo": f"ADDO Palencia: {ap}",
                    "texto": ("Resultados del buscador ADDO (Archivo "
                              "Diocesano de Palencia) para "
                              f"'{ap}':\n" + "\n".join(f"- {l}" for l in utiles)),
                })
            break  # la primera URL que responda basta
        except Exception as e:
            ui.log_warn(f"ADDO parse falló: {str(e)[:80]}")
    if docs:
        ui.log_ok(f"ADDO: {len(docs)} documentos de Palencia")
    return docs


# ============================== PARES — Catastro de Ensenada ===============

def _pares_sesion() -> None:
    """La GET inicial fija la cookie de sesión que exige el buscador PARES.
    v4.3: si PARES no responde se lanza ParesNoDisponible (antes devolvía
    False y la búsqueda devolvía [] => NOTA NEGATIVA falsa en el corpus)."""
    try:
        r = SESSION.get(PARES_CATASTRO,
                        params={"ini": 0, "accion": 0, "mapas": 0, "tipo": 0},
                        timeout=30, verify=False)
        r.raise_for_status()
    except Exception as e:
        raise ParesNoDisponible(
            f"PARES no responde al abrir sesión: {str(e)[:80]}") from e


def buscar_localidades_ensenada(nombre: str) -> list[dict]:
    """Localidades del Catastro de Ensenada cuyo nombre EMPIEZA por `nombre`.
    Devuelve dicts con actual/antigua/entidad/provincia/loc_id.

    v4.3: si PARES falla (conexión o error de BD del servidor) lanza
    ParesNoDisponible en vez de devolver [] — la diferencia importa:
    [] significa 'no está en el catastro' (dato genealógico) y el fallo
    significa 'no lo sabemos aún' (reintentar más tarde)."""
    _pares_sesion()   # lanza ParesNoDisponible si el portal está caído
    datos = {"mapas": "0", "accion": "1", "volver": "0",
             "txtBusqueda": nombre, "tipo": "0",
             "tipolocal": "-1", "provact": "0", "Buscar": "Buscar"}
    try:
        with ui.Indicador(f"PARES: '{nombre}'", nivel="search"):
            r = SESSION.post(PARES_CATASTRO, data=datos, timeout=30,
                             verify=False)
        r.raise_for_status()
    except ParesNoDisponible:
        raise
    except Exception as e:
        raise ParesNoDisponible(
            f"PARES falló al buscar '{nombre}': {str(e)[:80]}") from e
    # v4.3: página de error de BD del servidor (verificada en vivo:
    # 'Problema encontrado — Ha ocurrido un error al conectar con la
    # Base de Datos'). NO son 0 resultados: es una caída transitoria.
    if _pares_error_servidor(r.text):
        raise ParesNoDisponible(
            "PARES: error del servidor al conectar con su Base de Datos "
            "(caída temporal del portal; NO son 0 resultados)")
    s = BeautifulSoup(r.text, "html.parser")
    out: list[dict] = []
    for tabla in s.find_all("table"):
        filas = tabla.find_all("tr")
        if len(filas) < 2:
            continue
        cabecera = " ".join(c.get_text(strip=True).lower()
                            for c in filas[0].find_all(["th", "td"]))
        if "localidad actual" not in cabecera:
            continue
        for tr in filas[1:]:
            celdas = [c.get_text(strip=True) for c in tr.find_all("td")]
            if len(celdas) < 4 or not celdas[0]:
                continue
            a = tr.find("a")
            loc_id = ""
            if a and a.get("href"):
                import re
                m = re.search(r"loc=(\d+)", a["href"])
                loc_id = m.group(1) if m else ""
            out.append({
                "actual": celdas[0],
                "antigua": celdas[1] if len(celdas) > 1 else "",
                "entidad": celdas[2] if len(celdas) > 2 else "",
                "provincia": celdas[3] if len(celdas) > 3 else "",
                "loc_id": loc_id,
            })
        break
    return out


def _formas_ensenada(mun: str, alias) -> list[str]:
    """Formas de buscar un municipio en el catastro: nombre completo, alias
    y sus primeras palabras (los índices antiguos a veces registran solo la
    cabecera: 'Roscales de la Peña' está indexada como 'Roscales')."""
    formas = []
    for nombre in filter(None, (mun, alias)):
        if nombre not in formas:
            formas.append(nombre)
        primera = nombre.split()[0] if nombre.split() else ""
        if primera and primera not in formas:
            formas.append(primera)
    return formas


def _filtrar_por_provincia(locs: list[dict], prov: str,
                            forma: str, mun: str) -> list[dict]:
    """Al buscar por prefijo llegan pueblos homónimos de otras provincias:
    nos quedamos con los de la provincia objetivo."""
    if not prov:
        return locs
    prov_n = normalizar(prov)
    return [l for l in locs
            if prov_n in normalizar(l.get("provincia", ""))]


def recolector_ensenada(objetivo: dict, conn=None) -> list[dict]:
    """1-2 consultas de localidad por municipio (cacheadas): la referencia
    del pueblo en el Catastro de 1752 entra como documento del corpus. Si
    el pueblo NO está en el catastro, se deja constancia de la NOTA
    NEGATIVA (para no repetir la consulta ni perder el tiempo).

    v4.3: si PARES está caído (error de BD del servidor), la consulta queda
    en COOLDOWN (fail::, reintento automático tras el TTL) y JAMÁS se
    escribe una NOTA NEGATIVA: 'el servidor ha fallado' no es 'el pueblo
    no está en el catastro'. Antes, una caída de PARES envenenaba el
    corpus con notas negativas FALSAS que desaconsejaban la fuente para
    siempre."""
    mun = objetivo.get("municipio", "")
    if not mun:
        return []
    prov = objetivo.get("provincia", "") or ""
    provs = {normalizar(p) for p in (objetivo.get("provincias", []) or [prov])}
    if provs and provs <= PROVINCIAS_SIN_ENSENADA:
        return []
    clave_completa = f"ensenada::{normalizar(mun)}::{normalizar(prov)}"
    if _consulta_conector_hecha(conn, clave_completa):
        return []
    alias = MUNICIPIOS_EQUIVALENTES.get(normalizar(mun))
    docs = []
    respondidas = 0   # v4.3: búsquedas a las que PARES respondió DE VERDAD
    for forma in _formas_ensenada(mun, alias):
        clave = f"ensenada::{normalizar(forma)}::{normalizar(prov)}"
        if _conector_en_cooldown(conn, clave):
            continue
        if _consulta_conector_hecha(conn, clave):
            continue
        try:
            locs = _filtrar_por_provincia(
                buscar_localidades_ensenada(forma), prov, forma, mun)
        except ParesNoDisponible as e:
            # v4.3: fallo TRANSITORIO -> cooldown, ni caché ni nota negativa.
            _marcar_conector_fallo(conn, clave)
            ui.log_warn(f"PARES no disponible ({str(e)[:90]}): '{mun}' queda "
                        f"pendiente; se reintentará tras el cooldown")
            continue
        # v4.3: solo se marca 'hecha' cuando PARES respondió de verdad.
        _marcar_conector(conn, clave)
        respondidas += 1
        time.sleep(random.uniform(*DELAY_DESCARGAS))
        if not locs:
            continue
        lineas = []
        for l in locs:
            visor = (f" Visor de localidades: {PARES_CATASTRO}"
                     f"?accion=4&opcionV=3&loc={l['loc_id']}"
                     if l["loc_id"] else "")
            lineas.append(f"- {l['actual']} (antigua: {l['antigua'] or '?'}), "
                          f"entidad de {l['entidad'] or '?'}, provincia "
                          f"{l['provincia'] or '?'}.{visor}")
        docs.append({
            "origen": "pares", "url": PARES_CATASTRO,
            "titulo": f"Catastro de Ensenada: '{mun}'",
            "texto": ("Resultado del buscador de localidades del Catastro de "
                      f"Ensenada (PARES) para '{mun}':\n"
                      + "\n".join(lineas)),
        })
        break
    else:
        # v4.3: la NOTA NEGATIVA solo se escribe si PARES RESPONDIÓ de
        # verdad y dijo 'no hay localidades'. Si solo hubo fallos/cooldown,
        # no hay dato genealógico, solo una caída pendiente de reintentar.
        if respondidas:
            docs.append({
                "origen": "pares", "url": PARES_CATASTRO,
                "titulo": f"Catastro de Ensenada: '{mun}' (no encontrado)",
                "texto": (f"NOTA NEGATIVA: '{mun}' no aparece en el índice de "
                          "localidades del Catastro de Ensenada (1749-1756) "
                          "del portal PARES. El pueblo no fue interrogado o "
                          "sus respuestas no se conservan; no merece la pena "
                          "insistir en esta fuente."),
            })
    return docs


# =============== v10.3 — ERRORES DE PROGRAMACIÓN EN LOS CONECTORES ========
# El FIX 1 de la v10.2 arregló la INSTANCIA del fallo más caro del log
# (SIGA/ADDO/Ensenada invocados sin argumentos), pero no la CLASE: si
# cualquier conector revienta por un fallo de FIRMA, un import roto o un
# atributo mal escrito, la excepción se vuelve a tragar con un log_warn por
# objetivo y la tanda entera se va sin esa fuente (45 avisos amarillos
# idénticos en el log del 2026-09-11 y nadie se enteró).
# Estos tipos son BUGS NUESTROS (deterministas), no fallos de red: se
# avisan UNA vez por ejecución con nivel ERROR y se cuentan.
ERRORES_PROGRAMACION: tuple[type[BaseException], ...] = (
    TypeError,           # firma equivocada: el caso REAL de la v10.2
    NameError,           # nombre no definido
    UnboundLocalError,   # local usada antes de asignar
    AttributeError,      # atributo inexistente (p. ej. None.text)
    ImportError,         # import roto (ImportError ya cubre ModuleNotFound)
)

# conector -> nº de veces que ha abortado por error de programación
_ABORTOS_PROGRAMACION: dict[str, int] = {}


def reiniciar_contador_abortos() -> None:
    """Vacía el contador de abortos por error de programación (lo usan los
    tests para no arrastrar estado; y cualquier proceso de larga vida que
    quiera volver a ver el aviso)."""
    _ABORTOS_PROGRAMACION.clear()


# ============================== ORQUESTADOR ================================

def recolectar(objetivo: dict, conn=None) -> list[dict]:
    """Ejecuta los recolectores aplicables al objetivo. Nunca rompe la
    ejecución: si un archivo falla, se registra y se sigue con los demás.

    v9.1: se añaden HISPAGEN (transcripciones colaborativas, texto ya
    transcrito) y FamilySearch (catálogo por localidad: listado de libros
    ANTES de leer; su salida va al informe, no al corpus).

    v10.2 (bug crítico del log de ejecución): SIGA/ADDO/Ensenada se
    llamaban como ``conector()`` SIN argumentos (los tres comparten
    firma ``(objetivo, conn=None)``), así que reventaban con
    ``missing 1 required positional argument: 'objetivo'`` en CADA
    objetivo de CADA ciclo: los tres conectores de datos estructurados
    llevaban toda la ejecución muertos y sus fuentes (sacramentales de
    Álava, ADDO, Catastro de Ensenada) sin consultar. Ahora van
    envueltos en closures exactamente igual que HISPAGEN y
    FamilySearch, que ya capturaban ``objetivo`` y ``conn``.

    v10.3: el FIX 1 arregló la INSTANCIA, no la CLASE. Un fallo de
    programación en un conector (firma, import, atributo) ya no se
    confunde con un fallo de red: sale como log_error UNA vez por
    ejecución y ese conector se aborta sin impedir a los demás."""
    def _recolector_familysearch():
        from scrapers.familysearch import recolector_familysearch
        return recolector_familysearch(objetivo, conn)

    def _recolector_hispagen():
        from scrapers.hispagen import recolector_hispagen
        return recolector_hispagen(objetivo, conn)

    def _recolector_siga():
        return recolector_siga(objetivo, conn)

    def _recolector_addo():
        return recolector_addo(objetivo, conn)

    def _recolector_ensenada():
        return recolector_ensenada(objetivo, conn)

    docs: list[dict] = []
    for conector in (_recolector_siga, _recolector_addo,
                     _recolector_ensenada,
                     _recolector_hispagen, _recolector_familysearch):
        try:
            docs += conector()
        except PresupuestoExcedido:
            raise
        except ERRORES_PROGRAMACION as e:
            # v10.3: BUG NUESTRO, no fallo de red. Se aborta ESTA llamada
            # (los demás conectores siguen) y se avisa UNA sola vez con
            # nivel error; los objetivos siguientes lo reintentan en
            # silencio y solo suman el contador.
            nombre = conector.__name__
            _ABORTOS_PROGRAMACION[nombre] = (
                _ABORTOS_PROGRAMACION.get(nombre, 0) + 1)
            if _ABORTOS_PROGRAMACION[nombre] == 1:
                ui.log_error(
                    f"BUG en {nombre}(): {type(e).__name__}: "
                    f"{str(e)[:200]} — NO es un fallo de red: es un error "
                    f"de programación del conector. Se ABORTA ese conector "
                    f"(los demás siguen); arréglalo antes de la siguiente "
                    f"ejecución.")
        except Exception as e:
            ui.log_warn(f"recolector {conector.__name__} falló: "
                        f"{str(e)[:100]}")
    return docs


def probar_conectores() -> int:
    """--probar-conectores: comprueba en vivo que SIGA/ADDO/PARES responden
    y que el parseo detecta resultados. Devuelve el nº de problemas."""
    problemas = 0
    ui.cabecera("1. SIGA (Álava, sacramentales 1481-1900)")
    try:
        r = SESSION.get(SIGA_URL, timeout=30, verify=False)
        r.raise_for_status()
        ui.log_ok(f"formulario accesible ({len(r.text)} bytes)")
        docs = siga_buscar("S%enz", sacramento="bautismo", id_localidad=55)
        if docs:
            ui.log_ok(f"búsqueda 'S%enz' en bautismos de Vitoria: "
                      f"{len(docs)} filas (p. ej.: {docs[0]['texto'][:80]}...)")
        else:
            problemas += 1
            ui.log_error("la búsqueda devolvió 0 filas (¿cambió el formulario?)")
    except Exception as e:
        problemas += 1
        ui.log_error(f"SIGA: {str(e)[:100]}")

    ui.cabecera("2. ADDO (Archivo Diocesano de Palencia)")
    try:
        r = SESSION.get(ADDO_URL, timeout=30, verify=False)
        ui.log_ok(f"{ADDO_URL} responde (HTTP {r.status_code})")
        ui.log("        (si el buscador nominal usa otra URL, ponla en "
               "ADDO_BUSQUEDA del .env)")
    except Exception as e:
        ui.log_warn(f"ADDO no responde desde aquí: {str(e)[:80]} "
                    "(puede ser corte temporal o bloqueo geográfico)")

    ui.cabecera("3. PARES (Catastro de Ensenada)")
    try:
        locs = buscar_localidades_ensenada("Zamora")
        if locs:
            ui.log_ok(f"búsqueda 'Zamora': {len(locs)} localidades "
                      f"(p. ej.: {locs[0]['actual']}, loc={locs[0]['loc_id']})")
        else:
            problemas += 1
            ui.log_error("PARES no devolvió localidades (¿sesión?)")
    except ParesNoDisponible as e:
        problemas += 1
        ui.log_error(f"PARES CAÍDO: {str(e)[:110]}. No es un error tuyo: "
                     f"el portal tiene su base de datos inactiva ahora "
                     f"mismo. El agente lo tratará como fallo transitorio "
                     f"(cooldown) y NO escribirá notas negativas falsas; "
                     f"reinténtalo más tarde.")

    # v9.1: conectores nuevos (HISPAGEN + FamilySearch)
    problemas += _probar_hispagen()
    problemas += _probar_familysearch()

    ui.log_stats(f"=== Conectores: "
                 f"{'TODO CORRECTO' if problemas == 0 else f'{problemas} problema(s)'} "
                 f"===")
    return problemas


# ====================== v9.1 — CONECTORES NUEVOS ==========================

def _probar_hispagen() -> int:
    """4. HISPAGEN (transcripciones colaborativas): sitio Joomla con
    certificado mal emitido en www y búsqueda GET searchword."""
    problemas = 0
    ui.cabecera("4. HISPAGEN (transcripciones colaborativas)")
    try:
        from scrapers.hispagen import _url_busqueda, _parsear_resultados
        from config import HISPAGEN_URL
        r = SESSION.get(HISPAGEN_URL, timeout=30, verify=False)
        ui.log_ok(f"{HISPAGEN_URL} responde (HTTP {r.status_code})")
        url = _url_busqueda("garcia")
        r2 = SESSION.get(url, timeout=30, verify=False)
        filas = _parsear_resultados(r2.text, str(r2.url))
        ui.log_ok(f"búsqueda 'garcia': {len(filas)} resultados parseables")
    except Exception as e:
        problemas += 1
        ui.log_warn(f"HISPAGEN no responde desde aquí: {str(e)[:80]} "
                    "(el sandbox no llega; desde tu máquina debería: "
                    "revisa el certificado con verify=False)")
    return problemas


def _probar_familysearch() -> int:
    """5. FamilySearch: catálogo por localidad. VERIFICADO EN VIVO: el
    catálogo exige login ('Sign-in to your account'). Sin credenciales
    el conector registra 'sin_credenciales' (no falla en silencio)."""
    problemas = 0
    ui.cabecera("5. FamilySearch (catálogo por localidad)")
    try:
        from scrapers.familysearch import SesionFamilySearch, catalogo_localidad
        ses = SesionFamilySearch()
        estado = ses.iniciar()
        if estado == "login_ok":
            res = catalogo_localidad("Vitoria", ses)
            ui.log_ok(f"catálogo Vitoria: {res['estado']} — "
                      f"{len(res['libros'])} libros ({res['detalle'][:60]})")
        else:
            ui.log_warn(f"FamilySearch: {estado} — {ses.detalle[:100]}")
            ui.log("        (pon FAMILYSEARCH_USER/PASS o FAMILYSEARCH_COOKIE "
                   "en el .env para activar el listado de libros)")
    except Exception as e:
        problemas += 1
        ui.log_warn(f"FamilySearch inalcanzable: {str(e)[:80]}")
    return problemas
