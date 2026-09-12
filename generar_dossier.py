#!/usr/bin/env python3
"""
generar_dossier.py — v10.0 (TAREA 4).

Genera DOSSIER_PARA_QWEN.txt (UTF-8): el contenido COMPLETO de todos los
ficheros de texto del proyecto (*.py, *.md, *.txt, *.ps1,
requirements.txt, .env.example, .gitignore), cada uno precedido por la
línea exacta:

    === ARCHIVO: ruta/relativa/nombre.ext ===

Primera línea del dossier:

    === PROYECTO: genealogia | FECHA: <ISO> | COMMIT: <hash corto o
    "sin-git"> ===

Y al final tres secciones con la salida REAL de los comandos:

    === SECCION: PYTEST ===        (salida completa de pytest)
    === SECCION: GIT_LS_FILES ===  (salida de git ls-files)
    === SECCION: DIAGNOSTICO ===   (salida de main.py --diagnostico)

EXCLUSIONES (el dossier NUNCA contiene secretos ni datos familiares):
  - .git/, backups/, caches (__pycache__, .pytest_cache), .venv/,
    models/, documentos_propios/, .ocr_tmp/
  - *.zip, *.diff, *.gguf, *.pyc
  - el propio DOSSIER_PARA_QWEN.txt
  - TODO lo cubierto por .gitignore (se parsea y se aplica en runtime)
  - y una lista dura de seguridad (por si .gitignore se editara mal):
    .env, cache_agente.db, familia_conocida.json, arbol_*.json,
    corpus_bruto*.json, estado_investigacion.json, informe_fase1.json,
    informe_progreso.md, solicitudes.json/.md, candidatos_ensenada.json,
    registro_confirmaciones.jsonl, arbol.ged.

El diagnóstico se ejecuta con claves falsas si NO hay .env/claves reales
(nunca se incluyen claves reales en el dossier).

Uso:  python generar_dossier.py
"""
from __future__ import annotations

import fnmatch
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
DOSSIER = RAIZ / "DOSSIER_PARA_QWEN.txt"

# ----------------------------- EXCLUSIONES ----------------------------------

# Directorios SIEMPRE excluidos (por nombre, en cualquier nivel).
DIRS_EXCLUIDOS = {
    ".git", "backups", "__pycache__", ".pytest_cache", ".venv", "venv",
    "models", "documentos_propios", ".ocr_tmp", "node_modules",
    ".idea", ".vscode", ".mypy_cache", ".ruff_cache", "tool-results",
}

# Patrones de fichero SIEMPRE excluidos (además de lo que diga .gitignore).
FICHEROS_EXCLUIDOS = [
    "*.zip", "*.diff", "*.gguf", "*.pyc", "*.pyo", "*.db", "*.bak",
    "DOSSIER_PARA_QWEN.txt", "*.egg-info",
]

# Lista DURA de seguridad: aunque alguien editara .gitignore y quitara una
# de estas entradas, el dossier JAMÁS las incluye (secretos y datos
# familiares privados).
NUNCA_INCLUIR = [
    ".env",
    "cache_agente.db",
    "familia_conocida.json",
    "arbol_hallazgos.json",
    "arbol_refinado.json",
    "arbol_refinado.json.bak",
    "arbol.ged",
    "estado_investigacion.json",
    "informe_fase1.json",
    "informe_progreso.md",
    "solicitudes.json",
    "solicitudes.md",
    "candidatos_ensenada.json",
    "registro_confirmaciones.jsonl",
]

# Qué se incluye: ficheros de TEXTO del proyecto.
EXTENSIONES_INCLUIDAS = {".py", ".md", ".txt", ".ps1"}
NOMBRES_INCLUIDOS = {".env.example", ".gitignore", "requirements.txt"}


def _patrones_de_gitignore() -> list[str]:
    """Lee .gitignore y devuelve los patrones aplicables a ficheros.
    Se ignoran comentarios y reglas de directorio (los directorios ya los
    cubre DIRS_EXCLUIDOS + la lista dura)."""
    gi = RAIZ / ".gitignore"
    patrones: list[str] = []
    if not gi.exists():
        return patrones
    for linea in gi.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#"):
            continue
        if linea.startswith("!"):        # negaciones: no aplicamos lógica
            continue                     # compleja; la lista dura protege
        linea = linea.rstrip("/")
        if "/" in linea:                 # reglas con ruta (p. ej. backups/):
            pass                         # mantenemos la ruta completa
        patrones.append(linea)
    return patrones


def _excluido(nombre: str, ruta_relativa: str,
              patrones_gitignore: list[str]) -> bool:
    """True si el fichero debe quedar FUERA del dossier."""
    if nombre in NUNCA_INCLUIR:
        return True
    if ruta_relativa in NUNCA_INCLUIR:
        return True
    for patron in FICHEROS_EXCLUIDOS:
        if fnmatch.fnmatch(nombre, patron):
            return True
    for patron in patrones_gitignore:
        if (fnmatch.fnmatch(nombre, patron) or 
            fnmatch.fnmatch(ruta_relativa, patron) or
            fnmatch.fnmatch(ruta_relativa, f"{patron}/*")):
            return True
    return False


