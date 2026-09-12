"""
tests/test_llamacpp_multi_v101.py — TAREA B de la v10.1: backend llamacpp
MULTI-MODELO (GLM-OCR / HunyuanOCR-1.5 / olmOCR-2 / genérico).

Batería pedida por el spec (B.6), 100% OFFLINE: el HTTP de llama-server
se simula con fakes de requests (mismo patrón que test_llamacpp_v90.py);
NINGÚN test necesita un servidor real, GPU ni red.

Cubre:
  - Prompt por familia (olmocr2 conserva el oficial; glm-ocr manda "OCR";
    hunyuan el español; OCR_LLAMACPP_PROMPT sustituye al de cualquiera).
  - Reescalado por familia (olmocr2 1288; glm-ocr/hunyuan 0; .env manda).
  - Limpieza YAML solo con olmocr2.
  - Caché coherente con la familia (legacy "llamacpp" = olmocr2).
  - Familia inválida -> fallback olmocr2 con log_error.
  - llamacpp_disponible() reporta el modelo de /v1/models y avisa (una
    vez, sin bloquear) si no parece de la familia configurada.
  - Garantías v10 intactas con la familia nueva (caído = 1 aviso + fallo
    no cacheado + cero nube) y visibilidad (cabecera de --probar-ocr,
    línea del lanzador, punto 7f del diagnóstico).

Ejecución:  python -m pytest tests/test_llamacpp_multi_v101.py -q
"""
from __future__ import annotations

import base64
import importlib
import io
import sqlite3
from pathlib import Path

import pytest
import requests

import scrapers.web as web

RAIZ = Path(__file__).resolve().parent.parent


# Resetea el estado del cliente entre tests (flags de "ya avisado").
@pytest.fixture(autouse=True)
def _reset_llamacpp():
    web._reset_llamacpp_estado()
    yield
    web._reset_llamacpp_estado()


# ============================== HELPERS =====================================

class _Respuesta:
    """Respuesta HTTP falsa (API mínima que usa el cliente)."""

    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code}")


def _fija_familia(monkeypatch, familia, prompt=""):
    """Fija la familia llamacpp activa (y su prompt custom, si hay)."""
    monkeypatch.setattr(web, "OCR_LLAMACPP_FAMILIA", familia)
    monkeypatch.setattr(web, "OCR_LLAMACPP_PROMPT", prompt)


def _stub_servidor(monkeypatch, peticiones, contenido="TEXTO DE LA PAGINA",
                   modelo_id="GLM-OCR.gguf"):
    """Stub completo de llama-server: /health, /v1/models y
    /v1/chat/completions. Registra cada POST en peticiones["posts"]."""

    def _get(url, timeout=None, **kwargs):
        if url.endswith("/health"):
            return _Respuesta(200, {"status": "ok"})
        if url.endswith("/v1/models"):
            return _Respuesta(200, {"data": [{"id": modelo_id}]})
        raise AssertionError(f"URL inesperada: {url}")

    def _post(url, json=None, timeout=None, **kwargs):
        peticiones["posts"].append(json)
        return _Respuesta(200, {"choices": [{"message": {
            "role": "assistant", "content": contenido}}]})

    monkeypatch.setattr(requests, "get", _get)
    monkeypatch.setattr(requests, "post", _post)


def _conn_memoria():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute("CREATE TABLE IF NOT EXISTS ocr_cache "
                 "(hash_pdf TEXT PRIMARY KEY, texto TEXT, "
                 " backend_usado TEXT, confianza REAL)")
    return conn


def _png(ancho: int, alto: int) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (ancho, alto), color=(255, 255, 255)).save(
        buf, format="PNG")
    return buf.getvalue()


def _tamano_imagen_payload(payload: dict) -> tuple[int, int]:
    """Decodifica la imagen (base64) del payload capturado y devuelve su
    tamaño (ancho, alto) — para verificar el reescalado REAL."""
    from PIL import Image
    url = payload["messages"][1]["content"][0]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    datos = base64.b64decode(url[len("data:image/png;base64,"):])
    return Image.open(io.BytesIO(datos)).size


