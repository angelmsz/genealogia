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
  - v10.4.2 — cubre también la parte del menú antiguo (`lanzador.py`, retirado
    en esta versión) que de verdad se usaba: --ciclo N (autopiloto), --fase 1
    sola, --personas "A,B", --max-steps y --presupuesto-max libres, y el
    --test-llm del diagnóstico. Los comandos avanzados que NO están en el menú
    (opción 15) se imprimen en una chuleta, para copiar y pegar, sin ejecutar
    nada.
  - v10.4.2 — al terminar una acción que escribe estado, el menú dice QUÉ
    ficheros ha generado o actualizado (tamaño + recuento cuando se puede
    contar): antes había que abrir el explorador para saber si la noche había
    dejado algo nuevo.

LO QUE NO HACE (a propósito)
  - No toca la lógica del bot: la lanza tal cual, en subprocesos.
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
import json
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
# Ficheros de salida del bot que se vigilan para poder decir, al terminar cada
# acción, QUÉ ha generado o actualizado (mtime + tamaño + recuento si se puede).
FICHEROS_VIGILADOS = (
    "corpus_bruto.json",            # fase 1
    "informe_fase1.json",           # fase 1
    "arbol_hallazgos.json",         # fase 2
    "arbol_refinado.json",          # fase 2
    "arbol.ged",                    # fase 2
    "estado_investigacion.json",    # frontera / commit
    "informe_progreso.md",          # frontera / commit
    "familia_conocida.json",        # --aceptar
    "registro_confirmaciones.jsonl",  # --aceptar
)
# Orden en que se pintan las opciones (0 = salir, fuera de la lista).
ORDEN_OPCIONES = ("1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11",
                  "12", "13", "14", "15")
# Comandos avanzados que se quedan FUERA del menú a propósito: la opción 15 solo
# los enseña (con su aviso de si gastan o no) para no tener que recordarlos.
CHULETA_AVANZADOS = (
    ("--solicitudes",
     "genera las solicitudes de partidas (solicitudes.json + .md): para pedir "
     "por escrito lo que no está online. No gasta nada."),
    ("--importar-propios",
     "transcribe las fotos de documentos_propios/ con el OCR 100% LOCAL y las "
     "añade al corpus. No gasta nada."),
    ("--reclasificar",
     "vuelve a clasificar el árbol ya guardado con el clasificador "
     "determinista de evidencia. No gasta ni un token."),
    ("--ensenada",
     "busca los municipios del árbol en el Catastro de Ensenada (1752) y "
     "escribe candidatos_ensenada.json. Gasta algo de LLM."),
    ("--probar-conectores",
     "comprueba en vivo SIGA (Álava), ADDO (Palencia) y PARES. No gasta "
     "tokens, pero usa la red."),
    ("--fase 2 --sin-cache",
     "repite la extracción de hallazgos aunque esté en caché: es la forma de "
     "recuperar fragmentos que una versión anterior guardó como vacíos. "
     "GASTA (vuelve a extraer todo el corpus)."),
    ("--limpiar-cache-hallazgos",
     "borra del caché de extracción SOLO las filas que no aportan hallazgos "
     "(las que dejó la versión anterior al fallar un lote), para que la fase 2 "
     "vuelva a extraerlas. Pide confirmación y deja cache_agente.db.bak. No "
     "gasta."),
)
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
    "12": ("--ciclo N (autopiloto)",
           "N ciclos completos: fase 1 -> fase 2 -> commit -> frontera "
           "(GASTA dinero)"),
    "13": ("--fase 1 (solo búsqueda)",
           "corpus nuevo desde la web con Tavily + LLM (GASTA dinero)"),
    "14": ('--personas "A,B"',
           "investiga SOLO a esas personas (fase 1 + fase 2; GASTA dinero)"),
    "15": ("chuleta de comandos avanzados",
           "los comandos que NO están en el menú, para copiar y pegar "
           "(no ejecuta nada)"),
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


def _pedir_presupuesto() -> float | None:
    """Presupuesto en dólares del usuario, o None si lo escrito no vale.

    Enter = PRESUPUESTO_DEFECTO; se admite la coma decimal (1,5). Devuelve None
    (opción cancelada) ante texto no numérico, cero o negativo: el menú NUNCA
    adivina cuánto puedes gastar. Lo usan todas las opciones que gastan, para
    que la pregunta y las reglas sean las mismas en todas.
    """
    crudo = _preguntar(f"  ¿Presupuesto máximo en $? "
                       f"[{PRESUPUESTO_DEFECTO}]:", PRESUPUESTO_DEFECTO)
    try:
        valor = float(crudo.replace(",", "."))
    except ValueError:
        print(f"  [!] '{crudo}' no es un número válido: opción CANCELADA.")
        return None
    if valor <= 0:
        print("  [!] El presupuesto debe ser mayor que 0: opción CANCELADA.")
        return None
    return valor


