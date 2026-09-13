#!/usr/bin/env python3
"""
menu_principal.py — Menú de tareas comunes del agente genealógico (v10.4.1).

PARA QUÉ EXISTE
Escribir a mano `main.py --fase 2 --presupuesto-max 0.5`, `pytest tests -q` o
`git pull` cada vez es una fuente de errores (y de olvidos: el pull con el
árbol sucio, la fase 2 sin tope). Este menú hace esas tareas con dos teclas.

CÓMO FUNCIONA (y por qué así)
  - NO IMPORTA LÓGICA DE INVESTIGACIÓN. Cada opción lanza el comando
    documentado como SUBPROCESO (igual que hace generar_dossier.py con git,
    pytest y --diagnostico): lo que se ejecuta es exactamente lo que
    escribirías a mano. Así el menú no puede desviarse del bot ni al revés.
  - Usa `.venv\\Scripts\\python.exe` si existe (el intérprete con las
    dependencias); si no, `sys.executable`. Lo dice en la cabecera y en el log.
  - Muestra `config.VERSION` (si config no se puede importar, lo dice y sigue).
  - Registra cada comando en `logs/menu_AAAAmmdd_HHMMSS.log`: cabecera (fecha,
    versión, intérprete, opción, comando), salida COMPLETA y pie (código de
    salida y duración). Rota dejando los 30 más recientes. Los secretos que
    aparezcan en la salida se REDACTAN antes de escribirlos.
  - Pausa al terminar cada opción para que no se cierre la ventana
    (`--sin-pausa` lo desactiva, para tests y automatización).
  - Al hijo se le pasa PYTHONIOENCODING=utf-8 y PYTHONUTF8=1 para que la
    salida no reviente en consolas cp1252 al ir por tubería. Esto ESQUIVA ese
    problema en el menú; no lo arregla (sigue abierto para ejecuciones
    directas), y aquí queda dicho para no vender humo.

LO QUE NO HACE (a propósito)
  - No toca lanzador.py (el menú antiguo, en proceso) ni la lógica del bot.
  - No crea ejecutables .exe.
  - No lanza merges automáticos: `git pull --ff-only` y solo con el árbol
    limpio.
  - No modifica requirements.txt (fonttools se instala aparte, si lo pides).

Uso:
    Menu.bat                       (doble clic en Windows)
    .venv\\Scripts\\python.exe menu_principal.py
    python menu_principal.py --sin-pausa --sin-log
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
LOGS_DIR = BASE_DIR / "logs"
RETENCION_LOGS = 30          # logs/menu_*.log que se conservan
TAIL_ULTIMO_LOG = 60         # líneas que se muestran de un log
PRESUPUESTO_DEFECTO = "0.5"
CANDIDATOS_INTERPRETE = (
    Path(".venv") / "Scripts" / "python.exe",   # Windows
    Path(".venv") / "bin" / "python",           # Linux/macOS
)
# Paquetes de requirements.txt: (módulo importable, nombre pip).
PAQUETES = (
    ("openai", "openai"),
    ("tavily", "tavily-python"),
    ("dotenv", "python-dotenv"),
    ("requests", "requests"),
    ("bs4", "beautifulsoup4"),
    ("pypdf", "pypdf"),
    ("urllib3", "urllib3"),
    ("rapidocr_onnxruntime", "rapidocr_onnxruntime"),
    ("pdf2image", "pdf2image"),
    ("PIL", "Pillow"),
    ("playwright", "playwright"),
    ("pytest", "pytest"),
)

# El menú: número -> (etiqueta, descripción). Fuente única de verdad para
# pintar el menú, despachar y testear.
OPCIONES: dict[str, tuple[str, str]] = {
    "1": ("git pull",
          "traer los últimos commits (solo si el árbol está limpio)"),
    "2": ("pytest",
          "la suite completa de tests (no gasta tokens)"),
    "3": ("--diagnostico",
          "modelos, precios, conectores y cachés (0 tokens)"),
    "4": ("--fase 2 --presupuesto-max 0.5",
          "extrae y consolida hallazgos (GASTA dinero)"),
    "5": ("--aceptar",
          "commit al árbol de lo verificado (modifica estado)"),
    "6": ("--frontera",
          "cola priorizada de investigación (no gasta)"),
    "7": ("resumen_noche.py",
          "resumen offline de la última tanda (no gasta)"),
    "8": ("--probar-ocr",
          "prueba el OCR local con UN PDF tuyo (no gasta)"),
    "9": ("estado dependencias",
          "qué paquetes le faltan al intérprete elegido"),
    "10": ("instalar fonttools",
           "pip install fonttools (no está en requirements.txt)"),
    "11": ("ver último log",
           "rabo del log más reciente de logs/"),
    "0": ("Salir", ""),
}

# Código que se ejecuta en el intérprete ELEGIDO para el estado de dependencias
# (así informa del python que de verdad corre el bot, no del que corre al menú).
CODIGO_DEPENDENCIAS = (
    "import importlib.util, sys\n"
    f"MODS = {[(m, p) for m, p in PAQUETES]!r}\n"
    "faltan = 0\n"
    "for mod, pip in MODS:\n"
    "    try:\n"
    "        ok = importlib.util.find_spec(mod) is not None\n"
    "    except Exception:\n"
    "        ok = False\n"
    "    print(('OK    ' if ok else 'FALTA ') + pip)\n"
    "    faltan += 0 if ok else 1\n"
    "opcional = importlib.util.find_spec('fontTools') is not None\n"
    "print(('OK    ' if opcional else 'FALTA ') + 'fontTools (opcional)')\n"
    "print('python', sys.version.split()[0])\n"
    "print('RESUMEN', 'completo' if faltan == 0 else f'faltan {faltan}')\n"
)

# Patrones de secretos que se REDACTAN antes de escribir en el log. El menú
# nunca pasa claves en la línea de comandos, pero la salida del hijo podría
# filtrarlas (un .env mal cargado, un error de una librería...). Ante la duda,
# se tapa.
_PATRONES_SECRETO = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{6,}"),
    re.compile(r"\btvly-[A-Za-z0-9_\-]{6,}"),
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(r"(?i)\b([a-z0-9_]*(?:api[_-]?key|token|secret|password))"
               r"(\s*[=:]\s*)(\S+)"),
)


class MenuCancelado(Exception):
    """El usuario ha dicho que no (o el dato pedido no valía). Se vuelve al
    menú sin ejecutar nada y sin traceback."""


# ============================== AYUDANTES PUROS ============================

def resolver_interprete(base: Path | None = None) -> tuple[str, str]:
    """(ruta_del_intérprete, de_dónde_sale).

    Prefiere el .venv del proyecto (el que tiene las dependencias instaladas);
    si no existe, el intérprete que esté corriendo el menú.
    """
    base = base if base is not None else BASE_DIR
    for relativa in CANDIDATOS_INTERPRETE:
        candidato = base / relativa
        try:
            if candidato.is_file():
                return str(candidato), f"venv del proyecto ({relativa})"
        except OSError:
            continue
    return sys.executable, "sys.executable (no hay .venv)"


def resolver_git() -> str | None:
    """Ruta de git, buscando primero en el PATH y luego en las típicas de
    Windows/Linux (hay equipos donde git no está en el PATH del proceso)."""
    ruta = shutil.which("git")
    if ruta:
        return ruta
    for candidato in (r"C:\Program Files\Git\cmd\git.exe",
                      r"C:\Program Files (x86)\Git\cmd\git.exe",
                      "/usr/bin/git", "/usr/local/bin/git"):
        if Path(candidato).is_file():
            return candidato
    return None


def version_config() -> str:
    """`config.VERSION`, o un aviso legible si config no se puede cargar.

    Orden: (1) importar config de verdad (cargando antes .env, como hace el
    bot); (2) si eso falla (falta una dependencia, .env sin copiar), leer el
    literal de config.py, que no necesita nada; (3) aviso. Así el menú SIEMPRE
    arranca, incluso cuando el proyecto está a medias — que es justo cuando más
    se necesita.
    """
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))
    try:
        from dotenv import load_dotenv      # type: ignore[import-not-found]
        load_dotenv(BASE_DIR / ".env")
    except Exception:
        pass
    try:
        from config import VERSION          # type: ignore[import-not-found]
        return str(VERSION)
    except SystemExit:
        pass
    except Exception:
        pass
    try:
        texto = (BASE_DIR / "config.py").read_text(encoding="utf-8")
        m = re.search(r'^VERSION\s*=\s*["\']([^"\']+)["\']', texto, re.M)
        if m:
            return f"{m.group(1)} (leído de config.py)"
    except OSError:
        pass
    return "(config no disponible)"


def mostrar_argv(argv: list[str]) -> str:
    """El comando en una línea, SOLO para enseñarlo y registrarlo.

    La ejecución va siempre con la lista de argumentos (nunca con este texto):
    por eso las rutas con espacios funcionan. El entrecomillado de aquí es
    cosmético, y los saltos de línea se colapsan para que la cabecera del log
    ocupe UNA línea (la opción 9 pasa un programa de varias líneas con -c).
    """
    texto = " ".join(f'"{a}"' if " " in a else a for a in argv)
    return " ".join(texto.split())


def redactar(texto: str) -> str:
    """Tapa lo que parezca un secreto antes de escribir el log."""
    salida = texto
    for i, patron in enumerate(_PATRONES_SECRETO):
        if i == 3:      # nombre_de_clave = valor  ->  clave = ***
            salida = patron.sub(r"\1\2***", salida)
        elif i == 2:    # Bearer xxxxx -> Bearer ***
            salida = patron.sub(r"\1 ***", salida)
        else:           # sk-... / tvly-...
            salida = patron.sub("***", salida)
    return salida


def nombre_log(ahora: datetime | None = None) -> str:
    momento = ahora or datetime.now()
    return f"menu_{momento:%Y%m%d_%H%M%S}.log"


def rotar_logs(base: Path | None = None,
               conservar: int = RETENCION_LOGS) -> list[str]:
    """Borra los `menu_*.log` más antiguos dejando los `conservar` recientes.

    Solo mira los del MENÚ: los `agente_*.log` son la caja negra del bot y no
    se tocan nunca. Devuelve los nombres borrados.
    """
    base = base if base is not None else LOGS_DIR
    borrados: list[str] = []
    try:
        ficheros = sorted(base.glob("menu_*.log"),
                          key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return borrados
    for viejo in ficheros[conservar:]:
        try:
            viejo.unlink()
            borrados.append(viejo.name)
        except OSError:
            pass
    return borrados


def _entorno_hijo() -> dict:
    """Entorno del hijo: UTF-8 y sin buffering, para ver la salida en vivo y
    que no reviente por cp1252 al ir por tubería."""
    entorno = dict(os.environ)
    entorno["PYTHONIOENCODING"] = "utf-8"
    entorno["PYTHONUTF8"] = "1"
    entorno["PYTHONUNBUFFERED"] = "1"
    return entorno


# ============================== ENTRADA / SALIDA ===========================

def _preguntar(prompt: str, defecto: str = "") -> str:
    try:
        respuesta = input(prompt).strip()
    except EOFError:
        raise MenuCancelado("entrada cerrada")
    return respuesta if respuesta else defecto


def _confirmar(prompt: str) -> bool:
    """Confirmación explícita. El prompt lo pasa quien llama, con su texto
    exacto. SOLO 's'/'si'/'sí'/'y'/'yes' valen: cualquier otra cosa (incluido
    Enter) cancela."""
    respuesta = _preguntar(prompt, "").lower()
    return respuesta in ("s", "si", "sí", "y", "yes")


def _pausar() -> None:
    try:
        input("\n(Pulsa Enter para volver al menú) ")
    except EOFError:
        pass


# ============================== EJECUCIÓN ==================================

def capturar(argv: list[str], timeout: int = 60) -> tuple[int, str]:
    """Ejecuta un comando y devuelve (código, salida). Sin tee ni log: para
    comprobaciones cortas (git status, verificar un import)."""
    try:
        r = subprocess.run(argv, cwd=str(BASE_DIR), capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, env=_entorno_hijo())
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except FileNotFoundError:
        return 127, f"(no encuentro el ejecutable {argv[0]})"
    except subprocess.TimeoutExpired:
        return 124, f"(timeout de {timeout}s)"
    except Exception as e:      # noqa: BLE001  (red de seguridad del menú)
        return 127, f"(no se pudo ejecutar: {str(e)[:120]})"


def _correr(argv: list[str], *, opcion: str, descripcion: str,
            sin_log: bool = False) -> int:
    """Lanza el comando con tee: consola + logs/menu_*.log. Devuelve el código
    de salida."""
    reg = RegistroMenu(opcion=opcion, descripcion=descripcion, argv=argv,
                       sin_log=sin_log)
    reg.abrir()
    inicio = time.monotonic()
    codigo = 130
    try:
        try:
            proceso = subprocess.Popen(
                argv, cwd=str(BASE_DIR), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", bufsize=1, env=_entorno_hijo())
        except FileNotFoundError:
            print(f"  [x] No encuentro el ejecutable: {argv[0]}")
            reg.linea(f"[x] no encuentro el ejecutable {argv[0]}\n")
            return 127
        try:
            if proceso.stdout is not None:
                for linea in proceso.stdout:
                    print(linea, end="")
                    reg.linea(linea)
            codigo = proceso.wait()
        except KeyboardInterrupt:
            print("\n  [i] Interrumpido: terminando el proceso...")
            reg.linea("[i] interrumpido por el usuario (Ctrl+C)\n")
            proceso.terminate()
            try:
                codigo = proceso.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proceso.kill()
                codigo = 130
    finally:
        reg.cerrar(codigo, time.monotonic() - inicio)
    return codigo


class RegistroMenu:
    """Log del menú: una cabecera, la salida completa y un pie.

    Fichero: logs/menu_AAAAmmdd_HHMMSS.log. Nunca escribe secretos: todo pasa
    por `redactar()`. Con `sin_log=True` no crea nada (tests y --sin-log).
    """

    def __init__(self, *, opcion: str, descripcion: str, argv: list[str],
                 sin_log: bool = False, base: Path | None = None) -> None:
        self.opcion = opcion
        self.descripcion = descripcion
        self.argv = list(argv)
        self.sin_log = sin_log
        # OJO: se resuelve en el momento, no como valor por defecto, para que
        # un BASE_DIR/LOGS_DIR parcheado (tests, otra instalación) cuente.
        self.base = base if base is not None else LOGS_DIR
        self.ruta: Path | None = None
        self._fh = None

    def abrir(self) -> None:
        if self.sin_log:
            return
        try:
            self.base.mkdir(parents=True, exist_ok=True)
            self.ruta = self.base / nombre_log()
            self._fh = self.ruta.open("a", encoding="utf-8", newline="\n")
            self._fh.write("=" * 78 + "\n")
            self._fh.write(f"fecha      : {datetime.now():%Y-%m-%d %H:%M:%S}\n")
            self._fh.write(f"version    : {version_config()}\n")
            interprete, origen = resolver_interprete()
            self._fh.write(f"interprete : {interprete}  [{origen}]\n")
            self._fh.write(f"opcion     : {self.opcion} — {self.descripcion}\n")
            self._fh.write(f"comando    : {mostrar_argv(self.argv)}\n")
            self._fh.write("=" * 78 + "\n")
            self._fh.flush()
            rotar_logs(self.base)
        except OSError as e:
            print(f"  [!] No se pudo abrir el log del menú: {str(e)[:80]}")
            self._fh = None

    def linea(self, texto: str) -> None:
        if self._fh is None:
            return
        try:
            self._fh.write(redactar(texto))
            self._fh.flush()
        except OSError:
            self._fh = None

    def cerrar(self, codigo: int, duracion: float) -> None:
        if self._fh is None:
            return
        try:
            self._fh.write("-" * 78 + "\n")
            self._fh.write(f"codigo_salida : {codigo}\n")
            self._fh.write(f"duracion      : {duracion:.1f} s\n")
            self._fh.close()
        except OSError:
            pass
        finally:
            self._fh = None


# ============================== ACCIONES ===================================

def accion_git_pull(interprete: str, sin_log: bool = False) -> int:
    """Opción 1: git pull SIN merges automáticos y solo con el árbol limpio."""
    git = resolver_git()
    if not git:
        print("  [x] No encuentro 'git' (ni en el PATH ni en las rutas típicas).")
        print("      Instala Git o añádelo al PATH y vuelve a intentarlo.")
        return 1
    codigo, salida = capturar([git, "status", "--porcelain"])
    print("  Comprobando si hay cambios locales sin commitear...")
    if codigo != 0:
        print(f"  [x] 'git status' falló (código {codigo}). No hago el pull.")
        if salida.strip():
            print("      " + salida.strip()[:400])
        return codigo or 1
    if salida.strip():
        cambios = [l for l in salida.splitlines() if l.strip()]
        print(f"  [!] Hay {len(cambios)} cambio(s) local(es) sin commitear: "
              f"NO hago 'git pull'.")
        for linea in cambios[:15]:
            print(f"      {linea}")
        if len(cambios) > 15:
            print(f"      ... y {len(cambios) - 15} más")
        print("      Commitea o guarda tus cambios (git stash) y vuelve a "
              "intentarlo. El menú no mezcla ramas ni resuelve conflictos.")
        return 1
    print("  Repositorio limpio: 'git pull --ff-only' (sin merges automáticos).")
    return _correr([git, "pull", "--ff-only"], opcion="1",
                   descripcion="git pull --ff-only", sin_log=sin_log)


def accion_pytest(interprete: str, sin_log: bool = False) -> int:
    """Opción 2: la suite completa."""
    return _correr([interprete, "-m", "pytest", "tests", "-q"], opcion="2",
                   descripcion="pytest (suite completa)", sin_log=sin_log)


def accion_diagnostico(interprete: str, sin_log: bool = False) -> int:
    """Opción 3: --diagnostico (0 tokens)."""
    return _correr([interprete, "main.py", "--diagnostico"], opcion="3",
                   descripcion="main.py --diagnostico", sin_log=sin_log)


def accion_fase2(interprete: str, sin_log: bool = False) -> int:
    """Opción 4: fase 2 con presupuesto. Gasta dinero: confirmación EXPLÍCITA
    (Enter cancela) y presupuesto inválido = cancelar, no adivinar."""
    crudo = _preguntar(f"  ¿Presupuesto máximo en $? "
                       f"[{PRESUPUESTO_DEFECTO}]:", PRESUPUESTO_DEFECTO)
    try:
        valor = float(crudo.replace(",", "."))
    except ValueError:
        print(f"  [!] '{crudo}' no es un número válido: opción CANCELADA.")
        return 0
    if valor <= 0:
        print(f"  [!] El presupuesto debe ser mayor que 0: opción CANCELADA.")
        return 0
    argv = [interprete, "main.py", "--fase", "2",
            "--presupuesto-max", f"{valor:g}"]
    print(f"\n  Comando exacto: {mostrar_argv(argv)}")
    print("  (el tope lo aplica el bot: al alcanzarlo guarda el progreso y para)")
    if not _confirmar("  Esto puede gastar dinero en OpenRouter. "
                      "¿Continuar? (s/n, Enter = n):"):
        print("  Cancelado: no se ha gastado nada.")
        return 0
    return _correr(argv, opcion="4",
                   descripcion=f"fase 2 con --presupuesto-max {valor:g}",
                   sin_log=sin_log)


def accion_aceptar(interprete: str, sin_log: bool = False) -> int:
    """Opción 5: --aceptar. Escribe en el árbol familiar: confirmación
    explícita (Enter cancela)."""
    argv = [interprete, "main.py", "--aceptar"]
    print(f"\n  Comando exacto: {mostrar_argv(argv)}")
    if not _confirmar("  Esto puede aceptar candidatos y modificar estado. "
                      "¿Continuar? (s/n, Enter = n):"):
        print("  Cancelado: el árbol no se ha tocado.")
        return 0
    return _correr(argv, opcion="5", descripcion="main.py --aceptar",
                   sin_log=sin_log)


def accion_frontera(interprete: str, sin_log: bool = False) -> int:
    """Opción 6: --frontera (no gasta)."""
    return _correr([interprete, "main.py", "--frontera"], opcion="6",
                   descripcion="main.py --frontera", sin_log=sin_log)


def accion_resumen(interprete: str, sin_log: bool = False) -> int:
    """Opción 7: resumen_noche.py (offline, no gasta)."""
    return _correr([interprete, "resumen_noche.py"], opcion="7",
                   descripcion="resumen_noche.py", sin_log=sin_log)


def accion_probar_ocr(interprete: str, sin_log: bool = False) -> int:
    """Opción 8: --probar-ocr con UN PDF. La ruta se valida ANTES de lanzar
    nada y el comando se construye como lista de argumentos (las rutas con
    espacios funcionan)."""
    crudo = _preguntar("  Ruta del PDF (puedes arrastrarlo aquí):", "")
    ruta = crudo.strip().strip('"').strip("'")
    if not ruta:
        print("  [!] Sin ruta no hay nada que probar: opción CANCELADA.")
        return 0
    pdf = Path(ruta).expanduser()
    if not pdf.is_file():
        print(f"  [!] No existe el fichero: {pdf}")
        print("      Comprueba la ruta (o arrastra el PDF a esta ventana) "
              "y repite: NO se ha lanzado nada.")
        return 0
    argv = [interprete, "main.py", "--probar-ocr", str(pdf)]
    if _confirmar("  ¿Añadir --manuscrito (partida parroquial)? "
                  "(s/n, Enter = n):"):
        argv.append("--manuscrito")
    if _confirmar("  ¿Añadir --sin-cache (repetir el OCR aunque esté en "
                  "caché)? (s/n, Enter = n):"):
        argv.append("--sin-cache")
    print(f"\n  Comando exacto: {mostrar_argv(argv)}")
    return _correr(argv, opcion="8", descripcion=f"probar-ocr {pdf.name}",
                   sin_log=sin_log)


def accion_dependencias(interprete: str, sin_log: bool = False) -> int:
    """Opción 9: qué paquetes le faltan AL INTÉRPRETE ELEGIDO (no gasta, no
    toca nada). Se ejecuta en el venv para que informe del python que de
    verdad corre el bot."""
    estado_env = ("sí" if (BASE_DIR / ".env").exists()
                  else "NO (copia .env.example a .env)")
    estado_logs = ("sí" if LOGS_DIR.exists()
                   else "no (se crea al ejecutar la primera opción)")
    git = resolver_git()
    print(f"\n  Intérprete: {interprete}")
    print(f"  .env       : {estado_env}")
    print(f"  logs/      : {estado_logs}")
    print(f"  git        : {git or 'NO ENCONTRADO'}")
    print()
    return _correr([interprete, "-c", CODIGO_DEPENDENCIAS], opcion="9",
                   descripcion="estado de dependencias", sin_log=sin_log)


def accion_fonttools(interprete: str, sin_log: bool = False) -> int:
    """Opción 10: instalar fonttools explícitamente y comprobar el resultado.

    NO se añade a requirements.txt: solo se instalará ahí si se confirma que
    mejora la extracción o evita errores reales.
    """
    argv = [interprete, "-m", "pip", "install", "fonttools"]
    print(f"\n  Comando exacto: {mostrar_argv(argv)}")
    print("  (afecta SOLO al .venv de este proyecto; requirements.txt no se toca)")
    if not _confirmar("  ¿Instalar fonttools? (s/n, Enter = n):"):
        print("  Cancelado: no se ha instalado nada.")
        return 0
    codigo = _correr(argv, opcion="10", descripcion="pip install fonttools",
                     sin_log=sin_log)
    print("\n  Comprobando si ha quedado disponible...")
    verif, salida = capturar([interprete, "-c",
                              "import fontTools, sys; "
                              "print('fontTools', "
                              "getattr(fontTools, '__version__', '?'))"])
    if verif == 0 and salida.strip():
        print(f"  [ok] fontTools disponible: {salida.strip()}")
    else:
        print(f"  [!] fontTools NO se pudo importar (código {verif}). "
              f"{salida.strip()[:200]}")
    return codigo


def accion_ultimo_log(interprete: str, sin_log: bool = False) -> int:
    """Opción 11: rabo del log más reciente de logs/ (del menú o del bot)."""
    try:
        todos = [p for p in LOGS_DIR.glob("*.log") if p.is_file()]
    except OSError:
        todos = []
    if not todos:
        print(f"\n  [!] Todavía no hay ningún log en {LOGS_DIR}.")
        print("      Se crean al ejecutar cualquier opción (logs/menu_*.log) "
              "o una noche del bot (logs/agente_*.log).")
        return 0
    recientes = sorted(todos, key=lambda p: p.stat().st_mtime,
                       reverse=True)[:5]
    print("\n  Logs más recientes:")
    for i, p in enumerate(recientes, 1):
        try:
            kb = p.stat().st_size / 1024
            cuando = datetime.fromtimestamp(p.stat().st_mtime)
        except OSError:
            kb, cuando = 0.0, datetime.now()
        print(f"   {i}. {p.name}  ({kb:.0f} KB, {cuando:%Y-%m-%d %H:%M})")
    eleccion = _preguntar("  ¿Cuál? [1]:", "1")
    try:
        elegido = recientes[int(eleccion) - 1]
    except (ValueError, IndexError):
        print("  [!] Opción no válida: muestro el más reciente.")
        elegido = recientes[0]
    print(f"\n  --- últimos {TAIL_ULTIMO_LOG} mensajes de {elegido.name} ---")
    try:
        lineas = elegido.read_text(encoding="utf-8",
                                   errors="replace").splitlines()
    except OSError as e:
        print(f"  [!] No puedo leer {elegido.name}: {str(e)[:80]}")
        return 1
    for linea in lineas[-TAIL_ULTIMO_LOG:]:
        print(linea)
    print(f"  --- fin ({len(lineas)} líneas en total) ---")
    return 0


ACCIONES = {
    "1": accion_git_pull, "2": accion_pytest, "3": accion_diagnostico,
    "4": accion_fase2, "5": accion_aceptar, "6": accion_frontera,
    "7": accion_resumen, "8": accion_probar_ocr, "9": accion_dependencias,
    "10": accion_fonttools, "11": accion_ultimo_log,
}


# ============================== MENÚ =======================================

def _pintar_menu(version: str, interprete: str) -> None:
    print("=" * 62)
    print(f" AGENTE GENEALÓGICO — MENÚ DE TAREAS   (VERSION: {version})")
    print("=" * 62)
    for numero in ("1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11"):
        etiqueta, descripcion = OPCIONES[numero]
        print(f" {numero:>2}. {etiqueta}")
        print(f"     {descripcion}")
    print(f"  0. {OPCIONES['0'][0]}")
    print("-" * 62)
    print(f" intérprete: {interprete}")
    print(f" logs      : {LOGS_DIR}")
    print("=" * 62)


def _despachar(eleccion: str, interprete: str, sin_log: bool) -> None:
    accion = ACCIONES.get(eleccion)
    if accion is None:
        print(f"  [!] Opción '{eleccion}' no reconocida (0-11).")
        return
    try:
        accion(interprete, sin_log=sin_log)
    except MenuCancelado as e:
        print(f"\n  Cancelado. Volviendo al menú. ({e})")
    except KeyboardInterrupt:
        print("\n  Cancelado. Volviendo al menú.")


def _preparar_salida() -> None:
    """UTF-8 tolerante en la consola del menú (los iconos de la salida del bot
    no deben tumbar la ventana)."""
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _argumentos(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="menu_principal.py",
        description="Menú de tareas comunes del agente genealógico.")
    p.add_argument("--sin-pausa", action="store_true",
                   help="no esperar Enter al terminar cada opción (tests)")
    p.add_argument("--sin-log", action="store_true",
                   help="no escribir logs/menu_*.log (tests)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _argumentos(argv)
    _preparar_salida()
    interprete, origen = resolver_interprete()
    version = version_config()
    print(f"Intérprete: {interprete}  [{origen}]")
    print(f"Proyecto  : {BASE_DIR}")
    while True:
        _pintar_menu(version, interprete)
        try:
            eleccion = input("Elige una opción: ").strip()
        except EOFError:
            print("\nEntrada cerrada. Saliendo del menú.")
            return 0
        except KeyboardInterrupt:
            print("\nCancelado.")
            continue
        if eleccion in ("0", "q", "Q", "salir", "exit"):
            print("\nHasta luego.")
            return 0
        if not eleccion:
            continue
        try:
            _despachar(eleccion, interprete, args.sin_log)
        except Exception as e:      # noqa: BLE001  (red de seguridad del menú)
            print(f"\n  [x] Error inesperado: {str(e)[:200]}")
        finally:
            if not args.sin_pausa:
                _pausar()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nCancelado. Saliendo del menú.")
        sys.exit(0)
