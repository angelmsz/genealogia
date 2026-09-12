"""
agent/fase2.py — Refinado con GLM 5.2 (extracción, consolidación, auditoría).

FASE 2 del agente: toma los fragmentos limpios del corpus y los convierte en
hallazgos estructurados (con cita literal), verifica plausibilidad biológica
(edades padres-hijo), audita que cada cita esté realmente en la fuente, y
consolida todo contra la memoria familiar (familia_conocida.json).

Flujo:
  1. Extracción de hallazgos con GLM 5.2 (con caché por fragmento) y
     asociación a fichas por ID ESTABLE (v4.2, punto 6: emparejamiento
     difuso 'Isidro Merillas' ~ 'Isidro Merillas Panero').
  2. Verificación de plausibilidad biológica (>=13 años entre generaciones,
     <=55 madre-hijo). v4.2 (punto 2): solo marca posible_homonimo ante una
     CONTRADICCIÓN real; la falta de fechas de los padres ya no sospecha.
  3. Auditoría de citas con DeepSeek Flash: cada cita_literal debe estar
     en el TEXTO ORIGINAL descargado de la fuente (v4.2, punto 5), no en el
     resumen del LLM de filtrado. Determinista primero (gratis), LLM luego.
  4. Consolidación con GLM 5.2: cruza con familia_conocida.json, separa
     confirmado / estimado, lista contradicciones y personas nuevas.
  5. Exporta arbol_hallazgos.json y arbol_refinado.json.
"""

from __future__ import annotations

import json
from datetime import datetime

from config import (BASE_DIR, DB_LOCK, FAMILIA_JSON_PATH, HALLAZGOS_JSON,
                    INTENTOS_FASE2, JSON_SCHEMA_AUDITORIA,
                    JSON_SCHEMA_CONSOLIDACION,
                    JSON_SCHEMA_HALLAZGOS,
                    LOTE_CONSOLIDACION, LOTE_HALLAZGOS,
                    LOTES_FALLIDOS_CORTE,
                    MAX_CHARS_FASE2,
                    MAX_TOKENS_FASE2,
                    MODELO_FASE1, MODELO_FASE2, REFINADO_JSON,
                    SYSTEM_PROMPT_AUDITORIA, SYSTEM_PROMPT_CONSOLIDACION,
                    SYSTEM_PROMPT_FUSION, SYSTEM_PROMPT_HALLAZGOS,
                    TIMEOUT_LLM_FASE2,
                    _limpiar_claves, envolver_fuente,
                    normalizar, sha256_corto, sin_tildes, trocear)
from agent.evidencia import (NIVEL_CANDIDATO_FUERTE, NIVEL_COINCIDENCIA_DEBIL,
                             NIVEL_CONFIRMADO, clasificar_hallazgos,
                             nivel_de_persona, secciones_evidencia,
                             texto_resumen_evidencia)
from utils import ui
from utils.llm import (PresupuestoExcedido, chat_json)
from utils.personas import anio_persona, asignar_ids, emparejar_persona

# Constantes locales de fase 2 (no exportadas a config porque son
# detalles internos de esta fase).
LOTE_AUDITORIA = 8
MAX_CHARS_AUDITORIA = 8_000
MIN_ANIOS_ENTRE_GENERACIONES = 13
MAX_ANIOS_MADRE_HIJO = 55


def _anio_de(texto) -> int | None:
    import re
    m = re.search(r"\b(1[4-9]\d{2}|20\d{2})\b", str(texto or ""))
    return int(m.group(1)) if m else None


# ================== ASOCIACIÓN POR ID ESTABLE (v4.2, punto 6) ==============

def asociar_persona_ids(hallazgos: list[dict], datos_familia: dict) -> int:
    """v4.2 (punto 6 del informe): adjunta "persona_id" (el id estable de
    utils/personas.py) a cada hallazgo cuya persona casa con una ficha
    conocida. El emparejamiento es difuso:
      - exacto por nombre normalizado;
      - por subconjunto de tokens ('Isidro Merillas' casa con la ficha
        'Isidro Merillas Panero': antes ese hallazgo se descartaba en
        silencio por no coincidir el nombre literal);
      - con varios candidatos (abuelo/nieto tocayos) se desambigua por el
        año del evento; sin desambiguación posible NO se asigna (queda
        como pista, con aviso en el log).
    Devuelve cuántos hallazgos quedaron asociados a una ficha por id.
    """
    personas = [_limpiar_claves(p) for p in datos_familia.get("personas", [])]
    if not personas:
        return 0
    asociados = 0
    for h in hallazgos:
        anio = None
        if h.get("tipo_evento") in ("nacimiento", "bautismo"):
            anio = _anio_de(h.get("fecha_valor", ""))
        p = emparejar_persona(h.get("persona", ""), personas, anio=anio,
                              avisar=False, contexto="hallazgo->ficha")
        if p is not None and p.get("id"):
            h["persona_id"] = p["id"]
            asociados += 1
    return asociados


# ============================== EXTRACCIÓN =================================

