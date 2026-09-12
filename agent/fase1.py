"""
agent/fase1.py — Búsqueda exhaustiva de un objetivo genealógico.

FASE 1 del agente: para cada persona del árbol conocido, genera un conjunto
de consultas semilla (nominales, familiares, parroquiales, censos), las
ejecuta contra Tavily + recolectores de archivos, descarga y filtra los
resultados con el LLM barato, y devuelve un corpus de fragmentos relevantes.

FASE 4 del refactor v4.0 — Nuevas estrategias genealógicas:
  - Búsqueda de la Nidada (hermanos): cuando un objetivo tiene padres
    confirmados, se añaden automáticamente consultas para buscar
    bautismos de hermanos ("bautismo hijo de [Padre] y [Madre]"). Los
    hermanos aparecen en los mismos libros sacramentales y sus partidas
    revelan datos colaterales vitales: abuelos, lugar de origen de los
    padres, edades de los padres al casarse, etc.
  - Expansión geográfica automática: si tras N consultas un municipio
    devuelve 0 fragmentos relevantes, se añade un objetivo nuevo con
    consultas equivalentes pero a nivel provincial ("[Apellido]"
    "[Provincia]" en vez de "[Apellido]" "[Municipio]"). Esto atrapa a
    familias que emigraron a un pueblo vecino pero siguen en la provincia.
  - Flexibilidad de apellidos compuestos: los apellidos como "Sáenz de
    Navarrete" se trocean y se prueban todas las combinaciones razonables
    en las búsquedas Tavily: "Sáenz", "Navarrete", "Sáenz-Navarrete",
    "Sáenz Navarrete", "Navarrete Sáenz". Muchos índices antiguos los
    registraron de cualquiera de estas formas.
"""

from __future__ import annotations

import json
import random
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime

from config import (BASE_DIR, DELAY_DESCARGAS, FAMILIA_JSON_PATH,
                    HASHES_CORPUS,
                    JSON_SCHEMA_EXPANSION,
                    MAX_CHARS_TEXTO, MAX_EXPANSIONES, MAX_URLS_POR_QUERY,
                    MIN_RESULTADOS_PARA_FUENTES, MUNICIPIOS_EQUIVALENTES,
                    N_HILOS_DESCARGA, PAGES_PER_BATCH, PROVINCIAS_CONOCIDAS,
                    PROVINCIAS_SIN_ENSENADA, STOP_AFTER_EMPTY_SEARCHES,
                    STOPWORDS, SYSTEM_PROMPT_EXPANSION, SYSTEM_PROMPT_FASE1,
                    UMBRAL_SNIPPET, _limpiar_claves, _tokens_apellido,
                    envolver_fuente, fuentes_para,
                    marcar_buscado, marcar_vista, normalizar, sha256_corto,
                    sin_tildes, trocear, variantes_compuesto,
                    ya_buscado, ya_vista,
                    EXPANSION_GEO_INTENTOS)
from scrapers.archivos import recolectar
from scrapers.web import buscar_tavily, descargar_texto
from utils import ui
from utils.llm import (MODELO_FASE1, PresupuestoExcedido,
                       chat_json, presupuesto_agotado, variantes_apellido)

# HASHES_CORPUS es un set global definido en config que se mantiene durante
# toda una ejecución para evitar meter el mismo fragmento dos veces.
# Lo inicializa main() al cargar el corpus existente.


@dataclass
class ResultadoFase1:
    """Resultado de la Fase 1 para un objetivo."""
    apellido: str
    municipio: str
    fragmentos: list = field(default_factory=list)
    queries_ejecutadas: list = field(default_factory=list)
    provinciales_intentadas: int = 0   # FASE 4: contador para expansión geo


# ============================== FILTRO LOCAL ===============================

def terminos_de_filtro(objetivo: dict) -> list[str]:
    """Términos mínimos para que una página pase al LLM (filtro barato)."""
    terminos = set()
    for campo in ("nombre", "apellido_paterno", "apellido_materno", "conyuge"):
        valor = objetivo.get(campo, "")
        if valor:
            terminos.add(valor)
            for palabra in valor.split():
                if len(palabra) > 2 and normalizar(palabra) not in STOPWORDS:
                    terminos.add(palabra)
    for campo in ("municipio", "provincia"):
        if objetivo.get(campo):
            terminos.add(objetivo[campo])
    return [t for t in terminos if t]