def _espia_llm(llamadas: dict):
    """Espía utils.llm.llm: cualquier llamada a chat.completions.create
    cuenta (y revienta: el OCR local NO debe tocar la nube)."""
    from utils import llm as modulo_llm

    class _Completions:
        def create(self, **kwargs):
            llamadas["n"] += 1
            return None

    class _Chat:
        completions = _Completions()

    class _LlmFalso:
        chat = _Chat()

    monkeypatch_target = modulo_llm
    return monkeypatch_target, _LlmFalso()


# ======================= TABLA DE FAMILIAS (B.2) ============================

def test_tabla_familias_llamacpp_completa():
    """FAMILIAS_LLAMACPP es la tabla única de datos: las 4 familias con
    sus prompts, limpieza YAML y comandos de arranque (sin if/else
    dispersos: todo lo que cambia por familia vive aquí)."""
    assert set(web.FAMILIAS_LLAMACPP) == {
        "olmocr2", "glm-ocr", "hunyuan", "generico"}
    # Limpieza de YAML SOLO con olmocr2 (las demás no emiten front matter).
    assert web.FAMILIAS_LLAMACPP["olmocr2"]["limpia_yaml"] is True
    for familia in ("glm-ocr", "hunyuan", "generico"):
        assert web.FAMILIAS_LLAMACPP[familia]["limpia_yaml"] is False
    # El prompt de glm-ocr es el LITERAL con el que el modelo fue entrenado.
    assert web.FAMILIAS_LLAMACPP["glm-ocr"]["prompt"] == "OCR"
    # Hunyuan: prompt español de transcripción literal.
    assert web.FAMILIAS_LLAMACPP["hunyuan"]["prompt"].startswith(
        "Transcribe literalmente")
    # olmocr2 conserva el prompt OFICIAL (system) y las reglas (usuario).
    assert web.FAMILIAS_LLAMACPP["olmocr2"]["system"] == web._LLAMACPP_SYSTEM
    assert web.FAMILIAS_LLAMACPP["olmocr2"]["prompt"] == web._LLAMACPP_REGLAS
    # Cada familia sabe cómo arrancar su servidor.
    for familia, fila in web.FAMILIAS_LLAMACPP.items():
        assert fila["arranque"].startswith("llama-server")


# ======================= CONFIG (B.1) =======================================

def test_config_familia_invalida_fallback_olmocr2(monkeypatch, capsys):
    """Familia inválida en el entorno -> fallback a 'olmocr2' con
    log_error (comportamiento v10 exacto, conservador)."""
    import config
    monkeypatch.setenv("OCR_LLAMACPP_FAMILIA", "patata")
    importlib.reload(config)
    try:
        assert config.OCR_LLAMACPP_FAMILIA == "olmocr2"
        # El default de MAX_LADO sigue a la familia efectiva.
        assert config.OCR_LLAMACPP_MAX_LADO == 1288
    finally:
        monkeypatch.delenv("OCR_LLAMACPP_FAMILIA", raising=False)
        importlib.reload(config)
    salida = capsys.readouterr().out + capsys.readouterr().err
    assert "patata" in salida
    assert "olmocr2" in salida
    assert "OCR_LLAMACPP_FAMILIA" in salida


def test_config_max_lado_env_manda_sobre_default_familia(monkeypatch):
    """OCR_LLAMACPP_MAX_LADO explícito en el entorno manda sobre el
    default de familia; sin él, cada familia aplica su default."""
    import config
    monkeypatch.setenv("OCR_LLAMACPP_FAMILIA", "glm-ocr")
    monkeypatch.setenv("OCR_LLAMACPP_MAX_LADO", "777")
    importlib.reload(config)
    try:
        assert config.OCR_LLAMACPP_FAMILIA == "glm-ocr"
        assert config.OCR_LLAMACPP_MAX_LADO == 777   # .env manda
    finally:
        monkeypatch.delenv("OCR_LLAMACPP_MAX_LADO", raising=False)
        monkeypatch.delenv("OCR_LLAMACPP_FAMILIA", raising=False)
        importlib.reload(config)
    # Default POR FAMILIA (verificado con reload, sin variable a .env):
    monkeypatch.setenv("OCR_LLAMACPP_FAMILIA", "olmocr2")
    importlib.reload(config)
    try:
        assert config.OCR_LLAMACPP_MAX_LADO == 1288  # default de olmocr2
    finally:
        monkeypatch.delenv("OCR_LLAMACPP_FAMILIA", raising=False)
        importlib.reload(config)


