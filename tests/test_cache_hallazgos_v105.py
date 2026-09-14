"""
tests/test_cache_hallazgos_v105.py — BLOQUE 0: la caché de hallazgos no puede
guardar un FALLO como si fuera un resultado vacío (v10.4.2).

EL INCIDENTE (13/09/2026), reproducido en el harness aislado
------------------------------------------------------------
`agent/fase2.extraer_hallazgos` guardaba en `hallazgos_por_hash` la lista
``respuesta`` incluso cuando el lote había FALLADO (la lista quedaba vacía).
Resultado: un fallo transitorio (400, timeout, modelo saturado) se convertía
en "en este fragmento no hay nada" PARA SIEMPRE; la ejecución siguiente leía
esa fila vacía, no preguntaba al modelo, extraía 0 hallazgos y sobrescribía
`arbol_hallazgos.json` con ``[]``.

Log real del experimento (harness aislado, LLM de mentira, coste 0):
    [⚙] Extrayendo hallazgos: lote 1/9
    [!] deepseek-v4.1-flash error (intento 2/2): simulado: 400 del proveedor
    [x] LLM inaccesible tras 2 intentos: simulado: 400 del proveedor
    [!] lote 1 falló: LLM inaccesible tras 2 intentos: simulado: 400 ...
    CACHE: 9 filas, 1 VACIAS   <- la fila del fragmento del lote fallido
    2ª ejecución: el lote 1 ni aparece en el log (nunca se reintenta).

Ejecución:  python -m pytest tests/test_cache_hallazgos_v105.py -q
"""
from __future__ import annotations

import json
import re
import sqlite3

import config
from agent import fase2
from tests.harness_aislado import (copiar_entradas, huellas_de_estado,
                                   lanzar_sin_claves)


# ============================== UTILIDADES =================================

def _bd_temporal(tmp_path) -> sqlite3.Connection:
    """SQLite temporal con la tabla de la caché de hallazgos."""
    conn = sqlite3.connect(tmp_path / "cache_test.db")
    conn.execute("CREATE TABLE IF NOT EXISTS hallazgos_por_hash "
                 "(hash TEXT, modelo TEXT, hallazgos TEXT, "
                 " PRIMARY KEY (hash, modelo))")
    return conn


def _fragmento(url: str = "https://ejemplo.invalid/acta") -> dict:
    return {"hash": config.sha256_corto(url + "texto"), "url": url,
            "persona": "Persona de prueba", "texto_limpio": "Partida de bautismo"}


def _fila(conn, fragmento) -> str | None:
    fila = conn.execute("SELECT hallazgos FROM hallazgos_por_hash "
                        "WHERE hash=? AND modelo=?",
                        (fragmento["hash"], config.MODELO_FASE2)).fetchone()
    return fila[0] if fila else None


def _sembrar_vacio(conn, fragmento) -> None:
    """Deja la caché como la dejaba la versión antigua tras un fallo."""
    with config.DB_LOCK:
        conn.execute("INSERT OR REPLACE INTO hallazgos_por_hash VALUES (?,?,?)",
                     (fragmento["hash"], config.MODELO_FASE2, "[]"))
        conn.commit()


def _url_del_prompt(texto: str) -> str:
    return re.search(r'"url": "([^"]*)"', texto).group(1)


# ============================== EL BUG ======================================

def test_lote_fallido_no_se_cachea(tmp_path, monkeypatch):
    """Un lote que falla NO deja fila en la caché (antes dejaba '[]')."""
    conn = _bd_temporal(tmp_path)
    frag = _fragmento()

    def _explota(*args, **kwargs):
        raise RuntimeError("simulado: 400 del proveedor")

    monkeypatch.setattr(fase2, "chat_json", _explota)

    hallazgos = fase2.extraer_hallazgos([frag], conn)

    assert hallazgos == []
    assert _fila(conn, frag) is None, "el fallo se cacheó como resultado vacío"


def test_atribucion_dudosa_no_se_cachea(tmp_path, monkeypatch):
    """El lote trajo hallazgos, pero ninguno con la URL de este fragmento: no
    se puede afirmar que esté vacío -> no se cachea '[]'."""
    conn = _bd_temporal(tmp_path)
    frag = _fragmento()
    monkeypatch.setattr(fase2, "chat_json", lambda *a, **k: {
        "hallazgos": [{"persona": "Otra", "url_fuente": "https://otra.invalid/x"}]})

    fase2.extraer_hallazgos([frag], conn)

    assert _fila(conn, frag) is None


