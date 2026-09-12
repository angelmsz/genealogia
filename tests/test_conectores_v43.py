"""
tests/test_conectores_v43.py — Conectores con FALLO TRANSITORIO (v4.3).

Motivo (verificado en vivo el 2026-09-10): el portal PARES respondía HTTP
200 pero con la página 'Problema encontrado: Ha ocurrido un error al
conectar con la Base de Datos'. El conector antiguo interpretaba eso como
'0 localidades' y:
  1. cacheaba la consulta como HECHA (nunca más se reintentaba), y
  2. escribía en el corpus una NOTA NEGATIVA falsa ('el pueblo no está en
     el Catastro de Ensenada'), que además desaconsejaba la fuente.

ADDO (Palencia) estaba caído y la consulta se marcaba como hecha ANTES de
la petición: tampoco se reintentaba nunca.

v4.3 introduce:
  - ParesNoDisponible: distingue 'el servidor ha fallado' de 'no hay
    resultados' (que es un dato genealógico legítimo).
  - cooldown (fail:: + TTL de 6 h) en SQLite: el fallo se reintenta solo,
    sin repetirlo en cada objetivo de la misma ejecución.
  - dedup de filas en la paginación de SIGA.
"""

from __future__ import annotations

import sqlite3

import pytest

import scrapers.archivos as archivos
from config import (_conector_en_cooldown, _marcar_conector,
                    _marcar_conector_fallo, _consulta_conector_hecha)
from scrapers.archivos import (ParesNoDisponible, _pares_error_servidor,
                               recolector_addo, recolector_ensenada,
                               siga_buscar)


# ============================== FIXTURES ====================================

@pytest.fixture()
def conn():
    """SQLite en memoria con la tabla que usan los conectores."""
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE IF NOT EXISTS consultas_conectores "
              "(clave TEXT PRIMARY KEY, fecha TEXT)")
    return c


@pytest.fixture(autouse=True)
def _sin_sleeps(monkeypatch):
    """Ningún test debe dormir los DELAY_DESCARGAS (2.5-5 s)."""
    monkeypatch.setattr(archivos.time, "sleep", lambda *_: None)


def _objetivo(mun="Coreses", prov="Zamora"):
    return {"nombre": "Isidro Merillas Panero",
            "apellido_paterno": "Merillas",
            "municipio": mun, "provincia": prov, "provincias": [prov]}


# ======================= DETECCIÓN DEL ERROR DE PARES ======================

PAGINA_ERROR_PARES = ("""
<html><body><div id="central"><h2>Error</h2>
<div id="aviso"><span>Problema encontrado</span>
Ha ocurrido un error al conectar con la Base de Datos, puede que en
estos momentos no est&eacute; disponible. Int&eacute;ntelo m&aacute;s tarde.
</div></body></html>""")


def test_detecta_pagina_de_error_del_servidor():
    assert _pares_error_servidor(PAGINA_ERROR_PARES)
    assert not _pares_error_servidor("<html><body>tabla de resultados</body>")
    assert not _pares_error_servidor("")


def test_buscar_localidades_lanza_pares_no_disponible(monkeypatch):
    """La página de error de BD debe LANZAR (transitorio), no devolver []
    ([] significaría 'no está en el catastro' y generaría una nota
    negativa FALSA)."""
    class _Resp:
        text = PAGINA_ERROR_PARES

        def raise_for_status(self):
            pass

    class _Session:
        def get(self, *a, **kw):
            return _Resp()

        def post(self, *a, **kw):
            return _Resp()

    monkeypatch.setattr(archivos, "SESSION", _Session())
    with pytest.raises(ParesNoDisponible):
        archivos.buscar_localidades_ensenada("Zamora")


def test_buscar_localidades_conexion_rota_lanza(monkeypatch):
    """Si PARES ni siquiera responde (timeout/conexión), también es un
    fallo transitorio, no un 'no encontrado'."""

    class _SessionRota:
        def get(self, *a, **kw):
            raise ConnectionError("portal caído")

    monkeypatch.setattr(archivos, "SESSION", _SessionRota())
    with pytest.raises(ParesNoDisponible):
        archivos.buscar_localidades_ensenada("Zamora")


