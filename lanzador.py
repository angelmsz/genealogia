#!/usr/bin/env python3
"""
lanzador.py — Menú interactivo del agente genealógico v10.0.

Para qué existe (PARTE B de la v9.0): dejar de escribir los comandos de
main.py a mano. Un menú numerado cubre TODOS los flags existentes de
main.py preguntando solo lo imprescindible, con valores por defecto
sensatos y mostrando ANTES de cada ejecución el comando equivalente
(así se aprenden los flags sin tener que memorizarlos).

Diseño (importante):
  - NO DUPLICA LÓGICA: cada opción llama internamente a las MISMAS
    funciones que usa argparse en main.py (main.fase1, main.fase2,
    main.ejecutar_ciclos, agent.gedcom.diagnostico, ...) dentro del
    MISMO proceso. Ventaja extra: GASTO acumula el gasto de toda la
    sesión del menú, que se muestra en la cabecera.
  - Al arrancar ejecuta un chequeo rápido SILENCIOSO equivalente a
    --diagnostico (sin gastar tokens: captura la salida y cuenta los
    avisos) e incluye el estado del llama-server de OCR local (v9.0).
  - Ctrl+C no deja traceback: "Cancelado. Volviendo al menú."
  - Multiplataforma (Windows/Linux/macOS). En Windows, lanzador.ps1
    simplemente llama a este archivo para poder hacer doble clic.

Uso:
  python lanzador.py                (o doble clic en lanzador.ps1)
  python lanzador.py --sin-chequeo  (salta el chequeo de arranque; también
                                     con la variable LANZADOR_SIN_CHEQUEO=1)
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
from pathlib import Path

from utils import ui  # v10.4 (P0): registro de ejecución del menú

BASE_DIR = Path(__file__).resolve().parent

# Ficheros de salida que se vigilan para el resumen de "qué se ha generado"
# tras cada acción (mtime + tamaño + recuento cuando el formato lo permite).
FICHEROS_VIGILADOS = [
    "corpus_bruto.json",            # list de fragmentos (fase 1)
    "informe_fase1.json",           # dict v9.1: objetivos + archivos_a_consultar_in_situ + familysearch + ensenada
    "arbol_hallazgos.json",         # list de hallazgos auditados (fase 2)
    "arbol_refinado.json",          # dict: memoria + consolidación (fase 2)
    "arbol.ged",                    # GEDCOM 5.5.1 (fase 2)
    "estado_investigacion.json",    # dict: frontera + investigados
    "informe_progreso.md",          # informe legible de progreso
    "solicitudes.json",             # --solicitudes
    "solicitudes.md",               # --solicitudes
    "candidatos_ensenada.json",     # --ensenada
    "familia_conocida.json",        # --aceptar (commit)
    "registro_confirmaciones.jsonl" # --aceptar (registro append-only)
]

VERSION = "10.2"

MENU = """
========================================
 AGENTE GENEALÓGICO v{version} — MENÚ PRINCIPAL
========================================
 1. Ejecutar investigación completa (fase 1 + fase 2)
 2. Solo fase 1 (búsqueda)
 3. Solo fase 2 (refinado + GEDCOM)
 4. Ver frontera priorizada (qué investigar primero)
 5. Autopiloto: N ciclos completos
 6. Filtrar por personas concretas
 7. Generar solicitudes de partidas (emails)
 8. Hipótesis Catastro de Ensenada
 9. Diagnóstico completo (ver todos los avisos)
10. Probar conectores de archivos en vivo
11. Importar fotos propias (documentos_propios/)
12. Aceptar hallazgos verificados (commit al árbol) [--aceptar]
13. Reclasificar hallazgos existentes [gratis, no gasta tokens]
14. Resumen de la última tanda [gratis, no gasta tokens]
 0. Salir
