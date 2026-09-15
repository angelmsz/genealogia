"""
tests/test_version_textos_v105.py — PROPUESTA 3: la versión que se enseña sale
de `config.VERSION`, no de textos escritos a mano (v10.4.2).

POR QUÉ
-------
El cartel de arranque decía "Agente de investigación genealógica v10.2" y la
descripción del CLI "…v9.0" con el bot ya en la 10.4.1: cada vez que se sube la
versión había que acordarse de dos sitios más, y si no, el log y la pantalla
mentían. Ahora los carteles se construyen con `VERSION` y este test impide que
vuelva a colarse un número a mano.

Ejecución:  python -m pytest tests/test_version_textos_v105.py -q
"""
from __future__ import annotations

import re
from pathlib import Path

import config

RAIZ = Path(__file__).resolve().parent.parent
FUENTE_MAIN = (RAIZ / "main.py").read_text(encoding="utf-8")


def test_version_config_es_una_version_limpia():
    assert re.fullmatch(r"\d+\.\d+(\.\d+)?", config.VERSION), config.VERSION


def test_los_carteles_usan_version_de_config():
    """El cartel del arranque y el de --probar-ocr interpolan VERSION."""
    assert ('ui.separador(f"Agente de investigación genealógica v{VERSION}")'
            in FUENTE_MAIN)
    assert 'v{VERSION} — Prueba de la cascada OCR' in FUENTE_MAIN


def test_no_hay_versiones_escritas_a_mano_en_los_carteles():
    """Ningún separador/cabecera lleva una versión literal (sin f-string)."""
    a_mano = re.findall(r'ui\.(?:separador|cabecera)\(\s*"[^"]*v\d+\.\d+',
                        FUENTE_MAIN)
    assert a_mano == [], f"carteles con versión a mano: {a_mano}"


def test_la_descripcion_del_cli_lleva_la_version():
    assert 'description=f"Agente de investigación genealógica v{VERSION} ' \
           in FUENTE_MAIN
