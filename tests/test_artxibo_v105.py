"""
tests/test_artxibo_v105.py — BLOQUE 2: conector del buscador sacramental de
artxibo.euskadi.eus (Dokuklik/IRARGI, Archivo Histórico Diocesano de Vitoria,
Álava 1481-1900) y arreglo de la fragmentación de apellidos compuestos.

Qué fija este fichero:
  1. El PARSEO de las dos fichas reales guardadas en tests/fixtures (campos,
     fecha ISO, filiación, cita del fondo) y lo que la ficha NO trae
     (parroquia, abuelos y padrinos: eso es cosa de la copia literal).
  2. El PARSEO de la lista de resultados (la respuesta JSON real del portal:
     79 filas del apellido compuesto y los homónimos del token suelto).
  3. El ARREGLO de la auditoría: se busca el apellido COMPLETO primero y solo
     se fragmenta en tokens si devuelve CERO filas, marcando entonces los
     resultados como CONFIANZA BAJA. Medido en vivo (2026-09-19):
     'Saenz de Navarrete' -> 79 filas; 'Saenz' suelto -> 7.009.
  4. La integración en fase 1: el recolector de artxibo (solo Álava/Araba),
     su caché de conector (hecho / cooldown) y que NO filtra por el municipio
     del objetivo (el árbol daba 'Vitoria' por hecho y la partida está en
     Navaridas).
  5. La misma regla aplicada a SIGA, que era el conector denunciado.

Todos los tests son OFFLINE: la sesión HTTP se sustituye por un doble y los
datos salen de tests/fixtures/artxibo_*. Cualquier intento de red real sería
un fallo del test.

Ejecución:  python -m pytest tests/test_artxibo_v105.py -q
"""
from __future__ import annotations

import json
import sqlite3
import inspect
from pathlib import Path

import pytest

import scrapers.archivos as archivos
import scrapers.artxibo as artxibo
from config import (ARTXIBO_ANIO_MAX, ARTXIBO_ANIO_MIN, MARCA_CONFIANZA_BAJA,
                    _consulta_conector_hecha)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FICHA_1885 = "artxibo_bautismo_6210597.html"
FICHA_1760 = "artxibo_bautismo_6210615.html"
BUSQUEDA_COMPUESTO = "artxibo_busqueda_bautismo_apellido_compuesto.json"
BUSQUEDA_TOKEN = "artxibo_busqueda_bautismo_apellido_token.json"


def _texto_fixture(nombre: str) -> str:
    return (FIXTURES / nombre).read_text(encoding="utf-8")


def _json_fixture(nombre: str) -> dict:
    return json.loads(_texto_fixture(nombre))


# ============================== DOBLES ======================================

class _Resp:
    def __init__(self, texto: str = "", json_obj=None, status: int = 200):
        self.text = texto
        self.status_code = status
        self._json = json_obj

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        if self._json is None:
            raise ValueError("la respuesta no es JSON")
        return self._json


class _SesionFalsa:
    """Sesión HTTP de mentira: cuenta y guarda lo que se le pide.

    ``respuestas`` es la cola de respuestas de los POST (una por búsqueda);
    cuando se agota, devuelve una búsqueda sin resultados.
    """

    def __init__(self, respuestas=None, jsessionid: str | None = "ABC123",
                 error: Exception | None = None, html_ficha: str = ""):
        self.respuestas = list(respuestas or [])
        self.html_portada = (f'<form action="x;jsessionid={jsessionid}" '
                             f'method="post">' if jsessionid else "<html></html>")
        self.error = error
        self.html_ficha = html_ficha
        self.gets: list[str] = []
        self.posts: list[str] = []
        self.payloads: list[dict] = []

    def get(self, url, **kwargs):
        self.gets.append(url)
        if self.error is not None:
            raise self.error
        if self.html_ficha and "getFicha" in url:
            return _Resp(self.html_ficha)
        return _Resp(self.html_portada)

    def post(self, url, json=None, **kwargs):     # noqa: A002 (firma de requests)
        self.posts.append(url)
        self.payloads.append(json or {})
        if self.error is not None:
            raise self.error
        crudo = self.respuestas.pop(0) if self.respuestas else {"data": []}
        return crudo if isinstance(crudo, _Resp) else _Resp(json_obj=crudo)