# ======================= PROMPT POR FAMILIA (B.2/B.6) =======================

def test_prompt_olmocr2_conserva_el_oficial(monkeypatch):
    """olmocr2 = comportamiento v10.0 EXACTO: system con el prompt oficial
    (front matter YAML) y usuario con las reglas de paleógrafo."""
    _fija_familia(monkeypatch, "olmocr2")
    peticiones = {"posts": []}
    _stub_servidor(monkeypatch, peticiones,
                   contenido="---\nprimary_language: es\n---\nIsidro.")

    texto, conf = web._ocr_llamacpp([b"img"])
    assert conf == 0.85
    payload = peticiones["posts"][0]
    system = payload["messages"][0]["content"]
    user = payload["messages"][1]["content"]
    assert "front matter section on top" in system      # oficial olmOCR-2
    assert user[1]["text"] == web._LLAMACPP_REGLAS      # reglas como usuario
    assert "primary_language" not in texto              # YAML fuera


def test_prompt_glm_ocr_envia_ocr_literal(monkeypatch):
    """glm-ocr: el texto de usuario es EXACTAMENTE 'OCR' (el prompt con el
    que el modelo fue entrenado) y el system son las reglas de paleógrafo."""
    _fija_familia(monkeypatch, "glm-ocr")
    peticiones = {"posts": []}
    _stub_servidor(monkeypatch, peticiones, modelo_id="GLM-OCR.gguf")

    texto, conf = web._ocr_llamacpp([b"img"])
    assert conf == 0.85
    payload = peticiones["posts"][0]
    system = payload["messages"][0]["content"]
    user = payload["messages"][1]["content"]
    assert user[1]["text"] == "OCR"                      # literal, exacto
    assert system == web._LLAMACPP_REGLAS                # paleógrafo system
    assert "paleographer" in system


def test_prompt_hunyuan_envia_el_espanol(monkeypatch):
    """hunyuan: prompt de usuario en español (transcripción literal,
    [ilegible], nombres exactamente como aparecen escritos)."""
    _fija_familia(monkeypatch, "hunyuan")
    peticiones = {"posts": []}
    _stub_servidor(monkeypatch, peticiones, modelo_id="HunyuanOCR.gguf")

    web._ocr_llamacpp([b"img"])
    payload = peticiones["posts"][0]
    prompt = payload["messages"][1]["content"][1]["text"]
    assert prompt == web.FAMILIAS_LLAMACPP["hunyuan"]["prompt"]
    assert prompt.startswith("Transcribe literalmente")
    assert "[ilegible]" in prompt
    assert "exactamente como aparecen escritos" in prompt
    # El system sigue siendo el paleógrafo.
    assert payload["messages"][0]["content"] == web._LLAMACPP_REGLAS


def test_prompt_generico_usa_ocr_llamacpp_prompt(monkeypatch):
    """generico: OCR_LLAMACPP_PROMPT es el prompt de usuario."""
    _fija_familia(monkeypatch, "generico", prompt="Transcribe todo, sin más.")
    peticiones = {"posts": []}
    _stub_servidor(monkeypatch, peticiones, modelo_id="mi-modelo.gguf")

    web._ocr_llamacpp([b"img"])
    payload = peticiones["posts"][0]
    assert payload["messages"][1]["content"][1]["text"] == \
        "Transcribe todo, sin más."


