"""
scrapers/hispagen.py — PARTE D (v9.1): conector de HISPAGEN
(www.hispagen.es, Asociación de Genealogía Hispana).

QUÉ ES HISPAGEN: transcripciones COLABORATIVAS manuales de registros
parroquiales y civiles, públicas y gratuitas, hechas por socios — texto
ya transcrito, SIN OCR. Complemento ideal de la cascada: lo que allí hay
entra como texto limpio directo al corpus.

QUÉ ESTÁ VERIFICADO (sesión anterior, en vivo contra el sitio real):
  - Sitio Joomla vivo; archivo documental público en
    /index.php/archivo-documental.
  - La búsqueda es el com_search estándar de Joomla: GET
    /index.php/component/search/?searchword=<q>&searchphrase=all
    &ordering=newest (200 OK con curl).
  - Fondos mayormente de Andalucía/Extremadura/Murcia: para los
    municipios de esta familia (Vitoria, Castrejón de la Peña,
    Pobladura del Valle, Coreses) el resultado más probable es CERO
    filas; el conector lo deja registrado y sigue (un 'sin resultados'
    documentado NO es un error).

QUÉ ESTÁ VERIFICADO HOY (2026-09-11, este sandbox): la red del sandbox
NO llega a hispagen.es (y el lector remoto rechaza el www por certificado
mal emitido: ERR_CERT_COMMON_NAME_INVALID). Por eso el conector intenta
www Y el dominio apex, con verify=False (mismo criterio que SIGA/ADDO),
y ante fallo de red hace COOLDOWN (no 'consulta hecha': reintento tras
TTL). Los tests de este módulo son offline con HTML grabado.

PATRÓN: mismo esqueleto que el conector SIGA de scrapers/archivos.py
(formas del apellido con tokens, caché de consultas, cooldown, delay,
deduplicación de filas y agrupación en documentos).

CUÁNDO BUSCA: por apellido + municipio (la transcripción puede citar
el lugar en el texto del resultado); la búsqueda es por apellido porque
el com_search de Joomla busca en todo el texto del artículo.
"""

from __future__ import annotations

import random
import time
from urllib.parse import quote_plus, urljoin

import requests
from bs4 import BeautifulSoup

from config import (CONECTOR_MAX_CONSULTAS, DELAY_DESCARGAS, HISPAGEN_BUSQUEDA,
                    HISPAGEN_URL, PAGINAS_CONECTOR, SESSION,
                    _conector_en_cooldown, _consulta_conector_hecha,
                    _marcar_conector, _marcar_conector_fallo,
                    normalizar, sin_tildes, _tokens_apellido)
from utils import ui

# Marcas de "sin resultados" del com_search de Joomla. OJO: lista de
# cadenas SEPARADAS (una cadena concatenada haría que any() iterara
# CARACTERES y cualquier letra del HTML casaría -> siempre 'vacío').
SIN_RESULTADOS = ["no se ha encontrado ningún resultado",
                  "no results were found",
                  "0 resultados"]


def _url_busqueda(query: str) -> str:
    """URL GET del com_search con la query compuesta (apellido municipio)."""
    return HISPAGEN_BUSQUEDA.format(q=quote_plus(query))


def _get_hispagen(url: str):
    """GET a HISPAGEN con reintento de conexión limpia (v10.2).

    En el log de ejecución real TODAS las consultas a hispagen.es
    fallaban con ``('Connection aborted.', RemoteDisconnected('Remote
    end closed connection without response'))``: el servidor cierra
    las conexiones keep-alive que reusa la SESSION compartida, y la
    petición siguiente viaja por una conexión ya muerta. urllib3 solo
    reintenta por defecto cuando la petición es idempotente Y el
    adaptador tiene max_retries>0 (la SESSION del proyecto va con 0).

    Solución: si el GET falla con ConnectionError (que envuelve el
    RemoteDisconnected), se cierra el pool de conexiones de la SESSION
    (fuerza sockets NUEVOS en la siguiente petición) y se reintenta UNA
    vez tras una pausa corta. Si vuelve a fallar, la excepción sube al
    llamador como siempre (cooldown del conector)."""
    try:
        return SESSION.get(url, timeout=30, verify=False)
    except requests.exceptions.ConnectionError:
        time.sleep(2.0)
        try:
            SESSION.close()   # descarta los sockets keep-alive envenenados
        except Exception:
            pass
        return SESSION.get(url, timeout=30, verify=False)


def hispagen_buscar(query: str, max_paginas: int = PAGINAS_CONECTOR) -> list[dict]:
    """Una búsqueda completa en HISPAGEN (GET + paginación de Joomla).

    Devuelve filas {titulo, texto, url}. Lanza la excepción de red al
    llamador (convención SIGA: quien llama no cachea la consulta como
    hecha). El sitio se prueba en www y apex con verify=False porque el
    certificado de www.hispagen.es está mal emitido para ese subdominio
    (verificado en vivo: ERR_CERT_COMMON_NAME_INVALID).

    v10.2: cada GET pasa por _get_hispagen (reintento con conexión
    limpia ante RemoteDisconnected del keep-alive del servidor)."""
    bases = [HISPAGEN_BUSQUEDA.format(q=quote_plus(query))]
    paginas_restantes = max(0, max_paginas - 1)
    filas: list[dict] = []
    url_siguiente = bases[0]
    vistas: set[str] = set()
    while url_siguiente:
        r = _get_hispagen(url_siguiente)
        r.raise_for_status()
        nuevas = _parsear_resultados(r.text, str(r.url))
        for f in nuevas:
            if f["titulo"] not in vistas:
                vistas.add(f["titulo"])
                filas.append(f)
        # paginación Joomla: enlaces start=N del mismo com_search
        sopa = BeautifulSoup(r.text, "html.parser")
        siguiente = None
        for a in sopa.find_all("a", href=True):
            if ("start=" in a["href"] and "search" in a["href"]
                    and a["href"].startswith("/")):
                siguiente = urljoin(HISPAGEN_URL, a["href"])
                break
        if not nuevas or paginas_restantes <= 0:
            break
        paginas_restantes -= 1
        url_siguiente = siguiente
    return filas


