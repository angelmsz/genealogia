"""
agent/evidencia.py — Nivel de evidencia de cada hallazgo (v9.1, PARTE 0).

PROBLEMA DE MÉTODO QUE RESUELVE:
  De 716 "hallazgos" de una ejecución real, solo 1 era confirmación
  documental real; los otros 715 eran personas con apellido parecido en la
  misma zona, cada una con "conexión no probada" en su propia descripción,
  presentadas como progreso. Eso viola el principio genealógico básico:
  NUNCA aceptar una coincidencia de apellido+geografía como prueba.

REGLA (método genealógico estándar):
  Cada generación debe conectar con la siguiente mediante AL MENOS 2 datos
  INDEPENDIENTES que coincidan: nombre completo + fecha aproximada, o
  nombre completo + cónyuge/padres ya conocidos, o nombre + lugar exacto.

TRES NIVELES:
  - "confirmado": nombre completo casa con una persona de
    familia_conocida.json (o ya confirmada en el árbol) Y además >=1 dato
    verificable independiente coincide (fecha, cónyuge, padres, lugar
    exacto). Sin contradicción de fechas ni posible_homonimo.
  - "candidato_fuerte": apellido completo compuesto + municipio exacto +
    rango de fecha coherente con la generación, pero SIN segundo dato que
    case con una persona conocida.
  - "coincidencia_debil": solo apellido o zona amplia (lo que antes se
    mezclaba con todo lo demás en "nuevas_pistas").

DECISIÓN DE DISEÑO CRÍTICA: el clasificador es DETERMINISTA (este módulo)
y PREVALECE sobre la clasificación que haga el LLM en la extracción. El
prompt de extracción también pide nivel_evidencia (para que el modelo
razone la justificación al extraer), pero el valor final que se guarda
sale de aquí: código auditable, reproducible y no influenciable por la
alucinación de un modelo. Si el clasificador no puede clasificar, deja
"coincidencia_debil" (conservador: el error cuesta una pista perdida,
nunca una filiación falsa).

GUARDES contra los dos fallos que producían confirmaciones falsas:
  1. CONTRADICCIÓN de fechas: si el hallazgo casa por nombre con una
     ficha conocida pero su fecha contradice a la ficha (p. ej. 54 años
     de diferencia con el nacimiento conocido), NO es confirmado: es un
     tocayo probable y baja a coincidencia_debil.
  2. COMPARACIÓN CIRCULAR: un dato solo cuenta como "verificable" si
     procede de una fuente DISTINTA del propio hallazgo. El lugar del
     hallazgo se compara contra el municipio de la FICHA conocida (nunca
     contra sí mismo); los padres/cónyuge se buscan en otros_nombres pero
     solo cuentan si el nombre que aparece NO es el de la propia persona
     (que sería la misma fila del índice diciéndose a sí misma).

Sin I/O de ficheros: funciones puras (testeables aisladas), como
utils/personas.py.
"""

from __future__ import annotations

import re

from config import (MUNICIPIOS_EQUIVALENTES, STOPWORDS, _limpiar_claves,
                    normalizar, sin_tildes, _tokens_apellido)
from utils.personas import (anio_persona, emparejar_persona,
                            tokens_persona)

# ============================== CONSTANTES ==================================

NIVEL_CONFIRMADO = "confirmado"
NIVEL_CANDIDATO_FUERTE = "candidato_fuerte"
NIVEL_COINCIDENCIA_DEBIL = "coincidencia_debil"
NIVELES_VALIDOS = (NIVEL_CONFIRMADO, NIVEL_CANDIDATO_FUERTE,
                   NIVEL_COINCIDENCIA_DEBIL)

# Etiquetas de las 3 secciones tal y como deben salir en el informe de
# progreso y en el resumen_general del árbol (texto EXACTO, para que los
# tests y el usuario las encuentren siempre igual).
ETIQUETA_SECCION_CONFIRMADO = "Confirmado documentalmente"
ETIQUETA_SECCION_CANDIDATO = "Candidatos fuertes a verificar"
ETIQUETA_SECCION_DEBIL = ("Coincidencias débiles "
                          "(descartables salvo nueva evidencia)")

