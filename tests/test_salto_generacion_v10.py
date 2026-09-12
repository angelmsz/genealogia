"""
tests/test_salto_generacion_v10.py — TAREA 2 de la v10.0: salto de
generación robusto (_extraer_progenitor tolerante).

El parser viejo SOLO entendía "Nombre (padre)" en otros_nombres; ahora
también acepta "hijo/hija de X y Y" (en otros_nombres y, como último
recurso, en la cita literal), y ante dos candidatos DISTINTOS para el
mismo rol no crea la ficha y avisa (nunca adivina).

Cubre:
  1. Los 3 formatos crean las fichas de los padres vía
     cometer_confirmaciones.
  2. La ambigüedad NO crea nada y lanza log_warn.
  3. Tras crear los padres, calcular_frontera genera la entrada
     correspondiente (la nidada: "hermanos" del hijo confirmado).

Ejecución:  python -m pytest tests/test_salto_generacion_v10.py -q
"""
from __future__ import annotations

import json

import agent.frontera as frontera
from agent.frontera import (calcular_frontera, cometer_confirmaciones,
                            _extraer_progenitor, _progenitores_de_hijo_de)

URL = "http://archivodeejemplo.es/partida/77"
CITA_BASE = "Isidro Merillas Panero, hijo de Nazario Merillas y de " \
            "Bernarda Panero, bautizado el 2 de mayo de 1870"


def _escribir(tmp_path, nombre, datos):
    (tmp_path / nombre).write_text(json.dumps(datos, ensure_ascii=False,
                                              indent=2), encoding="utf-8")


def _familia() -> dict:
    return {"personas": [{
        "id": "P0001",
        "nombre": "Isidro Merillas Panero",
        "apellido_paterno": "Merillas", "apellido_materno": "Panero",
        "sexo": "M",
        "nacimiento": {"fecha_aproximada": "1870"},
        "defuncion": {}, "padre": "", "madre": "",
        "estado": "memoria", "evidencias": [],
    }]}


def _refinado() -> dict:
    return {"personas": [{
        "nombre": "Isidro Merillas Panero",
        "eventos_confirmados": [{
            "tipo": "bautismo", "fecha": "02-05-1870",
            "lugar": "Salas de los Infantes", "fuente_url": URL,
            "cita": CITA_BASE}],
        "eventos_estimados": [], "contradicciones": [],
        "nuevas_pistas": [],
    }]}


def _hallazgos(otros: list, cita: str = CITA_BASE) -> list:
    return [{
        "persona": "Isidro Merillas", "persona_id": "P0001",
        "tipo_evento": "bautismo", "fecha_valor": "02-05-1870",
        "cita_literal": cita, "url_fuente": URL,
        "verificacion_cita": "VERIFICADA", "posible_homonimo": False,
        "otros_nombres": otros,
    }]


def _preparar(tmp_path, monkeypatch, otros, cita=CITA_BASE):
    monkeypatch.setattr(frontera, "BASE_DIR", tmp_path)
    _escribir(tmp_path, "familia_conocida.json", _familia())
    _escribir(tmp_path, "arbol_refinado.json", _refinado())
    _escribir(tmp_path, "arbol_hallazgos.json", _hallazgos(otros, cita))


# ====================== 1) LOS 3 FORMATOS CREAN A LOS PADRES ================

def test_formato_1_canonico_parentesis(tmp_path, monkeypatch):
    """Formato del prompt: 'Nazario Merillas (padre)' / '(madre)'."""
    _preparar(tmp_path, monkeypatch,
              ["Nazario Merillas (padre)", "Bernarda Panero (madre)"])
    res = cometer_confirmaciones(aplicar=True)
    assert res["nuevas"] == 2
    fam = json.loads((tmp_path / "familia_conocida.json").read_text("utf-8"))
    nombres = {p["nombre"] for p in fam["personas"]}
    assert nombres == {"Isidro Merillas Panero", "Nazario Merillas",
                       "Bernarda Panero"}


