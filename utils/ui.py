"""
utils/ui.py — Interfaz de terminal enriquecida (FASE 2 del refactor v4.0).

Reemplaza los print básicos por una UI agéntica con:
  - Códigos ANSI de colores (verde éxito, amarillo aviso, rojo error,
    cian acciones, magenta LLM).
  - Iconos Unicode universales: [⚙] trabajando, [✓] éxito, [x] error,
    [!] aviso, [🔍] buscando.
  - Indicador de actividad (spinner) en su propia línea mientras una
    llamada (Tavily, LLM, descarga) está en curso; al terminar borra la
    línea para que el log normal ocupe su sitio.
  - FunelDescargas: un resumen vivo del embudo de descargas por consulta
    Tavily: URLs encontradas -> descartadas (basura) -> descargadas ->
    superan filtro local.
  - log_una_linea() para errores capturados sin stacktrace: la FASE 1 pide
    que los JSON rotos del LLM salgan como UN solo aviso limpio.

Toda la salida usa exclusivamente stdlib (sin rich, sin colorama):
funciona igual en PowerShell, CMD, bash, zsh y la terminal de VS Code.
Si la salida no es una TTY (pipes, redirección, tests), se desactivan
colores y spinner para no ensuciar los ficheros.
"""

from __future__ import annotations

import atexit
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# ============================== COLORES ANSI ================================
# Códigos ANSI estándar; se autodetecta si la salida los soporta.

_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"

# Colores línea a línea.
_VERDE = "\033[32m"
_AMARILLO = "\033[33m"
_ROJO = "\033[31m"
_CIAN = "\033[36m"
_MAGENTA = "\033[35m"
_AZUL = "\033[34m"
_GRIS = "\033[90m"

# ============================== ICONOS ======================================
# Unicode puro: segura en cualquier terminal moderna (PowerShell 7+,
# Terminal.app, gnome-terminal, kitty...). En codepages antiguos (cmd.exe
# sin /U) se ven sustitutos ASCII, no rompen la salida.

ICO_OK = "[\u2713]"        # [✓]
ICO_ERR = "[x]"
ICO_WARN = "[!]"
ICO_GEAR = "[\u2699]"      # [⚙]
ICO_SEARCH = "[\U0001F50D]" # [🔍]
ICO_BROOM = "[\U0001F9F9]" # [🧹]
ICO_CHART = "[\U0001F4CA]"  # [📊]
ICO_TARGET = "[\U0001F3AF]" # [🎯]
ICO_TREE = "[\U0001F332]"  # [🌲]
ICO_DOC = "[\U0001F4C4]"   # [📄]

# ============================== DETECCIÓN TTY ===============================
# Si stdout no es una terminal (pipes, redirección a fichero, tests), se
# desactivan colores y spinner para no ensuciar la salida.

_TTY = bool(sys.stdout.isatty())


def _color(texto: str, codigo: str) -> str:
    if not _TTY:
        return texto
    return f"{codigo}{texto}{_RESET}"


# ============================== LOG PRINCIPAL ===============================
# log_una_linea(msg, nivel) imprime UNA sola línea con timestamp, icono y
# color. Es el único punto de salida de mensajes para todo el agente:
# sustituye a los print() dispersos del monolito original.

_NIVELES = {
    "ok":      (_VERDE,    ICO_OK),
    "error":   (_ROJO,     ICO_ERR),
    "warn":    (_AMARILLO, ICO_WARN),
    "info":    (_CIAN,     ICO_GEAR),
    "search":  (_MAGENTA,  ICO_SEARCH),
    "llm":     (_MAGENTA,  ICO_GEAR),
    "clean":   (_AMARILLO, ICO_BROOM),
    "stats":   (_AZUL,     ICO_CHART),
    "target":  (_VERDE,    ICO_TARGET),
    "tree":    (_VERDE,    ICO_TREE),
    "doc":     (_CIAN,     ICO_DOC),
}

_UI_LOCK = threading.Lock()

