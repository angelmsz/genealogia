"""
v10.4.1 (tarea C) — Techos de tiempo y cortacircuitos de la fase 2.

Contexto REAL (log agente_20260912_222149.log, sobremesa):
  - 17 lotes de extracción; 5 respondieron (43/34/18/20/3 s) y 12 murieron.
  - Cada lote llevaba 3 fragmentos de hasta 12.000 car. y devolvía ~6.863
    tokens de SALIDA: la petición tardaba más que el techo de 45 s.
  - Se gastaron 36 intentos (12 lotes x 3) = 27 min de los 34 min que duró
    la fase 2, y cada intento abandonado se FACTURA en OpenRouter (el
    estimador no lo veía: ver tarea E).

Lo que fija esta suite:
  1. Un fragmento por lote (LOTE_HALLAZGOS=1): la respuesta deja de ser 3x
     más larga.
  2. La extracción y la consolidación usan el techo y los intentos PROPIOS
     de fase 2 (TIMEOUT_LLM_FASE2 / INTENTOS_FASE2), no los de fase 1.
  3. Cortacircuitos: N lotes SEGUIDOS fallidos = el modelo no está; se corta
     en vez de martillear la cola entera.
  4. El cortacircuitos es de fallos SEGUIDOS: un lote que responde lo
     reinicia (una caída puntual no puede tirar la extracción entera).

No toca red ni LLM: chat_json se sustituye por funciones de prueba.
"""

from __future__ import annotations

import json

import config
from agent import fase2


def _fragmentos(n: int) -> list[dict]:
    return [{"hash": f"h{i}", "persona": f"Persona {i}",
             "url": f"https://ejemplo.invalid/{i}",
             "texto_limpio": f"texto {i}",
             "texto_original": f"original {i}"}
            for i in range(n)]


def _payload(user: str) -> list:
    """El JSON de fragmentos dentro del prompt de extracción."""
    return json.loads(user[user.index("["):])


def _parchear(monkeypatch, fn) -> None:
    monkeypatch.setattr(fase2, "chat_json", fn)


# ---------------------------------------------------------------- 1. lotes --
def test_un_fragmento_por_lote(monkeypatch) -> None:
    """LOTE_HALLAZGOS=1: una llamada por fragmento y un solo documento por
    payload (era 3 -> respuestas de ~6.863 tokens y timeouts)."""
    tamanos: list[int] = []

    def fake(modelo, system, user, **kw):
        tamanos.append(len(_payload(user)))
        return {"hallazgos": []}

    _parchear(monkeypatch, fake)
    fase2.extraer_hallazgos(_fragmentos(4), conn=None)

    assert config.LOTE_HALLAZGOS == 1
    assert tamanos == [1, 1, 1, 1]


# ------------------------------------------------- 2. techo e intentos ------
def test_extraccion_usa_techo_e_intentos_de_fase2(monkeypatch) -> None:
    """La extracción pasa timeout= / intentos= explícitos y son los de fase 2
    (el techo de la fase 1 se quedaba corto con respuestas largas)."""
    vistos: list[dict] = []

    def fake(modelo, system, user, **kw):
        vistos.append(kw)
        return {"hallazgos": []}

    _parchear(monkeypatch, fake)
    fase2.extraer_hallazgos(_fragmentos(2), conn=None)

    assert len(vistos) == 2
    for kw in vistos:
        assert kw["timeout"] == config.TIMEOUT_LLM_FASE2
        assert kw["intentos"] == config.INTENTOS_FASE2
    # el techo de fase 2 tiene que ser MÁS largo que el general: si no, no
    # arreglamos nada (era el bug: un único 45 s para todo).
    assert config.TIMEOUT_LLM_FASE2 > config.TIMEOUT_LLM


def test_consolidacion_usa_techo_e_intentos_de_fase2(monkeypatch) -> None:
    """La consolidación es la llamada MÁS GRANDE de la noche (todos los
    hallazgos + toda la memoria familiar): murió 3 veces a 45 s. Debe llevar
    el mismo techo largo."""
    vistos: list[dict] = []

    def fake(modelo, system, user, **kw):
        vistos.append(kw)
        return {"resumen_general": "", "personas_nuevas_candidatas": []}

    _parchear(monkeypatch, fake)
    fase2.consolidar({"personas": []}, [{"persona": "X"}])

    assert len(vistos) == 1
    assert vistos[0]["timeout"] == config.TIMEOUT_LLM_FASE2
    assert vistos[0]["intentos"] == config.INTENTOS_FASE2


# ------------------------------------------------- 3. cortacircuitos --------
def test_cortacircuitos_tras_n_fallos_seguidos(monkeypatch) -> None:
    """10 lotes, todos fallidos: se hacen exactamente LOTES_FALLIDOS_CORTE
    intentos y el resto de la cola NO se martillea."""
    intentos: list[int] = []

    def fake(*a, **kw):
        intentos.append(1)
        raise RuntimeError("LLM inaccesible tras 2 intentos: "
                           "LLM no respondió en 120.0s")

    _parchear(monkeypatch, fake)
    hallazgos = fase2.extraer_hallazgos(_fragmentos(10), conn=None)

    assert hallazgos == []
    assert config.LOTES_FALLIDOS_CORTE >= 2     # un solo fallo no corta nada
    assert len(intentos) == config.LOTES_FALLIDOS_CORTE


def test_lote_bueno_reinicia_el_contador(monkeypatch) -> None:
    """Fallos SEGUIDOS: con una respuesta buena en medio (aunque traiga 0
    hallazgos) nunca se llega al corte y se procesan los 6 lotes."""
    llamadas = {"n": 0}

    def fake(*a, **kw):
        llamadas["n"] += 1
        if llamadas["n"] in (1, 2, 4, 5):
            raise RuntimeError("caída puntual")
        return {"hallazgos": []}

    _parchear(monkeypatch, fake)
    fase2.extraer_hallazgos(_fragmentos(6), conn=None)

    assert llamadas["n"] == 6


def test_el_presupuesto_no_se_confunde_con_un_fallo_de_lote(monkeypatch) -> None:
    """PresupuestoExcedido sube (parada segura): no es un fallo de lote y no
    debe activar el cortacircuitos ni tragarse la excepción."""
    from utils.llm import PresupuestoExcedido

    def fake(*a, **kw):
        raise PresupuestoExcedido("gasto acumulado $1.51 >= presupuesto $1.50")

    _parchear(monkeypatch, fake)
    try:
        fase2.extraer_hallazgos(_fragmentos(5), conn=None)
    except PresupuestoExcedido:
        return
    raise AssertionError("PresupuestoExcedido debe propagarse (parada segura)")