def _conn_memoria():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute("CREATE TABLE IF NOT EXISTS consultas_conectores "
                 "(clave TEXT PRIMARY KEY, fecha TEXT)")
    return conn


@pytest.fixture(autouse=True)
def _sin_estado_global(monkeypatch):
    """Entre tests: caché de jsessionid limpia y sin esperas de cortesía."""
    artxibo._JSID.clear()
    monkeypatch.setattr(artxibo.time, "sleep", lambda *_: None)
    monkeypatch.setattr(archivos.time, "sleep", lambda *_: None)
    yield
    artxibo._JSID.clear()


# ========================= PARSEO DE LAS FICHAS ============================

def test_ficha_1885_campos_completos():
    """La ficha de 1885 (Víctor Sáenz de Navarrete Dopico) se parsea entera."""
    datos = artxibo.parsear_ficha(_texto_fixture(FICHA_1885), "bautismo")
    campos = datos["campos"]
    assert datos["id"] == 6210597
    assert datos["fecha"] == "1885-03-10"          # la ficha da 10-03-1885
    assert campos["Fondo"] == "F006.329"
    assert campos["Signatura"] == "0193200301"
    assert campos["Folio/Página"] == "f.195 r."
    assert campos["Código referencia"] == "23041"
    assert campos["Diócesis"] == "Vitoria"
    assert campos["Territorio"] == "ÁLAVA"
    assert "577656" in datos["url_ahdv"]           # ficha del AHDV
    assert datos["personas"]["persona"]["completo"] == (
        "Victor Saenz de Navarrete Dopico")
    assert datos["personas"]["padre"]["completo"] == "Eusebio Saenz de Navarrete"
    assert datos["personas"]["madre"]["completo"] == "Leocadia Dopico"


def test_ficha_1760_campos_completos():
    """La ficha de 1760 (Joseph Simon, mismo fondo F006.329, otra diócesis)."""
    datos = artxibo.parsear_ficha(_texto_fixture(FICHA_1760), "bautismo")
    assert datos["id"] == 6210615
    assert datos["fecha"] == "1760-10-26"
    assert datos["campos"]["Diócesis"] == "Calahorra y la Calzada"
    assert datos["campos"]["Signatura"] == "0193200103"
    assert datos["campos"]["Folio/Página"] == "f.011 r. - v."
    assert datos["personas"]["persona"]["completo"] == (
        "Joseph Simon Saenz de Navarrete Gonzalez Moreno")
    assert "585725" in datos["url_ahdv"]


def test_ficha_no_cuela_el_formulario_de_sugerencias():
    """El modal de 'Sugerencias' tiene filas con la misma estructura
    (tabla-titulo/tabla-texto): su etiqueta 'Nombre:' NO es un campo de la
    ficha (regresión del primer extractor, que sí las colaba)."""
    datos = artxibo.parsear_ficha(_texto_fixture(FICHA_1885), "bautismo")
    assert "Nombre:" not in datos["campos"]
    assert "Correo electrónico:" not in datos["campos"]


def test_la_ficha_no_trae_parroquia_abuelos_ni_padrinos():
    """Documenta el límite REAL del portal: la ficha no los trae; la parroquia
    sale de la fila del buscador y los demás, de la copia literal."""
    campos = artxibo.parsear_ficha(_texto_fixture(FICHA_1885))["campos"]
    for etiqueta in ("Parroquia", "Abuelos", "Padrinos", "Abuelo paterno"):
        assert etiqueta not in campos
    assert "Hijo" in campos and "Padre" in campos and "Madre" in campos


def test_ficha_del_portal_de_verdad(monkeypatch):
    """ficha() va al endpoint correcto y devuelve lo parseado."""
    sesion = _SesionFalsa(html_ficha=_texto_fixture(FICHA_1885))
    datos = artxibo.ficha("bautismo", 6210597, sesion=sesion)
    assert sesion.gets[-1].endswith("bautismo/getFicha?bauid=6210597")
    assert datos["id"] == 6210597
    assert datos["url"].endswith("bauid=6210597")
    # sin id no hay ficha que pedir
    with pytest.raises(ValueError):
        artxibo.ficha("bautismo", "", sesion=sesion)