def test_prompt_generico_sin_prompt_cae_en_hunyuan_y_avisa(monkeypatch,
                                                            capsys):
    """generico SIN OCR_LLAMACPP_PROMPT: log_warn (UNA vez) y se usa el
    prompt por defecto de hunyuan."""
    _fija_familia(monkeypatch, "generico", prompt="")
    peticiones = {"posts": []}
    _stub_servidor(monkeypatch, peticiones, modelo_id="mi-modelo.gguf")

    web._ocr_llamacpp([b"img"])
    web._ocr_llamacpp([b"img"])       # 2ª llamada: el aviso NO se repite
    payload = peticiones["posts"][0]
    assert payload["messages"][1]["content"][1]["text"] == \
        web.FAMILIAS_LLAMACPP["hunyuan"]["prompt"]
    salida = capsys.readouterr().out
    assert salida.count("generico SIN OCR_LLAMACPP_PROMPT") == 1
    assert "hunyuan" in salida


def test_prompt_custom_sustituye_a_cualquier_familia(monkeypatch):
    """OCR_LLAMACPP_PROMPT no vacío SUSTITUYE al prompt por defecto de
    CUALQUIER familia (pruebas A/B): hunyuan y olmocr2 también."""
    for familia in ("hunyuan", "olmocr2", "glm-ocr"):
        web._reset_llamacpp_estado()
        _fija_familia(monkeypatch, familia, prompt="PROMPT CUSTOM AB")
        peticiones = {"posts": []}
        _stub_servidor(monkeypatch, peticiones, modelo_id="modelo.gguf")
        web._ocr_llamacpp([b"img"])
        payload = peticiones["posts"][0]
        assert payload["messages"][1]["content"][1]["text"] == \
            "PROMPT CUSTOM AB", f"la familia {familia} no aplicó el custom"


# ======================= REESCALADO (B.2/B.6) ===============================

def test_reescalado_olmocr2_a_1288_en_payload(monkeypatch):
    """Con el límite de olmocr2 (1288), la imagen del payload llega
    reescalada al lado mayor 1288 (resolución de entrenamiento)."""
    _fija_familia(monkeypatch, "olmocr2")
    monkeypatch.setattr(web, "OCR_LLAMACPP_MAX_LADO", 1288)
    peticiones = {"posts": []}
    _stub_servidor(monkeypatch, peticiones, modelo_id="olmOCR-2.gguf")

    web._ocr_llamacpp([_png(2339, 1654)])
    ancho, alto = _tamano_imagen_payload(peticiones["posts"][0])
    assert max(ancho, alto) == 1288


def test_glm_hunyuan_sin_reescalado_por_defecto(monkeypatch):
    """glm-ocr y hunyuan NO reescalan por defecto (MAX_LADO 0: el
    projector del llama-server gestiona la resolución)."""
    for familia, modelo in (("glm-ocr", "GLM-OCR.gguf"),
                            ("hunyuan", "HunyuanOCR.gguf")):
        web._reset_llamacpp_estado()
        _fija_familia(monkeypatch, familia)
        monkeypatch.setattr(web, "OCR_LLAMACPP_MAX_LADO", 0)
        peticiones = {"posts": []}
        _stub_servidor(monkeypatch, peticiones, modelo_id=modelo)
        web._ocr_llamacpp([_png(2339, 1654)])
        ancho, alto = _tamano_imagen_payload(peticiones["posts"][0])
        assert (ancho, alto) == (2339, 1654), \
            f"{familia} reescaló sin permiso"


def test_max_lado_explicito_manda_sobre_familia(monkeypatch):
    """Un MAX_LADO explícito (como el de .env) manda sobre el default de
    familia: con glm-ocr y límite 700, la imagen se reescala a 700."""
    _fija_familia(monkeypatch, "glm-ocr")
    monkeypatch.setattr(web, "OCR_LLAMACPP_MAX_LADO", 700)
    peticiones = {"posts": []}
    _stub_servidor(monkeypatch, peticiones, modelo_id="GLM-OCR.gguf")

    web._ocr_llamacpp([_png(2339, 1654)])
    ancho, alto = _tamano_imagen_payload(peticiones["posts"][0])
    assert max(ancho, alto) == 700