def extraer_hallazgos(fragmentos: list[dict], conn=None) -> list[dict]:
    """GLM 5.2 convierte los fragmentos brutos en hallazgos estructurados.

    CACHÉ por fragmento (tabla hallazgos_por_hash, clave hash+modelo):
    un fragmento ya extraído no se vuelve a pagar en ejecuciones siguientes.
    Imprescindible en el modo --ciclo, donde la fase 2 corre una vez por
    ciclo sobre un corpus que solo crece.

    v4.3 — Extracción desde el TEXTO ORIGINAL: la cita se audita contra el
    texto ORIGINAL descargado (v4.2, punto 5), pero la extracción seguía
    leyendo el RESUMEN que hizo el LLM de filtrado (texto_limpio, máx.
    6000 car.). Consecuencias: citas 'reformateadas' por el filtro que la
    auditoría rechazaba (falsos SIN_VERIFICAR: hallazgos legítimos fuera
    del árbol) y datos perdidos (el original guarda hasta 24000 car.).
    Ahora la extracción lee el ORIGINAL cuando existe (los conectores
    SIGA/ADDO/PARES ya traen su propio texto estructurado como original):
    lo que el modelo extrajo es literalmente lo que la auditoría comprueba
    — mas hallazgos verificados y menos rechazos falsos.
    """
    def _hash_fragmento(f: dict) -> str:
        return (f.get("hash")
                or sha256_corto((f.get("url") or "")
                                + (f.get("texto_limpio") or "")[:1000]))

    hallazgos = []
    lotes = list(trocear(fragmentos, LOTE_HALLAZGOS))
    # v10.4.1 (tarea C): el cortacircuitos cuenta fallos SEGUIDOS; un lote que
    # responde (aunque sea con 0 hallazgos) reinicia la cuenta.
    fallos_seguidos = 0
    for n, lote in enumerate(lotes, 1):
        cacheados: list[dict] = []
        pendientes: list[dict] = []
        for f in lote:
            if conn is not None:
                with DB_LOCK:
                    fila = conn.execute(
                        "SELECT hallazgos FROM hallazgos_por_hash "
                        "WHERE hash=? AND modelo=?",
                        (_hash_fragmento(f), MODELO_FASE2)).fetchone()
                if fila:
                    try:
                        lista = json.loads(fila[0])
                        if isinstance(lista, list):
                            cacheados.extend(lista)
                            continue
                    except json.JSONDecodeError:
                        pass
            pendientes.append(f)
        if pendientes or cacheados:
            ui.log(f"Extrayendo hallazgos: lote {n}/{len(lotes)}"
                   + (f" ({len(pendientes)} nuevos, {len(cacheados)} en caché)"
                      if cacheados else ""))

        if pendientes:
            payload = [{
                "persona_buscada": f.get("persona", ""),
                "url": f.get("url", ""),
                # v4.3: ORIGINAL primero (es lo que la auditoría comprueba
                # y trae hasta 4x más texto); el resumen solo como fallback
                # para corpus pre-v4.2 (fragmentos de conectores: su
                # "original" ES su propio texto estructurado).
                "texto": envolver_fuente(
                    (f.get("texto_original") or f.get("texto_limpio") or "")
                    [:MAX_CHARS_FASE2]),
            } for f in pendientes]
            contexto = ("Fragmentos de documentos. La 'persona_buscada' es el "
                        "objetivo de la investigación, pero extrae TODOS los "
                        "hechos genealógicos que aparezcan, también de otras "
                        "personas.\n\n")
            fallo_lote = False
            try:
                respuesta = chat_json(
                    MODELO_FASE2,
                    SYSTEM_PROMPT_HALLAZGOS,
                    contexto + json.dumps(payload, ensure_ascii=False),
                    json_schema=JSON_SCHEMA_HALLAZGOS,
                    schema_name="hallazgos_genealogicos",
                    # v10.4.1 (tarea C): techo e intentos PROPIOS de fase 2
                    # (ver config.TIMEOUT_LLM_FASE2). Un lote = 1 fragmento
                    # (LOTE_HALLAZGOS), así que la respuesta esperada es
                    # ~3x más corta que con lotes de 3.
                    timeout=TIMEOUT_LLM_FASE2,
                    intentos=INTENTOS_FASE2,
                    # v10.4.1 (E): techo de salida -> coste máximo del
                    # intento calculable y sin respuestas desbocadas.
                    max_tokens=MAX_TOKENS_FASE2,
                )
            except PresupuestoExcedido:
                raise
            except Exception as e:
                ui.log_warn(f"lote {n} falló: {str(e)[:100]}")
                respuesta = []
                fallo_lote = True
            if fallo_lote:
                # v10.4.1 (tarea C) — CORTACIRCUITOS. En el log real del
                # 12/09 el modelo estaba saturado y se quemaron 27 min en 36
                # intentos de 12 lotes que nunca respondieron (y cada intento
                # abandonado se factura: ver utils/llm.py). Si fallan N lotes
                # SEGUIDOS no es un lote concreto: es el modelo o su cuota.
                # Se corta, se declara la extracción PARCIAL y lo que quede se
                # recupera con --fase 2 (la caché de hallazgos_por_hash evita
                # repagar lo ya extraído).
                fallos_seguidos += 1
                if fallos_seguidos >= LOTES_FALLIDOS_CORTE:
                    ui.log_error(
                        f"extracción PARCIAL: {LOTES_FALLIDOS_CORTE} lotes "
                        f"seguidos fallidos (el modelo de fase 2 no responde);"
                        f" se cortan los {len(lotes) - n} lotes que quedaban. "
                        f"Reanuda con 'python main.py --fase 2': la caché no "
                        f"repite lo ya extraído.")
                    break
            else:
                fallos_seguidos = 0
            if isinstance(respuesta, dict):
                respuesta = respuesta.get("hallazgos", [])
            if isinstance(respuesta, list):
                for h in respuesta:
                    if isinstance(h, dict) and h.get("persona"):
                        h.setdefault("origen", "")
                        # arrastra el origen del fragmento para el GEDCOM
                        f_origen = next((f for f in pendientes
                                          if f.get("url") == h.get("url_fuente")),
                                         None)
                        if f_origen and not h["origen"]:
                            h["origen"] = f_origen.get("origen", "")
                        hallazgos.append(h)
            # guardar en caché
            if conn is not None and isinstance(respuesta, list):
                for f in pendientes:
                    propios = [x for x in respuesta
                               if isinstance(x, dict)
                               and (x.get("url_fuente") or "")
                               == (f.get("url") or "")]
                    with DB_LOCK:
                        conn.execute(
                            "INSERT OR REPLACE INTO hallazgos_por_hash "
                            "VALUES (?,?,?)",
                            (_hash_fragmento(f), MODELO_FASE2,
                             json.dumps(propios, ensure_ascii=False)))
                        conn.commit()
        hallazgos.extend(cacheados)
    return hallazgos