def _preguntar_max_steps() -> int | None:
    """--max-steps opcional. Devuelve el número, o None para no añadir el flag.

    Enter (vacío) = no añadir nada y dejar el valor del bot. Un valor que no sea
    un entero positivo no se adivina: se cancela la opción (MenuCancelado).
    """
    crudo = _preguntar("  ¿Consultas máx. por objetivo? (Enter = el del bot):",
                       "")
    if not crudo:
        return None
    try:
        valor = int(crudo)
    except ValueError:
        raise MenuCancelado(f"'{crudo}' no es un número entero")
    if valor <= 0:
        raise MenuCancelado("--max-steps debe ser mayor que 0")
    return valor


def _pedir_nombres() -> str:
    """Nombres para --personas (separados por comas). "" si no escriben nada."""
    crudo = _preguntar('  Nombres a investigar, separados por comas '
                       '(ej. "Isidro Merillas Panero,Obdulia Pelaz Merino"):',
                       "")
    return ",".join(p.strip() for p in crudo.split(",") if p.strip())


def _pedir_ciclos() -> int:
    """Número de ciclos del autopiloto (Enter = 1). Si no vale, cancela."""
    crudo = _preguntar("  ¿Cuántos ciclos completos? [1]:", "1")
    try:
        valor = int(crudo)
    except ValueError:
        raise MenuCancelado(f"'{crudo}' no es un número entero")
    if valor <= 0:
        raise MenuCancelado("el número de ciclos debe ser mayor que 0")
    return valor


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


def _contar_salida(ruta: Path) -> tuple[int | None, str]:
    """Recuento de lo contable de un fichero de salida (o (None, "")).

    Sirve para decir "arbol_hallazgos.json: 58 hallazgos (+6)" en vez de solo
    "ha cambiado": lo que se quiere saber al terminar una acción es CUÁNTO ha
    crecido el trabajo, no el tamaño en bytes.
    """
    try:
        if ruta.suffix == ".jsonl":
            with ruta.open(encoding="utf-8", errors="replace") as f:
                return sum(1 for linea in f if linea.strip()), "líneas"
        if ruta.suffix == ".json":
            datos = json.loads(ruta.read_text(encoding="utf-8"))
            if isinstance(datos, list):
                return len(datos), "elementos"
            if isinstance(datos, dict):
                for clave, etiqueta in (("personas", "personas"),
                                        ("frontera", "entradas de frontera"),
                                        ("hallazgos", "hallazgos"),
                                        ("objetivos", "objetivos")):
                    if isinstance(datos.get(clave), list):
                        return len(datos[clave]), etiqueta
                return len(datos), "claves"
    except (OSError, ValueError, TypeError):
        pass
    return None, ""


def instantanea_salidas(base: Path | None = None) -> dict:
    """Foto de los ficheros de salida (mtime, tamaño y recuento)."""
    base = base if base is not None else BASE_DIR
    foto: dict = {}
    for nombre in FICHEROS_VIGILADOS:
        ruta = base / nombre
        try:
            datos = ruta.stat()
        except OSError:
            continue
        cantidad, etiqueta = _contar_salida(ruta)
        foto[nombre] = (datos.st_mtime, datos.st_size, cantidad, etiqueta)
    return foto


def resumen_generados(antes: dict, base: Path | None = None) -> list[str]:
    """Qué ficheros de salida han cambiado desde `antes`, con su recuento.

    Devuelve las líneas ya formateadas (vacío si no cambió nada). Se compara
    mtime Y tamaño: en un sistema de ficheros con marcas de 1 segundo, dos
    escrituras dentro del mismo segundo se distinguen por el tamaño.
    """
    lineas: list[str] = []
    for nombre, (mtime, tamano, cantidad, etiqueta) in \
            instantanea_salidas(base).items():
        previo = antes.get(nombre)
        if previo is None:
            detalle = f"{cantidad} {etiqueta}" if cantidad is not None else \
                f"{tamano} bytes"
            lineas.append(f"   + {nombre}: CREADO ({detalle})")
            continue
        if (mtime, tamano) == (previo[0], previo[1]):
            continue
        texto = f"   ~ {nombre}: {tamano} bytes"
        if cantidad is not None:
            texto += f", {cantidad} {etiqueta}"
            if previo[2] is not None:
                delta = cantidad - previo[2]
                texto += f" ({delta:+d})"
        lineas.append(texto)
    return lineas