# ======================= PARSEO DE LA LISTA DE RESULTADOS ==================

def test_resultados_apellido_compuesto():
    """La respuesta real del apellido COMPLETO: 79 filas, y entre ellas la
    partida de 1885 con su cita completa (fondo, signatura, folio)."""
    crudo = _json_fixture(BUSQUEDA_COMPUESTO)
    filas = artxibo.parsear_resultados(crudo, "bautismo")
    assert crudo["recordsTotal"] == 79
    assert len(filas) == 79
    victor = [f for f in filas if f["id"] == 6210597]
    assert len(victor) == 1
    victor = victor[0]
    assert victor["fecha"] == "1885-03-10"
    assert victor["anio"] == 1885
    assert victor["persona"]["completo"] == "Victor Saenz de Navarrete Dopico"
    assert victor["padre"]["completo"] == "Eusebio Saenz de Navarrete"
    assert victor["madre"]["completo"] == "Leocadia Dopico"
    assert victor["parroquia"] == "La Purísima Concepción"
    assert victor["localidad"] == "Navaridas"
    assert victor["fondo"] == "F006.329"
    assert victor["signatura"] == "0193200301"
    assert victor["folio"] == "f.195 r."
    assert victor["url"].endswith("bautismo/getFicha?bauid=6210597")
    # la línea de corpus lleva la cita documental (lo que ve la fase 2)
    for dato in ("F006.329", "0193200301", "f.195 r.", "Navaridas",
                 "La Purísima Concepción"):
        assert dato in victor["texto"]


def test_los_huecos_del_indice_no_se_cuelan():
    """El portal rellena los huecos con '--' y devuelve None: nada de eso
    puede acabar en el texto del corpus."""
    filas = artxibo.parsear_resultados(_json_fixture(BUSQUEDA_COMPUESTO))
    assert all("--" not in f["persona"]["completo"] for f in filas)
    assert all("None" not in f["texto"] for f in filas)


def test_resultados_token_suelto_son_homonimos_del_siglo_xvi():
    """El token 'Saenz' devuelve 7.009 filas: la primera es de 1510. Es el
    ruido que la auditoría denunció; se guarda como fixture para no olvidarlo."""
    crudo = _json_fixture(BUSQUEDA_TOKEN)
    assert crudo["recordsTotal"] > 7000
    filas = artxibo.parsear_resultados(crudo)
    assert filas[0]["anio"] < 1600


def test_parsear_resultados_tolera_basura():
    assert artxibo.parsear_resultados(None) == []
    assert artxibo.parsear_resultados({"data": [None, "x", {"sin_id": 1}]}) == []


# ============================ LA PETICIÓN ==================================

def test_payload_con_las_claves_del_formulario():
    payload = artxibo.construir_payload(
        tipo="bautismo", apellido1="Saenz de Navarrete",
        anio_ini=1850, anio_fin=1900)
    assert payload["bautismoHijoApellido1"] == "Saenz de Navarrete"
    assert payload["anioInicial"] == "1850" and payload["anioFinal"] == "1900"
    assert payload["archivosDiocesanos"] == ["1"]      # archivo de Vitoria
    assert payload["start"] == 0 and payload["length"] == 100
    assert [c["data"] for c in payload["columns"]][0] == "baudfecsacra"
    with pytest.raises(ValueError):
        artxibo.construir_payload(tipo="confirmaciones")


def test_busqueda_usa_el_jsessionid_y_una_sola_peticion():
    sesion = _SesionFalsa(respuestas=[_json_fixture(BUSQUEDA_COMPUESTO)])
    filas = artxibo.buscar_sacramentales(apellido1="Saenz de Navarrete",
                                         anio_ini=1850, anio_fin=1900,
                                         sesion=sesion)
    assert len(filas) == 79
    assert len(sesion.posts) == 1
    assert sesion.posts[0].endswith("busquedaBautismo;jsessionid=ABC123")
    assert sesion.payloads[0]["bautismoHijoApellido1"] == "Saenz de Navarrete"
    # el jsessionid se pide UNA vez y se reutiliza
    sesion2 = _SesionFalsa(respuestas=[{"data": []}])
    artxibo.buscar_sacramentales(apellido1="Pelaz", sesion=sesion2)
    artxibo.buscar_sacramentales(apellido1="Pelaz", sesion=sesion2)
    assert len(sesion2.gets) == 1


