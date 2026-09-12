"""
v10.4.1 (tarea E) — El estimador de gasto ya no miente a la baja.

Contexto REAL (log agente_20260912_222149.log): el bot dijo $0.1344 y
OpenRouter cobró $0.38. La causa NO eran los precios de la tabla (la
aritmética del log cuadra al cuarto decimal con ella), sino que el estimador
solo veía las llamadas que VOLVÍAN: 84 contadas de ~130 peticiones. Las 46
abandonadas por el timeout de 45 s (38 intentos de lote + 3 de consolidación
+ 5 de fase 1) las generó y las facturó el proveedor. Y como
--presupuesto-max se calculaba sobre la cifra optimista, el tope NO protegía.

Lo que fija esta suite:
  1. Un intento abandonado se contabiliza con coste estimado POR ARRIBA
     (prompt conocido + max_tokens x precio de la tabla).
  2. Sin tope explícito se usa MAX_TOKENS_ESTIMADO (sigue siendo por arriba
     de lo que devuelven esas llamadas).
  3. El abandono CUENTA para el presupuesto: si al sumarlo se pasa, salta
     PresupuestoExcedido (parada segura) en lugar del timeout.
  4. El resumen de gasto declara los abandonados y separa medido/estimado.
  5. Sin modelo no se registra nada (compatibilidad con llamadas internas).

No toca red ni LLM.
"""

from __future__ import annotations

import time

import pytest

import config
from utils import llm as llm_mod
from utils.llm import Gasto, LLMTimeoutHard, PresupuestoExcedido

MODELO = "deepseek/deepseek-v4.1-flash"
PRECIO = config.PRECIO_MILLON_TOKENS[MODELO]