def test_formato_2_hijo_de_en_otros_nombres(tmp_path, monkeypatch):
    """Formato tolerante: 'hijo de Nazario Merillas y Bernarda Panero'
    como entrada de otros_nombres (X->padre, Y->madre)."""
    _preparar(tmp_path, monkeypatch,
              ["hijo de Nazario Merillas y Bernarda Panero"])
    res = cometer_confirmaciones(aplicar=True)
    assert res["nuevas"] == 2
    fam = json.loads((tmp_path / "familia_conocida.json").read_text("utf-8"))
    hijo = fam["personas"][0]
    assert hijo["padre"] == "Nazario Merillas"
    assert hijo["madre"] == "Bernarda Panero"
    nombres = {p["nombre"] for p in fam["personas"]}
    assert "Nazario Merillas" in nombres and "Bernarda Panero" in nombres


def test_formato_3_hijo_de_en_cita_literal(tmp_path, monkeypatch):
    """ÚLTIMO RECURSO: otros_nombres vacío, pero la cita literal dice
    '...hijo de Nazario Merillas y de Bernarda Panero, bautizado...'."""
    _preparar(tmp_path, monkeypatch, otros=[],
              cita="En la villa de Salas, Isidro, hijo de Nazario Merillas "
                   "y de Bernarda Panero, bautizado el 2 de mayo de 1870.")
    res = cometer_confirmaciones(aplicar=True)
    assert res["nuevas"] == 2
    fam = json.loads((tmp_path / "familia_conocida.json").read_text("utf-8"))
    hijo = fam["personas"][0]
    assert hijo["padre"] == "Nazario Merillas"
    assert hijo["madre"] == "Bernarda Panero"


# ====================== 2) AMBIGÜEDAD: NADA + AVISO =========================

def test_ambiguedad_no_crea_y_avisa(tmp_path, monkeypatch, capsys):
    """Dos candidatos DISTINTOS para el MISMO rol ('Pedro García (padre)'
    y 'Juan López (padre)'): NO se crea la ficha de ese rol (ni la del
    otro, que no tiene candidato) y hay log_warn (nunca se adivina).
    La cita es neutra (sin 'hijo de') para probar solo la ambigüedad."""
    _preparar(tmp_path, monkeypatch,
              ["Pedro García (padre)", "Juan López (padre)"],
              cita="Partida de bautismo de Isidro Merillas Panero, "
                   "2 de mayo de 1870, Salas de los Infantes.")
    res = cometer_confirmaciones(aplicar=True)
    assert res["nuevas"] == 0
    fam = json.loads((tmp_path / "familia_conocida.json").read_text("utf-8"))
    assert len(fam["personas"]) == 1          # solo el hijo: nadie creado
    hijo = fam["personas"][0]
    assert hijo["padre"] == "" and hijo["madre"] == ""
    salida = capsys.readouterr().out + capsys.readouterr().err
    assert "candidatos DISTINTOS" in salida
    assert "nunca se adivina" in salida


def test_conflicto_de_roles_se_resuelve_por_rol(tmp_path, monkeypatch, capsys):
    """El paréntesis dice 'Pedro García (padre)' pero el 'hijo de' nombra
    a otro padre: el rol 'padre' queda AMBIGUO (nada creado, aviso) y el
    rol 'madre' — nombrado sin conflicto en la misma frase — sí se crea
    (la regla del spec es POR ROL: nunca se adivina, pero lo explícito
    no se descarta)."""
    _preparar(tmp_path, monkeypatch,
              ["Pedro García (padre)",
               "hijo de Juan López y Ana Ruiz"])
    res = cometer_confirmaciones(aplicar=True)
    assert res["nuevas"] == 1                 # solo la madre, no el padre
    fam = json.loads((tmp_path / "familia_conocida.json").read_text("utf-8"))
    hijo = fam["personas"][0]
    assert hijo["padre"] == ""                 # ambiguo: no se adivina
    assert hijo["madre"] == "Ana Ruiz"         # sin conflicto: se crea
    salida = capsys.readouterr().out + capsys.readouterr().err
    assert "candidatos DISTINTOS" in salida
    nombres = {p["nombre"] for p in fam["personas"]}
    assert "Pedro García" not in nombres and "Juan López" not in nombres
    assert "Ana Ruiz" in nombres


def test_ambiguedad_en_cita_literal_no_crea(tmp_path, monkeypatch, capsys):
    """La cita literal nombra DOS parejas de padres distintas: no se
    crea nada y hay aviso."""
    _preparar(tmp_path, monkeypatch, otros=[],
              cita="Isidro, hijo de Juan López y Ana Ruiz; también hijo de "
                   "Pedro García y María Peña, según el acta.")
    res = cometer_confirmaciones(aplicar=True)
    assert res["nuevas"] == 0
    salida = capsys.readouterr().out + capsys.readouterr().err
    assert "candidatos DISTINTOS" in salida


