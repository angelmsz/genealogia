"""
tests/test_llamacpp_v90.py — Backend de OCR local con llama.cpp (v9.0,
PARTE A; actualizado a la v10.1 multi-modelo).

Estos tests son de LÓGICA: simulan el HTTP de llama-server con fakes de
requests (no necesitan servidor ni GPU). La prueba end-to-end contra un
servidor HTTP REAL (mock que imita /health, /v1/models y
/v1/chat/completions) está en scripts/probe_llamacpp_mock.py y se ejecuta
aparte; esta suite garantiza que la lógica no se rompa en el futuro.

v10.1: el default de familia pasa a ser glm-ocr; los tests que fijan el
comportamiento EXACTO de la v10.0 (prompt oficial + reescalado 1288 +
limpieza YAML + aviso con --mmproj) ahora fijan EXPLÍCITAMENTE la familia
"olmocr2" para seguir afirmando lo mismo. La bateria completa por
familia está en tests/test_llamacpp_multi_v101.py.

Ejecución:  python -m pytest tests/test_llamacpp_v90.py -q
"""
from __future__ import annotations

import json
import sqlite3

import pytest
import requests

import scrapers.web as web


def _fija_familia(monkeypatch, familia="olmocr2", prompt=""):
    """Fija la familia llamacpp activa (y sin prompt custom) para el test:
    así cada test declara la familia cuyo comportamiento afirma."""
    monkeypatch.setattr(web, "OCR_LLAMACPP_FAMILIA", familia)
    monkeypatch.setattr(web, "OCR_LLAMACPP_PROMPT", prompt)


# Resetea el estado del cliente entre tests (flags de "ya avisado").
@pytest.fixture(autouse=True)
def _reset_llamacpp():
    web._reset_llamacpp_estado()
    yield
    web._reset_llamacpp_estado()


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


# ============================== CONFIG ======================================

def test_config_llamacpp_por_defecto():
    """Las variables de config.py existen con los defaults pedidos (v10.1:
    familia por defecto glm-ocr y MAX_LADO por familia).

    v10.2: los valores que el .env puede overridear (URL, timeouts,
    familia, prompt, reescalado) se comprueban SOLO si el .env real no
    los pisa — el test anterior reventaba en cualquier instalación con
    OCR_LLAMACPP_TIMEOUT_INFERENCIA=600 en el .env (como la del
    usuario), que es una configuración legítima, no un bug."""
    import os
    import config
    if "OCR_LLAMACPP_URL" not in os.environ:
        assert config.OCR_LLAMACPP_URL == "http://localhost:8080"
    if "OCR_LLAMACPP_TIMEOUT_CONEXION" not in os.environ:
        assert config.OCR_LLAMACPP_TIMEOUT_CONEXION == 5.0
    if "OCR_LLAMACPP_TIMEOUT_INFERENCIA" not in os.environ:
        assert config.OCR_LLAMACPP_TIMEOUT_INFERENCIA == 60.0
    # v10.1: default de familia glm-ocr -> sin reescalado (0).
    if "OCR_LLAMACPP_FAMILIA" not in os.environ:
        assert config.OCR_LLAMACPP_FAMILIA == "glm-ocr"
    if "OCR_LLAMACPP_PROMPT" not in os.environ:
        assert config.OCR_LLAMACPP_PROMPT == ""
    if "OCR_LLAMACPP_MAX_LADO" not in os.environ:
        assert config.OCR_LLAMACPP_MAX_LADO == 0
    assert config.OCR_LLAMACPP_MAX_LADO_POR_FAMILIA == {
        "glm-ocr": 0, "hunyuan": 0, "generico": 0, "olmocr2": 1288}
    # OCR_BACKEND documenta el nuevo valor sin romper los existentes
    assert config.OCR_BACKEND in ("rapidocr", "paddleocr", "llamacpp", "off")
    # v10.2: con override, el valor configurado es un float válido > 0.
    assert config.OCR_LLAMACPP_TIMEOUT_INFERENCIA > 0
    assert config.OCR_LLAMACPP_TIMEOUT_CONEXION > 0


def test_init_ocr_local_ignora_llamacpp(monkeypatch):
    """Con OCR_BACKEND=llamacpp no se intenta cargar RapidOCR/Paddle: el
    motor es un servidor EXTERNO (evita inicializaciones inútiles y
    warnings engañosos del diagnóstico)."""
    monkeypatch.setattr(web, "OCR_BACKEND", "llamacpp")
    web._OCR_INIT_INTENTADO = False
    web._OCR_LOCAL = None
    try:
        assert web._init_ocr_local() is None
    finally:
        web._OCR_INIT_INTENTADO = False
        web._OCR_LOCAL = None


