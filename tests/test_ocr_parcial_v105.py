"""
v10.4.1 (tarea A) — La caché de OCR no puede fingir que un documento está
completo.

Contexto REAL (log agente_20260912_222149.log): la noche procesó 4 PDFs y en
3 de ellos se quedó en las 30 primeras páginas (355, 240 y 355 páginas: 860
páginas sin tocar) por OCR_MAX_PAGINAS_LOCAL=30. Lo grave no es el límite: es
que la caché guardaba (hash_pdf, texto, backend, confianza) SIN las páginas,
así que en el siguiente arranque `_ocr_cache_get` devolvía el trozo como si
fuera el documento entero, y el aviso de truncamiento solo se imprimía al
rasterizar (nunca en un acierto de caché). Para el investigador era
indistinguible de "lo miré entero y no había nada": un falso negativo de por
vida. El registro de evidencia negativa (P3) tampoco lo veía, porque solo
apunta objetivos de fase 1 sin fragmentos.

Lo que fija esta suite:
  1. La caché guarda y devuelve las páginas (procesadas / total).
  2. Un acierto de caché de un documento PARCIAL lo dice en el log.
  3. La parcialidad queda registrada como "parcial: X/Y páginas" en la
     evidencia negativa (no como "buscado y no encontrado").
  4. Compatibilidad: una BD con la tabla de 4 columnas (sin migrar) sigue
     funcionando; las páginas quedan como "no consta".
  5. La migración del esquema añade las columnas a una BD que ya existe.

Sin red, sin GPU: rasterización y OCR simulados.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

import config
from agent import evidencia_negativa
from scrapers import web

URL = "https://ejemplo.invalid/penas_de_muerte_conmutadas_1940-1953.pdf"


def _conn(columnas: int = 6) -> sqlite3.Connection:
    """BD en memoria con la tabla ocr_cache REAL (6 columnas) o la antigua."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    if columnas == 6:
        conn.execute("CREATE TABLE IF NOT EXISTS ocr_cache "
                     "(hash_pdf TEXT PRIMARY KEY, texto TEXT, "
                     " backend_usado TEXT, confianza REAL, "
                     " paginas_procesadas INTEGER, paginas_total INTEGER)")
    else:
        conn.execute("CREATE TABLE IF NOT EXISTS ocr_cache "
                     "(hash_pdf TEXT PRIMARY KEY, texto TEXT, "
                     " backend_usado TEXT, confianza REAL)")
    return conn


@pytest.fixture()
def oce_simulado(monkeypatch):
    """Rasterización + OCR simulados: 3 páginas de 355 (el caso del log)."""
    llamadas = {"ocr": 0}
    monkeypatch.setattr(web, "OCR_BACKEND", "llamacpp")
    monkeypatch.setattr(web, "_es_manuscrito", lambda url: False)
    monkeypatch.setattr(web, "_pdf_a_imagenes",
                        lambda b, m: ([b"img"] * 3, 3, 355))

    def _ocr(imagenes):
        llamadas["ocr"] += 1
        return "texto transcrito", 0.85

    monkeypatch.setattr(web, "_ocr_llamacpp", _ocr)
    monkeypatch.setattr(web, "_llamacpp_config_familia",
                        lambda: {"nombre": "GLM-OCR", "familia": "glm-ocr"})
    return llamadas


@pytest.fixture()
def registro_en_tmp(tmp_path, monkeypatch):
    """El registro de evidencia negativa, en un directorio de prueba."""
    monkeypatch.setattr(evidencia_negativa, "BASE_DIR", tmp_path)
    return tmp_path / config.EVIDENCIA_NEGATIVA


# ------------------------------------------- 1. la caché guarda páginas ---
def test_la_cache_guarda_y_devuelve_las_paginas() -> None:
    conn = _conn()
    web._ocr_cache_set(conn, "h1", "texto", "llamacpp-glm-ocr", 0.85, 30, 355)
    assert web._ocr_cache_get(conn, "h1") == ("texto", "llamacpp-glm-ocr",
                                              0.85, 30, 355)


def test_cache_sin_paginas_devuelve_none(monkeypatch) -> None:
    """Un backend que no rasteriza (pypdf) o una entrada antigua: páginas
    "no consta" (None), que es la verdad, y nunca un 0/0 que parezca dato."""
    conn = _conn()
    web._ocr_cache_set(conn, "h2", "texto", "pypdf", 1.0)
    texto, backend, conf, proc, total = web._ocr_cache_get(conn, "h2")
    assert (texto, backend, conf) == ("texto", "pypdf", 1.0)
    assert (proc, total) == (None, None)


# ------------------------------- 2. aviso al servir de caché -------------
def test_el_acierto_de_cache_parcial_avisa(monkeypatch, oce_simulado) -> None:
    """La segunda vuelta (caché) también dice que faltan páginas: era el
    agujero por el que el truncado desaparecía del log."""
    conn = _conn()
    web._ocr_cache_set(conn, web._hash_pdf(b"pdf-parcial"), "texto",
                       "llamacpp-glm-ocr", 0.85, 30, 355)
    avisos: list[str] = []
    monkeypatch.setattr(web.ui, "log_warn", lambda m: avisos.append(m))

    texto, backend, conf = web._extraer_texto_pdf(b"pdf-parcial", URL,
                                                  conn=conn)

    assert backend == "llamacpp-glm-ocr_cache"
    assert oce_simulado["ocr"] == 0          # no se re-OCR-ea
    assert any("PARCIAL 30/355" in a for a in avisos)


