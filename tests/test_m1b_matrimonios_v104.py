"""
tests/test_m1b_matrimonios_v104.py — v10.4 (M1b): la partida de MATRIMONIO
también certifica a un hijo/a ausente del árbol.

POR QUÉ (informe de metodología profesional): las partidas de matrimonio
recogen contrayentes, padres, abuelos, padrinos y testigos, igual que un
bautismo. Un hijo/a CASADO suele ser la vía natural de la línea troncal
(de ahí cuelgan las generaciones siguientes), así que la misma regla de dos
datos independientes que la v10.4 aplicó a los bautismos vale aquí.

DOS COSAS QUE ESTE FICHERO BLINDA:
  1. Si el documento nombra a los padres de LOS DOS contrayentes, hay dos
     candidatos distintos para el rol 'padre' -> NO se confirma nada
     (el extractor tolerante nunca adivina).
  2. La fecha de la BODA no es una fecha de NACIMIENTO (el informe insiste
     en no mezclar eventos): va a `matrimonios`, nunca a `nacimiento`.

100% offline: funciones puras + commit sobre un BASE_DIR temporal.
Ejecución:  python -m pytest tests/test_m1b_matrimonios_v104.py -q
"""

from __future__ import annotations

import json

import agent.frontera as frontera
from agent.evidencia import (NIVEL_CONFIRMADO, clasificar_hallazgo,
                             progenitores_confirmados)

URL = "http://archivodeejemplo.es/partida/21"
CITA_ISIDRO = ("Isidro Merillas Panero hijo de Nazario Merillas y de "
               "Obdulia Pelaz")
CITA_BODA = ("En Salas, Juana Merillas Pelaz, hija de Nazario Merillas y de "
             "Obdulia Pelaz, contrajo matrimonio con Torcuato Vega")


# ============================== FIXTURES ===================================

def _ficha(nombre, ap_p, ap_m, nac, sexo, hijos, con_evidencia=True) -> dict:
    return {
        "nombre": nombre, "apellido_paterno": ap_p, "apellido_materno": ap_m,
        "sexo": sexo, "nacimiento": ({"fecha_aproximada": nac} if nac else {}),
        "defuncion": {}, "padre": "", "madre": "", "hijos": hijos,
        "estado": "confirmado" if con_evidencia else "memoria",
        "evidencias": ([{"tipo": "bautismo", "fecha": nac, "lugar": "Salas",
                         "fuente_url": URL, "cita": CITA_ISIDRO,
                         "verificado": True}] if con_evidencia else []),
    }


def _familia(con_evidencia=True) -> dict:
    return {"personas": [
        {"id": "P0001", "nombre": "Isidro Merillas Panero",
         "apellido_paterno": "Merillas", "apellido_materno": "Panero",
         "sexo": "M", "padre": "Nazario Merillas", "madre": "Obdulia Pelaz",
         "nacimiento": {"fecha_aproximada": "1870", "municipio": "Salas"},
         "defuncion": {}, "hijos": ["Isidro Merillas Panero"],
         "estado": "confirmado",
         "evidencias": [{"tipo": "bautismo", "fecha": "02-05-1870",
                         "lugar": "Salas", "fuente_url": URL,
                         "cita": CITA_ISIDRO, "verificado": True}]},
        {"id": "P0002", **_ficha("Nazario Merillas", "Merillas", "", "1845",
                                 "M", ["Isidro Merillas Panero"],
                                 con_evidencia)},
        {"id": "P0003", **_ficha("Obdulia Pelaz", "Pelaz", "", "1850",
                                 "F", ["Isidro Merillas Panero"],
                                 con_evidencia)},
    ]}


def _hallazgo_boda(**extra) -> dict:
    """Boda de Juana Merillas Pelaz (hermana de Isidro, AUSENTE del árbol),
    con sus DOS progenitores ya confirmados."""
    h = {
        "persona": "Juana Merillas Pelaz",
        "tipo_evento": "matrimonio",
        "fecha_valor": "1878-06-20",
        "lugar": "Salas",
        "otros_nombres": ["Nazario Merillas (padre)",
                          "Obdulia Pelaz (madre)",
                          "Torcuato Vega (cónyuge)"],
        "cita_literal": CITA_BODA,
        "url_fuente": URL,
        "confianza": "alta",
        "justificacion": "partida de matrimonio literal",
        "verificacion_cita": "VERIFICADA",
        "posible_homonimo": False,
    }
    h.update(extra)
    return h


# ==================== EL EXTRACTOR Y EL CLASIFICADOR ========================

def test_matrimonio_con_dos_progenitores_confirmados_queda_confirmado():
    h = clasificar_hallazgo(_hallazgo_boda(), _familia())
    assert h["nivel_evidencia"] == NIVEL_CONFIRMADO
    assert len(h["datos_que_casan"]) == 2
    assert any("padre" in d for d in h["datos_que_casan"])
    assert any("madre" in d for d in h["datos_que_casan"])


def test_matrimonio_se_confirma_tambien_por_la_cita():
    """Si el modelo no rellena otros_nombres con los roles, el extractor
    tolerante lee 'hija de X y de Y' de la cita literal."""
    h = clasificar_hallazgo(_hallazgo_boda(otros_nombres=[]), _familia())
    assert h["nivel_evidencia"] == NIVEL_CONFIRMADO


