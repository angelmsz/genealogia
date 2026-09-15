"""
tests/test_lanzador_shim_v105.py — R-05: `lanzador.py` ya no es un menú, es un
aviso con redirección a `menu_principal.py` (v10.4.2).

POR QUÉ
-------
El menú antiguo se retiró (llamaba a las fases en su propio proceso, saltándose
la validación de claves y los precios vivos, y duplicaba lógica). Pero borrarlo
sin más rompe cualquier acceso directo, alias o script que lo llamara. Este shim
avisa y abre el menú nuevo con los mismos argumentos, y acepta el antiguo
`--sin-chequeo` sin quejarse (el menú nuevo no hace chequeo de arranque).

Ejecución:  python -m pytest tests/test_lanzador_shim_v105.py -q
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import lanzador

RAIZ = Path(__file__).resolve().parent.parent


def _entorno_utf8() -> dict:
    """Entorno del hijo forzando UTF-8 (FALLO 3 del sobremesa).

    En Windows la salida por defecto es cp1252 (0xFA = 'ú'): sin esto, el texto
    del aviso no es UTF-8 válido y quien lee esperando UTF-8 se encuentra un
    error o un texto roto.
    """
    entorno = dict(os.environ)
    entorno["PYTHONIOENCODING"] = "utf-8"
    entorno["PYTHONUTF8"] = "1"
    return entorno


# ============================== AYUDANTES ==================================

def test_quitar_flags_antiguos():
    """El flag del menú viejo se ignora; lo demás pasa tal cual."""
    utiles, ignorados = lanzador.quitar_flags_antiguos(
        ["--sin-chequeo", "--sin-pausa", "--sin-chequeo"])
    assert utiles == ["--sin-pausa"]
    assert ignorados == ["--sin-chequeo", "--sin-chequeo"]

    assert lanzador.quitar_flags_antiguos([]) == ([], [])


def test_sin_menu_no_revienta(monkeypatch, tmp_path, capsys):
    """Si el menú nuevo no está al lado, avisa y sale con 1 (sin traceback)."""
    monkeypatch.setattr(lanzador, "MENU", tmp_path / "no_existe.py")

    assert lanzador.main([]) == 1

    salida = capsys.readouterr().out
    assert "No encuentro" in salida and "junto a este script" in salida
    assert "Traceback" not in salida


# ============================== PROCESO REAL ================================

def test_el_shim_avisa_y_abre_el_menu_nuevo():
    """Proceso real: aviso de retirada + menú nuevo funcionando.

    Se le pasa el flag antiguo --sin-chequeo (debe tolerarse) y los del menú
    nuevo; stdin '0' cierra el menú.

    FALLO 3 del sobremesa: el hijo va con PYTHONIOENCODING=utf-8 y aquí se lee
    con encoding="utf-8" + errors="replace". Antes, con la salida en cp1252, el
    texto no era UTF-8 válido y stdout acababa inservible (None).
    """
    resultado = subprocess.run(
        [sys.executable, "lanzador.py", "--sin-chequeo", "--sin-pausa",
         "--sin-log"],
        input="0\n", cwd=RAIZ, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120,
        env=_entorno_utf8())

    assert resultado.returncode == 0, resultado.stderr[-500:]
    assert resultado.stdout, "stdout vacío/None: problema de codificación"
    assert "\ufffd" not in resultado.stdout, "salida no decodificable como UTF-8"
    assert "se retiró en la v10.4.2" in resultado.stdout
    assert "--sin-chequeo ya no hace falta" in resultado.stdout
    assert "MENÚ DE TAREAS" in resultado.stdout          # es el menú nuevo
    assert "Elige una opción" in resultado.stdout
    assert "Traceback" not in resultado.stdout
    assert "Traceback" not in resultado.stderr


def test_el_shim_emite_utf8_sin_ayuda_del_entorno():
    """FALLO 3 (el arreglo de verdad): el aviso del shim sale en UTF-8 AUNQUE el
    hijo no reciba PYTHONIOENCODING, es decir con una consola cp1252.

    Sin el `reconfigure` del shim, las tildes salían como bytes cp1252 (0xFA) y
    quien leía esperando UTF-8 se encontraba un error o texto roto.
    """
    entorno = dict(os.environ)
    entorno.pop("PYTHONIOENCODING", None)
    entorno.pop("PYTHONUTF8", None)

    resultado = subprocess.run(
        [sys.executable, "lanzador.py", "--sin-pausa", "--sin-log"],
        input="0\n", cwd=RAIZ, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120, env=entorno)

    assert resultado.returncode == 0, resultado.stderr[-500:]
    assert "se retiró en la v10.4.2" in resultado.stdout
    assert "\ufffd" not in resultado.stdout


def test_el_shim_avisa_con_la_variable_de_entorno(monkeypatch, capsys):
    """LANZADOR_SIN_CHEQUEO=1 (la vía antigua) también se avisa y se ignora."""
    monkeypatch.setenv("LANZADOR_SIN_CHEQUEO", "1")
    monkeypatch.setattr(lanzador.subprocess, "call", lambda *a, **k: 0)

    assert lanzador.main(["--sin-pausa"]) == 0
    assert "--sin-chequeo ya no hace falta" in capsys.readouterr().out