# --------------------- 3. parcialidad en la evidencia negativa -----------
def test_la_parcialidad_se_registra_como_parcial(registro_en_tmp) -> None:
    """P3 debe poder decir "parcial: X/Y páginas", no "buscado y no
    encontrado": no es lo mismo haber leído 30 páginas que ninguna."""
    web._registrar_ocr_parcial(URL, 30, 355)
    lineas = [json.loads(l) for l in
              registro_en_tmp.read_text(encoding="utf-8").splitlines() if l]
    assert len(lineas) == 1
    assert lineas[0]["tipo"] == "documento_parcial"
    assert "parcial: 30/355" in lineas[0]["motivo"]
    assert "penas_de_muerte_conmutadas" in lineas[0]["ancla"]


def test_documento_completo_no_se_registra(registro_en_tmp) -> None:
    """Sin truncamiento no se escribe nada: el registro es para lo que FALTA,
    y llenarlo de ruido lo haría inservible."""
    web._registrar_ocr_parcial(URL, 355, 355)
    web._registrar_ocr_parcial(URL, None, None)
    assert not registro_en_tmp.exists()


def test_flujo_completo_trunca_avisa_y_registra(monkeypatch, oce_simulado,
                                                registro_en_tmp) -> None:
    """De punta a punta: PDF de 355 páginas -> se procesan 3 -> se cachea
    (3/355) -> se registra la parcialidad -> el siguiente arranque avisa."""
    conn = _conn()
    avisos: list[str] = []
    monkeypatch.setattr(web.ui, "log_warn", lambda m: avisos.append(m))

    texto, backend, conf = web._extraer_texto_pdf(b"pdf-355", URL, conn=conn)
    assert (texto, backend) == ("texto transcrito", "llamacpp-glm-ocr")
    _, _, _, proc, total = web._ocr_cache_get(conn, web._hash_pdf(b"pdf-355"))
    assert (proc, total) == (3, 355)
    assert any("OCR_MAX_PAGINAS_LOCAL" in a for a in avisos)
    lineas = registro_en_tmp.read_text(encoding="utf-8").splitlines()
    assert len(lineas) == 1 and "parcial: 3/355" in lineas[0]

    # Segunda vuelta: caché + aviso (y sin volver a llamar al OCR).
    avisos.clear()
    web._extraer_texto_pdf(b"pdf-355", URL, conn=conn)
    assert oce_simulado["ocr"] == 1
    assert any("PARCIAL 3/355" in a for a in avisos)


# ------------------------------------- 4. compatibilidad hacia atrás ------
def test_bd_antigua_sin_columnas_de_paginas_sigue_funcionando(
        monkeypatch, oce_simulado) -> None:
    """Una BD con la tabla de 4 columnas (sin migrar) no puede romper el OCR:
    se lee/escribe la forma antigua y las páginas quedan "no consta"."""
    conn = _conn(columnas=4)
    conn.execute("INSERT INTO ocr_cache VALUES (?,?,?,?)",
                 (web._hash_pdf(b"viejo"), "texto viejo", "pypdf", 1.0))
    texto, backend, conf = web._extraer_texto_pdf(b"viejo", URL, conn=conn)
    assert (texto, backend, conf) == ("texto viejo", "pypdf_cache", 1.0)

    # Y al escribir (documento nuevo) tampoco revienta.
    web._ocr_cache_set(conn, "h3", "nuevo", "llamacpp-glm-ocr", 0.85, 3, 355)
    assert conn.execute("SELECT texto FROM ocr_cache WHERE hash_pdf='h3'"
                        ).fetchone()[0] == "nuevo"


# ------------------------------------------- 5. migración del esquema ----
def test_migracion_anade_las_columnas_a_una_bd_que_ya_existe(
        tmp_path, monkeypatch) -> None:
    """get_db() migra una BD ya creada: ADD COLUMN, sin tocar los datos."""
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    (tmp_path / config.DB_PATH).unlink(missing_ok=True)
    conn = sqlite3.connect(tmp_path / config.DB_PATH)
    conn.execute("CREATE TABLE ocr_cache (hash_pdf TEXT PRIMARY KEY, "
                 "texto TEXT, backend_usado TEXT, confianza REAL)")
    conn.execute("INSERT INTO ocr_cache VALUES ('h','t','pypdf',1.0)")
    conn.commit()
    conn.close()

    conn2 = config.get_db()
    try:
        columnas = {f[1] for f in conn2.execute("PRAGMA table_info(ocr_cache)")}
        assert {"paginas_procesadas", "paginas_total"} <= columnas
        # los datos de antes siguen ahí y las columnas nuevas quedan NULL
        fila = conn2.execute("SELECT texto, paginas_procesadas "
                             "FROM ocr_cache WHERE hash_pdf='h'").fetchone()
        assert fila == ("t", None)
    finally:
        conn2.close()


# --------------------------------------------------- 6. etiqueta ---------
def test_nombre_corto_identifica_el_documento() -> None:
    """El log del 12/09 imprimía los últimos 40 caracteres de la URL y no se
    podía saber de qué PDF hablaba cada aviso."""
    assert web._nombre_corto(URL) == "penas_de_muerte_conmutadas_1940-1953.pdf"
    assert web._nombre_corto("https://a.b/x/CCEP-Web-1-PM_0.pdf?x=1") == \
        "CCEP-Web-1-PM_0.pdf"
    assert web._nombre_corto("") == "?"
