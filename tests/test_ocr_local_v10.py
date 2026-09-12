"""
tests/test_ocr_local_v10.py — TAREA 1 de la v10.0: OCR 100% LOCAL.

Verifica que Gemini/nube ha salido de TODO el OCR y la transcripción de
imágenes: cero llamadas a chat.completions.create (espía sobre
utils.llm.llm) en cada fallo local, gasto intacto, fallos transitorios
SIN cachear, fuentes sin los símbolos eliminados y .gitignore presente.

Todos los tests son OFFLINE: motores locales stubeados y PDFs mínimos
como en test_probar_ocr_v90.py.

Ejecución:  python -m pytest tests/test_ocr_local_v10.py -q
"""
from __future__ import annotations

import io
import inspect
import sqlite3
from pathlib import Path

import pytest
import requests

import main
import scrapers.web as web
from utils import llm as modulo_llm
from utils.llm import GASTO

RAIZ = Path(__file__).resolve().parent.parent


# Resetea el estado del cliente llamacpp entre tests (flags de "ya avisado").
@pytest.fixture(autouse=True)
def _reset_llamacpp():
    web._reset_llamacpp_estado()
    yield
    web._reset_llamacpp_estado()


# ============================== HELPERS =====================================

def _espia_llm(llamadas: dict):
    """Espía utils.llm.llm: cualquier llamada a chat.completions.create
    cuenta (y revienta: el OCR local NO debe tocar la nube)."""

    class _Completions:
        def create(self, **kwargs):
            llamadas["n"] += 1
            return None

    class _Chat:
        completions = _Completions()

    class _LlmFalso:
        chat = _Chat()

    return _LlmFalso()


def _conn_memoria():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute("CREATE TABLE IF NOT EXISTS ocr_cache "
                 "(hash_pdf TEXT PRIMARY KEY, texto TEXT, "
                 " backend_usado TEXT, confianza REAL)")
    return conn


def _servidor_caido(monkeypatch):
    """Stub del entorno 'llama-server caído': chequeo de conexión rechazado
    y el cliente marcado como caído (fracaso transitorio)."""
    monkeypatch.setattr(web, "OCR_BACKEND", "llamacpp")
    monkeypatch.setattr(web, "_pdf_a_imagenes",
                        lambda b, m: ([b"img"], 1, 1))
    monkeypatch.setattr(web, "_ocr_llamacpp", lambda imgs: ("", 0.0))
    monkeypatch.setattr(web, "_llamacpp_servidor_caido", lambda: True)
    monkeypatch.setattr(
        requests, "get",
        lambda url, timeout=None, **k: (_ for _ in ()).throw(
            requests.exceptions.ConnectionError("refused")))


# ============================== TESTS =======================================

def test_a_web_sin_gemini_ni_chat_vision():
    """a) scrapers.web no define _ocr_gemini ni _escalar_a_gemini, y no
    importa chat_vision (ni en el objeto módulo ni en el fuente)."""
    assert not hasattr(web, "_ocr_gemini")
    assert not hasattr(web, "_escalar_a_gemini")
    fuente = Path(web.__file__).read_text(encoding="utf-8")
    assert "chat_vision" not in fuente
    assert "def _ocr_gemini" not in fuente
    assert "def _escalar_a_gemini" not in fuente


def test_b_manuscrito_ocr_local_fallando_sin_nube(monkeypatch):
    """b) Manuscrito con OCR local fallando (llama-server caído): 0
    llamadas a chat.completions.create, backend sin 'gemini'/'vlm' y
    GASTO intacto."""
    _servidor_caido(monkeypatch)
    llamadas = {"n": 0}
    monkeypatch.setattr(modulo_llm, "llm", _espia_llm(llamadas))
    gasto_antes = GASTO.coste

    texto, backend, conf = web._extraer_texto_pdf(
        b"pdf-falso", "https://pares.cultura.gob.es/partida.pdf", conn=None)

    assert llamadas["n"] == 0                      # 0 llamadas a la nube
    assert "gemini" not in backend and "vlm" not in backend
    assert backend == "ocr_local_fallido"          # fallo explícito
    assert texto == ""
    assert GASTO.coste == gasto_antes              # gasto intacto


def test_c_impreso_confianza_baja_sin_escalada(monkeypatch):
    """c) Impreso con confianza local baja (stub 0.2): 0 llamadas a la
    nube — el texto local se conserva como 'ocr_local_low' (v10.0: ya no
    hay escalada por confianza baja)."""
    monkeypatch.setattr(web, "OCR_BACKEND", "rapidocr")
    monkeypatch.setattr(web, "_pdf_a_imagenes",
                        lambda b, m: ([b"img"], 1, 1))
    # Motor local "instalado" pero de baja confianza.
    monkeypatch.setattr(web, "_init_ocr_local", lambda: object())
    monkeypatch.setattr(web, "_ocr_local",
                        lambda imgs: ("texto poco fiable", 0.2))
    llamadas = {"n": 0}
    monkeypatch.setattr(modulo_llm, "llm", _espia_llm(llamadas))
    gasto_antes = GASTO.coste

    texto, backend, conf = web._extraer_texto_pdf(
        b"pdf-falso", "https://ejemplo.org/libro.pdf", conn=None)

    assert llamadas["n"] == 0                      # 0 llamadas a la nube
    assert backend == "ocr_local_low"              # se conserva lo local
    assert texto == "texto poco fiable"
    assert conf == 0.2
    assert GASTO.coste == gasto_antes


