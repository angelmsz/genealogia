"""
v10.4.1 (tarea D) — La frontera se fabrica también con lo que la fase 2 halló.

Contexto REAL (log agente_20260912_222149.log + estado_investigacion.json):
  - ciclo 1: 15 objetivos investigados -> los 15 acaban en 'investigados'.
  - fase 2: 58 hallazgos, 2 de nivel 'candidato_fuerte', 56 débiles.
  - ciclo 2: 0 entradas en la frontera y NADA que hacer (el autopiloto se
    apagó dejando el 90 % del presupuesto sin gastar).
  - la única salida era borrar 'investigados' a mano: rompía la
    autosuficiencia.
  - y el resumen de las 00:45 decía "15 pendientes · 15 ya investigadas"
    porque main.py guardaba la frontera calculada ANTES de apuntar el ciclo.

Lo que fija esta suite:
  1. Un hallazgo 'candidato_fuerte' de alguien que NO está en el árbol entra
     en la COLA como 'candidato' (se verifica; nunca entra al árbol).
  2. Las pistas colaterales (padrino/testigo con apellido de la familia, que
     P1 deja en arbol_refinado.json) también entran, pero POR DETRÁS de un
     candidato fuerte: una pista no es una candidata.
  3. Una coincidencia_débil sin documento detrás NO entra: no se paga
     presupuesto por un 'quizá' del LLM.
  4. Alguien que ya está en el árbol no se duplica como candidato.
  5. Una entrada ya investigada se REABRE solo si su evidencia cambió
     (hash_evidencia): evidencia nueva sí, bucle no.
  6. Compatibilidad: las entradas de 'investigados' sin hash (formato viejo)
     siguen quedando fuera.

Los artefactos de fase 2 se leen de un BASE_DIR de prueba (tmp_path): estos
tests no tocan los ficheros reales del proyecto.
"""

from __future__ import annotations

import json

import pytest

from agent import frontera


@pytest.fixture()
def base_falsa(tmp_path, monkeypatch):
    """BASE_DIR de prueba: controla arbol_hallazgos.json y arbol_refinado.json."""
    monkeypatch.setattr(frontera, "BASE_DIR", tmp_path)
    return tmp_path


def _escribir(base, nombre: str, datos) -> None:
    (base / nombre).write_text(json.dumps(datos, ensure_ascii=False),
                              encoding="utf-8")


def _hallazgo(persona: str, nivel: str, tipo: str = "bautismo",
              url: str = "https://ejemplo.invalid/a") -> dict:
    return {"persona": persona, "nivel_evidencia": nivel,
            "tipo_evento": tipo, "fecha_valor": "1870",
            "url_fuente": url, "lugar": "Pobladura del Valle"}


def _persona(nombre: str) -> dict:
    partes = nombre.split()
    return {"nombre": nombre,
            "apellido_paterno": partes[-2] if len(partes) > 1 else partes[0],
            "nacimiento": {"fecha_aproximada": "1870",
                           "municipio": "Pobladura del Valle",
                           "provincia": "Zamora"},
            "padre": "", "madre": ""}


def _estado(investigados=None) -> dict:
    return {"ciclo": 1, "frontera": [], "candidatos": [], "descartados": [],
            "investigados": investigados or []}


# ------------------------------------------------- 1. candidato fuerte ----
def test_candidato_fuerte_entra_en_la_cola(base_falsa) -> None:
    """Un 'candidato_fuerte' sin dueño en el árbol pasa a la frontera: es lo
    que anoche no ocurría (los 2 fuertes se quedaron en un JSON que nadie
    releía) y dejó el ciclo 2 sin nada que hacer."""
    _escribir(base_falsa, frontera.HALLAZGOS_JSON,
              [_hallazgo("Gregorio Merino Calvo", "candidato_fuerte")])
    res = frontera.calcular_frontera(
        familia={"personas": [_persona("Obdulia Pelaz Merino")]},
        estado_previo=_estado())

    cands = [e for e in res["frontera"] if e["tipo"] == "candidato"]
    assert [c["ancla"] for c in cands] == ["Gregorio Merino Calvo"]
    assert cands[0]["prioridad"] > 5          # nivel de "hay que verificar"
    assert "verificar" in cands[0]["motivo"]


