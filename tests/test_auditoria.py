"""
tests/test_auditoria.py — Auditoría de citas contra el TEXTO ORIGINAL
(punto 5 del informe: "la verificación de citas no verifica de verdad").

v4.2: la cita se comprueba contra el texto ORIGINAL descargado de la
página, no contra el resumen que hizo el LLM de filtrado. Si la primera
IA se equivocó o rellenó un nombre, el error ya NO queda marcado como
"verificado con cita documental".
"""

from __future__ import annotations

import pytest

import agent.fase2 as fase2

URL = "http://archivodeejemplo.es/partida/123"
ORIGINAL = ("En el libro de bautismos de la parroquia de Salas: Isidro "
            "Merillas Panero hijo de Nazario Merillas y de Obdulia Pelaz "
            "nacio el dia dos de mayo de mil ochocientos setenta")
LIMPIO = ("Isidro Merillas Panero, hijo de Nazario Merillas, nacido en "
          "1870 en Salas de los Infantes")   # resumen del LLM (reformatado)


def _corpus():
    return [{"url": URL, "texto_limpio": LIMPIO,
             "texto_original": ORIGINAL}]


def _hallazgo(cita):
    return {"persona": "Isidro Merillas Panero", "tipo_evento": "bautismo",
            "cita_literal": cita, "url_fuente": URL}


@pytest.fixture(autouse=True)
def _sin_llm(monkeypatch):
    """Ningún test de auditoría debe gastar tokens: chat_json 'explota'
    si se le llama (los casos verificados determinísticamente ni siquiera
    lo intentan)."""
    def _boom(*a, **kw):
        raise AssertionError("chat_json no debería llamarse en este test")
    monkeypatch.setattr(fase2, "chat_json", _boom)


def test_cita_en_original_verificada_determinista():
    h = [_hallazgo("Isidro Merillas Panero hijo de Nazario Merillas")]
    fase2.auditar_citas(h, _corpus())
    assert h[0]["verificacion_cita"] == "VERIFICADA"
    assert h[0].get("verificacion_contra") == "texto_original"


def test_cita_solo_en_resumen_del_llm_NO_verificada():
    """EL fallo del punto 5: la cita 'reformatada' solo existe en el
    RESUMEN del LLM de filtrado, no en la página original. La v4.1 la
    daba por verificada; la v4.2 la deja SIN_VERIFICAR y no entra al
    árbol como hecho probado."""
    h = [_hallazgo("Isidro Merillas Panero, hijo de Nazario Merillas, "
                   "nacido en 1870 en Salas de los Infantes")]
    # el LLM de auditoría (fallback difuso) tampoco la encuentra:
    # simulamos que responde que NO aparece.
    monkey_response = {"resultados": [{"indice": 0, "aparece": False}]}
    fase2.chat_json = lambda *a, **kw: monkey_response  # type: ignore
    fase2.auditar_citas(h, _corpus())
    assert h[0]["verificacion_cita"] == "SIN_VERIFICAR"
    assert "no se encontró" in h[0].get("motivo_verificacion", "")


def test_cita_con_nombre_inventado_no_verificada():
    """La IA de filtrado 'rellenó' un apellido que no está en el original:
    el error no puede colar como verificado."""
    h = [_hallazgo("Isidro Merillas Panero Gutierrez hijo de Nazario")]
    monkey_response = {"resultados": [{"indice": 0, "aparece": False}]}
    fase2.chat_json = lambda *a, **kw: monkey_response  # type: ignore
    fase2.auditar_citas(h, _corpus())
    assert h[0]["verificacion_cita"] == "SIN_VERIFICAR"


def test_sin_texto_original_para_la_url():
    h = [_hallazgo("cualquier cita")]
    fase2.auditar_citas(h, [])
    assert h[0]["verificacion_cita"] == "SIN_VERIFICAR"
    assert h[0].get("motivo_verificacion") == "sin texto original para esa URL"


def test_cita_vacia():
    h = [_hallazgo("")]
    fase2.auditar_citas(h, _corpus())
    assert h[0]["verificacion_cita"] == "SIN_VERIFICAR"
    assert h[0].get("motivo_verificacion") == "cita vacía"


def test_fallback_corpus_prev42():
    """Fragmentos guardados antes de la v4.2 (sin texto_original) caen al
    texto_limpio: mismo comportamiento que siempre tuvieron."""
    h = [_hallazgo("hijo de Nazario Merillas, nacido en 1870")]
    # quitamos el original para simular un corpus antiguo
    corpus = [{"url": URL, "texto_limpio": LIMPIO}]
    fase2.auditar_citas(h, corpus)
    assert h[0]["verificacion_cita"] == "VERIFICADA"