def pasa_filtro_local(texto: str, terminos: list[str],
                      es_snippet: bool = False) -> bool:
    """Filtro barato pre-LLM. Con es_snippet=True se rebaja el umbral."""
    minimo = UMBRAL_SNIPPET if es_snippet else 200
    if not texto or len(texto) < minimo:
        return False
    t = normalizar(texto)
    return any(normalizar(term) in t for term in terminos)


# ============================== FILTRO LLM =================================

def llamar_filtro_llm(lote: list[dict], objetivo: dict) -> list[dict]:
    """DeepSeek V4 Flash decide qué páginas son relevantes y limpia su texto."""
    contexto = (
        f"Persona buscada: {objetivo.get('nombre', '?')} "
        f"(apellidos: {objetivo.get('apellido_paterno', '?')} / "
        f"{objetivo.get('apellido_materno', '?')}). "
        f"Municipio objetivo: {objetivo.get('municipio') or 'desconocido'}; "
        f"provincia: {objetivo.get('provincia') or 'desconocida'}.\n\n"
        f"Páginas:\n"
    )
    payload = [{"url": p["url"], "titulo": p.get("titulo", ""),
                "texto": envolver_fuente(p["texto"])} for p in lote]
    try:
        respuesta = chat_json(
            MODELO_FASE1,
            SYSTEM_PROMPT_FASE1,
            contexto + json.dumps(payload, ensure_ascii=False),
        )
        return respuesta if isinstance(respuesta, list) else []
    except PresupuestoExcedido:
        raise
    except Exception as e:
        ui.log_warn(f"fallo en el filtrado LLM del lote: {str(e)[:100]}")
        return []


def pedir_expansion(objetivo: dict, queries_hechas: list[str],
                    resumen: list[str]) -> list[str]:
    """DeepSeek V4 Flash propone nuevas consultas para agotar la investigación."""
    user = json.dumps({
        "persona": {
            "nombre": objetivo.get("nombre"),
            "apellido_paterno": objetivo.get("apellido_paterno"),
            "apellido_materno": objetivo.get("apellido_materno"),
            "nacimiento": objetivo.get("nacimiento"),
            "defuncion": objetivo.get("defuncion"),
            "padre": objetivo.get("padre"),
            "madre": objetivo.get("madre"),
            "conyuge": objetivo.get("conyuge"),
            "notas": objetivo.get("notas"),
        },
        "consultas_ya_hechas": queries_hechas,
        "resumen_hallazgos": [envolver_fuente(r) for r in resumen],
    }, ensure_ascii=False)
    try:
        respuesta = chat_json(MODELO_FASE1, SYSTEM_PROMPT_EXPANSION, user,
                              json_schema=JSON_SCHEMA_EXPANSION,
                              schema_name="expansion_consultas")
    except PresupuestoExcedido:
        raise
    except Exception as e:
        ui.log_warn(f"fallo pidiendo expansión: {str(e)[:100]}")
        return []
    nuevas = respuesta.get("queries", []) if isinstance(respuesta, dict) else []
    return [q.strip() for q in nuevas if isinstance(q, str) and q.strip()]


# ============================== EJECUCIÓN FASE 1 ==========================