# Holgura de la fecha como segundo dato (en años), por tipo de evento
# respecto al NACIMIENTO conocido de la ficha. Un bautismo debe caer cerca
# del nacimiento; una defunción, entre el nacimiento y los 110 años; un
# matrimonio u otra mención, entre los ~14 y los ~90 años de vida.
TOLERANCIA_NACIMIENTO = 8      # bautismo/nacimiento: nacimiento ± 8 años
EDAD_MAXIMA_DEFUNCION = 110    # defunción: 0..110 años después de nacer
EDAD_MINIMA_EVENTO = 14        # matrimonio/mención: >=14 años de vida
EDAD_MAXIMA_EVENTO = 90        # matrimonio/mención: <=90 años de vida

# Ventana de "fecha coherente con la generación" para personas que NO
# casan con ninguna ficha conocida (líneas por apellido).
VENTANA_GENERACION = 90        # años alrededor de la ventana de la línea

RE_ANIO = re.compile(r"\b(1[4-9]\d{2}|20\d{2})\b")


# ============================== HELPERS =====================================

def _anio_de(texto) -> int | None:
    m = RE_ANIO.search(str(texto or ""))
    return int(m.group(1)) if m else None


def _mun_norm(municipio: str) -> str:
    return normalizar(sin_tildes(municipio or ""))


def _lugares_casan(lugar_a: str, lugar_b: str) -> bool:
    """True si dos topónimos plausiblemente designan el mismo municipio
    (contención en cualquier dirección tras normalizar, con alias)."""
    a, b = _mun_norm(lugar_a), _mun_norm(lugar_b)
    if not a or not b:
        return False
    alias_a = _mun_norm(MUNICIPIOS_EQUIVALENTES.get(normalizar(lugar_a or ""),
                                                    ""))
    alias_b = _mun_norm(MUNICIPIOS_EQUIVALENTES.get(normalizar(lugar_b or ""),
                                                    ""))
    return any((a in b or b in a) for a, b in
               ((a, b), (alias_a, b), (a, alias_b))
               if a and b)


def _municipios_ficha(p: dict) -> list[str]:
    """Municipios conocidos de una ficha (nacimiento y defunción)."""
    muns = []
    for evento in ("nacimiento", "defuncion"):
        ev = _limpiar_claves(p.get(evento, {}))
        if ev.get("municipio"):
            muns.append(str(ev["municipio"]))
    return muns


def _apellidos_familia(personas: list[dict]) -> dict[str, int]:
    """Tokens de APELLIDO conocidos de la familia -> nº de fichas que los
    llevan. Fuente: apellido_paterno/apellido_materno explícitos, los
    tokens finales del nombre (en 'Isidro Merillas Panero' los apellidos
    son los tokens que no son nombre de pila) y los APELLIDOS de los
    progenitores/cónyuge NOMBRADOS en las fichas (los padres de una ficha
    son línea familiar por definición: sus apellidos son apellidos de la
    familia aunque no tengan ficha propia)."""
    frec: dict[str, int] = {}

    def _sumar(apellido: str):
        for tok in _tokens_apellido(apellido):
            t = normalizar(sin_tildes(tok))
            if t and len(t) > 2:
                frec[t] = frec.get(t, 0) + 1

    def _tokens_apellidos_de(nombre: str):
        """Tokens de un nombre propio que NO son el nombre de pila (se
        asume que el PRIMERO en orden es el nombre de pila, como
        'Obdulia Pelaz' -> {'pelaz'}). OJO: tokens_persona() devuelve un
        set desordenado; aquí hay que conservar el ORDEN."""
        toks = [t for t in normalizar(nombre or "").split()
                if len(t) > 2 and t not in STOPWORDS]
        return toks[1:]

    for p in personas:
        for campo in ("apellido_paterno", "apellido_materno"):
            ap = (p.get(campo) or "").strip()
            if ap:
                _sumar(ap)
        # fallback: tokens del nombre que no son nombre de pila — solo si
        # no hay apellidos explícitos.
        if not (p.get("apellido_paterno") or p.get("apellido_materno")):
            for t in _tokens_apellidos_de(p.get("nombre", "")):
                frec[t] = frec.get(t, 0) + 1
        # progenitores/cónyuge nombrados: sus apellidos son de la línea
        for rol in ("padre", "madre", "conyuge", "cónyuge"):
            for t in _tokens_apellidos_de(p.get(rol) or ""):
                frec[t] = frec.get(t, 0) + 1
    return frec


