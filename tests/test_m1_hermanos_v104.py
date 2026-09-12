"""
tests/test_m1_hermanos_v104.py — v10.4 (M1): los HERMANOS entran al árbol.

PROBLEMA QUE CORRIGE (auditoría de autonomía del 2026-09-12): el árbol solo
crecía de forma ASCENDENTE. `cometer_confirmaciones` creaba fichas nuevas
únicamente como stubs de padre/madre de alguien ya conocido, así que un
HERMANO —partida verificada, con sus dos progenitores ya confirmados— era
persona nueva, nunca podía clasificarse como "confirmado" (el clasificador
exigía casar con una ficha conocida) y quedaba atrapado para siempre en
personas_nuevas_candidatas. La frontera tipo "hermanos" buscaba una cosecha
que el sistema no sabía certificar.

REGLA NUEVA (determinista, en el clasificador, NUNCA en el LLM): un
nacimiento/bautismo de una persona ausente del árbol se CONFIRMA si el
documento nombra a sus DOS progenitores y AMBOS son fichas conocidas con
evidencia documental. Son dos datos INDEPENDIENTES (padre y madre) que no
dependen del nombre del nuevo: la misma regla de método (>=2 datos) que ya
se aplicaba a nombre+fecha, no una excepción.

Guards cubiertos aquí: tipo de evento, un solo progenitor, progenitores solo
de memoria familiar (sin evidencias), fecha biológicamente imposible,
candidatos ambiguos (nunca se adivina) y personas que no deben entrar.

100% offline: funciones puras + commit sobre un BASE_DIR temporal.
Ejecución:  python -m pytest tests/test_m1_hermanos_v104.py -q
"""

from __future__ import annotations

import json

import agent.frontera as frontera
from agent.evidencia import (NIVEL_CONFIRMADO, clasificar_hallazgo,
                             progenitores_confirmados)

URL = "http://archivodeejemplo.es/partida/9"
CITA = ("Juan Merillas Pelaz hijo de Nazario Merillas y de Obdulia Pelaz")
CITA_ISIDRO = ("Isidro Merillas Panero hijo de Nazario Merillas y de "
               "Obdulia Pelaz")


# ============================== FIXTURES ===================================

def _ficha_padre(nac="1845", con_evidencia=True) -> dict:
    return {
        "id": "P0002", "nombre": "Nazario Merillas",
        "apellido_paterno": "Merillas", "apellido_materno": "", "sexo": "M",
        "nacimiento": ({"fecha_aproximada": nac} if nac else {}),
        "defuncion": {}, "padre": "", "madre": "",
        "conyuge": "Obdulia Pelaz", "hijos": ["Isidro Merillas Panero"],
        "estado": "confirmado" if con_evidencia else "memoria",
        "evidencias": ([{"tipo": "bautismo", "fecha": nac, "lugar": "Salas",
                         "fuente_url": URL, "cita": CITA_ISIDRO,
                         "verificado": True}] if con_evidencia else []),
    }


def _ficha_madre(nac="1850", con_evidencia=True) -> dict:
    return {
        "id": "P0003", "nombre": "Obdulia Pelaz",
        "apellido_paterno": "Pelaz", "apellido_materno": "", "sexo": "F",
        "nacimiento": ({"fecha_aproximada": nac} if nac else {}),
        "defuncion": {}, "padre": "", "madre": "",
        "conyuge": "Nazario Merillas", "hijos": ["Isidro Merillas Panero"],
        "estado": "confirmado" if con_evidencia else "memoria",
        "evidencias": ([{"tipo": "bautismo", "fecha": nac, "lugar": "Salas",
                         "fuente_url": URL, "cita": CITA_ISIDRO,
                         "verificado": True}] if con_evidencia else []),
    }


def _hijo_conocido() -> dict:
    return {
        "id": "P0001", "nombre": "Isidro Merillas Panero",
        "apellido_paterno": "Merillas", "apellido_materno": "Panero",
        "sexo": "M",
        "nacimiento": {"fecha_aproximada": "1870", "municipio": "Salas"},
        "defuncion": {}, "padre": "Nazario Merillas",
        "madre": "Obdulia Pelaz", "hijos": [],
        "estado": "confirmado",
        "evidencias": [{"tipo": "bautismo", "fecha": "02-05-1870",
                        "lugar": "Salas", "fuente_url": URL,
                        "cita": CITA_ISIDRO, "verificado": True}],
    }