# ======================= LIMPIEZA YAML (B.2/B.6) ============================

def test_yaml_se_limpia_solo_con_olmocr2(monkeypatch):
    """Un texto que empieza con '---' (front matter): con olmocr2 se
    descarta; con glm-ocr NO se toca (no es salida de olmOCR-2)."""
    contenido = "---\nprimary_language: es\nis_table: False\n---\nCONTENIDO"
    # olmocr2: limpieza activada.
    _fija_familia(monkeypatch, "olmocr2")
    monkeypatch.setattr(web, "OCR_LLAMACPP_MAX_LADO", 1288)
    peticiones = {"posts": []}
    _stub_servidor(monkeypatch, peticiones, contenido=contenido,
                   modelo_id="olmOCR-2.gguf")
    texto, _ = web._ocr_llamacpp([b"img"])
    assert texto == "CONTENIDO"
    assert "primary_language" not in texto
    # glm-ocr: sin limpieza, el '---' inicial NO se toca.
    web._reset_llamacpp_estado()
    _fija_familia(monkeypatch, "glm-ocr")
    monkeypatch.setattr(web, "OCR_LLAMACPP_MAX_LADO", 0)
    peticiones2 = {"posts": []}
    _stub_servidor(monkeypatch, peticiones2, contenido=contenido,
                   modelo_id="GLM-OCR.gguf")
    texto2, _ = web._ocr_llamacpp([b"img"])
    assert texto2.startswith("---")
    assert "primary_language" in texto2
    assert "CONTENIDO" in texto2


# ======================= CACHÉ POR FAMILIA (B.3/B.6) ========================

def test_familia_de_backend_cacheado():
    """Unit: el backend cacheado codifica la familia; los legacy v10.0
    ('llamacpp'/'llamacpp_manuscrito' sin familia) cuentan como olmocr2."""
    assert web._familia_de_backend_cacheado("llamacpp") == "olmocr2"
    assert web._familia_de_backend_cacheado("llamacpp_manuscrito") == \
        "olmocr2"
    assert web._familia_de_backend_cacheado("llamacpp-glm-ocr") == "glm-ocr"
    assert web._familia_de_backend_cacheado("llamacpp-hunyuan") == "hunyuan"
    assert web._familia_de_backend_cacheado(
        "llamacpp_manuscrito-glm-ocr") == "glm-ocr"
    assert web._familia_de_backend_cacheado("llamacpp_manuscrito-olmocr2") \
        == "olmocr2"
    assert web._familia_de_backend_cacheado("pypdf") is None
    assert web._familia_de_backend_cacheado("ocr_local") is None
    assert web._familia_de_backend_cacheado("ocr_local_fallido") is None


def test_cache_escrito_con_familia(monkeypatch):
    """El éxito de la cascada se cachea con backend 'llamacpp-<familia>'
    (y manuscrito con 'llamacpp_manuscrito-<familia>')."""
    monkeypatch.setattr(web, "OCR_BACKEND", "llamacpp")
    _fija_familia(monkeypatch, "glm-ocr")
    monkeypatch.setattr(web, "_pdf_a_imagenes",
                        lambda b, m: ([b"img"], 1, 1))
    monkeypatch.setattr(web, "_ocr_llamacpp",
                        lambda imgs: ("texto glm", 0.85))
    conn = _conn_memoria()
    _, backend, _ = web._extraer_texto_pdf(
        b"pdf-glm", "https://a.org/x.pdf", conn=conn)
    assert backend == "llamacpp-glm-ocr"
    fila = conn.execute("SELECT backend_usado FROM ocr_cache").fetchone()
    assert fila[0] == "llamacpp-glm-ocr"
    # Manuscrito (PARES) con la misma familia.
    conn2 = _conn_memoria()
    _, backend_m, _ = web._extraer_texto_pdf(
        b"pdf-glm-m", "https://pares.cultura.gob.es/x.pdf", conn=conn2)
    assert backend_m == "llamacpp_manuscrito-glm-ocr"
    fila_m = conn2.execute("SELECT backend_usado FROM ocr_cache").fetchone()
    assert fila_m[0] == "llamacpp_manuscrito-glm-ocr"


