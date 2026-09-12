"""
tests/test_p1_padrinos_v104.py — v10.4 (P1): padrinos y testigos como pistas
de ramas colaterales.

PROBLEMA QUE CORRIGE (contraste con el informe de metodología profesional):
el prompt de extracción PIDE los roles "(padrino), (madrina), (testigo)"
(config.py) y hasta la v10.4 el parser los ignoraba: `_extraer_progenitor`
solo entendía (padre)/(madre) y `_dato_relacionados` solo miraba
padre/madre/cónyuge. Los padrinos que el modelo transcribía caían en
`otros_nombres` y nadie los volvía a mirar: dato pagado en tokens, dato
tirado.

REGLA NUEVA (determinista): un padrino/madrina/testigo cuyo APELLIDO sea
apellido de la familia se convierte en CANDIDATO de la frontera (el informe:
"quienes comparten apellidos con el padre o la madre son muy probablemente
familiares directos"). Un padrino con apellido ajeno es un vecino o un
amigo: se ignora.

GUARDARRAÍL (lo más importante de este fichero): un padrino NUNCA es un dato
de filiación y NUNCA cambia el nivel de evidencia de nada. Sería parentesco
por apellido, justo lo prohibido desde la v9.1.

100% offline: funciones puras + commit sobre un BASE_DIR temporal.
Ejecución:  python -m pytest tests/test_p1_padrinos_v104.py -q
"""

from __future__ import annotations

import json

import agent.fase2 as fase2
import agent.frontera as frontera
from agent.evidencia import NIVEL_CONFIRMADO, clasificar_hallazgo
from agent.frontera import _extraer_progenitor, nombres_colaterales

URL = "http://archivodeejemplo.es/partida/11"
CITA = ("Juan Merillas Pelaz hijo de Nazario Merillas y de Obdulia Pelaz; "
        "fueron sus padrinos Saturio Panero y Ana Merillas y testigo "
        "Pedro Ruiz")


# ============================== FIXTURES ===================================

def _familia() -> dict:
    """Familia conocida: los apellidos Merillas / Panero / Pelaz."""
    return {"personas": [
        {"id": "P0001", "nombre": "Isidro Merillas Panero",
         "apellido_paterno": "Merillas", "apellido_materno": "Panero",
         "padre": "Nazario Merillas", "madre": "Obdulia Pelaz",
         "nacimiento": {"fecha_aproximada": "1870", "municipio": "Salas"},
         "defuncion": {}, "hijos": [],
         "evidencias": [{"tipo": "bautismo", "fecha": "1870"}]},
        {"id": "P0002", "nombre": "Nazario Merillas",
         "apellido_paterno": "Merillas", "apellido_materno": "",
         "nacimiento": {"fecha_aproximada": "1845"}, "defuncion": {},
         "evidencias": [{"tipo": "bautismo", "fecha": "1845"}]},
        {"id": "P0003", "nombre": "Obdulia Pelaz",
         "apellido_paterno": "Pelaz", "apellido_materno": "",
         "nacimiento": {"fecha_aproximada": "1850"}, "defuncion": {},
         "evidencias": [{"tipo": "bautismo", "fecha": "1850"}]},
    ]}


def _hallazgo(**extra) -> dict:
    h = {"persona": "Juan Merillas Pelaz", "tipo_evento": "bautismo",
         "fecha_valor": "1872-03-14", "lugar": "Salas",
         "otros_nombres": ["Nazario Merillas (padre)",
                           "Obdulia Pelaz (madre)",
                           "Saturio Panero (padrino)",
                           "Ana Merillas (madrina)",
                           "Pedro Ruiz (testigo)"],
         "cita_literal": CITA, "url_fuente": URL,
         "verificacion_cita": "VERIFICADA", "posible_homonimo": False}
    h.update(extra)
    return h


# ==================== LECTURA DE ROLES (frontera.py) ========================

def test_extrae_padrino_madrina_y_testigo():
    col = nombres_colaterales(_hallazgo())
    assert ("padrino", "Saturio Panero") in col
    assert ("madrina", "Ana Merillas") in col
    assert ("testigo", "Pedro Ruiz") in col
    assert len(col) == 3


def test_acepta_plurales_y_no_duplica():
    """El modelo puede escribir '(padrinos)': el rol vuelve en singular y
    sin repetir el mismo nombre."""
    h = _hallazgo(otros_nombres=["Saturio Panero (padrinos)",
                                 "Saturio Panero (padrino)"])
    assert nombres_colaterales(h) == [("padrino", "Saturio Panero")]


def test_padre_no_se_confunde_con_padrino():
    """'(padre)' no es '(padrino)': siguen siendo cosas distintas."""
    h = _hallazgo(otros_nombres=["Nazario Merillas (padre)"])
    assert nombres_colaterales(h) == []
    assert _extraer_progenitor(["Nazario Merillas (padre)"], "padre") \
        == "Nazario Merillas"


def test_rol_desconocido_no_es_colateral():
    assert nombres_colaterales(_hallazgo(
        otros_nombres=["Juan Vecino (vecino)"])) == []


# ================= CANDIDATAS COLATERALES (fase2.py) ========================

def test_candidata_con_apellido_de_la_familia():
    cons = {"personas_nuevas_candidatas": []}
    n = fase2.candidatas_por_colaterales(cons, [_hallazgo()], _familia())
    assert n == 2                       # Saturio Panero + Ana Merillas
    nombres = [c["nombre"] for c in cons["personas_nuevas_candidatas"]]
    assert nombres == ["Saturio Panero", "Ana Merillas"]
    assert "Pedro Ruiz" not in nombres  # apellido ajeno: vecino/amigo
    sat = cons["personas_nuevas_candidatas"][0]
    assert sat["apellido"] == "Panero"
    assert sat["municipio"] == "Salas"
    assert "padrino" in sat["motivo"]
    assert "familia" in sat["motivo"]