========================================"""


# ============================ EXCEPCIÓN DE CONTROL ==========================

class AccionCancelada(Exception):
    """El usuario canceló la acción (respuesta 'n' en una confirmación o
    parámetro vacío). El menú vuelve a mostrarse sin traceback."""


# ============================ HELPERS PUROS (testables) =====================

def parsear_indices(texto: str, n_opciones: int) -> list[int]:
    """Convierte "1,3,5" (con rangos tipo "2-4" y espacios) en [1, 3, 5].

    Devuelve [] si no hay nada válido. Los índices son 1-based y se
    filtran los que queden fuera de [1, n_opciones]. Se usa en la opción
    6 para elegir personas de familia_conocida.json por número.
    """
    elegidos: list[int] = []
    texto = (texto or "").strip()
    if not texto:
        return []
    for trozo in texto.replace(";", ",").split(","):
        trozo = trozo.strip()
        if not trozo:
            continue
        if "-" in trozo and not trozo.startswith("-"):
            try:
                ini, fin = trozo.split("-", 1)
                ini_i, fin_i = int(ini), int(fin)
            except ValueError:
                continue
            if ini_i > fin_i:
                ini_i, fin_i = fin_i, ini_i
            elegidos.extend(range(ini_i, fin_i + 1))
        else:
            try:
                elegidos.append(int(trozo))
            except ValueError:
                continue
    return [i for i in elegidos if 1 <= i <= n_opciones] or []


def construir_comando(flags: list[str]) -> str:
    """Construye el comando equivalente de main.py a partir de los flags.

    "python main.py --ciclo 3 --presupuesto-max 2.0" con los valores que
    contengan espacios entrecomillados (p. ej. --personas "A, B").
    """
    partes = ["python", "main.py"]
    for f in flags:
        if " " in f:
            partes.append(f'"{f}"')
        else:
            partes.append(f)
    return " ".join(partes)


def contar_json(ruta: Path) -> tuple[int | None, str]:
    """Cuenta lo contable de un fichero de salida para el resumen.

    Devuelve (numero, etiqueta). numero es None si no se puede contar
    (GEDCOM, markdown...), y entonces la etiqueta describe otra cosa.
    """
    try:
        if ruta.suffix == ".jsonl":
            with ruta.open(encoding="utf-8") as f:
                n = sum(1 for line in f if line.strip())
            return n, "confirmaciones"
        if ruta.suffix == ".json":
            datos = json.loads(ruta.read_text(encoding="utf-8"))
            if isinstance(datos, list):
                return len(datos), "elementos"
            if isinstance(datos, dict):
                if "personas" in datos and isinstance(datos["personas"], list):
                    return len(datos["personas"]), "personas"
                if "frontera" in datos and isinstance(datos["frontera"], list):
                    return len(datos["frontera"]), "entradas de frontera"
                if "hallazgos" in datos and isinstance(datos["hallazgos"], list):
                    return len(datos["hallazgos"]), "hallazgos"
                # v9.1: informe_fase1.json ahora es dict con "objetivos"
                if "objetivos" in datos and isinstance(datos["objetivos"], list):
                    return len(datos["objetivos"]), "objetivos (+secciones v9.1)"
                return len(datos), "claves"
    except (OSError, ValueError):
        pass
    return None, ""


# ============================ ENTRADA INTERACTIVA ===========================

def _preguntar(mensaje: str, defecto: str = "") -> str:
    """input() con valor por defecto: Enter devuelve el default."""
    try:
        respuesta = input(f"{mensaje} ").strip()
    except EOFError:
        raise AccionCancelada("entrada cerrada")
    return respuesta if respuesta else defecto


def _pedir_entero(mensaje: str, defecto: int, minimo: int,
                  maximo: int) -> int:
    """Pide un entero con default visible [entre corchetes] y rango
    validado (repite si está mal). Enter devuelve el default."""
    while True:
        crudo = _preguntar(f"{mensaje} [{defecto}]:", str(defecto))
        try:
            valor = int(crudo)
        except ValueError:
            print(f"  [!] '{crudo}' no es un número; prueba otra vez.")
            continue
        if not (minimo <= valor <= maximo):
            print(f"  [!] debe estar entre {minimo} y {maximo}.")
            continue
        return valor


def _pedir_presupuesto() -> float | None:
    """Pide el presupuesto máximo en $ (el control de gasto del proyecto
    trabaja en dólares). Enter = 2.0 (default sensato del menú);
    'sin'/'none'/'ilimitado' = sin tope (comportamiento por defecto de
    main.py); 0 también se interpreta como sin tope para evitar la
    trampa de "0 = no puedo gastar nada"."""
    while True:
        crudo = _preguntar("¿Presupuesto máximo en $ (Enter=2.0; 'sin'=sin "
                           "límite)?", "2.0").lower()
        if crudo in ("sin", "none", "ilimitado", "no", "0", ""):
            return None
        try:
            valor = float(crudo.replace(",", "."))
        except ValueError:
            print(f"  [!] '{crudo}' no es un número; prueba otra vez.")
            continue
        if valor <= 0:
            return None
        return valor


def _confirmar(mensaje: str, defecto: bool = True) -> bool:
    """Pregunta s/n. Enter usa el default. 'n' lanza el flujo de vuelta."""
    sugerencia = "s/n" if defecto else "n/s"
    crudo = _preguntar(f"{mensaje} [{sugerencia}]:",
                       "s" if defecto else "n").lower()
    return crudo in ("s", "si", "sí", "y", "yes")


def _confirmar_comando(flags: list[str]) -> None:
    """Muestra el comando equivalente y pide confirmación: así el usuario
    aprende los flags de main.py sin tener que escribirlos."""
    print(f"\n  Comando equivalente:  {construir_comando(flags)}")
    if not _confirmar("¿Ejecutar?"):
        raise AccionCancelada()
    print()


def _pausa() -> None:
    """Pulsa Enter para volver al menú (sin cerrar el programa)."""
    try:
        input("\n(Pulsa Enter para volver al menú) ")
    except EOFError:
        pass


# ============================ IMPORTACIÓN DEL PROYECTO ======================

_PROYECTO = None


def importar_proyecto():
    """Importa perezosamente el proyecto (config exige claves en .env).

    Devuelve un argparse.Namespace con las MISMAS funciones que usa
    argparse en main.py — el lanzador no reescribe nada de la lógica.
    Los errores de importación (claves ausentes, .env sin copiar) se
    muestran como mensaje amable, no como traceback.
    """
    global _PROYECTO
    if _PROYECTO is not None:
        return _PROYECTO
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))
    try:
        import main as main_mod
        from agent.frontera import (calcular_frontera, cometer_confirmaciones,
                                    generar_informe_progreso, guardar_estado,
                                    mostrar_frontera)
        from agent.gedcom import (diagnostico, generar_candidatos_ensenada,
                                  generar_solicitudes,
                                  importar_documentos_propios)
        from scrapers.archivos import probar_conectores
        from utils.llm import GASTO, resumen_gasto
        from config import MAX_STEPS, FAMILIA_JSON_PATH, get_db
        reclasificar_comando = main_mod.reclasificar_comando
    except SystemExit as e:
        print(f"\n  [!] No se puede cargar el proyecto:\n      {e}")
        print("      Copia .env.example a .env, pega tus claves de Tavily y "
              "OpenRouter y vuelve a arrancar.")
        raise AccionCancelada(str(e))
    except ImportError as e:
        print(f"\n  [!] Falta una dependencia: {e}")
        print("      Instálalas con:  pip install -r requirements.txt")
        raise AccionCancelada(str(e))
    # Igual que main(): ids estables en familia_conocida.json antes de
    # cualquier acción (idempotente, solo AÑADE el campo 'id').
    try:
        main_mod._asegurar_ids_estables()
    except Exception:
        pass  # no rompemos el menú por esto (main.py tampoco)
    _PROYECTO = argparse.Namespace(
        main_mod=main_mod, get_db=get_db, MAX_STEPS=MAX_STEPS,
        FAMILIA_JSON_PATH=FAMILIA_JSON_PATH, GASTO=GASTO,
        resumen_gasto=resumen_gasto, diagnostico=diagnostico,
        calcular_frontera=calcular_frontera,
        mostrar_frontera=mostrar_frontera, guardar_estado=guardar_estado,
        generar_informe_progreso=generar_informe_progreso,
        cometer_confirmaciones=cometer_confirmaciones,
        generar_solicitudes=generar_solicitudes,
        generar_candidatos_ensenada=generar_candidatos_ensenada,
        importar_documentos_propios=importar_documentos_propios,
        probar_conectores=probar_conectores,
        reclasificar_comando=reclasificar_comando,
    )
    return _PROYECTO


@contextlib.contextmanager
def _conexion_bd():
    """get_db() con cierre garantizado, como hace main() en su finally."""
    proy = importar_proyecto()
    conn = proy.get_db()
    try:
        yield conn, proy
    finally:
        conn.close()


# ============================ NAMESPACE DE ARGS =============================

def _namespace(**kwargs) -> argparse.Namespace:
    """argparse.Namespace con TODOS los campos que usan las funciones de
    main.py, con los mismos defaults que argparse (fase="all",
    max_steps=MAX_STEPS, personas="", sin_cache=False...)."""
    proy = importar_proyecto()
    base = dict(fase="all", max_steps=proy.MAX_STEPS, personas="",
                sin_cache=False, presupuesto_max=None, ciclo=0,
                diagnostico=False, test_llm=False, solicitudes=False,
                frontera=False, aceptar=False, ensenada=False,
                importar_propios=False, probar_conectores=False)
    base.update(kwargs)
    return argparse.Namespace(**base)


def _aplicar_presupuesto(valor: float | None) -> None:
    """Fija GASTO.presupuesto_max igual que main() (con su aviso)."""
    proy = importar_proyecto()
    proy.GASTO.presupuesto_max = valor
    if valor is not None:
        ya_gastado = proy.GASTO.coste
        aviso_extra = ""
        if ya_gastado >= valor:
            aviso_extra = ("  [!] OJO: la sesión ya lleva "
                           f"${ya_gastado:.4f} gastados: con este tope la "
                           "primera acción parará en seco.")
        print(f"  [!] Presupuesto máximo: ${valor:.2f} "
              f"(parada segura al alcanzarlo; el tope cuenta el gasto TOTAL "
              f"de esta sesión del menú, no solo la próxima acción)."
              f"{aviso_extra}")


# ============================ CHEQUEO RÁPIDO DE ARRANQUE ====================

def chequeo_rapido() -> tuple[str, int, int]:
    """Chequeo silencioso equivalente a --diagnostico, SIN gastar tokens.

    Ejecuta el diagnostico() real con la salida capturada (para eso no se
    ve nada en pantalla), cuenta sus avisos y errores, y añade el estado
    del llama-server (v9.0) si OCR_BACKEND es "llamacpp".

    Devuelve (linea_resumen, n_avisos, n_errores). La línea es del tipo:
      "✓ Chequeo: todo OK"  /  "⚠ Chequeo: 3 avisos (opción 9 para detalle)"
    Si el proyecto ni siquiera carga (claves ausentes), la línea lo dice
    claramente y los contadores vienen a -1.
    """
    avisos, errores = 0, -1
    detalle_fatal = ""
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))
    try:
        from dotenv import load_dotenv
        load_dotenv(BASE_DIR / ".env")
    except Exception:
        pass
    os.environ.setdefault("TAVILY_API_KEY", "__chequeo_pendiente__")
    os.environ.setdefault("OPENROUTER_API_KEY", "__chequeo_pendiente__")
    bufer = io.StringIO()
    try:
        import main as _main_mod  # fuerza la validación de claves de config
        from agent.gedcom import diagnostico as _diag
        with contextlib.redirect_stdout(bufer), \
                contextlib.redirect_stderr(bufer):
            errores = _diag(test_llm=False)
        # Los avisos se cuentan por su icono [!] en la salida capturada
        # (diagnostico() solo devuelve los ERRORES; los warn son avisos
        # opcionales: deps que faltan, servidores caídos...).
        avisos = bufer.getvalue().count("[!]")
    except SystemExit as e:
        detalle_fatal = str(e)
    except Exception as e:  # red, BD corrupta...: no bloqueamos el menú
        detalle_fatal = f"error inesperado: {str(e)[:100]}"
        errores = -1

    # v9.0 — estado del llama-server (solo si el backend llamacpp está
    # activo; es un GET corto a localhost, no gasta tokens).
    llama_txt = ""
    try:
        from config import OCR_BACKEND
        if OCR_BACKEND == "llamacpp":
            from scrapers.web import llamacpp_disponible
            ok, detalle = llamacpp_disponible()
            if ok:
                llama_txt = f" · llama-server: ✓ ({detalle})"
            else:
                avisos += 1
                llama_txt = " · llama-server: ✗ no arrancado (PDFs escaneados sin texto)"
    except Exception:
        pass

    if errores == -1:
        linea = (f"✗ Chequeo: el proyecto no carga ({detalle_fatal[:70]}). "
                 f"Corrige .env (opción 9 para detalle){llama_txt}")
    elif errores == 0 and avisos == 0:
        linea = f"✓ Chequeo: todo OK{llama_txt}"
    else:
        linea = (f"⚠ Chequeo: {avisos} avisos, {errores} errores "
                 f"(opción 9 para ver el detalle){llama_txt}")
    return linea, avisos, errores


# ============================ RESUMEN DE GENERADOS ==========================

def _snapshot_salidas() -> dict[str, tuple[float, int, int | None]]:
    """{(ruta): (mtime, tamaño, recuento)} de los ficheros vigilados."""
    salida = {}
    for nombre in FICHEROS_VIGILADOS:
        ruta = BASE_DIR / nombre
        try:
            st = ruta.stat()
        except OSError:
            continue
        n, _ = contar_json(ruta)
        salida[nombre] = (st.st_mtime, st.st_size, n)
    return salida


def resumen_generados(antes: dict) -> list[str]:
    """Compara el snapshot previo con el estado actual y devuelve las
    líneas de resumen: qué ficheros se han actualizado/creado y cuánto
    ha crecido lo contable (p. ej. '+12 hallazgos')."""
    lineas: list[str] = []
    ahora = _snapshot_salidas()
    for nombre, (mtime_ahora, tam_ahora, n_ahora) in ahora.items():
        previo = antes.get(nombre)
        if previo is None:
            n_txt = f" · {n_ahora} {contar_json(BASE_DIR / nombre)[1]}" \
                if n_ahora is not None else ""
            lineas.append(f"  ✓ {nombre} CREADO{n_txt}")
            continue
        mtime_prev, tam_prev, n_prev = previo
        if mtime_ahora <= mtime_prev and tam_ahora == tam_prev:
            continue
        etiqueta = contar_json(BASE_DIR / nombre)[1]
        if n_ahora is not None and n_prev is not None and n_ahora != n_prev:
            delta = n_ahora - n_prev
            signo = "+" if delta > 0 else ""
            lineas.append(f"  ✓ {nombre} ({etiqueta}: {n_prev} -> {n_ahora}, "
                          f"{signo}{delta})")
        else:
            lineas.append(f"  ✓ {nombre} actualizado")
    return lineas


# ============================ ACCIONES DEL MENÚ =============================

def _accion_investigacion(fases: str, personas: str = "",
                          titulo: str = "") -> None:
    """Opciones 1/2/3/6: fase 1 + fase 2 (o una sola fase) con los
    parámetros imprescindibles. Son las mismas funciones de main()."""
    if titulo:
        print(f"\n=== {titulo} ===")
    presupuesto = _pedir_presupuesto()
    proy = importar_proyecto()
    max_steps = _pedir_entero("¿Consultas máx. por objetivo?",
                              proy.MAX_STEPS, 1, 200)
    sin_cache = _confirmar("¿Repetir también búsquedas ya en caché "
                           "(--sin-cache)?", False)
    flags: list[str] = []
    if fases != "all":
        flags += ["--fase", fases]
    if personas:
        flags += ["--personas", personas]
    if presupuesto is not None:
        flags += ["--presupuesto-max", f"{presupuesto:g}"]
    flags += ["--max-steps", str(max_steps)]
    if sin_cache:
        flags.append("--sin-cache")
    _confirmar_comando(flags)
    _aplicar_presupuesto(presupuesto)
    args = _namespace(fase=fases, max_steps=max_steps, personas=personas,
                      sin_cache=sin_cache, presupuesto_max=presupuesto)
    antes = _snapshot_salidas()
    with _conexion_bd() as (conn, proy):
        proy.main_mod._asegurar_ids_estables()
        if fases in ("1", "all"):
            proy.main_mod.fase1(args, conn)
        if fases in ("2", "all"):
            if fases == "all" and proy.GASTO.presupuesto_max is not None \
                    and proy.GASTO.coste >= proy.GASTO.presupuesto_max:
                print("  [!] Fase 2 omitida: el presupuesto se agotó en "
                      "la fase 1.")
            else:
                proy.main_mod.fase2(args, conn)
    proy.resumen_gasto()
    _mostrar_resumen(antes)


def _mostrar_resumen(antes: dict) -> None:
    lineas = resumen_generados(antes)
    if lineas:
        print("\n  Generado/actualizado en esta acción:")
        for linea in lineas:
            print(linea)
    else:
        print("\n  (esta acción no ha cambiado ningún fichero de salida)")


def _opcion_1() -> None:
    _accion_investigacion("all", titulo="Investigación completa "
                                         "(fase 1 + fase 2)")


def _opcion_2() -> None:
    _accion_investigacion("1", titulo="Solo fase 1 (búsqueda)")


def _opcion_3() -> None:
    _accion_investigacion("2", titulo="Solo fase 2 (refinado + GEDCOM)")


def _opcion_4() -> None:
    """--frontera: cola priorizada (no gasta tokens)."""
    proy = importar_proyecto()
    print("\n  Comando equivalente:  " + construir_comando(["--frontera"]))
    antes = _snapshot_salidas()
    estado = proy.calcular_frontera()
    proy.mostrar_frontera(estado)
    proy.guardar_estado(estado)
    proy.generar_informe_progreso(estado=estado)
    _mostrar_resumen(antes)


def _opcion_5() -> None:
    """--ciclo N: autopiloto completo."""
    proy = importar_proyecto()
    n_ciclos = _pedir_entero("¿Cuántos ciclos?", 3, 1, 100)
    max_steps = _pedir_entero("¿Consultas máx. por objetivo?",
                              proy.MAX_STEPS, 1, 200)
    presupuesto = _pedir_presupuesto()
    flags = ["--ciclo", str(n_ciclos)]
    if presupuesto is not None:
        flags += ["--presupuesto-max", f"{presupuesto:g}"]
    flags += ["--max-steps", str(max_steps)]
    _confirmar_comando(flags)
    _aplicar_presupuesto(presupuesto)
    args = _namespace(ciclo=n_ciclos, max_steps=max_steps,
                      presupuesto_max=presupuesto)
    antes = _snapshot_salidas()
    with _conexion_bd() as (conn, proy):
        proy.main_mod.ejecutar_ciclos(args, conn)
    proy.resumen_gasto()
    _mostrar_resumen(antes)


def _opcion_6() -> None:
    """--personas: lista numerada de familia_conocida.json y elección por
    número ("1,3,5") o nombres escritos a mano."""
    proy = importar_proyecto()
    ruta = BASE_DIR / proy.FAMILIA_JSON_PATH
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"  [!] No puedo leer {proy.FAMILIA_JSON_PATH}: {e}")
        raise AccionCancelada()
    personas = datos.get("personas", []) if isinstance(datos, dict) else []
    if not personas:
        print(f"  [!] {proy.FAMILIA_JSON_PATH} no tiene personas que listar.")
        raise AccionCancelada()
    print(f"\n  Personas en {proy.FAMILIA_JSON_PATH}:")
    for i, p in enumerate(personas, 1):
        nombre = p.get("nombre", "?")
        nac = p.get("nacimiento") or {}
        anio = nac.get("anio") or nac.get("fecha") or "?"
        municipio = nac.get("municipio") or ""
        extra = f" · {municipio}" if municipio else ""
        print(f"   {i:3d}. {nombre} ({anio}){extra}")
    print("\n  Elige por número (ej. 1,3,5 o rangos 2-4), escribe nombres, "
          "o Enter para volver:")
    try:
        eleccion = input("  ¿Quiénes? ").strip()
    except EOFError:
        raise AccionCancelada()
    if not eleccion:
        raise AccionCancelada()
    indices = parsear_indices(eleccion, len(personas))
    if indices:
        elegidos = [personas[i - 1].get("nombre", "?") for i in indices]
    else:
        elegidos = [n.strip() for n in eleccion.split(",") if n.strip()]
    if not elegidos:
        print("  [!] No he entendido la selección.")
        raise AccionCancelada()
    filtro = ",".join(elegidos)
    print(f"\n  Se investigará SOLO a: {filtro}")
    _accion_investigacion("all", personas=filtro,
                          titulo=f"Investigación filtrada: {filtro}")


def _opcion_7() -> None:
    """--solicitudes: emails para pedir partidas (no gasta tokens)."""
    proy = importar_proyecto()
    print("\n  Comando equivalente:  "
          + construir_comando(["--solicitudes"]))
    antes = _snapshot_salidas()
    proy.generar_solicitudes()
    _mostrar_resumen(antes)


def _opcion_8() -> None:
    """--ensenada: hipótesis del Catastro de 1752 (gasta algunos tokens)."""
    proy = importar_proyecto()
    presupuesto = _pedir_presupuesto()
    flags = ["--ensenada"]
    if presupuesto is not None:
        flags += ["--presupuesto-max", f"{presupuesto:g}"]
    _confirmar_comando(flags)
    _aplicar_presupuesto(presupuesto)
    antes = _snapshot_salidas()
    proy.generar_candidatos_ensenada()
    proy.resumen_gasto()
    _mostrar_resumen(antes)


def _opcion_9() -> None:
    """--diagnostico completo (todo el detalle en pantalla)."""
    proy = importar_proyecto()
    ping = _confirmar("¿Hacer además un ping real a los LLMs? "
                      "(gasta unos pocos tokens)", False)
    print()
    antes = _snapshot_salidas()
    proy.diagnostico(test_llm=ping)
    _mostrar_resumen(antes)


def _opcion_10() -> None:
    """--probar-conectores: SIGA/ADDO/PARES en vivo (red, sin tokens)."""
    proy = importar_proyecto()
    print("\n  Comando equivalente:  "
          + construir_comando(["--probar-conectores"]))
    proy.probar_conectores()


def _opcion_11() -> None:
    """--importar-propios: fotos de documentos_propios/ -> OCR local (v10.0, gratis)."""
    proy = importar_proyecto()
    presupuesto = _pedir_presupuesto()
    flags = ["--importar-propios"]
    if presupuesto is not None:
        flags += ["--presupuesto-max", f"{presupuesto:g}"]
    _confirmar_comando(flags)
    _aplicar_presupuesto(presupuesto)
    antes = _snapshot_salidas()
    proy.importar_documentos_propios()
    proy.resumen_gasto()
    _mostrar_resumen(antes)


def _opcion_12() -> None:
    """--aceptar: commit de lo verificado al árbol (con backup)."""
    proy = importar_proyecto()
    flags = ["--aceptar"]
    _confirmar_comando(flags)
    antes = _snapshot_salidas()
    resumen = proy.cometer_confirmaciones(aplicar=True)
    estado = proy.calcular_frontera()
    estado["ciclo"] = max(estado.get("ciclo", 0), 1)
    proy.guardar_estado(estado)
    proy.generar_informe_progreso()
    print(f"\n  ✓ Árbol actualizado: {resumen['actualizadas']} eventos, "
          f"{resumen['evidencias']} evidencias nuevas, "
          f"{resumen['nuevas']} fichas nuevas.")
    _mostrar_resumen(antes)


def _opcion_13() -> None:
    """--reclasificar (v9.2): reclasifica el árbol YA guardado con el
    clasificador determinista de evidencia. GRATIS: ni un token, ni una
    llamada de red (no pide presupuesto ni confirmación)."""
    proy = importar_proyecto()
    print("\n  Comando equivalente:  "
          + construir_comando(["--reclasificar"]))
    antes = _snapshot_salidas()
    totales = proy.reclasificar_comando()
    _mostrar_resumen(antes)
    if totales is None:
        print("  [!] No hay arbol_refinado.json que reclasificar "
              "(ejecuta antes la fase 2).")


def _opcion_14() -> None:
    """resumen_noche.py (v10.2): pinta en consola el resumen de la
    última tanda de investigación (corpus, hallazgos por nivel de
    evidencia, árbol, frontera y cachés). GRATIS: 100% offline, ni un
    token ni una llamada de red. Es lo que se esperaba poder ejecutar
    con 'python resumen_noche.py' tras dejar el autopiloto toda la
    noche."""
    importar_proyecto()   # claves de .env presentes (resumen_noche
                          # también importa config, que las exige)
    print("\n  Comando equivalente:  python resumen_noche.py")
    import resumen_noche
    resumen_noche.main()


ACCIONES = {
    "1": _opcion_1, "2": _opcion_2, "3": _opcion_3, "4": _opcion_4,
    "5": _opcion_5, "6": _opcion_6, "7": _opcion_7, "8": _opcion_8,
    "9": _opcion_9, "10": _opcion_10, "11": _opcion_11, "12": _opcion_12,
    "13": _opcion_13, "14": _opcion_14,
}


# ============================ LÍNEA DE GASTO =================================

def _linea_gasto() -> str:
    """Gasto acumulado de la SESIÓN (todas las acciones del menú comparten
    el mismo proceso, así que GASTO va sumando). El proyecto contabiliza
    en dólares; no hay registro histórico entre ejecuciones distintas."""
    try:
        proy = importar_proyecto()
    except AccionCancelada:
        return "Gasto sesión: (proyecto no cargado)"
    g = proy.GASTO
    partes = [f"Gasto sesión: ${g.coste:.4f} · {g.llamadas} llamadas LLM · "
              f"{g.busquedas_tavily} búsquedas Tavily"]
    if g.presupuesto_max is not None:
        restante = g.presupuesto_max - g.coste
        partes.append(f"presupuesto ${g.presupuesto_max:.2f} "
                      f"({'AGOTADO' if restante <= 0 else f'quedan ${restante:.2f}'})")
    return " · ".join(partes)


def _linea_llama() -> str:
    """Estado vivo del llama-server (v9.0) para la cabecera del menú, con
    la FAMILIA de modelo activa (v10.1). GET corto a localhost: connection
    refused es instantáneo, no frena."""
    try:
        from config import OCR_BACKEND, OCR_LLAMACPP_FAMILIA
        if OCR_BACKEND != "llamacpp":
            return ""
        from scrapers.web import llamacpp_disponible
        ok, detalle = llamacpp_disponible()
        if ok:
            return (f"OCR local: llama-server ✓ ({detalle[:40]} · "
                    f"familia {OCR_LLAMACPP_FAMILIA})")
        return (f"OCR local: llama-server ✗ (sin OCR de PDFs escaneados · "
                f"familia {OCR_LLAMACPP_FAMILIA})")
    except Exception:
        return ""


# ============================ MENÚ PRINCIPAL ================================

def _pintar_menu(linea_chequeo: str | None) -> None:
    print(MENU.format(version=VERSION))
    if linea_chequeo:
        print(f" {linea_chequeo}")
    print(f" {_linea_gasto()}")
    llama = _linea_llama()
    if llama:
        print(f" {llama}")
    print()


def _despachar(eleccion: str, estado_chequeo: list) -> None:
    accion = ACCIONES.get(eleccion)
    if accion is None:
        print(f"  [!] Opción '{eleccion}' no reconocida "
              f"(0-14).")
        return
    try:
        accion()
    except AccionCancelada:
        print("\n  Cancelado. Volviendo al menú.")
    except KeyboardInterrupt:
        print("\n  Cancelado. Volviendo al menú.")
    except SystemExit as e:
        # Algunos modos de main.py hacen raise SystemExit: no es un error.
        print(f"\n  La acción terminó: {e or 'OK'}")
    except Exception as e:
        # Última red de seguridad: nada de traceback feo en el menú.
        print(f"\n  [x] Error inesperado: {str(e)[:200]}")
        print("      Ejecuta la opción 9 (diagnóstico) para más detalle.")


def main() -> None:
    _preparar_entorno()
    # v10.4 (P0): registro de ejecución. El menú en sí no se registra (usa
    # print), pero SÍ todo lo que hace el agente desde aquí dentro: fases,
    # conectores, OCR, avisos y errores. El cierre lo garantiza atexit.
    ui.iniciar_log()
    sin_chequeo = ("--sin-chequeo" in sys.argv
                   or os.getenv("LANZADOR_SIN_CHEQUEO") == "1")
    estado: list = [None]  # línea de resumen del chequeo (mutable, por ref)
    if sin_chequeo:
        estado[0] = None
        print("(chequeo de arranque omitido por --sin-chequeo)")
    else:
        print("Chequeo rápido silencioso (equivalente a --diagnostico, sin "
              "gastar tokens)... puede tardar unos segundos.")
        estado[0], _, _ = chequeo_rapido()
        print(estado[0])
    while True:
        _pintar_menu(estado[0])
        try:
            eleccion = input("Elige una opción: ").strip()
        except EOFError:
            print("\nEntrada cerrada. Saliendo del lanzador.")
            break
        except KeyboardInterrupt:
            print("\nCancelado. Volviendo al menú.")
            continue
        if eleccion in ("0", "q", "Q", "salir", "exit"):
            break
        if not eleccion:
            continue
        _despachar(eleccion, estado)
        _pausa()
    _despedida()


def _despedida() -> None:
    print()
    try:
        proy = importar_proyecto()
        proy.resumen_gasto()
    except (AccionCancelada, Exception):
        pass
    print("Hasta la próxima. Recuerda: los hallazgos solo entran en el árbol "
          "con la opción 12 (--aceptar) si están verificados.")


def _preparar_entorno() -> None:
    """cd a la carpeta del proyecto (los JSON de salida son rutas relativas,
    igual que cuando se ejecuta main.py desde la raíz) y UTF-8 en Windows
    para que los iconos ✓/⚠/📄 no rompan la consola."""
    try:
        os.chdir(BASE_DIR)
    except OSError:
        pass
    if os.name == "nt":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelado. Saliendo del lanzador.")
