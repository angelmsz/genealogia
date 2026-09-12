"""
tests/test_gasto.py — Control de gasto honesto (punto 8 del informe).

  - Cuando el provider NO devuelve usage, el coste se ESTIMA (nunca coste
    cero): así el tope de --presupuesto-max salta aunque falte el recuento.
  - Con usage presente, el coste se mide.
  - El presupuesto lanza PresupuestoExcedido al superarse.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from utils.llm import Gasto, PresupuestoExcedido

MODELO = "z-ai/glm-5.2"


def test_coste_estimado_cuando_falta_usage():
    """El fallo del punto 8: usage=None se apuntaba como coste CERO y el
    límite de gasto nunca saltaba."""
    g = Gasto()
    g.registrar_llamada(MODELO, None,
                        texto_entrada="x" * 4000,   # ~1000 tokens
                        texto_salida="y" * 800)     # ~200 tokens
    assert g.coste > 0
    assert g.llamadas_estimadas == 1
    assert g.prompt_tokens >= 1000
    d = g.por_modelo[MODELO]
    assert d["estimadas"] == 1


def test_coste_medido_cuando_hay_usage():
    g = Gasto()
    usage = SimpleNamespace(prompt_tokens=1000, completion_tokens=200)
    g.registrar_llamada(MODELO, usage)
    assert g.coste > 0
    assert g.llamadas_estimadas == 0
    assert g.prompt_tokens == 1000
    assert g.completion_tokens == 200


def test_usage_dict_y_none_seguros():
    g = Gasto()
    g.registrar_llamada(MODELO, {"prompt_tokens": 50, "completion_tokens": 10})
    assert g.prompt_tokens == 50
    # usage con ceros: se estima, no se traga
    g.registrar_llamada(MODELO, SimpleNamespace(prompt_tokens=0,
                                                completion_tokens=0),
                        texto_entrada="hola", texto_salida="mundo")
    assert g.llamadas_estimadas == 1


def test_imagenes_suman_estimacion():
    g = Gasto()
    g.registrar_llamada(MODELO, None, texto_entrada="", texto_salida="",
                        n_imagenes=3)
    assert g.prompt_tokens >= 3000      # ~1000 tokens por imagen


def test_presupuesto_excedido_salta():
    g = Gasto(presupuesto_max=0.000001)
    with pytest.raises(PresupuestoExcedido):
        g.registrar_llamada(MODELO, None, texto_entrada="x" * 4000,
                            texto_salida="")


def test_reintentos_limitados():
    """El tope de reintentos por consulta LLM bajó de 4 a
    MAX_REINTENTOS_LLM (3), y el SDK de 3 a 1: máx. 6 llamadas reales
    por consulta en vez de 16 (la causa de los cuelgues de 6 minutos)."""
    from config import MAX_REINTENTOS_LLM
    import utils.llm as llm_mod
    assert MAX_REINTENTOS_LLM <= 3
    assert llm_mod.llm.max_retries <= 1