# ================= v10.4 (P0) — REGISTRO DE EJECUCIÓN ("caja negra") ========
# TODO lo que sale por la consola se escribe TAMBIÉN en un fichero de texto
# (logs/agente_AAAAMMDD_HHMMSS.log) con la fecha completa y SIN códigos de
# color. Es lo que permite, al día siguiente, saber qué pasó y por qué falló
# algo en una noche cuya salida ya no está en pantalla.
#
# Reglas de diseño:
#   - Está APAGADO hasta que alguien llame a iniciar_log() (lo hacen main.py
#     y lanzador.py al arrancar): así los tests, los imports sueltos y
#     cualquier script auxiliar NO escriben ficheros por sorpresa.
#   - Escribir el registro NUNCA puede tumbar al agente: si el disco falla,
#     se apaga y la investigación continúa.
#   - traza() escribe SOLO en el fichero (no ensucia la consola): se usa para
#     el perfil de tiempos (cuánto tardó cada operación).
#   - Al salir del proceso se cierra solo (atexit): también en los modos que
#     terminan con SystemExit (--diagnostico, --probar-ocr...), de modo que
#     el pie y el recuento de avisos/errores SIEMPRE quedan escritos.

_SIN_ANSI = re.compile(r"\033\[[0-9;]*m")

_LOGFILE = None
_RUTA_LOG: Optional[Path] = None
_LOG_INICIO: Optional[float] = None
_CONTADORES: dict = {}


def _escribir_log(nivel: str, msg: str) -> None:
    """Añade UNA línea al registro de ejecución (si está abierto)."""
    global _LOGFILE
    if _LOGFILE is None:
        return
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        texto = _SIN_ANSI.sub("", str(msg)).replace("\r", " ")
        with _UI_LOCK:
            _LOGFILE.write(f"{ts} [{nivel.upper()}] {texto}\n")
            _LOGFILE.flush()
        _CONTADORES[nivel] = _CONTADORES.get(nivel, 0) + 1
    except Exception:
        # El registro existe para depurar, no para romper la noche: si el
        # disco falla (lleno, permisos), se apaga y se sigue. Aviso con
        # print() directo para no volver a entrar aquí.
        _LOGFILE = None
        print("[!] el registro de ejecución se ha desactivado: fallo de "
              "escritura en disco")


def traza(msg: str) -> None:
    """Línea de TRAZA: solo al fichero (perfil de tiempos y detalle fino).
    No imprime nada en consola."""
    _escribir_log("traza", msg)


def iniciar_log(dir_log=None, extra: str = "") -> Optional[str]:
    """Abre el registro de esta ejecución y escribe su cabecera.

    `dir_log` permite redirigirlo (lo usan los tests). Si no se indica, se
    usa config.LOG_DIR dentro de la carpeta del proyecto (import DIFERIDO:
    config.py importa este módulo). Respeta LOG_AGENTE=false para
    desactivarlo. Idempotente: si ya está abierto devuelve la misma ruta.
    Devuelve la ruta del fichero o None.
    """
    global _LOGFILE, _RUTA_LOG, _LOG_INICIO
    if _LOGFILE is not None:
        return str(_RUTA_LOG)
    try:
        if dir_log is None:
            from config import BASE_DIR, LOG_AGENTE, LOG_DIR
            if not LOG_AGENTE:
                return None
            dir_log = BASE_DIR / LOG_DIR
        dir_log = Path(dir_log)
        dir_log.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        _RUTA_LOG = dir_log / f"agente_{ts}.log"
        # Dos acciones dentro del MISMO segundo (menú del lanzador) no pueden
        # machacarse el registro: se añade un sufijo.
        n = 2
        while _RUTA_LOG.exists():
            _RUTA_LOG = dir_log / f"agente_{ts}_{n}.log"
            n += 1
        _LOGFILE = open(_RUTA_LOG, "w", encoding="utf-8")
        _LOG_INICIO = time.time()
        _CONTADORES.clear()
        # El pie (duración + recuento) se escribe SIEMPRE, incluso si el modo
        # termina con SystemExit: atexit cubre todos los caminos de salida.
        atexit.register(cerrar_log)
        _escribir_log("info", f"=== REGISTRO DE EJECUCIÓN -> {_RUTA_LOG} ===")
        for linea in str(extra).splitlines():
            if linea.strip():
                _escribir_log("info", linea)
    except Exception as e:
        _LOGFILE = None
        _RUTA_LOG = None
        print(f"[!] no se pudo abrir el registro de ejecución: {str(e)[:150]}")
        return None
    return str(_RUTA_LOG)


def cabecera_log(datos: dict) -> None:
    """Escribe la CABECERA DE CONFIGURACIÓN del run (clave: valor): qué se
    pidió y con qué ajustes. Es lo primero que se mira al depurar."""
    if _LOGFILE is None:
        return
    _escribir_log("info", "--- configuración de esta ejecución ---")
    for clave, valor in (datos or {}).items():
        _escribir_log("info", f"    {clave}: {valor}")
    _escribir_log("info", "-" * 40)