def ejecutar_fase1(objetivo: dict, fuentes: list[str], conn, max_steps: int,
                   sin_cache: bool = False) -> ResultadoFase1:
    """Búsqueda exhaustiva de un objetivo:
    1. Recolectores directos (SIGA/ADDO/PARES): filas de índice estructuradas
       que entran directo al corpus sin pasar por el filtro.
    2. Cola de consultas Tavily (semillas + expansiones propuestas por el LLM).
    3. Descarga paralela + filtro local barato + filtrado/limpieza con LLM.
    4. FASE 4 — Expansión geográfica: si tras EXPANSION_GEO_INTENTOS consultas
       vacías no hemos encontrado nada en el municipio, se añaden consultas
       a nivel provincial (sin municipio) para atrapar familias emigradas
       a pueblos vecinos.
    5. Cuando la cola se vacía, el LLM propone nuevas consultas (hasta
       MAX_EXPANSIONES rondas): búsqueda recursiva.
    """
    resultado = ResultadoFase1(apellido=objetivo.get("apellido_paterno", ""),
                               municipio=objetivo.get("municipio", ""))
    terminos = terminos_de_filtro(objetivo)

    # --- 1. RECOLECTORES directos ---
    docs: list[dict] = []
    try:
        docs = recolectar(objetivo, conn)
    except PresupuestoExcedido:
        raise
    except Exception as e:
        ui.log_warn(f"recolectores: {str(e)[:100]}")
    for d in docs:
        h = sha256_corto((d.get("url") or "") + (d.get("texto") or "")[:1000])
        if h in HASHES_CORPUS:
            continue
        HASHES_CORPUS.add(h)
        resultado.fragmentos.append({
            "hash": h,
            "persona": objetivo.get("nombre", ""),
            "origen": d.get("origen", "web"),
            "apellido": resultado.apellido,
            "municipio": resultado.municipio,
            "query": f"conector:{d.get('origen', '?')}",
            "url": d.get("url", ""),
            "titulo": d.get("titulo", ""),
            "texto_limpio": (d.get("texto") or "")[:6000],
            # v4.2 (punto 5): el ORIGINAL de los conectores es su propio
            # texto (filas de índice estructuradas, no resumen de IA).
            "texto_original": (d.get("texto") or "")[:MAX_CHARS_TEXTO],
            "fecha_descarga": datetime.now().isoformat(timespec="seconds"),
        })
    if docs:
        ui.log_ok(f"+{len(docs)} documentos de recolectores (SIGA/ADDO/PARES)")

    # --- 2. Cola de consultas ---
    cola = list(dict.fromkeys(objetivo["queries"]))
    hechas: list[str] = []
    urls_locales: set[str] = set()
    vacias_consecutivas = 0
    rondas_expansion = 0
    # FASE 4 — contadores para expansión geográfica
    consultas_vacias_municipio = 0
    expansion_geo_activa = False

    while True:
        if presupuesto_agotado():
            ui.log_warn(f"presupuesto agotado: se detiene este objetivo.")
            break
        if not cola:
            if rondas_expansion >= MAX_EXPANSIONES:
                break
            rondas_expansion += 1
            resumen = [r[:700] for r in [f["texto_limpio"]
                                         for f in resultado.fragmentos][-6:]]
            ui.log_llm(f"pidiendo nuevas consultas a DeepSeek V4 Flash...")
            cola = [q for q in pedir_expansion(objetivo, hechas, resumen)
                    if normalizar(q) not in {normalizar(h) for h in hechas}]
            if not cola:
                break

        if len(hechas) >= max_steps:
            break

        query = cola.pop(0)
        # v4.2 (punto 4 del informe) — clave de caché ESTABLE: se basa en el
        # NOMBRE NORMALIZADO de la persona y no en el índice p{NN} del
        # objetivo. Antes, en modo --ciclo, la pseudo-familia de la frontera
        # cambiaba de composición entre ciclos -> los índices cambiaban ->
        # las claves cambiaban -> se volvían a lanzar las MISMAS búsquedas
        # cada ciclo (repetir trabajo) y los resultados aparecían y
        # desaparecían entre ejecuciones.
        persona_clave = normalizar(objetivo.get("nombre", ""))
        clave = f"{persona_clave}::{normalizar(query)}"
        if not sin_cache and ya_buscado(conn, clave):
            continue
        marcar_buscado(conn, clave)
        hechas.append(query)
        resultado.queries_ejecutadas.append(query)
        ui.log_search(f"({len(hechas)}/{max_steps}) {query[:80]}")

        # --- búsqueda web: primero la general; si aporta poco, UNA sola
        #     consulta adicional restringida a TODAS las fuentes archivísticas
        #     de golpe (v4.2, punto 8 del informe: antes se lanzaba UNA
        #     búsqueda por dominio — hasta 8-9 llamadas Tavily por consulta —
        #     para conseguir lo que una sola con include_domains resuelve). ---
        hits = buscar_tavily(query, max_results=10)
        if len(hits) < MIN_RESULTADOS_PARA_FUENTES and fuentes:
            hits += buscar_tavily(query, dominios=fuentes, max_results=10)

        # --- FASE 4: detectar municipio estéril y disparar expansión geo ---
        if not hits and not expansion_geo_activa:
            consultas_vacias_municipio += 1
            if (consultas_vacias_municipio >= EXPANSION_GEO_INTENTOS
                    and objetivo.get("provincia")
                    and objetivo.get("municipio")):
                ui.log_warn(f"municipio '{objetivo.get('municipio')}' estéril "
                            f"tras {consultas_vacias_municipio} consultas: "
                            f"activando expansión geográfica a provincia "
                            f"'{objetivo.get('provincia')}'")
                expansion_geo_activa = True
                # Inyectamos consultas provinciales equivalentes: con el
                # apellido pero SIN municipio (solo provincia).
                prov = objetivo.get("provincia", "")
                ap_p = objetivo.get("apellido_paterno", "")
                ap_m = objetivo.get("apellido_materno", "")
                nombre = objetivo.get("nombre", "")
                seeds_geo = []
                for var in variantes_compuesto(ap_p) if ap_p else []:
                    seeds_geo.append(f'"{var}" "{prov}"')
                if ap_m:
                    for var in variantes_compuesto(ap_m):
                        if normalizar(var) != normalizar(ap_p):
                            seeds_geo.append(f'"{var}" "{prov}"')
                if nombre:
                    seeds_geo.append(f'"{nombre}" "{prov}"')
                # también a nivel de archivo diocesano provincial
                seeds_geo.append(f'"Archivo Diocesano de {prov}"')
                hechas_norm = {normalizar(h) for h in hechas}
                cola.extend(s for s in seeds_geo
                            if normalizar(s) not in hechas_norm
                            and s not in cola)
                resultado.provinciales_intentadas = len(seeds_geo)
                # Reiniciamos el contador de vacías para que no vuelva a
                # disparar otra ronda idéntica.
                consultas_vacias_municipio = 0
                continue

        # --- embudo de descargas ---
        funel = ui.FunelDescargas()
        candidatas: dict[str, dict] = {}
        for h in hits:
            url = (h.get("url") or "").strip()
            if not url:
                continue
            funel.add_encontrada()
            if ya_vista(conn, url) or url in urls_locales:
                funel.add_duplicada()
                continue
            if descargar_texto_es_basura(url):
                funel.add_basura()
                continue
            if url not in candidatas:
                candidatas[url] = {"url": url,
                                   "titulo": h.get("title", ""),
                                   "snippet": h.get("content", "")}
        candidatas = dict(list(candidatas.items())[:MAX_URLS_POR_QUERY])

        # --- descargar y filtrar en paralelo ---
        # v4.1: pasamos conn para que descargar_texto pueda cachear el OCR
        # de PDFs escaneados (cascada pypdf -> OCR local, v10.0).
        def _descargar(item):
            url, meta = item
            texto, motivo = descargar_texto(url, conn=conn)
            time.sleep(random.uniform(*DELAY_DESCARGAS))  # cortesía anti-WAF
            return url, meta, texto, motivo

        paginas = []
        with ThreadPoolExecutor(max_workers=N_HILOS_DESCARGA) as executor:
            for url, meta, texto, motivo in executor.map(
                    _descargar, list(candidatas.items())):
                urls_locales.add(url)
                es_snippet = False
                if texto:
                    funel.add_descargada()
                    marcar_vista(conn, url)
                elif meta["snippet"]:
                    texto = meta["snippet"]
                    es_snippet = True
                    funel.add_descargada()
                    marcar_vista(conn, url)
                else:
                    funel.add_fallida()
                    if motivo == "dominio_ignorado":
                        # ya contado como basura arriba; recontar es ruido
                        pass
                    else:
                        ui.log_warn(f"descarga fallida ({motivo}): "
                                    f"{url[:80]}")
                if pasa_filtro_local(texto, terminos, es_snippet=es_snippet):
                    funel.add_filtrada()
                    paginas.append({"url": url, "titulo": meta["titulo"],
                                    "texto": texto})

        # --- embudo vivo: una sola línea con el resumen de esta consulta ---
        funel.resumen(query=query[:60])

        # --- filtrado/limpieza con LLM ---
        nuevos = 0
        for lote in trocear(paginas, PAGES_PER_BATCH):
            # v4.2 (punto 5 del informe): guardamos también el TEXTO ORIGINAL
            # descargado de cada página. La auditoría de citas de fase 2
            # comprueba la cita contra ESTE texto, no contra el resumen que
            # hace el LLM de filtrado (si el filtro "rellenó" un nombre, el
            # error ya no queda marcado como verificado).
            crudos_lote = {p["url"]: p["texto"] for p in lote}
            for item in llamar_filtro_llm(lote, objetivo):
                if not isinstance(item, dict) or not item.get("relevante"):
                    continue
                texto_limpio = (item.get("texto_limpio") or "").strip()
                if len(texto_limpio) < 120:
                    continue
                url_item = item.get("url") or ""
                h = sha256_corto(url_item + texto_limpio[:1000])
                if h in HASHES_CORPUS:
                    continue
                HASHES_CORPUS.add(h)
                nuevos += 1
                resultado.fragmentos.append({
                    "hash": h,
                    "persona": objetivo.get("nombre", ""),
                    "apellido": resultado.apellido,
                    "municipio": resultado.municipio,
                    "query": query,
                    "url": url_item,
                    "texto_limpio": texto_limpio[:6000],
                    "texto_original": crudos_lote.get(
                        url_item, "")[:MAX_CHARS_TEXTO],
                    "fecha_descarga": datetime.now().isoformat(timespec="seconds"),
                })

        if nuevos:
            ui.log_ok(f"+{nuevos} fragmentos relevantes")
            vacias_consecutivas = 0
        else:
            vacias_consecutivas += 1
            if vacias_consecutivas >= STOP_AFTER_EMPTY_SEARCHES:
                ui.log_warn(f"varias búsquedas seguidas sin resultados: "
                            f"se detiene este objetivo.")
                break

    return resultado