def _ejecutar_con_resumen(argv: list[str], *, opcion: str, descripcion: str,
                          sin_log: bool = False) -> int:
    """Lanza el comando y, al terminar, dice QUÉ ha generado o actualizado.

    Es lo que hacía el menú antiguo ya retirado tras cada acción, y que se
    había perdido al pasar a subprocesos: saber si la noche ha dejado algo nuevo
    en el disco sin abrir el explorador de ficheros.
    """
    antes = instantanea_salidas()
    codigo = _correr(argv, opcion=opcion, descripcion=descripcion,
                     sin_log=sin_log)
    lineas = resumen_generados(antes)
    if lineas:
        print("\n  Esta acción ha generado o actualizado:")
        for linea in lineas:
            print(linea)
    else:
        print("\n  (esta acción no ha cambiado ningún fichero de salida)")
    return codigo


def _lanzar_gastando(argv: list[str], *, opcion: str, descripcion: str,
                     sin_log: bool = False, aviso: str = "") -> int:
    """Enseña el comando, pide CONFIRMACIÓN explícita (Enter = n) y lo lanza.

    La usan todas las opciones que gastan dinero, para que el texto y las
    reglas sean idénticos en todas: un comando, un aviso y una confirmación.
    Devuelve 0 si se cancela (no se ha gastado nada).
    """
    print(f"\n  Comando exacto: {mostrar_argv(argv)}")
    if aviso:
        print(f"  {aviso}")
    if not _confirmar("  Esto puede gastar dinero en OpenRouter. "
                      "¿Continuar? (s/n, Enter = n):"):
        print("  Cancelado: no se ha gastado nada.")
        return 0
    return _ejecutar_con_resumen(argv, opcion=opcion, descripcion=descripcion,
                                 sin_log=sin_log)


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
    """Opción 3: --diagnostico (0 tokens), con --test-llm opcional.

    --test-llm hace un ping REAL a cada modelo: confirma que la clave y el
    modelo funcionan, y cuesta unos pocos tokens. Por eso es una subpregunta
    con Enter = no.
    """
    argv = [interprete, "main.py", "--diagnostico"]
    if _confirmar("  ¿Hacer además el ping REAL a los modelos (--test-llm)? "
                  "Consume unos pocos tokens (s/n, Enter = n):"):
        argv.append("--test-llm")
    descripcion = "main.py --diagnostico" + (
        " --test-llm" if "--test-llm" in argv else "")
    print(f"\n  Comando exacto: {mostrar_argv(argv)}")
    return _correr(argv, opcion="3", descripcion=descripcion, sin_log=sin_log)


def accion_fase2(interprete: str, sin_log: bool = False) -> int:
    """Opción 4: fase 2 con presupuesto libre y --max-steps opcional.

    Gasta dinero: presupuesto obligatorio y confirmación EXPLÍCITA (Enter
    cancela). Un presupuesto inválido cancela la opción; el menú nunca adivina
    cuánto puedes gastar.
    """
    valor = _pedir_presupuesto()
    if valor is None:
        return 0
    argv = [interprete, "main.py", "--fase", "2",
            "--presupuesto-max", f"{valor:g}"]
    pasos = _preguntar_max_steps()
    if pasos is not None:
        argv += ["--max-steps", str(pasos)]
    return _lanzar_gastando(
        argv, opcion="4",
        descripcion=f"fase 2 (presupuesto ${valor:g})", sin_log=sin_log,
        aviso="(el tope lo aplica el bot: al alcanzarlo guarda el progreso y "
              "para)")


def accion_ciclo(interprete: str, sin_log: bool = False) -> int:
    """Opción 12: autopiloto de N ciclos (fase 1 -> fase 2 -> commit -> frontera).

    Es la opción que más gasta (N ciclos completos): presupuesto obligatorio y
    confirmación explícita con Enter = n.
    """
    ciclos = _pedir_ciclos()
    valor = _pedir_presupuesto()
    if valor is None:
        return 0
    argv = [interprete, "main.py", "--ciclo", str(ciclos),
            "--presupuesto-max", f"{valor:g}"]
    return _lanzar_gastando(
        argv, opcion="12",
        descripcion=f"autopiloto {ciclos} ciclos (${valor:g})",
        sin_log=sin_log,
        aviso="(el autopiloto repite fase 1 + fase 2 + frontera en cada ciclo "
              "hasta agotar el presupuesto o los ciclos)")


