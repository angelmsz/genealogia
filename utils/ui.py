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

import sys
import threading
import time
from datetime import datetime
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

def log_una_linea(msg: str, nivel: str = "info") -> None:
    """Imprime una línea con timestamp, icono y color. Una sola línea por
    llamada, sin stacktrace ni traceback: es lo que pide la FASE 1 para los
    JSON rotos del LLM."""
    codigo, icono = _NIVELES.get(nivel, (_RESET, ICO_GEAR))
    ts = datetime.now().strftime("%H:%M:%S")
    with _UI_LOCK:
        print(f"{_GRIS}[{ts}]{_RESET} {_color(icono, codigo)} {msg}", flush=True)


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
    """Imprime una línea separadora para marcar inicio de fase/sección."""
    if titulo:
        print(f"\n{'#' * 62}\n### {titulo}\n{'#' * 62}", flush=True)
    else:
        print("#" * 62, flush=True)


def cabecera(texto: str) -> None:
    """Cabecera ligera para sub-secciones dentro de una fase."""
    print(f"\n{_color('--- ' + texto + ' ---', _BOLD + _CIAN)}", flush=True)


# ============================== RESET DE COLORS =============================

def limpiar_linea() -> None:
    """Borra la línea actual del terminal (útil tras spinner roto)."""
    if _TTY:
        print("\r" + " " * 100 + "\r", end="", flush=True)