# Helper de filtro (definido aparte para no importar toda la lógica de web)
def descargar_texto_es_basura(url: str) -> bool:
    """True si la URL apunta a un dominio de la lista negra (Facebook,
    Tripadvisor, PubMed, Dateas...). Reutilizamos la función de scrapers."""
    from scrapers.web import url_descartable
    return url_descartable(url)


# ============================== GENERACIÓN DE OBJETIVOS ===================

def generar_objetivos_busqueda(path: str = FAMILIA_JSON_PATH,
                               filtro_personas: list[str] | None = None,
                               conn=None,
                               datos: dict | None = None,
                               ) -> list[dict]:
    """Genera objetivos de búsqueda por persona. Cuatro familias de semillas:

    1. Nominales: nombre y apellidos cruzados con municipio/provincia.
       FASE 4 — Apellidos compuestos: para 'Sáenz de Navarrete' se prueban
       todas las combinaciones razonables (tokens sueltos, con guion, sin
       partícula, orden invertido) para cubrir las distintas formas en que
       los índices antiguos los registraban.

    2. Familiares y de CLÚSTER (FAN): padres y cónyuge en los municipios
       conocidos.
       FASE 4 — Búsqueda de la Nidada: si la persona tiene padres
       confirmados (p.ej. viene de la frontera tipo 'padres'), se añaden
       consultas explícitas para localizar bautismos de hermanos: las
       partidas de hermanos revelan datos colaterales vitales (abuelos,
       origen de los padres).

    3. Parroquiales/diocesanas: localizar los libros y catálogos.

    4. Censos y catastros: padrones, amillaramientos, quintas, Ensenada.
    """
    if datos is None:
        ruta = BASE_DIR / path
        if not ruta.exists():
            raise SystemExit(f"No encuentro '{path}'.")
        with open(ruta, "r", encoding="utf-8") as f:
            datos = _limpiar_claves(json.load(f))
    else:
        datos = _limpiar_claves(datos)

    filtro_norm = {normalizar(p) for p in filtro_personas} if filtro_personas else None
    personas = [_limpiar_claves(p) for p in datos.get("personas", [])]
    por_nombre = {normalizar(p.get("nombre", "")): p
                  for p in personas if p.get("nombre", "").strip()}
    objetivos = []

    for idx, persona in enumerate(personas):
        nombre = persona.get("nombre", "").strip()
        if not nombre:
            continue
        if filtro_norm and normalizar(nombre) not in filtro_norm:
            continue

        ap_p = persona.get("apellido_paterno", "").strip()
        ap_m = persona.get("apellido_materno", "").strip()
        conyuge = persona.get("conyuge", "").strip()
        padre = persona.get("padre", "").strip()
        madre = persona.get("madre", "").strip()

        municipios, provincias = set(), set()
        for evento in ("nacimiento", "defuncion"):
            ev = _limpiar_claves(persona.get(evento, {}))
            if ev.get("municipio"):
                mun = ev["municipio"].strip()
                municipios.add(mun)
                alias = MUNICIPIOS_EQUIVALENTES.get(normalizar(mun))
                if alias:
                    municipios.add(alias)
            if ev.get("provincia"):
                provincias.add(ev["provincia"].strip())

        # Sin localidad propia: heredar la de los hijos.
        if not municipios:
            for hijo in persona.get("hijos", []) or []:
                h = por_nombre.get(normalizar(hijo.strip()))
                if not h:
                    continue
                for evento in ("nacimiento", "defuncion"):
                    ev = _limpiar_claves(h.get(evento, {}))
                    if ev.get("municipio"):
                        municipios.add(ev["municipio"].strip())
                    if ev.get("provincia"):
                        provincias.add(ev["provincia"].strip())

        # Provincias mencionadas en las notas, p. ej. "(Zamora)"
        import re
        for menc in re.findall(r"\(([^()]{3,40})\)", persona.get("notas", "")):
            menc = menc.strip()
            if normalizar(menc) in PROVINCIAS_CONOCIDAS:
                provincias.add(menc)

        base = {
            "id": f"p{idx:02d}",
            "nombre": nombre,
            "apellido_paterno": ap_p,
            "apellido_materno": ap_m,
            "conyuge": conyuge,
            "padre": padre,
            "madre": madre,
            "nacimiento": _limpiar_claves(persona.get("nacimiento", {})),
            "defuncion": _limpiar_claves(persona.get("defuncion", {})),
            "notas": persona.get("notas", ""),
        }

        # FASE 4 — Variantes de apellidos compuestos
        # Para cada apellido, generamos todas las formas razonables en que
        # puede aparecer indexado: 'Sáenz de Navarrete' -> 'Sáenz',
        # 'Navarrete', 'Sáenz-Navarrete', etc. Las variantes con/sin tilde
        # las aporta variantes_apellido() (con caché LLM).
        vars_ap_p = []
        for v in variantes_compuesto(ap_p):
            vars_ap_p.extend(variantes_apellido(v, conn))
        vars_ap_p = list(dict.fromkeys(vars_ap_p))
        vars_ap_m = []
        for v in variantes_compuesto(ap_m):
            if normalizar(v) != normalizar(ap_p):
                vars_ap_m.extend(variantes_apellido(v, conn))
        vars_ap_m = list(dict.fromkeys(vars_ap_m))

        # ¿La persona tiene padres confirmados? Si es así, sembramos la
        # búsqueda de la NIDADA (hermanos).
        # v10.3: comprobar que los PADRES tienen ficha confirmada, no la persona actual
        padres_confirmados = False
        if padre and madre:
            padre_ok = any(normalizar(f.get("nombre", "")) == normalizar(padre)
                          and f.get("estado") == "confirmado" for f in personas)
            madre_ok = any(normalizar(f.get("nombre", "")) == normalizar(madre)
                          and f.get("estado") == "confirmado" for f in personas)
            padres_confirmados = padre_ok and madre_ok

        seeds: list[str] = []
        if municipios or provincias:
            for mun in sorted(municipios):
                # 1) búsquedas nominales (apellido en todas sus variantes)
                seeds.append(f'"{nombre}" "{mun}"')
                for var in vars_ap_p:
                    seeds.append(f'"{var}" "{mun}"')
                for var in vars_ap_m:
                    seeds.append(f'"{var}" "{mun}"')

                # 2) combinaciones familiares (una generación atrás)
                if padre:
                    seeds.append(f'"{padre}" "{mun}"')
                if madre:
                    seeds.append(f'"{madre}" "{mun}"')
                if padre and madre:
                    seeds.append(f'"{padre}" "{madre}" "{mun}"')
                    seeds.append(f'"{padre}" "{madre}" "matrimonio"')
                    seeds.append(f'"hijos de {padre} y {madre}"')
                    seeds.append(f'"hijo de {padre} y {madre}" "{mun}"')
                    seeds.append(f'"{padre}" "{madre}" "expediente matrimonial"')
                    # FASE 4 — BÚSQUEDA DE LA NIDADA (hermanos del objetivo)
                    # Si el objetivo tiene padres confirmados, sus hermanos
                    # son el siguiente objetivo colateral. Sus partidas
                    # revelan datos de los ABUELOS (4 abuelos en el expediente
                    # matrimonial de los padres) y permiten extender el árbol
                    # sin esperar a confirmar al objetivo.
                    if padres_confirmados:
                        seeds.append(f'"bautismo hijo de {padre} y {madre}"')
                        seeds.append(f'"bautismo hijo de {padre} y {madre}" "{mun}"')
                        seeds.append(f'"bautismo hijo de {padre}" "{mun}"')
                        seeds.append(f'"bautismo hijo de {madre}" "{mun}"')
                        seeds.append(f'"hijo de {padre} y {madre}" "{mun}" "bautismo"')
                if padre:
                    seeds.append(f'"hijo de {padre}" "{mun}"')
                if madre:
                    seeds.append(f'"hijo de {madre}" "{mun}"')

                # 3) censos y catastros
                seeds.append(f'"{mun}" "padrón"')
                seeds.append(f'"{mun}" "padrones" "archivo"')
                seeds.append(f'"{mun}" "amillaramiento"')
                seeds.append(f'"{mun}" "quintas" "sorteo"')
                seeds.append(f'"{mun}" "censo electoral"')
                if not {normalizar(pv) for pv in provincias} & PROVINCIAS_SIN_ENSENADA:
                    seeds.append(f'"{mun}" "Catastro de Ensenada"')
                    if ap_p:
                        seeds.append(f'"{ap_p}" "Catastro de Ensenada" "{mun}"')

                # 4) parroquiales/diocesanas
                seeds.append(f'"{mun}" "Archivo Diocesano"')
                seeds.append(f'"{mun}" "libros sacramentales"')
                seeds.append(f'"{mun}" "partidas sacramentales"')
                seeds.append(f'"{mun}" "bautismos"')
                seeds.append(f'"{mun}" "matrimonios"')
                seeds.append(f'"{mun}" "defunciones"')
                for prov in sorted(provincias):
                    seeds.append(f'"{mun}" "Archivo Diocesano de {prov}"')

            for prov in sorted(provincias):
                seeds.append(f'"{nombre}" "{prov}"')
        else:
            seeds.append(f'"{nombre}"')

        if conyuge:
            seeds.append(f'"{nombre}" "{conyuge}"')

        mun_principal = sorted(municipios)[0] if municipios else ""
        prov_principal = sorted(provincias)[0] if provincias else ""
        objetivos.append({
            **base,
            "municipio": mun_principal,
            "provincia": prov_principal,
            "provincias": sorted(provincias),
            "queries": list(dict.fromkeys(seeds)),
            "descripcion": f"{nombre}" + (f" en {mun_principal}" if mun_principal
                                          else " (sin localidad conocida)"),
            "padres_confirmados": padres_confirmados,
        })

    # Deduplicar objetivos con las mismas queries
    vistos, unicos = set(), []
    for obj in objetivos:
        clave = json.dumps(obj["queries"], ensure_ascii=False)
        if clave not in vistos:
            vistos.add(clave)
            unicos.append(obj)
    return unicos