# ============================== CONSOLIDACIÓN ==============================

def consolidar(datos_familia: dict, hallazgos: list[dict]) -> dict:
    """GLM 5.2 cruza los hallazgos con la memoria familiar. Si hay muchos
    hallazgos, consolida por partes y fusiona.

    v10.4.1 (tarea C): con techo (TIMEOUT_LLM_FASE2) e intentos
    (INTENTOS_FASE2) propios de fase 2. La consolidación es la llamada MÁS
    GRANDE de la noche (todos los hallazgos + toda la memoria familiar en un
    prompt) y en el log del 12/09 murió 3 veces seguidas a 45 s: es
    exactamente la llamada que más necesita el techo largo.
    """
    if len(hallazgos) <= LOTE_CONSOLIDACION:
        user = json.dumps({
            "arbol_conocido": datos_familia,
            "hallazgos": hallazgos,
        }, ensure_ascii=False)
        return chat_json(MODELO_FASE2, SYSTEM_PROMPT_CONSOLIDACION, user,
                         json_schema=JSON_SCHEMA_CONSOLIDACION,
                         schema_name="arbol_consolidado",
                         timeout=TIMEOUT_LLM_FASE2,
                         intentos=INTENTOS_FASE2,
                         max_tokens=MAX_TOKENS_FASE2)

    mitad = len(hallazgos) // 2
    ui.log("Consolidando por partes...")
    a = consolidar(datos_familia, hallazgos[:mitad])
    b = consolidar(datos_familia, hallazgos[mitad:])
    return chat_json(MODELO_FASE2, SYSTEM_PROMPT_FUSION,
                     json.dumps({"informe_a": a, "informe_b": b},
                                ensure_ascii=False),
                     json_schema=JSON_SCHEMA_CONSOLIDACION,
                     schema_name="arbol_consolidado",
                     timeout=TIMEOUT_LLM_FASE2,
                     intentos=INTENTOS_FASE2,
                     max_tokens=MAX_TOKENS_FASE2)


# ===================== VERIFICACIÓN BIOLÓGICA ============================