def test_consenso_entre_formatos_si_crea(tmp_path, monkeypatch):
    """Los dos formatos que coinciden (mismo nombre) NO son ambigüedad:
    se crea UNA sola ficha por rol."""
    _preparar(tmp_path, monkeypatch,
              ["Nazario Merillas (padre)", "Bernarda Panero (madre)",
               "hijo de Nazario Merillas y Bernarda Panero"])
    res = cometer_confirmaciones(aplicar=True)
    assert res["nuevas"] == 2          # no 4: el consenso deduplica
    fam = json.loads((tmp_path / "familia_conocida.json").read_text("utf-8"))
    assert len(fam["personas"]) == 3


# ====================== 3) FRONTERA TRAS CREAR A LOS PADRES =================

def test_tras_crear_padres_frontera_genera_entrada(tmp_path, monkeypatch):
    """Tras el salto de generación (formato 'hijo de'), calcular_frontera
    genera la entrada correspondiente: el hijo está confirmado y sus
    padres constan (con evidencia de la partida) -> entrada 'hermanos'
    (la nidada), con los padres en el campo 'padres' de la entrada."""
    _preparar(tmp_path, monkeypatch,
              ["hijo de Nazario Merillas y Bernarda Panero"])
    cometer_confirmaciones(aplicar=True)
    fam = json.loads((tmp_path / "familia_conocida.json").read_text("utf-8"))

    estado = calcular_frontera(familia=fam,
                               estado_previo={"ciclo": 0,
                                              "frontera": [],
                                              "candidatos": [],
                                              "descartados": [],
                                              "investigados": []})
    entradas_hijo = [e for e in estado["frontera"]
                     if e["ancla"] == "Isidro Merillas Panero"]
    assert len(entradas_hijo) == 1
    entrada = entradas_hijo[0]
    # La entrada correspondiente al salto: buscar a los HERMANOS de la
    # nidada confirmada (padre + madre ya constan en la partida).
    assert entrada["tipo"] == "hermanos"
    assert "Nazario Merillas" in entrada["padres"]
    assert "Bernarda Panero" in entrada["padres"]
    assert entrada["clave_investigacion"].startswith("hermanos::")


# ====================== UNITARIOS DEL PARSER =================================

def test_parser_unitario_tres_formatos():
    """El parser aislado entiende los 3 formatos y limpia puntuación."""
    # 1) paréntesis
    assert _extraer_progenitor(
        ["Nazario Merillas (padre)", "Obdulia Pelaz (madre)"], "padre") == \
        "Nazario Merillas"
    assert _extraer_progenitor(
        ["Nazario Merillas (padre)", "Obdulia Pelaz (madre)"], "madre") == \
        "Obdulia Pelaz"
    # 2) hijo de X y Y en otros_nombres
    assert _extraer_progenitor(
        ["hijo de Nazario Merillas y Obdulia Pelaz"], "padre") == \
        "Nazario Merillas"
    assert _extraer_progenitor(
        ["hijo de Nazario Merillas y Obdulia Pelaz"], "madre") == \
        "Obdulia Pelaz"
    # 3) último recurso: cita literal (con puntuación alrededor)
    assert _extraer_progenitor(
        [], "padre",
        cita_literal="Isidro, hijo de Nazario Merillas y de Obdulia "
                     "Pelaz, bautizado el 2-05-1870.") == "Nazario Merillas"
    assert _extraer_progenitor(
        [], "madre",
        cita_literal="Isidro, hijo de Nazario Merillas y de Obdulia "
                     "Pelaz, bautizado el 2-05-1870.") == "Obdulia Pelaz"


def test_parser_unitario_ambiguedad():
    """Dos candidatos distintos -> '' (nunca adivinar)."""
    assert _extraer_progenitor(
        ["A (padre)", "hijo de B y C"], "padre") == ""


def test_progenitores_de_hijo_de_variante_legitima():
    """'hijo legítimo de X y de Y' también se entiende (frase típica de
    las partidas parroquiales del XVIII-XIX)."""
    pares = _progenitores_de_hijo_de(
        "María, hija legítima de Juan Martínez y de Luisa Fernández, "
        "nació el 4 de abril de 1802.")
    assert pares == [("Juan Martínez", "Luisa Fernández")]
