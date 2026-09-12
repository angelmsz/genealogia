"""
tests/test_reasoning_v92.py — v9.2 PARTE 2: DeepSeek V4.1 Flash, precios
punta/valle y el parámetro "reasoning" de chat_json().

Lo que fija:
  - Con REASONING_ACTIVADO=false (default) el payload NO lleva
    "reasoning" (los modelos sin soporte ni se enteran: cero cambios de
    comportamiento respecto a la v9.1).
  - Con REASONING_ACTIVADO=true el payload lleva
    reasoning={"enabled": true} y COEXISTE con response_format
    (structured outputs), que V4.1 Flash también soporta.
  - Si el modelo rechaza "reasoning" (400), chat_json lo desactiva para
    esa sesión y reintenta sin él (mismo patrón que _MODELOS_SIN_SCHEMA).
  - El default de fase 2 tiene entrada de precio y el control de GASTO
    calcula con ella (los tests de coste de la v4.2 siguen verdes).

Ejecución:  python -m pytest tests/test_reasoning_v92.py -q
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import utils.llm as llm_mod
from utils.llm import Gasto


# ============================== HELPERS ======================================

def _respuesta_ok(contenido: str = '{"ok": 1}'):
    """Respuesta mínima del SDK con usage (para que el coste se MIDA)."""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=contenido))],
        usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=200))


@pytest.fixture(autouse=True)
def _limpiar_estado_de_sesion():
    """Los sets _MODELOS_SIN_* son estado de módulo: se vacían antes de
    cada test para que un test no contamine al siguiente."""
    llm_mod._MODELOS_SIN_REASONING.clear()
    llm_mod._MODELOS_SIN_SCHEMA.clear()
    yield
    llm_mod._MODELOS_SIN_REASONING.clear()
    llm_mod._MODELOS_SIN_SCHEMA.clear()


# ====================== PARÁMETRO REASONING EN EL PAYLOAD ===================

def test_reasoning_no_se_envia_por_defecto(monkeypatch):
    """Default: REASONING_ACTIVADO=false -> el payload de chat_json NO
    incluye "reasoning" (ni siquiera para el propio V4.1 Flash). Así no
    se rompe ningún modelo que no lo soporte."""
    capturado = {}

    def _crear(**kwargs):
        capturado.update(kwargs)
        return _respuesta_ok()

    monkeypatch.setattr(llm_mod.llm.chat.completions, "create", _crear)
    monkeypatch.setattr(llm_mod, "REASONING_ACTIVADO", False)
    llm_mod.chat_json("deepseek/deepseek-v4.1-flash", "sistema", "usuario")
    assert "reasoning" not in capturado


def test_reasoning_se_envia_si_se_activa_y_convive_con_schema(monkeypatch):
    """Con REASONING_ACTIVADO=true el payload lleva
    reasoning={"enabled": true} Y response_format a la vez (V4.1 Flash
    soporta ambos según OpenRouter: reasoning + structured_outputs)."""
    capturado = {}

    def _crear(**kwargs):
        capturado.update(kwargs)
        return _respuesta_ok()

    monkeypatch.setattr(llm_mod.llm.chat.completions, "create", _crear)
    monkeypatch.setattr(llm_mod, "REASONING_ACTIVADO", True)
    llm_mod.chat_json(
        "deepseek/deepseek-v4.1-flash", "sistema", "usuario",
        json_schema={"type": "object",
                     "properties": {"ok": {"type": "integer"}}})
    assert capturado["reasoning"] == {"enabled": True}
    assert capturado["response_format"]["type"] == "json_schema"


def test_fallback_si_el_modelo_rechaza_reasoning(monkeypatch):
    """Un modelo sin soporte de reasoning devuelve 400 -> se desactiva
    para esa sesión, se reintenta SIN él y la llamada sale bien (no se
    rompe por tener REASONING_ACTIVADO=true)."""
    llamadas = []

    def _crear(**kwargs):
        llamadas.append(kwargs)
        if "reasoning" in kwargs:
            raise RuntimeError(
                "400 Bad Request: unsupported parameter: reasoning is "
                "not supported for this model")
        return _respuesta_ok()

    monkeypatch.setattr(llm_mod.llm.chat.completions, "create", _crear)
    monkeypatch.setattr(llm_mod, "REASONING_ACTIVADO", True)
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)  # sin backoff
    modelo = "demo/modelo-sin-reasoning"

    resultado = llm_mod.chat_json(modelo, "sistema", "usuario")

    assert resultado == {"ok": 1}
    assert len(llamadas) == 2
    assert "reasoning" in llamadas[0]
    assert "reasoning" not in llamadas[1]
    assert modelo in llm_mod._MODELOS_SIN_REASONING


def test_error_de_schema_sigue_siendo_de_schema(monkeypatch):
    """Un 400 que menciona response_format pero NO reasoning no toca el
    fallback de reasoning: se desactiva el schema (comportamiento v4.x
    intacto) aunque REASONING_ACTIVADO=true."""
    llamadas = []

    def _crear(**kwargs):
        llamadas.append(kwargs)
        if "response_format" in kwargs:
            raise RuntimeError(
                "400 Bad Request: response_format json_schema is not "
                "supported by this provider")
        return _respuesta_ok()

    monkeypatch.setattr(llm_mod.llm.chat.completions, "create", _crear)
    monkeypatch.setattr(llm_mod, "REASONING_ACTIVADO", True)
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)
    modelo = "demo/modelo-sin-schema"

    resultado = llm_mod.chat_json(
        modelo, "sistema", "usuario",
        json_schema={"type": "object"})

    assert resultado == {"ok": 1}
    assert "reasoning" in llamadas[0]        # el reasoning sigue intentándose
    assert "response_format" not in llamadas[1]
    assert modelo in llm_mod._MODELOS_SIN_SCHEMA
    assert modelo not in llm_mod._MODELOS_SIN_REASONING


# ====================== PRECIOS Y CONTROL DE GASTO ==========================

def test_default_de_fase2_y_su_precio():
    """MODELO_FASE2 apunta a DeepSeek V4.1 Flash y tiene entrada de
    precio: el control de gasto nunca cae al precio por defecto."""
    from config import MODELO_FASE2, PRECIO_MILLON_TOKENS
    assert MODELO_FASE2 == "deepseek/deepseek-v4.1-flash"
    assert MODELO_FASE2 in PRECIO_MILLON_TOKENS
    p = PRECIO_MILLON_TOKENS[MODELO_FASE2]
    assert p["entrada"] > 0 and p["salida"] > 0


def test_precio_v41_flash_nunca_por_debajo_del_valle():
    """OpenRouter cobra a v4.1-flash $0.15/$0.60 en valle y el doble en
    punta: la tabla usa el precio MÁS ALTO (política v9.2 de no
    infravalorar el gasto). Este test documenta la política: el precio
    apuntado nunca puede bajar de la tarifa base."""
    from config import PRECIO_MILLON_TOKENS
    p = PRECIO_MILLON_TOKENS["deepseek/deepseek-v4.1-flash"]
    assert p["entrada"] >= 0.15   # tarifa base (valle) consultada 2026-09-11
    assert p["salida"] >= 0.60


def test_gasto_calcula_con_el_precio_del_nuevo_default():
    """El acumulador de gasto mide con la entrada de precio de v4.1-flash
    (regresión del punto 8 de la v4.2: nunca coste cero, presupuesto
    real). Con usage de 1000 entrada + 200 salida y precio punta
    0.30/1.20: coste = 1000*0.30/1e6 + 200*1.20/1e6 = $0.00054."""
    g = Gasto()
    usage = SimpleNamespace(prompt_tokens=1000, completion_tokens=200)
    g.registrar_llamada("deepseek/deepseek-v4.1-flash", usage)
    esperado = 1000 * 0.30 / 1_000_000 + 200 * 1.20 / 1_000_000
    assert abs(g.coste - esperado) < 1e-12
    assert g.llamadas_estimadas == 0