def verificar_plausibilidad_biologica(hallazgos: list[dict],
                                      datos_familia: dict) -> int:
    """Antes de dar por buena una coincidencia nombre+apellido, comprueba que
    los años implicados sean biológicamente plausibles respecto a los padres
    conocidos (>=13 años entre generaciones; <=55 años madre-hijo).

    v4.2 (punto 2 del informe — el fallo número uno): SOLO se marca
    "posible_homonimo" cuando hay una CONTRADICCIÓN REAL y comprobable
    (p. ej. el "hijo" nació 2 años después que el "padre", o 60 años
    después que la madre). FALTA de datos ya NO marca nada:
      - el hallazgo no tiene año interpretable      -> no sospechoso;
      - no conocemos el año de nacimiento de los padres -> no sospechoso.
    Antes pasaba justo lo contrario: como los padres son LO QUE EL AGENTE
    ESTÁ BUSCANDO, casi nunca había fechas de padres -> se marcaba TODO
    como sospechoso -> la consolidación no confirmaba nada -> el árbol
    nunca crecía y el autopiloto se paraba en el segundo ciclo por "falta
    de progreso". La comprobación inaplicable se anota como
    "plausibilidad_biologica": "NO_COMPROBABLE" (transparente, sin veto).

    v4.1 — Tolerancia con fechas derivadas de cálculo: si el hallazgo tiene
    "fecha_precision": "aproximada" Y su "justificacion" menciona un cálculo
    (palabras clave: "->", "nacimiento estimado", "edad", "cálculo"), se
    relajan los umbrales en ±5 años a ambos lados: es un dato derivado, no
    literal, y no debe penalizarse como posible_homonimo por diferencias
    pequeñas. Sí se marca posible_homonimo si la diferencia es absurda
    (>80 años padre-hijo, >70 madre-hijo), lo cual indica claramente que
    no es la misma persona.
    """
    personas = [_limpiar_claves(p) for p in (datos_familia.get("personas") or [])]

    # v4.2 (punto 6): los años de cada persona se guardan por ID ESTABLE
    # (no por nombre): si el abuelo y el nieto se llaman igual, sus años
    # ya no se mezclan en la misma cubeta y la comprobación no genera
    # falsos homónimos. El matching persona-hallazgo y
    # progenitor-ficha es difuso (emparejar_persona), con desambiguación
    # por año cuando hay varios tocayos.
    pid_por_obj = {id(p): (p.get("id") or f"i{i}")
                   for i, p in enumerate(personas)}
    anios_id: dict[str, set[int]] = {}
    anios_nombre: dict[str, set[int]] = {}   # solo nombres SIN ficha
    for p in personas:
        a = anio_persona(p)
        if a:
            anios_id.setdefault(pid_por_obj[id(p)], set()).add(a)

    # Pre-pase: los años de nacimiento que traen los PROPIOS hallazgos
    # sirven para comprobar hallazgos posteriores (p. ej. el bautismo del
    # padre hallado en este mismo lote). Si el nombre casa con una ficha,
    # el año va a su cubeta por id; si no, a una cubeta por nombre.
    for h in hallazgos:
        if h.get("tipo_evento") in ("nacimiento", "bautismo"):
            a = _anio_de(h.get("fecha_valor", ""))
            if a:
                p = emparejar_persona(h.get("persona", ""), personas,
                                      anio=a, avisar=False,
                                      contexto="plausibilidad")
                if p is not None:
                    anios_id.setdefault(pid_por_obj[id(p)], set()).add(a)
                else:
                    anios_nombre.setdefault(
                        normalizar(h.get("persona", "")), set()).add(a)

    marcados = 0
    for h in hallazgos:
        if h.get("tipo_evento") not in ("nacimiento", "bautismo"):
            continue
        anio_evento = _anio_de(h.get("fecha_valor", ""))
        persona = emparejar_persona(h.get("persona", ""), personas,
                                    anio=anio_evento, avisar=False,
                                    contexto="plausibilidad")
        if persona is None:
            continue
        motivos = []

        # v4.1 — Detectar si este hallazgo es un cálculo derivado (edad en
        # defunción, etc.). Si lo es, los umbrales se relajan.
        justificacion = (h.get("justificacion") or "").lower()
        es_calculo = (h.get("fecha_precision") == "aproximada"
                      and any(k in justificacion for k in
                              ("->", "nacimiento estimado", "edad ",
                               "cálculo", "calculado")))
        # Relajación de umbrales para cálculos derivados.
        if es_calculo:
            min_padre = MIN_ANIOS_ENTRE_GENERACIONES - 5   # 13 - 5 = 8 años
            max_madre = MAX_ANIOS_MADRE_HIJO + 5          # 55 + 5 = 60 años
            max_padre_absurdo = 80   # dif > 80: claramente no es el padre
            max_madre_absurdo = 70
            h["es_calculo_derivado"] = True
        else:
            min_padre = MIN_ANIOS_ENTRE_GENERACIONES
            max_madre = MAX_ANIOS_MADRE_HIJO
            max_padre_absurdo = None  # no se aplica este criterio
            max_madre_absurdo = None

        if anio_evento is None:
            # v4.2 (punto 2): sin año en el hallazgo NO hay sospecha, solo
            # transparencia: se anota que no se pudo comprobar (y el filtro
            # de contradictorios no aplica al no haber nada que contradecir).
            h["posible_homonimo"] = False
            h["plausibilidad_biologica"] = "NO_COMPROBABLE"
            h["motivo_no_comprobable"] = ("el hallazgo no trae año "
                                          "interpretable")
            continue
        comprobable = False
        for etiqueta, campo, es_madre in (
                ("padre", "padre", False),
                ("madre", "madre", True)):
            progenitor = (persona.get(campo) or "").strip()
            if not progenitor:
                continue
            # v4.2: resolución difusa del progenitor; sus años salen de SU
            # cubeta por id (nunca mezclada con la de un tocayo).
            prog = emparejar_persona(progenitor, personas, avisar=False,
                                     contexto="plausibilidad")
            if prog is not None:
                anios_prog = anios_id.get(pid_por_obj[id(prog)], set())
            else:
                anios_prog = anios_nombre.get(normalizar(progenitor), set())
            for anio_prog in sorted(anios_prog):
                comprobable = True
                diff = anio_evento - anio_prog
                if diff < min_padre:
                    # Para cálculos derivados, una diferencia ligeramente
                    # menor no marca homónimo: es tolerable.
                    if es_calculo and diff >= 0:
                        # Caso: nacido poco después del padre (8-12 años).
                        # Tolerable como estimación, lo dejamos pasar con
                        # un aviso en lugar de marcarlo.
                        pass
                    else:
                        motivos.append(
                            f"solo {diff} años respecto al {etiqueta} "
                            f"(n. {anio_prog}); mínimo "
                            f"{min_padre}")
                elif es_madre and diff > max_madre:
                    # Diferencia absurda: marcamos homónimo siempre.
                    if (max_madre_absurdo is not None
                            and diff > max_madre_absurdo):
                        motivos.append(
                            f"{diff} años respecto a la madre (n. "
                            f"{anio_prog}); absurdo incluso para un "
                            f"cálculo derivado")
                    elif not es_calculo:
                        motivos.append(
                            f"{diff} años respecto a la madre (n. "
                            f"{anio_prog}); máximo {max_madre}")
                elif (not es_madre and max_padre_absurdo is not None
                      and diff > max_padre_absurdo):
                    motivos.append(
                        f"{diff} años respecto al padre (n. {anio_prog}); "
                        f"absurdo incluso para un cálculo derivado")

        # v4.2 (punto 2 del informe — fallo número uno): "no hay años de los
        # padres para comparar" YA NO ES MOTIVO DE SOSPECHA. Los padres son
        # justamente lo que el agente está intentando descubrir: exigir sus
        # fechas para NO marcar el hallazgo como sospechoso era un círculo
        # vicioso (todo quedaba posible_homonimo -> nada se confirmaba ->
        # el árbol nunca crecía y el freno del autopiloto paraba en el
        # ciclo 2 por "falta de progreso"). La comprobación inaplicable se
        # anota como NO_COMPROBABLE y el hallazgo sigue adelante: la
        # verificación de la CITA (punto 5) sigue siendo la puerta de
        # entrada al árbol, que es la garantía correcta.
        if not comprobable:
            h["posible_homonimo"] = False
            h["plausibilidad_biologica"] = "NO_COMPROBABLE"
            h["motivo_no_comprobable"] = ("sin año de nacimiento conocido de "
                                          "los padres: la comprobación "
                                          "biológica no aplica (no es "
                                          "sospecha)")
            continue
        if motivos:
            h["posible_homonimo"] = True
            h["motivo_homonomia"] = "; ".join(dict.fromkeys(motivos))
            marcados += 1
        else:
            h["posible_homonimo"] = False
            h["plausibilidad_biologica"] = "OK"
            if es_calculo:
                h["plausibilidad_biologica"] = "OK_DERIVADO"
    return marcados


