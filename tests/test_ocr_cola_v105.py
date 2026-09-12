"""
v10.4.1 (tarea B) — Una cola para el OCR (una sola GPU) y reintento de página.

Contexto REAL (log agente_20260912_222149.log):
  - fase1.py descarga con ThreadPoolExecutor(max_workers=N_HILOS_DESCARGA=3)
    y el OCR de cada PDF corre DENTRO de esos hilos. No había ningún cerrojo
    alrededor de llama-server, así que esa noche DOS PDFs se transcribieron a
    la vez (log interleaved 23:31-23:45: un 355 y un 240 páginas) contra una
    sola GPU. La GPU se repartió y las páginas se pasaron de los 60 s de
    OCR_LLAMACPP_TIMEOUT_INFERENCIA: 10 de 30 páginas sin transcribir (y 2 de
    6 en otro PDF).
  - esas 12 páginas se perdían SIN REINTENTO: el bucle pasaba a la siguiente
    y el texto se cacheaba con huecos (y, hasta la tarea A, sin decirlo).

Lo que fija esta suite:
  1. Con el semáforo, N hilos OCR-eando a la vez hacen como MÁXIMO una
     petición simultánea a llama-server (las descargas siguen en paralelo).
  2. Una página que falla (timeout) se reintenta y, si responde, se recupera.
  3. Una respuesta vacía también se reintenta (es un fallo de la página).
  4. Con OCR_PAGINAS_REINTENTOS=0 se recupera el comportamiento anterior.
  5. Servidor CAÍDO a mitad de PDF: NO se reintenta y se conserva lo ya
     transcrito (mismo criterio que la v10.0).

Sin GPU y sin servidor: se simula el HTTP de llama-server con fakes.
"""

from __future__ import annotations

import threading
import time

import pytest
import requests

import scrapers.web as web


class _Respuesta:
    """Respuesta HTTP falsa (la API mínima que usa el cliente)."""

    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code}")


@pytest.fixture(autouse=True)
def _estado_limpio(monkeypatch):
    """Familia fija (independiente del .env del usuario) y flags reseteados."""
    monkeypatch.setattr(web, "OCR_LLAMACPP_FAMILIA", "olmocr2")
    monkeypatch.setattr(web, "OCR_LLAMACPP_PROMPT", "")
    web._reset_llamacpp_estado()
    monkeypatch.setattr(web, "_LLAMACPP_MODELO", "olmOCR-2-7B.gguf")
    monkeypatch.setattr(web, "llamacpp_disponible",
                        lambda timeout=None: (True, "ok"))
    yield
    web._reset_llamacpp_estado()


def _texto(contenido: str) -> _Respuesta:
    return _Respuesta(200, {"choices": [{"message": {"content": contenido}}]})


# ----------------------------------------------- 1. cola de un solo doc --
def test_un_solo_documento_a_la_vez_en_llama_server(monkeypatch) -> None:
    """Tres hilos (los de N_HILOS_DESCARGA) OCR-eando a la vez: como mucho UNA
    petición simultánea contra el servidor. Era el origen de los timeouts de
    página del 12/09."""
    estado = {"ahora": 0, "max": 0}
    cerrojo = threading.Lock()

    def _post(url, json=None, timeout=None, **kwargs):
        with cerrojo:
            estado["ahora"] += 1
            estado["max"] = max(estado["max"], estado["ahora"])
        time.sleep(0.05)                     # simula la inferencia de la GPU
        with cerrojo:
            estado["ahora"] -= 1
        return _texto("texto de la pagina")

    monkeypatch.setattr(requests, "post", _post)
    resultados: list = []
    hilos = [threading.Thread(
        target=lambda: resultados.append(web._ocr_llamacpp([b"img1", b"img2"])))
        for _ in range(3)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(timeout=15)

    assert len(resultados) == 3
    assert estado["max"] == 1, "dos documentos a la vez en la misma GPU"


def test_el_semaforo_se_puede_desactivar(monkeypatch) -> None:
    """OCR_SERIALIZAR_SERVIDOR=false mantiene el camino antiguo (por si algún
    día el OCR no vive en el mismo servidor)."""
    monkeypatch.setattr(web, "OCR_SERIALIZAR_SERVIDOR", False)
    monkeypatch.setattr(requests, "post", lambda *a, **k: _texto("ok"))
    assert web._ocr_llamacpp([b"img"])[1] == 0.85


# ------------------------------------------------- 2. reintento ----------
def test_una_pagina_que_falla_se_reintenta_y_se_recupera(
        monkeypatch, capsys) -> None:
    """Página con timeout en el primer intento y buena en el segundo: entra en
    el texto (antes se perdía para siempre)."""
    monkeypatch.setattr(web, "OCR_PAGINAS_REINTENTOS", 1)
    posts = {"n": 0}

    def _post(url, json=None, timeout=None, **kwargs):
        posts["n"] += 1
        if posts["n"] == 1:
            raise requests.exceptions.ReadTimeout("lento")
        return _texto("PAGINA RECUPERADA")

    monkeypatch.setattr(requests, "post", _post)
    texto, conf = web._ocr_llamacpp([b"pagina-1"])

    assert "PAGINA RECUPERADA" in texto
    assert conf == 0.85
    assert posts["n"] == 2
    assert "reintentada" in capsys.readouterr().out


def test_una_respuesta_vacia_tambien_se_reintenta(monkeypatch) -> None:
    """Respuesta vacía = fallo de la página (no se cachea basura vacía)."""
    monkeypatch.setattr(web, "OCR_PAGINAS_REINTENTOS", 1)
    posts = {"n": 0}

    def _post(url, json=None, timeout=None, **kwargs):
        posts["n"] += 1
        return _texto("" if posts["n"] == 1 else "TEXTO AL SEGUNDO INTENTO")

    monkeypatch.setattr(requests, "post", _post)
    texto, _ = web._ocr_llamacpp([b"pagina-1"])

    assert "TEXTO AL SEGUNDO INTENTO" in texto
    assert posts["n"] == 2


def test_sin_reintentos_se_comporta_como_antes(monkeypatch) -> None:
    """OCR_PAGINAS_REINTENTOS=0: una página fallida se pierde (una sola
    petición), que era el comportamiento anterior."""
    monkeypatch.setattr(web, "OCR_PAGINAS_REINTENTOS", 0)
    posts = {"n": 0}

    def _post(url, json=None, timeout=None, **kwargs):
        posts["n"] += 1
        raise requests.exceptions.ReadTimeout("lento")

    monkeypatch.setattr(requests, "post", _post)
    assert web._ocr_llamacpp([b"pagina-1"]) == ("", 0.0)
    assert posts["n"] == 1


def test_servidor_caido_a_mitad_no_se_reintenta(monkeypatch) -> None:
    """ConnectionError = el servidor se cayó: se corta el PDF y se conserva lo
    transcrito (reintentar contra un servidor muerto solo gasta tiempo)."""
    monkeypatch.setattr(web, "OCR_PAGINAS_REINTENTOS", 1)
    posts = {"n": 0}

    def _post(url, json=None, timeout=None, **kwargs):
        posts["n"] += 1
        if posts["n"] == 1:
            return _texto("PAGINA UNO OK")
        raise requests.exceptions.ConnectionError("se cayó")

    monkeypatch.setattr(requests, "post", _post)
    texto, conf = web._ocr_llamacpp([b"p1", b"p2"])

    assert "PAGINA UNO OK" in texto      # lo ya transcrito se conserva
    assert conf == 0.85
    assert posts["n"] == 2               # la página 2 NO se reintentó
