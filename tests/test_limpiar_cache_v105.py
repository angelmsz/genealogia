"""
tests/test_limpiar_cache_v105.py — PROPUESTA 1 + R-03: `--limpiar-cache-hallazgos`
purga SOLO las filas sospechosas del caché de extracción (v10.4.2).

PARA QUÉ
--------
La versión anterior de la fase 2 guardaba ``[]`` en `hallazgos_por_hash` cuando
un lote FALLABA. Esas filas hacen que el fragmento no se vuelva a extraer
NUNCA (la fase 2 lo da por hecho), así que la caché queda "envenenada" y la
extracción devuelve 0 hallazgos para siempre. Es la mitad del incidente del
13/09.

R-03 (revisión externa): un ``[]`` puede ser DOS cosas opuestas, y no se puede
borrar a ciegas:
  - "el modelo dice que en este fragmento no hay nada" (legítimo: el documento
    no contenía datos) -> NO se purga;
  - "este lote falló y lo apunté como vacío" (el bug) -> SÍ se purga.
Para distinguirlas, la tabla gana un MARCADOR de estado:
  'ok' (trae hallazgos) | 'vacio' (vacío legítimo) | 'fallo' (reservado) |
  NULL (fila antigua, sin marca: sospechosa).

Este comando borra las sospechosas —y solo esas— con tres garantías:
  1. antes dice CUÁNTAS va a borrar y desglosa el resto;
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

def _bd(tmp_path, filas, nombre: str = "cache_test.db",
        con_estado: bool = True) -> sqlite3.Connection:
    """BD temporal con la tabla de la caché.

    `filas` es una lista de (hash, contenido) o (hash, contenido, estado).
    Con `con_estado=False` se crea la tabla ANTIGUA (3 columnas, sin marcador)
    para comprobar que nada revienta con una BD sin migrar.
    """
    conn = sqlite3.connect(tmp_path / nombre)
    if con_estado:
        conn.execute("CREATE TABLE IF NOT EXISTS hallazgos_por_hash "
                     "(hash TEXT, modelo TEXT, hallazgos TEXT, estado TEXT, "
                     " PRIMARY KEY (hash, modelo))")
    else:
        conn.execute("CREATE TABLE IF NOT EXISTS hallazgos_por_hash "
                     "(hash TEXT, modelo TEXT, hallazgos TEXT, "
                     " PRIMARY KEY (hash, modelo))")
    for fila in filas:
        if con_estado:
            hash_, contenido, estado = (list(fila) + [None])[:3]
            conn.execute("INSERT OR REPLACE INTO hallazgos_por_hash "
                         "VALUES (?,?,?,?)", (hash_, "modelo/x", contenido,
                                              estado))
        else:
            hash_, contenido = fila[0], fila[1]
            conn.execute("INSERT OR REPLACE INTO hallazgos_por_hash "
                         "VALUES (?,?,?)", (hash_, "modelo/x", contenido))
    conn.commit()
    return conn


def _hashes(conn) -> set[str]:
    return {f[0] for f in conn.execute("SELECT hash FROM hallazgos_por_hash")}


# ==================== 1. QUÉ SE CONSIDERA "SOSPECHOSA" =====================

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


def test_purgables_detecta_solo_las_sospechosas(tmp_path):
    """R-03: vacío legítimo ('vacio') NO se purga; sin marca SÍ."""
    conn = _bd(tmp_path, [("buena1", BUENO, "ok"),
                          ("legitima", "[]", "vacio"),
                          ("sin_marca", "[]", None),
                          ("fallo", "[]", "fallo"),
                          ("rota", "{no json", "ok"),
                          ("buena2", BUENO, "ok")])

    assert sorted(fase2.filas_cache_purgables(conn)) == ["fallo", "rota",
                                                        "sin_marca"]


def test_resumen_desglosa_la_cache(tmp_path):
    conn = _bd(tmp_path, [("buena", BUENO, "ok"),
                          ("legitima", "[]", "vacio"),
                          ("sin_marca", "[]", None)])

    assert fase2.resumen_cache_hallazgos(conn) == {
        "total": 3, "con_hallazgos": 1, "vacios_legitimos": 1,
        "sospechosas": 1}


# ==================== 2. LIMPIAR (las buenas sobreviven) ===================

def test_limpiar_borra_solo_las_sospechosas(tmp_path):
    conn = _bd(tmp_path, [("buena1", BUENO, "ok"),
                          ("legitima", "[]", "vacio"),
                          ("sin_marca", "[]", None),
                          ("nula", "null", "ok"),
                          ("buena2", BUENO, "ok")])

    borradas = fase2.limpiar_cache_hallazgos(conn)

    assert borradas == 2
    assert _hashes(conn) == {"buena1", "legitima", "buena2"}
    assert fase2.filas_cache_purgables(conn) == []


def test_limpiar_sin_nada_que_borrar(tmp_path):
    conn = _bd(tmp_path, [("buena", BUENO, "ok"),
                          ("legitima", "[]", "vacio")])
    assert fase2.limpiar_cache_hallazgos(conn) == 0
    assert _hashes(conn) == {"buena", "legitima"}


def test_bd_sin_columna_estado_no_revienta(tmp_path):
    """Una BD sin migrar (tabla de 3 columnas) se lee igual: las filas sin
    marca son sospechosas, que es la dirección segura."""
    conn = _bd(tmp_path, [("buena", BUENO), ("vacia", "[]")],
               con_estado=False)

    assert sorted(fase2.filas_cache_purgables(conn)) == ["vacia"]
    assert fase2.resumen_cache_hallazgos(conn)["total"] == 2


def test_cachear_hallazgos_escribe_la_marca(tmp_path):
    """El marcador se escribe al cachear: 'vacio' cuando el modelo dijo que no
    había nada, 'ok' cuando trae hallazgos."""
    conn = _bd(tmp_path, [], con_estado=True)

    fase2._cachear_hallazgos(conn, "h1", "vacio", [])
    fase2._cachear_hallazgos(conn, "h2", "ok", [{"persona": "P"}])

    estados = dict(conn.execute("SELECT hash, estado FROM hallazgos_por_hash"))
    assert estados == {"h1": "vacio", "h2": "ok"}
    assert fase2.filas_cache_purgables(conn) == []


# ==================== 3. EL COMANDO (copia + confirmación) =================

def _comando(tmp_path, monkeypatch, filas, respuestas, con_estado=True):
    """Prepara una BD temporal, la conecta a main y lanza el comando."""
    import config
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)     # get_db() -> tmp
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)       # copia del .bak
    conn = _bd(tmp_path, filas, nombre="cache_agente.db",
               con_estado=con_estado)
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
    _comando(tmp_path, monkeypatch,
             [("buena", BUENO, "ok"), ("sin_marca", "[]", None)], [""])

    codigo = main.limpiar_cache_hallazgos_comando()

    salida = capsys.readouterr().out
    assert codigo == 0
    assert "1 sospechosas" in salida
    assert "Cancelado" in salida
    assert not (tmp_path / "cache_agente.db.bak").exists()
    conn = sqlite3.connect(tmp_path / "cache_agente.db")
    assert _hashes(conn) == {"buena", "sin_marca"}        # nada borrado


def test_comando_con_si_borra_y_deja_copia(tmp_path, monkeypatch, capsys):
    _comando(tmp_path, monkeypatch,
             [("buena", BUENO, "ok"), ("legitima", "[]", "vacio"),
              ("sin_marca", "[]", None)], ["s"])

    codigo = main.limpiar_cache_hallazgos_comando()

    salida = capsys.readouterr().out
    assert codigo == 0
    assert "copia de seguridad" in salida
    assert "1 filas sospechosas borradas" in salida
    assert (tmp_path / "cache_agente.db.bak").exists()
    # La copia conserva la foto de ANTES (las tres filas)...
    antes = sqlite3.connect(tmp_path / "cache_agente.db.bak")
    assert _hashes(antes) == {"buena", "legitima", "sin_marca"}
    # ...y la BD viva se queda con la buena y la vacía LEGÍTIMA.
    despues = sqlite3.connect(tmp_path / "cache_agente.db")
    assert _hashes(despues) == {"buena", "legitima"}


def test_comando_sin_sospechosas_no_pregunta(tmp_path, monkeypatch, capsys):
    _comando(tmp_path, monkeypatch,
             [("buena", BUENO, "ok"), ("legitima", "[]", "vacio")], [])

    codigo = main.limpiar_cache_hallazgos_comando()

    salida = capsys.readouterr().out
    assert codigo == 0
    assert "No hay nada que limpiar" in salida
    assert "1 vacías legítimas" in salida
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


# ==================== 4. R-06: modo NO interactivo (-y / --si) =============

def test_comando_con_asumir_no_pregunta_y_borra(tmp_path, monkeypatch, capsys):
    """R-06: con `asumir=True` (CLI -y/--si) no se pide nada por teclado, pero
    el desglose se imprime y la copia previa se hace igual."""
    _comando(tmp_path, monkeypatch,
             [("buena", BUENO, "ok"), ("sin_marca", "[]", None)], [])

    codigo = main.limpiar_cache_hallazgos_comando(asumir=True)

    salida = capsys.readouterr().out
    assert codigo == 0
    assert "modo no interactivo" in salida
    assert "copia de seguridad" in salida
    assert "1 filas sospechosas borradas" in salida
    assert (tmp_path / "cache_agente.db.bak").exists()
    despues = sqlite3.connect(tmp_path / "cache_agente.db")
    assert _hashes(despues) == {"buena"}


def test_el_flag_si_esta_en_el_cli_y_no_toca_los_flujos_que_gastan():
    """R-06: -y/--si afecta SOLO al comando de mantenimiento."""
    import inspect
    src = inspect.getsource(main.main)
    # OJO: "args.si" es subcadena de "args.sin_cache": hay que excluir esa.
    usos = [linea for linea in src.splitlines()
            if "args.si" in linea and "sin_cache" not in linea]
    assert len(usos) == 1, usos
    assert "limpiar_cache_hallazgos_comando(asumir=args.si)" in usos[0]
    # Y el texto de la ayuda deja claro que NO afecta a los flujos de gasto.
    assert "NO afecta a los flujos que gastan" in src