# ================== RECOLECTOR ENSENADA: SIN NOTAS NEGATIVAS FALSAS ========

def test_pares_caido_no_escribe_nota_negativa_ni_cachea(monkeypatch, conn):
    def _caido(nombre):
        raise ParesNoDisponible("error de BD del servidor")

    monkeypatch.setattr(archivos, "buscar_localidades_ensenada", _caido)
    docs = recolector_ensenada(_objetivo(), conn=conn)
    assert docs == []            # nada utilizable...
    textos = " ".join(d.get("texto", "") for d in docs)
    assert "NOTA NEGATIVA" not in textos
    # ...y la consulta NO queda marcada como hecha (se reintentará):
    assert not _consulta_conector_hecha(
        conn, "ensenada::coreses::zamora")
    # pero SÍ queda en cooldown (para no repetirla en cada objetivo):
    assert _conector_en_cooldown(conn, "ensenada::coreses::zamora")


def test_cooldown_expira_y_se_reintenta(conn):
    """El cooldown es temporal: se marca el fallo con fecha antigua
    manipulando la tabla y la consulta vuelve a poder lanzarse."""
    from datetime import datetime, timedelta
    clave = "ensenada::coreses::zamora"
    _marcar_conector_fallo(conn, clave)
    assert _conector_en_cooldown(conn, clave)   # recién fallido: en cooldown
    # simular que el fallo fue hace 12 horas (> TTL de 6 h):
    conn.execute(
        "UPDATE consultas_conectores SET fecha=? WHERE clave=?",
        ((datetime.now() - timedelta(hours=12)).isoformat(
            timespec="seconds"), f"fail::{clave}"))
    conn.commit()
    assert not _conector_en_cooldown(conn, clave)


def test_respuesta_vacia_si_genera_nota_negativa(monkeypatch, conn):
    """Cuando PARES RESPONDE de verdad y no hay localidades, la nota
    negativa SÍ es un dato legítimo (comportamiento v4.2 conservado)."""
    monkeypatch.setattr(archivos, "buscar_localidades_ensenada",
                        lambda nombre: [])
    docs = recolector_ensenada(_objetivo(), conn=conn)
    assert len(docs) == 1
    assert "NOTA NEGATIVA" in docs[0]["texto"]
    assert _consulta_conector_hecha(conn, "ensenada::coreses::zamora")


def test_respuesta_con_localidades_no_marca_notas(monkeypatch, conn):
    locs = [{"actual": "Coreses", "antigua": "Coreses",
             "entidad": "Zamora", "provincia": "Zamora", "loc_id": "12345"}]
    monkeypatch.setattr(archivos, "buscar_localidades_ensenada",
                        lambda nombre: locs)
    docs = recolector_ensenada(_objetivo(), conn=conn)
    assert len(docs) == 1
    assert "Coreses" in docs[0]["texto"]
    assert "NOTA NEGATIVA" not in docs[0]["texto"]


# ======================= ADDO: FALLO -> COOLDOWN ===========================

def test_addo_caido_no_cachea_como_hecha(monkeypatch, conn):
    """v4.2 marcaba la consulta ANTES de pedir (ADDO caído = 'investigado'
    para siempre). v4.3: fallo transitorio con cooldown."""

    class _SessionRota:
        def get(self, *a, **kw):
            raise ConnectionError("timeout")

    monkeypatch.setattr(archivos, "SESSION", _SessionRota())
    monkeypatch.setattr(archivos, "ADDO_BUSQUEDA", "",
                        raising=False)  # usa las URLs por defecto
    docs = recolector_addo(_objetivo("Saldaña", "Palencia"), conn=conn)
    assert docs == []
    ap = "Merillas"
    # no hay clave 'addo::...' (hecha), solo 'fail::addo::...' (cooldown)
    claves = [r[0] for r in conn.execute(
        "SELECT clave FROM consultas_conectores").fetchall()]
    assert claves and all(c.startswith("fail::addo::") for c in claves)