def test_busqueda_pagina_hasta_recordsTotal():
    """ARREGLO del 2026-09-19: el portal devuelve 100 filas por petición y
    ordena por fecha ascendente. Con una sola petición, un apellido que sale
    144 veces en 1481-1900 traía solo las 100 MÁS ANTIGUAS y la partida de 1885
    se quedaba fuera (la opción 1 decía "no hay nada que pedir"). Ahora se pide
    la página siguiente."""
    def _fila(i):
        return {"bauid": 500000 + i, "baudfecsacra": "1700-01-01",
                "baubautizadonom": "X", "baubautizadoape1": "Saenz de Navarrete",
                "baubautizadoape2": "Dopico", "baumunicipio": "NAVARIDAS",
                "baulocalidad": "Navaridas", "bauparroquia": "La Purísima"}

    sesion = _SesionFalsa(respuestas=[
        {"recordsTotal": 144, "data": [_fila(i) for i in range(100)]},
        {"recordsTotal": 144, "data": [_fila(i) for i in range(100, 144)]},
    ])
    filas = artxibo.buscar_sacramentales(apellido1="Saenz de Navarrete",
                                         sesion=sesion)
    assert len(filas) == 144
    assert len(sesion.posts) == 2
    assert [p["start"] for p in sesion.payloads] == [0, 100]
    # y no sigue pidiendo cuando ya no hay más
    assert len(sesion.posts) == 2


def test_busqueda_no_pagina_si_cabe_en_una_pagina():
    sesion = _SesionFalsa(respuestas=[{"recordsTotal": 2, "data": [
        {"bauid": 1, "baudfecsacra": "1811-01-01",
         "baubautizadoape1": "Perez de Palomares", "baumunicipio": "AÑANA"}]}])
    artxibo.buscar_sacramentales(apellido1="Perez de Palomares", sesion=sesion)
    assert len(sesion.posts) == 1


def test_el_token_no_arrastra_miles_de_filas():
    """El respaldo por token se corta a propósito (MAX_FILAS_TOKEN): el índice
    devuelve 7.009 filas para 'Saenz' y no queremos ninguna de las 7.000."""
    sesion = _SesionFalsa(respuestas=[
        {"data": []},                                    # el compuesto: 0
        {"recordsTotal": 7009,
         "data": [{"bauid": i, "baudfecsacra": "1510-01-01",
                   "baubautizadoape1": "Saenz", "baumunicipio": "RIBERA ALTA"}
                  for i in range(100)]},
        {"data": []},
    ])
    filas, confianza = artxibo.buscar_apellido("Saenz de Navarrete",
                                               sesion=sesion)
    assert confianza == artxibo.CONFIANZA_BAJA
    assert len(filas) <= artxibo.MAX_FILAS_TOKEN
    assert sesion.payloads[1]["length"] == artxibo.MAX_FILAS_TOKEN


def test_busqueda_sin_jsessionid_no_revienta():
    sesion = _SesionFalsa(jsessionid=None, respuestas=[{"data": []}])
    assert artxibo.buscar_sacramentales(apellido1="Pelaz", sesion=sesion) == []
    assert sesion.posts[0].endswith("busquedaBautismo")


def test_sesion_caducada_lanza_y_no_pasa_por_buena():
    """Si el portal contesta HTML (sesión caducada), la búsqueda LANZA: el
    recolector lo tratará como fallo transitorio y NO lo cacheará como hecho."""
    sesion = _SesionFalsa(respuestas=[_Resp(texto="<html>error</html>")])
    with pytest.raises(RuntimeError):
        artxibo.buscar_sacramentales(apellido1="Pelaz", sesion=sesion)


