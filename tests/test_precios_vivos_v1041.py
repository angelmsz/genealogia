"""
tests/test_precios_vivos_v1041.py — v10.4.1: el estimador usa el precio REAL
de OpenRouter, consultado al arrancar, x margen de seguridad.

QUÉ PROBLEMA ARREGLA
En la noche del 12/09 el bot dijo $0.1344 y OpenRouter cobró $0.38. Parte del
desfase eran los intentos abandonados por timeout (tarea E) y parte, el precio
de partida: la tabla de config.py es una foto escrita a mano. Desde esta mejora,
cada ejecución que puede gastar pregunta a OpenRouter cuánto cuesta de verdad
cada modelo configurado y usa ese precio multiplicado por
MARGEN_PRECIO_SEGURIDAD (1.1 por defecto) para el estimador y para el tope de
--presupuesto-max.

LO QUE FIJA ESTA SUITE
  1. El precio vivo (con margen) manda en el estimador, en el tope de
     presupuesto y en la estimación de los intentos abandonados.
  2. Sin precio vivo se usa la tabla de config (comportamiento de antes).
  3. La consulta al catálogo: URL, timeout de 5 s, parseo de pricing.prompt /
     pricing.completion a USD por millón.
  4. Fallos (timeout, HTTP, modelo sin precio, catálogo vacío): ({}, motivo)
     y NUNCA un precio inventado.
  5. main._fijar_precios_del_dia deja en el log la línea explícita:
     "precio usado: VIVO (consultado HH:MM:SS) x margen 1.1" o
     "precio usado: CONFIG (consulta falló: motivo)" — esta última con [!] en
     pantalla — y no revienta aunque el consultor lance.
  6. --diagnostico y el arranque comparten el HTTP y el parseo (sin duplicar).

Sin red real: el GET del catálogo se sustituye por dobles.
"""

from __future__ import annotations

import inspect
import re

import pytest
import requests

import config
import main
from agent import gedcom
from utils.llm import Gasto, PresupuestoExcedido

MODELO = "deepseek/deepseek-v4.1-flash"      # está en la tabla de config.py

CATALOGO = {"data": [
    {"id": MODELO,
     "pricing": {"prompt": "0.0000003", "completion": "0.0000012"}},
    {"id": "otro/modelo",
     "pricing": {"prompt": "0.0000001", "completion": "0.0000002"}},
]}


class _Respuesta:
    """Respuesta HTTP falsa (la API mínima que usa el cliente)."""

    def __init__(self, status: int = 200, payload=None) -> None:
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code}")


@pytest.fixture(autouse=True)
def _precios_limpios():
    """Cada test empieza y termina sin precios vivos: nada se filtra entre
    tests (ni a los demás ficheros de la suite)."""
    config.limpiar_precios_vivos()
    yield
    config.limpiar_precios_vivos()


def _mock_catalogo(monkeypatch, payload, visto: dict | None = None):
    def _get(url, timeout=None, **kwargs):
        if visto is not None:
            visto["url"] = url
            visto["timeout"] = timeout
        return _Respuesta(200, payload)
    monkeypatch.setattr(gedcom.SESSION, "get", _get)


# ==================== 1. EL PRECIO VIVO MANDA EN EL ESTIMADOR ==============

def test_el_estimador_usa_el_precio_vivo_con_margen():
    """1 M de entrada + 1 M de salida a $1/$4 con margen 1.1 = $5.50 (con la
    tabla de config serían $1.50: el precio vivo se está usando de verdad)."""
    config.fijar_precios_vivos({MODELO: {"entrada": 1.0, "salida": 4.0}},
                               margen=1.1)
    g = Gasto()
    g.registrar_llamada(MODELO, {"prompt_tokens": 1_000_000,
                                "completion_tokens": 1_000_000})
    assert g.coste == pytest.approx(5.5)
    tabla = config.PRECIO_MILLON_TOKENS[MODELO]
    assert g.coste != pytest.approx(tabla["entrada"] + tabla["salida"])