def _anios_linea(personas: list[dict],
                 apellidos: dict[str, int]) -> tuple[int | None, int | None]:
    """(min, max) de años conocidos de las fichas que llevan alguno de los
    apellidos de la línea (incluidos los progenitores NOMBRADOS en la
    ficha, que son línea familiar). (None, None) si no hay fechas."""
    anios = []
    for p in personas:
        toks = {normalizar(sin_tildes(t))
                for t in _tokens_apellido(p.get("apellido_paterno", ""))
                + _tokens_apellido(p.get("apellido_materno", ""))}
        fuente = p.get("nombre", "")
        if fuente:
            toks |= {t for t in normalizar(sin_tildes(fuente)).split()
                     if len(t) > 2 and t not in STOPWORDS}
        for rol in ("padre", "madre", "conyuge", "cónyuge"):
            toks |= {t for t in normalizar(p.get(rol) or "").split()
                     if len(t) > 2 and t not in STOPWORDS}
        if toks & set(apellidos):
            a = anio_persona(p)
            if a:
                anios.append(a)
    return (min(anios), max(anios)) if anios else (None, None)


def _fecha_evento_coherente(anio_evento: int, anio_nacimiento: int,
                            tipo_evento: str) -> bool:
    """Coherencia de la fecha del evento con el nacimiento conocido de la
    ficha, según el tipo de evento (NO simétrica: una defunción es SIEMPRE
    posterior al nacimiento; un bautismo, contemporáneo)."""
    dif = anio_evento - anio_nacimiento
    t = normalizar(tipo_evento or "")
    if t in ("nacimiento", "bautismo"):
        return abs(dif) <= TOLERANCIA_NACIMIENTO
    if t == "defuncion":
        return 0 <= dif <= EDAD_MAXIMA_DEFUNCION
    # matrimonio, mencion, otro: dentro de la vida adulta plausible
    return EDAD_MINIMA_EVENTO - TOLERANCIA_NACIMIENTO <= dif <= EDAD_MAXIMA_EVENTO


def _contradiccion_de_fechas(anio_evento: int, anio_nacimiento: int,
                             tipo_evento: str) -> int | None:
    """Magnitud de la contradicción si la fecha del evento es INCOMPATIBLE
    con el nacimiento conocido de la ficha; None si es compatible.

    Un bautismo 54 años antes del nacimiento conocido, o una defunción
    ANTES de nacer, son contradicciones reales (tocayo probable), no
    simples imprecisiones."""
    dif = anio_evento - anio_nacimiento
    t = normalizar(tipo_evento or "")
    if t in ("nacimiento", "bautismo"):
        # holgura de tolerancia a ambos lados: fuera de ahí es contradicción
        if abs(dif) > TOLERANCIA_NACIMIENTO:
            return dif
    elif t == "defuncion":
        if dif < 0 or dif > EDAD_MAXIMA_DEFUNCION:
            return dif
    else:
        if dif < EDAD_MINIMA_EVENTO - TOLERANCIA_NACIMIENTO - 10 \
                or dif > EDAD_MAXIMA_EVENTO + 20:
            return dif
    return None