def test_respuesta_vacia_honesta_si_se_cachea(tmp_path, monkeypatch):
    """Si el modelo responde "nada" para el lote entero, SÍ se cachea la lista
    vacía: es un resultado honesto y no hay que volver a pagarlo."""
    conn = _bd_temporal(tmp_path)
    frag = _fragmento()
    monkeypatch.setattr(fase2, "chat_json", lambda *a, **k: {"hallazgos": []})

    fase2.extraer_hallazgos([frag], conn)

    assert json.loads(_fila(conn, frag)) == []


def test_fallo_no_contamina_el_resultado_de_los_demas(tmp_path, monkeypatch):
    """Un lote fallido no impide que los demás se extraigan y se cacheen."""
    conn = _bd_temporal(tmp_path)
    frags = [_fragmento(f"https://ejemplo.invalid/{i}") for i in range(3)]
    llamadas = {"n": 0}

    def _chat(modelo, sistema, usuario, **kwargs):
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            raise RuntimeError("timeout simulado")
        return {"hallazgos": [{"persona": "P",
                               "url_fuente": _url_del_prompt(usuario)}]}

    monkeypatch.setattr(fase2, "chat_json", _chat)
    hallazgos = fase2.extraer_hallazgos(frags, conn)

    assert len(hallazgos) == 2                    # los 2 lotes que respondieron
    assert _fila(conn, frags[0]) is None          # el fallido, sin cachear
    assert json.loads(_fila(conn, frags[1]))      # los buenos, cacheados


# ==================== RECUPERACIÓN DE UNA CACHÉ ENVENENADA =================

def test_sin_cache_reextrae_aunque_la_fila_este_vacia(tmp_path, monkeypatch):
    """--sin-cache ignora la caché: es la vía para recuperar fragmentos que una
    versión anterior marcó como vacíos por un fallo transitorio."""
    conn = _bd_temporal(tmp_path)
    frag = _fragmento()
    _sembrar_vacio(conn, frag)
    monkeypatch.setattr(fase2, "chat_json", lambda *a, **k: {
        "hallazgos": [{"persona": "P", "url_fuente": frag["url"]}]})

    hallazgos = fase2.extraer_hallazgos([frag], conn, sin_cache=True)

    assert len(hallazgos) == 1                    # recuperado del modelo
    assert json.loads(_fila(conn, frag))          # y la caché queda corregida


def test_sin_sin_cache_la_fila_vacia_se_respeta(tmp_path, monkeypatch):
    """Sin --sin-cache, una fila vacía se da por buena (comportamiento normal
    de la caché): por eso existe la palanca --sin-cache."""
    conn = _bd_temporal(tmp_path)
    frag = _fragmento()
    _sembrar_vacio(conn, frag)
    llamadas = {"n": 0}

    def _chat(*args, **kwargs):
        llamadas["n"] += 1
        return {"hallazgos": []}

    monkeypatch.setattr(fase2, "chat_json", _chat)

    assert fase2.extraer_hallazgos([frag], conn) == []
    assert llamadas["n"] == 0


# ============================== EL HARNESS ==================================

def test_hijo_aislado_no_toca_ningun_fichero_del_proyecto(tmp_path):
    """El harness cumple lo que promete: el hijo trabaja en la carpeta temporal
    y NO modifica (ni crea) ningún fichero del proyecto.

    Se usa --frontera porque es gratis (0 tokens) y escribe ficheros de estado:
    si el aislamiento fallara, se vería aquí.
    """
    antes = huellas_de_estado()
    copiar_entradas(tmp_path)
    r = lanzar_sin_claves(["--frontera"], tmp_path)

    assert r.returncode == 0, r.stdout[-900:] + r.stderr[-400:]
    assert huellas_de_estado() == antes, "el hijo escribió en el proyecto"
    assert (tmp_path / "estado_investigacion.json").exists()   # sí trabajó


def test_hijo_aislado_no_ve_las_claves_del_env(tmp_path):
    """El hijo no hereda el .env real: config se parchea EN MEMORIA antes de
    importar main (así el fallo de Windows con las variables vacías no puede
    repetirse)."""
    copiar_entradas(tmp_path)
    r = lanzar_sin_claves(["--frontera"], tmp_path)
    assert r.returncode == 0, r.stdout[-900:] + r.stderr[-400:]
    assert "Traceback" not in r.stderr
