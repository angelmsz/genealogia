"""
utils/personas.py — Identidad de personas: IDs estables y emparejamiento
difuso de nombres (v4.2).

Problema que resuelve (punto 6 del informe de fallos):
  - El programa identificaba a la gente SOLO por su nombre escrito:
    * Abuelo y nieto con el mismo nombre se fundían en una sola ficha.
    * "Isidro Merillas" y "Isidro Merillas Panero" no se reconocían como
      la misma persona y el hallazgo se descartaba EN SILENCIO.

Solución:
  - asignar_ids(): da a cada persona de familia_conocida.json un
    identificador estable ("P0001", "P0002"...) independiente del nombre,
    que se persiste en el propio fichero y NUNCA se reutiliza. Los xrefs
    del GEDCOM usan estos ids, así que abuelo y nieto tocayos ya no se
    funden.
  - emparejar_persona(): empareja un nombre suelto (de un hallazgo o de
    una entrada de la frontera) con la lista de personas conocidas:
      1. coincidencia exacta (normalizada, sin tildes);
      2. coincidencia por subconjunto de tokens ("Isidro Merillas" es
         subconjunto de "Isidro Merillas Panero");
      3. desambiguación por año de nacimiento cuando hay varios
         candidatos (caso abuelo/nieto): se prefiere el candidato cuya
         fecha encaje con la del hallazgo (± TOLERANCIA_ANIOS); si sigue
         habiendo ambigüedad se devuelve None y se AVISA por log (nunca
         se descarta en silencio).

Sin I/O de ficheros: funciones puras para poder testearlas aisladas.
"""

from __future__ import annotations

import re

from config import STOPWORDS, normalizar

# Años de holgura al desambiguar por fecha de nacimiento.
TOLERANCIA_ANIOS = 6

RE_ANIO = re.compile(r"\b(1[4-9]\d{2}|20\d{2})\b")


# ============================== TOKENS ======================================

def tokens_persona(nombre: str) -> set[str]:
    """Tokens significativos de un nombre: minúsculas, sin tildes, sin
    stopwords ('Isidro Merillas Panero' -> {'isidro','merillas','panero'})."""
    return {t for t in normalizar(nombre or "").split()
            if len(t) > 2 and t not in STOPWORDS}


def anio_persona(p: dict) -> int | None:
    """Año de nacimiento de una ficha (de la memoria familiar), o None."""
    nac = p.get("nacimiento") or {}
    m = RE_ANIO.search(str(nac.get("fecha_aproximada", "")))
    return int(m.group(1)) if m else None


# ============================== ASIGNACIÓN DE IDs ===========================

def _max_num_ids(ids: set[str]) -> int:
    """Mayor número usado en ids tipo 'P0007' (0 si ninguno)."""
    maximo = 0
    for pid in ids:
        m = re.fullmatch(r"P(\d+)", (pid or "").strip())
        if m:
            maximo = max(maximo, int(m.group(1)))
    return maximo


def asignar_ids(familia: dict) -> int:
    """Añade un id estable ('P0001'...) a cada persona sin id. No reutiliza
    NUNCA un id ya presente, aunque la persona se haya borrado: los ids de
    personas eliminadas quedan jubilados para siempre (así un hallazgo viejo
    nunca acabará apuntando a otra persona).

    Muta `familia["personas"]` in situ y devuelve cuántos ids nuevos asignó.
    El llamador es responsable de persistir el fichero después.
    """
    personas = familia.get("personas", [])
    usados = {p.get("id") for p in personas if p.get("id")}
    contador = _max_num_ids(usados)
    nuevos = 0
    for p in personas:
        if not p.get("id"):
            contador += 1
            pid = f"P{contador:04d}"
            while pid in usados:  # paranoia: colisión imposible, pero barata
                contador += 1
                pid = f"P{contador:04d}"
            p["id"] = pid
            usados.add(pid)
            nuevos += 1
    return nuevos


# ============================== EMPAREJAMIENTO ==============================

def _desambiguar_por_anio(candidatos: list[dict], anio: int | None,
                          nombre: str, contexto: str) -> dict | None:
    """De una lista de candidatos con el mismo nombre, elige el único cuya
    fecha de nacimiento encaja con `anio` (± tolerancia). Si sigue habiendo
    0 o >=2 opciones, avisa y devuelve None (NUNCA se funde a lo ciego)."""
    if anio is None:
        ui_aviso(f"{contexto}: '{nombre}' aparece como {len(candidatos)} "
                 f"personas distintas con el mismo nombre (p. ej. abuelo y "
                 f"nieto) y el hallazgo no trae fecha para decidir: NO se "
                 f"asigna, queda como pista por revisar a mano.")
        return None
    con_anio = [(p, anio_persona(p)) for p in candidatos]
    encajan = [p for p, a in con_anio
               if a is not None and abs(a - anio) <= TOLERANCIA_ANIOS]
    if len(encajan) == 1:
        return encajan[0]
    ui_aviso(f"{contexto}: '{nombre}' casa con {len(candidatos)} personas "
             f"y el año {anio} no desambigua (encajan {len(encajan)}): "
             f"NO se asigna, queda como pista por revisar a mano.")
    return None


def emparejar_persona(nombre: str, personas: list[dict],
                      anio: int | None = None,
                      avisar: bool = True,
                      contexto: str = "identidad") -> dict | None:
    """Empareja `nombre` con una ficha de `personas`.

    1. Exacto (normalizado). Si hay VARIAS con el mismo nombre exacto
       (abuelo/nieto) se intenta desambiguar por `anio`; sin éxito -> None.
    2. Subconjunto de tokens: 'Isidro Merillas' ⊆ 'Isidro Merillas Panero'.
       Varios candidatos -> desambiguación por año igual que arriba.
    3. Nada -> None (y aviso si `avisar`).

    Nunca lanza; nunca fusiona a ciegas. Los avisos van una sola línea.
    """
    clave = normalizar(nombre or "")
    if not clave:
        return None

    exactos = [p for p in personas
               if normalizar(p.get("nombre", "")) == clave]
    if exactos:
        if len(exactos) == 1:
            return exactos[0]
        return _desambiguar_por_anio(exactos, anio, clave, contexto)

    tk = tokens_persona(nombre)
    if tk:
        candidatos = [p for p in personas
                      if tk <= tokens_persona(p.get("nombre", ""))]
        if len(candidatos) == 1:
            return candidatos[0]
        if len(candidatos) > 1:
            return _desambiguar_por_anio(candidatos, anio, clave, contexto)

    if avisar:
        ui_aviso(f"{contexto}: '{nombre}' no casa con ninguna persona "
                 f"conocida (el hallazgo NO se descarta: se guarda como "
                 f"pista/candidato).")
    return None


# ============================== AVISOS ======================================

def ui_aviso(msg: str) -> None:
    """Import perezoso para evitar dependencia circular con utils.ui."""
    from utils import ui
    ui.log_warn(msg)