def _dato_relacionados(h: dict, p: dict) -> str | None:
    """Segundo dato independiente por CÓNYUGE/PADRES: alguno de los
    nombres relacionados conocidos de la ficha (padre, madre, cónyuge)
    aparece en otros_nombres del hallazgo.

    GUARD anti-circular: el nombre buscado en otros_nombres debe ser el de
    OTRA persona (padre/madre/cónyuge), nunca el de la propia persona del
    hallazgo — una fila de índice que repite el nombre del bautizado como
    'hijo de...' no aporta un dato independiente."""
    propios = tokens_persona(h.get("persona", ""))
    texto_otros = " ".join(str(x) for x in (h.get("otros_nombres") or []))
    if not texto_otros.strip():
        return None
    for rol in ("padre", "madre", "conyuge", "cónyuge"):
        nombre_rel = (p.get(rol) or "").strip()
        if not nombre_rel:
            continue
        toks_rel = {t for t in tokens_persona(nombre_rel) if len(t) > 2}
        # los tokens del relacionado deben aparecer en otros_nombres PERO
        # no ser simplemente los tokens de la propia persona (circular)
        if toks_rel and toks_rel <= tokens_persona(texto_otros) \
                and not (toks_rel <= propios):
            etiqueta = {"conyuge": "cónyuge", "cónyuge": "cónyuge"}.get(rol, rol)
            return f"{etiqueta} conocido '{nombre_rel}' aparece en el documento"
    return None


# ============================== CLASIFICADOR ================================