# ============================== AUDITORÍA DE CITAS =======================

def auditar_citas(hallazgos: list[dict], corpus: list[dict]) -> None:
    """Segunda pasada barata de auditoría (DeepSeek Flash): comprueba que cada
    cita_literal aparece realmente en el texto original de su url_fuente.

    v4.2 (punto 5 del informe — el que más afecta a la fiabilidad): la cita
    se comprueba contra el TEXTO ORIGINAL descargado de la página
    ("texto_original" del fragmento), NO contra el resumen/limpieza que
    hace el LLM de filtrado de fase 1. Antes ocurría esto: la IA nº1
    resume la página, la IA nº2 extrae la cita del resumen, y la
    "verificación" comprobaba la cita... contra el resumen de la IA nº1.
    Si la primera IA se equivocó o rellenó un nombre, el error entraba al
    árbol como "verificado con cita documental". Ahora, si el nombre
    inventado no está en el original, la cita queda SIN_VERIFICAR y no
    entra. Además abarata: el pase determinista contra el original (más
    largo y literal que el resumen) resuelve más casos sin gastar tokens.

    1. Pase determinista: si la cita normalizada está contenida en el
       texto ORIGINAL, se marca VERIFICADA sin gastar tokens.
    2. Los casos dudosos van al modelo barato en lotes; si no confirma que
       la cita aparece (o la llamada falla), el hallazgo queda como
       verificacion_cita = "SIN_VERIFICAR" y la consolidación no lo
       confirma.

    Fragmentos guardados antes de la v4.2 (sin "texto_original") caen al
    texto_limpio como fallback: mismo comportamiento que siempre tuvieron.
    """
    crudos_por_url: dict = {}
    for f in corpus:
        u = (f.get("url") or "").strip()
        if u:
            # v4.2 (punto 5): ORIGINAL primero (lo descargado de verdad);
            # texto_limpio solo como fallback para corpus pre-v4.2.
            es_original = bool(f.get("texto_original"))
            texto = (f.get("texto_original")
                     or f.get("texto_limpio") or "")
            if texto:
                crudos_por_url.setdefault(u, []).append((texto, es_original))

    pendientes = []
    for h in hallazgos:
        cita = (h.get("cita_literal") or "").strip()
        url = (h.get("url_fuente") or "").strip()
        textos = crudos_por_url.get(url, [])
        if not cita or not textos:
            h["verificacion_cita"] = "SIN_VERIFICAR"
            h["motivo_verificacion"] = ("cita vacía" if not cita
                                        else "sin texto original para esa URL")
            continue
        cita_norm = normalizar(cita)[:400]
        contra = ""
        if cita_norm:
            for t, es_original in textos:
                if cita_norm in normalizar(t):
                    contra = ("texto_original" if es_original
                              else "texto_limpio_fallback_pre_v4.2")
                    break
        if contra:
            h["verificacion_cita"] = "VERIFICADA"
            h["verificacion_contra"] = contra
        else:
            pendientes.append(h)

    if not pendientes:
        return
    ui.log(f"Auditando citas: {len(pendientes)} casos dudosos con "
           f"{MODELO_FASE1}...")
    for lote in trocear(pendientes, LOTE_AUDITORIA):
        payload = []
        for i, h in enumerate(lote):
            url = (h.get("url_fuente") or "").strip()
            texto = " ".join(t for t, _ in
                             crudos_por_url.get(url, [])
                             )[:MAX_CHARS_AUDITORIA]
            payload.append({
                "indice": i,
                "cita": (h.get("cita_literal") or "")[:500],
                "texto_fuente": envolver_fuente(texto),
            })
        aparece: dict = {}
        try:
            resp = chat_json(MODELO_FASE1, SYSTEM_PROMPT_AUDITORIA,
                             json.dumps(payload, ensure_ascii=False),
                             json_schema=JSON_SCHEMA_AUDITORIA,
                             schema_name="auditoria_citas")
            resultados = resp.get("resultados", []) if isinstance(resp, dict) else []
            aparece = {}
            for r in resultados:
                if isinstance(r, dict):
                    try:
                        aparece[int(r.get("indice", -1))] = bool(r.get("aparece"))
                    except (ValueError, TypeError):
                        pass
        except PresupuestoExcedido:
            raise
        except Exception as e:
            ui.log_warn(f"lote de auditoría falló: {str(e)[:100]}")
        for i, h in enumerate(lote):
            if aparece.get(i) is True:
                h["verificacion_cita"] = "VERIFICADA"
                h["verificacion_contra"] = "texto_original"
            else:
                h["verificacion_cita"] = "SIN_VERIFICAR"
                h["motivo_verificacion"] = ("la cita no se encontró en el texto "
                                            "original de la fuente")