# ============================== CLIENTE HTTP ================================

def test_llamacpp_disponible_ok(monkeypatch):
    """/health 200 + /v1/models con un modelo -> (True, id)."""
    _fija_familia(monkeypatch, "olmocr2")

    def _get(url, timeout=None, **kwargs):
        if url.endswith("/health"):
            return _Respuesta(200, {"status": "ok"})
        if url.endswith("/v1/models"):
            return _Respuesta(200, {"data": [{"id": "olmOCR-2-Q4_K_M.gguf"}]})
        raise AssertionError(f"URL inesperada: {url}")

    monkeypatch.setattr(requests, "get", _get)
    ok, detalle = web.llamacpp_disponible()
    assert ok
    assert "olmOCR-2-Q4_K_M.gguf" == detalle


def test_llamacpp_disponible_caido(monkeypatch):
    """Connection refused -> (False, motivo) SIN excepción."""

    def _get(url, timeout=None, **kwargs):
        raise requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(requests, "get", _get)
    ok, motivo = web.llamacpp_disponible(timeout=0.5)
    assert not ok
    assert "refused" in motivo


def test_llamacpp_disponible_health_error(monkeypatch):
    """/health devuelve 500 -> caído con el código en el motivo."""

    def _get(url, timeout=None, **kwargs):
        return _Respuesta(500, {})

    monkeypatch.setattr(requests, "get", _get)
    ok, motivo = web.llamacpp_disponible()
    assert not ok
    assert "500" in motivo


def test_llamacpp_disponible_sin_models(monkeypatch):
    """Servidor vivo pero /v1/models roto: sigue OK con 'desconocido'."""

    def _get(url, timeout=None, **kwargs):
        if url.endswith("/health"):
            return _Respuesta(200, {"status": "ok"})
        raise requests.exceptions.ConnectionError("models caído")

    monkeypatch.setattr(requests, "get", _get)
    ok, detalle = web.llamacpp_disponible()
    assert ok
    assert detalle == "desconocido"


def test_ocr_llamacpp_exito_y_payload(monkeypatch):
    """Respuesta válida -> (texto sin front matter, 0.85) y el payload
    manda el prompt oficial + las reglas de paleógrafo + 1 imagen.
    (Familia olmocr2: comportamiento v10.0 EXACTO, sin cambios.)"""
    _fija_familia(monkeypatch, "olmocr2")
    peticiones = {"posts": []}

    def _get(url, timeout=None, **kwargs):
        if url.endswith("/health"):
            return _Respuesta(200, {"status": "ok"})
        return _Respuesta(200, {"data": [{"id": "olmOCR-2-7B.gguf"}]})

    def _post(url, json=None, timeout=None, **kwargs):
        peticiones["posts"].append(json)
        contenido = ("---\nprimary_language: es\nis_rotation_valid: True\n"
                     "rotation_correction: 0\nis_table: False\n"
                     "is_diagram: False\n---\n"
                     "Isidro Merillas, hijo de Nazario y Petrona. "
                     "[ilegible] padrinos.")
        return _Respuesta(200, {
            "choices": [{"message": {"role": "assistant",
                                     "content": contenido}}]})

    monkeypatch.setattr(requests, "get", _get)
    monkeypatch.setattr(requests, "post", _post)

    texto, conf = web._ocr_llamacpp([b"imagen-fake-1", b"imagen-fake-2"])
    assert conf == 0.85                      # confianza fija conservadora
    assert "Isidro Merillas" in texto
    assert "primary_language" not in texto   # front matter fuera
    assert "cambio de página" in texto       # 2 páginas concatenadas
    # Payload: una petición por imagen, prompt oficial + reglas.
    assert len(peticiones["posts"]) == 2
    payload = peticiones["posts"][0]
    assert payload["model"] == "olmOCR-2-7B.gguf"
    assert payload["temperature"] == 0.0
    system = payload["messages"][0]["content"]
    assert "front matter section on top" in system       # oficial olmOCR-2
    user = payload["messages"][1]["content"]
    tipos = [parte["type"] for parte in user]
    assert tipos == ["image_url", "text"]
    assert "data:image/png;base64," in user[0]["image_url"]["url"]
    assert "[ilegible]" in user[1]["text"]               # reglas paleógrafo
    assert "EXACTLY as written" in user[1]["text"]


