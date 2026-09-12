"""
tests/test_lanzador_v90.py — Lanzador con menú interactivo (v9.0, PARTE B).

Cubre:
  - helpers puros: parsear_indices, construir_comando, resumen_generados;
  - _pedir_entero con default y validación (input monkeypatcheado);
  - humo del proceso real: python lanzador.py --sin-chequeo con stdin
    cerrado ("0") arranca el menú y sale limpio (sin traceback).

El flujo interactivo completo (chequeo silencioso, opciones 4/6, Ctrl+C)
se verificó en vivo durante el desarrollo de la v9.0; aquí se fija lo
esencial para regresiones futuras.

Ejecución:  python -m pytest tests/test_lanzador_v90.py -q
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

import lanzador


# ============================== HELPERS PUROS ================================

def test_parsear_indices_basico():
    assert lanzador.parsear_indices("1,3,5", 10) == [1, 3, 5]
    assert lanzador.parsear_indices(" 2 ", 10) == [2]
    assert lanzador.parsear_indices("1, 3 ,5", 10) == [1, 3, 5]


def test_parsear_indices_rangos():
    assert lanzador.parsear_indices("2-4", 10) == [2, 3, 4]
    assert lanzador.parsear_indices("1,3-4", 10) == [1, 3, 4]
    assert lanzador.parsear_indices("4-2", 10) == [2, 3, 4]   # invertido
    assert lanzador.parsear_indices("1;3", 10) == [1, 3]      # con ; también


def test_parsear_indices_invalidos():
    assert lanzador.parsear_indices("", 10) == []
    assert lanzador.parsear_indices("abc", 10) == []
    assert lanzador.parsear_indices("99", 10) == []           # fuera de rango
    assert lanzador.parsear_indices("0", 10) == []            # 0 no es persona
    assert lanzador.parsear_indices("-1", 10) == []
    assert lanzador.parsear_indices("x,y,z", 10) == []


def test_parsear_indices_repeticiones():
    # Se permiten repeticiones tal cual (quien pide dos veces la misma
    # persona obtiene la selección que pidió); lo importante es el orden.
    assert lanzador.parsear_indices("1,1,3", 10) == [1, 1, 3]


def test_construir_comando():
    cmd = lanzador.construir_comando(["--ciclo", "3",
                                      "--presupuesto-max", "2.0"])
    assert cmd == "python main.py --ciclo 3 --presupuesto-max 2.0"
    # Los valores con espacios van entre comillas (nombres de personas).
    cmd2 = lanzador.construir_comando(
        ["--personas", "Isidro Merillas Panero,Obdulia Pelaz Merino"])
    assert cmd2 == ('python main.py --personas '
                    '"Isidro Merillas Panero,Obdulia Pelaz Merino"')
    assert lanzador.construir_comando([]) == "python main.py"


def test_menu_tiene_todas_las_opciones():
    """El menú cubre TODOS los flags de main.py (la 12 es --aceptar, que
    faltaba en la lista original de opciones del menú; la 13 es
    --reclasificar, añadida en la v9.2, y su texto deja claro que es
    gratis y no gasta tokens; la 14 es el resumen offline de la última
    tanda, añadida en la v10.2 junto a resumen_noche.py)."""
    # Traducción número -> acción del menú: 1..14 + 0 (salir, fuera del
    # diccionario porque no es una acción de main.py).
    assert len(lanzador.ACCIONES) == 14
    for i in range(1, 15):
        assert str(i) in lanzador.ACCIONES
    assert "0" not in lanzador.ACCIONES           # 0 = salir
    menu_formateado = lanzador.MENU.format(version=lanzador.VERSION)
    assert f"v{lanzador.VERSION}" in menu_formateado
    assert lanzador.VERSION == "10.2"   # v10.2: fixes del log de ejecución
    # El menú menciona explícitamente el flag de la 12 (--aceptar).
    assert "[--aceptar]" in lanzador.MENU
    # v9.2 — la 13 anuncia que es gratis (reclasificación offline).
    assert "no gasta tokens" in lanzador.MENU
    # v10.2 — la 14 es el resumen de la última tanda (también gratis).
    assert "Resumen de la última tanda" in lanzador.MENU


# ============================== ENTRADA INTERACTIVA =========================

def test_pedir_entero_default(monkeypatch):
    """Enter devuelve el default y lo muestra entre corchetes."""
    prompts = []
    monkeypatch.setattr("builtins.input",
                        lambda prompt="": prompts.append(prompt) or "")
    assert lanzador._pedir_entero("¿Cuántos ciclos?", 3, 1, 100) == 3
    assert prompts and "[3]" in prompts[0]


def test_pedir_entero_validacion(monkeypatch):
    """Texto inválido se re-pregunta; el rango se valida."""
    respuestas = iter(["abc", "500", "7"])
    monkeypatch.setattr("builtins.input",
                        lambda prompt="": next(respuestas))
    assert lanzador._pedir_entero("N", 3, 1, 100) == 7


def test_pedir_presupuesto(monkeypatch):
    """Enter = 2.0 (default del menú); 'sin' = None (sin límite, default
    de main.py); número válido = float (con coma decimal también)."""
    respuestas = iter(["", "sin", "1,5", "2.5"])
    monkeypatch.setattr("builtins.input",
                        lambda prompt="": next(respuestas))
    assert lanzador._pedir_presupuesto() == 2.0
    assert lanzador._pedir_presupuesto() is None
    assert lanzador._pedir_presupuesto() == 1.5
    assert lanzador._pedir_presupuesto() == 2.5


def test_confirmar(monkeypatch):
    respuestas = iter(["", "n", "s", "SI", "x", "sí"])
    monkeypatch.setattr("builtins.input",
                        lambda prompt="": next(respuestas))
    assert lanzador._confirmar("¿Ejecutar?", True) is True    # Enter = s
    assert lanzador._confirmar("¿Ejecutar?", True) is False
    assert lanzador._confirmar("¿Ejecutar?", False) is True
    assert lanzador._confirmar("¿Ejecutar?", False) is True   # SI también
    assert lanzador._confirmar("¿Ejecutar?", True) is False   # x no vale
    assert lanzador._confirmar("¿Ejecutar?", True) is True


def test_preguntar_enter_devuelve_default(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    assert lanzador._preguntar("¿X?", "default") == "default"
    monkeypatch.setattr("builtins.input", lambda prompt="": "respuesta")
    assert lanzador._preguntar("¿X?", "default") == "respuesta"


def test_confirmar_comando_cancela(monkeypatch):
    """Si el usuario responde 'n' al comando equivalente, la acción se
    cancela sin ejecutar nada (excepción AccionCancelada)."""
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    with pytest.raises(lanzador.AccionCancelada):
        lanzador._confirmar_comando(["--ciclo", "3"])


# ============================== RESUMEN DE GENERADOS ========================

def test_resumen_generados(tmp_path, monkeypatch):
    """Detecta ficheros creados/actualizados y cuenta el delta de lo
    contable (p. ej. fragmentos del corpus)."""
    monkeypatch.setattr(lanzador, "BASE_DIR", tmp_path)
    (tmp_path / "corpus_bruto.json").write_text(
        json.dumps([{"a": 1}]), encoding="utf-8")
    antes = lanzador._snapshot_salidas()

    # Nada cambió: sin líneas.
    assert lanzador.resumen_generados(antes) == []

    # El corpus crece: se reporta con el delta.
    (tmp_path / "corpus_bruto.json").write_text(
        json.dumps([{"a": 1}] * 13), encoding="utf-8")
    time.sleep(0.01)   # forzar mtime distinto (FS con granularidad 1s)
    lineas = lanzador.resumen_generados(antes)
    assert len(lineas) == 1
    assert "corpus_bruto.json" in lineas[0]
    assert "13" in lineas[0] and "+12" in lineas[0]

    # Fichero nuevo (familia con personas).
    (tmp_path / "familia_conocida.json").write_text(
        json.dumps({"personas": [{"nombre": "X"}] * 4}), encoding="utf-8")
    antes2 = lanzador._snapshot_salidas()
    lineas2 = lanzador.resumen_generados(antes)
    creados = [l for l in lineas2 if "CREADO" in l]
    assert creados and "familia_conocida.json" in creados[0]
    assert "4" in creados[0]
    assert lanzador.resumen_generados(antes2) == []


# ============================== HUMO DEL PROCESO REAL =======================

def test_lanzador_arranca_y_sale_limpio():
    """python lanzador.py --sin-chequeo con stdin '0': menú visible,
    salida limpia (código 0, sin traceback)."""
    raiz = Path(__file__).resolve().parent.parent
    resultado = subprocess.run(
        [sys.executable, "lanzador.py", "--sin-chequeo"],
        input="0\n", cwd=raiz, capture_output=True, text=True,
        timeout=120, encoding="utf-8")
    assert resultado.returncode == 0, resultado.stderr[-500:]
    assert "MENÚ PRINCIPAL" in resultado.stdout
    assert "AGENTE GENEALÓGICO v10.2" in resultado.stdout
    assert "Elige una opción" in resultado.stdout
    assert "Hasta la próxima" in resultado.stdout
    assert "Traceback" not in resultado.stderr
    assert "Traceback" not in resultado.stdout


def test_lanzador_eof_sin_traceback():
    """stdin cerrado (EOF/doble clic raro): sale con cortesía, sin
    traceback y con código 0."""
    raiz = Path(__file__).resolve().parent.parent
    resultado = subprocess.run(
        [sys.executable, "lanzador.py", "--sin-chequeo"],
        input="", cwd=raiz, capture_output=True, text=True, timeout=120)
    assert resultado.returncode == 0, resultado.stderr[-500:]
    assert "Entrada cerrada" in resultado.stdout
    assert "Traceback" not in resultado.stderr