def _coste_esperado(chars_prompt: int, max_tokens: int) -> float:
    pt = max(1, chars_prompt // 4)
    return (pt / 1_000_000 * PRECIO["entrada"]
            + max_tokens / 1_000_000 * PRECIO["salida"])


# --------------------------------------------------- 1. coste conservador --
def test_abandono_cuenta_coste_por_arriba() -> None:
    """El coste del abandono se calcula ANTES de enviar (prompt conocido +
    techo de salida x precio de la tabla): nunca es cero."""
    g = Gasto()
    chars = 4_000                      # -> 1.000 tokens de entrada
    coste = g.registrar_abandonada(MODELO, "x" * chars, 8_192)

    assert coste == _coste_esperado(chars, 8_192)
    assert g.llamadas_abandonadas == 1
    assert g.coste_abandonado == coste
    assert g.coste == coste            # suma al gasto total
    assert g.por_modelo[MODELO]["abandonadas"] == 1


def test_abandono_sin_tope_explicito_usa_el_estimado() -> None:
    """Sin max_tokens en la llamada (fase 1, auditoría) se estima con
    MAX_TOKENS_ESTIMADO: por arriba de lo que devuelven esas llamadas."""
    g = Gasto()
    coste = g.registrar_abandonada(MODELO, "x" * 400, None)
    assert coste == _coste_esperado(400, config.MAX_TOKENS_ESTIMADO)


def test_el_coste_del_abandono_no_es_optimista() -> None:
    """El estimado de un intento abandonado es >= el coste real de una
    respuesta llena (mismo techo): el error va siempre a favor de parar
    antes, nunca de pasarse del presupuesto."""
    g = Gasto()
    techo = config.MAX_TOKENS_FASE2
    estimado = g.registrar_abandonada(MODELO, "x" * 4_000, techo)
    # coste MEDIDO de una respuesta que agotase el techo (misma entrada)
    real = (1_000 / 1_000_000 * PRECIO["entrada"]
            + techo / 1_000_000 * PRECIO["salida"])
    assert estimado >= real


def test_el_factor_de_calibracion_ajusta_la_estimacion(monkeypatch) -> None:
    """config.FACTOR_COSTE_ABANDONADO permite calibrar con datos reales de la
    'Activity' de OpenRouter sin tocar código. Por defecto es 1.0 (peor caso):
    con la noche del 12/09 lo cobrado fue la mitad del peor caso (~0.5)."""
    assert config.FACTOR_COSTE_ABANDONADO == 1.0     # defecto = conservador
    monkeypatch.setattr(llm_mod, "FACTOR_COSTE_ABANDONADO", 0.5)
    g = Gasto()
    medio = g.registrar_abandonada(MODELO, "x" * 4_000, 8_192)
    g2 = Gasto()
    monkeypatch.setattr(llm_mod, "FACTOR_COSTE_ABANDONADO", 1.0)
    entero = g2.registrar_abandonada(MODELO, "x" * 4_000, 8_192)
    assert medio == pytest.approx(entero / 2)


# --------------------------------------------- 3. el tope sí protege -------
def test_el_abandono_cuenta_para_el_presupuesto() -> None:
    """Si el abandono hace superar --presupuesto-max, salta la parada segura
    (PresupuestoExcedido) y NO se sigue esperando al modelo."""
    g = Gasto(presupuesto_max=0.001)
    try:
        g.registrar_abandonada(MODELO, "x" * 4_000, 8_192)
    except PresupuestoExcedido as e:
        assert "abandonado" in str(e)
        return
    raise AssertionError("el abandono debe contar para el presupuesto")


# ------------------------------------------- 4. hard timeout integrado -----
def test_el_hard_timeout_registra_el_abandono(monkeypatch) -> None:
    """Extremo a extremo: una llamada que no responde a tiempo lanza
    LLMTimeoutHard Y deja el intento contabilizado en GASTO."""
    g = Gasto()
    monkeypatch.setattr(llm_mod, "GASTO", g)

    def _lenta(**kw):
        time.sleep(0.6)
        return "tarde"

    try:
        llm_mod._llamada_con_hard_timeout(
            _lenta, hard_timeout=0.05, modelo=MODELO,
            texto_entrada="x" * 4_000, max_tokens_salida=8_192)
    except LLMTimeoutHard:
        pass
    else:
        raise AssertionError("debería haber lanzado LLMTimeoutHard")

    assert g.llamadas_abandonadas == 1
    assert g.coste == _coste_esperado(4_000, 8_192)
    assert g.llamadas == 0             # no es una llamada medida


def test_sin_modelo_no_se_registra_nada(monkeypatch) -> None:
    """Compatibilidad: las llamadas internas sin modelo no contabilizan
    abandonos (no hay precio que aplicar)."""
    g = Gasto()
    monkeypatch.setattr(llm_mod, "GASTO", g)

    def _lenta(**kw):
        time.sleep(0.3)
        return "tarde"

    try:
        llm_mod._llamada_con_hard_timeout(_lenta, hard_timeout=0.05)
    except LLMTimeoutHard:
        pass
    assert g.llamadas_abandonadas == 0
    assert g.coste == 0.0


# ------------------------------------------------- 5. resumen honesto -----
def test_resumen_declara_medido_y_abandonado(monkeypatch) -> None:
    """El resumen de gasto separa lo medido de lo abandonado: así se puede
    comparar de un vistazo con la 'Activity' de OpenRouter."""
    g = Gasto()
    g.registrar_llamada(MODELO, {"prompt_tokens": 1_000,
                                "completion_tokens": 2_000})
    g.registrar_abandonada(MODELO, "x" * 4_000, 8_192)
    monkeypatch.setattr(llm_mod, "GASTO", g)

    mensajes: list[str] = []
    monkeypatch.setattr(llm_mod.ui, "log", lambda m: mensajes.append(m))
    monkeypatch.setattr(llm_mod.ui, "log_stats", lambda m: mensajes.append(m))
    monkeypatch.setattr(llm_mod.ui, "log_warn", lambda m: mensajes.append(m))
    llm_mod.resumen_gasto()

    texto = "\n".join(mensajes)
    assert "ABANDONADO" in texto
    assert f"${g.coste_abandonado:.4f}" in texto
    assert "medido" in texto and "abandonado" in texto
    assert "abandonada(s)" in texto     # línea por modelo
