"""
tests/conftest.py — Preparación del entorno para la suite de pruebas (v4.2).

Punto 11 del informe de fallos: "para un programa cuyo resultado son
afirmaciones sobre personas reales de tu familia, esto es lo que más falta
hace: un conjunto de comprobaciones que se ejecuten solas y avisen cuando
algo se rompe".

Qué hace este archivo:
  1. Añade la raíz del proyecto a sys.path (los tests importan config,
     utils..., agent... como módulos del proyecto).
  2. Fija claves de API de mentira ANTES de importar config (config.py
     hace SystemExit si faltan: correcto en producción, molesto en tests).
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

# Claves de prueba: config.py exige que existan (SystemExit si no).
os.environ.setdefault("TAVILY_API_KEY", "clave-de-prueba")
os.environ.setdefault("OPENROUTER_API_KEY", "clave-de-prueba")


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
