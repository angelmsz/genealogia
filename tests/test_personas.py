"""
tests/test_personas.py — Identidad de personas: ids estables y matching
difuso (punto 6 del informe de fallos).

Cubre el módulo utils/personas.py:
  - asignar_ids: ids estables, idempotente, NUNCA reutiliza un id aunque
    la persona se haya borrado (un hallazgo viejo nunca apuntará a otra
    persona).
  - emparejar_persona: exacto, subconjunto de tokens ('Isidro Merillas'
    casa con 'Isidro Merillas Panero'), desambiguación de tocayos por
    año (abuelo/nieto), y NUNCA fusiona a ciegas cuando no se puede
    desambiguar.
  - asociar_persona_ids (agent/fase2.py): cada hallazgo queda asociado a
    su ficha por id estable.
"""

from __future__ import annotations

from utils.personas import (anio_persona, asignar_ids, emparejar_persona,
                            tokens_persona)


def _familia_tocayos() -> dict:
    """Abuelo y nieto con el MISMO nombre (caso típico español)."""
    return {"personas": [
        {"nombre": "Isidro Merillas Panero",
         "id": "P0001",
         "nacimiento": {"fecha_aproximada": "1870"}},
        {"nombre": "Isidro Merillas Panero",
         "id": "P0002",
         "nacimiento": {"fecha_aproximada": "1785"}},
        {"nombre": "Obdulia Pelaz Merino",
         "id": "P0003",
         "nacimiento": {"fecha_aproximada": "1875"}},
    ]}


# ------------------------------ asignar_ids --------------------------------

def test_asignar_ids_asigna_y_es_idempotente():
    fam = {"personas": [{"nombre": "A"}, {"nombre": "B"}]}
    assert asignar_ids(fam) == 2
    assert {p["id"] for p in fam["personas"]} == {"P0001", "P0002"}
    # segunda pasada: nada nuevo, mismos ids
    assert asignar_ids(fam) == 0
    assert fam["personas"][0]["id"] == "P0001"


def test_asignar_ids_no_reutiliza_ids_de_borrados():
    """Si una persona se borra, su id queda jubilado para siempre."""
    fam = {"personas": [{"nombre": "A", "id": "P0001"},
                        {"nombre": "B", "id": "P0003"}]}
    asignar_ids(fam)
    ids = [p["id"] for p in fam["personas"]]
    assert "P0002" not in ids          # P0002 murió con su persona
    assert ids == ["P0001", "P0003"]   # los existentes no se tocan
    # una persona NUEVA no puede heredar P0002 (ni P0001/P0003)
    fam["personas"].append({"nombre": "C"})
    asignar_ids(fam)
    assert fam["personas"][2]["id"] == "P0004"


# --------------------------- emparejar_persona ------------------------------

def test_emparejar_exacto():
    fam = _familia_tocayos()
    p = emparejar_persona("Obdulia Pelaz Merino", fam["personas"],
                          avisar=False)
    assert p is not None and p["id"] == "P0003"


def test_emparejar_subconjunto_de_tokens():
    """'Isidro Merillas' debe casa con la ficha 'Isidro Merillas Panero':
    antes este hallazgo se descartaba EN SILENCIO por no coincidir el
    nombre literal."""
    fam = {"personas": [{"nombre": "Isidro Merillas Panero",
                         "id": "P0001"}]}
    p = emparejar_persona("Isidro Merillas", fam["personas"], avisar=False)
    assert p is not None and p["id"] == "P0001"


def test_emparejar_tocayos_desambigua_por_anio():
    """Abuelo (1785) y nieto (1870) tocayos: el año del hallazgo decide."""
    fam = _familia_tocayos()
    nieto = emparejar_persona("Isidro Merillas Panero", fam["personas"],
                              anio=1870, avisar=False)
    assert nieto is not None and nieto["id"] == "P0001"
    abuelo = emparejar_persona("Isidro Merillas Panero", fam["personas"],
                               anio=1785, avisar=False)
    assert abuelo is not None and abuelo["id"] == "P0002"


def test_emparejar_tocayos_sin_anio_no_fusiona():
    """Sin año que desambigüe, NUNCA se elige a ciegas (None)."""
    fam = _familia_tocayos()
    p = emparejar_persona("Isidro Merillas Panero", fam["personas"],
                          avisar=False)
    assert p is None


def test_emparejar_sin_coincidencia_none():
    fam = _familia_tocayos()
    assert emparejar_persona("Fulano Detal", fam["personas"],
                             avisar=False) is None
    assert emparejar_persona("", fam["personas"], avisar=False) is None


def test_tokens_y_anio():
    assert tokens_persona("Isidro Merillas Panero") == {
        "isidro", "merillas", "panero"}
    assert anio_persona({"nacimiento": {"fecha_aproximada": "hacia 1870"}}) == 1870
    assert anio_persona({"nacimiento": {}}) is None


# ------------------------- asociar_persona_ids (fase2) ---------------------

def test_asociar_persona_ids_fuzzy():
    from agent.fase2 import asociar_persona_ids
    fam = _familia_tocayos()
    hallazgos = [
        # nombre corto de la ficha P0001 + año que desambigua del abuelo
        {"persona": "Isidro Merillas", "tipo_evento": "bautismo",
         "fecha_valor": "1870-05-02"},
        # exacto
        {"persona": "Obdulia Pelaz Merino", "tipo_evento": "nacimiento",
         "fecha_valor": "1875"},
        # no casa con nadie: queda como pista, SIN persona_id
        {"persona": "Desconocido Zapatero", "tipo_evento": "defuncion",
         "fecha_valor": "1901"},
    ]
    n = asociar_persona_ids(hallazgos, fam)
    assert n == 2
    assert hallazgos[0]["persona_id"] == "P0001"
    assert hallazgos[1]["persona_id"] == "P0003"
    assert "persona_id" not in hallazgos[2]
