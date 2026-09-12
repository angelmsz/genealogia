"""
tests/test_commit.py — El COMMIT (--aceptar) del bucle agéntico.

Cubre los puntos 3, 6 y 7 del informe:
  - Punto 3: dedupe ESTABLE de evidencias (la misma prueba no se re-apunta
    en cada ejecución; el freno del autopiloto vuelve a funcionar porque
    el contador de evidencias nuevas vuelve a 0).
  - Punto 6: el evento de la consolidación casa con la ficha por id
    estable o matching difuso; los padres nombrados en la partida crean
    fichas nuevas CON id, y el segundo commit no las duplica.
  - Punto 7: backup con marca de tiempo + rotación (nunca una única copia
    machacable) y registro append-only que solo crece.
"""

from __future__ import annotations

import json

import agent.frontera as frontera
from agent.frontera import cometer_confirmaciones

URL = "http://archivodeejemplo.es/partida/123"
CITA = "Isidro Merillas Panero hijo de Nazario Merillas y de Obdulia Pelaz"


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
            "cita": CITA}],
        "eventos_estimados": [], "contradicciones": [],
        "nuevas_pistas": [],
    }]}


def _hallazgos() -> list:
    return [{
        "persona": "Isidro Merillas",          # nombre corto (punto 6)
        "persona_id": "P0001",
        "tipo_evento": "bautismo", "fecha_valor": "02-05-1870",
        "cita_literal": CITA, "url_fuente": URL,
        "verificacion_cita": "VERIFICADA", "posible_homonimo": False,
        "otros_nombres": ["Nazario Merillas (padre)",
                          "Obdulia Pelaz (madre)"],
    }]


def _preparar(tmp_path, monkeypatch):
    monkeypatch.setattr(frontera, "BASE_DIR", tmp_path)
    _escribir(tmp_path, "familia_conocida.json", _familia())
    _escribir(tmp_path, "arbol_refinado.json", _refinado())
    _escribir(tmp_path, "arbol_hallazgos.json", _hallazgos())


def test_commit_primera_vez(tmp_path, monkeypatch):
    _preparar(tmp_path, monkeypatch)
    res = cometer_confirmaciones(aplicar=True)
    assert res["evidencias"] == 1
    assert res["nuevas"] == 2          # Nazario + Obdulia (salto de generación)
    fam = json.loads((tmp_path / "familia_conocida.json").read_text("utf-8"))
    assert len(fam["personas"]) == 3
    hijo = fam["personas"][0]
    assert hijo["estado"] == "confirmado"
    assert hijo["nacimiento"]["municipio"] == "Salas de los Infantes"
    assert hijo["padre"] == "Nazario Merillas"
    # las fichas nuevas llevan id estable
    assert all(p.get("id") for p in fam["personas"])
    ids = [p["id"] for p in fam["personas"]]
    assert len(set(ids)) == 3


def test_commit_segunda_vez_no_duplica_nada(tmp_path, monkeypatch):
    """Punto 3: el freno del autopiloto. Antes la evidencia llevaba
    timestamp nuevo en cada commit -> nunca era igual a la anterior ->
    se re-apuntaba -> el contador nunca llegaba a 0 y el autopiloto no
    paraba aunque el ciclo no aportara nada."""
    _preparar(tmp_path, monkeypatch)
    cometer_confirmaciones(aplicar=True)
    res2 = cometer_confirmaciones(aplicar=True)
    assert res2["evidencias"] == 0
    assert res2["nuevas"] == 0
    assert res2["actualizadas"] == 0
    fam = json.loads((tmp_path / "familia_conocida.json").read_text("utf-8"))
    # sigue habiendo 3 fichas y 1 evidencia en el hijo (sin duplicados)
    assert len(fam["personas"]) == 3
    assert len(fam["personas"][0]["evidencias"]) == 1


def test_registro_append_only_solo_crece(tmp_path, monkeypatch):
    """Punto 7: registro_confirmaciones.jsonl crece con cada evidencia
    NUEVA y no se reescribe al re-commit."""
    _preparar(tmp_path, monkeypatch)
    cometer_confirmaciones(aplicar=True)
    reg = tmp_path / "registro_confirmaciones.jsonl"
    lineas1 = reg.read_text("utf-8").strip().splitlines()
    assert len(lineas1) == 3            # 1 evidencia + 2 menciones en partida
    for linea in lineas1:
        d = json.loads(linea)
        assert d["verificado"] is True
        assert d["persona_id"] or d["persona"]
    cometer_confirmaciones(aplicar=True)   # nada nuevo
    lineas2 = reg.read_text("utf-8").strip().splitlines()
    assert lineas2 == lineas1          # no cambió: append-only


def test_backups_con_marca_de_tiempo_y_rotacion(tmp_path, monkeypatch):
    """Punto 7: backups/ con copias fechadas; se conservan las últimas
    BACKUP_MAX_COPIAS (30). La copia única .bak de la v4.1 se machacaba."""
    from config import BACKUP_MAX_COPIAS
    _preparar(tmp_path, monkeypatch)
    dir_bak = tmp_path / "backups"
    dir_bak.mkdir()
    # 35 backups 'viejos' (2020) para forzar la rotación
    for i in range(35):
        (dir_bak / f"familia_conocida_20200101_{i:06d}.json").write_text(
            "{}", encoding="utf-8")
    cometer_confirmaciones(aplicar=True)
    copias = sorted(dir_bak.glob("familia_conocida_*.json"))
    assert len(copias) == BACKUP_MAX_COPIAS
    # las más viejas se han borrado, la nueva (fecha actual) se conserva
    assert all(c.name >= "familia_conocida_20200101_000005" for c in copias)
    assert any(c.name.startswith("familia_conocida_2026")
               or c.name > "familia_conocida_20200101_000034"
               for c in copias)


def test_commit_matching_difuso_nombre_corto(tmp_path, monkeypatch):
    """Punto 6: el bloque de la consolidación trae el nombre corto
    ('Isidro Merillas') y la ficha es 'Isidro Merillas Panero': el commit
    llega por persona_id aunque el nombre no coincida literal."""
    monkeypatch.setattr(frontera, "BASE_DIR", tmp_path)
    _escribir(tmp_path, "familia_conocida.json", _familia())
    ref = _refinado()
    ref["personas"][0]["nombre"] = "Isidro Merillas"      # nombre corto
    _escribir(tmp_path, "arbol_refinado.json", ref)
    _escribir(tmp_path, "arbol_hallazgos.json", _hallazgos())
    res = cometer_confirmaciones(aplicar=True)
    assert res["evidencias"] == 1        # llegó por persona_id difuso


def test_hallazgo_no_verificado_no_entra(tmp_path, monkeypatch):
    """Filtro de evidencia: sin verificacion_cita VERIFICADA no hay commit
    (la puerta de entrada al árbol sigue siendo la cita en el original)."""
    monkeypatch.setattr(frontera, "BASE_DIR", tmp_path)
    _escribir(tmp_path, "familia_conocida.json", _familia())
    _escribir(tmp_path, "arbol_refinado.json", _refinado())
    hs = _hallazgos()
    hs[0]["verificacion_cita"] = "SIN_VERIFICAR"     # cita no encontrada
    _escribir(tmp_path, "arbol_hallazgos.json", hs)
    res = cometer_confirmaciones(aplicar=True)
    assert res["evidencias"] == 0 and res["nuevas"] == 0
    fam = json.loads((tmp_path / "familia_conocida.json").read_text("utf-8"))
    assert len(fam["personas"]) == 1      # nada cambió
