"""
tests/test_limpiar_cache_v105.py — PROPUESTA 1: `--limpiar-cache-hallazgos`
purga SOLO las filas inútiles del caché de extracción (v10.4.2).

PARA QUÉ
--------
La versión anterior de la fase 2 guardaba ``[]`` en `hallazgos_por_hash` cuando
un lote FALLABA. Esas filas hacen que el fragmento no se vuelva a extraer
NUNCA (la fase 2 lo da por hecho), así que la caché queda "envenenada" y la
extracción devuelve 0 hallazgos para siempre. Es la mitad del incidente del
13/09.

Este comando borra esas filas —y solo esas— con tres garantías:
  1. antes dice CUÁNTAS va a borrar (y cuántas hay en total);
  2. pide confirmación explícita con Enter = n;
  3. deja copia de la base de datos en `cache_agente.db.bak`.

Ejecución:  python -m pytest tests/test_limpiar_cache_v105.py -q
"""
from __future__ import annotations

import json
import sqlite3

import pytest

import main
from agent import fase2

BUENO = json.dumps([{"persona": "P", "url_fuente": "https://x/1"}])


# ============================== UTILIDADES =================================

def _bd(tmp_path, filas: list[tuple[str, str]],
        nombre: str = "cache_test.db") -> sqlite3.Connection:
    """BD temporal con la tabla de la caché y las filas indicadas."""
    conn = sqlite3.connect(tmp_path / nombre)
    conn.execute("CREATE TABLE IF NOT EXISTS hallazgos_por_hash "
                 "(hash TEXT, modelo TEXT, hallazgos TEXT, "
                 " PRIMARY KEY (hash, modelo))")
    for hash_, contenido in filas:
        conn.execute("INSERT OR REPLACE INTO hallazgos_por_hash VALUES (?,?,?)",
                     (hash_, "modelo/x", contenido))
    conn.commit()
    return conn


def _hashes(conn) -> set[str]:
    return {f[0] for f in conn.execute("SELECT hash FROM hallazgos_por_hash")}


# ==================== 1. QUÉ SE CONSIDERA "INÚTIL" =========================

@pytest.mark.parametrize("contenido,inutil", [
    ("[]", True),                       # lo que dejaba un lote fallido
    ("", True),
    ("null", True),
    ("{esto no es json", True),         # ilegible
    (BUENO, False),                     # con hallazgos: se respeta
    (json.dumps([{"persona": "P"}]), False),
])
def test_fila_sin_hallazgos(contenido, inutil):
    assert fase2._fila_sin_hallazgos(contenido) is inutil


def test_filas_vacias_detecta_solo_las_inutiles(tmp_path):
    conn = _bd(tmp_path, [("buena1", BUENO), ("vacia", "[]"),
                          ("rota", "{no json"), ("buena2", BUENO)])

    assert sorted(fase2.filas_cache_vacias(conn)) == ["rota", "vacia"]


# ==================== 2. LAS BUENAS SOBREVIVEN =============================

def test_limpiar_borra_solo_las_vacias_y_devuelve_cuantas(tmp_path):
    conn = _bd(tmp_path, [("buena1", BUENO), ("vacia", "[]"),
                          ("nula", "null"), ("buena2", BUENO)])

    borradas = fase2.limpiar_cache_hallazgos(conn)

    assert borradas == 2
    assert _hashes(conn) == {"buena1", "buena2"}      # las buenas, intactas
    assert fase2.filas_cache_vacias(conn) == []       # y ya no queda nada


def test_limpiar_sin_nada_que_borrar(tmp_path):
    conn = _bd(tmp_path, [("buena", BUENO)])
    assert fase2.limpiar_cache_hallazgos(conn) == 0
    assert _hashes(conn) == {"buena"}


# ==================== 3. EL COMANDO (copia + confirmación) =================

def _comando(tmp_path, monkeypatch, filas, respuestas):
    """Prepara una BD temporal, la conecta a main y lanza el comando."""
    import config
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)     # get_db() -> tmp
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)       # copia del .bak
    conn = _bd(tmp_path, filas, nombre="cache_agente.db")
    conn.close()                                          # get_db() reabre
    cola = list(respuestas)

    def _input(prompt: str = "") -> str:
        if not cola:
            raise EOFError
        return cola.pop(0)

    monkeypatch.setattr("builtins.input", _input)
    return cola


def test_comando_enter_no_borra_nada(tmp_path, monkeypatch, capsys):
    """Enter en la confirmación = n: no se borra y no se deja .bak."""
    _comando(tmp_path, monkeypatch, [("buena", BUENO), ("vacia", "[]")], [""])

    codigo = main.limpiar_cache_hallazgos_comando()

    salida = capsys.readouterr().out
    assert codigo == 0
    assert "1 sin hallazgos" in salida
    assert "Cancelado" in salida
    assert not (tmp_path / "cache_agente.db.bak").exists()
    conn = sqlite3.connect(tmp_path / "cache_agente.db")
    assert _hashes(conn) == {"buena", "vacia"}        # nada borrado


def test_comando_con_si_borra_y_deja_copia(tmp_path, monkeypatch, capsys):
    _comando(tmp_path, monkeypatch, [("buena", BUENO), ("vacia", "[]")], ["s"])

    codigo = main.limpiar_cache_hallazgos_comando()

    salida = capsys.readouterr().out
    assert codigo == 0
    assert "copia de seguridad" in salida
    assert "1 filas inútiles borradas" in salida
    assert (tmp_path / "cache_agente.db.bak").exists()
    # La copia conserva la fila vacía (es la foto de ANTES) y la buena.
    antes = sqlite3.connect(tmp_path / "cache_agente.db.bak")
    assert _hashes(antes) == {"buena", "vacia"}
    # Y la BD viva se queda solo con la buena.
    despues = sqlite3.connect(tmp_path / "cache_agente.db")
    assert _hashes(despues) == {"buena"}


def test_comando_sin_filas_inutiles_no_pregunta(tmp_path, monkeypatch, capsys):
    _comando(tmp_path, monkeypatch, [("buena", BUENO)], [])

    codigo = main.limpiar_cache_hallazgos_comando()

    salida = capsys.readouterr().out
    assert codigo == 0
    assert "No hay nada que limpiar" in salida
    assert not (tmp_path / "cache_agente.db.bak").exists()


def test_el_flag_existe_y_sale_antes_de_la_validacion_de_claves():
    """El flag está en el CLI y es de los modos que NO gastan: sale antes de
    la validación de claves y de la consulta de precios."""
    import inspect
    src = inspect.getsource(main.main)
    assert "args.limpiar_cache_hallazgos" in src
    assert (src.index("if args.limpiar_cache_hallazgos:")
            < src.index("validar_credenciales_api("))
    assert (src.index("if args.limpiar_cache_hallazgos:")
            < src.index("_fijar_precios_del_dia()"))