# ============================== FASE 2 — ENTRY POINT ======================

def fase2(args, conn=None) -> None:
    """Punto de entrada principal de la Fase 2.

    Lee el corpus, extrae hallazgos, verifica plausibilidad, audita citas,
    consolida con la memoria familiar y exporta GEDCOM.
    """
    from agent.fase1 import cargar_corpus
    from agent.gedcom import exportar_gedcom

    corpus = cargar_corpus()
    relevantes = [f for f in corpus if f.get("texto_limpio")]
    ui.log_target(f"Fase 2: {len(relevantes)} fragmentos para refinar "
                  f"(modelo: {MODELO_FASE2})")

    ruta_fam = BASE_DIR / FAMILIA_JSON_PATH
    if not ruta_fam.exists():
        raise SystemExit(f"No encuentro '{FAMILIA_JSON_PATH}'.")
    with open(ruta_fam, "r", encoding="utf-8") as f:
        datos_familia = _limpiar_claves(json.load(f))

    # v4.2 (punto 6): ids estables para cada persona (P0001...). Solo AÑADE
    # el campo "id" (nunca toca nada más) y se persiste si había fichas sin
    # id: a partir de aquí, abuelo y nieto tocayos son personas distintas
    # para GEDCOM, commit y frontera.
    nuevos_ids = asignar_ids(datos_familia)
    if nuevos_ids:
        with open(ruta_fam, "w", encoding="utf-8") as f:
            json.dump(datos_familia, f, ensure_ascii=False, indent=2)
        ui.log_ok(f"{nuevos_ids} fichas de familia_conocida.json han "
                  f"recibido id estable (P0001...): los homónimos ya no se "
                  f"fusionan")

    hallazgos: list[dict] = []
    if relevantes:
        try:
            ui.cabecera("1/6 Extrayendo hallazgos estructurados")
            hallazgos = extraer_hallazgos(relevantes, conn)
            ui.log_ok(f"{len(hallazgos)} hallazgos extraídos")

            # v4.2 (punto 6): asocia cada hallazgo a su ficha por ID
            # (emparejamiento difuso: 'Isidro Merillas' casa con la ficha
            # 'Isidro Merillas Panero'; tocayos se desambiguan por año).
            asociados = asociar_persona_ids(hallazgos, datos_familia)
            ui.log(f"{asociados}/{len(hallazgos)} hallazgos asociados a "
                   f"fichas conocidas por id estable")

            # v9.1 (PARTE 0): clasificación DETERMINISTA de nivel de
            # evidencia (confirmado / candidato_fuerte / coincidencia_debil).
            # Prevalece sobre lo que haya clasificado el LLM al extraer:
            # nunca más apellido+geografía vendido como parentesco.
            ui.cabecera("2/6 Clasificación de nivel de evidencia (determinista)")
            recuento = clasificar_hallazgos(hallazgos, datos_familia)
            ui.log_ok(f"{recuento[NIVEL_CONFIRMADO]} confirmados · "
                      f"{recuento[NIVEL_CANDIDATO_FUERTE]} candidatos fuertes · "
                      f"{recuento[NIVEL_COINCIDENCIA_DEBIL]} coincidencias débiles "
                      f"(la señal, separada del ruido)")

            ui.cabecera("3/6 Verificación de plausibilidad biológica")
            marcados = verificar_plausibilidad_biologica(hallazgos, datos_familia)
            ui.log_ok(f"{marcados} hallazgos marcados como posible_homonimo")

            # v9.1: la plausibilidad puede haber marcado contradicciones que
            # demoten confirmados (guarde integrado en el clasificador); se
            # reclasifica una 2ª vez para recoger ese efecto.
            if marcados:
                recuento = clasificar_hallazgos(hallazgos, datos_familia)
                ui.log(f"reclasificación tras plausibilidad: "
                       f"{recuento[NIVEL_CONFIRMADO]} confirmados")

            ui.cabecera("4/6 Auditoría de citas (modelo barato)")
            auditar_citas(hallazgos, corpus)
            verif = sum(1 for h in hallazgos
                        if h.get("verificacion_cita") == "VERIFICADA")
            ui.log_ok(f"citas verificadas: {verif}/{len(hallazgos)}; el resto "
                      f"queda como SIN_VERIFICAR")

            # Guardado intermedio: los hallazgos auditados valen por sí solos
            with open(BASE_DIR / HALLAZGOS_JSON, "w", encoding="utf-8") as f:
                json.dump(hallazgos, f, ensure_ascii=False, indent=2)
            ui.log_doc(f"hallazgos auditados -> {HALLAZGOS_JSON}")

            ui.cabecera("5/6 Consolidando con la memoria familiar")
            # v10.2 (bug crítico del log): si el LLM está inaccesible
            # (rate-limit 429/503, timeouts...) la consolidación NO mata
            # el proceso con un traceback. Antes: RuntimeError sin
            # capturar -> moría el programa entero (sin GEDCOM, sin
            # siguiente ciclo, sin resumen de gasto limpio). Ahora: los
            # hallazgos YA están guardados (arriba), el árbol refinado
            # se escribe con lo determinista que sí se pudo calcular y
            # el GEDCOM se exporta igualmente. La caché de extracción
            # hace que reintentar la fase 2 luego sea barato.
            try:
                consolidado = consolidar(datos_familia, hallazgos)
            except PresupuestoExcedido:
                raise
            except Exception as e:
                ui.log_error(
                    f"Consolidación LLM no disponible ({str(e)[:120]}). "
                    f"NO se pierde nada: {len(hallazgos)} hallazgos ya "
                    f"están guardados en {HALLAZGOS_JSON}. Se escribe un "
                    f"árbol refinado determinista (sin cruce LLM) y el "
                    f"GEDCOM se exporta igual. Reintenta la fase 2 más "
                    f"tarde: la caché evita repetir la extracción.")
                consolidado = {
                    "resumen_general": (
                        "CONSOLIDACIÓN PENDIENTE: el LLM de fase 2 no "
                        "estuvo accesible al terminar esta ejecución "
                        "(rate-limit o timeouts). Los hallazgos extraídos "
                        "y auditados están guardados en "
                        f"{HALLAZGOS_JSON}; vuelve a ejecutar la fase 2 "
                        "para cruzarlos con la memoria familiar."),
                    "contradicciones": [],
                    "personas_nuevas_candidatas": [],
                }
            consolidado = {
                "meta": {
                    "fecha_generacion": datetime.now().isoformat(timespec="seconds"),
                    "modelo": MODELO_FASE2,
                    "num_fragmentos": len(relevantes),
                    "num_hallazgos": len(hallazgos),
                    "hallazgos_posible_homonimo": marcados,
                    "citas_verificadas": verif,
                    # v9.1 (PARTE 0): recuento por nivel de evidencia, para
                    # que el estado real de la investigación sea legible de
                    # un vistazo (señal vs ruido).
                    "hallazgos_por_nivel": recuento,
                },
                **(consolidado if isinstance(consolidado, dict) else {}),
            }
            # v9.1 (PARTE 0): post-proceso determinista de evidencia sobre
            # el consolidado (NO lo hace el LLM):
            #  - cada persona_nueva_candidata recibe el mejor nivel de sus
            #    hallazgos ('conexión no probada' = débil por definición);
            #  - resumen_general con las 3 secciones separadas;
            #  - clave resumen_evidencia para consumo programático.
            # v10.4 (P1): ANTES de repartir niveles se añaden los padrinos y
            # testigos con apellido de la familia como candidatas (pistas
            # colaterales): el clasificador determinista les dará su nivel
            # como a cualquier otra persona nueva.
            n_colat = candidatas_por_colaterales(consolidado, hallazgos,
                                                 datos_familia)
            if n_colat:
                ui.log_ok(f"{n_colat} padrino(s)/testigo(s) con apellido de "
                          f"la familia -> candidatos nuevos (pistas "
                          f"colaterales: se investigan, NUNCA son prueba)")
            _aplicar_evidencia_al_arbol(consolidado, hallazgos)
            with open(BASE_DIR / REFINADO_JSON, "w", encoding="utf-8") as f:
                json.dump(consolidado, f, ensure_ascii=False, indent=2)
            ui.log_doc(f"Árbol refinado -> {REFINADO_JSON}")
        except PresupuestoExcedido as e:
            ui.log_error(f"PARADA SEGURA por presupuesto en fase 2: {e}")
            if hallazgos:
                with open(BASE_DIR / HALLAZGOS_JSON, "w", encoding="utf-8") as f:
                    json.dump(hallazgos, f, ensure_ascii=False, indent=2)
                ui.log_doc(f"Hallazgos parciales guardados en {HALLAZGOS_JSON}. "
                           f"Reanuda subiendo --presupuesto-max.")
    else:
        ui.log_warn(f"No hay fragmentos relevantes; la consolidación y el "
                    f"GEDCOM se generan solo con la memoria familiar.")

    ui.cabecera("6/6 Exportando GEDCOM")
    exportar_gedcom()
    ui.log_ok("Fase 2 terminada.")