def test_candidato_que_ya_es_ficha_no_se_duplica(base_falsa) -> None:
    """Si el 'candidato_fuerte' es una ficha conocida (anoche: 'Araceli
    Panero' = P0009), su camino son las ramas normales de la frontera, no una
    entrada de candidato duplicada."""
    _escribir(base_falsa, frontera.HALLAZGOS_JSON,
              [_hallazgo("Araceli Panero", "candidato_fuerte")])
    fam = {"personas": [_persona("Maria Aurora Araceli Panero")]}
    res = frontera.calcular_frontera(familia=fam, estado_previo=_estado())
    assert [e for e in res["frontera"] if e["tipo"] == "candidato"] == []


def test_un_candidato_ya_investigado_no_repite(base_falsa) -> None:
    """Sin evidencia nueva, un candidato ya investigado no vuelve a la cola
    (dedupe por clave "candidato::nombre")."""
    _escribir(base_falsa, frontera.HALLAZGOS_JSON,
              [_hallazgo("Gregorio Merino Calvo", "candidato_fuerte")])
    res = frontera.calcular_frontera(
        familia={"personas": [_persona("Obdulia Pelaz Merino")]},
        estado_previo=_estado([{"clave": "candidato::gregorio merino calvo",
                                "ciclo": 1}]))
    assert [e for e in res["frontera"] if e["tipo"] == "candidato"] == []


# ------------------------------------------------- 2. pistas colaterales --
def test_pista_colateral_entra_por_detras_del_candidato_fuerte(base_falsa) -> None:
    """P1 (padrinos/testigos con apellido de familia) deja candidatas débiles
    CON fuente en arbol_refinado.json: entran como pista, con menos prioridad
    que un candidato fuerte.

    Los dos llevan el MISMO apellido a propósito: la prioridad es
    1.5*rareza + bonus, así que con rarezas distintas la comparación no
    probaría nada sobre el bonus de 'pista' (fue el primer error de este
    test: 'Merino Calvo' y 'Merillas' acabaron en 6.5 los dos)."""
    _escribir(base_falsa, frontera.HALLAZGOS_JSON,
              [_hallazgo("Gregorio Merillas", "candidato_fuerte")])
    _escribir(base_falsa, frontera.REFINADO_JSON, {
        "personas_nuevas_candidatas": [
            {"nombre": "Manuel Merillas",
             "apellido": "Merillas", "municipio": "Pobladura del Valle",
             "fuente_url": "https://ejemplo.invalid/partida",
             "motivo": "padrino de Isidro Merillas Panero: apellido de la "
                       "familia — verificar",
             "nivel_evidencia": "coincidencia_debil"},
        ]})
    res = frontera.calcular_frontera(
        familia={"personas": [_persona("Obdulia Pelaz Merino")]},
        estado_previo=_estado())

    cands = {e["ancla"]: e for e in res["frontera"] if e["tipo"] == "candidato"}
    fuerte, pista = cands["Gregorio Merillas"], cands["Manuel Merillas"]
    assert "pista_colateral" in pista["motivo"]
    assert frontera.rareza_apellido("Merillas") == \
        frontera.rareza_apellido(fuerte["apellido"])   # rarezas iguales
    assert pista["prioridad"] < fuerte["prioridad"]


def test_debil_sin_fuente_no_gasta_presupuesto(base_falsa) -> None:
    """Una coincidencia_débil del LLM sin documento detrás NO entra: el ruido
    no se investiga a precio de objetivo."""
    _escribir(base_falsa, frontera.REFINADO_JSON, {
        "personas_nuevas_candidatas": [
            {"nombre": "Juan Perez Gomez", "nivel_evidencia":
             "coincidencia_debil", "motivo": "quizá"},
        ]})
    res = frontera.calcular_frontera(
        familia={"personas": [_persona("Obdulia Pelaz Merino")]},
        estado_previo=_estado())
    assert [e for e in res["frontera"] if e["tipo"] == "candidato"] == []


