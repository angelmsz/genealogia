#!/usr/bin/env python3
"""
main.py — Punto de entrada del agente de investigación genealógica v10.2
(modo AVANZADO: para el uso diario, ejecuta lanzador.py, que ofrece un
menú interactivo con todas estas opciones sin escribir comandos).

Estructura del proyecto (FASE 3 del refactor):

    main.py            <-- este archivo: CLI con argparse + bucle de ciclos
    config.py          : constantes, prompts, schemas, DOMINIOS_IGNORADOS, SQLite
    utils/llm.py       : chat_json, hard timeout radical, control de gasto
    utils/ui.py         : log con colores ANSI, iconos, embudo de descargas
    scrapers/web.py     : descargas web + Tavily con filtro de dominios basura
    scrapers/archivos.py: SIGA (Álava), ADDO (Palencia), PARES (Ensenada)
    agent/fase1.py      : búsqueda exhaustiva + nuevas estrategias genealógicas
    agent/fase2.py       : extracción + consolidación + auditoría
    agent/frontera.py    : frontera priorizada + commit de verificaciones
    agent/gedcom.py      : GEDCOM, solicitudes, Ensenada, OCR local, diag.

Uso (modo avanzado; el día a día va con lanzador.py, ver README):
  python main.py                         # fase 1 + fase 2
  python main.py --fase 1                # solo búsqueda
  python main.py --fase 2                # solo refinado + GEDCOM
  python main.py --ciclo 3               # AUTOPILOTO: 3 ciclos completos
  python main.py --frontera              # ver cola priorizada
  python main.py --aceptar               # cometer los confirmados
  python main.py --solicitudes           # emails de petición de partidas
  python main.py --ensenada              # catastro 1750 -> candidatos
  python main.py --importar-propios      # fotos de certificados -> OCR local
  python main.py --probar-conectores     # diagnóstico SIGA/ADDO/PARES
  python main.py --probar-ocr acta.pdf   # cascada OCR 100% local con UN PDF
                                         # (añade --manuscrito; ver README)
  python main.py --diagnostico           # prueba rápida de claves y BD
  python main.py --max-steps 15
  python main.py --personas "Isidro Merillas Panero"
  python main.py --presupuesto-max 2.0   # para al llegar a $2
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime

from config import (BASE_DIR, ESTADO_PATH, FAMILIA_JSON_PATH, HASHES_CORPUS,
                    HALLAZGOS_JSON, INFORME_FASE1, INTENTOS_FASE2,
                    LOTE_HALLAZGOS, MAX_STEPS, MODELO_FASE1,
                    MODELO_FASE2, OCR_BACKEND, OCR_LLAMACPP_FAMILIA,
                    OCR_MAX_PAGINAS_LOCAL, REFINADO_JSON, SALIDA_JSON,
                    TIMEOUT_LLM, TIMEOUT_LLM_FASE2, VERSION,
                    _limpiar_claves, normalizar, sha256_corto, get_db)
from agent.evidencia import (NIVEL_CANDIDATO_FUERTE, NIVEL_COINCIDENCIA_DEBIL,
                             NIVEL_CONFIRMADO, reclasificar_arbol)
from agent.fase1 import (ejecutar_fase1, generar_objetivos_busqueda,
                          cargar_corpus, guardar_corpus)
from agent.fase2 import fase2
from agent.frontera import (calcular_frontera, cargar_estado, cargar_familia,
                              cometer_confirmaciones, generar_informe_progreso,
                              guardar_estado, mostrar_frontera)
from agent.gedcom import (diagnostico, exportar_gedcom,
                           generar_candidatos_ensenada,
                           generar_solicitudes, importar_documentos_propios)
from scrapers.archivos import probar_conectores
from utils import ui
from utils.llm import (GASTO, PresupuestoExcedido, presupuesto_agotado,
                       resumen_gasto)
from utils.personas import asignar_ids, emparejar_persona


# ==================== v4.2 — IDS ESTABLES (punto 6) =======================

def _asegurar_ids_estables() -> None:
    """v4.2 (punto 6 del informe): asigna ids estables ('P0001'...) a las
    fichas de familia_conocida.json que no lo tengan, y persiste el
    fichero. Solo AÑADE el campo "id" (no toca nada más), es idempotente
    y nunca reutiliza un id: a partir de aquí, abuelo y nieto tocayos son
    personas distintas para GEDCOM, el commit y la frontera."""
    ruta = BASE_DIR / FAMILIA_JSON_PATH
    if not ruta.exists():
        return   # los modos que requieren familia ya avisan con su error
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return   # no rompemos el arranque por esto
    if not isinstance(datos, dict):
        return
    nuevos = asignar_ids(datos)
    if nuevos:
        ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        ui.log_ok(f"{nuevos} fichas de {FAMILIA_JSON_PATH} han recibido id "
                  f"estable (P0001...): los homónimos ya no se fusionan")


# ============================== FASE 1 (ORQUESTACIÓN) =====================

def fase1(args, conn, datos: dict | None = None) -> None:
    """Orquesta la Fase 1: genera objetivos y los investiga uno a uno."""
    filtro = [p.strip() for p in args.personas.split(",")] if args.personas else None
    objetivos = generar_objetivos_busqueda(datos=datos,
                                            filtro_personas=filtro, conn=conn)
    ui.log_target(f"Fase 1: {len(objetivos)} objetivos de búsqueda "
                  f"(modelo: {MODELO_FASE1}, máx. {args.max_steps} "
                  f"consultas/objetivo)")

    corpus = cargar_corpus()
    HASHES_CORPUS.clear()
    HASHES_CORPUS.update(f.get("hash", sha256_corto(f.get("url", "") +
                                                     f.get("texto_limpio", "")[:1000]))
                         for f in corpus)

    informe = []
    parado_por_presupuesto = False
    try:
        from config import fuentes_para, FUENTES_BASE
        for i, obj in enumerate(objetivos, 1):
            fuentes_obj = fuentes_para(obj.get("provincias", []))
            ui.separador(f"({i}/{len(objetivos)}) Investigando: {obj['descripcion']}")
            extra_fuentes = [f for f in fuentes_obj if f not in FUENTES_BASE]
            if extra_fuentes:
                ui.log(f"fuentes extra: {', '.join(extra_fuentes)}")
            res = ejecutar_fase1(obj, fuentes_obj, conn, args.max_steps,
                                 sin_cache=args.sin_cache)
            ui.log_ok(f"{len(res.fragmentos)} fragmentos relevantes, "
                      f"{len(res.queries_ejecutadas)} consultas ejecutadas")
            if res.provinciales_intentadas:
                ui.log_warn(f"expansión geográfica: "
                            f"{res.provinciales_intentadas} consultas "
                            f"provinciales añadidas por municipio estéril")
            # v10.4 (P3) — EVIDENCIA NEGATIVA: el informe de metodología
            # profesional pide registrar las búsquedas infructuosas. Se
            # apunta un objetivo que se ejecutó de verdad y no sacó ni un
            # fragmento, con su fecha: así el bot sabe qué NO repetir mañana
            # (y el humano, dónde toca escribir o ir en persona). No es una
            # prueba de que el documento no exista y el informe lo dice así.
            if not res.fragmentos:
                try:
                    from agent.evidencia_negativa import registrar as _reg_neg
                    _reg_neg(ancla=obj.get("nombre", ""), tipo="objetivo",
                             municipio=obj.get("municipio", ""),
                             provincia=obj.get("provincia", ""),
                             consultas=len(res.queries_ejecutadas),
                             motivo="sin fragmentos relevantes en la web")
                except Exception as e:
                    ui.log_warn(f"evidencia negativa no registrada: "
                                f"{str(e)[:80]}")
            # Gasto acumulado tras cada objetivo
            total_tok = GASTO.prompt_tokens + GASTO.completion_tokens
            ui.log_stats(f"gasto acumulado: {total_tok} tokens, ${GASTO.coste:.4f}"
                         + (f" de ${GASTO.presupuesto_max:.2f}"
                            if GASTO.presupuesto_max is not None else ""))
            corpus.extend(res.fragmentos)
            guardar_corpus(corpus)  # guardado progresivo
            informe.append({
                "objetivo": obj["descripcion"],
                "queries": res.queries_ejecutadas,
                "fragmentos": len(res.fragmentos),
                "expansion_geo": res.provinciales_intentadas,
            })
            if presupuesto_agotado():
                parado_por_presupuesto = True
                break
    except PresupuestoExcedido as e:
        parado_por_presupuesto = True
        ui.log_error(f"PARADA SEGURA por presupuesto: {str(e)[:100]}")
    except KeyboardInterrupt:
        ui.log_warn("Interrumpido por el usuario: guardando lo conseguido...")
    finally:
        guardar_corpus(corpus)
        # v9.1: informe_fase1.json pasa de lista a dict con las secciones
        # nuevas de la revisión de fondo:
        #   - "objetivos": la lista de siempre (compatibilidad);
        #   - "archivos_a_consultar_in_situ" (PARTE C): AHP + archivo
        #     municipal por municipio de la familia, con catálogos online
        #     verificados o la marca "requiere visita o solicitud postal";
        #   - "familysearch" (PARTE A): libros del catálogo por localidad
        #     con rangos de fechas ANTES de leer, y estados explícitos
        #     (online / pendiente — requiere Centro de Historia Familiar);
        #   - "catastro_ensenada_particulares" (PARTE B): estado real de
        #     las Respuestas Particulares por provincia (AHP, no PARES) y
        #     la corrección factual de Álava.
        try:
            from scrapers.archivos_provinciales import (
                estado_ensenada_particulares, generar_archivos_a_consultar_in_situ)
            from scrapers.familysearch import RESULTADOS as fs_resultados
            from scrapers.familysearch import resumir_para_informe
            familia_dict = (_limpiar_claves(json.loads(
                (BASE_DIR / FAMILIA_JSON_PATH).read_text(encoding="utf-8")))
                if (BASE_DIR / FAMILIA_JSON_PATH).exists() else {})
            informe_salida = {
                "objetivos": informe,
                "archivos_a_consultar_in_situ":
                    generar_archivos_a_consultar_in_situ(familia_dict),
                "familysearch": resumir_para_informe(
                    list(fs_resultados.values())),
                "catastro_ensenada_particulares": {
                    prov: estado_ensenada_particulares(prov)
                    for prov in ("zamora", "palencia", "alava")},
            }
        except Exception as e:  # jamás romper la fase 1 por el informe
            ui.log_warn(f"secciones v9.1 del informe no generadas: "
                        f"{str(e)[:90]}")
            informe_salida = {"objetivos": informe}
        with open(BASE_DIR / INFORME_FASE1, "w", encoding="utf-8") as f:
            json.dump(informe_salida, f, ensure_ascii=False, indent=2)

    if parado_por_presupuesto:
        ui.log_warn(f"Fase 1 DETENIDA por presupuesto alcanzado "
                    f"(${GASTO.coste:.4f}). Progreso guardado: {len(corpus)} "
                    f"fragmentos. Puedes reanudar subiendo --presupuesto-max "
                    f"o ejecutando de nuevo (la caché evita repetir).")
    else:
        ui.log_ok(f"Fase 1 terminada: {len(corpus)} fragmentos en "
                  f"{SALIDA_JSON}")


# ============================== AUTOPILOTO =================================

def ejecutar_ciclos(args, conn) -> None:
    """AUTOPILOTO (--ciclo N): por cada ciclo,
    1. recalcular la frontera (a quién tocaría investigar y por qué),
    2. fase 1 SOLO sobre las personas de la frontera (recolectores + Tavily),
    3. fase 2 (extracción con caché + auditoría + consolidación),
    4. COMMIT de lo verificado (--aceptar automático, con backup),
    5. informe de progreso y frontera nueva -> siguiente ciclo.
    Paradas seguras: presupuesto, Ctrl+C (siempre se guarda el progreso) y
    estancamiento (un ciclo sin commit no quema más presupuesto).
    """
    for ciclo in range(1, max(1, args.ciclo) + 1):
        if presupuesto_agotado():
            ui.log_error(f"Presupuesto agotado antes del ciclo {ciclo}.")
            break
        ui.separador(f"CICLO {ciclo}/{args.ciclo} — "
                     f"{datetime.now().strftime('%Y-%m-%d %H:%M')}")
        estado = calcular_frontera()
        if not estado["frontera"]:
            ui.log_ok("Frontera vacía: no queda nada que investigar (árbol "
                      "completado, todo lo conocido ya investigado o faltan "
                      "datos en familia_conocida.json).")
            break
        # v4.2 (punto 4): claves de las entradas que este ciclo VA a
        # investigar; tras el ciclo quedan apuntadas en el estado para no
        # repetir la misma búsqueda en ciclos futuros (salvo que el tipo
        # cambie, p. ej. 'padres' -> 'hermanos' al confirmarlos).
        claves_ciclo = {e.get("clave_investigacion")
                        for e in estado["frontera"] if e.get("clave_investigacion")}
        ui.log(f"Prioridad del ciclo (top 3 de {len(estado['frontera'])}):")
        for e in estado["frontera"][:3]:
            icono = {"padres": "[padres]", "hermanos": "[hermanos]",
                     "persona": "[persona]", "candidato": "[candidato]"}.get(
                e["tipo"], "[?]")
            ui.log(f"  {icono} [{e['prioridad']:4.1f}] {e['ancla']} — "
                   f"{e['tipo']} ({e.get('municipio') or '?'})")

        # pseudo-familia con las personas de la frontera -> las semillas
        # normales (nominales + clúster + parroquiales + censos) apuntan
        # ahora a donde la frontera dice que hay que buscar
        familia = cargar_familia()
        por_nombre = {normalizar(p.get("nombre", "")): p
                      for p in familia.get("personas", [])}
        personas_frontera = []
        for e in estado["frontera"]:
            # v4.2 (punto 6): matching difuso del ancla de la frontera con
            # las fichas conocidas (antes exacto: 'Isidro Merillas' no
            # encontraba la ficha 'Isidro Merillas Panero' y el ciclo
            # trabajaba con una ficha vacía sintética).
            p = por_nombre.get(normalizar(e.get("ancla", ""))) \
                or emparejar_persona(e.get("ancla", ""),
                                     familia.get("personas", []),
                                     avisar=False, contexto="frontera->fase1")
            if p:
                q = dict(p)
                if e.get("municipio"):
                    nac = dict(q.get("nacimiento") or {})
                    nac.setdefault("municipio", e["municipio"])
                    if not nac.get("provincia") and e.get("provincia"):
                        nac["provincia"] = e["provincia"]
                    q["nacimiento"] = nac
                # Si el tipo es "hermanos", marcamos padres_confirmados para
                # que generar_objetivos_busqueda genere las consultas de la
                # nidada.
                if e.get("tipo") == "hermanos":
                    q["estado"] = "confirmado"
                personas_frontera.append(q)
            else:
                personas_frontera.append({
                    "nombre": e.get("ancla", ""),
                    "apellido_paterno": e.get("apellido", ""),
                    "nacimiento": {"municipio": e.get("municipio", ""),
                                   "provincia": e.get("provincia", "")},
                    "notas": e.get("motivo", ""),
                })
        try:
            fase1(args, conn, datos={"personas": personas_frontera})
        except KeyboardInterrupt:
            ui.log_warn("Interrumpido: progreso guardado. Ejecuta --aceptar "
                        "para cometer lo encontrado antes de reanudar con "
                        "--ciclo.")
            break
        except PresupuestoExcedido as e:
            # v4.3: parada segura si una burbuja de gasto salta DENTRO de la
            # fase 1 (antes de llegar al chequeo de abajo): no se marca nada
            # como investigado y el progreso ya está guardado por la propia
            # fase 1.
            ui.log_error(f"PARADA SEGURA por presupuesto en el ciclo {ciclo}: "
                         f"{str(e)[:90]}. Reanuda subiendo --presupuesto-max.")
            break
        if presupuesto_agotado():
            ui.log_error(f"Presupuesto agotado tras la fase 1 del ciclo "
                         f"{ciclo}: se omite la fase 2 de este ciclo (los "
                         f"fragmentos quedan guardados; reanuda subiendo "
                         f"--presupuesto-max).")
            break
        try:
            fase2(args, conn)
        except KeyboardInterrupt:
            ui.log_warn("Interrumpido: hallazgos parciales guardados.")
            break
        except PresupuestoExcedido as e:
            # v4.3: idem — parada segura y visible, sin traceback.
            ui.log_error(f"PARADA SEGURA por presupuesto en fase 2 del ciclo "
                         f"{ciclo}: {str(e)[:90]}.")
            break
        except Exception as e:
            # v10.2: red de seguridad del autopiloto. La fase 2 ya degrada
            # con elegancia sus fallos internos, pero si algo inesperado
            # salta más allá (p. ej. un JSON corrupto en el consolidado),
            # NO mata el bucle de ciclos: se registra, se conserva el
            # progreso ya guardado (corpus + hallazgos en disco) y se
            # pasa al siguiente ciclo, que reintentará la fase 2 con la
            # caché caliente.
            ui.log_error(f"fallo no previsto en fase 2 del ciclo {ciclo} "
                         f"(se continúa con el ciclo siguiente; el "
                         f"progreso ya está en disco): {str(e)[:120]}")
        resumen = cometer_confirmaciones(aplicar=True)
        estado = calcular_frontera()
        estado["ciclo"] = ciclo
        # v4.2 (punto 4): apunta las entradas investigadas en este ciclo:
        # la próxima llamada a calcular_frontera() las deja fuera y el
        # autopiloto deja de repetir las mismas búsquedas (hermanos,
        # candidatos...) ciclo tras ciclo.
        previos = {inv.get("clave") for inv in
                   estado.get("investigados", []) if isinstance(inv, dict)}
        estado["investigados"] = (estado.get("investigados", []) + [
            {"clave": c, "ciclo": ciclo} for c in sorted(claves_ciclo)
            if c not in previos])
        guardar_estado(estado)
        generar_informe_progreso()
        ui.log_ok(f"FIN DEL CICLO {ciclo}: {resumen['evidencias']} evidencias "
                  f"NUEVAS comprometidas, {resumen['nuevas']} fichas nuevas, "
                  f"{len(estado['frontera'])} entradas en la frontera, "
                  f"{len(estado['investigados'])} ya investigadas")
        # v4.2 (punto 3): el freno funciona de nuevo. Antes las evidencias
        # se re-apuntaban en cada commit (dedupe roto por el timestamp) y
        # este contador nunca llegaba a 0 aunque el ciclo no aportara nada.
        if resumen["evidencias"] == 0 and resumen["nuevas"] == 0 and ciclo >= 2:
            ui.log_warn("El ciclo no ha comprometido nada nuevo (frontera "
                        "estancada): se detiene el autopiloto para no quemar "
                        "presupuesto. Prueba con más --max-steps, --ensenada "
                        "o los emails de --solicitudes. Para reintentar las "
                        "entradas ya investigadas, borra 'investigados' de "
                        f"{ESTADO_PATH}.")
            break


# ==================== v9.0/v10.0 — PROBAR EL OCR CON UN PDF ================

def _probar_ocr(ruta: str, manuscrito: bool, sin_cache: bool) -> int:
    """Prueba la cascada OCR completa (pypdf -> RapidOCR/olmOCR-2 -> fallo
    explícito) con UN PDF tuyo: sin lanzar el agente, sin fases, sin tocar
    la frontera y SIN coste (v10.0: OCR 100% local, el fallo no escala a
    nube — no hay etapa de pago que omitir).

    Ejecuta exactamente el mismo scrapers.web._extraer_texto_pdf() que usa
    el agente en vivo, así que lo que veas aquí es lo que verá el agente.

    --manuscrito simula que el PDF viene de PARES (dominio de
    DOMINIOS_MANUSCRITOS): es la rama que usará el agente con las
    partidas parroquiales reales. Sin él, el PDF se trata como "impreso"
    (la rama de libros modernos con capa de texto o escaneados normales).

    Devuelve 0 si se extrajo texto y 1 si no (para el exit code).
    """
    from pathlib import Path
    from scrapers import web

    ruta_pdf = Path(ruta).expanduser()
    if not ruta_pdf.is_file():
        ui.log_error(f"No encuentro el PDF: {ruta_pdf}")
        return 1
    pdf_bytes = ruta_pdf.read_bytes()

    # URL "de origen" del PDF: con --manuscrito se simula PARES para que
    # _es_manuscrito() tome la rama de las partidas parroquiales (la que
    # usa el agente con PARES/SIGA/ADDO/FamilySearch).
    url_fuente = (f"https://pares.cultura.gob.es/{ruta_pdf.name}"
                  if manuscrito else f"file://localhost/{ruta_pdf.name}")

    ui.separador("v10.1 — Prueba de la cascada OCR (100% local, sin agente)")
    print(f"  PDF          : {ruta_pdf}")
    print(f"  Tamaño       : {len(pdf_bytes):,} bytes "
          f"(hash caché: {web._hash_pdf(pdf_bytes)[:16]})")
    print(f"  OCR_BACKEND  : {web.OCR_BACKEND}")
    print(f"  Ruta         : "
          + ("MANUSCRITO (como PARES/SIGA/ADDO)"
             if manuscrito else "impreso (normal)"))
    if sin_cache:
        print("  Caché        : DESACTIVADA (--sin-cache): se procesa de cero")
    if web.OCR_BACKEND == "llamacpp":
        # v10.1: la cabecera muestra la FAMILIA activa, el prompt EXACTO
        # que se va a enviar y el modelo que el servidor dice servir.
        fila_fam = web._llamacpp_config_familia()
        print(f"  Familia      : {fila_fam['familia']} "
              f"(modelo: {fila_fam['nombre']})")
        print(f"  Prompt (usuario, EXACTO): {fila_fam['prompt']}")
        ok, info = web.llamacpp_disponible()
        print(f"  llama-server : "
              + ("\u2713 (modelo servido: " + info + ")" if ok
                 else "\u2717 NO responde (" + info + ") — el OCR local "
                      "se saltará y el PDF quedará sin texto"))
        if manuscrito and not ok:
            print("                 Arranca el servidor para probar el OCR "
                  "local con manuscritos (comandos por familia en el "
                  "README, sección \"OCR local con llama.cpp + Vulkan\").")
    print("  Nube         : NINGUNA. OCR 100% local: el fallo no escala "
          "a nube.")
    print()

    conn = None if sin_cache else get_db()
    t0 = datetime.now()
    try:
        texto, backend, conf = web._extraer_texto_pdf(
            pdf_bytes, url_fuente, conn=conn)
    finally:
        duracion = (datetime.now() - t0).total_seconds()
        if conn is not None:
            conn.close()

    print()
    print(f"  Backend ganador : {backend}"
          + ("   (viene de la CACHÉ; re-ejecuta con --sin-cache para "
             "procesarlo de verdad)" if backend.endswith("_cache") else ""))
    print(f"  Confianza       : {conf:.2f}")
    print(f"  Caracteres      : {len(texto):,}")
    print(f"  Duración        : {duracion:.1f} s")
    print(f"  Coste           : $0.0000 (OCR 100% local: sin escalada a nube)")
    print()
    ui.separador("TEXTO EXTRAÍDO (primeros 1.500 caracteres)")
    print((texto or "(vacío: ninguna etapa produjo texto)")[:1500])
    print()
    if not texto:
        ui.log_error("La cascada no sacó texto. Revisa arriba qué etapa "
                     "falló: ¿llama-server arrancado (OCR_BACKEND="
                     "llamacpp)? ¿rapidocr instalado? ¿poppler instalado "
                     "para pdf2image? OCR 100% local: el fallo NO escala "
                     "a nube.")
        return 1
    ui.log_ok("Cascada OK: esto es exactamente lo que verá el agente "
              "cuando descargue un PDF así. Si el texto te parece bueno, "
              "puedes soltar el autopiloto con confianza.")
    return 0


# ===================== v9.2 — RECLASIFICAR (PARTE 1) ========================

def reclasificar_comando(base=None) -> dict | None:
    """--reclasificar: reclasifica los hallazgos YA GUARDADOS con el
    clasificador determinista de evidencia (v9.1), sin re-ejecutar la
    fase 2 ni gastar NI UN TOKEN.

    Es 100% OFFLINE: no llama al LLM ni toca la red; solo lee
    arbol_refinado.json + arbol_hallazgos.json + familia_conocida.json
    del directorio del proyecto, aplica la lógica de
    agent/evidencia.py::reclasificar_arbol y reescribe arbol_refinado.json
    dejando un backup .bak del anterior por si acaso.

    Devuelve los totales por nivel (dict) para el resumen, o None si no
    hay árbol que reclasificar (main() convierte eso en código 1).
    """
    base = base or BASE_DIR
    ruta_refinado = base / REFINADO_JSON
    if not ruta_refinado.exists():
        ui.log_error(f"No existe {REFINADO_JSON} en {base}. Ejecuta antes "
                     "la fase 2 (o copia aquí tu árbol) y vuelve a "
                     "lanzar --reclasificar.")
        return None
    try:
        refinado = _limpiar_claves(
            json.loads(ruta_refinado.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError) as e:
        ui.log_error(f"No puedo leer {REFINADO_JSON}: {str(e)[:120]}")
        return None

    # Hallazgos y familia son opcionales: reclasificar_arbol() degrada
    # con elegancia si faltan (sin hallazgos no puede reclasificarlos,
    # pero sí nivela las candidatas y reescribe el resumen).
    ruta_hallazgos = base / HALLAZGOS_JSON
    hallazgos: list = []
    if ruta_hallazgos.exists():
        try:
            hallazgos = _limpiar_claves(
                json.loads(ruta_hallazgos.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError) as e:
            ui.log_warn(f"{HALLAZGOS_JSON} ilegible ({str(e)[:90]}); "
                        "se reclasifica solo el árbol.")
    familia: dict = {"personas": []}
    if (base / FAMILIA_JSON_PATH).exists():
        try:
            familia = _limpiar_claves(
                json.loads((base / FAMILIA_JSON_PATH)
                           .read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass   # familia ilegible: el clasificador trabaja sin ella

    # Backup .bak del ANTERIOR antes de sobrescribir (por si acaso).
    ruta_bak = ruta_refinado.with_name(ruta_refinado.name + ".bak")
    ruta_bak.write_bytes(ruta_refinado.read_bytes())

    refinado = reclasificar_arbol(refinado, hallazgos, familia)
    with open(ruta_refinado, "w", encoding="utf-8") as f:
        json.dump(refinado, f, ensure_ascii=False, indent=2)

    # Resumen claro: cuántos quedan en cada una de las 3 categorías.
    tot = ((refinado.get("resumen_evidencia") or {}).get("totales") or {})
    n_conf = tot.get(NIVEL_CONFIRMADO, 0)
    n_fuer = tot.get(NIVEL_CANDIDATO_FUERTE, 0)
    n_deb = tot.get(NIVEL_COINCIDENCIA_DEBIL, 0)
    ui.log_ok(f"{REFINADO_JSON} reclasificado (backup del anterior en "
              f"{ruta_bak.name}) — 0 tokens gastados")
    ui.log(f"Hallazgos ({len(hallazgos)} en {HALLAZGOS_JSON}): "
           f"confirmados: {n_conf} | candidatos fuertes: {n_fuer} | "
           f"coincidencias débiles: {n_deb}")
    candidatas = refinado.get("personas_nuevas_candidatas")
    if isinstance(candidatas, list) and candidatas:
        fuertes = sum(1 for c in candidatas
                      if isinstance(c, dict)
                      and c.get("nivel_evidencia") == NIVEL_CANDIDATO_FUERTE)
        ui.log(f"Personas nuevas candidatas: {len(candidatas)} "
               f"(fuertes: {fuertes}, débiles: {len(candidatas) - fuertes})")
    ui.log("Siguiente paso: --aceptar comete SOLO los confirmados al "
           "árbol familiar.")
    return tot


# ============================== MAIN =======================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Agente de investigación genealógica v9.0 (modular, con "
                    "timeout duro radical, verificación de citas contra el "
                    "texto ORIGINAL, ids estables de persona, backups con "
                    "fecha, freno real del autopiloto, conectores con fallo "
                    "transitorio/cooldown y OCR local llama.cpp/olmOCR-2 "
                    "para GPU AMD vía Vulkan).")
    parser.add_argument("--fase", choices=["1", "2", "all"], default="all",
                        help="qué fase ejecutar (por defecto, ambas)")
    parser.add_argument("--max-steps", type=int, default=MAX_STEPS,
                        help=f"consultas máx. por objetivo (def. {MAX_STEPS})")
    parser.add_argument("--personas", type=str, default="",
                        help='nombres a investigar, separados por comas; '
                             'p. ej. "Isidro Merillas Panero,Obdulia Pelaz Merino"')
    parser.add_argument("--sin-cache", action="store_true",
                        help="repetir búsquedas aunque estén en la caché")
    parser.add_argument("--diagnostico", action="store_true",
                        help="prueba rápida previa: claves, BD, familia, "
                             "modelos, filtro de dominios y fechas GEDCOM")
    parser.add_argument("--test-llm", action="store_true",
                        help="con --diagnostico: además hace un ping real a "
                             "cada modelo (consume unos pocos tokens)")
    parser.add_argument("--solicitudes", action="store_true",
                        help="genera solicitudes de partidas para los registros "
                             "que no están online por la regla de los 100 años "
                             "(solicitudes.json + solicitudes.md)")
    parser.add_argument("--presupuesto-max", type=float, default=None,
                        metavar="N",
                        help="coste máximo estimado en dólares para los LLMs; "
                             "al superarlo el script para de forma segura "
                             "guardando todo el progreso")
    parser.add_argument("--ciclo", type=int, default=0, metavar="N",
                        help="AUTOPILOTO: N ciclos completos de fase1->fase2->"
                             "commit->frontera. El árbol crece solo, "
                             "generación a generación (imprescindible con "
                             "--presupuesto-max)")
    parser.add_argument("--frontera", action="store_true",
                        help="muestra la cola priorizada de investigación "
                             "(estado_investigacion.json) y sale")
    parser.add_argument("--reclasificar", action="store_true",
                        help="reclasifica los hallazgos YA GUARDADOS en "
                             "arbol_refinado.json con el clasificador "
                             "determinista de evidencia: OFFLINE (0 tokens, "
                             "0 llamadas de red), con backup .bak del "
                             "anterior")
    parser.add_argument("--aceptar", action="store_true",
                        help="COMMIT: fusiona en familia_conocida.json solo lo "
                             "verificado del último arbol_refinado.json "
                             "(backup con marca de tiempo + registro "
                             "append-only) y recalcula la frontera")
    parser.add_argument("--ensenada", action="store_true",
                        help="descenso inverso: busca los municipios del árbol "
                             "en el Catastro de Ensenada (1752) y escribe "
                             "candidatos_ensenada.json (hipótesis)")
    parser.add_argument("--importar-propios", action="store_true",
                        dest="importar_propios",
                        help="transcribe las fotos de documentos_propios/ con "
                             "el modelo de visión y las añade al corpus")
    parser.add_argument("--probar-conectores", action="store_true",
                        dest="probar_conectores",
                        help="comprueba en vivo SIGA (Álava), ADDO (Palencia) "
                             "y PARES (Catastro de Ensenada)")
    parser.add_argument("--probar-ocr", type=str, default="", metavar="RUTA",
                        dest="probar_ocr",
                        help="prueba la cascada OCR 100%% LOCAL (pypdf -> OCR "
                             "local -> fallo explícito) con UN PDF tuyo, "
                             "sin lanzar el agente y sin coste (ver README: "
                             "sección \"Probar el OCR con un solo PDF\")")
    parser.add_argument("--manuscrito", action="store_true",
                        help="con --probar-ocr: simula que el PDF viene de "
                             "PARES/SIGA (la rama de partidas parroquiales "
                             "que usará el agente)")
    args = parser.parse_args()

    # v10.4 (P0) — REGISTRO DE EJECUCIÓN ("caja negra"). Se abre AQUÍ, cuando
    # ya se sabe qué se ha pedido, para que la cabecera del log diga con qué
    # ajustes se lanzó (es lo primero que se mira al depurar una noche). El
    # pie y el cierre los garantiza atexit, también en los modos que acaban
    # con SystemExit (--diagnostico, --probar-ocr...).
    ui.iniciar_log()
    ui.cabecera_log({
        "version": VERSION,
        "fase": args.fase,
        "ciclos": args.ciclo,
        "max_steps": args.max_steps,
        "presupuesto_max": (f"${args.presupuesto_max:.2f}"
                            if args.presupuesto_max is not None
                            else "SIN TOPE (ojo)"),
        "personas": args.personas or "(todas las de la frontera)",
        "sin_cache": args.sin_cache,
        # v10.4.1 (tarea C): los techos de tiempo EFECTIVOS, en la cabecera.
        # El log del 12/09 decía "no respondió en 45.0s" y no había forma de
        # saber de dónde salía ese 45 sin abrir config.py y .env.
        "timeout_llm": TIMEOUT_LLM,
        "timeout_llm_fase2": TIMEOUT_LLM_FASE2,
        "intentos_fase2": INTENTOS_FASE2,
        "lote_hallazgos": LOTE_HALLAZGOS,
        "OCR_BACKEND": OCR_BACKEND,
        "OCR_LLAMACPP_FAMILIA": OCR_LLAMACPP_FAMILIA,
        "OCR_MAX_PAGINAS_LOCAL": OCR_MAX_PAGINAS_LOCAL,
        "MODELO_FASE1": MODELO_FASE1,
        "MODELO_FASE2": MODELO_FASE2,
    })

    # v4.2 (punto 6): ids estables en familia_conocida.json antes de
    # cualquier modo que la toque (--frontera, --aceptar, --ciclo...).
    # Solo AÑADE el campo 'id'; no cambia nada más.
    _asegurar_ids_estables()

    # --- modos que no gastan tokens ni red: se ejecutan y se sale ---
    if args.diagnostico:
        raise SystemExit(0 if diagnostico(args.test_llm) == 0 else 1)

    if args.probar_conectores:
        raise SystemExit(0 if probar_conectores() == 0 else 1)

    if args.frontera:
        estado = calcular_frontera()
        mostrar_frontera(estado)
        guardar_estado(estado)
        generar_informe_progreso(estado=estado)
        raise SystemExit(0)

    if args.reclasificar:
        # v9.2 (PARTE 1): 100% offline — clasificador determinista sobre
        # los JSON ya guardados; ni un token, ni una llamada de red.
        totales = reclasificar_comando()
        raise SystemExit(0 if totales is not None else 1)

    if args.solicitudes:
        generar_solicitudes()
        # v9.1 (PARTE B): solicitudes por escrito de las Respuestas
        # Particulares del Catastro de Ensenada (AHP de Zamora/Palencia;
        # Álava no existe: excluida del Catastro — queda explícito en el
        # informe, no como solicitud fallida).
        try:
            from scrapers.archivos_provinciales import integrar_solicitudes_ensenada
            familia_dict = (_limpiar_claves(json.loads(
                (BASE_DIR / FAMILIA_JSON_PATH).read_text(encoding="utf-8")))
                if (BASE_DIR / FAMILIA_JSON_PATH).exists() else {})
            integrar_solicitudes_ensenada(familia_dict)
        except Exception as e:
            ui.log_warn(f"solicitudes Ensenada no generadas: {str(e)[:90]}")
        raise SystemExit(0)

    # --- modos que gastan tokens (o red): registran presupuesto si se pidió ---
    GASTO.presupuesto_max = args.presupuesto_max
    if GASTO.presupuesto_max is not None:
        ui.log_warn(f"Presupuesto máximo: ${GASTO.presupuesto_max:.2f} "
                    f"(parada segura al alcanzarlo)")

    if args.probar_ocr:
        # v10.0: OCR 100% local — la prueba NUNCA gasta (no hay etapa de
        # pago). --presupuesto-max sigue aplicando al resto de modos.
        raise SystemExit(_probar_ocr(args.probar_ocr, args.manuscrito,
                                     args.sin_cache))

    if args.ensenada:
        generar_candidatos_ensenada()
        resumen_gasto()
        raise SystemExit(0)

    if args.importar_propios:
        importar_documentos_propios()
        resumen_gasto()
        raise SystemExit(0)

    if args.aceptar:
        resumen = cometer_confirmaciones(aplicar=True)
        estado = calcular_frontera()
        estado["ciclo"] = max(estado.get("ciclo", 0), 1)
        guardar_estado(estado)
        generar_informe_progreso()
        ui.log_ok(f"Árbol actualizado: {resumen['actualizadas']} eventos, "
                  f"{resumen['evidencias']} evidencias. Siguiente paso: "
                  f"--frontera para ver qué investigar ahora.")
        raise SystemExit(0)

    # --- flujo principal: fase 1 + fase 2, o --ciclo N autopiloto ---
    ui.separador("Agente de investigación genealógica v10.2")
    conn = get_db()
    try:
        if args.ciclo > 0:
            ejecutar_ciclos(args, conn)
        else:
            if args.fase in ("1", "all"):
                fase1(args, conn)
            if args.fase in ("2", "all"):
                if presupuesto_agotado():
                    ui.log_warn("Fase 2 omitida: el presupuesto ya está agotado.")
                else:
                    fase2(args, conn)
    finally:
        resumen_gasto()
        conn.close()
        # v10.4 (P0): pie del registro (duración + recuento de avisos y
        # errores) y aviso de dónde quedó el fichero. Idempotente.
        ui.cerrar_log()


if __name__ == "__main__":
    main()