def test_matrimonio_sin_los_padres_no_confirma():
    h = clasificar_hallazgo(
        _hallazgo_boda(otros_nombres=["Torcuato Vega (cónyuge)"],
                       cita_literal="Juana Merillas Pelaz contrajo "
                                    "matrimonio con Torcuato Vega"),
        _familia())
    assert h["nivel_evidencia"] != NIVEL_CONFIRMADO


def test_matrimonio_con_los_padres_de_los_dos_contrayentes_no_confirma():
    """El documento nombra al padre de Juana Y al de Torcuato: dos
    candidatos distintos para el mismo rol -> NUNCA se adivina."""
    h = _hallazgo_boda(otros_nombres=["Nazario Merillas (padre)",
                                      "Domingo Vega (padre)",
                                      "Obdulia Pelaz (madre)"])
    assert progenitores_confirmados(h, _familia()) is None
    assert clasificar_hallazgo(h, _familia())["nivel_evidencia"] \
        != NIVEL_CONFIRMADO


def test_matrimonio_con_progenitores_sin_evidencias_no_confirma():
    h = clasificar_hallazgo(_hallazgo_boda(), _familia(con_evidencia=False))
    assert h["nivel_evidencia"] != NIVEL_CONFIRMADO


def test_una_mencion_sigue_sin_confirmar():
    h = clasificar_hallazgo(_hallazgo_boda(tipo_evento="mencion"), _familia())
    assert h["nivel_evidencia"] != NIVEL_CONFIRMADO


# ==================== COMMIT: LA BODA VA A 'MATRIMONIOS' ====================

def _escribir(tmp_path, nombre, datos):
    (tmp_path / nombre).write_text(json.dumps(datos, ensure_ascii=False,
                                              indent=2), encoding="utf-8")


def _preparar_commit(tmp_path, monkeypatch, hallazgo):
    monkeypatch.setattr(frontera, "BASE_DIR", tmp_path)
    _escribir(tmp_path, "familia_conocida.json", _familia())
    _escribir(tmp_path, "arbol_refinado.json", {
        "personas": [],
        "personas_nuevas_candidatas": [{
            "nombre": hallazgo["persona"],
            "motivo": "hija casada de Nazario y Obdulia",
            "fuente_url": URL,
            "nivel_evidencia": NIVEL_CONFIRMADO}],
    })
    _escribir(tmp_path, "arbol_hallazgos.json", [hallazgo])


def test_commit_crea_la_ficha_y_la_boda_no_es_un_nacimiento(tmp_path,
                                                            monkeypatch):
    """LO IMPORTANTE: la fecha de la boda NO se cuela en `nacimiento`."""
    _preparar_commit(tmp_path, monkeypatch, _hallazgo_boda())
    res = frontera.cometer_confirmaciones(aplicar=True)
    assert res["nuevas"] == 1

    fam = json.loads((tmp_path / "familia_conocida.json").read_text("utf-8"))
    assert len(fam["personas"]) == 4
    juana = next(p for p in fam["personas"]
                 if p["nombre"] == "Juana Merillas Pelaz")
    assert juana["padre"] == "Nazario Merillas"
    assert juana["madre"] == "Obdulia Pelaz"
    assert juana["apellido_paterno"] == "Merillas"
    assert juana["apellido_materno"] == "Pelaz"
    assert juana["estado"] == NIVEL_CONFIRMADO
    assert juana.get("id")
    # la boda va a su sitio, no a nacimiento
    assert not (juana["nacimiento"] or {}).get("fecha_aproximada")
    assert juana["matrimonios"][0]["fecha"] == "1878-06-20"
    assert juana["matrimonios"][0]["lugar"] == "Salas"
    assert juana["matrimonios"][0]["fuente_url"] == URL
    # el cónyuge queda enlazado (el GEDCOM saca su FAM con él)
    assert juana["conyuge"] == "Torcuato Vega"
    # y Juana entra en la lista 'hijos' de sus dos progenitores
    nazario = next(p for p in fam["personas"]
                   if p["nombre"] == "Nazario Merillas")
    assert "Juana Merillas Pelaz" in nazario["hijos"]


def test_commit_de_bautismo_sigue_rellenando_nacimiento(tmp_path,
                                                        monkeypatch):
    """Regresión de M1: un bautismo sí va a `nacimiento` (y deja
    `matrimonios` vacío)."""
    bautismo = _hallazgo_boda(tipo_evento="bautismo",
                              fecha_valor="1872-03-14",
                              otros_nombres=["Nazario Merillas (padre)",
                                             "Obdulia Pelaz (madre)"],
                              cita_literal="Juana Merillas Pelaz hija de "
                                           "Nazario Merillas y de Obdulia "
                                           "Pelaz")
    _preparar_commit(tmp_path, monkeypatch, bautismo)
    assert frontera.cometer_confirmaciones(aplicar=True)["nuevas"] == 1
    fam = json.loads((tmp_path / "familia_conocida.json").read_text("utf-8"))
    juana = next(p for p in fam["personas"]
                 if p["nombre"] == "Juana Merillas Pelaz")
    assert juana["nacimiento"]["fecha_aproximada"] == "1872-03-14"
    assert juana["nacimiento"]["municipio"] == "Salas"
    assert juana["matrimonios"] == []
    assert juana["conyuge"] == ""
