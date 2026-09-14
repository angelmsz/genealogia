"""
tests/conftest.py — Preparación del entorno para la suite de pruebas (v4.2).

Punto 11 del informe de fallos: "para un programa cuyo resultado son
afirmaciones sobre personas reales de tu familia, esto es lo que más falta
hace: un conjunto de comprobaciones que se ejecuten solas y avisen cuando
algo se rompe".

Qué hace este archivo:
  1. Añade la raíz del proyecto a sys.path (los tests importan config,
     utils..., agent... como módulos del proyecto).
  2. Deja una clave de OpenRouter de mentira (ver abajo por qué sigue haciendo
     falta) y NADA MÁS: desde la v10.4.2 config.py no exige claves al
     importarse, así que la de Tavily ya no se inventa.
  3. Inyecta módulos falsos de openai/tavily SOLO si no están instalados:
     la suite es de LÓGICA (identidad, dedupe, auditoría, GEDCOM...),
     nunca llama a la red. Así puede correr en cualquier máquina.

Ejecución:  python -m pytest tests/ -q     (desde la raíz del proyecto)
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

# v10.4.2 (arreglo 1) — Claves de mentira: ahora solo UNA, y no por el bot.
#
# config.py YA NO exige claves al importarse (la exigencia vive en
# config.validar_credenciales_api(), que solo llaman los flujos que gastan).
# Por eso la TAVILY_API_KEY de mentira SE HA QUITADO: verificado que la suite
# completa pasa con ella vacía. El aislamiento de los tests de subproceso lo
# hace tests/harness_aislado.py (parchea config en memoria, BASE_DIR temporal).
#
# OPENROUTER_API_KEY SÍ sigue haciendo falta, y el motivo no es el bot: hay
# tests que parchean el método sobre el objeto cliente REAL, p. ej.
#     monkeypatch.setattr(llm_mod.llm.chat.completions, "create", _crear)
# y el SDK de OpenAI construye ese cliente exigiendo una clave no vacía. No se
# usa para ninguna llamada real (esos tests bloquean la red a propósito): solo
# evita que el constructor falle. Cuando esos tests parcheen el proxy `llm` en
# vez del cliente, esta línea se podrá borrar también.
os.environ.setdefault("OPENROUTER_API_KEY", "clave-de-prueba")

# v10.4 (P0) — los tests NUNCA escriben el registro de ejecución en el disco
# del usuario: algunos tests llaman a main.main() (p. ej. el de --probar-ocr)
# y sin esto dejarían ficheros en logs/. Se asigna (no setdefault) para que
# valga aunque el entorno del usuario tenga LOG_AGENTE=true.
os.environ["LOG_AGENTE"] = "false"


def _asegurar_modulo(nombre: str, fabricar) -> None:
    """Inyecta un módulo falso SOLO si el real no está instalado."""
    if nombre in sys.modules:
        return
    try:
        __import__(nombre)
    except ImportError:
        sys.modules[nombre] = fabricar()


def _fabricar_openai() -> types.ModuleType:
    mod = types.ModuleType("openai")

    class OpenAI:  # el cliente real no se usa en ningún test
        def __init__(self, *args, **kwargs):
            self.__dict__.update(kwargs)   # p. ej. max_retries

    mod.OpenAI = OpenAI
    return mod


def _fabricar_tavily() -> types.ModuleType:
    mod = types.ModuleType("tavily")

    class TavilyClient:
        def __init__(self, *args, **kwargs):
            pass

        def search(self, **kwargs):
            return {"results": []}

    mod.TavilyClient = TavilyClient
    return mod


_asegurar_modulo("openai", _fabricar_openai)
_asegurar_modulo("tavily", _fabricar_tavily)