def test_addo_caido_en_cooldown_no_repite_la_peticion(monkeypatch, conn):
    """En la MISMA ejecución, una vez en cooldown no se vuelve a pedir la
    MISMA consulta (mismo apellido + URL): una sola pérdida de 30 s, no
    una por objetivo."""
    llamadas = {"n": 0}

    class _SessionRota:
        def get(self, *a, **kw):
            llamadas["n"] += 1
            raise ConnectionError("timeout")

    monkeypatch.setattr(archivos, "SESSION", _SessionRota())
    obj = _objetivo("Saldaña", "Palencia")
    recolector_addo(obj, conn=conn)
    # mismo objetivo otra vez: las claves fallidas están en cooldown
    recolector_addo(obj, conn=conn)
    # solo el primer intento tocó la red (una vez por URL candidata)
    n_urls = len([f"{archivos.ADDO_URL}/?s=",
                  f"{archivos.ADDO_URL}/busqueda?query="])
    assert llamadas["n"] == n_urls


# ======================= SIGA: DEDUP DE PAGINACIÓN =========================

# Layout real de la tabla de resultados de SIGA: col1 = ID de registro,
# col2 = Persona, col3 = Fecha (el parser localiza 'fecha' dinámicamente).
# La tabla necesita >= 3 filas (cabecera + datos) para que el parser la tome.
HTML_SIGA = ("""
<html><body><table>
<tr><td></td><td>ID</td><td>Persona</td><td>Fecha</td><td>Sexo</td></tr>
<tr><td>1</td><td>R001</td><td>Merillas, Isidro</td><td>1870-05-02</td>
<td>M</td></tr>
<tr><td>2</td><td>R002</td><td>Merillas, Obdulia</td><td>1872-01-10</td>
<td>F</td></tr>
</table></body></html>""")


def test_siga_paginacion_no_duplica_filas(monkeypatch, conn):
    """La página 2 de SIGA puede devolver lo mismo que la 1 (el parámetro
    page no siempre aplica): v4.3 deduplica y corta la paginación."""

    class _Resp:
        status_code = 200
        text = HTML_SIGA

        def raise_for_status(self):
            pass

    class _Session:
        def post(self, *a, **kw):
            return _Resp()

        def get(self, *a, **kw):
            return _Resp()   # página 2 = MISMO contenido que la 1

    monkeypatch.setattr(archivos, "SESSION", _Session())
    docs = siga_buscar("Merillas", sacramento="bautismo", id_localidad=55)
    textos = [d["texto"] for d in docs]
    assert len(textos) == len(set(textos)), \
        "las páginas repetidas no deben duplicar registros"
    assert len(docs) == 2   # las 2 filas de la página 1, ni una más


def test_siga_paginacion_acumula_filas_distintas(monkeypatch, conn):
    """Cuando la página 2 trae filas NUEVAS de verdad, se acumulan."""
    html_p2 = HTML_SIGA.replace("R001", "R003").replace(
        "R002", "R004").replace("Merillas, Isidro", "Pelaz, Nazario").replace(
        "Merillas, Obdulia", "Pelaz, Obdulia")

    class _Resp:
        def __init__(self, texto):
            self.status_code = 200
            self.text = texto

        def raise_for_status(self):
            pass

    class _Session:
        def post(self, *a, **kw):
            return _Resp(HTML_SIGA)

        def get(self, *a, **kw):
            return _Resp(html_p2)

    monkeypatch.setattr(archivos, "SESSION", _Session())
    docs = siga_buscar("Merillas", sacramento="bautismo", id_localidad=55)
    assert len(docs) == 4   # 2 de la página 1 + 2 nuevas de la página 2


def test_marcado_de_conector_normal_sigue_permanente(conn):
    """Las consultas CON ÉXITO siguen cacheadas para siempre (comportamiento
    v4.2); solo los fallos tienen TTL."""
    clave = "siga::bautismo::merillas::55"
    _marcar_conector(conn, clave)
    assert _consulta_conector_hecha(conn, clave)
    # el marcado normal no activa el cooldown:
    assert not _conector_en_cooldown(conn, clave)