def test_sin_precios_vivos_manda_la_tabla_de_config():
    """El camino de siempre (y el respaldo): la tabla de config.py."""
    g = Gasto()
    g.registrar_llamada(MODELO, {"prompt_tokens": 1_000_000,
                                "completion_tokens": 1_000_000})
    tabla = config.PRECIO_MILLON_TOKENS[MODELO]
    assert g.coste == pytest.approx(tabla["entrada"] + tabla["salida"])


def test_el_tope_de_presupuesto_cuenta_con_el_precio_vivo():
    """--presupuesto-max se controla con el gasto estimado: con el precio vivo
    (x margen) salta antes."""
    config.fijar_precios_vivos({MODELO: {"entrada": 1.0, "salida": 4.0}},
                               margen=2.0)
    g = Gasto(presupuesto_max=1.0)
    with pytest.raises(PresupuestoExcedido):
        g.registrar_llamada(MODELO, {"prompt_tokens": 100_000,
                                    "completion_tokens": 100_000})


def test_los_intentos_abandonados_tambien_usan_el_precio_vivo():
    """El coste conservador de un abandono (tarea E) usa el MISMO precio
    activo: si no, el tope volvería a mentir por el otro lado."""
    config.fijar_precios_vivos({MODELO: {"entrada": 1.0, "salida": 1.0}},
                               margen=1.0)
    g = Gasto()
    coste = g.registrar_abandonada(MODELO, "x" * 4_000, 1_000)
    assert coste == pytest.approx(1_000 / 1_000_000 * 1.0
                                  + 1_000 / 1_000_000 * 1.0)


def test_precio_activo_nunca_devuelve_vacio():
    assert config.precio_activo("modelo/desconocido") == \
        config.PRECIO_POR_DEFECTO
    assert config.precio_activo(MODELO) == config.PRECIO_MILLON_TOKENS[MODELO]


# ==================== 2. LA CONSULTA AL CATÁLOGO ===========================

def test_consulta_la_url_y_el_timeout_correctos(monkeypatch):
    """GET a /api/v1/models con el tope de 5 s que pidió el usuario."""
    visto: dict = {}
    _mock_catalogo(monkeypatch, CATALOGO, visto)

    precios, motivo = gedcom.consultar_precios_vivos([MODELO], timeout=5.0)

    assert visto["url"].endswith("/api/v1/models")
    assert visto["timeout"] == 5.0
    assert motivo == ""
    assert precios[MODELO]["entrada"] == pytest.approx(0.30)
    assert precios[MODELO]["salida"] == pytest.approx(1.20)


# Catálogo REAL (consultado el 2026-09-13) del default de fase 2: la tarifa por
# franjas horarias va dentro de pricing.overrides. Valle = 0.15/0.60;
# punta = 0.30/1.20 (que es justo la tabla de config.py).
CATALOGO_CON_FRANJAS = {"data": [{
    "id": "deepseek/deepseek-v4.1-flash",
    "pricing": {
        "prompt": "0.00000015", "completion": "0.0000006",
        "overrides": [
            {"utc_days": ["saturday", "sunday"],
             "prompt": "0.00000015", "completion": "0.0000006"},
            {"utc_days": ["monday", "tuesday", "wednesday", "thursday",
                          "friday"],
             "utc_start": 100, "utc_end": 400,
             "prompt": "0.0000003", "completion": "0.0000012"},
            {"utc_days": ["monday", "tuesday", "wednesday", "thursday",
                          "friday"],
             "utc_start": 600, "utc_end": 1000,
             "prompt": "0.0000003", "completion": "0.0000012"},
        ]}}]}


def test_tarifa_por_franjas_se_toma_el_peor_caso(monkeypatch):
    """Si el modelo tiene tarifa horaria, se usa la PUNTA (el máximo), no la
    base: una noche puede caer en cualquiera de las franjas y quedarse con el
    valle contaría la mitad (era el riesgo al leer solo pricing.prompt)."""
    _mock_catalogo(monkeypatch, CATALOGO_CON_FRANJAS)
    precios, motivo = gedcom.consultar_precios_vivos(
        ["deepseek/deepseek-v4.1-flash"])
    assert precios["deepseek/deepseek-v4.1-flash"]["entrada"] == \
        pytest.approx(0.30)                      # punta, no valle (0.15)
    assert precios["deepseek/deepseek-v4.1-flash"]["salida"] == \
        pytest.approx(1.20)                      # punta, no valle (0.60)
    assert motivo == ""


