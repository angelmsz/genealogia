"""
tests/test_siga_v10.py — TAREA 3 de la v10.0: SIGA sin fallback silencioso.

Antes, _siga_localidad devolvía 55 (Vitoria) para CUALQUIER municipio
fuera del mapa de localidades de SIGA: las búsquedas se hacían en la
localidad EQUIVOCADA y quedaban cacheadas como hechas (búsquedas muertas
para siempre). Ahora:

  - _siga_localidad devuelve None si el municipio no está en
    SIGA_LOCALIDADES (Vitoria solo si ES Vitoria; alias explícito
    'vitoria' -> 'vitoria-gasteiz' -> 55).
  - recolector_siga ante None: log_warn explícito, consulta OMITIDA y
    NO cacheada como hecha (0 búsquedas).

Todos los tests son OFFLINE: siga_buscar interceptado (cualquier intento
de red cuenta como llamada y falla el test).

Ejecución:  python -m pytest tests/test_siga_v10.py -q
"""
from __future__ import annotations

import sqlite3

import pytest

import scrapers.archivos as archivos
from config import _consulta_conector_hecha
from scrapers.archivos import (_siga_localidad, recolector_siga,
                                SIGA_LOCALIDADES)


def _conn_memoria():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    # Esquema real de config.py: los fallos van como 'fail::clave' en la
    # MISMA tabla (cooldown), no en una tabla aparte.
    conn.execute("CREATE TABLE IF NOT EXISTS consultas_conectores "
                 "(clave TEXT PRIMARY KEY, fecha TEXT)")
    return conn


def _objetivo(municipio: str) -> dict:
    return {"nombre": "Isidro Merillas", "apellido_paterno": "Merillas",
            "apellido_materno": "Panero", "municipio": municipio,
            "provincia": "alava", "provincias": ["alava"]}


def _espiar_siga(monkeypatch, registro: dict):
    """Spy de siga_buscar: cuenta llamadas y captura el id_localidad.
    Nunca toca la red: devuelve [] (los tests solo miden cómo se llama).
    Sin sleeps: los DELAY_DESCARGAS (2.5-5 s por consulta) volverían la
    suite lentísima (mismo criterio que test_conectores_v43.py)."""
    monkeypatch.setattr(archivos.time, "sleep", lambda *_: None)

    def _siga_buscar(apellido1, sacramento="", id_localidad=55, **kwargs):
        registro["n"] += 1
        registro["ids"].append(id_localidad)
        return []

    monkeypatch.setattr(archivos, "siga_buscar", _siga_buscar)


# ============================== TESTS =======================================

def test_siga_localidad_sin_fallback():
    """Municipio desconocido -> None (ya NO devuelve 55/Vitoria)."""
    assert _siga_localidad("Salas de los Infantes") is None
    assert _siga_localidad("Bogotá") is None
    assert _siga_localidad("") is None
    assert _siga_localidad(None) is None


def test_siga_localidad_vitoria_solo_si_es_vitoria():
    """Vitoria SOLO si ES Vitoria (alias explícito de vitoria-gasteiz)."""
    assert _siga_localidad("Vitoria") == 55
    assert _siga_localidad("Vitoria-Gasteiz") == 55
    assert _siga_localidad("vitoria gasteiz") is None  # no es la forma del mapa


def test_siga_localidad_municipio_conocido_como_antes():
    """Un municipio del mapa sigue devolviendo SU id (nada cambia)."""
    assert _siga_localidad("Navaridas") == 36
    assert _siga_localidad("Amurrio") == 3
    assert _siga_localidad("Laguardia") == 27
    # sanity: 55 es vitoria-gasteiz en el mapa real de SIGA
    assert SIGA_LOCALIDADES["vitoria-gasteiz"] == 55


def test_recolector_municipio_desconocido_cero_busquedas(monkeypatch, capsys):
    """Municipio fuera del mapa -> 0 búsquedas + aviso explícito + NADA
    cacheado como hecho (la consulta queda OMITIDA, no muerta)."""
    registro = {"n": 0, "ids": []}
    _espiar_siga(monkeypatch, registro)
    conn = _conn_memoria()

    docs = recolector_siga(_objetivo("Salas de los Infantes"), conn=conn)

    assert docs == []
    assert registro["n"] == 0                     # 0 búsquedas
    filas = conn.execute("SELECT COUNT(*) FROM consultas_conectores"
                         ).fetchone()[0]
    assert filas == 0                             # nada cacheado como hecho
    salida = capsys.readouterr().out + capsys.readouterr().err
    assert "NO está en el mapa" in salida         # aviso explícito
    assert "OMITIDA" in salida
    assert "NO cacheada" in salida


def test_recolector_municipio_vacio_no_busca_en_vitoria(monkeypatch, capsys):
    """Municipio vacío: antes caía al fallback 55 (Vitoria por la cara);
    ahora consulta omitida con aviso y sin cachear."""
    registro = {"n": 0, "ids": []}
    _espiar_siga(monkeypatch, registro)
    conn = _conn_memoria()

    docs = recolector_siga(_objetivo(""), conn=conn)

    assert docs == []
    assert registro["n"] == 0
    filas = conn.execute("SELECT COUNT(*) FROM consultas_conectores"
                         ).fetchone()[0]
    assert filas == 0
    salida = capsys.readouterr().out + capsys.readouterr().err
    assert "NO está en el mapa" in salida


def test_recolector_vitoria_busca_como_antes(monkeypatch, capsys):
    """Municipio Vitoria -> las búsquedas se lanzan con id_localidad=55
    (comportamiento COMO ANTES, ahora por ser Vitoria de verdad)."""
    registro = {"n": 0, "ids": []}
    _espiar_siga(monkeypatch, registro)
    conn = _conn_memoria()

    recolector_siga(_objetivo("Vitoria"), conn=conn)

    assert registro["n"] > 0                      # sí se buscaron cosas
    assert set(registro["ids"]) <= {55, ""}       # 55 (Vitoria) y "" (provincial)
    salida = capsys.readouterr().out + capsys.readouterr().err
    assert "NO está en el mapa" not in salida     # sin aviso de omisión


def test_recolector_navaridas_busca_como_antes(monkeypatch, capsys):
    """Municipio conocido (Navaridas, id 36) -> búsquedas con SU id."""
    registro = {"n": 0, "ids": []}
    _espiar_siga(monkeypatch, registro)
    conn = _conn_memoria()

    recolector_siga(_objetivo("Navaridas"), conn=conn)

    assert registro["n"] > 0
    assert set(registro["ids"]) <= {36, ""}       # 36 (Navaridas) y provincial
    salida = capsys.readouterr().out + capsys.readouterr().err
    assert "NO está en el mapa" not in salida


def test_recolector_cachea_como_antes(monkeypatch, capsys):
    """Regresión: con municipio conocido, las consultas realizadas SÍ se
    marcan como hechas en la caché de conectores (el comportamiento normal
    no cambia; solo el fallback mentiroso desaparece)."""
    registro = {"n": 0, "ids": []}
    _espiar_siga(monkeypatch, registro)
    conn = _conn_memoria()

    recolector_siga(_objetivo("Navaridas"), conn=conn)

    filas = conn.execute("SELECT COUNT(*) FROM consultas_conectores"
                         ).fetchone()[0]
    assert filas == registro["n"]                 # lo buscado queda cacheado
    if registro["n"]:
        clave = conn.execute(
            "SELECT clave FROM consultas_conectores LIMIT 1").fetchone()[0]
        assert _consulta_conector_hecha(conn, clave)