def test_cache_otra_familia_no_se_reutiliza(monkeypatch):
    """Al cambiar OCR_LLAMACPP_FAMILIA, la entrada antigua NO se reutiliza:
    se re-procesa (el prompt/modelo que la produjo era distinto) y el
    INSERT OR REPLACE la refresca."""
    monkeypatch.setattr(web, "OCR_BACKEND", "llamacpp")
    monkeypatch.setattr(web, "_pdf_a_imagenes",
                        lambda b, m: ([b"img"], 1, 1))
    llamadas = {"n": 0}

    def _ocr(imgs):
        llamadas["n"] += 1
        return "texto re-inferido", 0.85

    monkeypatch.setattr(web, "_ocr_llamacpp", _ocr)
    conn = _conn_memoria()

    _fija_familia(monkeypatch, "glm-ocr")
    _, backend_1, _ = web._extraer_texto_pdf(
        b"pdf-ab", "https://a.org/x.pdf", conn=conn)
    assert backend_1 == "llamacpp-glm-ocr"
    assert llamadas["n"] == 1

    _fija_familia(monkeypatch, "hunyuan")
    _, backend_2, _ = web._extraer_texto_pdf(
        b"pdf-ab", "https://a.org/x.pdf", conn=conn)
    assert backend_2 == "llamacpp-hunyuan"     # NO salió de la caché
    assert llamadas["n"] == 2                   # re-infiere de verdad
    fila = conn.execute("SELECT backend_usado FROM ocr_cache").fetchone()
    assert fila[0] == "llamacpp-hunyuan"        # la entrada se refrescó


def test_cache_legacy_llamacpp_reutilizado_solo_con_olmocr2(monkeypatch):
    """Compatibilidad v10.0: la entrada legacy 'llamacpp' se reutiliza con
    familia olmocr2; con glm-ocr NO (se re-procesa)."""
    monkeypatch.setattr(web, "OCR_BACKEND", "llamacpp")
    monkeypatch.setattr(web, "_pdf_a_imagenes",
                        lambda b, m: ([b"img"], 1, 1))
    llamadas = {"n": 0}

    def _ocr(imgs):
        llamadas["n"] += 1
        return "texto nuevo", 0.85

    monkeypatch.setattr(web, "_ocr_llamacpp", _ocr)

    def _conn_con_legacy():
        conn = _conn_memoria()
        conn.execute(
            "INSERT INTO ocr_cache VALUES (?,?,?,?)",
            (web._hash_pdf(b"pdf-legacy"), "texto legado",
             "llamacpp", 0.85))
        return conn

    # Con olmocr2: la entrada legacy se REUTILIZA (misma familia efectiva).
    _fija_familia(monkeypatch, "olmocr2")
    conn = _conn_con_legacy()
    texto, backend, _ = web._extraer_texto_pdf(
        b"pdf-legacy", "https://a.org/x.pdf", conn=conn)
    assert backend == "llamacpp_cache"
    assert texto == "texto legado"
    assert llamadas["n"] == 0

    # Con glm-ocr: la entrada legacy NO se reutiliza.
    _fija_familia(monkeypatch, "glm-ocr")
    conn2 = _conn_con_legacy()
    texto2, backend2, _ = web._extraer_texto_pdf(
        b"pdf-legacy", "https://a.org/x.pdf", conn=conn2)
    assert backend2 == "llamacpp-glm-ocr"
    assert texto2 == "texto nuevo"
    assert llamadas["n"] == 1


# ============== VISIBILIDAD: MODELO SERVIDO / MISMATCH (B.4/B.6) ============