def clasificar_hallazgo(h: dict, familia: dict) -> dict:
    """Clasifica UN hallazgo y le añade en sitio:
      - "nivel_evidencia": confirmado | candidato_fuerte | coincidencia_debil
      - "datos_que_casan": lista de datos independientes que coinciden
      - "justificacion_evidencia": por qué ese nivel (auditable).

    El valor previo de nivel_evidencia (el del LLM) se recalcula y
    sobrescribe SIEMPRE: este clasificador es la fuente de verdad.
    Devuelve el propio hallazgo (por comodidad en pipes)."""
    if not isinstance(h, dict):
        return h
    personas = [_limpiar_claves(p) for p in (familia or {}).get("personas", [])]
    anio_h = _anio_de(h.get("fecha_valor", ""))
    tipo = h.get("tipo_evento", "")

    # matching difuso con las fichas conocidas (mismo criterio que el
    # resto del programa: utils/personas.emparejar_persona).
    persona = None
    if personas:
        persona = emparejar_persona(h.get("persona", ""), personas,
                                    anio=anio_h if tipo in
                                    ("nacimiento", "bautismo") else None,
                                    avisar=False, contexto="evidencia")

    datos: list[str] = []
    contradiccion: str | None = None

    if persona is not None:
        # DATO 1 (imprescindible): el nombre completo casa con una ficha
        # conocida (exacto o subconjunto de tokens con desambiguación).
        datos.append(f"nombre completo casa con la ficha conocida "
                     f"'{persona.get('nombre', '')}'")

        # DATO 2a: fecha coherente con el nacimiento conocido de la ficha
        anio_ficha = anio_persona(persona)
        if anio_h is not None and anio_ficha is not None:
            mal = _contradiccion_de_fechas(anio_h, anio_ficha, tipo)
            if mal is not None:
                contradiccion = (f"la fecha del evento ({anio_h}) contradice "
                                 f"al nacimiento conocido de la ficha "
                                 f"({anio_ficha}): diferencia de {mal:+d} años")
            elif _fecha_evento_coherente(anio_h, anio_ficha, tipo):
                datos.append(f"fecha coherente con el nacimiento conocido "
                             f"({anio_h} vs {anio_ficha})")

        # DATO 2b: lugar exacto (NO circular: lugar del hallazgo contra el
        # municipio CONOCIDO de la ficha, nunca contra sí mismo)
        lugar_h = h.get("lugar", "")
        for mun in _municipios_ficha(persona):
            if lugar_h and _lugares_casan(lugar_h, mun):
                datos.append(f"lugar del evento casa con el municipio "
                             f"conocido de la ficha ({mun})")
                break

        # DATO 2c: cónyuge/padres conocidos aparecen en el documento
        rel = _dato_relacionados(h, persona)
        if rel:
            datos.append(rel)

        # GUARD: la plausibilidad biológica ya marcó contradicción real
        if h.get("posible_homonimo"):
            contradiccion = contradiccion or (
                "marcado posible_homonimo por contradicción biológica "
                "comprobable (verificar_plausibilidad_biologica)")

        if contradiccion:
            nivel = NIVEL_COINCIDENCIA_DEBIL
            just = (f"{contradiccion}: nombre coincidente pero probable "
                    f"TOCAYO, no la misma persona")
        elif len(datos) >= 2:
            nivel = NIVEL_CONFIRMADO
            just = ("nombre completo + >=1 dato verificable independiente "
                    "coinciden con la ficha conocida: " + "; ".join(datos))
        else:
            nivel = NIVEL_CANDIDATO_FUERTE
            just = ("el nombre casa con una ficha conocida pero NINGÚN otro "
                    "dato verificable lo corrobora (hace falta fecha, lugar, "
                    "padres o cónyuge que casen)")
    else:
        # Persona desconocida (nueva): ¿candidato fuerte o coincidencia débil?
        # Requisitos de candidato_fuerte: apellido compuesto completo +
        # municipio exacto + fecha coherente con la generación.
        apellidos = _apellidos_familia(personas)
        toks_h = tokens_persona(h.get("persona", ""))
        # apellidos de la familia que aparecen en el nombre del hallazgo
        aps_h = {t for t in toks_h if t in apellidos}
        # compuesto: >=2 tokens de apellido de la familia en el nombre, o
        # un apellido conocido multi-token presente como cadena completa
        # (p. ej. 'saenz de navarrete' dentro del nombre del hallazgo).
        nombre_h_norm = normalizar(sin_tildes(h.get("persona", "")))
        compuesto = len(aps_h) >= 2 or any(
            len(_tokens_apellido(str(p.get(campo, "")))) >= 2
            and normalizar(sin_tildes(str(p.get(campo, ""))))
            in nombre_h_norm
            for p in personas
            for campo in ("apellido_paterno", "apellido_materno"))

        muns_familia = set()
        for p in personas:
            for m in _municipios_ficha(p):
                muns_familia.add(m)
        lugar_h = h.get("lugar", "")
        mun_exacto = lugar_h and any(
            _lugares_casan(lugar_h, m) for m in muns_familia)

        fecha_coherente = False
        if anio_h is not None:
            if aps_h:
                linf, lsup = _anios_linea(personas, apellidos)
                if linf is None:
                    fecha_coherente = 1700 <= anio_h <= 2100  # sin fechas de línea: no castigar
                else:
                    fecha_coherente = (linf - VENTANA_GENERACION
                                       <= anio_h <=
                                       lsup + VENTANA_GENERACION)
            else:
                fecha_coherente = 1700 <= anio_h <= 2100

        if compuesto and mun_exacto and fecha_coherente:
            nivel = NIVEL_CANDIDATO_FUERTE
            just = ("apellido compuesto de la familia + municipio exacto + "
                    "fecha coherente con la generación, pero sin segundo "
                    "dato que case con persona conocida")
        else:
            faltan = []
            if not compuesto:
                faltan.append("apellido completo compuesto")
            if not mun_exacto:
                faltan.append("municipio exacto")
            if not fecha_coherente:
                faltan.append("rango de fechas coherente")
            nivel = NIVEL_COINCIDENCIA_DEBIL
            just = ("solo coincidencia de apellido/zona: faltan "
                    + ", ".join(faltan)
                    + " — descartable salvo nueva evidencia")

    h["nivel_evidencia"] = nivel
    h["datos_que_casan"] = datos if nivel == NIVEL_CONFIRMADO else []
    h["justificacion_evidencia"] = just
    return h


