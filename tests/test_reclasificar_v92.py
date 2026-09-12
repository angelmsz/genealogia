"""
tests/test_reclasificar_v92.py — v9.2 PARTE 1: --reclasificar como comando.

Lo que fija:
  - reclasificar_comando() corre SOBRE DATOS YA GUARDADOS (los JSON del
    directorio de trabajo) sin re-ejecutar la fase 2.
  - CERO llamadas de red y CERO llamadas al LLM: cualquier intento de
    conectar un socket o de invocar el cliente LLM HACE FALLAR el test
    (no solo se detecta: se lanza AssertionError desde el propio mock).
  - El resultado sobrescribe arbol_refinado.json dejando un backup .bak
    del ANTERIOR, y devuelve los totales de las 3 categorías.

Ejecución:  python -m pytest tests/test_reclasificar_v92.py -q
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import main as main_mod
import utils.llm as llm_mod
from agent.evidencia import (NIVEL_CANDIDATO_FUERTE, NIVEL_COINCIDENCIA_DEBIL,
                             NIVEL_CONFIRMADO)

URL = "http://archivodeejemplo.es/partida/1"
CITA = "yo el cura bautice a Isidro Merillas Panero hijo de Nazario"


# ====================== BLOQUEO TOTAL DE RED / LLM ==========================

class IntentoDeRed(AssertionError):
    """Cualquier intento de red o de llamada LLM durante --reclasificar
    es un FALLO del test ( así lo pide la spec de la v9.2: el comando
    debe correr sin gastar ni un token ni tocar la red)."""


def _bloquear_red_y_llm(monkeypatch):
    """Intercepta TODA conexión de socket (requests, urllib, httpx y el
    SDK de OpenAI acabarán aquí) y toda llamada al cliente LLM."""
    def _conectar(sock, direccion):
        raise IntentoDeRed(f"intentó conectar un socket a {direccion}")
    def _crear(*args, **kwargs):
        raise IntentoDeRed(
            f"intentó llamar al LLM (modelo={kwargs.get('model')})")
    monkeypatch.setattr(socket.socket, "connect", _conectar)
    monkeypatch.setattr(socket.socket, "connect_ex", _conectar)
    monkeypatch.setattr(llm_mod.llm.chat.completions, "create", _crear)


# ============================== FIXTURES =====================================

def _familia() -> dict:
    return {"personas": [{
        "id": "P0001", "nombre": "Isidro Merillas Panero",
        "apellido_paterno": "Merillas", "apellido_materno": "Panero",
        "padre": "Nazario Merillas", "madre": "Obdulia Pelaz",
        "nacimiento": {"fecha_aproximada": "1870", "municipio": "Salas",
                       "provincia": "Burgos"},
        "defuncion": {},
    }]}


def _hallazgo_confirmable() -> dict:
    # Nombre + fecha + lugar + padres: >=2 datos independientes.
    return {"persona": "Isidro Merillas Panero", "tipo_evento": "bautismo",
            "fecha_valor": "1870-05-02", "fecha_precision": "exacta",
            "lugar": "Salas", "otros_nombres": ["Nazario Merillas (padre)",
                                                "Obdulia Pelaz (madre)"],
            "cita_literal": CITA, "url_fuente": URL, "confianza": "alta",
            "justificacion": "partida literal"}


def _hallazgo_debil() -> dict:
    # Apellido + zona solamente: coincidencia débil por definición.
    return {"persona": "Fulano Merillas", "tipo_evento": "mencion",
            "fecha_valor": "", "lugar": "Burgos", "otros_nombres": [],
            "cita_literal": "Fulano Merillas, vecino de Burgos",
            "url_fuente": URL, "confianza": "baja",
            "justificacion": "apellido suelto"}


def _refinado() -> dict:
    return {"personas": [],
            "personas_nuevas_candidatas": [
                {"nombre": "Fulano Merillas",
                 "motivo": "conexión no probada", "fuente_url": URL}],
            "resumen_general": "texto del LLM de la fase 2 anterior."}


def _preparar(tmp_path, monkeypatch, hallazgos, refinado=None):
    """Escribe los JSON 'ya guardados' en tmp_path y apunta main.BASE_DIR
    allí (mismo patrón que test_commit.py / test_nivel_evidencia.py)."""
    monkeypatch.setattr(main_mod, "BASE_DIR", tmp_path)
    (tmp_path / "familia_conocida.json").write_text(
        json.dumps(_familia(), ensure_ascii=False), encoding="utf-8")
    (tmp_path / "arbol_refinado.json").write_text(
        json.dumps(refinado or _refinado(), ensure_ascii=False),
        encoding="utf-8")
    (tmp_path / "arbol_hallazgos.json").write_text(
        json.dumps(hallazgos, ensure_ascii=False), encoding="utf-8")


# ============================== TESTS ========================================

def test_reclasificar_cero_red_cero_llm(tmp_path, monkeypatch):
    """LA spec de la v9.2: --reclasificar corre sobre datos ya guardados
    sin NINGUNA llamada de red ni al LLM. El propio mock lanza
    AssertionError (falla el test) si alguien intenta conectar o llamar."""
    _bloquear_red_y_llm(monkeypatch)
    _preparar(tmp_path, monkeypatch,
              [_hallazgo_confirmable(), _hallazgo_debil()])
    llamas_antes = llm_mod.GASTO.llamadas
    coste_antes = llm_mod.GASTO.coste

    totales = main_mod.reclasificar_comando(base=tmp_path)

    assert totales is not None
    assert totales[NIVEL_CONFIRMADO] == 1
    assert totales[NIVEL_COINCIDENCIA_DEBIL] == 1
    # Ni un token contado, ni un céntimo de gasto.
    assert llm_mod.GASTO.llamadas == llamas_antes
    assert llm_mod.GASTO.coste == coste_antes


def test_backup_bak_y_sobrescritura(tmp_path, monkeypatch):
    """Sobrescribe arbol_refinado.json con la reclasificación y deja un
    backup .bak con el contenido ANTERIOR exacto."""
    _bloquear_red_y_llm(monkeypatch)
    original = _refinado()
    _preparar(tmp_path, monkeypatch, [_hallazgo_confirmable()], original)

    main_mod.reclasificar_comando(base=tmp_path)

    texto_original = json.dumps(original, ensure_ascii=False)
    bak = tmp_path / "arbol_refinado.json.bak"
    assert bak.exists(), "falta el backup .bak del árbol anterior"
    assert json.loads(bak.read_text(encoding="utf-8")) == json.loads(
        texto_original)
    # El nuevo arbol_refinado.json ya no es el original: tiene las
    # secciones de evidencia.
    nuevo = json.loads((tmp_path / "arbol_refinado.json")
                       .read_text(encoding="utf-8"))
    assert "resumen_evidencia" in nuevo
    assert "RESUMEN POR NIVEL DE EVIDENCIA" in nuevo["resumen_general"]


def test_reclasificar_es_idempotente(tmp_path, monkeypatch):
    """Reclasificar dos veces no acumula bloques ni cambia los totales
    (el .bak de la 2ª pasada contiene la 1ª salida, no el original)."""
    _bloquear_red_y_llm(monkeypatch)
    _preparar(tmp_path, monkeypatch,
              [_hallazgo_confirmable(), _hallazgo_debil()])
    tot1 = main_mod.reclasificar_comando(base=tmp_path)
    salida1 = (tmp_path / "arbol_refinado.json").read_text(encoding="utf-8")
    tot2 = main_mod.reclasificar_comando(base=tmp_path)
    salida2 = (tmp_path / "arbol_refinado.json").read_text(encoding="utf-8")
    assert tot1 == tot2
    assert salida1 == salida2
    assert salida2.count("RESUMEN POR NIVEL DE EVIDENCIA") == 1


def test_sin_arbol_devuelve_none_sin_red(tmp_path, monkeypatch):
    """Sin arbol_refinado.json el comando avisa y devuelve None (main()
    lo convierte en código de salida 1) — sin trazar ninguna red."""
    _bloquear_red_y_llm(monkeypatch)
    monkeypatch.setattr(main_mod, "BASE_DIR", tmp_path)
    assert main_mod.reclasificar_comando(base=tmp_path) is None
    assert not (tmp_path / "arbol_refinado.json.bak").exists()


def test_flag_reclasificar_humo_proceso_real():
    """`python main.py --reclasificar` contra el proyecto real (sin árbol
    en esta copia): exit code 1 con el aviso amable, sin traceback. Esto
    fija que el flag existe y está cableado al dispatch de main()."""
    raiz = Path(__file__).resolve().parent.parent
    env = {**os.environ,
           "TAVILY_API_KEY": "clave-de-prueba",
           "OPENROUTER_API_KEY": "clave-de-prueba"}
    resultado = subprocess.run(
        [sys.executable, "main.py", "--reclasificar"],
        cwd=raiz, capture_output=True, text=True, timeout=120, env=env)
    assert resultado.returncode == 1, resultado.stderr[-400:]
    assert "arbol_refinado.json" in (resultado.stdout + resultado.stderr)
    assert "Traceback" not in resultado.stderr
    assert "Traceback" not in resultado.stdout
