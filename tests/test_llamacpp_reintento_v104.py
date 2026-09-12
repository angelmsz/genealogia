"""
tests/test_llamacpp_reintento_v104.py — v10.4: la caída del llama-server ya
no apaga el OCR local para TODA la ejecución.

EL PROBLEMA QUE CORRIGE (auditoría de autonomía): el primer fallo de conexión
marcaba `_LLAMACPP_CAIDO` y era DEFINITIVO para el proceso. Si el usuario
arrancaba llama-server veinte minutos después (o el servidor se reiniciaba a
mitad de noche), el agente seguía sin transcribir un solo manuscrito hasta la
noche siguiente. Con la regla "sin relanzamientos manuales" eso era una fuga
de toda la cosecha de la noche.

LA REGLA NUEVA: la marca caduca. Pasada LLAMACPP_REINTENTO_SEGUNDOS se vuelve
a preguntar por /health (un GET de milisegundos). El aviso sigue saliendo UNA
vez por proceso, y dentro de la ventana NO se toca la red (no se martillea un
servidor caído).

100% offline: el HTTP de llama-server se simula con fakes de requests.
Ejecución:  python -m pytest tests/test_llamacpp_reintento_v104.py -q
"""

from __future__ import annotations

import time

import pytest
import requests

import scrapers.web as web


@pytest.fixture(autouse=True)
def _reset_llamacpp():
    web._reset_llamacpp_estado()
    yield
    web._reset_llamacpp_estado()


class _Respuesta:
    """Respuesta HTTP falsa (mínimo que usa el cliente)."""

    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code}")


def _stub_servidor(monkeypatch, peticiones, contenido="TEXTO DE LA PAGINA"):
    """Stub de llama-server: /health, /v1/models y /v1/chat/completions."""

    def _get(url, timeout=None, **kwargs):
        if url.endswith("/health"):
            return _Respuesta(200, {"status": "ok"})
        if url.endswith("/v1/models"):
            return _Respuesta(200, {"data": [{"id": "modelo-de-prueba.gguf"}]})
        raise AssertionError(f"URL inesperada: {url}")

    def _post(url, json=None, timeout=None, **kwargs):
        peticiones.append(json)
        return _Respuesta(200, {"choices": [{"message": {
            "role": "assistant", "content": contenido}}]})

    monkeypatch.setattr(requests, "get", _get)
    monkeypatch.setattr(requests, "post", _post)


# ==================== LA MARCA CADUCA (UNIDAD) ==============================

def test_dentro_de_la_ventana_el_servidor_sigue_caido():
    web._aviso_llamacpp_caido("connection refused")
    assert web._LLAMACPP_CAIDO is True
    assert web._llamacpp_servidor_caido() is True


def test_pasada_la_ventana_se_reintenta(monkeypatch):
    monkeypatch.setattr(web, "LLAMACPP_REINTENTO_SEGUNDOS", 600.0)
    web._aviso_llamacpp_caido("connection refused")
    web._LLAMACPP_CAIDO_TS = time.time() - 601
    assert web._llamacpp_servidor_caido() is False
    assert web._LLAMACPP_CAIDO is False      # la marca se ha borrado


def test_reset_limpia_la_marca_y_el_timestamp():
    web._aviso_llamacpp_caido("connection refused")
    assert web._LLAMACPP_CAIDO_TS > 0
    web._reset_llamacpp_estado()
    assert web._LLAMACPP_CAIDO is False
    assert web._LLAMACPP_CAIDO_TS == 0.0


# ==================== COMPORTAMIENTO DEL CLIENTE ============================

def test_dentro_de_la_ventana_no_se_toca_la_red(monkeypatch):
    """No se martillea un servidor caído: dentro de la ventana, el OCR
    devuelve vacío SIN hacer una sola petición."""
    def _no_llamar(*a, **kw):
        raise AssertionError("no debería haber peticiones HTTP")

    monkeypatch.setattr(requests, "get", _no_llamar)
    monkeypatch.setattr(requests, "post", _no_llamar)
    web._aviso_llamacpp_caido("connection refused")
    assert web._ocr_llamacpp([b"imagen"]) == ("", 0.0)


def test_pasada_la_ventana_el_ocr_se_recupera(monkeypatch):
    """Escenario real: la noche empieza sin llama-server y el usuario lo
    arranca diez minutos después. El OCR vuelve solo."""
    peticiones: list = []
    _stub_servidor(monkeypatch, peticiones)
    monkeypatch.setattr(web, "LLAMACPP_REINTENTO_SEGUNDOS", 600.0)

    web._aviso_llamacpp_caido("connection refused")
    assert web._ocr_llamacpp([b"imagen"]) == ("", 0.0)   # dentro de la ventana

    web._LLAMACPP_CAIDO_TS = time.time() - 601           # ventana cumplida
    texto, confianza = web._ocr_llamacpp([b"imagen"])
    assert texto == "TEXTO DE LA PAGINA"
    assert confianza == 0.85
    assert peticiones and peticiones[0]["max_tokens"] == 4096