def clasificar_hallazgos(hallazgos: list[dict], familia: dict) -> dict:
    """Clasifica TODOS los hallazgos (en sitio) y devuelve el recuento por
    nivel: {"confirmado": n, "candidato_fuerte": n, "coincidencia_debil": n}."""
    recuento = {NIVEL_CONFIRMADO: 0, NIVEL_CANDIDATO_FUERTE: 0,
                NIVEL_COINCIDENCIA_DEBIL: 0}
    for h in hallazgos:
        clasificar_hallazgo(h, familia)
        nivel = h.get("nivel_evidencia")
        if nivel in recuento:
            recuento[nivel] += 1
        else:  # valor no reconocido (p. ej. LLM desvariado): conservador
            h["nivel_evidencia"] = NIVEL_COINCIDENCIA_DEBIL
            recuento[NIVEL_COINCIDENCIA_DEBIL] += 1
    return recuento


# ============================== RESÚMENES ===================================

def secciones_evidencia(hallazgos: list[dict]) -> dict[str, list[dict]]:
    """Reparte los hallazgos clasificados en las 3 secciones del informe.

    Devuelve {"confirmado": [...], "candidato_fuerte": [...],
    "coincidencia_debil": [...]}; cada elemento lleva persona, cita corta,
    url y datos que casan (solo en confirmado)."""
    secs: dict[str, list[dict]] = {NIVEL_CONFIRMADO: [],
                                   NIVEL_CANDIDATO_FUERTE: [],
                                   NIVEL_COINCIDENCIA_DEBIL: []}
    for h in hallazgos:
        nivel = h.get("nivel_evidencia")
        if nivel not in secs:
            nivel = NIVEL_COINCIDENCIA_DEBIL
        secs[nivel].append({
            "persona": h.get("persona", ""),
            "evento": h.get("tipo_evento", ""),
            "fecha": h.get("fecha_valor", ""),
            "lugar": h.get("lugar", ""),
            "cita": (h.get("cita_literal") or "")[:120],
            "url": h.get("url_fuente", ""),
            "datos_que_casan": h.get("datos_que_casan", []),
            "justificacion": h.get("justificacion_evidencia", ""),
        })
    return secs


def texto_resumen_evidencia(hallazgos: list[dict]) -> str:
    """Bloque de texto con las 3 secciones, para AÑADIR al
    resumen_general del árbol (determinista: se recalcula siempre que se
    regenera el árbol, no lo escribe el LLM)."""
    secs = secciones_evidencia(hallazgos)
    n = {k: len(v) for k, v in secs.items()}
    lineas = [
        f"[{ETIQUETA_SECCION_CONFIRMADO}]: {n[NIVEL_CONFIRMADO]} — "
        "cada uno con >=2 datos independientes coincidentes con persona "
        "conocida (nombre + fecha/lugar/padres/cónyuge).",
        f"[{ETIQUETA_SECCION_CANDIDATO}]: {n[NIVEL_CANDIDATO_FUERTE]} — "
        "apellido compuesto + municipio exacto + fecha coherente con la "
        "generación; falta el segundo dato que case con persona conocida.",
        f"[{ETIQUETA_SECCION_DEBIL}]: {n[NIVEL_COINCIDENCIA_DEBIL]} — "
        "solo apellido o zona amplia; descartables salvo nueva evidencia.",
    ]
    return "\n".join(lineas)


def nivel_de_persona(nombre: str, hallazgos: list[dict]) -> str | None:
    """Mejor nivel de evidencia entre los hallazgos de una persona (para
    propagarlo a las personas_nuevas_candidatas del árbol refinado)."""
    mejor: dict[str, int] = {NIVEL_CONFIRMADO: 2, NIVEL_CANDIDATO_FUERTE: 1,
                             NIVEL_COINCIDENCIA_DEBIL: 0}
    toks = tokens_persona(nombre)
    nivel: str | None = None
    for h in hallazgos:
        if toks and toks <= tokens_persona(h.get("persona", "")):
            n = h.get("nivel_evidencia")
            if n in mejor and (nivel is None or mejor[n] > mejor[nivel]):
                nivel = n
    return nivel