def _incluido(nombre: str) -> bool:
    if nombre in NOMBRES_INCLUIDOS:
        return True
    return Path(nombre).suffix.lower() in EXTENSIONES_INCLUIDAS


def _ficheros_del_proyecto() -> list[Path]:
    """Lista ordenada y determinista de ficheros de texto del proyecto."""
    patrones_gi = _patrones_de_gitignore()
    encontrados: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(RAIZ):
        # Podar directorios excluidos del recorrido (in place).
        dirnames[:] = sorted(d for d in dirnames if d not in DIRS_EXCLUIDOS)
        for nombre in sorted(filenames):
            ruta = Path(dirpath) / nombre
            rel = ruta.relative_to(RAIZ).as_posix()
            if _excluido(nombre, rel, patrones_gi):
                continue
            if not _incluido(nombre):
                continue
            encontrados.append(ruta)
    return sorted(encontrados, key=lambda p: p.relative_to(RAIZ).as_posix())


# ----------------------------- SECCIONES ------------------------------------

def _commit_corto() -> str:
    """Hash corto del HEAD o 'sin-git' si no hay repo/commits."""
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                           cwd=RAIZ, capture_output=True, text=True,
                           timeout=15)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    return "sin-git"


def _seccion_pytest() -> str:
    """Salida COMPLETA de la suite (pytest tests/ -q)."""
    r = subprocess.run([sys.executable, "-m", "pytest", "tests/", "-q"],
                       cwd=RAIZ, capture_output=True, text=True,
                       timeout=600)
    return (r.stdout + ("\n" + r.stderr if r.stderr.strip() else "")
            ).rstrip() + "\n"


def _seccion_git_ls_files() -> str:
    """Salida de git ls-files (o aviso si no hay repo)."""
    try:
        r = subprocess.run(["git", "ls-files"], cwd=RAIZ,
                           capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            return r.stdout.rstrip() + "\n"
        return f"(git ls-files falló: {r.stderr.strip()[:120]})\n"
    except Exception as e:
        return f"(sin repo git: {str(e)[:80]})\n"


def _seccion_diagnostico() -> str:
    """Salida de main.py --diagnostico (con claves FALSAS si no hay
    reales: nunca se exponen claves verdaderas en el dossier)."""
    env = dict(os.environ)
    env.setdefault("TAVILY_API_KEY", "clave-falsa-de-diagnostico")
    env.setdefault("OPENROUTER_API_KEY", "clave-falsa-de-diagnostico")
    try:
        r = subprocess.run([sys.executable, "main.py", "--diagnostico"],
                           cwd=RAIZ, capture_output=True, text=True,
                           env=env, timeout=180)
        salida = r.stdout + (("\n" + r.stderr) if r.stderr.strip() else "")
        return salida.rstrip() + f"\n(exit code: {r.returncode})\n"
    except Exception as e:
        return f"(diagnóstico no pudo ejecutarse: {str(e)[:80]})\n"


# ----------------------------- PRINCIPAL ------------------------------------

def main() -> int:
    fecha = datetime.now().isoformat(timespec="seconds")
    commit = _commit_corto()
    ficheros = _ficheros_del_proyecto()

    partes: list[str] = []
    partes.append(f"=== PROYECTO: genealogia | FECHA: {fecha} | "
                  f"COMMIT: {commit} ===\n\n")
    partes.append(f"{len(ficheros)} ficheros de texto del proyecto "
                  f"(excluidos: .git/, backups/, caches, .venv/, models/, "
                  f"documentos_propios/, *.zip, *.diff, este dossier y "
                  f"todo lo cubierto por .gitignore).\n\n")

    for ruta in ficheros:
        rel = ruta.relative_to(RAIZ).as_posix()
        try:
            contenido = ruta.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            partes.append(f"=== ARCHIVO: {rel} ===\n"
                          "(binario o no-UTF-8: contenido omitido)\n\n")
            continue
        partes.append(f"=== ARCHIVO: {rel} ===\n{contenido}\n\n")

    partes.append("=== SECCION: PYTEST ===\n")
    partes.append(_seccion_pytest() + "\n\n")
    partes.append("=== SECCION: GIT_LS_FILES ===\n")
    partes.append(_seccion_git_ls_files() + "\n\n")
    partes.append("=== SECCION: DIAGNOSTICO ===\n")
    partes.append(_seccion_diagnostico())

    DOSSIER.write_text("".join(partes), encoding="utf-8")

    n_lineas = DOSSIER.read_text(encoding="utf-8").count("\n")
    print(f"DOSSIER_PARA_QWEN.txt generado: {len(ficheros)} ficheros, "
          f"{n_lineas} líneas, {DOSSIER.stat().st_size:,} bytes "
          f"(commit {commit})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