def test_d_llamacpp_caido_fallo_no_cacheado(monkeypatch):
    """d) OCR_BACKEND=llamacpp con servidor caído: 0 llamadas a la nube y
    el fallo NO queda cacheado en ocr_cache (transitorio: se reintenta)."""
    _servidor_caido(monkeypatch)
    llamadas = {"n": 0}
    monkeypatch.setattr(modulo_llm, "llm", _espia_llm(llamadas))
    conn = _conn_memoria()

    texto, backend, _ = web._extraer_texto_pdf(
        b"pdf-impreso", "https://ejemplo.org/registro.pdf", conn=conn)

    assert llamadas["n"] == 0
    assert backend == "ocr_local_fallido"
    filas = conn.execute("SELECT COUNT(*) FROM ocr_cache").fetchone()[0]
    assert filas == 0                               # NADA cacheado


def test_e_gedcom_transcribir_imagen_local(monkeypatch, tmp_path):
    """e) agent/gedcom.py no referencia transcribir_imagen_llm, y
    transcribir_imagen() sobre una imagen de pega: 0 llamadas a la nube y
    no lanza excepción (sin motor local devuelve '')."""
    fuente_gedcom = (RAIZ / "agent" / "gedcom.py").read_text(encoding="utf-8")
    assert "transcribir_imagen_llm" not in fuente_gedcom

    from PIL import Image
    img = tmp_path / "certificado_de_pega.png"
    Image.new("RGB", (40, 20), color=(255, 255, 255)).save(
        img, format="PNG")

    # Sin motor local disponible (rapidocr no instalado / caído).
    monkeypatch.setattr(web, "OCR_BACKEND", "rapidocr")
    monkeypatch.setattr(web, "_ocr_local", lambda imgs: ("", 0.0))
    llamadas = {"n": 0}
    monkeypatch.setattr(modulo_llm, "llm", _espia_llm(llamadas))

    import agent.gedcom as gedcom
    # No debe lanzar excepción: devuelva texto o cadena vacía.
    texto = gedcom.transcribir_imagen(img)
    assert isinstance(texto, str)
    assert llamadas["n"] == 0                       # 0 llamadas a la nube


def test_f_main_sin_con_gemini():
    """f) main._probar_ocr no tiene parámetro con_gemini y el fuente de
    main.py no contiene '--con-gemini' ni 'GEMINI OMITIDO'."""
    parametros = inspect.signature(main._probar_ocr).parameters
    assert "con_gemini" not in parametros
    assert list(parametros) == ["ruta", "manuscrito", "sin_cache"]
    fuente = (RAIZ / "main.py").read_text(encoding="utf-8")
    assert "--con-gemini" not in fuente
    assert "GEMINI OMITIDO" not in fuente


def test_g_fuentes_sin_solo_vision():
    """g) Los fuentes de config.py, scrapers/web.py y .env.example no
    contienen 'solo_vision' (el modo desaparece con el VLM)."""
    for ruta in (RAIZ / "config.py", RAIZ / "scrapers" / "web.py",
                 RAIZ / ".env.example"):
        fuente = ruta.read_text(encoding="utf-8")
        assert "solo_vision" not in fuente, f"{ruta} aún menciona solo_vision"


def test_h_gitignore_presente():
    """h) Existe .gitignore en la raíz y cubre los secretos y datos
    familiares: .env, cache_agente.db y familia_conocida.json."""
    gitignore = RAIZ / ".gitignore"
    assert gitignore.exists()
    contenido = gitignore.read_text(encoding="utf-8")
    for entrada in (".env", "cache_agente.db", "familia_conocida.json"):
        assert entrada in contenido, f".gitignore sin {entrada}"


def test_extra_manuscrito_con_ocr_clasico_no_se_intenta(monkeypatch):
    """(extra, spec TAREA 1 'manuscritos: olmOCR-2 local o fallo
    explícito'): manuscrito con OCR_BACKEND=rapidocr -> fallo explícito
    SIN ejecutar el OCR clásico de impreso (no alucina) ni llamar a la
    nube; sin cachear (es un problema de configuración, reversible)."""
    monkeypatch.setattr(web, "OCR_BACKEND", "rapidocr")
    ejecutado = {"ocr_local": 0}
    monkeypatch.setattr(web, "_pdf_a_imagenes",
                        lambda b, m: ([b"img"], 1, 1))
    monkeypatch.setattr(
        web, "_ocr_local",
        lambda imgs: (ejecutado.__setitem__("ocr_local", 1), ("", 0.0))[1])
    llamadas = {"n": 0}
    monkeypatch.setattr(modulo_llm, "llm", _espia_llm(llamadas))
    conn = _conn_memoria()

    texto, backend, _ = web._extraer_texto_pdf(
        b"pdf-m", "https://pares.cultura.gob.es/libro.pdf", conn=conn)

    assert backend == "ocr_local_fallido"
    assert ejecutado["ocr_local"] == 0        # el OCR de impreso NO se ejecutó
    assert llamadas["n"] == 0
    filas = conn.execute("SELECT COUNT(*) FROM ocr_cache").fetchone()[0]
    assert filas == 0                         # sin cachear (reversible)