def test_filtro_local_por_parroquia_y_municipio():
    """La API no filtra por texto libre: si se pide, se recorta en local."""
    respuestas = [_json_fixture(BUSQUEDA_COMPUESTO)]
    sesion = _SesionFalsa(respuestas=respuestas)
    filas = artxibo.buscar_sacramentales(
        apellido1="Saenz de Navarrete", parroquia=["Santísima Trinidad"],
        sesion=sesion)
    assert filas == []
    sesion = _SesionFalsa(respuestas=[_json_fixture(BUSQUEDA_COMPUESTO)])
    filas = artxibo.buscar_sacramentales(
        apellido1="Saenz de Navarrete", parroquia=["La Purísima Concepción"],
        municipio=["Navaridas"], sesion=sesion)
    assert filas and all(f["localidad"] == "Navaridas" for f in filas)


# ============== EL ARREGLO: EL APELLIDO COMPUESTO, PRIMERO =================

def test_compuesto_con_resultados_no_se_fragmenta():
    """EL ARREGLO. Con el apellido completo devolviendo filas, no se prueba
    ningún token: una sola petición y confianza alta."""
    sesion = _SesionFalsa(respuestas=[_json_fixture(BUSQUEDA_COMPUESTO)])
    filas, confianza = artxibo.buscar_apellido("Saenz de Navarrete",
                                               sesion=sesion)
    assert confianza == artxibo.CONFIANZA_ALTA
    assert len(sesion.posts) == 1
    assert sesion.payloads[0]["bautismoHijoApellido1"] == "Saenz de Navarrete"
    assert len(filas) == 79
    assert all(f["confianza"] == "alta" for f in filas)
    assert all(MARCA_CONFIANZA_BAJA not in f["texto"] for f in filas)


def test_compuesto_vacio_cae_a_tokens_y_los_marca(monkeypatch):
    """Si el compuesto devuelve CERO, se fragmenta: esos resultados van
    marcados de CONFIANZA BAJA (y queda el aviso en el log)."""
    sesion = _SesionFalsa(respuestas=[{"data": []},
                                      _json_fixture(BUSQUEDA_TOKEN)])
    filas, confianza = artxibo.buscar_apellido("Saenz de Navarrete",
                                               sesion=sesion)
    assert confianza == artxibo.CONFIANZA_BAJA
    assert len(sesion.posts) >= 2
    assert sesion.payloads[0]["bautismoHijoApellido1"] == "Saenz de Navarrete"
    assert {p["bautismoHijoApellido1"] for p in sesion.payloads[1:]} == {
        "Saenz", "Navarrete"}     # los dos tokens, nunca el compuesto otra vez
    assert all(" " not in p["bautismoHijoApellido1"]
               for p in sesion.payloads[1:])
    assert filas and all(f["confianza"] == "baja" for f in filas)
    assert all(MARCA_CONFIANZA_BAJA in f["texto"] for f in filas)


def test_apellido_simple_no_se_fragmenta_nunca():
    """'Pelaz' es un solo token: sin resultados, no hay nada que partir (una
    sola petición, confianza alta)."""
    sesion = _SesionFalsa(respuestas=[{"data": []}])
    filas, confianza = artxibo.buscar_apellido("Pelaz", sesion=sesion)
    assert (filas, confianza) == ([], artxibo.CONFIANZA_ALTA)
    assert len(sesion.posts) == 1


def test_compuesto_sin_resultados_ni_por_tokens():
    sesion = _SesionFalsa(respuestas=[])
    filas, confianza = artxibo.buscar_apellido("Perez de Palomares",
                                               sesion=sesion)
    assert filas == [] and confianza == artxibo.CONFIANZA_ALTA


# ============================== RECOLECTOR =================================

def _objetivo(municipio="Vitoria", provincia="alava", ap_p="Saenz de Navarrete",
              ap_m="Perez de Palomares"):
    return {"nombre": "Victor Saenz de Navarrete Dopico",
            "apellido_paterno": ap_p, "apellido_materno": ap_m,
            "municipio": municipio, "provincia": provincia,
            "provincias": [provincia]}


