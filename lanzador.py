#!/usr/bin/env python3
"""
lanzador.py — SHIM de compatibilidad (v10.4.2, R-05).

QUÉ ES ESTO: un cartel con redirección, no un menú. El menú antiguo vivía en
este fichero y se retiró en la v10.4.2 porque llamaba a las fases en su propio
proceso —saltándose la validación de claves y la consulta de precios vivos— y
duplicaba lógica (preguntas, presupuesto, resumen de ficheros). El menú bueno es
`menu_principal.py` (y `Menu.bat` en Windows).

PARA QUÉ SIGUE EXISTIENDO: para que los accesos directos, alias y scripts que
llamaban a `lanzador.py` no se rompan. Avisa del cambio y lanza el menú nuevo
con los mismos argumentos. Se acepta (y se ignora, avisando) el antiguo
`--sin-chequeo` y la variable `LANZADOR_SIN_CHEQUEO=1`: el menú nuevo no hace
chequeo de arranque, así que ya no hacen falta.

Uso:
    python lanzador.py                 (equivale a: python menu_principal.py)
    python lanzador.py --sin-pausa     (los argumentos se pasan al menú nuevo)
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
MENU = BASE_DIR / "menu_principal.py"
# Flags del menú ANTIGUO que se aceptan por compatibilidad y se ignoran.
FLAGS_ANTIGUOS = ("--sin-chequeo",)


def quitar_flags_antiguos(argv: list[str]) -> tuple[list[str], list[str]]:
    """Separa los flags del menú viejo (ignorados) de los que sí valen.

    Devuelve (argumentos_para_el_menu_nuevo, flags_ignorados).
    """
    utiles: list[str] = []
    ignorados: list[str] = []
    for argumento in argv:
        if argumento in FLAGS_ANTIGUOS:
            ignorados.append(argumento)
        else:
            utiles.append(argumento)
    return utiles, ignorados


def main(argv: list[str] | None = None) -> int:
    """Avisa del cambio y ejecuta el menú nuevo con los mismos argumentos."""
    argumentos = list(sys.argv[1:] if argv is None else argv)
    if not MENU.is_file():
        print(f"[x] No encuentro {MENU.name} junto a este script ({BASE_DIR}).")
        print("    Este fichero solo redirige al menú: sin él no hay nada que "
              "abrir.")
        return 1
    utiles, ignorados = quitar_flags_antiguos(argumentos)
    print("=" * 62)
    print(" AVISO: el menú antiguo (lanzador.py) se retiró en la v10.4.2.")
    print("        Ahora se abre menu_principal.py (o Menu.bat en Windows).")
    if ignorados or os.getenv("LANZADOR_SIN_CHEQUEO"):
        print("        (--sin-chequeo ya no hace falta: el menú nuevo no hace "
              "chequeo de arranque)")
    print("=" * 62)
    try:
        return subprocess.call([sys.executable, str(MENU), *utiles],
                               cwd=str(BASE_DIR))
    except KeyboardInterrupt:
        print("\nCancelado.")
        return 130
    except OSError as e:
        print(f"[x] No se pudo lanzar el menú: {str(e)[:120]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