def test_disponible_reporta_modelo_del_v1_models(monkeypatch, capsys):
    """llamacpp_disponible() devuelve el modelo del /v1/models stubeado Y
    lo deja en el log (junto a la familia activa)."""
    _fija_familia(monkeypatch, "glm-ocr")

    def _get(url, timeout=None, **kwargs):
        if url.endswith("/health"):
            return _Respuesta(200, {"status": "ok"})
        return _Respuesta(200, {"data": [{"id": "GLM-OCR.gguf"}]})

    monkeypatch.setattr(requests, "get", _get)
    ok, detalle = web.llamacpp_disponible()
    assert ok
    assert detalle == "GLM-OCR.gguf"
    salida = capsys.readouterr().out
    assert "sirve el modelo" in salida
    assert "GLM-OCR.gguf" in salida
    assert "glm-ocr" in salida


def test_disponible_avisa_modelo_no_coincide_una_vez(monkeypatch, capsys):
    """Modelo que no parece de la familia configurada -> log_warn
    INFORMATIVO exactamente UNA vez y NO bloqueante (sigue (True, ...))."""
    _fija_familia(monkeypatch, "glm-ocr")

    def _get(url, timeout=None, **kwargs):
        if url.endswith("/health"):
            return _Respuesta(200, {"status": "ok"})
        return _Respuesta(200, {"data": [{"id": "olmOCR-2-7B.gguf"}]})

    monkeypatch.setattr(requests, "get", _get)
    ok, detalle = web.llamacpp_disponible()
    assert ok is True                       # NO bloqueante
    assert detalle == "olmOCR-2-7B.gguf"
    ok2, _ = web.llamacpp_disponible()
    assert ok2 is True
    salida = capsys.readouterr().out
    assert salida.count("NO parece un modelo de la familia") == 1
    assert "glm-ocr" in salida


def test_aviso_caido_comando_por_familia(monkeypatch, capsys):
    """El aviso de 'servidor caído' trae el comando de arranque de la
    FAMILIA activa: -hf ggml-org/GLM-OCR-GGUF (glm-ocr) y -hf
    ggml-org/HunyuanOCR-GGUF (hunyuan); siempre con 1 solo aviso."""
    for familia, comando in (("glm-ocr", "ggml-org/GLM-OCR-GGUF"),
                             ("hunyuan", "ggml-org/HunyuanOCR-GGUF")):
        web._reset_llamacpp_estado()
        capsys.readouterr()
        _fija_familia(monkeypatch, familia)
        web._aviso_llamacpp_caido("refused")
        web._aviso_llamacpp_caido("refused de nuevo")   # no repite aviso
        salida = capsys.readouterr().out
        assert salida.count("llama-server no responde") == 1
        assert comando in salida
        assert familia in salida


# ================== GARANTÍAS v10 CON LA FAMILIA NUEVA (B.6) ================

def test_cascada_glm_ocr_caido_un_aviso_cero_nube_sin_cache(monkeypatch,
                                                             capsys):
    """Garantías v10 intactas con la familia por defecto glm-ocr: servidor
    caído -> fallo EXPLÍCITO 'ocr_local_fallido', CERO llamadas a la nube
    (espía utils.llm.llm), GASTO intacto y NADA cacheado (transitorio)."""
    from utils import llm as modulo_llm
    from utils.llm import GASTO

    _fija_familia(monkeypatch, "glm-ocr")
    monkeypatch.setattr(web, "OCR_BACKEND", "llamacpp")
    monkeypatch.setattr(web, "_pdf_a_imagenes",
                        lambda b, m: ([b"img"], 1, 1))
    monkeypatch.setattr(web, "_ocr_llamacpp", lambda imgs: ("", 0.0))
    monkeypatch.setattr(web, "_llamacpp_servidor_caido", lambda: True)
    monkeypatch.setattr(
        requests, "get",
        lambda url, timeout=None, **k: (_ for _ in ()).throw(
            requests.exceptions.ConnectionError("refused")))

    llamadas = {"n": 0}
    modulo_llm, espia = _espia_llm(llamadas)
    monkeypatch.setattr(modulo_llm, "llm", espia)
    gasto_antes = GASTO.coste
    conn = _conn_memoria()

    texto, backend, _ = web._extraer_texto_pdf(
        b"pdf-glm-caido", "https://ejemplo.org/registro.pdf", conn=conn)

    assert texto == ""
    assert backend == "ocr_local_fallido"
    assert llamadas["n"] == 0                  # cero llamadas a la nube
    assert GASTO.coste == gasto_antes          # gasto intacto
    filas = conn.execute("SELECT COUNT(*) FROM ocr_cache").fetchone()[0]
    assert filas == 0                          # transitorio: sin cachear