def test_sin_franjas_se_usa_el_precio_base(monkeypatch):
    _mock_catalogo(monkeypatch, CATALOGO)
    precios, _ = gedcom.consultar_precios_vivos([MODELO])
    assert precios[MODELO]["entrada"] == pytest.approx(0.30)   # solo la base


def test_franjas_ilegibles_no_rompen(monkeypatch):
    """Franjas con datos raros: se usan las legibles y nunca se inventa."""
    catalogo = {"data": [{"id": "m/x", "pricing": {
        "prompt": "0.000001", "completion": "0.000002",
        "overrides": [{"prompt": None, "completion": "x"}, "no soy un dict"]}}]}
    _mock_catalogo(monkeypatch, catalogo)
    precios, motivo = gedcom.consultar_precios_vivos(["m/x"])
    assert precios["m/x"] == {"entrada": pytest.approx(1.0),
                              "salida": pytest.approx(2.0)}
    assert motivo == ""


def test_solo_devuelve_los_modelos_pedidos(monkeypatch):
    _mock_catalogo(monkeypatch, CATALOGO)
    precios, _ = gedcom.consultar_precios_vivos([MODELO])
    assert set(precios) == {MODELO}


def test_timeout_devuelve_vacio_con_motivo(monkeypatch):
    def _get(url, timeout=None, **kwargs):
        raise requests.exceptions.ReadTimeout("lento")
    monkeypatch.setattr(gedcom.SESSION, "get", _get)

    precios, motivo = gedcom.consultar_precios_vivos([MODELO], timeout=5.0)

    assert precios == {}
    assert "ReadTimeout" in motivo


def test_error_http_devuelve_vacio_con_motivo(monkeypatch):
    monkeypatch.setattr(gedcom.SESSION, "get",
                        lambda *a, **k: _Respuesta(503, {}))
    precios, motivo = gedcom.consultar_precios_vivos([MODELO])
    assert precios == {}
    assert "503" in motivo


def test_modelo_sin_precio_no_se_inventa(monkeypatch):
    """Un modelo con pricing vacío no entra: se devuelve el otro y se dice
    cuál falta (quien llama cae a config para ese)."""
    catalogo = {"data": [CATALOGO["data"][0], {"id": "sin/precio",
                                              "pricing": {}}]}
    _mock_catalogo(monkeypatch, catalogo)
    precios, motivo = gedcom.consultar_precios_vivos([MODELO, "sin/precio"])
    assert set(precios) == {MODELO}
    assert "sin/precio" in motivo


def test_catalogo_vacio_no_inventa(monkeypatch):
    _mock_catalogo(monkeypatch, {"data": []})
    precios, motivo = gedcom.consultar_precios_vivos([MODELO])
    assert precios == {}
    assert MODELO in motivo


def test_sin_modelos_no_consulta_la_red(monkeypatch):
    def _get(*a, **k):
        raise AssertionError("no debería consultar la red")
    monkeypatch.setattr(gedcom.SESSION, "get", _get)
    assert gedcom.consultar_precios_vivos([]) == ({},
                                                 "no se pidió ningún modelo")


# ==================== 3. EL MARGEN =========================================

def test_margen_por_defecto_1_1():
    assert config.MARGEN_PRECIO_SEGURIDAD == 1.1


def test_fijar_precios_aplica_margen_y_deja_meta():
    meta = config.fijar_precios_vivos(
        {MODELO: {"entrada": 1.0, "salida": 2.0}}, margen=1.1,
        hora="12:34:56")
    assert config.PRECIOS_VIVOS[MODELO]["entrada"] == pytest.approx(1.1)
    assert config.PRECIOS_VIVOS[MODELO]["salida"] == pytest.approx(2.2)
    assert meta == {"margen": 1.1, "hora": "12:34:56", "modelos": [MODELO]}