# ============== v10.4 (P1) — PADRINOS Y TESTIGOS COMO PISTAS ================

def candidatas_por_colaterales(consolidado: dict, hallazgos: list[dict],
                               datos_familia: dict) -> int:
    """AÑADE (en sitio) a consolidado["personas_nuevas_candidatas"] los
    padrinos/madrinas/testigos cuyo APELLIDO sea un apellido de la familia.

    Criterio: el informe de metodología profesional dice que "quienes
    comparten apellidos con el padre o la madre son muy probablemente
    familiares directos" y que la repetición de padrinos y testigos permite
    reconstruir redes de parentesco. Un padrino con apellido AJENO es un
    vecino o un amigo: NO se convierte en candidato (evita ruido y
    presupuesto gastado buscando a desconocidos).

    Garantías (filosofía del proyecto):
      - El nivel de evidencia de estas candidatas lo decide el clasificador
        DETERMINISTA, igual que el de cualquier otra persona nueva (casi
        siempre 'coincidencia_debil'): se INVESTIGAN, no entran al árbol.
      - No cambia el nivel de ningún hallazgo: un padrino no es un dato de
        filiación (sería parentesco por apellido, justo lo prohibido).
      - No duplica: ni nombres ya en el árbol, ni candidatas ya presentes.

    Devuelve cuántas candidatas nuevas ha añadido.
    """
    from agent.evidencia import _apellidos_familia
    from agent.frontera import nombres_colaterales

    conocidas = [_limpiar_claves(p)
                 for p in (datos_familia or {}).get("personas", [])]
    apellidos_familia = _apellidos_familia(conocidas)
    if not apellidos_familia:
        return 0
    candidatas = consolidado.get("personas_nuevas_candidatas")
    if not isinstance(candidatas, list):
        candidatas = []
        consolidado["personas_nuevas_candidatas"] = candidatas
    ya_norm = {normalizar((c.get("nombre") or "").strip())
               for c in candidatas if isinstance(c, dict)}
    anadidas = 0
    for h in hallazgos:
        if not isinstance(h, dict):
            continue
        for rol, nombre in nombres_colaterales(h):
            clave = normalizar(nombre)
            if not clave or clave in ya_norm:
                continue
            partes = [x for x in nombre.split() if x]
            apellido = partes[1] if len(partes) > 1 else ""
            if normalizar(sin_tildes(apellido)) not in apellidos_familia:
                continue      # apellido ajeno: vecino/amigo, no familiar
            if emparejar_persona(nombre, conocidas, avisar=False,
                                 contexto="colateral") is not None:
                continue      # ya está en el árbol: no es candidato
            quien = (h.get("persona") or "").strip()
            candidatas.append({
                "nombre": nombre,
                "motivo": (f"{rol} de "
                           f"{quien or 'un miembro de la familia'}"
                           + (f" ({h.get('tipo_evento')})"
                              if h.get("tipo_evento") else "")
                           + f": apellido '{apellido}' de la familia — "
                             f"posible tío/abuelo/primo, verificar"),
                "fuente_url": h.get("url_fuente", ""),
                "municipio": (h.get("lugar") or "").strip(),
                # 'apellido' lo usa calcular_frontera para priorizar por
                # RAREZA: en un pueblo pequeño, un apellido raro es casi
                # siempre familia.
                "apellido": apellido,
            })
            ya_norm.add(clave)
            anadidas += 1
    return anadidas


