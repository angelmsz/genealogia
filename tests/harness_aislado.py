"""
tests/harness_aislado.py — Ejecutar comandos del bot SIN tocar el proyecto.

POR QUÉ EXISTE (incidente del 13/09/2026)
-----------------------------------------
Un test lanzó ``main.py`` como subproceso poniendo las claves de API a cadena
vacía para simular "máquina sin .env". En el MISMO proceso eso funciona, pero
**Windows no pasa a los hijos las variables de entorno vacías: las elimina**,
así que el hijo no veía "clave vacía", veía "no hay variable": ``config.py``
cargó el ``.env`` REAL y el comando se ejecutó de verdad (fase 2 completa,
con coste y escribiendo los ficheros de estado del usuario).

Este harness cierra esa puerta para siempre. Todo hijo que se lance con él:

  1. **No lee el .env**: se parchea ``config`` EN MEMORIA (``config.
     TAVILY_API_KEY = ""``...) antes de importar ``main``. No depende de
     variables de entorno, así que el fallo de Windows no puede repetirse.
  2. **No escribe en el proyecto**: ``config.BASE_DIR`` apunta a ``tmp_path``.
     Como los módulos del bot hacen ``from config import BASE_DIR`` DESPUÉS
     del parche, todos trabajan en la carpeta temporal (incluida la BD
     ``cache_agente.db`` y ``logs/``).
  3. **No escapa por rutas relativas**: el hijo corre con ``cwd=tmp_path``.
  4. **Se puede auditar**: ``huellas_de_estado()`` da el sha256 de cada
     fichero de estado para comparar antes/después y afirmar en el test que
     el hijo no ha tocado NADA del proyecto.

Uso típico en un test::

    from tests.harness_aislado import huellas_de_estado, lanzar_sin_claves

    antes = huellas_de_estado()
    r = lanzar_sin_claves(["--fase", "2"], tmp_path)
    assert r.returncode == 2          # muere en el punto de validación
    assert huellas_de_estado() == antes
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

# Entradas que un flujo puede LEER. Se copian a la carpeta temporal para poder
# probar con datos reales sin abrir jamás los del usuario.
ENTRADAS = ("corpus_bruto.json", "informe_fase1.json", "familia_conocida.json")

# Ficheros del proyecto que ningún test puede modificar (ni crear).
VIGILADOS = (
    "arbol_hallazgos.json",
    "arbol_refinado.json",
    "arbol.ged",
    "estado_investigacion.json",
    "informe_progreso.md",
    "solicitudes.json",
    "solicitudes.md",
    "candidatos_ensenada.json",
    "registro_confirmaciones.jsonl",
    "familia_conocida.json",
    "corpus_bruto.json",
    "informe_fase1.json",
    "cache_agente.db",
    # Backups que introduce la protección v10.4.2: tampoco deben aparecer.
    "arbol_hallazgos.json.bak",
    "arbol_refinado.json.bak",
    "arbol.ged.bak",
)

_PLANTILLA_DRIVER = '''\
"""Driver generado por tests/harness_aislado.py — NO editar a mano."""
import sys
from pathlib import Path

REPO = Path(r"{raiz}")
TMP = Path(r"{base}")
sys.path.insert(0, str(REPO))

{pre}

import config                      # noqa: E402  (tras fijar sys.path)

# --- AISLAMIENTO (ver tests/harness_aislado.py) ---
config.BASE_DIR = TMP              # ni BD ni logs ni salidas en el proyecto
config.TAVILY_API_KEY = {tavily!r}
config.OPENROUTER_API_KEY = {openrouter!r}
config.LOG_AGENTE = {log_agente!r}

{extra}

import main                        # noqa: E402  (lee config YA parcheado)

sys.argv = ["main.py"] + {argv!r}
main.main()
'''

_PLANTILLA_PROGRAMA = '''\
"""Driver generado por tests/harness_aislado.py — NO editar a mano."""
import sys
from pathlib import Path

REPO = Path(r"{raiz}")
TMP = Path(r"{base}")
sys.path.insert(0, str(REPO))

{pre}

import config                      # noqa: E402

config.BASE_DIR = TMP
config.TAVILY_API_KEY = {tavily!r}
config.OPENROUTER_API_KEY = {openrouter!r}
config.LOG_AGENTE = {log_agente!r}

{extra}

import importlib                   # noqa: E402

programa = importlib.import_module({programa!r})
sys.argv = [{programa!r}] + {argv!r}
raise SystemExit(programa.main())
'''


def sha256_de(ruta: Path) -> str:
    """sha256 de un fichero (o AUSENTE si no existe)."""
    if not ruta.exists():
        return "AUSENTE"
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for bloque in iter(lambda: f.read(65536), b""):
            h.update(bloque)
    return h.hexdigest()


def huellas_de_estado(carpeta: Path | None = None,
                      nombres: tuple[str, ...] = VIGILADOS) -> dict[str, str]:
    """sha256 de cada fichero vigilado: se compara ANTES y DESPUÉS del hijo."""
    carpeta = Path(carpeta) if carpeta is not None else RAIZ
    return {n: sha256_de(carpeta / n) for n in nombres}


def escribir_driver(destino: Path, base: Path, argv: list[str], *,
                    claves: tuple = ("", ""),
                    log_agente: bool = False,
                    extra: str = "",
                    pre: str = "",
                    programa: str = "main") -> Path:
    """Escribe el script que el hijo ejecutará (parchea config y llama a main).

    `claves` es (TAVILY, OPENROUTER): ("", "") simula "clave vacía" y
    (None, None) simula "no existe la variable" (máquina sin .env);
    `programa` es el módulo del bot con función ``main()`` (por defecto
    ``main``; también vale ``resumen_noche``);
    `pre` se ejecuta ANTES de importar config: sirve para simular que no existe
    el fichero .env (p. ej. anulando dotenv.load_dotenv);
    `extra` permite inyectar código (p. ej. un cliente LLM de mentira) entre
    el parcheo de config y la llamada a main."""
    plantilla = _PLANTILLA_DRIVER if programa == "main" else _PLANTILLA_PROGRAMA
    destino.write_text(
        plantilla.format(raiz=str(RAIZ), base=str(base), argv=argv,
                         tavily=claves[0], openrouter=claves[1],
                         log_agente=log_agente, extra=extra, pre=pre,
                         programa=programa),
        encoding="utf-8")
    return destino


def lanzar(argv: list[str], tmp_path: Path, *,
           claves: tuple = ("", ""),
           con_log: bool = False,
           timeout: int = 300,
           extra: str = "",
           pre: str = "",
           programa: str = "main",
           cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Lanza el bot en un entorno AISLADO y devuelve el CompletedProcess.

    argv es la lista de argumentos del programa (p. ej. ["--fase", "2"]).
    """
    tmp_path = Path(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    driver = escribir_driver(tmp_path / "_driver_aislado.py", tmp_path, argv,
                            claves=claves, log_agente=con_log, extra=extra,
                            pre=pre, programa=programa)
    entorno = dict(os.environ)
    # El hijo no hereda .env ni el registro de logs del proyecto; además se
    # cierra la puerta por si algún módulo leyese os.environ en vez de config.
    # (None se pasa como cadena vacía: Windows descarta las variables vacías,
    #  y el aislamiento real lo hace el propio driver al parchear config.)
    entorno["TAVILY_API_KEY"] = claves[0] or ""
    entorno["OPENROUTER_API_KEY"] = claves[1] or ""
    entorno["LOG_AGENTE"] = "true" if con_log else "false"
    entorno["PYTHONUTF8"] = "1"
    entorno["PYTHONIOENCODING"] = "utf-8"
    entorno["PYTHONPATH"] = str(RAIZ)
    return subprocess.run([sys.executable, str(driver)], cwd=str(cwd or tmp_path),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, env=entorno)


def lanzar_sin_claves(argv: list[str], tmp_path: Path,
                      **kwargs) -> subprocess.CompletedProcess:
    """Igual que lanzar() con las DOS claves ausentes: máquina sin .env."""
    return lanzar(argv, tmp_path, claves=("", ""), **kwargs)


def copiar_entradas(tmp_path: Path) -> list[str]:
    """Copia al entorno temporal las entradas reales que existan (nunca las
    salidas de estado). Devuelve los nombres copiados."""
    tmp_path = Path(tmp_path)
    copiados = []
    for nombre in ENTRADAS:
        origen = RAIZ / nombre
        if origen.exists():
            shutil.copy2(origen, tmp_path / nombre)
            copiados.append(nombre)
    return copiados