def resumen_log() -> str:
    """Recuento en una línea de lo registrado (errores, avisos, trazas...)."""
    if not _CONTADORES:
        return "sin registro"
    orden = ("error", "warn", "traza", "info", "doc", "seccion", "ok",
             "search", "llm", "stats", "target", "tree", "clean")
    partes = [f"{n}: {_CONTADORES[n]}" for n in orden if _CONTADORES.get(n)]
    return " · ".join(partes) if partes else "sin líneas"


def cerrar_log() -> Optional[str]:
    """Escribe el pie (duración + recuento), avisa de dónde quedó el registro
    y lo cierra. Idempotente. Devuelve la ruta o None."""
    global _LOGFILE
    if _LOGFILE is None:
        return None
    ruta = str(_RUTA_LOG) if _RUTA_LOG else None
    duracion = int(time.time() - (_LOG_INICIO or time.time()))
    _escribir_log("info", f"=== FIN DE LA EJECUCIÓN ({duracion} s) ===")
    log_doc(f"registro de la ejecución: {ruta} ({resumen_log()})")
    try:
        with _UI_LOCK:
            _LOGFILE.close()
    except Exception:
        pass
    _LOGFILE = None
    # El recuento pertenece al registro que se acaba de cerrar: cerrado el
    # registro, resumen_log() vuelve a decir "sin registro".
    _CONTADORES.clear()
    return ruta


def log_una_linea(msg: str, nivel: str = "info") -> None:
    """Imprime una línea con timestamp, icono y color — y la escribe TAMBIÉN
    en el registro de ejecución (v10.4). Una sola línea por llamada, sin
    stacktrace: es lo que pide la FASE 1 para los JSON rotos del LLM."""
    codigo, icono = _NIVELES.get(nivel, (_RESET, ICO_GEAR))
    ts = datetime.now().strftime("%H:%M:%S")
    with _UI_LOCK:
        print(f"{_GRIS}[{ts}]{_RESET} {_color(icono, codigo)} {msg}", flush=True)
    _escribir_log(nivel, msg)


# Funciones de conveniencia con nombres legibles (reemplazan a log() del
# monolito original).
def log(msg: str) -> None:
    log_una_linea(msg, nivel="info")


def log_ok(msg: str) -> None:
    log_una_linea(msg, nivel="ok")


def log_warn(msg: str) -> None:
    log_una_linea(msg, nivel="warn")


def log_error(msg: str) -> None:
    log_una_linea(msg, nivel="error")


def log_search(msg: str) -> None:
    log_una_linea(msg, nivel="search")


def log_llm(msg: str) -> None:
    log_una_linea(msg, nivel="llm")


def log_clean(msg: str) -> None:
    log_una_linea(msg, nivel="clean")


def log_stats(msg: str) -> None:
    log_una_linea(msg, nivel="stats")


def log_target(msg: str) -> None:
    log_una_linea(msg, nivel="target")


def log_tree(msg: str) -> None:
    log_una_linea(msg, nivel="tree")


def log_doc(msg: str) -> None:
    log_una_linea(msg, nivel="doc")


# ============================== SPINNER =====================================
# Indicador de actividad. Un hilo daemon pinta "| Mensaje... (Ns)" cada
# segundo en la MISMA línea; al terminar el contexto se borra. ASCII puro
# para no romper en PowerShell antiguo.

_SPINNER_FRAMES = ["|", "/", "-", "\\"]

UMBRAL_LENTO = 15  # a partir de aquí el spinner avisa "(tardando...)"


def formatear_indicador(mensaje: str, segundos: int, frame: str = "|") -> str:
    """Texto de una frame del indicador (sin \\r; lo añade quien imprime)."""
    txt = f"{frame} {mensaje}... ({segundos}s)"
    if segundos >= UMBRAL_LENTO:
        txt += " (tardando más de lo normal)"
    return txt