# ------------------------------------------------- 5. reapertura ----------
def test_evidencia_nueva_reabre_una_entrada_investigada(base_falsa) -> None:
    """La regla que pidió el usuario: una semilla antigua se reintenta SOLO
    cuando algo nuevo la toca. Con hash de evidencia distinto -> REABIERTA."""
    _escribir(base_falsa, frontera.HALLAZGOS_JSON,
              [_hallazgo("Isidro Merillas Panero", "coincidencia_debil")])
    fam = {"personas": [_persona("Isidro Merillas Panero")]}
    res = frontera.calcular_frontera(
        familia=fam,
        estado_previo=_estado([{"clave": "persona::isidro merillas panero",
                                "ciclo": 1,
                                "evidencia_hash": "hash-de-antes"}]))
    entradas = [e for e in res["frontera"]
                if e["ancla"] == "Isidro Merillas Panero"]
    assert len(entradas) == 1
    assert "REABIERTA" in entradas[0]["motivo"]
    assert "ciclo 1" in entradas[0]["motivo"]


def test_sin_evidencia_nueva_no_se_reabre(base_falsa) -> None:
    """El hash es el de la evidencia ACTUAL: sin novedad la entrada sigue
    fuera (nada de bucles de re-búsqueda)."""
    hallazgo = _hallazgo("Isidro Merillas Panero", "coincidencia_debil")
    _escribir(base_falsa, frontera.HALLAZGOS_JSON, [hallazgo])
    hash_actual = frontera.hash_evidencia("Isidro Merillas Panero")
    assert hash_actual                       # hay evidencia, y es la misma
    res = frontera.calcular_frontera(
        familia={"personas": [_persona("Isidro Merillas Panero")]},
        estado_previo=_estado([{"clave": "persona::isidro merillas panero",
                                "ciclo": 1,
                                "evidencia_hash": hash_actual}]))
    assert [e for e in res["frontera"]
            if e["ancla"] == "Isidro Merillas Panero"] == []


def test_investigados_del_formato_viejo_no_se_reabren(base_falsa) -> None:
    """Compatibilidad: las entradas guardadas antes de la v10.4.1 no tienen
    'evidencia_hash'. Se tratan como 'sin novedad' (conservador): no se
    reabren solas."""
    _escribir(base_falsa, frontera.HALLAZGOS_JSON,
              [_hallazgo("Isidro Merillas Panero", "coincidencia_debil")])
    res = frontera.calcular_frontera(
        familia={"personas": [_persona("Isidro Merillas Panero")]},
        estado_previo=_estado([{"clave": "persona::isidro merillas panero",
                                "ciclo": 1}]))
    assert [e for e in res["frontera"]
            if e["ancla"] == "Isidro Merillas Panero"] == []


# ------------------------------------------------- 6. hash_evidencia -----
def test_hash_evidencia_distingue_lo_nuevo_de_lo_repetido(base_falsa) -> None:
    """El hash depende de los hallazgos (persona+tipo+fecha+URL), no del
    orden ni de la repeticion; y es '' sin evidencia (nunca reabre)."""
    h1 = _hallazgo("Isidro Merillas Panero", "coincidencia_debil")
    h2 = _hallazgo("Isidro Merillas Panero", "coincidencia_debil",
                   tipo="matrimonio", url="https://ejemplo.invalid/b")
    a = frontera.hash_evidencia("Isidro Merillas Panero", [h1])
    b = frontera.hash_evidencia("Isidro Merillas Panero", [h2])
    c = frontera.hash_evidencia("Isidro Merillas Panero", [h1, h2])
    assert a and b and a != b
    assert a not in (c, "")                  # c es un conjunto mayor
    assert frontera.hash_evidencia("Nadie De Ninguna Parte", [h1]) == ""
    assert frontera.hash_evidencia("Isidro Merillas", [h1]) == a  # nombre parcial