def reclasificar_arbol(refinado: dict, hallazgos: list[dict] | None = None,
                       familia: dict | None = None) -> dict:
    """Reclasifica un arbol_refinado.json YA EXISTENTE (p. ej. el de los
    716 hallazgos) sin re-ejecutar la fase 2:

      1. Si trae hallazgos completos, se reclasifican con el clasificador
         determinista (necesita familia).
      2. Las personas_nuevas_candidatas reciben nivel_evidencia (el mejor
         de sus hallazgos; si no hay hallazgos, 'coincidencia_debil': una
         candidata con 'conexión no probada' es por definición débil).
      3. El resumen_general se reescribe con las 3 secciones.

    Mutación mínima: no toca personas/eventos; solo añade claves. Devuelve
    el propio dict (por comodidad)."""
    if not isinstance(refinado, dict):
        return refinado
    hallazgos = hallazgos or []
    familia = familia or {"personas": []}

    # 1) reclasificar los hallazgos que lleguen
    if hallazgos:
        clasificar_hallazgos(hallazgos, familia)
    secs = secciones_evidencia(hallazgos)
    n_conf = len(secs[NIVEL_CONFIRMADO])
    n_fuer = len(secs[NIVEL_CANDIDATO_FUERTE])
    n_deb = len(secs[NIVEL_COINCIDENCIA_DEBIL])

    # 2) nivel por candidata
    candidatas = refinado.get("personas_nuevas_candidatas")
    if isinstance(candidatas, list):
        for c in candidatas:
            if not isinstance(c, dict):
                continue
            nivel = (nivel_de_persona(c.get("nombre", ""), hallazgos)
                     if hallazgos else None)
            c["nivel_evidencia"] = nivel or NIVEL_COINCIDENCIA_DEBIL

    # 3) resumen_general con las 3 secciones (se PREFIJA el bloque
    #    determinista; el texto del LLM se conserva debajo).
    bloque = ("RESUMEN POR NIVEL DE EVIDENCIA (regla: >=2 datos "
              "independientes por generación)\n"
              + texto_resumen_evidencia(hallazgos)
              + f"\nPersonas nuevas candidatas: "
              + f"{len(candidatas) if isinstance(candidatas, list) else 0} "
              + f"(fuertes: "
              + f"{sum(1 for c in (candidatas or []) if isinstance(c, dict) and c.get('nivel_evidencia') == NIVEL_CANDIDATO_FUERTE)}, "
              + f"débiles: "
              + f"{sum(1 for c in (candidatas or []) if isinstance(c, dict) and c.get('nivel_evidencia') == NIVEL_COINCIDENCIA_DEBIL)})."
              + "\n\n---\n\n")
    previo = (refinado.get("resumen_general") or "").strip()
    # quita un bloque anterior para no acumularlo en reclasificaciones
    # sucesivas
    previo = re.split(r"\n\n---\n\n", previo, maxsplit=1)[-1]
    refinado["resumen_general"] = bloque + previo

    # 4) sección estructurada para consumo programático
    refinado["resumen_evidencia"] = {
        ETIQUETA_SECCION_CONFIRMADO: secs[NIVEL_CONFIRMADO],
        ETIQUETA_SECCION_CANDIDATO: secs[NIVEL_CANDIDATO_FUERTE],
        ETIQUETA_SECCION_DEBIL: secs[NIVEL_COINCIDENCIA_DEBIL],
        "totales": {NIVEL_CONFIRMADO: n_conf,
                    NIVEL_CANDIDATO_FUERTE: n_fuer,
                    NIVEL_COINCIDENCIA_DEBIL: n_deb},
    }
    return refinado