# ================== EVIDENCIA SOBRE EL ÁRBOL (v9.1) ========================

def _aplicar_evidencia_al_arbol(consolidado: dict,
                                hallazgos: list[dict]) -> None:
    """PARTE 0, post-proceso determinista del consolidado (en sitio):

    1. personas_nuevas_candidatas: nivel_evidencia = mejor nivel de sus
       hallazgos; si no tiene hallazgos clasificados,
       coincidencia_debil (una candidata cuya propia descripción dice
       'conexión no probada' es, por definición, débil).
    2. resumen_general: se PREFIJA el bloque de 3 secciones
       (Confirmado documentalmente / Candidatos fuertes a verificar /
       Coincidencias débiles) recalculado de los hallazgos; el párrafo
       del LLM se conserva a continuación.
    3. resumen_evidencia: las 3 listas con totales, para consumo
       programático (informe de progreso, --frontera, lanzador).
    """
    if not isinstance(consolidado, dict):
        return
    candidatas = consolidado.get("personas_nuevas_candidatas")
    if isinstance(candidatas, list):
        for c in candidatas:
            if isinstance(c, dict):
                nivel = nivel_de_persona(c.get("nombre", ""), hallazgos)
                c["nivel_evidencia"] = nivel or NIVEL_COINCIDENCIA_DEBIL
    secs = secciones_evidencia(hallazgos)
    bloque = ("RESUMEN POR NIVEL DE EVIDENCIA (regla: >=2 datos "
              "independientes por generación)\n"
              + texto_resumen_evidencia(hallazgos) + "\n\n---\n\n")
    previo = (consolidado.get("resumen_general") or "").strip()
    consolidado["resumen_general"] = bloque + previo
    consolidado["resumen_evidencia"] = {
        "Confirmado documentalmente": secs[NIVEL_CONFIRMADO],
        "Candidatos fuertes a verificar": secs[NIVEL_CANDIDATO_FUERTE],
        "Coincidencias débiles (descartables salvo nueva evidencia)":
            secs[NIVEL_COINCIDENCIA_DEBIL],
        "totales": {NIVEL_CONFIRMADO: len(secs[NIVEL_CONFIRMADO]),
                    NIVEL_CANDIDATO_FUERTE: len(secs[NIVEL_CANDIDATO_FUERTE]),
                    NIVEL_COINCIDENCIA_DEBIL: len(secs[NIVEL_COINCIDENCIA_DEBIL])},
    }