def accion_fase1(interprete: str, sin_log: bool = False) -> int:
    """Opción 13: solo fase 1 (búsqueda), con presupuesto y confirmación.

    Gasta: cada consulta es una búsqueda de Tavily y el filtro de páginas usa
    el LLM. Con el corpus ya lleno no suele hacer falta.
    """
    valor = _pedir_presupuesto()
    if valor is None:
        return 0
    argv = [interprete, "main.py", "--fase", "1",
            "--presupuesto-max", f"{valor:g}"]
    pasos = _preguntar_max_steps()
    if pasos is not None:
        argv += ["--max-steps", str(pasos)]
    return _lanzar_gastando(
        argv, opcion="13", descripcion=f"fase 1 (presupuesto ${valor:g})",
        sin_log=sin_log)


def accion_personas(interprete: str, sin_log: bool = False) -> int:
    """Opción 14: investigar SOLO a las personas que se escriban (--personas).

    Filtra la frontera a esos nombres y ejecuta fase 1 + fase 2. Gasta, así que
    pide presupuesto y confirmación como las demás.
    """
    nombres = _pedir_nombres()
    if not nombres:
        print("  [!] Sin nombres no hay nada que filtrar: opción CANCELADA.")
        return 0
    valor = _pedir_presupuesto()
    if valor is None:
        return 0
    argv = [interprete, "main.py", "--personas", nombres,
            "--presupuesto-max", f"{valor:g}"]
    pasos = _preguntar_max_steps()
    if pasos is not None:
        argv += ["--max-steps", str(pasos)]
    return _lanzar_gastando(
        argv, opcion="14",
        descripcion=f"investigación filtrada: {nombres[:60]}", sin_log=sin_log)


def accion_chuleta(interprete: str, sin_log: bool = False) -> int:
    """Opción 15: imprime la chuleta de los comandos avanzados.

    NO ejecuta nada: los comandos que no están en el menú (--solicitudes,
    --importar-propios, --reclasificar, --ensenada, --probar-conectores) se
    copian y pegan desde aquí. Así el menú no crece con opciones de uso
    esporádico, pero tampoco se olvidan.
    """
    print("\n  Comandos avanzados (se ejecutan a mano; el menú solo los "
          "enseña):")
    print(f"  intérprete: {interprete}\n")
    for comando, descripcion in CHULETA_AVANZADOS:
        print(f"   {interprete} main.py {comando}")
        print(f"       {descripcion}")
    print(f"\n   {interprete} main.py --help      (todas las opciones)")
    print("   (nada de esto se ha ejecutado: esto es solo una chuleta)")
    return 0


def accion_aceptar(interprete: str, sin_log: bool = False) -> int:
    """Opción 5: --aceptar. Escribe en el árbol familiar: confirmación
    explícita (Enter cancela)."""
    argv = [interprete, "main.py", "--aceptar"]
    print(f"\n  Comando exacto: {mostrar_argv(argv)}")
    if not _confirmar("  Esto puede aceptar candidatos y modificar estado. "
                      "¿Continuar? (s/n, Enter = n):"):
        print("  Cancelado: el árbol no se ha tocado.")
        return 0
    return _ejecutar_con_resumen(argv, opcion="5",
                                 descripcion="main.py --aceptar",
                                 sin_log=sin_log)


def accion_frontera(interprete: str, sin_log: bool = False) -> int:
    """Opción 6: --frontera (no gasta). Reescribe el estado y el informe de
    progreso, así que también dice qué ha cambiado."""
    return _ejecutar_con_resumen([interprete, "main.py", "--frontera"],
                                 opcion="6",
                                 descripcion="main.py --frontera",
                                 sin_log=sin_log)


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
    "10": accion_fonttools, "11": accion_ultimo_log, "12": accion_ciclo,
    "13": accion_fase1, "14": accion_personas, "15": accion_chuleta,
}


# ============================== MENÚ =======================================

def _pintar_menu(version: str, interprete: str) -> None:
    print("=" * 62)
    print(f" AGENTE GENEALÓGICO — MENÚ DE TAREAS   (VERSION: {version})")
    print("=" * 62)
    for numero in ORDEN_OPCIONES:
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
        ultima = ORDEN_OPCIONES[-1]
        print(f"  [!] Opción '{eleccion}' no reconocida (0-{ultima}).")
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