class Indicador:
    """Context manager: muestra actividad mientras dura el bloque.

    Uso:
        with Indicador("Buscando en Tavily"):
            resultado = tavily.search(...)
    """

    _ACTIVO = True  # ponlo a False para desactivar globalmente el spinner

    def __init__(self, mensaje: str, nivel: str = "llm"):
        self.mensaje = mensaje
        self._nivel = nivel
        self._stop = threading.Event()
        self._hilo: Optional[threading.Thread] = None
        self._len_max = 0
        self._tty = False
        self._inicio: Optional[float] = None

    def _pintar(self, txt: str) -> None:
        with _UI_LOCK:
            self._len_max = max(self._len_max, len(txt))
            print("\r" + txt + "   ", end="", flush=True)

    def _borrar(self) -> None:
        with _UI_LOCK:
            if self._len_max:
                print("\r" + " " * (self._len_max + 3) + "\r", end="", flush=True)
                self._len_max = 0

    def _bucle(self) -> None:
        inicio = time.time()
        i = 0
        while not self._stop.is_set():
            segundos = int(time.time() - inicio)
            self._pintar(formatear_indicador(
                self.mensaje, segundos, _SPINNER_FRAMES[i % len(_SPINNER_FRAMES)]))
            i += 1
            self._stop.wait(1.0)

    def __enter__(self):
        self._inicio = time.time()
        self._tty = bool(Indicador._ACTIVO and _TTY)
        if self._tty:
            self._hilo = threading.Thread(target=self._bucle, daemon=True)
            self._hilo.start()
        return self

    def __exit__(self, *exc):
        if self._hilo is not None:
            self._stop.set()
            self._hilo.join(timeout=2)
        if self._tty:
            self._borrar()
        # v10.4: perfil de tiempos en el registro (solo fichero). Con esto,
        # al día siguiente se ve cuánto tardó CADA operación y dónde se fue
        # la noche, sin que la consola se llene de ruido.
        if self._inicio is not None:
            traza(f"{self.mensaje}: {int(time.time() - self._inicio)} s")
        return False  # no tragar excepciones


# ============================== EMBUDO DE DESCARGAS ========================
# FASE 2 — Transparencia del embudo: por cada consulta Tavily el agente
# imprime UNA sola línea al final con el recuento:
#
#   [📊] 10 URLs -> 3 descartadas (basura) -> 7 descargadas -> 2 superan filtro
#
# Esto da al usuario una idea inmediata de la calidad de cada ronda de
# búsqueda sin necesidad de leerse logs completos ni abrir el corpus.

class FunelDescargas:
    """Acumulador por consulta. Uso:
        f = FunelDescargas()
        f.urls_encontradas = 10
        f.descartadas_basura = 3
        f.descargadas_ok = 5
        f.fallidas = 2
        f.superan_filtro = 2
        f.resumen()
    """

    def __init__(self) -> None:
        self.urls_encontradas: int = 0
        self.descartadas_basura: int = 0   # por DOMINIOS_IGNORADOS
        self.descartadas_duplicadas: int = 0  # URL ya vista en SQLite
        self.descargadas_ok: int = 0
        self.fallidas: int = 0               # timeout, 403, etc.
        self.superan_filtro: int = 0

    def add_encontrada(self) -> None:
        self.urls_encontradas += 1

    def add_basura(self) -> None:
        self.descartadas_basura += 1

    def add_duplicada(self) -> None:
        self.descartadas_duplicadas += 1

    def add_descargada(self) -> None:
        self.descargadas_ok += 1

    def add_fallida(self) -> None:
        self.fallidas += 1

    def add_filtrada(self) -> None:
        self.superan_filtro += 1

    def resumen(self, query: str = "") -> None:
        """Imprime UNA sola línea con el embudo completo."""
        q = f' "{query[:60]}..."' if query and len(query) > 60 else (
            f' "{query}"' if query else "")
        msg = (
            f"{self.urls_encontradas} URLs encontradas -> "
            f"{self.descartadas_basura} descartadas (basura) + "
            f"{self.descartadas_duplicadas} (duplicadas) -> "
            f"{self.descargadas_ok} descargadas, "
            f"{self.fallidas} fallidas -> "
            f"{self.superan_filtro} superan filtro local{q}"
        )
        log_stats(msg)


# ============================== SEPARADORES =================================

def separador(titulo: str = "") -> None:
    """Imprime una línea separadora para marcar inicio de fase/sección (y la
    deja también en el registro de ejecución: son los hitos del log)."""
    if titulo:
        print(f"\n{'#' * 62}\n### {titulo}\n{'#' * 62}", flush=True)
        _escribir_log("seccion", f"### {titulo}")
    else:
        print("#" * 62, flush=True)


def cabecera(texto: str) -> None:
    """Cabecera ligera para sub-secciones dentro de una fase."""
    print(f"\n{_color('--- ' + texto + ' ---', _BOLD + _CIAN)}", flush=True)
    _escribir_log("seccion", f"--- {texto} ---")


# ============================== RESET DE COLORS =============================

def limpiar_linea() -> None:
    """Borra la línea actual del terminal (útil tras spinner roto)."""
    if _TTY:
        print("\r" + " " * 100 + "\r", end="", flush=True)
