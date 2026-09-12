"""
agent/gedcom.py — Exportación GEDCOM 5.5.1, solicitudes de partidas,
descenso inverso (Catastro de Ensenada) y diagnóstico.

v9.0 — diagnostico() comprueba además si el llama-server (OCR local con
olmOCR-2 vía Vulkan) está arrancado y qué modelo sirve (punto 7f).

Contiene:
  - exportar_gedcom: árbol familiar + hallazgos en formato GEDCOM 5.5.1
    con registros SOUR formales (citas verificables).
  - generar_solicitudes: plantillas de email para partidas no online.
  - generar_candidatos_ensenada: hipótesis de tatarabuelos a partir del
    Catastro de 1752.
  - importar_documentos_propios: transcribe fotos de certificados con
    OCR 100% local (v10.0) y las añade al corpus como fuente primaria.
  - diagnostico: prueba rápida previa (claves, BD, modelos, fechas).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from config import (BASE_DIR, DIR_DOCUMENTOS_PROPIOS, FAMILIA_JSON_PATH,
                    HALLAZGOS_JSON, GEDCOM_PATH, ARCHIVO_POR_PROVINCIA,
                    LIMITE_ANIOS_ONLINE, MAX_PDF_BYTES, MUNICIPIOS_EQUIVALENTES,
                    PARROQUIAS_CONOCIDAS, PARES_CATASTRO, PROVINCIAS_CONOCIDAS,
                    PROVINCIAS_SIN_ENSENADA, SALIDA_SOLICITUDES,
                    SALIDA_SOLICITUDES_MD, CANDIDATOS_ENSENADA, SESSION,
                    MODELO_FASE1, MODELO_FASE2, PRECIO_MILLON_TOKENS,
                    PRECIO_POR_DEFECTO, _limpiar_claves,
                    _consulta_conector_hecha, _marcar_conector,
                    normalizar, sha256_corto, sin_tildes, variantes_compuesto)
from scrapers.archivos import (ParesNoDisponible,
                                buscar_localidades_ensenada,
                                _filtrar_por_provincia, _formas_ensenada,
                                probar_conectores)
from utils import ui
from utils.llm import (GASTO, PresupuestoExcedido, chat_json,
                       presupuesto_agotado,
                       variantes_apellido)


# ============================== GEDCOM =====================================

MESES_GED = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
             "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

MESES_ES = {
    "enero": "JAN", "febrero": "FEB", "marzo": "MAR", "abril": "APR",
    "mayo": "MAY", "junio": "JUN", "julio": "JUL", "agosto": "AUG",
    "septiembre": "SEP", "octubre": "OCT", "noviembre": "NOV",
    "diciembre": "DEC",
}

RE_ISO = re.compile(r"(\d{4})-(\d{2})(?:-(\d{2}))?")
RE_ESP = re.compile(r"(?:(\d{1,2})\s*de\s*)?([a-záéíóúñ]+)\s*(?:de\s+|del\s+)?(\d{4})",
                    re.IGNORECASE)


def fecha_gedcom(fecha: str) -> str:
    """Convierte fechas en cualquier formato razonable al formato GEDCOM
    5.5.1 (DD MMM YYYY), p. ej. '2 de mayo de 1936' -> '2 MAY 1936'.
    Devuelve '' si no puede interpretar nada."""
    f = (fecha or "").strip()
    if not f:
        return ""
    t = normalizar(f)
    m = RE_ISO.search(t)
    if m:
        anio, mes = int(m.group(1)), int(m.group(2))
        if 1 <= mes <= 12:
            base = f"{MESES_GED[mes - 1]} {anio}"
            return f"{int(m.group(3))} {base}" if m.group(3) else base
    m = RE_ESP.search(t)
    if m and m.group(2) in MESES_ES:
        base = f"{MESES_ES[m.group(2)]} {m.group(3)}"
        return f"{int(m.group(1))} {base}" if m.group(1) else base
    if re.fullmatch(r"\d{4}", t):
        return f"ABT {t}"
    if re.search(r"\d{1,2}\s+[A-Z]{3}\s+\d{4}", f, re.IGNORECASE):
        return f.upper()
    return ""


def _tipos_equivalentes(tipo: str) -> set[str]:
    from agent.frontera import _tipos_equivalentes as te
    return te(tipo)


def exportar_gedcom(familia_path: str = FAMILIA_JSON_PATH,
                    hallazgos_path: str = HALLAZGOS_JSON,
                    salida: str = GEDCOM_PATH) -> None:
    """Escribe un GEDCOM 5.5.1 determinista a partir de familia_conocida.json,
    añadiendo los hallazgos documentales como notas con su fuente.

    v4.2 (punto 9 del informe — validez del estándar):
      - HEAD completo: 2 NAME bajo SOUR, 1 SUBM + registro SUBM (obligatorio
        para muchos validadores/importadores), 2 TIME bajo DATE.
      - Registros SOUR: TITL, ABBR y WWW/PUBL a NIVEL 1. Antes el URL iba
        como `2 URL` colgando de `1 TITL`: anidado MAL (el URL era un hijo
        del título), y Gramps/Ancestry podían perder la fuente al importar.
      - Citas largas: CONC (continuación SIN salto de línea) en vez de
        CONT, que metía un '\\n' en mitad de la cita textual.
      - Ninguna línea supera ~250 caracteres (tope del estándar: 255).

    v4.2 (punto 6 del informe — homónimos):
      - Cada persona tiene un XREF propio derivado de su ID ESTABLE
        (P0001...): el abuelo y el nieto tocayos ya no se funden en una
        sola ficha (antes el xref se asignaba por NOMBRE y el segundo
        tocayo sobrescribía al primero).
      - Las referencias por nombre (padre/madre/cónyuge/hijo) se resuelven
        con matching difuso y desambiguación por año; si el tocayo no se
        puede desambiguar, NO se fusiona a ciegas: se emite un NOTE de
        aviso.
      - Los hallazgos se adhieren a la persona por persona_id (o matching
        difuso): 'Isidro Merillas' ya llega a la ficha 'Isidro Merillas
        Panero' (antes se descartaba en silencio).
    """
    from agent.frontera import _nombre_casa
    from utils.personas import anio_persona, asignar_ids

    ruta_fam = BASE_DIR / familia_path
    with open(ruta_fam, "r", encoding="utf-8") as f:
        datos = _limpiar_claves(json.load(f))
    # v4.2 (punto 6): asegurar ids estables (solo AÑADE el campo 'id').
    nuevos_ids = asignar_ids(datos)
    if nuevos_ids:
        with open(ruta_fam, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, indent=2)
    personas = [_limpiar_claves(p) for p in datos.get("personas", [])]

    hallazgos = []
    ruta_h = BASE_DIR / hallazgos_path
    if ruta_h.exists():
        with open(ruta_h, "r", encoding="utf-8") as f:
            hallazgos = json.load(f)

    # ---- v4.2: una clave interna ÚNICA por persona (id estable) ----------
    claves: list[str] = []
    por_clave: dict[str, dict] = {}
    for p in personas:
        if not (p.get("nombre") or "").strip():
            continue
        base = (p.get("id")
                or normalizar(p.get("nombre", "")) or "sin_nombre")
        clave, k = base, 2
        while clave in por_clave:      # paranoia: nunca dos personas igual
            clave, k = f"{base}#{k}", k + 1
        claves.append(clave)
        por_clave[clave] = p

    def _fantasma(nombre: str) -> str:
        """Referencia por nombre que no casa con NINGUNA ficha: persona
        mínima para que la estructura familiar se vea completa en el
        GEDCOM (mismo comportamiento que la v4.1)."""
        clave = f"ref::{normalizar(nombre)}"
        if clave not in por_clave:
            claves.append(clave)
            por_clave[clave] = {"nombre": nombre.strip()}
        return clave

    por_nombre: dict[str, list[str]] = {}
    for c in claves:
        n = normalizar(por_clave[c].get("nombre", ""))
        if n:
            por_nombre.setdefault(n, []).append(c)

    # Ventanas de años plausibles (año_candidato - año_referencia):
    VENTANA_PROG = (13, 60)     # progenitores: 13-60 años mayores que el hijo
    VENTANA_CONY = (-12, 12)    # cónyuges: ±12 años

    def _resolver(nombre: str, anio_ref: int | None = None,
                  ventana: tuple | None = None) -> tuple[str | None, bool]:
        """Resuelve un nombre suelto (padre/madre/cónyuge/hijo) a la clave
        de una persona. Devuelve (clave, ambiguo):
          - (clave, False): resuelta (exacta o difusa);
          - (None, True):  VARIAS fichas casan y el año no desambigua
                           (tocayos): NO se fusiona a ciegas, el llamador
                           emite un NOTE de aviso;
          - (None, False): no casa con nadie -> fantasma."""
        n = normalizar(nombre or "")
        if not n:
            return None, False

        def _filtrar_anio(cands: list[str]) -> list[str]:
            """Filtra candidatos por la ventana de años (año_candidato -
            año_referencia). Si NINGUNO tiene año conocido no se puede
            filtrar: se devuelven todos (ambigüedad honesta). Si hay
            fechados y NINGUNO encaja, el padre/cónyuge real NO está en
            el árbol -> lista vacía (la referencia irá a fantasma)."""
            if anio_ref is None or ventana is None:
                return cands
            con_anio = [c for c in cands
                        if anio_persona(por_clave[c]) is not None]
            if not con_anio:
                return cands
            return [c for c in con_anio
                    if ventana[0]
                    <= anio_persona(por_clave[c]) - anio_ref
                    <= ventana[1]]

        exactos = por_nombre.get(n, [])
        if len(exactos) == 1:
            return exactos[0], False
        if len(exactos) > 1:
            encajan = _filtrar_anio(exactos)
            if len(encajan) == 1:
                return encajan[0], False
            if not encajan:
                # ninguna ficha fechada encaja: el progenitor real no está
                # en el árbol -> persona fantasma (NO fusionamos tocayos).
                return None, False
            return None, True
        difusos = [c for c in claves
                   if _nombre_casa(por_clave[c].get("nombre", ""), nombre)]
        if len(difusos) == 1:
            return difusos[0], False
        if len(difusos) > 1:
            encajan = _filtrar_anio(difusos)
            if len(encajan) == 1:
                return encajan[0], False
            if not encajan:
                return None, False
            return None, True
        return None, False

    # ---- estructuras familiares -------------------------------------------
    familias: dict[tuple, dict] = {}

    def fam(clave_padre: str, clave_madre: str) -> dict:
        k = (clave_padre, clave_madre)
        return familias.setdefault(k, {"husb": clave_padre,
                                       "wife": clave_madre, "chil": []})

    notas_homonimia: dict[str, list[str]] = {}

    def _apuntar_homonimia(clave: str, aviso: str) -> None:
        if aviso not in notas_homonimia.setdefault(clave, []):
            notas_homonimia[clave].append(aviso)

    for c in claves:
        p = por_clave[c]
        if not p.get("nombre"):
            continue
        anio_hijo = anio_persona(p)
        padre_cl, amb_p = _resolver(p.get("padre", ""), anio_hijo,
                                    VENTANA_PROG)
        madre_cl, amb_m = _resolver(p.get("madre", ""), anio_hijo,
                                    VENTANA_PROG)
        for amb, rol, valor in ((amb_p, "padre", p.get("padre", "")),
                                (amb_m, "madre", p.get("madre", ""))):
            if amb and valor:
                _apuntar_homonimia(
                    c, f"relación ambigua por homonimia: el {rol} "
                       f"'{valor}' casa con varias fichas y la fecha no "
                       f"desambigua; revisar a mano")
        if padre_cl is None and not amb_p and (p.get("padre") or "").strip():
            padre_cl = _fantasma(p["padre"])
        if madre_cl is None and not amb_m and (p.get("madre") or "").strip():
            madre_cl = _fantasma(p["madre"])
        if padre_cl or madre_cl:
            fam(padre_cl or "", madre_cl or "")["chil"].append(c)
        cony = (p.get("conyuge") or "").strip()
        if cony:
            cony_cl, amb_c = _resolver(cony, anio_hijo, VENTANA_CONY)
            if amb_c:
                _apuntar_homonimia(
                    c, f"relación ambigua por homonimia: el cónyuge "
                       f"'{cony}' casa con varias fichas y la fecha no "
                       f"desambigua; revisar a mano")
            elif cony_cl is None:
                cony_cl = _fantasma(cony)
            if cony_cl:
                sexo = (p.get("sexo") or "").strip().upper()
                if sexo == "M":
                    fam(c, cony_cl)
                elif sexo == "F":
                    fam(cony_cl, c)
                else:
                    fam(*sorted((c, cony_cl)))

    id_fam = {k: f"@F{i + 1}@" for i, k in enumerate(familias)}
    xref_de: dict[str, str] = {}
    for i, c in enumerate(claves):
        # v4.2 (punto 6): xref derivado del ID estable -> determinista y
        # único aunque dos personas se llamen exactamente igual.
        xref_de[c] = f"@I{i + 1}@"
    hijos_de: dict[str, list[str]] = {}
    fams_de: dict[str, list[str]] = {}
    for k, fm in familias.items():
        for hijo in fm["chil"]:
            hijos_de.setdefault(hijo, []).append(id_fam[k])
        for miembro in (fm["husb"], fm["wife"]):
            if miembro:
                fams_de.setdefault(miembro, []).append(id_fam[k])

    # hallazgos -> persona (por persona_id; fallback difuso único)
    hall_por_clave: dict[str, list[dict]] = {}
    for h in hallazgos:
        if not isinstance(h, dict):
            continue
        c_h = h.get("persona_id") if h.get("persona_id") in por_clave else None
        if c_h is None:
            difusos = [c for c in claves
                       if _nombre_casa(por_clave[c].get("nombre", ""),
                                       h.get("persona", ""))]
            if len(difusos) == 1:
                c_h = difusos[0]
        if c_h is not None:
            hall_por_clave.setdefault(c_h, []).append(h)

    fuentes: dict[str, str] = {}
    orden_fuentes: list[str] = []

    def id_fuente(url: str) -> str:
        if url and url not in fuentes:
            fuentes[url] = f"@S{len(fuentes) + 1}@"
            orden_fuentes.append(url)
        return fuentes.get(url, "")

    def titulo_fuente(url: str) -> str:
        u = (url or "").lower()
        if "ahdv-geah" in u:
            return "SIGA — Archivo Histórico Diocesano de Vitoria (1481-1900)"
        if "pares.cultura.gob" in u:
            return "PARES — Catastro de Ensenada (1749-1756)"
        if "archivodiocesanopalencia" in u:
            return "ADDO — Archivo Diocesano de Palencia"
        if u.startswith("documentos_propios/"):
            return "Documento familiar propio (OCR local v10.0)"
        if "familysearch" in u:
            return "FamilySearch"
        if "geneanet" in u:
            return "Geneanet"
        if "hispagen" in u:
            return "Hispagen"
        if "hemerotecadigital.bne" in u or "prensahistorica" in u:
            return "Hemeroteca digital (BNE / Prensa Histórica)"
        if "boe.es" in u:
            return "BOE"
        return "Fuente web"

    def _evidencia_de(p: dict, tipos: set[str]) -> dict | None:
        for ev in (p.get("evidencias", []) or []) + (p.get("matrimonios", []) or []):
            if _tipos_equivalentes(ev.get("tipo", "")) & tipos:
                return ev
        return None

    # v4.2 (punto 9): troceo con CONC (sin saltos de línea) y líneas <=255.
    MAX_LINEA = 200

    def _linea_valor(nivel: int, tag: str, valor: str) -> list[str]:
        """Emite `nivel tag valor` troceado con CONC si supera MAX_LINEA."""
        v = (valor or "").strip()
        if not v:
            return []
        out = [f"{nivel} {tag} {v[:MAX_LINEA]}"]
        resto = v[MAX_LINEA:]
        while resto:
            out.append(f"{nivel + 1} CONC {resto[:MAX_LINEA]}")
            resto = resto[MAX_LINEA:]
        return out

    def lineas_cita(evid: dict, nivel: int = 2) -> list[str]:
        sid = id_fuente(evid.get("fuente_url", ""))
        if not sid:
            return []
        out = [f"{nivel} SOUR {sid}"]
        cita = (evid.get("cita") or "").strip()
        if cita:
            # v4.2 (punto 9): PAGE con CONC para las citas largas (CONT
            # metía un salto de línea dentro de la cita textual y los
            # importadores la partían o perdían).
            out.extend(_linea_valor(nivel + 1, "PAGE", cita))
        return out

    ahora = datetime.now()
    lineas = [
        "0 HEAD",
        "1 SOUR AgenteGenealogia",
        # v4.2 (punto 9): 2 NAME era recomendado y faltaba.
        "2 NAME Agente de investigación genealógica",
        "2 VERS 4.2",
        # v4.2 (punto 9): 1 SUBM + su registro: parte del estándar que
        # varios validadores exigen y faltaba.
        "1 SUBM @SUB1@",
        "1 GEDC",
        "2 VERS 5.5.1",
        "2 FORM LINEAGE-LINKED",
        "1 CHAR UTF-8",
        f"1 DATE {ahora.day} {MESES_GED[ahora.month - 1]} {ahora.year}",
        f"2 TIME {ahora.strftime('%H:%M:%S')}",
    ]

    for c in claves:
        p = por_clave[c]
        nombre = (p.get("nombre") or "").strip()
        apellidos = f"{p.get('apellido_paterno', '')} " \
                    f"{p.get('apellido_materno', '')}".strip()
        dado = nombre
        if apellidos and nombre.endswith(apellidos):
            dado = nombre[: -len(apellidos)].strip()
        xref = xref_de[c]
        lineas += [f"0 {xref} INDI",
                   f"1 NAME {dado} /{apellidos or nombre}/"]
        if p.get("id") and not c.startswith("ref::"):
            # trazabilidad GEDCOM <-> familia_conocida.json
            lineas.append(f"1 REFN {p['id']}")
        if dado:
            lineas.append(f"2 GIVN {dado}")
        if apellidos:
            lineas.append(f"2 SURN {apellidos}")
        sexo = (p.get("sexo") or "").strip().upper()
        if sexo in ("M", "F"):
            lineas.append(f"1 SEX {sexo}")

        for etiqueta, evento in (("BIRT", "nacimiento"), ("DEAT", "defuncion")):
            ev = _limpiar_claves(p.get(evento, {}))
            fecha = fecha_gedcom(ev.get("fecha_aproximada", ""))
            lugar = ", ".join(x for x in (ev.get("municipio", ""),
                                          ev.get("provincia", "")) if x)
            if fecha or lugar:
                lineas.append(f"1 {etiqueta}")
                if fecha:
                    lineas.append(f"2 DATE {fecha}")
                if lugar:
                    lineas.append(f"2 PLAC {lugar}")
                tipos = {"nacimiento"} if etiqueta == "BIRT" else {"defuncion"}
                evid = _evidencia_de(p, tipos)
                if evid:
                    lineas.extend(lineas_cita(evid))

        for idf in hijos_de.get(c, []):
            lineas.append(f"1 FAMC {idf}")
        for idf in fams_de.get(c, []):
            lineas.append(f"1 FAMS {idf}")

        # v4.2 (punto 6): avisos de homonimia como NOTE (nunca fusión
        # ciega de tocayos).
        for aviso in notas_homonimia.get(c, []):
            lineas.extend(_linea_valor(1, "NOTE", f"[AVISO] {aviso}"))

        for h in hall_por_clave.get(c, []):
            texto = (f"[{h.get('confianza', '?')}] {h.get('tipo_evento', '?')}"
                     + (f" {h.get('fecha_valor', '')}" if h.get('fecha_valor') else "")
                     + (f" en {h.get('lugar', '')}" if h.get('lugar') else "")
                     + f" — cita: \"{h.get('cita_literal', '')}\"")
            # v4.2 (punto 9): CONC para el troceo (sin saltos a mitad de
            # frase); CONT solo donde un salto es intencional (Fuente:).
            lineas.extend(_linea_valor(1, "NOTE", texto))
            lineas.extend(_linea_valor(2, "CONT", f"Fuente: {h.get('url_fuente', '')}"))

    for k, fm in familias.items():
        lineas.append(f"0 {id_fam[k]} FAM")
        if fm["husb"] and fm["husb"] in xref_de:
            lineas.append(f"1 HUSB {xref_de[fm['husb']]}")
        if fm["wife"] and fm["wife"] in xref_de:
            lineas.append(f"1 WIFE {xref_de[fm['wife']]}")
        for hijo in fm["chil"]:
            lineas.append(f"1 CHIL {xref_de[hijo]}")
        evid_mat = None
        for miembro in (fm["husb"], fm["wife"]):
            p_info = por_clave.get(miembro, {})
            evid_mat = _evidencia_de(p_info, {"matrimonio"})
            if evid_mat:
                break
        if evid_mat:
            fecha_mat = fecha_gedcom(evid_mat.get("fecha", ""))
            lineas.append("1 MARR")
            if fecha_mat:
                lineas.append(f"2 DATE {fecha_mat}")
            if evid_mat.get("lugar"):
                lineas.append(f"2 PLAC {evid_mat['lugar']}")
            lineas.extend(lineas_cita(evid_mat))

    for url in orden_fuentes:
        sid = fuentes[url]
        titulo = titulo_fuente(url)
        lineas.append(f"0 {sid} SOUR")
        lineas.extend(_linea_valor(1, "TITL", titulo))
        # v4.2 (punto 9): ABBR (título corto) y el ACCESO a NIVEL 1.
        # Antes: `2 URL` colgando de `1 TITL` -> mal anidado; los
        # importadores estrictos lo pierden. Ahora: WWW (extensión
        # de-facto que leen Gramps/Ancestry/FTM) y PUBL (estrictamente
        # 5.5.1, garantiza que el URL sobreviva en cualquier importador).
        lineas.extend(_linea_valor(1, "ABBR", titulo[:60]))
        if (url or "").startswith("http"):
            lineas.extend(_linea_valor(1, "WWW", url))
            lineas.extend(_linea_valor(1, "PUBL", f"Disponible en: {url}"))
        else:
            lineas.extend(_linea_valor(1, "NOTE", f"Fichero local: {url}"))

    # v4.2 (punto 9): registro SUBM referenciado desde HEAD.
    lineas += ["0 @SUB1@ SUBM",
               "1 NAME Investigador del árbol familiar",
               "0 TRLR"]
    with open(BASE_DIR / salida, "w", encoding="utf-8") as f:
        f.write("\n".join(lineas) + "\n")
    extra = " (migradas con ids estables)" if nuevos_ids else ""
    ui.log_tree(f"GEDCOM escrito en {salida} ({len(claves)} personas, "
                f"{len(familias)} familias){extra}")


# ============================== SOLICITUDES ===============================

def _anio(texto: str):
    m = re.search(r"\b(1[5-9]\d{2}|20\d{2})\b", texto or "")
    return int(m.group(1)) if m else None


def generar_solicitudes(path: str = FAMILIA_JSON_PATH) -> None:
    """Genera solicitudes formales de partidas para los registros que casi
    seguro NO están online por la regla de los 100 años (protección de datos).
    Produce solicitudes.json (datos) y solicitudes.md (plantillas de email)."""
    ruta = BASE_DIR / path
    if not ruta.exists():
        raise SystemExit(f"No encuentro '{path}'.")
    with open(ruta, "r", encoding="utf-8") as f:
        datos = _limpiar_claves(json.load(f))

    personas = [_limpiar_claves(p) for p in datos.get("personas", [])]
    anio_limite = datetime.now().year - LIMITE_ANIOS_ONLINE
    firmante = next((p.get("nombre", "").strip() for p in personas
                     if "reconstruyendo" in (p.get("notas") or "").lower()),
                    "") or "(tu nombre completo)"

    solicitudes = []
    for p in personas:
        nombre = p.get("nombre", "").strip()
        if not nombre:
            continue
        nac = _limpiar_claves(p.get("nacimiento", {}))
        anio_nac = _anio(nac.get("fecha_aproximada", ""))
        mun = nac.get("municipio", "").strip()
        prov = normalizar(nac.get("provincia", "").strip())
        if anio_nac is None or anio_nac < anio_limite or not mun:
            continue
        info_archivo = ARCHIVO_POR_PROVINCIA.get(prov, {
            "archivo": f"Archivo Diocesano de la provincia de {nac.get('provincia', 'desconocida')}",
            "contacto": "(localizar contacto en la web de la diócesis)",
            "aviso": "",
        })
        parroquia = PARROQUIAS_CONOCIDAS.get(normalizar(mun))
        solicitudes.append({
            "persona": nombre, "tipo": "bautismo",
            "fecha_aproximada": str(anio_nac),
            "lugar": f"{mun}, {nac.get('provincia', '')}".strip(", "),
            "parroquia": parroquia or "",
            "padre": p.get("padre", "").strip(),
            "madre": p.get("madre", "").strip(),
            "archivo": info_archivo["archivo"],
            "contacto": info_archivo["contacto"],
            "aviso": info_archivo["aviso"],
            "motivo": "descendiente directo",
        })

    with open(BASE_DIR / SALIDA_SOLICITUDES, "w", encoding="utf-8") as f:
        json.dump(solicitudes, f, ensure_ascii=False, indent=2)

    lineas = ["# Solicitudes de partidas sacramentales", "",
              f"Regla aplicada: los archivos diocesanos no suelen publicar "
              f"registros posteriores a {anio_limite} (100 años). Estas partidas "
              f"hay que pedirlas por escrito.", ""]
    for s in solicitudes:
        parroquia_txt = s["parroquia"] or "la del municipio (consultar catálogo del archivo)"
        linea = [
            f"## {s['persona']} — {s['tipo']} ({s['fecha_aproximada']})", "",
            f"**Archivo:** {s['archivo']}  ",
            f"**Contacto:** {s['contacto']}",
        ]
        if s["aviso"]:
            linea.append(f"> ⚠️ {s['aviso']}")
        linea += ["",
                  "```",
                  f"Asunto: Solicitud de partida de {s['tipo']} - {s['persona']}, {s['lugar']}",
                  "",
                  "Estimados/as señores/as:", "",
                  f"Me dirijo a ustedes para solicitar, si fuera posible, la "
                  f"localización o copia de la partida de {s['tipo']} de:", "",
                  f"Nombre: {s['persona']}",
                  f"Fecha aproximada: {s['fecha_aproximada']}",
                  f"Lugar: {s['lugar']}",
                  f"Parroquia probable: {parroquia_txt}",
                  f"Padre: {s['padre'] or 'desconocido'}",
                  f"Madre: {s['madre'] or 'desconocida'}", "",
                  f"Soy {s['motivo']} y estoy realizando una investigación "
                  f"genealógica familiar.", "",
                  "En caso de existir tasas por búsqueda, certificación o "
                  "reproducción, les agradecería que me indicaran el "
                  "procedimiento de pago.", "",
                  "Muchas gracias por su atención.", "",
                  "Atentamente,", firmante, "```", ""]
        lineas.extend(linea)

    with open(BASE_DIR / SALIDA_SOLICITUDES_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lineas))

    ui.log_ok(f"{len(solicitudes)} solicitudes generadas -> "
              f"{SALIDA_SOLICITUDES} y {SALIDA_SOLICITUDES_MD}")
    for s in solicitudes:
        ui.log(f"  - {s['persona']} ({s['tipo']} {s['fecha_aproximada']}, "
               f"{s['lugar']}) -> {s['archivo']}")


# ============================== CANDIDATOS ENSENADA =======================

def generar_candidatos_ensenada() -> None:
    """--ensenada: para cada municipio del árbol busca sus localidades en el
    Catastro y escribe candidatos_ensenada.json (hipótesis, jamás
    confirmaciones)."""
    from agent.frontera import cargar_familia
    import time, random
    from config import DELAY_DESCARGAS

    familia = cargar_familia()
    municipios: dict[str, str] = {}
    for p in familia.get("personas", []):
        for evento in ("nacimiento", "defuncion"):
            ev = _limpiar_claves(p.get(evento, {}))
            if ev.get("municipio"):
                municipios.setdefault(ev["municipio"], ev.get("provincia", ""))
                alias = MUNICIPIOS_EQUIVALENTES.get(normalizar(ev["municipio"]))
                if alias:
                    municipios.setdefault(alias, ev.get("provincia", ""))
    candidatos = []
    for mun in sorted(municipios):
        prov = municipios[mun]
        ui.log_search(f"Catastro de Ensenada: '{mun}'"
                      + (f" ({prov})" if prov else "") + "...")
        encontradas: list[dict] = []
        alias = MUNICIPIOS_EQUIVALENTES.get(normalizar(mun))
        pares_caido = False   # v4.3: caída del portal ≠ "no está en el catastro"
        for forma in _formas_ensenada(mun, alias):
            try:
                locs = _filtrar_por_provincia(
                    buscar_localidades_ensenada(forma), prov, forma, mun)
            except ParesNoDisponible as e:
                ui.log_warn(f"PARES no disponible para '{mun}' "
                            f"({str(e)[:80]}): se deja sin concluir, "
                            f"reintenta con --ensenada más tarde")
                pares_caido = True
                break
            time.sleep(random.uniform(*DELAY_DESCARGAS))
            if locs:
                encontradas = locs
                break
        if pares_caido:
            candidatos.append({
                "municipio": mun, "encontrado": None,
                "nota": "PARES no disponible en el momento de la consulta "
                        "(error del servidor): SIN DATO, reintentar más "
                        "tarde. NO significa que el pueblo falte en el "
                        "catastro.",
            })
            continue
        if not encontradas:
            candidatos.append({
                "municipio": mun, "encontrado": False,
                "nota": "no aparece en el índice de localidades del catastro "
                        "(no interrogado o respuestas no conservadas)",
            })
            continue
        for l in encontradas:
            entrada = {"municipio": mun, "encontrado": True, **l,
                       "hipotesis": "vecinos de 1752 con los apellidos de la "
                                    "familia: confirmar el enlace con las "
                                    "partidas parroquiales (bautismo de un "
                                    "hijo hacia 1770-1790)"}
            if l["loc_id"]:
                try:
                    with ui.Indicador(f"Visor de {l['actual']}"):
                        r = SESSION.get(PARES_CATASTRO,
                                        params={"accion": 4, "opcionV": 3,
                                                "orden": 0, "loc": l["loc_id"]},
                                        timeout=30, verify=False)
                    entrada["paginas_digitalizadas"] = r.text.count("ImageServlet")
                except Exception:
                    pass
            candidatos.append(entrada)
    (BASE_DIR / CANDIDATOS_ENSENADA).write_text(
        json.dumps(candidatos, ensure_ascii=False, indent=2), encoding="utf-8")
    ui.log_ok(f"{len(candidatos)} entradas -> {CANDIDATOS_ENSENADA} "
              f"(hipótesis de trabajo, NO confirmaciones)")


# ==================== OCR LOCAL — DOCUMENTOS PROPIOS (v10.0) ================

def transcribir_imagen(ruta: Path) -> str:
    """Transcribe una imagen de un documento propio con el OCR 100% LOCAL
    (v10.0): olmOCR-2 vía llama-server si OCR_BACKEND=llamacpp, RapidOCR/
    PaddleOCR en caso contrario. SIN modelo de visión en la nube.

    Devuelve "" si no hay motor local disponible (llama-server caído o
    motor no instalado): el llamador avisa y el documento queda PENDIENTE.
    Nunca lanza excepción por falta de motor (los errores de lectura del
    fichero sí se propagan, como siempre).
    """
    import scrapers.web as _web
    datos = ruta.read_bytes()
    if len(datos) > 10_000_000:
        raise ValueError(f"{ruta.name} pesa {len(datos)/1e6:.1f} MB (máx. 10)")
    # Se normaliza a PNG: RapidOCR abre la imagen con PIL (cualquier
    # formato) y el cliente llama.cpp manda data URIs PNG.
    try:
        from PIL import Image
        import io as _io
        img = Image.open(_io.BytesIO(datos)).convert("RGB")
        buf = _io.BytesIO()
        img.save(buf, format="PNG")
        png = buf.getvalue()
    except Exception as e:
        ui.log_warn(f"{ruta.name}: no es una imagen legible ({str(e)[:60]})")
        return ""
    if _web.OCR_BACKEND == "llamacpp":
        # _ocr_llamacpp ya gestiona sola el "servidor caído" (aviso único y
        # ("", 0.0) inmediato en las llamadas siguientes de este proceso).
        texto, _conf = _web._ocr_llamacpp([png])
        return texto
    texto, _conf = _web._ocr_local([png])
    return texto


def persona_desde_nombre_archivo(nombre_archivo: str, familia: dict) -> str:
    """'1936_isidro_bautismo' -> 'Isidro Merillas Panero': empareja tokens
    del nombre del fichero con las personas conocidas."""
    tokens = {t for t in re.split(r"[\s_\-.,()]+", normalizar(nombre_archivo))
              if len(t) > 2 and not t.isdigit()}
    mejor, puntos = "", 0
    for p in familia.get("personas", []):
        nombre = p.get("nombre", "")
        nt = {t for t in normalizar(nombre).split() if len(t) > 2}
        if not nt:
            continue
        inter = tokens & nt
        puntuacion = len(inter) * 100 - len(nt)
        if inter and puntuacion > puntos:
            mejor, puntos = nombre, puntuacion
    return mejor


def importar_documentos_propios() -> int:
    """--importar-propios: transcribe las imágenes de documentos_propios/ con
    el OCR 100% LOCAL (v10.0) y las añade al corpus como fuente primaria
    (origen 'propio'). SIN modelo de visión en la nube: si no hay motor
    local disponible, cada imagen queda PENDIENTE con un aviso claro (sin
    crash) y puede reintentarse en cuanto se instale/arranque el motor."""
    from agent.fase1 import cargar_corpus, guardar_corpus
    from config import HASHES_CORPUS, OCR_BACKEND
    from agent.frontera import cargar_familia

    carpeta = BASE_DIR / DIR_DOCUMENTOS_PROPIOS
    if not carpeta.exists():
        carpeta.mkdir(parents=True)
        ui.log(f"Carpeta creada: {carpeta}/")
        ui.log("Mete ahí las fotos de certificados (jpg/png/webp), p. ej. "
               "'1936_isidro_bautismo.jpg', y vuelve a ejecutar "
               "--importar-propios.")
        return 0
    imagenes = sorted(f for f in carpeta.iterdir()
                      if f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"))
    if not imagenes:
        ui.log(f"No hay imágenes en {carpeta}/.")
        return 0
    familia = cargar_familia()
    corpus = cargar_corpus()
    HASHES_CORPUS.clear()
    for f in corpus:
        hash_val = f.get("hash")
        if not hash_val:
            url = f.get("url") or ""
            texto = f.get("texto_limpio") or ""
            hash_val = sha256_corto(url + texto[:1000])
        HASHES_CORPUS.add(hash_val)
    nuevos = 0
    pendientes = 0
    for img in imagenes:
        try:
            texto = transcribir_imagen(img)
        except PresupuestoExcedido:
            raise
        except Exception as e:
            ui.log_warn(f"{img.name}: {str(e)[:100]}")
            continue
        if not texto or len(texto) < 60:
            # v10.0: sin motor local de OCR el documento queda PENDIENTE
            # (aviso claro, sin crash, sin nube): se reintentará cuando el
            # motor esté disponible.
            pendientes += 1
            ui.log_warn(f"{img.name}: sin transcripción local (¿motor OCR "
                        f"instalado? ¿llama-server arrancado con "
                        f"OCR_BACKEND=llamacpp?). OCR 100% local: no se "
                        f"escala a nube — el documento queda PENDIENTE "
                        f"para un reintento.")
            continue
        persona = persona_desde_nombre_archivo(img.stem, familia)
        url = f"{DIR_DOCUMENTOS_PROPIOS}/{img.name}"
        h = sha256_corto(url + texto[:1000])
        if h in HASHES_CORPUS:
            continue
        HASHES_CORPUS.add(h)
        corpus.append({
            "hash": h, "persona": persona, "origen": "propio",
            "apellido": "", "municipio": "", "query": "--importar-propios",
            "url": url, "titulo": img.name,
            "texto_limpio": texto[:6000],
            "fecha_descarga": datetime.now().isoformat(timespec="seconds"),
        })
        nuevos += 1
        ui.log_ok(f"{img.name} -> {persona or 'sin asignar'} "
                  f"({len(texto)} caracteres)")
    guardar_corpus(corpus)
    if nuevos:
        docs = familia.setdefault("documentos_propios", [])
        for img in imagenes:
            entrada = {"archivo": f"{DIR_DOCUMENTOS_PROPIOS}/{img.name}",
                       "transcrito_con": f"OCR local ({OCR_BACKEND})",
                       "fecha": datetime.now().isoformat(timespec="seconds")}
            if entrada not in docs:
                docs.append(entrada)
        (BASE_DIR / FAMILIA_JSON_PATH).write_text(
            json.dumps(familia, ensure_ascii=False, indent=2),
            encoding="utf-8")
        ui.log(f"    constancia de los documentos -> {FAMILIA_JSON_PATH}")
    if pendientes:
        ui.log_warn(f"{pendientes} documento(s) propio(s) PENDIENTES sin "
                    f"transcribir (sin motor OCR local disponible): no han "
                    f"entrado al corpus. Reintenta --importar-propios "
                    f"cuando el motor esté disponible.")
    ui.log_ok(f"{nuevos} documentos propios transcritos con OCR local")
    return nuevos


# ============================== DIAGNÓSTICO ===============================

def diagnostico(test_llm: bool = False) -> int:
    """Prueba pequeña antes de lanzar la investigación completa: comprueba
    claves, base de datos, familia, modelos reales en OpenRouter y el
    conversor de fechas. Devuelve el número de problemas."""
    from config import get_db, TAVILY_API_KEY, OPENROUTER_API_KEY
    from agent.fase1 import generar_objetivos_busqueda
    errores = 0

    ui.cabecera("1. Claves de API")
    for nombre, valor in (("TAVILY_API_KEY", TAVILY_API_KEY),
                          ("OPENROUTER_API_KEY", OPENROUTER_API_KEY)):
        if valor and len(valor) > 10:
            ui.log_ok(f"{nombre} presente")
        else:
            ui.log_error(f"{nombre} ausente o vacía")
            errores += 1

    ui.cabecera("2. Base de datos local")
    try:
        from config import (ya_buscado, marcar_buscado, ya_vista, marcar_vista,
                            DB_PATH)
        conn = get_db()
        marcar_buscado(conn, "__diagnostico__")
        assert ya_buscado(conn, "__diagnostico__")
        marcar_vista(conn, "https://ejemplo.invalid/x")
        assert ya_vista(conn, "https://ejemplo.invalid/x")
        conn.execute("DELETE FROM busquedas_hechas WHERE clave='__diagnostico__'")
        conn.execute("DELETE FROM urls_vistas WHERE url='https://ejemplo.invalid/x'")
        conn.commit()
        conn.close()
        ui.log_ok(f"{DB_PATH} se crea y admite lectura/escritura")
    except Exception as e:
        ui.log_error(f"base de datos: {str(e)[:100]}")
        errores += 1

    ui.cabecera("3. familia_conocida.json y objetivos")
    try:
        objetivos = generar_objetivos_busqueda()
        n_queries = sum(len(o["queries"]) for o in objetivos)
        ui.log_ok(f"{len(objetivos)} objetivos, {n_queries} consultas semilla")
    except SystemExit as e:
        ui.log_error(str(e))
        errores += 1

    ui.cabecera("4. Existencia de los modelos en OpenRouter")
    try:
        r = SESSION.get("https://openrouter.ai/api/v1/models", timeout=30)
        r.raise_for_status()
        catalogo = r.json()["data"]
        ids = {m["id"] for m in catalogo}
        # v10.4.1 (tarea E): la descarga del catálogo YA trae los precios
        # vivos (pricing.prompt / pricing.completion). Aprovecharla para
        # comprobar que la tabla de config.py no INFRAVALORA el gasto: si el
        # precio del proveedor es más alto que el de la tabla, el estimador y
        # --presupuesto-max se quedan cortos (que es exactamente lo que pasó:
        # $0.1344 contados frente a $0.38 cobrados). Solo se avisa en la
        # dirección peligrosa: si la tabla es MÁS ALTA que el precio vivo
        # (p. ej. porque se apunta la tarifa PUNTA de un modelo con tarifa
        # horaria), el estimador se queda largo y eso es lo que queremos.
        precios_vivos = {m.get("id"): (m.get("pricing") or {})
                         for m in catalogo}
        for etiqueta, modelo in (("fase 1", MODELO_FASE1), ("fase 2", MODELO_FASE2)):
            if modelo in ids:
                ui.log_ok(f"{modelo} ({etiqueta}) existe")
            else:
                errores += 1
                ui.log_error(f"{modelo} ({etiqueta}) NO existe en OpenRouter")
                familia = modelo.split("/")[0]
                similares = sorted(i for i in ids if familia in i)[:8]
                if similares:
                    ui.log(f"       alternativas: {', '.join(similares)}")
        for etiqueta, modelo in (("fase 1", MODELO_FASE1), ("fase 2", MODELO_FASE2)):
            tabla = PRECIO_MILLON_TOKENS.get(modelo)
            vivo = precios_vivos.get(modelo) or {}
            if not tabla:
                ui.log_warn(f"{modelo} ({etiqueta}) no está en "
                            f"PRECIO_MILLON_TOKENS: se usaría el precio por "
                            f"defecto (${PRECIO_POR_DEFECTO['entrada']}/"
                            f"${PRECIO_POR_DEFECTO['salida']} por millón)")
                continue
            try:
                vivo_in = float(vivo.get("prompt")) * 1_000_000
                vivo_out = float(vivo.get("completion")) * 1_000_000
            except (TypeError, ValueError):
                ui.log_warn(f"{modelo} ({etiqueta}): OpenRouter no devolvió "
                            f"precios legibles para comprobar la tabla")
                continue
            infras = []
            if tabla["entrada"] < vivo_in * 0.8:
                infras.append(f"entrada ${tabla['entrada']:.4f} < "
                              f"${vivo_in:.4f} real")
            if tabla["salida"] < vivo_out * 0.8:
                infras.append(f"salida ${tabla['salida']:.4f} < "
                              f"${vivo_out:.4f} real")
            if infras:
                errores += 1
                ui.log_error(
                    f"{modelo} ({etiqueta}): la tabla de config.py "
                    f"INFRAVALORA el gasto ({'; '.join(infras)} por millón de "
                    f"tokens). Actualiza PRECIO_MILLON_TOKENS o el estimador "
                    f"y --presupuesto-max mentirán a la baja.")
            else:
                ui.log_ok(f"{modelo} ({etiqueta}): precio de config "
                          f"${tabla['entrada']:.4f}/${tabla['salida']:.4f} >= "
                          f"vivo ${vivo_in:.4f}/${vivo_out:.4f} (no infravalora)")
    except Exception as e:
        ui.log_warn(f"no se pudo consultar la lista de modelos: {str(e)[:80]}")

    ui.cabecera("5. Conversor de fechas GEDCOM")
    casos = {
        "1974": "ABT 1974",
        "1936-05-02": "2 MAY 1936",
        "2 de mayo de 1936": "2 MAY 1936",
        "mayo de 1936": "MAY 1936",
        "2 MAY 1936": "2 MAY 1936",
    }
    for entrada, esperado in casos.items():
        obtenido = fecha_gedcom(entrada)
        if obtenido == esperado:
            ui.log_ok(f"{entrada!r} -> {obtenido!r}")
        else:
            ui.log_error(f"{entrada!r} -> {obtenido!r} (esperado {esperado!r})")
            errores += 1

    ui.cabecera("6. Filtro de dominios basura (DOMINIOS_IGNORADOS)")
    from config import es_dominio_ignorado
    casos_dom = {
        "https://www.facebook.com/pepe": True,
        "https://tripadvisor.es/restaurantes": True,
        "https://pubmed.ncbi.nlm.nih.gov/123": True,
        "https://pares.cultura.gob.es/catastro": False,
        "https://internet.ahdv-geah.org/": False,
        "https://hispagen.es/": False,
    }
    for url, esperado in casos_dom.items():
        obtenido = es_dominio_ignorado(url)
        if obtenido == esperado:
            ui.log_ok(f"{url} -> {'ignorada' if obtenido else 'OK'}")
        else:
            ui.log_error(f"{url} -> {'ignorada' if obtenido else 'OK'} "
                         f"(esperado {'ignorada' if esperado else 'OK'})")
            errores += 1

    # v4.2/v10.0 — Comprobaciones de las dependencias de OCR y headless.
    # Cada check imprime OK o FALTA con instrucciones de instalación.
    # v10.0: OCR 100% LOCAL — sin modelo de visión en la nube, el fallo de
    # cualquier motor es explícito y no escala a nada.
    ui.cabecera("7. OCR 100% local (v10.0) — dependencias")

    # 7a. Motor de OCR local instalado (RapidOCR por defecto)
    from config import OCR_BACKEND
    if OCR_BACKEND == "off":
        ui.log_warn("OCR_BACKEND=off: OCR local desactivado por config "
                    "(los PDFs escaneados quedarán SIN TEXTO: v10.0 no "
                    "escala a la nube).")
    elif OCR_BACKEND == "paddleocr":
        ui.log_warn("OCR_BACKEND=paddleocr (LEGACY): requiere "
                    "paddleocr+paddlepaddle, que NO instala en Windows con "
                    "Python moderno. Se recomienda RapidOCR: pip install "
                    "rapidocr_onnxruntime + OCR_BACKEND=rapidocr en .env")
        try:
            import paddleocr
            ui.log_ok(f"paddleocr {getattr(paddleocr, '__version__', '?')} "
                      f"instalado (legacy)")
        except ImportError:
            ui.log_warn("paddleocr NO instalado: OCR local desactivado "
                        "(OCR 100% local: el fallo no escala a nube).")
    elif OCR_BACKEND == "llamacpp":
        # v9.0/v10.1: el motor es EXTERNO (llama-server con el modelo de la
        # familia activa): aquí no hay nada que importar; el chequeo real
        # del servidor (y de la familia) es el punto 7f.
        from config import OCR_LLAMACPP_FAMILIA
        ui.log_ok(f"OCR_BACKEND=llamacpp: OCR local vía llama.cpp "
                  f"(familia {OCR_LLAMACPP_FAMILIA}; motor externo, se "
                  f"comprueba abajo en el punto 7f)")
    else:
        try:
            try:
                import rapidocr_onnxruntime as r
                version_ocr = getattr(r, "__version__", "?")
            except ImportError:
                import rapidocr as r
                version_ocr = getattr(r, "__version__", "?")
            ui.log_ok(f"rapidocr {version_ocr} instalado (ONNX, "
                      f"instalable en Windows/macOS/Linux sin compilar)")
        except ImportError:
            ui.log_warn("rapidocr NO instalado (opcional). Los PDFs "
                        "escaneados quedarán SIN TEXTO (OCR 100% local: "
                        "el fallo no escala a nube). Instala con: "
                        "pip install rapidocr_onnxruntime")

    # 7a-bis. v10.0/v10.1 — manuscritos: SOLO OCR local de llama.cpp o
    # fallo explícito (la familia que esté configurada).
    from config import DOMINIOS_MANUSCRITOS
    ui.log_ok(f"Manuscritos (v10.0): los PDFs de {len(DOMINIOS_MANUSCRITOS)} "
              f"dominios (PARES, SIGA, ADDO...) SOLO se transcriben con el "
              f"OCR local de llama.cpp (OCR_BACKEND=llamacpp, familia "
              f"glm-ocr/hunyuan/olmocr2) o fallan de forma "
              f"explícita; nunca se mandan a un modelo de la nube")

    # 7b. Inicialización real del OCR local (sin gastar tokens, solo
    # carga de modelos en memoria).
    try:
        import scrapers.web as _web
        if OCR_BACKEND not in ("off", "llamacpp"):
            motor = _web._init_ocr_local()
            if motor is not None:
                ui.log_ok(f"OCR local inicializa OK (motor="
                          f"{_web._OCR_BACKEND_ACTIVO or '?'})")
            else:
                # No es error fatal, pero v10.0 NO tiene fallback en la nube:
                # hay que decirlo con todas las letras.
                ui.log_warn("OCR local no inicializa. Los PDFs escaneados "
                            "quedarán SIN TEXTO (OCR 100% local: el fallo "
                            "NO escala a nube). Revisa los warnings arriba.")
    except Exception as e:
        ui.log_warn(f"no se pudo inicializar el OCR local: {str(e)[:100]} "
                    f"(v10.0: sin OCR local, los PDFs escaneados quedan "
                    f"sin texto; no hay escalada a la nube)")

    # 7c. pdf2image + poppler-utils (necesario para convertir PDF → PNG)
    try:
        import pdf2image
        ui.log_ok(f"pdf2image {getattr(pdf2image, '__version__', '?')} instalado")
    except ImportError:
        ui.log_warn("pdf2image NO instalado (opcional). Sin él no se puede "
                    "convertir PDF a imágenes para OCR. Instala con: "
                    "pip install pdf2image")
    # poppler-utils en PATH (lo usa pdf2image internamente)
    try:
        import subprocess
        r = subprocess.run(["pdftoppm", "-v"], capture_output=True,
                           timeout=5)
        # pdftoppm -v devuelve el código en stderr en versiones viejas.
        salida = (r.stderr or r.stdout or b"").decode("utf-8",
                                                       errors="replace")
        if "poppler" in salida.lower() or r.returncode == 0 or r.returncode == 99:
            ui.log_ok(f"poppler-utils detectado en PATH "
                      f"({salida.strip().splitlines()[0][:60] if salida.strip() else 'OK'})")
        else:
            ui.log_warn(f"pdftoppm devolvió código {r.returncode}. "
                        f"poppler-utils puede no estar instalado. Instala: "
                        f"apt install poppler-utils  (Debian/Ubuntu) · "
                        f"brew install poppler  (macOS)")
    except FileNotFoundError:
        ui.log_warn("poppler-utils NO está en PATH. Sin él, pdf2image no "
                    "puede convertir PDF a imágenes. Instala: "
                    "apt install poppler-utils  (Debian/Ubuntu) · "
                    "brew install poppler  (macOS)")
    except Exception as e:
        ui.log_warn(f"no se pudo verificar poppler-utils: {str(e)[:80]}")

    # 7d. Tabla ocr_cache en SQLite
    try:
        from config import get_db
        conn = get_db()
        tablas = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        if "ocr_cache" in tablas:
            ui.log_ok("tabla ocr_cache presente en SQLite")
        else:
            ui.log_error("tabla ocr_cache AUSENTE (la BD está corrupta o "
                         "es de una versión anterior). Borra cache_agente.db "
                         "y se recreará al arrancar.")
            errores += 1
        conn.close()
    except Exception as e:
        ui.log_error(f"no se pudo verificar tabla ocr_cache: {str(e)[:100]}")
        errores += 1

    # 7e. Playwright + Chromium descargado
    try:
        import playwright
        ui.log_ok(f"playwright {getattr(playwright, '__version__', '?')} instalado")
        # Verificar que chromium está descargado
        try:
            from playwright.sync_api import sync_playwright
            pw = sync_playwright().start()
            try:
                # launch(headless=True) falla SI NO se ha bajado chromium.
                browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
                browser.close()
                ui.log_ok("chromium descargado y arranca OK (headless)")
            finally:
                pw.stop()
        except Exception as e:
            ui.log_warn(f"playwright está pero chromium NO arranca: "
                        f"{str(e)[:80]}. Ejecuta: playwright install chromium")
    except ImportError:
        ui.log_warn("playwright NO instalado (opcional). Sin él, las "
                    "descargas que devuelvan 403/503 no se reintentarán "
                    "con navegador headless. Instala con: "
                    "pip install playwright && playwright install chromium")

    # 7f. v9.0/v10.1 — llama.cpp + OCR local multi-modelo: ¿está el
    # servidor arrancado? GET corto a /health y /v1/models de
    # OCR_LLAMACPP_URL (no gasta tokens: es el servidor OCR LOCAL). Se
    # muestra la FAMILIA configurada junto al estado; si no está, se dan
    # las instrucciones exactas para levantarlo (compilación y comandos
    # por familia en el README, sección "OCR local con llama.cpp +
    # Vulkan").
    if OCR_BACKEND == "llamacpp":
        ui.cabecera("7f. llama.cpp + OCR local multi-modelo (v9.0/v10.1) — "
                    "servidor OCR local")
        from config import OCR_LLAMACPP_FAMILIA, OCR_LLAMACPP_URL
        try:
            from scrapers.web import llamacpp_disponible
            ok, detalle = llamacpp_disponible()
            if ok:
                ui.log_ok(f"llama-server responde en {OCR_LLAMACPP_URL} "
                          f"(modelo servido: {detalle}; "
                          f"familia configurada: {OCR_LLAMACPP_FAMILIA})")
            else:
                # No es error fatal, pero v10.0 NO tiene fallback en la
                # nube: sin servidor no hay OCR de PDFs escaneados.
                ui.log_warn(
                    f"llama-server NO responde en {OCR_LLAMACPP_URL} "
                    f"({detalle}). v10.0: OCR 100% local — SIN servidor, "
                    f"los PDFs escaneados quedarán SIN TEXTO (no se escala "
                    f"a la nube). Para usar el OCR local con la familia "
                    f"'{OCR_LLAMACPP_FAMILIA}' en tu GPU AMD (vulkan, sin "
                    f"ROCm), tres servidores posibles (los comandos con "
                    f"-hf descargan el modelo solos, 1-2 GB a disco):\n"
                    f"       1) GLM-OCR (0.9B, ~2.5 GB VRAM; recomendado "
                    f"para español y manuscrito 1800-1930):\n"
                    f"            ./build/bin/llama-server -hf "
                    f"ggml-org/GLM-OCR-GGUF -ngl 99 --host 0.0.0.0 "
                    f"--port 8080\n"
                    f"       2) HunyuanOCR-1.5 (alternativa SOTA, 100+ "
                    f"idiomas):\n"
                    f"            ./build/bin/llama-server -hf "
                    f"ggml-org/HunyuanOCR-GGUF -ngl 99 --host 0.0.0.0 "
                    f"--port 8080\n"
                    f"       3) olmOCR-2-7B (especialista en manuscrito "
                    f"anterior a 1800; GGUF+mmproj a mano, ver README):\n"
                    f"            ./build/bin/llama-server -m ./models/"
                    f"olmOCR-2-7B-1025-Q4_K_M.gguf --mmproj ./models/"
                    f"mmproj-olmOCR-2-7B-1025-F16.gguf -ngl 99 -c 8192 "
                    f"--host 0.0.0.0 --port 8080\n"
                    f"       (compilación de llama.cpp con Vulkan y demás "
                    f"detalles en el README, sección 'OCR local con "
                    f"llama.cpp + Vulkan')")
        except Exception as e:
            ui.log_warn(f"no se pudo comprobar llama-server: {str(e)[:80]}")

    if test_llm:
        ui.cabecera("8. Ping a los LLMs (consume unos pocos tokens)")
        for etiqueta, modelo in (("fase 1", MODELO_FASE1), ("fase 2", MODELO_FASE2)):
            try:
                resp = chat_json(modelo, "Eres un verificador de conectividad.",
                                 'Responde únicamente: {"ok": true}', intentos=2)
                ui.log_ok(f"{modelo} responde: {resp}")
            except Exception as e:
                ui.log_error(f"{modelo}: {str(e)[:100]}")
                errores += 1

    ui.log_stats(f"=== Diagnóstico terminado: "
                 f"{'TODO CORRECTO' if errores == 0 else f'{errores} problema(s)'} ===")
    return errores