def _familia(padre=True, madre=True, con_evidencia=True) -> dict:
    personas = [_hijo_conocido()]
    if padre:
        personas.append(_ficha_padre(con_evidencia=con_evidencia))
    if madre:
        personas.append(_ficha_madre(con_evidencia=con_evidencia))
    return {"personas": personas}


def _hallazgo_hermano(**extra) -> dict:
    """Bautismo de un hermano NUEVO (no está en familia_conocida.json)."""
    h = {
        "persona": "Juan Merillas Pelaz",
        "tipo_evento": "bautismo",
        "fecha_valor": "1872-03-14",
        "lugar": "Salas",
        "otros_nombres": ["Juan Merillas Pelaz hijo de Nazario Merillas y de "
                          "Obdulia Pelaz"],
        "cita_literal": CITA,
        "url_fuente": URL,
        "confianza": "alta",
        "justificacion": "partida literal",
        "verificacion_cita": "VERIFICADA",
        "posible_homonimo": False,
    }
    h.update(extra)
    return h


# =================== EL EXTRACTOR (formato canónico y tolerante) ============

def test_extrae_los_dos_progenitores_formato_tolerante():
    pareja = progenitores_confirmados(_hallazgo_hermano(), _familia())
    assert pareja is not None
    assert pareja[0]["nombre"] == "Nazario Merillas"
    assert pareja[1]["nombre"] == "Obdulia Pelaz"


def test_extrae_los_dos_progenitores_formato_canonico():
    h = _hallazgo_hermano(otros_nombres=["Nazario Merillas (padre)",
                                         "Obdulia Pelaz (madre)"])
    pareja = progenitores_confirmados(h, _familia())
    assert pareja is not None
    assert pareja[0]["nombre"] == "Nazario Merillas"


# =================== CLASIFICADOR: CONFIRMADO POR LOS DOS ===================

def test_hermano_con_dos_progenitores_confirmados_queda_confirmado():
    """El caso que desbloquea la frontera 'hermanos'."""
    h = clasificar_hallazgo(_hallazgo_hermano(), _familia())
    assert h["nivel_evidencia"] == NIVEL_CONFIRMADO
    assert len(h["datos_que_casan"]) == 2
    assert any("padre" in d for d in h["datos_que_casan"])
    assert any("madre" in d for d in h["datos_que_casan"])
    # OJO: la justificación dice "los DOS progenitores" (mayúsculas a
    # propósito, para que destaque en el informe): se compara en minúsculas.
    assert "dos progenitores" in h["justificacion_evidencia"].lower()


def test_confirmado_sin_anio_no_veta():
    """La falta de año no bloquea (misma política que la plausibilidad
    v4.2): los dos progenitores siguen siendo los dos datos."""
    h = clasificar_hallazgo(_hallazgo_hermano(fecha_valor=""),
                            _familia())
    assert h["nivel_evidencia"] == NIVEL_CONFIRMADO


# =================== GUARDS: LO QUE NO DEBE CONFIRMAR =======================

def test_un_solo_progenitor_confirmado_no_confirma():
    h = clasificar_hallazgo(_hallazgo_hermano(), _familia(madre=False))
    assert h["nivel_evidencia"] != NIVEL_CONFIRMADO


def test_progenitores_sin_evidencias_no_confirman():
    """Memoria familiar sin evidencia documental no vale como progenitor
    confirmado (mismo criterio que calcular_frontera)."""
    h = clasificar_hallazgo(_hallazgo_hermano(),
                            _familia(con_evidencia=False))
    assert h["nivel_evidencia"] != NIVEL_CONFIRMADO


def test_fecha_imposible_para_los_progenitores_no_confirma():
    """Padre nacido en 1900 y bautismo del hijo en 1830: contradicción."""
    fam = {"personas": [_hijo_conocido(), _ficha_padre(nac="1900"),
                        _ficha_madre(nac="1895")]}
    h = clasificar_hallazgo(_hallazgo_hermano(fecha_valor="1830-05-02"), fam)
    assert h["nivel_evidencia"] != NIVEL_CONFIRMADO
    assert "imposible" in h["motivo_no_confirmado_por_progenitores"]


def test_ni_una_mencion_ni_una_defuncion_confirman():
    """Solo las partidas de FILIACIÓN confirman: nacimiento, bautismo (M1) y
    matrimonio (M1b). Una mención suelta o una defunción que nombre a los
    padres, no."""
    for tipo in ("mencion", "defuncion"):
        h = clasificar_hallazgo(_hallazgo_hermano(tipo_evento=tipo),
                                _familia())
        assert h["nivel_evidencia"] != NIVEL_CONFIRMADO, tipo
        assert progenitores_confirmados(
            _hallazgo_hermano(tipo_evento=tipo), _familia()) is None, tipo