def test_ocr_llamacpp_servidor_caido_un_aviso(monkeypatch, capsys):
    """Connection refused en el chequeo: ("", 0.0), warn EXACTAMENTE una
    vez aunque se llame dos veces, y la segunda llamada no toca la red.
    (Familia olmocr2: el aviso trae el comando manual GGUF+mmproj.)"""
    _fija_familia(monkeypatch, "olmocr2")
    intentos = {"n": 0}

    def _get(url, timeout=None, **kwargs):
        intentos["n"] += 1
        raise requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(requests, "get", _get)
    assert web._ocr_llamacpp([b"img"]) == ("", 0.0)
    n_tras_1 = intentos["n"]
    assert web._ocr_llamacpp([b"img"]) == ("", 0.0)
    assert intentos["n"] == n_tras_1          # no volvió a llamar
    salida = capsys.readouterr().out
    assert salida.count("llama-server no responde") == 1
    # El aviso trae el comando para arrancar el servidor.
    assert "--mmproj" in salida
    assert web._llamacpp_servidor_caido() is True


def test_ocr_llamacpp_paginas_parciales(monkeypatch):
    """Si una página falla (timeout de inferencia) pero otra responde, se
    conserva el texto de la buena (resiliencia por página)."""

    def _get(url, timeout=None, **kwargs):
        if url.endswith("/health"):
            return _Respuesta(200, {"status": "ok"})
        return _Respuesta(200, {"data": [{"id": "olmOCR.gguf"}]})

    estados = {"n": 0}

    def _post(url, json=None, timeout=None, **kwargs):
        estados["n"] += 1
        if estados["n"] == 1:
            # timeout de inferencia en la primera página
            raise requests.exceptions.ReadTimeout("lento")
        return _Respuesta(200, {"choices": [{"message": {
            "content": "---\nx: 1\n---\nPAGINA DOS LEGIBLE"}}]})

    monkeypatch.setattr(requests, "get", _get)
    monkeypatch.setattr(requests, "post", _post)
    texto, conf = web._ocr_llamacpp([b"a", b"b"])
    assert "PAGINA DOS LEGIBLE" in texto
    assert conf == 0.85


def test_ocr_llamacpp_todas_fallan(monkeypatch):
    """/health OK pero TODAS las páginas fallan -> ("", 0.0): el fallo es
    del motor local (v10.0: NO hay escalada a la nube; la decisión de
    cachear o no la toma _extraer_texto_pdf)."""

    def _get(url, timeout=None, **kwargs):
        if url.endswith("/health"):
            return _Respuesta(200, {"status": "ok"})
        return _Respuesta(200, {"data": [{"id": "olmOCR.gguf"}]})

    def _post(url, json=None, timeout=None, **kwargs):
        raise requests.exceptions.ReadTimeout("lento")

    monkeypatch.setattr(requests, "get", _get)
    monkeypatch.setattr(requests, "post", _post)
    assert web._ocr_llamacpp([b"a", b"b"]) == ("", 0.0)
    # El servidor NO está marcado como caído (respondió al health).
    assert web._llamacpp_servidor_caido() is False


# ============================== LIMPIEZA DE SALIDA ==========================

def test_limpiar_salida_olmocr():
    """El front matter YAML de olmOCR-2 se elimina; texto normal intacto."""
    fm = ("---\nprimary_language: es\nis_rotation_valid: True\n"
          "rotation_correction: 0\nis_table: False\nis_diagram: False\n"
          "---\nTEXTO DE LA PAGINA")
    assert web._limpiar_salida_olmocr(fm) == "TEXTO DE LA PAGINA"
    assert web._limpiar_salida_olmocr("SIN FRONT MATTER") == \
        "SIN FRONT MATTER"
    # Front matter sin cierre (respuesta rara): se devuelve tal cual.
    assert web._limpiar_salida_olmocr("---\nabierto") == "---\nabierto"
    assert web._limpiar_salida_olmocr("") == ""