# ============================== CORPUS =====================================

def cargar_corpus() -> list[dict]:
    ruta = BASE_DIR / "corpus_bruto.json"
    if ruta.exists():
        try:
            with open(ruta, "r", encoding="utf-8") as f:
                corpus = json.load(f)
            if isinstance(corpus, list):
                return corpus
        except (json.JSONDecodeError, OSError):
            # v4.2 (punto 4): "hallazgos que aparecen en una ejecución y
            # desaparecen en la siguiente". Antes, un corpus corrupto se
            # descartaba EN SILENCIO y se empezaba de cero: las evidencias
            # guardadas se perdían. Ahora se RENOMBRA con marca de tiempo
            # (nunca se borra) para poder recuperar hallazgos a mano, y se
            # avisa claramente.
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            ruta_rescate = ruta.with_name(f"corpus_bruto.corrupto_{ts}.json")
            try:
                ruta.rename(ruta_rescate)
                ui.log_warn(f"corpus_bruto.json corrupto: se ha renombrado a "
                            f"{ruta_rescate.name} para NO perder su "
                            f"contenido y se empieza uno nuevo.")
            except OSError:
                ui.log_warn("corpus_bruto.json corrupto y no se pudo "
                            "renombrar: se empieza de cero (revísalo a mano).")
    return []


def guardar_corpus(corpus: list[dict]) -> None:
    with open(BASE_DIR / "corpus_bruto.json", "w", encoding="utf-8") as f:
        json.dump(corpus, f, ensure_ascii=False, indent=2)