def _parsear_resultados(html: str, url_final: str) -> list[dict]:
    """Convierte la página de resultados de com_search en filas.

    El resultado de Joomla trae: título enlazado + snippet con el texto
    de la transcripción (donde suelen estar fechas y lugares). Se
    filtran los resultados vacíos y las URLs relativas se absolutizan."""
    texto_total = (html or "")
    if any(marca in texto_total.lower() for marca in SIN_RESULTADOS):
        return []
    sopa = BeautifulSoup(texto_total, "html.parser")
    filas = []
    vistos: set[str] = set()
    for res in sopa.select(".result-item, .search-result, "
                           "div[class*=result]"):
        a = res.find("a")
        if not a:
            continue
        titulo = (a.get_text(" ", strip=True) or "").strip()
        if not titulo or len(titulo) < 4:
            continue
        snippet = res.get_text(" ", strip=True)
        snippet = snippet.replace(titulo, "", 1).strip()
        href = a.get("href", "")
        if not href:
            continue
        if href.startswith("/"):
            href = urljoin(HISPAGEN_URL, href)
        # el selector doble (.result-item + div[class*=result]) puede
        # devolver el MISMO bloque dos veces: dedup por URL
        if href in vistos:
            continue
        vistos.add(href)
        filas.append({"titulo": titulo, "texto": snippet, "url": href})
    return filas


def _agrupar_filas(filas: list[dict], etiqueta: str) -> list[dict]:
    """Agrupa filas sueltas en documentos (como _agrupar_docs_siga):
    mantiene la fase 2 barata y evita el 'lost in the middle'."""
    out = []
    TAM = 5
    for i in range(0, len(filas), TAM):
        grupo = filas[i:i + TAM]
        if not grupo:
            continue
        out.append({
            "origen": "hispagen",
            "url": grupo[0]["url"],
            "titulo": f"HISPAGEN — {etiqueta} (filas {i + 1}-{i + len(grupo)})",
            "texto": ("Transcripciones colaborativas de HISPAGEN "
                      "(www.hispagen.es), texto ya transcrito a mano por "
                      "socios (sin OCR). Cada línea es un resultado "
                      "independiente de la búsqueda:\n"
                      + "\n".join(f"- {f['titulo']}: {f['texto']}"
                                  for f in grupo)),
        })
    return out


def recolector_hispagen(objetivo: dict, conn=None) -> list[dict]:
    """Recolector estándar del agente (patrón SIGA): apellidos del
    objetivo + municipio, con tokens para compuestos, caché de consultas
    y cooldown ante fallos de red. HISPAGEN es estatal: se consulta para
    cualquier provincia de la familia."""
    ap_p = (objetivo.get("apellido_paterno") or "").strip()
    ap_m = (objetivo.get("apellido_materno") or "").strip()
    municipio = (objetivo.get("municipio") or "").strip()
    if not (ap_p or ap_m):
        return []
    docs: list[dict] = []
    consultas = 0
    filas_vistas: set[str] = set()

    def lanzar(query: str, clave: str) -> list[dict]:
        nonlocal consultas
        if consultas >= CONECTOR_MAX_CONSULTAS:
            return []
        if _conector_en_cooldown(conn, clave) or _consulta_conector_hecha(conn,
                                                                          clave):
            return []
        consultas += 1
        try:
            filas = hispagen_buscar(query)
        except Exception as e:
            _marcar_conector_fallo(conn, clave)
            ui.log_warn(f"HISPAGEN no respondió ({query!r}): "
                        f"{str(e)[:90]} (cooldown; reintento tras TTL)")
            return []
        _marcar_conector(conn, clave)
        time.sleep(random.uniform(*DELAY_DESCARGAS))
        nuevas = [f for f in filas if f["titulo"] not in filas_vistas]
        filas_vistas.update(f["titulo"] for f in nuevas)
        return nuevas

    for ap in (ap_p, ap_m):
        if not ap:
            continue
        for forma in _formas_busqueda(ap):
            etiqueta = f"'{forma}'" + (f" @ {municipio}" if municipio else "")
            clave = f"hispagen::{forma}::{normalizar(municipio)}"
            filas = lanzar(f"{forma} {municipio}".strip(), clave)
            docs += _agrupar_filas(filas, etiqueta)

    if docs:
        ui.log_ok(f"HISPAGEN: {len(docs)} documentos de transcripciones "
                  f"({consultas} consultas)")
    elif consultas:
        ui.log("HISPAGEN: sin resultados (registrado: 0 transcripciones "
               "para esos apellidos — no es un error)")
    return docs


def _formas_busqueda(apellido: str) -> list[str]:
    """Formas de búsqueda de un apellido en HISPAGEN (en MINÚSCULAS,
    como el resto de conectores): tokens sueltos primero (las
    transcripciones citan los apellidos por separado), el compuesto
    después. Sin tildes (el buscador de Joomla las distingue)."""
    formas: list[str] = []
    for tok in _tokens_apellido(apellido):
        base = sin_tildes(tok).lower()
        if base:
            formas.append(base)
    completo = sin_tildes((apellido or "").strip()).lower()
    if completo and completo not in formas:
        formas.append(completo)
    return list(dict.fromkeys(f for f in formas if f))[:3]