def test_padrino_ya_en_el_arbol_no_es_candidato():
    """Si el padrino ya es una ficha conocida, no hay nada que investigar."""
    h = _hallazgo(otros_nombres=["Nazario Merillas (padrino)"])
    cons = {"personas_nuevas_candidatas": []}
    assert fase2.candidatas_por_colaterales(cons, [h], _familia()) == 0


def test_no_duplica_candidatas_existentes():
    cons = {"personas_nuevas_candidatas": [{"nombre": "Saturio Panero"}]}
    h = _hallazgo(otros_nombres=["Saturio Panero (padrino)"])
    assert fase2.candidatas_por_colaterales(cons, [h], _familia()) == 0
    assert len(cons["personas_nuevas_candidatas"]) == 1


def test_sin_apellidos_de_familia_no_inventa_candidatos():
    """Sin apellidos conocidos no hay señal: mejor no buscar a nadie que
    buscar a un desconocido con el presupuesto de la familia."""
    fam = {"personas": [{"nombre": "Isidro"}]}
    cons = {"personas_nuevas_candidatas": []}
    assert fase2.candidatas_por_colaterales(cons, [_hallazgo()], fam) == 0


# ==================== GUARDARRAÍL: NO CERTIFICAN NADA =======================

def test_los_padrinos_no_cambian_el_nivel_de_evidencia():
    """Un padrino NO es un dato de filiación: un hallazgo que solo nombra
    padrinos y testigos (SIN padres, ni en otros_nombres ni en la cita) no
    puede quedar 'confirmado' — sería parentesco por apellido, lo prohibido
    desde la v9.1."""
    h = _hallazgo(otros_nombres=["Saturio Panero (padrino)",
                                 "Ana Merillas (madrina)"],
                  cita_literal="Fueron sus padrinos Saturio Panero y Ana "
                               "Merillas y testigo Pedro Ruiz")
    clasificar_hallazgo(h, _familia())
    assert h["nivel_evidencia"] != NIVEL_CONFIRMADO
    assert h["datos_que_casan"] == []


def test_los_padrinos_no_bloquean_la_confirmacion_por_padres():
    """Integración con M1: si el documento nombra a los DOS progenitores
    confirmados, el hallazgo sigue confirmándose (los padrinos conviven con
    la regla, no la estorban) y además generan sus candidatas."""
    h = _hallazgo()
    clasificar_hallazgo(h, _familia())
    assert h["nivel_evidencia"] == NIVEL_CONFIRMADO
    cons = {"personas_nuevas_candidatas": []}
    assert fase2.candidatas_por_colaterales(cons, [h], _familia()) == 2


# ==================== INTEGRACIÓN CON EL COMMIT Y LA FRONTERA ===============

def test_commit_pasa_municipio_y_apellido_al_estado(tmp_path, monkeypatch):
    monkeypatch.setattr(frontera, "BASE_DIR", tmp_path)
    (tmp_path / "familia_conocida.json").write_text(
        json.dumps(_familia(), ensure_ascii=False), encoding="utf-8")
    (tmp_path / "arbol_refinado.json").write_text(
        json.dumps({"personas": [], "personas_nuevas_candidatas": [
            {"nombre": "Saturio Panero", "motivo": "padrino de Juan Merillas",
             "fuente_url": URL, "municipio": "Salas", "apellido": "Panero",
             "nivel_evidencia": "coincidencia_debil"}]},
            ensure_ascii=False), encoding="utf-8")
    (tmp_path / "arbol_hallazgos.json").write_text("[]", encoding="utf-8")

    res = frontera.cometer_confirmaciones(aplicar=True)
    assert res["nuevas"] == 0                     # una pista NO es una ficha
    assert len(res["candidatos"]) == 1
    c = res["candidatos"][0]
    assert c["municipio"] == "Salas"
    assert c["apellido"] == "Panero"
    fam = json.loads((tmp_path / "familia_conocida.json").read_text("utf-8"))
    assert len(fam["personas"]) == 3              # el padrino no entra
    est = json.loads((tmp_path / "estado_investigacion.json")
                     .read_text("utf-8"))
    assert est["candidatos"][0]["nombre"] == "Saturio Panero"
    assert est["candidatos"][0]["municipio"] == "Salas"


def test_frontera_prioriza_al_candidato_por_rareza_de_apellido():
    """Antes, el apellido del candidato no llegaba nunca a la frontera: el
    término 1.5*rareza_apellido() valía 0 para todos y la prioridad era un
    5.0 plano."""
    estado_previo = {
        "ciclo": 1, "frontera": [], "investigados": [], "descartados": [],
        "profundidad": {},
        "candidatos": [{"nombre": "Saturio Panero", "motivo": "padrino",
                        "fuente_url": URL, "municipio": "Salas",
                        "apellido": "Panero",
                        "nivel_evidencia": "coincidencia_debil"}],
    }
    estado = frontera.calcular_frontera(familia={"personas": []},
                                        estado_previo=estado_previo)
    entradas = [e for e in estado["frontera"] if e["tipo"] == "candidato"]
    assert len(entradas) == 1
    assert entradas[0]["prioridad"] > 5.0
    assert entradas[0]["municipio"] == "Salas"