def test_reescalar_para_olmocr(monkeypatch):
    """La imagen se reescala al lado mayor 1288 (entrenamiento de olmOCR-2);
    una imagen pequeña va tal cual; bytes no-imagen van tal cual.
    (v10.1: el límite lo fija OCR_LLAMACPP_MAX_LADO: default por familia,
    override por .env; aquí se fija a 1288 como con familia olmocr2.)"""
    monkeypatch.setattr(web, "OCR_LLAMACPP_MAX_LADO", 1288)
    from PIL import Image
    img = Image.new("RGB", (2339, 1654), color=(255, 255, 255))
    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    reducida = web._reescalar_para_olmocr(buf.getvalue())
    nueva = Image.open(io.BytesIO(reducida))
    assert max(nueva.size) == 1288

    pequena = io.BytesIO()
    Image.new("RGB", (600, 400)).save(pequena, format="PNG")
    assert web._reescalar_para_olmocr(pequena.getvalue()) == \
        pequena.getvalue()

    assert web._reescalar_para_olmocr(b"no-es-una-imagen") == \
        b"no-es-una-imagen"


# ============================== CASCADA =====================================

def _conn_memoria():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute("CREATE TABLE IF NOT EXISTS ocr_cache "
                 "(hash_pdf TEXT PRIMARY KEY, texto TEXT, "
                 " backend_usado TEXT, confianza REAL)")
    return conn


def test_cascada_llamacpp_exito(monkeypatch):
    """OCR_BACKEND=llamacpp + texto válido -> backend 'llamacpp-<familia>'
    + caché (v10.1: el backend incluye la familia)."""
    _fija_familia(monkeypatch, "olmocr2")
    monkeypatch.setattr(web, "OCR_BACKEND", "llamacpp")
    monkeypatch.setattr(web, "_pdf_a_imagenes",
                        lambda b, m: ([b"img"], 1, 1))
    monkeypatch.setattr(web, "_ocr_llamacpp",
                        lambda imgs: ("texto de olmocr", 0.85))
    conn = _conn_memoria()
    texto, backend, conf = web._extraer_texto_pdf(
        b"pdf-falso", "https://ejemplo.org/registro.pdf", conn=conn)
    assert (texto, backend, conf) == ("texto de olmocr",
                                      "llamacpp-olmocr2", 0.85)
    fila = conn.execute("SELECT backend_usado FROM ocr_cache").fetchone()
    assert fila[0] == "llamacpp-olmocr2"


def test_cascada_llamacpp_cacheado(monkeypatch):
    """La segunda llamada del mismo PDF sale de la caché (sufijo _cache,
    con la familia v10.1 en el backend)."""
    _fija_familia(monkeypatch, "olmocr2")
    monkeypatch.setattr(web, "OCR_BACKEND", "llamacpp")
    llamadas = {"n": 0}

    def _ocr(imgs):
        llamadas["n"] += 1
        return "texto de olmocr", 0.85

    monkeypatch.setattr(web, "_pdf_a_imagenes",
                        lambda b, m: ([b"img"], 1, 1))
    monkeypatch.setattr(web, "_ocr_llamacpp", _ocr)
    conn = _conn_memoria()
    web._extraer_texto_pdf(b"pdf-abc", "https://a.org/x.pdf", conn=conn)
    texto, backend, conf = web._extraer_texto_pdf(
        b"pdf-abc", "https://a.org/x.pdf", conn=conn)
    assert backend == "llamacpp-olmocr2_cache"
    assert llamadas["n"] == 1


def test_cascada_llamacpp_caido_no_cachea_fracaso(monkeypatch):
    """v10.0 (reescrito del viejo test de escalada a Gemini): servidor
    caído -> fallo EXPLÍCITO 'ocr_local_fallido', SIN ninguna llamada a
    la nube y NADA cacheado en ocr_cache, para que la próxima ejecución
    (con llama-server arrancado) reintente."""
    monkeypatch.setattr(web, "OCR_BACKEND", "llamacpp")
    monkeypatch.setattr(web, "_pdf_a_imagenes",
                        lambda b, m: ([b"img"], 1, 1))
    monkeypatch.setattr(web, "_ocr_llamacpp", lambda imgs: ("", 0.0))
    # Simular que el cliente detectó el servidor caído (flag del módulo).
    monkeypatch.setattr(web, "_LLAMACPP_CAIDO", True)
    monkeypatch.setattr(web, "_llamacpp_servidor_caido", lambda: True)
    conn = _conn_memoria()
    texto, backend, _ = web._extraer_texto_pdf(
        b"pdf-caido", "https://a.org/x.pdf", conn=conn)
    assert texto == ""
    assert backend == "ocr_local_fallido"     # fallo explícito, no nube
    filas = conn.execute("SELECT backend_usado FROM ocr_cache").fetchall()
    assert filas == []                          # NADA cacheado (transitorio)