def test_recolector_solo_para_alava(monkeypatch):
    """Fuera de Álava/Araba no se consulta (y no se toca la red)."""
    def _explota(*a, **kw):
        raise AssertionError("no debe llamarse al portal fuera de Álava")

    monkeypatch.setattr(artxibo, "buscar_sacramentales", _explota)
    assert artxibo.recolector_artxibo(_objetivo(provincia="palencia")) == []


def test_recolector_no_filtra_por_el_municipio_del_objetivo(monkeypatch):
    """CLAVE: el objetivo dice 'Vitoria' y las partidas están en Navaridas.
    Filtrar por el municipio del objetivo tiraría la partida que buscamos."""
    monkeypatch.setattr(artxibo, "buscar_sacramentales",
                        lambda **kw: artxibo.parsear_resultados(
                            _json_fixture(BUSQUEDA_COMPUESTO)))
    monkeypatch.setattr(artxibo, "buscar_apellido",
                        lambda ap, **kw: (artxibo.parsear_resultados(
                            _json_fixture(BUSQUEDA_COMPUESTO)), "alta"))
    conn = _conn_memoria()
    docs = artxibo.recolector_artxibo(_objetivo(municipio="Vitoria"), conn=conn)
    assert docs
    assert any("Navaridas" in d["texto"] for d in docs)


def test_recolector_documentos_llevan_cita_y_origen():
    docs = artxibo._agrupar_docs(
        artxibo.parsear_resultados(_json_fixture(BUSQUEDA_COMPUESTO)),
        "bautismos 'Saenz de Navarrete' (toda Álava)", "Saenz de Navarrete",
        "alta")
    assert docs and all(d["origen"] == "artxibo" for d in docs)
    assert len(docs) == 7                      # 79 filas / 12 por documento
    assert "F006.329" in docs[0]["texto"]
    assert "[CONFIANZA BAJA]" not in docs[0]["titulo"]


def test_recolector_marca_la_consulta_hecha_y_no_la_repite(monkeypatch):
    conn = _conn_memoria()
    sesion = _SesionFalsa(respuestas=[_json_fixture(BUSQUEDA_COMPUESTO)])
    monkeypatch.setattr(artxibo, "SESSION", sesion)
    docs = artxibo.recolector_artxibo(_objetivo(ap_m=""), conn=conn)
    assert docs
    assert _consulta_conector_hecha(conn,
                                    "artxibo::bautismo::saenz de navarrete")
    assert len(sesion.posts) == 1
    # Segunda pasada con OTRA sesión: la caché lo salta (0 peticiones).
    sesion2 = _SesionFalsa(respuestas=[_json_fixture(BUSQUEDA_COMPUESTO)])
    monkeypatch.setattr(artxibo, "SESSION", sesion2)
    artxibo._JSID.clear()
    assert artxibo.recolector_artxibo(_objetivo(ap_m=""), conn=conn) == []
    assert sesion2.posts == [] and sesion2.gets == []


def test_recolector_fallo_de_red_va_a_cooldown_y_no_se_cachea(monkeypatch):
    """Un fallo (o la sesión caducada) NO puede quedar como 'consulta hecha':
    quedaría envenenada para siempre. Va a cooldown con TTL."""
    conn = _conn_memoria()
    sesion = _SesionFalsa(error=RuntimeError("timeout de prueba"))
    monkeypatch.setattr(artxibo, "SESSION", sesion)
    docs = artxibo.recolector_artxibo(_objetivo(ap_m=""), conn=conn)
    assert docs == []
    clave = "artxibo::bautismo::saenz de navarrete"
    assert not _consulta_conector_hecha(conn, clave)
    assert conn.execute("SELECT clave FROM consultas_conectores").fetchone()[0] \
        == f"fail::{clave}"


def test_recolector_sin_apellidos_no_consulta():
    assert artxibo.recolector_artxibo(
        _objetivo(ap_p="", ap_m=""), conn=_conn_memoria()) == []


# ================== LA MISMA REGLA APLICADA A SIGA =========================

