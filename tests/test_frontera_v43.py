"""
tests/test_frontera_v43.py — La frontera reconoce a los padres confirmados
con matching DIFUSO (v4.3).

Problema real: la partida nombra al padre como 'Nazario Merillas' y la
ficha (creada por el commit o escrita a mano) se llama 'Nazario Merillas
Uribarri'. El matching EXACTO de la v4.2 nunca las casaba: la pareja
nunca constaba como confirmada y la frontera regeneraba la entrada
'padres' en CADA ciclo — trabajo repetido y presupuesto quemado sin que
el árbol avanzara de verdad.
"""

from __future__ import annotations

from agent.frontera import calcular_frontera


def _familia() -> dict:
    """Hijo confirmado con padres confirmados cuyos nombres en la ficha
    del hijo NO coinciden literalmente con los de las fichas de los
    padres (grafía más corta, como sale en las partidas)."""
    padre = {"nombre": "Nazario Merillas Uribarri", "sexo": "M",
             "nacimiento": {"fecha_aproximada": "1845"},
             "conyuge": "Obdulia Pelaz Merino",
             "evidencias": [{"tipo": "bautismo"}],
             "estado": "confirmado", "padre": "", "madre": ""}
    madre = {"nombre": "Obdulia Pelaz Merino", "sexo": "F",
             "nacimiento": {"fecha_aproximada": "1848"},
             "conyuge": "Nazario Merillas Uribarri",
             "evidencias": [{"tipo": "bautismo"}],
             "estado": "confirmado", "padre": "", "madre": ""}
    hijo = {"nombre": "Isidro Merillas Panero", "sexo": "M",
            "nacimiento": {"fecha_aproximada": "1870"},
            # nombres CORTOS, como los nombra la partida:
            "padre": "Nazario Merillas", "madre": "Obdulia Pelaz",
            "evidencias": [{"tipo": "bautismo"}],
            "estado": "confirmado"}
    return {"personas": [padre, madre, hijo]}


def _estado_vacio() -> dict:
    return {"ciclo": 1, "frontera": [], "candidatos": [],
            "descartados": [], "investigados": []}


def test_padres_confirmados_con_grafia_distinta_no_repiten():
    """v4.3: el matching difuso casa 'Nazario Merillas' (nombre en el
    hijo) con 'Nazario Merillas Uribarri' (ficha): los padres constan
    como confirmados y la frontera NO genera la entrada 'padres'."""
    res = calcular_frontera(familia=_familia(), estado_previo=_estado_vacio())
    tipos = {(e["tipo"], e["ancla"]) for e in res["frontera"]}
    assert ("padres", "Isidro Merillas Panero") not in tipos, \
        ("con matching difuso, unos padres confirmados con grafía "
         "distinta ya no piden búsqueda eterna")


def test_genera_hermanos_cuando_toda_la_familia_esta_confirmada():
    """Con persona + padres confirmados (casados por matching difuso),
    el siguiente objetivo es la NIDADA (hermanos), no rebuscar padres."""
    res = calcular_frontera(familia=_familia(), estado_previo=_estado_vacio())
    hermanos = [e for e in res["frontera"] if e["tipo"] == "hermanos"]
    assert len(hermanos) == 1
    assert hermanos[0]["ancla"] == "Isidro Merillas Panero"


def test_padres_sin_confirmar_siguen_generando_entrada():
    """Regresión: si de verdad faltan evidencias de los padres, la
    entrada 'padres' se sigue generando (el matching difuso no confirma
    nada por sí solo, solo casa NOMBRES)."""
    fam = _familia()
    for p in fam["personas"][:2]:        # desconfirmar padre y madre
        p["evidencias"] = []
        p["estado"] = "memoria"
    res = calcular_frontera(familia=fam, estado_previo=_estado_vacio())
    tipos = {(e["tipo"], e["ancla"]) for e in res["frontera"]}
    assert ("padres", "Isidro Merillas Panero") in tipos