def test_cascada_llamacpp_manuscrito_local(monkeypatch):
    """v10.0/v10.1 (reescrito del viejo test 'antes_que_vlm'): URL de
    manuscrito (PARES) + llamacpp -> 'llamacpp_manuscrito-<familia>' y
    CERO llamadas al LLM de la nube: el OCR de manuscritos es 100% local."""
    _fija_familia(monkeypatch, "olmocr2")
    from utils import llm as modulo_llm
    llamadas = {"n": 0}

    class _Completions:
        def create(self, **kwargs):
            llamadas["n"] += 1
            raise AssertionError("el OCR local no debe llamar a la nube")

    class _Chat:
        completions = _Completions()

    class _LlmFalso:
        chat = _Chat()

    monkeypatch.setattr(modulo_llm, "llm", _LlmFalso())
    monkeypatch.setattr(web, "OCR_BACKEND", "llamacpp")
    monkeypatch.setattr(web, "_pdf_a_imagenes",
                        lambda b, m: ([b"img"], 1, 1))
    monkeypatch.setattr(web, "_ocr_llamacpp",
                        lambda imgs: ("partida manuscrita", 0.85))
    conn = _conn_memoria()
    texto, backend, conf = web._extraer_texto_pdf(
        b"pdf-m", "https://pares.cultura.gob.es/catastro/libro.pdf",
        conn=conn)
    assert backend == "llamacpp_manuscrito-olmocr2"
    assert conf == 0.85
    assert llamadas["n"] == 0                # 0 llamadas a la nube


def test_cascada_llamacpp_manuscrito_sin_presupuesto(monkeypatch):
    """Con presupuesto agotado, llamacpp AÚN transcribe manuscritos (la
    inferencia local es gratis y v10.0 no consulta el presupuesto en el
    OCR: no hay etapa de pago)."""
    _fija_familia(monkeypatch, "olmocr2")
    from utils.llm import GASTO
    monkeypatch.setattr(web, "OCR_BACKEND", "llamacpp")
    monkeypatch.setattr(web, "_pdf_a_imagenes",
                        lambda b, m: ([b"img"], 1, 1))
    monkeypatch.setattr(web, "_ocr_llamacpp",
                        lambda imgs: ("partida manuscrita", 0.85))
    presupuesto_previo = GASTO.presupuesto_max
    GASTO.presupuesto_max = 0.0   # agotado
    try:
        conn = _conn_memoria()
        texto, backend, _ = web._extraer_texto_pdf(
            b"pdf-sin-euros", "https://pares.cultura.gob.es/x.pdf",
            conn=conn)
        assert backend == "llamacpp_manuscrito-olmocr2"
        assert texto == "partida manuscrita"
    finally:
        GASTO.presupuesto_max = presupuesto_previo


def test_diagnostico_check_llamacpp(monkeypatch, capsys):
    """La comprobación 7f de diagnostico() avisa con instrucciones cuando
    el llama-server no está (y cuenta como AVISO, no como error).
    (Familia olmocr2: el aviso trae el comando manual -m/--mmproj.)"""
    _fija_familia(monkeypatch, "olmocr2")
    from agent.gedcom import diagnostico
    import config
    monkeypatch.setattr(web, "OCR_BACKEND", "llamacpp")
    monkeypatch.setattr(config, "OCR_BACKEND", "llamacpp")
    # OCR_LLAMACPP_URL se importa dentro de diagnostico: parchear config.
    monkeypatch.setattr(requests, "get",
                        lambda url, timeout=None, **k: (
                            _raise_conexion()))
    errores_antes = capsys.readouterr()
    # diagnostico() completo toca red y BD: se prueba SOLO la parte 7f
    # replicando su lógica exacta (llama a llamacpp_disponible).
    ok, detalle = web.llamacpp_disponible()
    assert not ok
    # _aviso_llamacpp_caido emite el warn con el comando de arranque.
    web._aviso_llamacpp_caido(detalle)
    salida = capsys.readouterr().out
    assert "llama-server no responde" in salida
    assert "llama-server -m" in salida


def _raise_conexion():
    raise requests.exceptions.ConnectionError("refused")