# ================== VISIBILIDAD: PROBAR-OCR / LANZADOR / 7f (B.4/B.6) =======

def test_probar_ocr_cabecera_familia_prompt_y_modelo(monkeypatch, tmp_path,
                                                      capsys):
    """--probar-ocr (OCR_BACKEND=llamacpp): la cabecera muestra la familia
    activa, el prompt EXACTO que se va a enviar y el modelo que el
    servidor dice servir."""
    import main
    import config
    from tests.test_probar_ocr_v90 import _pdf_con_texto

    monkeypatch.setattr(config, "BASE_DIR", tmp_path)   # BD a tmp_path
    monkeypatch.setattr(web, "OCR_BACKEND", "llamacpp")
    _fija_familia(monkeypatch, "glm-ocr")

    def _get(url, timeout=None, **kwargs):
        if url.endswith("/health"):
            return _Respuesta(200, {"status": "ok"})
        return _Respuesta(200, {"data": [{"id": "GLM-OCR.gguf"}]})

    monkeypatch.setattr(requests, "get", _get)

    pdf = tmp_path / "acta.pdf"
    pdf.write_bytes(_pdf_con_texto("Partida de bautismo " + "datos " * 30))

    rc = main._probar_ocr(str(pdf), manuscrito=False, sin_cache=False)
    salida = capsys.readouterr().out + capsys.readouterr().err

    assert rc == 0                       # pypdf gana: PDF con capa de texto
    assert "Familia      : glm-ocr" in salida
    assert "Prompt (usuario, EXACTO): OCR" in salida
    assert "modelo servido: GLM-OCR.gguf" in salida
    assert "OCR 100% local" in salida


def test_lanzador_linea_llama_muestra_familia(monkeypatch):
    """lanzador._linea_llama(): el estado del servidor va acompañado de la
    familia activa (v10.1), con servidor vivo y con servidor caído."""
    import config
    import lanzador

    monkeypatch.setattr(config, "OCR_BACKEND", "llamacpp")
    monkeypatch.setattr(config, "OCR_LLAMACPP_FAMILIA", "glm-ocr")

    def _get(url, timeout=None, **kwargs):
        if url.endswith("/health"):
            return _Respuesta(200, {"status": "ok"})
        return _Respuesta(200, {"data": [{"id": "GLM-OCR.gguf"}]})

    monkeypatch.setattr(requests, "get", _get)
    linea = lanzador._linea_llama()
    assert "llama-server ✓" in linea
    assert "GLM-OCR.gguf" in linea
    assert "familia glm-ocr" in linea

    monkeypatch.setattr(
        requests, "get",
        lambda url, timeout=None, **k: (_ for _ in ()).throw(
            requests.exceptions.ConnectionError("refused")))
    linea_caida = lanzador._linea_llama()
    assert "llama-server ✗" in linea_caida
    assert "familia glm-ocr" in linea_caida


def test_gedcom_diagnostico_muestra_familia():
    """agent/gedcom.py (punto 7/7f del diagnóstico): el fuente consulta
    OCR_LLAMACPP_FAMILIA y la muestra junto al estado del llama-server
    ("familia configurada: ...")."""
    fuente = (RAIZ / "agent" / "gedcom.py").read_text(encoding="utf-8")
    assert "OCR_LLAMACPP_FAMILIA" in fuente
    assert "familia configurada" in fuente