def test_progenitores_ambiguos_no_confirman():
    """Dos candidatos DISTINTOS para el mismo rol: nunca se adivina."""
    h = _hallazgo_hermano(otros_nombres=["Nazario Merillas (padre)",
                                         "Joaquin Merillas (padre)",
                                         "Obdulia Pelaz (madre)"])
    assert progenitores_confirmados(h, _familia()) is None
    assert clasificar_hallazgo(h, _familia())["nivel_evidencia"] \
        != NIVEL_CONFIRMADO


# =================== COMMIT: LA FICHA DEL HERMANO SE CREA ===================

def _escribir(tmp_path, nombre, datos):
    (tmp_path / nombre).write_text(json.dumps(datos, ensure_ascii=False,
                                              indent=2), encoding="utf-8")


def _preparar_commit(tmp_path, monkeypatch, nivel=NIVEL_CONFIRMADO):
    monkeypatch.setattr(frontera, "BASE_DIR", tmp_path)
    _escribir(tmp_path, "familia_conocida.json", _familia())
    _escribir(tmp_path, "arbol_refinado.json", {
        "personas": [],                       # no hay eventos de personas ya
        "personas_nuevas_candidatas": [{      # conocidas en este escenario
            "nombre": "Juan Merillas Pelaz",
            "motivo": "hermano de Isidro (mismos padres)",
            "fuente_url": URL,
            "nivel_evidencia": nivel}],
    })
    _escribir(tmp_path, "arbol_hallazgos.json",
              [_hallazgo_hermano(nivel_evidencia=nivel)])


def test_commit_crea_la_ficha_del_hermano(tmp_path, monkeypatch):
    _preparar_commit(tmp_path, monkeypatch)
    res = frontera.cometer_confirmaciones(aplicar=True)
    assert res["nuevas"] == 1
    assert res["evidencias"] == 1
    assert res["candidatos"] == []          # ya es ficha, no candidata

    fam = json.loads((tmp_path / "familia_conocida.json").read_text("utf-8"))
    assert len(fam["personas"]) == 4
    juan = next(p for p in fam["personas"]
                if p["nombre"] == "Juan Merillas Pelaz")
    assert juan["padre"] == "Nazario Merillas"
    assert juan["madre"] == "Obdulia Pelaz"
    assert juan["apellido_paterno"] == "Merillas"
    assert juan["apellido_materno"] == "Pelaz"
    assert juan["estado"] == NIVEL_CONFIRMADO
    assert juan["evidencias"] and juan["evidencias"][0]["cita"]
    assert juan["nacimiento"]["municipio"] == "Salas"
    assert juan.get("id")                    # id estable asignado
    # el hermano entra en la lista 'hijos' de sus DOS progenitores
    nazario = next(p for p in fam["personas"]
                   if p["nombre"] == "Nazario Merillas")
    obdulia = next(p for p in fam["personas"]
                   if p["nombre"] == "Obdulia Pelaz")
    assert "Juan Merillas Pelaz" in nazario["hijos"]
    assert "Juan Merillas Pelaz" in obdulia["hijos"]
    # registro append-only (v4.2 punto 7): la confirmación queda auditada
    registro = (tmp_path / "registro_confirmaciones.jsonl").read_text("utf-8")
    assert "Juan Merillas Pelaz" in registro

    # idempotencia: el segundo commit NO duplica la ficha
    res2 = frontera.cometer_confirmaciones(aplicar=True)
    assert res2["nuevas"] == 0
    fam2 = json.loads((tmp_path / "familia_conocida.json").read_text("utf-8"))
    assert len(fam2["personas"]) == 4


def test_commit_no_deja_entrar_a_una_candidata_debil(tmp_path, monkeypatch):
    """El guardarraíl de la v9.1 sigue intacto: nivel débil -> candidata,
    nunca ficha, aunque la cita esté VERIFICADA."""
    _preparar_commit(tmp_path, monkeypatch,
                     nivel="coincidencia_debil")
    res = frontera.cometer_confirmaciones(aplicar=True)
    assert res["nuevas"] == 0
    assert res["evidencias"] == 0
    fam = json.loads((tmp_path / "familia_conocida.json").read_text("utf-8"))
    assert len(fam["personas"]) == 3
    assert [c["nombre"] for c in res["candidatos"]] == ["Juan Merillas Pelaz"]