def test_sin_margen_explicito_usa_el_de_config():
    config.fijar_precios_vivos({MODELO: {"entrada": 1.0, "salida": 1.0}})
    assert config.PRECIOS_VIVOS[MODELO]["entrada"] == \
        pytest.approx(config.MARGEN_PRECIO_SEGURIDAD)


def test_entradas_incompletas_se_ignoran():
    """Ante un precio a medias, mejor la tabla de config que un número raro."""
    config.fijar_precios_vivos({MODELO: {"entrada": 1.0}, "x/y": None})
    assert config.PRECIOS_VIVOS == {}


# ==================== 4. EL ARRANQUE (main) ================================

def test_main_loguea_precio_vivo_con_hora_y_margen(monkeypatch, capsys):
    monkeypatch.setattr(
        main, "consultar_precios_vivos",
        lambda modelos, timeout=None: ({MODELO: {"entrada": 0.5,
                                                "salida": 2.0}}, ""))
    main._fijar_precios_del_dia()
    salida = capsys.readouterr().out

    assert re.search(r"precio usado: VIVO \(consultado \d{2}:\d{2}:\d{2}\) "
                     r"x margen 1\.1", salida)
    assert config.PRECIOS_VIVOS[MODELO]["entrada"] == pytest.approx(0.55)
    # y el detalle de lo que usará el estimador, ya con margen
    assert "por millón de tokens, ya con margen" in salida


def test_main_avisa_con_exclamacion_y_usa_config_si_falla(monkeypatch, capsys):
    monkeypatch.setattr(
        main, "consultar_precios_vivos",
        lambda modelos, timeout=None: ({}, "ReadTimeout: lento"))
    main._fijar_precios_del_dia()
    salida = capsys.readouterr().out

    assert "[!]" in salida                                   # aviso en pantalla
    assert "precio usado: CONFIG (consulta falló: ReadTimeout: lento)" in salida
    assert config.PRECIOS_VIVOS == {}                        # tabla de config


def test_main_no_revienta_si_el_consultor_lanza(monkeypatch, capsys):
    """La consulta de precios NUNCA puede tumbar una investigación."""
    def _explota(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(main, "consultar_precios_vivos", _explota)
    main._fijar_precios_del_dia()          # no debe lanzar

    salida = capsys.readouterr().out
    assert "precio usado: CONFIG" in salida
    assert "RuntimeError" in salida
    assert config.PRECIOS_VIVOS == {}


def test_el_arranque_usa_el_timeout_de_config_y_los_dos_modelos(monkeypatch):
    visto: dict = {}

    def _fake(modelos, timeout=None):
        visto["modelos"] = list(modelos)
        visto["timeout"] = timeout
        return {}, "x"

    monkeypatch.setattr(main, "consultar_precios_vivos", _fake)
    main._fijar_precios_del_dia()

    assert visto["modelos"] == [config.MODELO_FASE1, config.MODELO_FASE2]
    assert visto["timeout"] == config.PRECIO_TIMEOUT_S
    assert visto["timeout"] <= 5.0         # como máximo 5 s antes de arrancar


def test_los_modos_que_no_gastan_salen_antes_de_la_consulta():
    """--diagnostico/--frontera/--probar-ocr/--aceptar/--reclasificar terminan
    (SystemExit) ANTES de la línea que consulta los precios: no pagan la
    consulta. Se comprueba sobre el código de main(), que es donde está el
    orden."""
    src = inspect.getsource(main.main)
    pos_consulta = src.index("_fijar_precios_del_dia()")
    for salida_temprana in ("if args.diagnostico:",
                            "if args.frontera:",
                            "if args.aceptar:",
                            "if args.probar_ocr:",
                            "if args.reclasificar:"):
        assert src.index(salida_temprana) < pos_consulta


# ==================== 5. SIN DUPLICAR LÓGICA ===============================

def test_diagnostico_reutiliza_la_misma_consulta():
    """--diagnostico y el arranque comparten el HTTP y el parseo: si OpenRouter
    cambia el formato, se arregla en un solo sitio."""
    src = inspect.getsource(gedcom.diagnostico)
    assert "catalogo_openrouter(" in src
    assert "_precios_del_catalogo(" in src
    assert "api/v1/models" not in src          # ni una URL a mano
