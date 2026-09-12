#!/usr/bin/env python3
"""
resumen_noche.py — Resumen de la última tanda de investigación.

El comando existe porque en el log de ejecución real se intentó
``python resumen_noche.py`` tras una noche del autopiloto y el archivo
no estaba en el proyecto ("can't open file ... [Errno 2]"). Esta es la
versión de verdad, pensada para ejecutarse AL LEVANTARSE y ver en un
minuto qué consiguió el agente mientras dormías.

QUÉ HACE: es 100% OFFLINE — cero tokens, cero red, cero LLM. Solo lee
los artefactos que el agente ya escribió en disco y pinta un resumen
legible en consola:

  1. Corpus     : fragmentos recogidos (corpus_bruto.json) y de qué
                  fuentes vienen (conectores vs web).
  2. Hallazgos  : arbol_hallazgos.json — totales por nivel de evidencia
                  (confirmado / candidato fuerte / coincidencia débil)
                  y citas verificadas contra el texto original.
  3. Árbol      : familia_conocida.json — fichas confirmadas (con
                  evidencias documentales) vs solo de memoria familiar.
  4. Frontera   : estado_investigacion.json — cola priorizada de lo
                  que queda por investigar (top 8 y total).
  5. Cachés     : cache_agente.db — consultas de conectores, OCR y
                  variantes ya hechas (para saber qué NO se repetirá).
  6. Siguiente  : pasos recomendados según lo que haya (o falte) en
                  cada fichero.

Cada sección degrada con elegancia si su fichero no existe: muestra
"— sin datos todavía" y sigue. Código de salida: 0 si encontró algo
que resumir, 1 si el directorio está virgen (antes de la primera
ejecución de fase 1).
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from config import (BASE_DIR, DB_PATH, ESTADO_PATH, FAMILIA_JSON_PATH,
                    HALLAZGOS_JSON, REFINADO_JSON, SALIDA_JSON, _anio_de)
from utils import ui


# ============================== HELPERS =====================================

def _cargar_json(ruta: Path):
    """Carga un JSON del proyecto; devuelve None si falta o está roto
    (cada sección sabe degradarse)."""
    try:
        if ruta.exists():
            return json.loads(ruta.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        pass
    return None


def _edad_fichero(ruta: Path) -> str:
    """'hace 3 h' / 'hace 2 d' para saber de qué tanda es cada artefacto."""
    try:
        seg = (datetime.now() - datetime.fromtimestamp(ruta.stat().st_mtime)
               ).total_seconds()
    except OSError:
        return ""
    if seg < 90:
        return "ahora mismo"
    if seg < 5400:
        return f"hace {int(seg // 60)} min"
    if seg < 172_800:
        return f"hace {int(seg // 3600)} h"
    return f"hace {int(seg // 86_400)} d"


# ============================== SECCIONES ===================================

def _seccion_corpus() -> bool:
    ruta = BASE_DIR / SALIDA_JSON
    datos = _cargar_json(ruta)
    if not isinstance(datos, list) or not datos:
        return False
    ui.cabecera(f"1. Corpus ({SALIDA_JSON}, {_edad_fichero(ruta)})")
    ui.log(f"{len(datos)} fragmentos recogidos en total")

    # ¿De dónde viene cada fragmento? Los conectores directos ponen
    # "origen" (siga/addo/pares/hispagen/familysearch); el resto es
    # búsqueda web (Tavily) filtrada por el LLM en fase 1.
    por_origen: dict[str, int] = {}
    con_original = 0
    for f in datos:
        if not isinstance(f, dict):
            continue
        origen = (f.get("origen") or "").strip().lower() or "web (tavily)"
        por_origen[origen] = por_origen.get(origen, 0) + 1
        if f.get("texto_original"):
            con_original += 1
    for origen, n in sorted(por_origen.items(), key=lambda kv: -kv[1]):
        ui.log(f"   {origen:12s} {n} fragmentos")
    ui.log(f"{con_original} conservan el TEXTO ORIGINAL descargado "
           f"(lo que la auditoría de citas comprueba)")
    return True


def _seccion_hallazgos() -> bool:
    ruta = BASE_DIR / HALLAZGOS_JSON
    datos = _cargar_json(ruta)
    if not isinstance(datos, list) or not datos:
        return False
    ui.cabecera(f"2. Hallazgos ({HALLAZGOS_JSON}, {_edad_fichero(ruta)})")
    ui.log(f"{len(datos)} hallazgos extraídos de las fuentes")

    niveles: dict[str, int] = {}
    verificadas = 0
    homonimos = 0
    por_tipo: dict[str, int] = {}
    for h in datos:
        if not isinstance(h, dict):
            continue
        nivel = h.get("nivel_evidencia") or "sin_clasificar"
        niveles[nivel] = niveles.get(nivel, 0) + 1
        if h.get("verificacion_cita") == "VERIFICADA":
            verificadas += 1
        if h.get("posible_homonimo"):
            homonimos += 1
        tipo = h.get("tipo_evento") or "?"
        por_tipo[tipo] = por_tipo.get(tipo, 0) + 1

    # Orden fijo: señal primero, ruido al final.
    orden = ("confirmado", "candidato_fuerte", "coincidencia_debil",
             "sin_clasificar")
    for nivel in orden + tuple(k for k in niveles if k not in orden):
        if niveles.get(nivel):
            ui.log(f"   {nivel:20s} {niveles[nivel]}")
    ui.log(f"citas verificadas contra el texto original: "
           f"{verificadas}/{len(datos)}")
    if homonimos:
        ui.log(f"{homonimos} marcados como posible homónimo "
               f"(contradicción biológica)")
    tipos = ", ".join(f"{t} ({n})" for t, n in
                      sorted(por_tipo.items(), key=lambda kv: -kv[1])[:6])
    if tipos:
        ui.log(f"tipos de evento: {tipos}")
    return True


def _seccion_arbol() -> bool:
    ruta = BASE_DIR / FAMILIA_JSON_PATH
    datos = _cargar_json(ruta)
    if not isinstance(datos, dict):
        return False
    personas = [p for p in (datos.get("personas") or [])
                if isinstance(p, dict)]
    if not personas:
        return False
    ui.cabecera(f"3. Árbol familiar ({FAMILIA_JSON_PATH})")
    con_evidencia = [p for p in personas if p.get("evidencias")]
    ui.log(f"{len(personas)} fichas en total · "
           f"{len(con_evidencia)} confirmadas con evidencia documental · "
           f"{len(personas) - len(con_evidencia)} solo de memoria familiar")

    # Profundidad por línea (apellido paterno): el año más antiguo con
    # evidencia de cada línea es "hasta dónde" hemos llegado.
    lineas: dict[str, list[int]] = {}
    for p in personas:
        ap = (p.get("apellido_paterno") or "").strip()
        if not ap:
            continue
        anios = [a for a in (
            (p.get("nacimiento") or {}).get("fecha_aproximada", ""),
            (p.get("defuncion") or {}).get("fecha_aproximada", ""))
            if _anio_de(a)]
        if anios and p.get("evidencias"):
            lineas.setdefault(ap, []).append(min(_anio_de(x) for x in anios))
    for ap, anios in sorted(lineas.items(), key=lambda kv: min(kv[1])):
        ui.log(f"   {ap}: confirmado hasta ~{min(anios)}")
    return True


def _seccion_frontera() -> bool:
    ruta = BASE_DIR / ESTADO_PATH
    datos = _cargar_json(ruta)
    if not isinstance(datos, dict):
        return False
    frontera = [e for e in (datos.get("frontera") or [])
                if isinstance(e, dict)]
    investigados = [i for i in (datos.get("investigados") or [])
                    if isinstance(i, dict)]
    if not frontera and not investigados:
        return False
    ui.cabecera(f"4. Frontera de investigación ({ESTADO_PATH}, "
                f"{_edad_fichero(ruta)})")
    ui.log(f"{len(frontera)} entradas pendientes · "
           f"{len(investigados)} ya investigadas en ciclos anteriores")
    for e in sorted(frontera, key=lambda x: -x.get("prioridad", 0))[:8]:
        icono = {"padres": "[padres]", "hermanos": "[hermanos]",
                 "persona": "[persona]", "candidato": "[candidato]"}.get(
            e.get("tipo"), "[?]")
        ui.log(f"   {icono} [{e.get('prioridad', 0):4.1f}] "
               f"{e.get('ancla', '?')} ({e.get('municipio') or '?'})")
    if len(frontera) > 8:
        ui.log(f"   ... y {len(frontera) - 8} más (ver con --frontera)")
    return True


def _seccion_cache() -> bool:
    ruta = BASE_DIR / DB_PATH
    if not ruta.exists():
        return False
    ui.cabecera(f"5. Cachés ({DB_PATH}, {_edad_fichero(ruta)})")
    try:
        con = sqlite3.connect(f"{ruta.as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as e:
        ui.log_warn(f"no se pudo abrir la BD en solo-lectura: {str(e)[:80]}")
        return True
    try:
        tablas = {fila[0] for fila in
                  con.execute("SELECT name FROM sqlite_master "
                              "WHERE type='table'")}
        for tabla, etiqueta in (
                ("consultas_conectores", "consultas de conectores"),
                ("ocr_cache", "PDFs ya pasados por OCR"),
                ("variantes_apellidos", "apellidos con variantes cacheadas"),
                ("hallazgos_por_hash", "fragmentos ya extraídos"),
                ("busquedas_hechas", "búsquedas web hechas"),
                ("urls_vistas", "URLs ya visitadas")):
            if tabla in tablas:
                n = con.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()
                ui.log(f"   {etiqueta:32s} {n[0]}")
    finally:
        con.close()
    return True


def _seccion_siguiente(hay_corpus: bool, hay_hallazgos: bool,
                       hay_frontera: bool) -> None:
    ui.cabecera("6. Siguiente paso recomendado")
    if not hay_corpus:
        ui.log("Aún no hay corpus: arranca el agente con 'python main.py' "
               "(o lanzador.py) para la primera pasada de búsqueda.")
        return
    if hay_hallazgos:
        ui.log("python main.py --reclasificar   (gratis: reordena lo "
               "hallado por nivel de evidencia)")
        ui.log("python main.py --aceptar        (COMMIT: solo lo "
               "confirmado entra al árbol familiar)")
    if hay_frontera:
        ui.log("python main.py --frontera       (ver la cola priorizada "
               "completa de investigación)")
        ui.log("python main.py --ciclo 2 --presupuesto-max 1.0   "
               "(autopiloto: seguir desde donde se quedó)")
    else:
        ui.log("Sin frontera calculada todavía: python main.py --frontera")
    ui.log("python resumen_noche.py          (este resumen, otra vez)")


# ============================== MAIN ========================================

def main() -> int:
    ui.separador("Resumen de la investigación "
                 f"— {datetime.now():%Y-%m-%d %H:%M}")
    ui.log("100% offline: 0 tokens, 0 red. Solo lee lo que el agente "
           "ya escribió en disco.")
    print()

    hay_corpus = _seccion_corpus()
    hay_hallazgos = _seccion_hallazgos()
    hay_arbol = _seccion_arbol()
    hay_frontera = _seccion_frontera()
    hay_cache = _seccion_cache()

    if not any((hay_corpus, hay_hallazgos, hay_arbol, hay_frontera,
                hay_cache)):
        ui.log_warn("No encuentro artefactos que resumir en "
                    f"{BASE_DIR}. Ejecuta primero el agente "
                    "(python main.py) y vuelve a lanzar este resumen.")
        return 1

    print()
    _seccion_siguiente(hay_corpus, hay_hallazgos, hay_frontera)
    print()
    # Nota sobre arbol_refinado.json: si la consolidación quedó
    # pendiente (LLM caído a mitad de noche), el aviso sale aquí.
    refinado = _cargar_json(BASE_DIR / REFINADO_JSON)
    if isinstance(refinado, dict):
        resumen = (refinado.get("resumen_general") or "")
        if "CONSOLIDACIÓN PENDIENTE" in resumen:
            ui.log_warn("La última consolidación quedó PENDIENTE (el LLM "
                        "de fase 2 no estaba accesible): ejecuta "
                        "'python main.py --fase 2' para completarla — "
                        "la caché evita repetir la extracción.")
    elif hay_hallazgos:
        ui.log_warn(f"No existe {REFINADO_JSON}: la fase 2 no llegó a "
                    f"consolidar. Ejecuta 'python main.py --fase 2'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
