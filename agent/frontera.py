"""
agent/frontera.py — Bucle agéntico: frontera priorizada y commit de hallazgos.

La FRONTERA es la cola priorizada de investigación: tras cada ciclo, toda
persona confirmada cuyos padres no lo estén genera una entrada. El siguiente
ciclo investiga esas entradas: el árbol crece generación a generación hasta
1700 y más atrás SIN editar familia_conocida.json a mano.

FASE 4 del refactor v4.0 — Búsqueda de la Nidada integrada en la frontera:
  Cuando el agente confirma a unos padres (su bautismo/matrimonio), se
  añade automáticamente a la frontera el objetivo de buscar a sus HERMANOS.
  Las partidas de hermanos revelan datos colaterales vitales: abuelos,
  origen de los padres, edades. Se representa como tipo "hermanos".

Composición de la prioridad:
  prioridad = 1.5 * rareza_apellido + digitalizacion_archivo
              + evidencia (2 confirmada / 1 fecha de memoria / 0 nada)
              + 1 si la persona nació <= 1900 (más cerca de 1700)

El COMMIT (--aceptar) solo toca el árbol con hechos VERIFICADOS:
cita literal presente en la fuente (VERIFICADA), sin posible_homonimo, y
confirmado por la consolidación con fuente_url. Un bautismo confirmado
arrastra además los PADRES nombrados en la partida (salto de generación) y
crea sus fichas.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime

from config import (BASE_DIR, ESTADO_PATH, FAMILIA_JSON_PATH,
                    INFORME_PROGRESO_MD, MUNICIPIOS_EQUIVALENTES,
                    PROVINCIAS_SIN_ENSENADA, REFINADO_JSON, HALLAZGOS_JSON,
                    APELLIDOS_COMUNES, DIGITALIZACION_PROVINCIA, STOPWORDS,
                    DIR_BACKUPS, BACKUP_MAX_COPIAS, REGISTRO_PATH,
                    _limpiar_claves, normalizar)
from agent.evidencia import (ETIQUETA_SECCION_CANDIDATO,
                             ETIQUETA_SECCION_CONFIRMADO,
                             ETIQUETA_SECCION_DEBIL, NIVEL_CANDIDATO_FUERTE,
                             NIVEL_COINCIDENCIA_DEBIL, NIVEL_CONFIRMADO,
                             progenitores_confirmados, secciones_evidencia)
from utils import ui
from utils.personas import asignar_ids, emparejar_persona, tokens_persona


# ============================== HELPERS GENEALÓGICOS =====================

def rareza_apellido(apellido: str) -> int:
    """0 = hiperfrecuente (Lopez), 1 = compuesto con trozo común,
    3 = raro (Merillas, Panero, Pelaz...). Un apellido raro en un pueblo
    pequeño permite buscar SOLO por apellido: cada partida del índice es
    casi seguro familia."""
    ap = normalizar(apellido or "").strip()
    if not ap:
        return 0
    if ap in APELLIDOS_COMUNES:
        return 0
    tokens = [t for t in ap.split() if t not in STOPWORDS]
    if any(t in APELLIDOS_COMUNES for t in tokens):
        return 1
    return 3


def puntos_digitalizacion(provincias: list[str]) -> int:
    """Mejor puntuación de digitalización entre las provincias (0 si ninguna)."""
    puntos = [DIGITALIZACION_PROVINCIA.get(normalizar(p), 0)
              for p in (provincias or []) if p]
    return max(puntos) if puntos else 0


def cargar_familia(path: str = FAMILIA_JSON_PATH) -> dict:
    """Carga familia_conocida.json en memoria (con claves limpias)."""
    ruta = BASE_DIR / path
    if not ruta.exists():
        raise SystemExit(f"No encuentro '{path}'.")
    return _limpiar_claves(json.loads(ruta.read_text(encoding="utf-8")))


def cargar_estado() -> dict:
    ruta = BASE_DIR / ESTADO_PATH
    if ruta.exists():
        try:
            datos = json.loads(ruta.read_text(encoding="utf-8"))
            if isinstance(datos, dict):
                datos.setdefault("ciclo", 0)
                datos.setdefault("frontera", [])
                datos.setdefault("candidatos", [])
                datos.setdefault("descartados", [])
                # v4.2 (punto 4): entradas ya investigadas en ciclos previos
                datos.setdefault("investigados", [])
                return datos
        except (json.JSONDecodeError, OSError):
            ui.log_warn(f"{ESTADO_PATH} corrupto: se recalcula.")
    return {"ciclo": 0, "frontera": [], "candidatos": [],
            "descartados": [], "profundidad": {}, "investigados": []}


def guardar_estado(estado: dict) -> None:
    (BASE_DIR / ESTADO_PATH).write_text(
        json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8")


def _generaciones(familia: dict) -> dict:
    """Profundidad de cada persona: 0 = la raíz (quien reconstruye el árbol),
    1 = sus padres, 2 = abuelos..."""
    personas = familia.get("personas", [])
    por_nombre = {normalizar(p.get("nombre", "")): p for p in personas}
    raiz = next((normalizar(p.get("nombre", "")) for p in personas
                 if "reconstruyendo" in (p.get("notas") or "").lower()), None)
    prof: dict[str, int] = {}
    if raiz:
        cola = [(raiz, 0)]
        while cola:
            actual, g = cola.pop()
            if actual in prof:
                continue
            prof[actual] = g
            p = por_nombre.get(actual, {})
            for progenitor in (p.get("padre", ""), p.get("madre", "")):
                n = normalizar(progenitor)
                if n and n in por_nombre and n not in prof:
                    cola.append((n, g + 1))
    return prof


def _geo_persona(p: dict, por_nombre: dict) -> tuple[str, str]:
    """(municipio, provincia) de una persona: los suyos o los heredados de
    sus hijos (los padres aparecen en las partidas de los hijos)."""
    muns, provs = set(), set()
    for evento in ("nacimiento", "defuncion"):
        ev = _limpiar_claves(p.get(evento, {}))
        if ev.get("municipio"):
            muns.add(ev["municipio"])
            alias = MUNICIPIOS_EQUIVALENTES.get(normalizar(ev["municipio"]))
            if alias:
                muns.add(alias)
        if ev.get("provincia"):
            provs.add(ev["provincia"])
    if not muns:
        for hijo in p.get("hijos", []) or []:
            h = por_nombre.get(normalizar(hijo))
            if not h:
                continue
            for evento in ("nacimiento", "defuncion"):
                ev = _limpiar_claves(h.get(evento, {}))
                if ev.get("municipio"):
                    muns.add(ev["municipio"])
                if ev.get("provincia"):
                    provs.add(ev["provincia"])
    return (sorted(muns)[0] if muns else "",
            sorted(provs)[0] if provs else "")


def _anio_de(texto) -> int | None:
    m = re.search(r"\b(1[4-9]\d{2}|20\d{2})\b", str(texto or ""))
    return int(m.group(1)) if m else None


# ================== v4.2 — HELPERS DE IDENTIDAD Y DEDUPE ==================

def _nombre_casa(a: str, b: str) -> bool:
    """v4.2 (punto 6): True si dos nombres plausiblemente designan a la
    MISMA persona: iguales normalizados, o los tokens de uno son subconjunto
    de los del otro en CUALQUIER dirección ('Isidro Merillas' casa con
    'Isidro Merillas Panero'). Se usa como fallback del matching exacto:
    antes, un hallazgo con el nombre corto se descartaba EN SILENCIO porque
    no coincidía literalmente con la ficha."""
    na, nb = normalizar(a or ""), normalizar(b or "")
    if not na or not nb:
        return False
    if na == nb:
        return True
    ta, tb = tokens_persona(a), tokens_persona(b)
    return bool(ta and tb and (ta <= tb or tb <= ta))


def _clave_evidencia(ev: dict) -> str:
    """v4.2 (punto 3 del informe): clave ESTABLE de deduplicación de
    evidencias. SIN el timestamp del commit: antes se comparaba el dict
    entero, el campo 'commit' cambiaba en cada ejecución y la MISMA prueba
    se re-apuntaba una y otra vez. Consecuencia: el fichero de familia se
    llenaba de líneas repetidas y el freno del autopiloto ('este ciclo no
    ha aportado nada, paro') nunca saltaba, porque el contador de
    evidencias siempre crecía."""
    return "|".join((
        normalizar(str(ev.get("tipo", ""))),
        normalizar(str(ev.get("fecha", ""))),
        normalizar(str(ev.get("lugar", ""))),
        (ev.get("fuente_url") or "").strip(),
        normalizar(str(ev.get("cita", ""))[:400]),
        normalizar(str(ev.get("origen", ""))),
    ))


def _backup_con_rotacion(ruta_fam) -> None:
    """v4.2 (punto 7 del informe): copia de seguridad con MARCA DE TIEMPO
    (YYYYMMDD_HHMMSS) dentro de backups/, conservando las últimas
    BACKUP_MAX_COPIAS. La copia única .bak de la v4.1 se machacaba en cada
    ejecución: si un commit metía datos malos y volvías a ejecutar, perdías
    también la copia buena."""
    dir_bak = BASE_DIR / DIR_BACKUPS
    dir_bak.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = dir_bak / f"{ruta_fam.stem}_{ts}{ruta_fam.suffix}"
    try:
        shutil.copy2(ruta_fam, destino)
        # rotación: borrar solo las que se salen del tope (las más viejas)
        copias = sorted(dir_bak.glob(f"{ruta_fam.stem}_*{ruta_fam.suffix}"))
        for vieja in copias[:-BACKUP_MAX_COPIAS]:
            try:
                vieja.unlink()
            except OSError:
                pass
        ui.log_doc(f"backup con marca de tiempo -> {DIR_BACKUPS}/"
                   f"{destino.name} (se conservan las últimas "
                   f"{BACKUP_MAX_COPIAS})")
    except OSError as e:
        ui.log_warn(f"no se pudo hacer el backup con fecha: {str(e)[:80]} "
                    f"(se escribe el fichero igualmente: revisa a mano)")


def _registrar_evidencia_append_only(persona: dict, evidencia: dict) -> None:
    """v4.2 (punto 7 del informe): añade UNA línea JSON al registro
    append-only (registro_confirmaciones.jsonl). El registro SOLO crece:
    nunca se reescribe ni se borra. Aunque un commit malo corrompiera
    familia_conocida.json (y aunque se perdieran los backups), aquí queda
    el histórico completo de confirmaciones para reconstruir el árbol a
    mano. Escritura inmediata (append + flush) para sobrevivir a cortes."""
    linea = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "persona_id": persona.get("id", ""),
        "persona": persona.get("nombre", ""),
        "tipo": evidencia.get("tipo", ""),
        "fecha": evidencia.get("fecha", ""),
        "lugar": evidencia.get("lugar", ""),
        "fuente_url": evidencia.get("fuente_url", ""),
        "cita": (evidencia.get("cita") or "")[:400],
        "origen": evidencia.get("origen", ""),
        "verificado": True,
    }
    try:
        with open(BASE_DIR / REGISTRO_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(linea, ensure_ascii=False) + "\n")
    except OSError as e:
        ui.log_warn(f"no se pudo escribir en {REGISTRO_PATH}: "
                    f"{str(e)[:80]} (la confirmación SÍ está en el árbol)")


def _estrategias_para(provincia: str, tipo: str) -> list[str]:
    """Cómo se ataca una entrada de la frontera, por provincia y tipo."""
    prov = normalizar(provincia)
    e = []
    if prov in ("alava", "araba"):
        e.append("siga (buscador nominal 1481-1900)")
    if prov == "palencia":
        e.append("addo (buscador nominal)")
    if prov == "zamora":
        e += ["email zamora (--solicitudes)",
             "familysearch imagenes (descarga + OCR local)"]
    e.append("tavily + cluster FAN")
    if tipo == "padres":
        e.append("bautismo del hijo (nombra a los padres)")
        e.append("expediente matrimonial")
    if tipo == "hermanos":
        e.append("bautismos 'hijo de [Padre] y [Madre]'")
        e.append("expediente matrimonial de los padres (nombra a los 4 abuelos)")
    return e


# ============================== CÁLCULO DE LA FRONTERA ====================

def calcular_frontera(familia: dict | None = None,
                      estado_previo: dict | None = None) -> dict:
    """La cola priorizada de investigación.

    Tipos de entrada:
      - "persona":   sin evidencias confirmadas -> localizar su partida.
      - "padres":    persona confirmada cuyos padres no lo están (salto de
                     generación).
      - "hermanos":  FASE 4 — el objetivo tiene padres confirmados, hay que
                     localizar a sus hermanos (la nidada). Sus partidas
                     revelan datos colaterales vitales (abuelos, origen de
                     los padres).
      - "candidato": personas nuevas propuestas por la consolidación.

    prioridad = 1.5*rareza_apellido + digitalizacion_archivo
                + evidencia (2 confirmada / 1 fecha memoria / 0 nada)
                + 1 si la persona nacio <= 1900 (mas cerca de 1700)

    v4.2 (punto 4 del informe): la frontera ya no repite trabajo.
      - Entradas "hermanos": UNA SOLA por nidada (binomio de padres):
        antes cada hermano confirmado generaba su propia entrada para la
        MISMA nidada y el ciclo rebuscaba a los mismos hermanos N veces.
      - Entradas YA INVESTIGADAS en un ciclo anterior (estado
        "investigados", clave ancla+tipo): quedan fuera salvo que el tipo
        cambie (p. ej. al confirmarse los padres pasa de "padres" a
        "hermanos"), que es justo cuando hay algo nuevo que buscar.
    """
    familia = familia or cargar_familia()
    estado_previo = estado_previo or cargar_estado()
    personas = [_limpiar_claves(p) for p in familia.get("personas", [])]
    por_nombre = {normalizar(p.get("nombre", "")): p
                  for p in personas if p.get("nombre")}
    prof = _generaciones(familia)

    # v4.2 (punto 4): claves de entradas ya investigadas en ciclos previos.
    investigados = {}
    for inv in estado_previo.get("investigados", []) or []:
        if isinstance(inv, dict) and inv.get("clave"):
            investigados[inv["clave"]] = inv.get("ciclo", 0)
    nidadas_vistas: set[str] = set()

    def _clave_entrada(ancla: str, tipo: str, padres: list[str]) -> str:
        """Clave estable de una entrada: ancla+tipo (para 'hermanos', el
        binomio de padres, que es lo que de verdad define la búsqueda)."""
        if tipo == "hermanos" and padres:
            return "hermanos::" + "::".join(
                sorted(normalizar(x) for x in padres))
        return f"{tipo}::{normalizar(ancla)}"

    frontera: list[dict] = []
    omitidas_investigadas = 0
    for p in personas:
        nombre = (p.get("nombre") or "").strip()
        if not nombre:
            continue
        clave = normalizar(nombre)
        confirmada = bool(p.get("evidencias"))
        mun, prov = _geo_persona(p, por_nombre)
        anio = _anio_de(_limpiar_claves(p.get("nacimiento", {}))
                        .get("fecha_aproximada", ""))
        progenitores = [x.strip() for x in (p.get("padre", ""), p.get("madre", ""))
                        if x and x.strip()]
        # v4.3: matching DIFUSO del progenitor (emparejar_persona) en vez
        # del exacto por por_nombre: la partida puede nombrar al padre como
        # 'Nazario Merillas' mientras la ficha creada por el commit (o por
        # el usuario) se llama 'Nazario Merillas Uribarri'. Con el matching
        # exacto esa pareja jamás constaba como confirmada y la frontera
        # regeneraba la entrada 'padres' ciclo tras ciclo (trabajo repetido
        # y presupuesto quemado sin avance real del árbol).
        padres_confirmados = all(
            bool((emparejar_persona(x, personas, avisar=False) or {}).get(
                "evidencias"))
            for x in progenitores) if progenitores else False

        # FASE 4 — NIDADA: si persona confirmada Y padres confirmados, hay
        # que buscar a sus hermanos (datos colaterales).
        tipo = None
        if confirmada and progenitores and not padres_confirmados:
            tipo = "padres"
        elif confirmada and padres_confirmados:
            # La persona y sus padres están confirmados. La siguiente
            # frontera útil es buscar a los HERMANOS de esta persona:
            # - Sus partidas nombran a los mismos padres (verificación).
            # - Sus matrimonios revelan a los 4 abuelos del cónyuge.
            # - Aportan datos colaterales vitales cuando la línea
            #   principal está atascada.
            # v4.2 (punto 4): UNA entrada por NIDADA (binomio de padres).
            # Antes el comentario decía "evitamos duplicar" pero el código
            # no lo hacía: cada hermano confirmado añadía su propia entrada
            # y el ciclo rebuscaba la MISMA nidada una y otra vez.
            clave_nidada = "::".join(sorted(normalizar(x)
                                           for x in progenitores))
            if clave_nidada in nidadas_vistas:
                continue   # esta nidada ya tiene su entrada en la frontera
            nidadas_vistas.add(clave_nidada)
            tipo = "hermanos"
        elif confirmada:
            continue  # confirmada y sin padres que buscar: nada que hacer
        else:
            tipo = "persona"

        prioridad = (
            1.5 * rareza_apellido(p.get("apellido_paterno", ""))
            + puntos_digitalizacion([prov] if prov else [])
            + (2 if confirmada else (1 if anio else 0))
            + (1 if (anio or 9999) <= 1900 else 0)
        )
        # Para "hermanos" añadimos un bonus: son el siguiente paso natural
        # y desbloquean líneas colaterales muertas.
        if tipo == "hermanos":
            prioridad += 0.5

        buscar = progenitores if tipo == "padres" else [nombre]
        if tipo == "hermanos":
            # Para buscar hermanos buscamos "bautismos hijo de Padre y Madre"
            # en el municipio. El "buscar" lo dejamos como el propio nombre
            # para que la semilla lo aplique; las queries específicas las
            # añade generar_objetivos_busqueda vía padres_confirmados=True.
            buscar = [nombre]

        motivo = {
            "padres": (f"padres de {nombre} sin confirmar"
                       + (f" (n. {anio})" if anio else "")
                       + "; localizar su bautismo/expediente matrimonial"),
            "persona": (f"sin confirmar: localizar su partida de nacimiento"
                        + (f" (~{anio})" if anio else " (fecha desconocida)")
                        + " y la de sus hermanos (cluster FAN)"),
            "hermanos": (f"{nombre} y sus padres confirmados: localizar a "
                         f"los HERMANOS de {nombre} (bautismos 'hijo de "
                         f"{'+'.join(progenitores)}'). Sus partidas revelan "
                         f"abuelos y datos colaterales vitales."),
        }[tipo]
        clave_investigacion = _clave_entrada(nombre, tipo, progenitores)
        # v4.2 (punto 4): si esta entrada ya se investigó en un ciclo
        # anterior (y su tipo no cambió, porque entonces la clave cambia),
        # queda fuera: no se vuelve a gastar en lo mismo.
        if clave_investigacion in investigados:
            omitidas_investigadas += 1
            continue

        frontera.append({
            "ancla": nombre,
            "apellido": p.get("apellido_paterno", ""),
            "tipo": tipo,
            "buscar": buscar,
            "padres": progenitores,  # para que fase1 pueda usarlos
            "municipio": mun,
            "provincia": prov,
            "prioridad": round(min(prioridad, 10.0), 1),
            "motivo": motivo,
            "estrategias": _estrategias_para(prov, tipo),
            "generacion": prof.get(clave),
            "clave_investigacion": clave_investigacion,
        })

    # candidatos del estado anterior
    for c in estado_previo.get("candidatos", []):
        if not isinstance(c, dict) or not c.get("nombre"):
            continue
        if any(normalizar(e["ancla"]) == normalizar(c["nombre"])
               for e in frontera):
            continue
        clave_inv = f"candidato::{normalizar(c['nombre'])}"
        if clave_inv in investigados:
            omitidas_investigadas += 1
            continue
        frontera.append({
            "ancla": c["nombre"], "apellido": "", "tipo": "candidato",
            "buscar": [c["nombre"]], "municipio": c.get("municipio", ""),
            "provincia": c.get("provincia", ""),
            "prioridad": round(1.5 * rareza_apellido(c.get("apellido", "")) + 5, 1),
            "motivo": f"candidato a validar: {c.get('motivo', '')}",
            "estrategias": ["tavily", "cluster FAN"],
            "generacion": None,
            "clave_investigacion": clave_inv,
        })

    frontera.sort(key=lambda e: -e["prioridad"])
    if omitidas_investigadas:
        ui.log(f"{omitidas_investigadas} entradas ya investigadas en ciclos "
               f"anteriores quedan fuera de la frontera (para reintentarlas, "
               f"borra 'investigados' de {ESTADO_PATH})")
    return {
        "ciclo": estado_previo.get("ciclo", 0),
        "frontera": frontera,
        "candidatos": estado_previo.get("candidatos", []),
        "descartados": estado_previo.get("descartados", []),
        "investigados": estado_previo.get("investigados", []),
        "profundidad": prof,
        "actualizado": datetime.now().isoformat(timespec="seconds"),
    }


def mostrar_frontera(estado: dict) -> None:
    ui.log_stats(f"Ciclo actual: {estado.get('ciclo', 0)} · "
                 f"{len(estado.get('frontera', []))} entradas en la frontera · "
                 f"{len(estado.get('investigados', []))} ya investigadas "
                 f"(fuera de la cola)")
    for e in estado.get("frontera", []):
        icono = {"padres": "🎯", "hermanos": "👥", "persona": "🔍",
                 "candidato": "❓"}.get(e["tipo"], "•")
        ui.log(f"  {icono} [{e['prioridad']:4.1f}] {e['ancla']} — "
               f"{e['tipo']} ({e.get('municipio') or 'sin municipio'})")
        ui.log(f"          buscar: {', '.join(e.get('buscar', [])) or '—'}")
        ui.log(f"          motivo: {e.get('motivo', '')}")
        ui.log(f"          estrategias: {', '.join(e.get('estrategias', []))}")
    if estado.get("candidatos"):
        ui.log(f"  + {len(estado['candidatos'])} candidatos sin validar "
               f"(personas nuevas propuestas por la consolidación; NO entran "
               f"al árbol hasta verificarse)")


# ============================== COMMIT: --aceptar ==========================

def _tipos_equivalentes(tipo: str) -> set[str]:
    """Equivalencias de tipos de evento entre consolidación y hallazgos."""
    t = normalizar(tipo)
    if t in ("nacimiento", "nacer", "bautismo", "bautizo", "bautismos"):
        return {"nacimiento", "bautismo", "bautizo", "nacer"}
    if t in ("defuncion", "fallecimiento", "entierro", "difunto", "muerte"):
        return {"defuncion", "fallecimiento", "entierro", "difunto"}
    if "matrimonio" in t or "boda" in t or "casamiento" in t:
        return {"matrimonio", "boda", "casamiento"}
    return {t}


def _apellido_de(nombre: str) -> str:
    """'Nazario Merillas' -> 'Merillas' (todo menos el nombre propio)."""
    partes = [x for x in (nombre or "").split() if x]
    if len(partes) > 1:
        return " ".join(partes[1:])
    return partes[0] if partes else ""


# v10.0 (TAREA 2 — salto de generación) — Parser de progenitores TOLERANTE.
# Formato canónico (el que el SYSTEM_PROMPT_HALLAZGOS exige desde v10.0):
#   "Nombre Apellidos (padre)"  /  "(madre)"  /  "(cónyuge)"...
# Formato tolerante (por si el modelo no obeyece): "hijo/hija de X y Y",
# también como "hijo legítimo/natural de X y de Y", en otros_nombres y,
# como ÚLTIMO RECURSO, en la cita literal. X->padre, Y->madre.
# Ante DOS candidatos distintos para el mismo rol: NO se crea nada y se
# avisa (nunca se adivina).
_RE_HIJO_DE = re.compile(
    r"\bhij[oa]s?\b[^,.;:()]{0,40}?\bde\s+([^,.;:()]+?)\s+y\s+(?:de\s+)?"
    r"((?:[^,.;:()\s]+)(?:\s+[^,.;:()\s]+){0,5}?)(?=\s*[,.;:)\n]|$)",
    re.IGNORECASE)


def _limpiar_candidato_progenitor(cand: str) -> str:
    """Limpia un candidato a progenitor: puntuación de los bordes,
    espacios dobles y basura evidente ('desconocido', 'no consta'...).
    Devuelve '' si el candidato no parece un nombre."""
    t = re.sub(r"\s+", " ", (cand or "").strip()).strip(",.;:¿?¡!\"'()")
    if len(t) < 3:
        return ""
    if not re.search(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]", t):
        return ""
    if re.search(r"desconocid|ignorad|no consta|sin nombre|padres",
                 t, re.IGNORECASE):
        return ""
    return t


def _progenitores_de_hijo_de(texto: str) -> list[tuple[str, str]]:
    """Extrae los pares (padre, madre) de todas las frases 'hijo/hija de
    X y Y' de un texto (v10.0). Devuelve [] si no hay ninguna."""
    pares: list[tuple[str, str]] = []
    for m in _RE_HIJO_DE.finditer(texto or ""):
        padre = _limpiar_candidato_progenitor(m.group(1))
        madre = _limpiar_candidato_progenitor(m.group(2))
        if padre and madre:
            pares.append((padre, madre))
    return pares


# ============ v10.4 (P1) — PADRINOS Y TESTIGOS (roles colaterales) ==========
# El informe de metodología profesional es explícito: los padrinos de las
# partidas suelen ser tíos, abuelos o vecinos, y "quienes comparten apellidos
# con el padre o la madre son muy probablemente familiares directos", lo que
# los convierte en pistas de primer orden para ramas colaterales.
# El prompt de fase 2 YA pide estos roles en otros_nombres (config.py:
# "...(abuelo), (abuela), (padrino), (madrina), (hijo), (hija), (testigo)")
# y hasta la v10.4 el parser los ignoraba por completo: dato pagado (tokens
# de extracción), dato tirado.
# FILOSOFÍA (innegociable): un padrino/testigo NUNCA es un dato de filiación
# y NUNCA cambia el nivel de evidencia de ningún hallazgo — sería parentesco
# por apellido, justo lo prohibido. Son PISTAS: se convierten en candidatos
# de la frontera (se investigan, no entran al árbol).
ROLES_COLATERALES = ("padrino", "madrina", "testigo")


def _patron_rol(roles: str | tuple[str, ...]) -> re.Pattern:
    """Patrón 'Nombre Apellidos (rol)' de otros_nombres. `roles` admite un
    rol o varios ALTERNATIVOS (p. ej. ('padrino', 'padrinos'), para no
    depender de que el modelo use el singular). El GRUPO 1 es el nombre."""
    if isinstance(roles, str):
        roles = (roles,)
    alternancia = "|".join(re.escape(r) for r in roles)
    return re.compile(r"^\s*([^()]+?)\s*\((?:es\s+)?(?:su\s+)?(?:"
                      + alternancia + r")\)", re.IGNORECASE)


def _nombres_con_rol(otros_nombres: list,
                     roles: str | tuple[str, ...]) -> list[str]:
    """Nombres limpios y SIN repetir de las entradas 'Nombre (rol)' de
    otros_nombres, en el orden en que aparecen."""
    patron = _patron_rol(roles)
    nombres: list[str] = []
    for cand in otros_nombres or []:
        m = patron.match((cand or "").strip())
        if not m:
            continue
        limpio = _limpiar_candidato_progenitor(m.group(1))
        if limpio and normalizar(limpio) not in {normalizar(x)
                                                 for x in nombres}:
            nombres.append(limpio)
    return nombres


def nombres_colaterales(h: dict) -> list[tuple[str, str]]:
    """[(rol, nombre)] de los padrinos/madrinas/testigos que el documento
    nombra en otros_nombres de un hallazgo. El rol vuelve SIEMPRE en
    singular ('padrino' aunque el modelo escriba 'padrinos'), sin duplicados.

    Solo se lee el formato CON etiqueta de rol: para un padrino no existe
    ningún patrón tolerante fiable ('hijo de X y Y' habla de progenitores) y
    adivinar aquí sería peor que no tener el dato."""
    out: list[tuple[str, str]] = []
    vistos: set[tuple[str, str]] = set()
    otros = h.get("otros_nombres") or []
    for rol in ROLES_COLATERALES:
        for nombre in _nombres_con_rol(otros, (rol, rol + "s")):
            clave = (rol, normalizar(nombre))
            if clave in vistos:
                continue
            vistos.add(clave)
            out.append((rol, nombre))
    return out


def _extraer_progenitor(otros_nombres: list, rol: str,
                        cita_literal: str = "") -> str:
    """'Nazario Merillas (padre)' -> 'Nazario Merillas' para rol='padre'.

    v10.0 (TAREA 2): antes SOLO entendía el formato 'Nombre (padre)' en
    otros_nombres, así que cuando el modelo de fase 2 escribía 'hijo de X
    y Y' el árbol NO crecía. Ahora, por este orden:
      1. 'Nombre Apellidos (rol)' en otros_nombres (formato del prompt).
      2. 'hijo/hija de X y Y' en otros_nombres (X->padre, Y->madre).
      3. Como ÚLTIMO RECURSO, 'hijo/hija de X y Y' en cita_literal.
    Si en 1+2 hay DOS candidatos DISTINTOS para el mismo rol, NO se crea
    ninguno y se avisa con log_warn (nunca se adivina). Los candidatos se
    limpian de puntuación y basura antes de compararlos.
    """
    candidatos: list[str] = []
    # 1) Formato canónico: "Nombre Apellidos (rol)". El patrón lo comparte
    #    _patron_rol con los roles colaterales (v10.4/P1): UNA sola fuente
    #    de verdad para el formato de otros_nombres.
    for cand in otros_nombres or []:
        for limpio in _nombres_con_rol([cand], rol):
            candidatos.append(limpio)
    # 2) Formato tolerante: "hijo/hija de X y Y" en otros_nombres.
    for cand in otros_nombres or []:
        for padre, madre in _progenitores_de_hijo_de(cand or ""):
            candidatos.append(padre if rol == "padre" else madre)

    unicos: list[str] = []
    for c in candidatos:
        if normalizar(c) not in {normalizar(u) for u in unicos}:
            unicos.append(c)
    if len(unicos) == 1:
        return unicos[0]
    if len(unicos) > 1:
        ui.log_warn(
            f"salto de generación: {len(unicos)} candidatos DISTINTOS para "
            f"el rol '{rol}' ({' vs '.join(unicos[:2])}) en otros_nombres: "
            f"NO se crea la ficha (nunca se adivina).")
        return ""

    # 3) Último recurso: "hijo/hija de X y Y" en la cita literal.
    cands_cita = [p[0] if rol == "padre" else p[1]
                  for p in _progenitores_de_hijo_de(cita_literal or "")]
    unicos_cita: list[str] = []
    for c in cands_cita:
        if normalizar(c) not in {normalizar(u) for u in unicos_cita}:
            unicos_cita.append(c)
    if len(unicos_cita) == 1:
        return unicos_cita[0]
    if len(unicos_cita) > 1:
        ui.log_warn(
            f"salto de generación: {len(unicos_cita)} candidatos DISTINTOS "
            f"para el rol '{rol}' ({' vs '.join(unicos_cita[:2])}) en la "
            f"cita literal: NO se crea la ficha (nunca se adivina).")
    return ""


def cometer_confirmaciones(aplicar: bool = True) -> dict:
    """COMMIT: fusiona en familia_conocida.json SOLO los eventos de
    arbol_refinado.json que superen el filtro de evidencia:

      1. existe un hallazgo de esa persona y ese tipo de evento,
      2. con verificacion_cita == "VERIFICADA" (la cita está literalmente
         en el TEXTO ORIGINAL de la fuente) y posible_homonimo == False,
      3. y la consolidación lo lista en eventos_confirmados con fuente_url.

    Efectos: rellena fechas/lugares VACÍOS (nunca sobrescribe), añade la
    evidencia con su cita, marca persona "estado": "confirmado" y, al
    confirmar un nacimiento/bautismo, incorpora los PADRES nombrados en la
    partida creando sus fichas ("documentado") = salto de generación.

    v4.2 (puntos 3, 6 y 7 del informe):
      - DEDUPE estable de evidencias por contenido (sin el timestamp): la
        misma prueba ya no se re-apunta en cada ejecución, el fichero deja
        de llenarse de líneas repetidas y el freno del autopiloto
        ("ciclo sin aportes -> paro") vuelve a funcionar.
      - CONTADORES honestos: "evidencias" cuenta SOLO evidencias nuevas;
        "actualizadas" cuenta solo eventos que cambiaron algo de verdad.
      - MATCHING difuso (utils/personas.py): el evento de la consolidación
        casa con la ficha por id estable, nombre exacto o subconjunto de
        tokens ('Isidro Merillas' ~ 'Isidro Merillas Panero'); tocayos se
        desambiguan por año y, si no se puede, NO se fusiona (aviso).
      - SEGURIDAD: backup con marca de tiempo y rotación (backups/) y
        registro append-only (registro_confirmaciones.jsonl) que solo
        crece: aunque un commit corrompiera el árbol, el histórico de
        confirmaciones siempre es recuperable.
    """
    ruta_fam = BASE_DIR / FAMILIA_JSON_PATH
    ruta_ref = BASE_DIR / REFINADO_JSON
    ruta_hall = BASE_DIR / HALLAZGOS_JSON
    vacio = {"actualizadas": 0, "nuevas": 0, "evidencias": 0, "candidatos": []}
    if not ruta_ref.exists():
        ui.log_warn(f"no existe {REFINADO_JSON}: nada que cometer "
                    f"(ejecuta antes la fase 2).")
        return vacio
    refinado = _limpiar_claves(json.loads(ruta_ref.read_text(encoding="utf-8")))
    familia = _limpiar_claves(json.loads(ruta_fam.read_text(encoding="utf-8")))
    hallazgos: list[dict] = []
    if ruta_hall.exists():
        try:
            hallazgos = json.loads(ruta_hall.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            hallazgos = []

    por_persona_tipo: dict[tuple, list[dict]] = {}
    por_persona_id: dict[tuple, list[dict]] = {}   # v4.2 (punto 6)
    por_tipo: dict[str, list[dict]] = {}
    for h in hallazgos:
        if not isinstance(h, dict):
            continue
        clave = (normalizar(h.get("persona", "")),
                 normalizar(h.get("tipo_evento", "")))
        por_persona_tipo.setdefault(clave, []).append(h)
        por_tipo.setdefault(normalizar(h.get("tipo_evento", "")), []).append(h)
        if h.get("persona_id"):
            por_persona_id.setdefault(
                (h["persona_id"], normalizar(h.get("tipo_evento", ""))),
                []).append(h)

    # v4.2 (punto 6): ids estables para todas las fichas (incluidas las que
    # vengan de familia_conocida.json sin migrar aún).
    asignar_ids(familia)
    personas = [_limpiar_claves(p) for p in familia.get("personas", [])]
    # _limpiar_claves puede descopiar la lista: reconstruir por id/nombre
    por_id = {p.get("id"): p for p in personas if p.get("id")}
    por_nombre = {normalizar(p.get("nombre", "")): p
                  for p in personas if p.get("nombre")}
    actualizadas = nuevas = evidencias = 0

    def _es_verificador(h: dict) -> bool:
        # v9.1 (PARTE 0): un hallazgo clasificado como coincidencia_debil
        # (solo apellido+zona) NUNCA da fe de una filiación, aunque su cita
        # esté verificada: la cita prueba que el documento existe, no que
        # la persona sea de la familia. Hallazgos pre-v9.1 (sin campo) y
        # confirmados/candidatos_fuertes siguen siendo elegibles.
        if h.get("nivel_evidencia") == NIVEL_COINCIDENCIA_DEBIL:
            return False
        return (h.get("verificacion_cita") == "VERIFICADA"
                and not h.get("posible_homonimo")
                and (h.get("cita_literal") or "").strip())

    def hallazgo_verificador(nombre: str, tipo: str,
                             pid: str | None = None) -> dict | None:
        """v4.2 (punto 6): busca el hallazgo que da fe del evento.
        1) por persona_id ESTABLE (los hallazgos v4.2 lo llevan: los
           tocayos no se cruzan);
        2) por nombre exacto;
        3) difuso: el nombre del hallazgo casa con el de la ficha por
           subconjunto de tokens en cualquier dirección ('Isidro
           Merillas' ~ 'Isidro Merillas Panero'): antes ese hallazgo no
           se encontraba y el evento se descartaba EN SILENCIO."""
        for t in _tipos_equivalentes(tipo):
            if pid:
                for h in por_persona_id.get((pid, t), []):
                    if _es_verificador(h):
                        return h
            for h in por_persona_tipo.get((normalizar(nombre), t), []):
                if _es_verificador(h):
                    return h
        for t in _tipos_equivalentes(tipo):
            for h in por_tipo.get(t, []):
                if _es_verificador(h) and _nombre_casa(
                        h.get("persona", ""), nombre):
                    return h
        return None

    for bloque in refinado.get("personas", []):
        if not isinstance(bloque, dict):
            continue
        nombre = (bloque.get("nombre") or "").strip()
        if not nombre:
            continue
        p = por_nombre.get(normalizar(nombre))
        if p is None:
            # v4.2 (punto 6): matching difuso con el año del primer
            # evento confirmado como pista de desambiguación.
            anio_pista = _anio_de(" ".join(
                str(e.get("fecha", "")) for e in
                bloque.get("eventos_confirmados", [])[:3]
                if isinstance(e, dict)))
            p = emparejar_persona(nombre, list(por_nombre.values()),
                                  anio=anio_pista, contexto="commit")
        if p is None:
            continue
        cambio = False
        for ev in (_limpiar_claves(e) for e in bloque.get("eventos_confirmados", [])
                   if isinstance(e, dict)):
            tipo = (ev.get("tipo") or "").strip()
            if not tipo or not (ev.get("fuente_url") or "").strip():
                continue
            h = hallazgo_verificador(p.get("nombre", ""), tipo,
                                     pid=p.get("id"))
            if h is None:
                continue
            evidencia = {
                "tipo": tipo,
                "fecha": ev.get("fecha", ""),
                "lugar": ev.get("lugar", ""),
                "fuente_url": ev.get("fuente_url", ""),
                "cita": (h.get("cita_literal") or ev.get("cita") or "")[:400],
                "origen": h.get("origen", "web"),
                "verificado": True,
                "commit": datetime.now().isoformat(timespec="seconds"),
            }
            # v4.2 (punto 3): dedupe por CLAVE ESTABLE (sin el timestamp).
            previas = {_clave_evidencia(e)
                       for e in (p.get("evidencias") or [])}
            if _clave_evidencia(evidencia) not in previas:
                p.setdefault("evidencias", []).append(evidencia)
                evidencias += 1
                cambio = True
                # v4.2 (punto 7): registro append-only (solo crece)
                _registrar_evidencia_append_only(p, evidencia)
            if p.get("estado") != "confirmado":
                cambio = True
            p["estado"] = "confirmado"

            fecha = ev.get("fecha", "")
            lugar = ev.get("lugar", "")
            tipos = _tipos_equivalentes(tipo)
            if tipos & {"nacimiento", "bautismo", "bautizo"}:
                nac = p.setdefault("nacimiento", {})
                if not isinstance(nac, dict):
                    nac = {}
                    p["nacimiento"] = nac
                if fecha and not nac.get("fecha_aproximada"):
                    nac["fecha_aproximada"] = fecha
                    cambio = True
                if lugar and not nac.get("municipio"):
                    nac["municipio"] = lugar
                    cambio = True
                # SALTO DE GENERACIÓN: padres nombrados en la partida.
                # v10.0 (TAREA 2): parser tolerante — además del formato
                # "Nombre (padre)", acepta "hijo de X y Y" en otros_nombres
                # y, como último recurso, en la cita_literal.
                for rol, campo in (("padre", "padre"), ("madre", "madre")):
                    nom = _extraer_progenitor(
                        h.get("otros_nombres", []), rol,
                        cita_literal=h.get("cita_literal", ""))
                    if not nom:
                        continue
                    if not p.get(campo):
                        p[campo] = nom
                        cambio = True
                    elif normalizar(p[campo]) != normalizar(nom):
                        p["notas"] = (p.get("notas") or "") + (
                            f" [aviso commit: el {rol} de la partida ({nom}) "
                            f"difiere del conocido ({p[campo]})]")
                        continue
                    # v4.2 (punto 6): ¿ya existe una ficha para ese
                    # progenitor? matching difuso (antes: exacto y en
                    # silencio).
                    existente = por_nombre.get(normalizar(nom)) or next(
                        (x for x in personas
                         if _nombre_casa(x.get("nombre", ""), nom)), None)
                    if existente is not None:
                        continue
                    conyuge = p.get("madre" if rol == "padre" else "padre", "")
                    stub = {
                        "nombre": nom,
                        "apellido_paterno": _apellido_de(nom),
                        "apellido_materno": "",
                        "sexo": "M" if rol == "padre" else "F",
                        "nacimiento": {}, "defuncion": {},
                        "padre": "", "madre": "",
                        "conyuge": conyuge,
                        "hijos": [p.get("nombre", "")],
                        "notas": f"Nombrado como {rol} en la partida de "
                                 f"{p.get('nombre', '')} (ver evidencias).",
                        "estado": "documentado",
                        "evidencias": [dict(evidencia, tipo="mencion_en_partida")],
                        "fuente": "partida_verificada",
                    }
                    # v4.2 (punto 6): el id estable de la ficha nueva lo
                    # asigna asignar_ids(familia) al final del commit, con
                    # numeración GLOBAL (nunca reutiliza un id existente).
                    personas.append(stub)
                    por_nombre[normalizar(nom)] = stub
                    if stub.get("id"):
                        por_id[stub["id"]] = stub
                    nuevas += 1
                    _registrar_evidencia_append_only(
                        stub, dict(evidencia, tipo="mencion_en_partida"))
            elif tipos & {"defuncion", "fallecimiento", "difunto"}:
                dec = p.setdefault("defuncion", {})
                if not isinstance(dec, dict):
                    dec = {}
                    p["defuncion"] = dec
                if fecha and not dec.get("fecha_aproximada"):
                    dec["fecha_aproximada"] = fecha
                    cambio = True
                if lugar and not dec.get("municipio"):
                    dec["municipio"] = lugar
                    cambio = True
            elif tipos & {"matrimonio", "boda", "casamiento"}:
                mats = p.setdefault("matrimonios", [])
                entrada = {"tipo": "matrimonio", "fecha": fecha, "lugar": lugar,
                           "fuente_url": evidencia["fuente_url"]}
                if entrada not in mats:
                    mats.append(entrada)
                    cambio = True
        if cambio:
            actualizadas += 1

    # ---- v10.4 (M1): commit de las PERSONAS NUEVAS confirmadas ----------
    # Hasta la v10.3 el árbol solo crecía de forma ASCENDENTE: cada ficha
    # nueva nacía como stub de padre/madre de alguien ya conocido, así que
    # un HERMANO (partida verificada, con sus dos progenitores ya
    # confirmados) quedaba atrapado para siempre en
    # personas_nuevas_candidatas. Aquí entran las que el clasificador
    # DETERMINISTA marcó como "confirmado" por la regla de los DOS
    # PROGENITORES (dos datos independientes: padre y madre), siempre que
    # tengan hallazgo verificador con cita VERIFICADA. Si algo no cuadra se
    # AVISA: nunca un descarte en silencio (filosofía de fallo explícito).
    confirmados_arbol: set[str] = set()
    if aplicar:
        for candidata in (refinado.get("personas_nuevas_candidatas") or []):
            if not isinstance(candidata, dict):
                continue
            if (candidata.get("nivel_evidencia") or "") != NIVEL_CONFIRMADO:
                continue
            nombre_nuevo = (candidata.get("nombre") or "").strip()
            if not nombre_nuevo:
                continue
            clave_nuevo = normalizar(nombre_nuevo)
            if clave_nuevo in por_nombre or emparejar_persona(
                    nombre_nuevo, personas, avisar=False,
                    contexto="commit:nueva") is not None:
                confirmados_arbol.add(clave_nuevo)  # ya estaba: no se duplica
                continue
            h = None
            for tipo_ev in ("nacimiento", "bautismo", "matrimonio"):
                h = hallazgo_verificador(nombre_nuevo, tipo_ev)
                if h is not None:
                    break
            if h is None:
                ui.log_warn(
                    f"COMMIT: '{nombre_nuevo}' está clasificada como "
                    f"CONFIRMADA (dos progenitores) pero no encuentro su "
                    f"hallazgo verificador (nacimiento, bautismo o "
                    f"matrimonio) en {HALLAZGOS_JSON}: NO entra al árbol. "
                    f"Revisa esa clasificación a mano.")
                continue
            pareja = progenitores_confirmados(h, familia)
            if pareja is None:
                ui.log_warn(
                    f"COMMIT: '{nombre_nuevo}' está clasificada como "
                    f"CONFIRMADA pero sus DOS progenitores ya no casan con "
                    f"fichas confirmadas: NO entra al árbol (nunca se "
                    f"adivina).")
                continue
            nombre_padre = (pareja[0].get("nombre") or "").strip()
            nombre_madre = (pareja[1].get("nombre") or "").strip()
            evidencia = {
                "tipo": h.get("tipo_evento", ""),
                "fecha": h.get("fecha_valor", ""),
                "lugar": h.get("lugar", ""),
                "fuente_url": (h.get("url_fuente")
                               or candidata.get("fuente_url") or ""),
                "cita": (h.get("cita_literal") or "")[:400],
                "origen": h.get("origen", "web"),
                "verificado": True,
                "commit": datetime.now().isoformat(timespec="seconds"),
            }
            nacimiento: dict = {}
            matrimonios: list[dict] = []
            # v10.4 (M1b): cada evento va a SU sitio. La fecha de una BODA
            # no es una fecha de nacimiento (el informe de metodología
            # insiste en no mezclar eventos), así que el matrimonio se
            # guarda en 'matrimonios' con la MISMA forma que usa el resto
            # del commit, y 'nacimiento' solo se rellena con
            # nacimientos/bautismos.
            tipos_ev = _tipos_equivalentes(evidencia["tipo"])
            if tipos_ev & {"nacimiento", "bautismo", "bautizo", "nacer"}:
                if evidencia["fecha"]:
                    nacimiento["fecha_aproximada"] = evidencia["fecha"]
                if evidencia["lugar"]:
                    nacimiento["municipio"] = evidencia["lugar"]
            elif tipos_ev & {"matrimonio", "boda", "casamiento"}:
                matrimonios.append({
                    "tipo": "matrimonio",
                    "fecha": evidencia["fecha"],
                    "lugar": evidencia["lugar"],
                    "fuente_url": evidencia["fuente_url"],
                })
            # Cónyuge nombrado en la partida ('(cónyuge)'): se lee con el
            # lector de ROLES (no con _extraer_progenitor, cuyo formato
            # tolerante 'hijo de X y Y' devolvería a la MADRE para
            # cualquier rol que no sea 'padre'). Si hay varios candidatos
            # distintos, no se adivina.
            conyuges = _nombres_con_rol(h.get("otros_nombres") or [],
                                        ("cónyuge", "conyuge", "esposo",
                                         "esposa"))
            nueva = {
                "nombre": nombre_nuevo,
                # los apellidos salen de los PROGENITORES ya confirmados
                "apellido_paterno": (_apellido_de(nombre_padre)
                                     or _apellido_de(nombre_nuevo)),
                "apellido_materno": _apellido_de(nombre_madre),
                "sexo": "",        # la filiación no implica el sexo del bebé
                "nacimiento": nacimiento, "defuncion": {},
                "matrimonios": matrimonios,
                "padre": nombre_padre, "madre": nombre_madre,
                "conyuge": conyuges[0] if len(conyuges) == 1 else "",
                "hijos": [],
                "notas": (f"Nombrado en su propia partida "
                          f"({evidencia['tipo']}) con los DOS progenitores "
                          f"ya confirmados ({nombre_padre} y {nombre_madre}): "
                          f"entra al árbol por la regla de dos datos "
                          f"independientes (v10.4/M1)."),
                "estado": NIVEL_CONFIRMADO,
                "evidencias": [dict(evidencia)],
                "fuente": "partida_verificada",
            }
            personas.append(nueva)
            por_nombre[clave_nuevo] = nueva
            confirmados_arbol.add(clave_nuevo)
            nuevas += 1
            evidencias += 1
            # v4.2 (punto 7): el registro append-only no se salta ni aquí
            _registrar_evidencia_append_only(nueva, evidencia)
            # El nuevo hermano entra en la lista 'hijos' de sus DOS padres:
            # así los progenitores heredan municipio por sus hijos
            # (_geo_persona) y el GEDCOM sale coherente.
            for nombre_prog in (nombre_padre, nombre_madre):
                ficha_prog = emparejar_persona(nombre_prog, personas,
                                               avisar=False,
                                               contexto="commit:hijos")
                if ficha_prog is None:
                    continue
                hijos_prog = ficha_prog.get("hijos")
                if not isinstance(hijos_prog, list):
                    hijos_prog = []
                    ficha_prog["hijos"] = hijos_prog
                if nombre_nuevo not in hijos_prog:
                    hijos_prog.append(nombre_nuevo)
            ui.log_ok(f"COMMIT: ficha NUEVA '{nombre_nuevo}' como hijo/a de "
                      f"{nombre_padre} y {nombre_madre} (2 datos "
                      f"independientes verificados)")

    if aplicar and (evidencias or nuevas or actualizadas):
        # v4.2 (punto 7): backup con marca de tiempo + rotación (nunca una
        # única copia .bak que se machaca en la siguiente ejecución).
        _backup_con_rotacion(ruta_fam)
        familia["personas"] = personas
        # v4.2: ids estables para las fichas nuevas de este commit
        nuevos_ids = asignar_ids(familia)
        ruta_fam.write_text(json.dumps(familia, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        extra = (f" (+{nuevos_ids} ids estables nuevos)"
                 if nuevos_ids else "")
        ui.log_ok(f"COMMIT: {actualizadas} fichas con cambios, "
                  f"{evidencias} evidencias NUEVAS y {nuevas} "
                  f"padres/fichas nuevas -> {FAMILIA_JSON_PATH} "
                  f"(backup con fecha en {DIR_BACKUPS}/, registro en "
                  f"{REGISTRO_PATH}){extra}")
    elif actualizadas:
        ui.log(f"COMMIT (seco): {actualizadas} fichas con cambios "
               f"elegibles y {evidencias} evidencias nuevas; {nuevas} "
               f"fichas nuevas. Nada escrito.")
    else:
        ui.log_warn(f"COMMIT: ningún evento superó el filtro de verificación. "
                    f"Normal tras búsquedas sin resultados documentales.")

    # candidatos nuevos -> estado (se investigan, NO entran al árbol)
    candidatos = [{"nombre": (c.get("nombre") or "").strip(),
                   "motivo": c.get("motivo", ""),
                   "fuente_url": c.get("fuente_url", ""),
                   # v10.4 (P1): el municipio y el apellido viajan con el
                   # candidato. El apellido lo usa calcular_frontera para
                   # PRIORIZAR por rareza (antes llegaba siempre vacío: el
                   # término 1.5*rareza_apellido() valía 0 para todo el
                   # mundo); el municipio, para que la búsqueda del
                   # candidato tenga localidad (padrinos y testigos salen
                   # del mismo pueblo que la partida que los nombra).
                   "municipio": c.get("municipio", ""),
                   "apellido": c.get("apellido", ""),
                   # v9.1 (PARTE 0): el nivel de evidencia viaja con el
                   # candidato para que el informe de progreso y la frontera
                   # sepan si es una pista seria o ruido de apellido.
                   "nivel_evidencia": c.get("nivel_evidencia",
                                             NIVEL_COINCIDENCIA_DEBIL)}
                  for c in refinado.get("personas_nuevas_candidatas", [])
                  if isinstance(c, dict) and (c.get("nombre") or "").strip()]
    if candidatos:
        estado = cargar_estado()
        previos = {normalizar(c.get("nombre", ""))
                   for c in estado.get("candidatos", [])}
        # v10.4 (M1): las personas nuevas que este commit acaba de meter en
        # el árbol YA no son candidatas (ni lo eran ya si estaban dentro).
        nuevos_c = [c for c in candidatos
                    if normalizar(c["nombre"]) not in previos
                    and normalizar(c["nombre"]) not in confirmados_arbol]
        if nuevos_c:
            estado.setdefault("candidatos", []).extend(nuevos_c)
            guardar_estado(estado)
            ui.log(f"    {len(nuevos_c)} candidatos nuevos -> {ESTADO_PATH}")
        candidatos = nuevos_c
    return {"actualizadas": actualizadas, "nuevas": nuevas,
            "evidencias": evidencias, "candidatos": candidatos}


# ============================== INFORME DE PROGRESO =======================

def generar_informe_progreso(familia: dict | None = None,
                             estado: dict | None = None) -> None:
    """Escribe informe_progreso.md: profundidad por línea (apellido) y el
    próximo paso recomendado según la frontera."""
    familia = familia or cargar_familia()
    estado = estado or cargar_estado()
    personas = [_limpiar_claves(p) for p in familia.get("personas", [])]
    lineas: dict[str, dict] = {}
    for p in personas:
        ap = (p.get("apellido_paterno") or "").strip()
        if not ap:
            continue
        d = lineas.setdefault(ap, {"conf": None, "mem": None, "n": 0,
                                   "confirmadas": 0})
        d["n"] += 1
        anios = [a for a in (
            _anio_de(_limpiar_claves(p.get("nacimiento", {}))
                     .get("fecha_aproximada", "")),
            _anio_de(_limpiar_claves(p.get("defuncion", {}))
                     .get("fecha_aproximada", ""))) if a]
        anio = min(anios) if anios else None
        if p.get("evidencias"):
            d["confirmadas"] += 1
            if anio and (d["conf"] is None or anio < d["conf"]):
                d["conf"] = anio
        elif anio and (d["mem"] is None or anio < d["mem"]):
            d["mem"] = anio

    frontera = estado.get("frontera", [])
    proximo: dict[str, str] = {}
    for ap in lineas:
        ap_norm = normalizar(ap)
        for e in frontera:
            if ap_norm in normalizar(e.get("ancla", "")):
                proximo[ap] = (f"{e.get('tipo')} de {e.get('ancla')}: "
                               f"{', '.join(e.get('estrategias', [])[:2])}")
                break

    filas = ["| Línea | Confirmado hasta | Memoria hasta | Faltan (gen.) | "
             "Próximo paso |",
             "|---|---|---|---|---|"]
    for ap, d in sorted(lineas.items()):
        anio = d["conf"] or d["mem"]
        faltan = "—" if anio is None else str(max(0, round((anio - 1700) / 28)))
        conf = f"{d['conf']} ({d['confirmadas']} conf.)" if d["conf"] else "—"
        mem = str(d["mem"]) if d["mem"] else "—"
        filas.append(f"| {ap} | {conf} | {mem} | {faltan} | "
                     f"{proximo.get(ap, '—')} |")

    confirmadas = sum(1 for p in personas if p.get("evidencias"))

    # v9.1 (PARTE 0): sección de evidencia con las 3 clases separadas.
    # Fuente: arbol_hallazgos.json (si existe la fase 2 lo acaba de
    # escribir con nivel_evidencia) + los candidatos del estado, que
    # llevan su nivel desde el commit.
    hallazgos: list[dict] = []
    try:
        ruta_hall = BASE_DIR / HALLAZGOS_JSON
        if ruta_hall.exists():
            cargado = json.loads(ruta_hall.read_text(encoding="utf-8"))
            if isinstance(cargado, list):
                hallazgos = [h for h in cargado if isinstance(h, dict)]
    except (json.JSONDecodeError, OSError):
        hallazgos = []
    secs = secciones_evidencia(hallazgos)
    cands_estado = [c for c in estado.get("candidatos", [])
                    if isinstance(c, dict)]
    cands_nivel = {"confirmado": [], "candidato_fuerte": [],
                   "coincidencia_debil": []}
    for c in cands_estado:
        nivel = c.get("nivel_evidencia") or NIVEL_COINCIDENCIA_DEBIL
        if nivel not in cands_nivel:      # valor desconocido -> débil
            nivel = NIVEL_COINCIDENCIA_DEBIL
        cands_nivel[nivel].append(c)
    bloques_evidencia = [
        f"## {ETIQUETA_SECCION_CONFIRMADO} "
        f"({len(secs[NIVEL_CONFIRMADO])} hallazgos · "
        f"{len(cands_nivel['confirmado'])} candidatas)",
        "Cada uno conecta con persona conocida por >=2 datos "
        "independientes (nombre + fecha/lugar/padres/cónyuge). "
        "ÚNICA clase que cuenta como progreso real del árbol.\n",
        f"## {ETIQUETA_SECCION_CANDIDATO} "
        f"({len(secs[NIVEL_CANDIDATO_FUERTE])} hallazgos · "
        f"{len(cands_nivel['candidato_fuerte'])} candidatas)",
        "Apellido compuesto + municipio exacto + fecha coherente con la "
        "generación: merecen una consulta dirigida (SIGA/IRARGI/"
        "FamilySearch) para conseguir el segundo dato que los confirme "
        "o los descarte.\n",
        f"## {ETIQUETA_SECCION_DEBIL} "
        f"({len(secs[NIVEL_COINCIDENCIA_DEBIL])} hallazgos · "
        f"{len(cands_nivel['coincidencia_debil'])} candidatas)",
        "Solo apellido o zona amplia. NO son progreso: descartables salvo "
        "que aparezca nueva evidencia (por eso ya NO entran al árbol por "
        "el commit).\n",
    ]

    contenido = (
        f"# Progreso del árbol genealógico — "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        "Objetivo: confirmar hasta 1700 y más atrás "
        "(una generación ≈ 28 años).\n\n"
        + "\n".join(filas) + "\n\n"
        + "\n".join(bloques_evidencia) + "\n"
        f"**Personas confirmadas:** {confirmadas} de {len(personas)} · "
        f"**Entradas en la frontera:** {len(frontera)} · "
        f"**Candidatos sin validar:** {len(cands_estado)} "
        f"(fuertes: {len(cands_nivel['candidato_fuerte'])}, "
        f"débiles: {len(cands_nivel['coincidencia_debil'])})\n\n"
        "Cómo leerlo: «Confirmado hasta» = año más antiguo con evidencia "
        "verificada (cita en la fuente) de esa línea; «Memoria hasta» = lo "
        "mismo según memoria familiar (sin confirmar); «Faltan» = "
        "generaciones estimadas hasta 1700. El próximo paso sale de la "
        "frontera priorizada (`--frontera`).\n"
        "Método (v9.1): cada generación se conecta con la siguiente "
        "mediante >=2 datos INDEPENDIENTES que coincidan; una coincidencia "
        "de apellido+geografía NUNCA es prueba de parentesco.\n")
    # v10.4 (P3): sección de EVIDENCIA NEGATIVA (búsquedas infructuosas).
    # Es lo que pide el informe de metodología profesional y lo que permite
    # decidir dónde escribir/ir en persona sin repetir lo ya intentado.
    # Degrada con elegancia: si el registro está vacío, la sección no se
    # añade y el informe queda igual que siempre.
    try:
        from agent.evidencia_negativa import texto_markdown as _neg_md
        bloque_neg = _neg_md()
    except Exception as e:
        bloque_neg = ""
        ui.log_warn(f"sección de evidencia negativa no generada: "
                    f"{str(e)[:80]}")
    if bloque_neg:
        contenido += "\n" + bloque_neg
    (BASE_DIR / INFORME_PROGRESO_MD).write_text(contenido, encoding="utf-8")
    ui.log_doc(f"Informe de progreso -> {INFORME_PROGRESO_MD}")