def _objetivo_siga(ap_p="Saenz de Navarrete", ap_m=""):
    return {"nombre": "Victor Saenz de Navarrete Dopico",
            "apellido_paterno": ap_p, "apellido_materno": ap_m,
            "municipio": "Navaridas", "provincia": "alava",
            "provincias": ["alava"]}


def _fila_siga(texto: str) -> dict:
    return {"origen": "siga", "url": "https://internet.ahdv-geah.org/x",
            "titulo": "SIGA", "texto": texto}


def test_siga_prueba_el_compuesto_primero_y_no_fragmenta(monkeypatch):
    """Denuncia de la auditoría (sección 2.3): SIGA partía 'Saenz de
    Navarrete' en tokens y bajaba cientos de homónimos. Ahora el apellido
    completo va primero y, si devuelve filas, no se fragmenta.

    Única excepción legítima (y ya existía): la búsqueda de PRECISIÓN por
    nombre de pila + token, que no es lo mismo que soltar el token a secas.
    """
    detalle: list[tuple] = []

    def _siga_buscar(apellido1, **kwargs):
        detalle.append((apellido1, kwargs.get("nombre", "")))
        if apellido1 == "Saenz de Navarrete":
            return [_fila_siga("Bautismo 1885 de Victor Saenz de Navarrete")]
        return []

    monkeypatch.setattr(archivos, "siga_buscar", _siga_buscar)
    docs = archivos.recolector_siga(_objetivo_siga(), conn=_conn_memoria())
    assert detalle[0] == ("Saenz de Navarrete", "")
    # ningún token suelto SIN nombre de pila (eso es lo que inundaba el corpus)
    assert [ap for ap, nombre in detalle if " " not in ap and not nombre] == []
    assert docs and "CONFIANZA BAJA" not in docs[0]["texto"]


def test_siga_fragmenta_solo_si_el_compuesto_da_cero(monkeypatch):
    """Y cuando fragmenta, lo dice: los documentos salen marcados."""
    llamadas: list[str] = []

    def _siga_buscar(apellido1, **kwargs):
        llamadas.append(apellido1)
        if apellido1 == "Saenz de Navarrete":
            return []
        return [_fila_siga(f"Bautismo de alguien {apellido1}")]

    monkeypatch.setattr(archivos, "siga_buscar", _siga_buscar)
    docs = archivos.recolector_siga(_objetivo_siga(), conn=_conn_memoria())
    assert llamadas[0] == "Saenz de Navarrete"
    assert "Saenz" in llamadas and "Navarrete" in llamadas
    assert docs
    assert all(MARCA_CONFIANZA_BAJA in d["texto"] for d in docs)
    assert all(d["confianza"] == "baja" for d in docs)


def test_siga_apellido_simple_se_comporta_como_siempre(monkeypatch):
    """'Merillas' es un token: no hay fragmentación que aplicar."""
    llamadas: list[str] = []

    def _siga_buscar(apellido1, **kwargs):
        llamadas.append(apellido1)
        return [_fila_siga(f"Bautismo de alguien {apellido1}")]

    monkeypatch.setattr(archivos, "siga_buscar", _siga_buscar)
    docs = archivos.recolector_siga(_objetivo_siga(ap_p="Merillas"),
                                    conn=_conn_memoria())
    assert llamadas[0] == "Merillas"
    assert docs and docs[0]["confianza"] == "alta"


def test_anios_de_cobertura_declarados():
    """El conector declara la cobertura REAL medida en el portal (1481-1900):
    1901-1935 devuelve 0 filas, así que pedir después de 1900 no aporta."""
    assert (ARTXIBO_ANIO_MIN, ARTXIBO_ANIO_MAX) == (1481, 1900)
    firma = inspect.signature(artxibo.buscar_sacramentales)
    assert firma.parameters["anio_ini"].default == ARTXIBO_ANIO_MIN
    assert firma.parameters["anio_fin"].default == ARTXIBO_ANIO_MAX
    sesion = _SesionFalsa(respuestas=[{"data": []}])
    artxibo.buscar_sacramentales(apellido1="Pelaz", sesion=sesion)
    assert sesion.payloads[0]["anioInicial"] == "1481"
    assert sesion.payloads[0]["anioFinal"] == "1900"
