"""
tests/test_proteccion_estado_v105.py — Red de seguridad de los ficheros de
estado: `.bak` antes de escribir y un resultado VACÍO nunca pisa contenido
(v10.4.2).

POR QUÉ (incidente del 13/09/2026)
----------------------------------
Una ejecución de fase 2, con la caché de extracción envenenada, escribió ``[]``
encima de `arbol_hallazgos.json` y los **58 hallazgos** de la noche se
perdieron. En el proyecto no había ninguna copia (los JSON de estado están en
.gitignore), así que solo se recuperaron porque el usuario tenía un respaldo en
la otra máquina.

Ahora, antes de que el bot escriba cualquiera de los tres ficheros de estado
irremplazables (`arbol_hallazgos.json`, `arbol_refinado.json` y `arbol.ged`):

  1. la versión anterior queda como `fichero.bak`;
  2. si el resultado es VACÍO y el fichero que hay SÍ tiene contenido, NO se
     sobrescribe: se avisa y se conserva lo anterior.

Todos los tests trabajan sobre un BASE_DIR TEMPORAL (monkeypatcheado): el disco
del usuario no se toca (ver tests/harness_aislado.py).

Ejecución:  python -m pytest tests/test_proteccion_estado_v105.py -q
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import agent.fase1 as fase1
import agent.fase2 as fase2
import agent.gedcom as gedcom
import config
from tests.harness_aislado import copiar_entradas, huellas_de_estado

HALLAZGOS = "arbol_hallazgos.json"
REFINADO = "arbol_refinado.json"
GED = "arbol.ged"


# ============================ escribir_con_backup ==========================

def test_escribir_con_backup_guarda_el_anterior(tmp_path):
    ruta = tmp_path / "estado.json"
    ruta.write_text("[]", encoding="utf-8")

    respaldo = config.escribir_con_backup(ruta, '[{"a": 1}]')

    assert ruta.read_text(encoding="utf-8") == '[{"a": 1}]'
    assert respaldo is not None
    assert (tmp_path / "estado.json.bak").read_text(encoding="utf-8") == "[]"


def test_escribir_con_backup_sin_fichero_previo(tmp_path):
    ruta = tmp_path / "nuevo.json"
    assert config.escribir_con_backup(ruta, "[]") is None
    assert not (tmp_path / "nuevo.json.bak").exists()
    assert ruta.read_text(encoding="utf-8") == "[]"


def test_tenia_contenido(tmp_path):
    assert config.tenia_contenido([1]) is True
    assert config.tenia_contenido([]) is False
    assert config.tenia_contenido({"a": 1}) is True
    assert config.tenia_contenido({}) is False


# ==================== _guardar_json: protección ante vacío ==================

def test_guardar_json_no_pisa_con_vacio(tmp_path, capsys):
    ruta = tmp_path / HALLAZGOS
    ruta.write_text(json.dumps([{"persona": f"P{i}"} for i in range(58)]),
                    encoding="utf-8")

    escrito = fase2._guardar_json(ruta, [], HALLAZGOS)

    assert escrito is False
    assert len(json.loads(ruta.read_text(encoding="utf-8"))) == 58
    assert "PROTECCIÓN" in capsys.readouterr().out


def test_guardar_json_si_escribe_con_contenido(tmp_path):
    ruta = tmp_path / HALLAZGOS
    ruta.write_text(json.dumps([{"persona": "viejo"}]), encoding="utf-8")

    assert fase2._guardar_json(ruta, [{"persona": "nuevo"}], HALLAZGOS) is True

    assert json.loads(ruta.read_text(encoding="utf-8")) == [{"persona": "nuevo"}]
    respaldo = json.loads((tmp_path / (HALLAZGOS + ".bak"))
                          .read_text(encoding="utf-8"))
    assert respaldo == [{"persona": "viejo"}]


def test_guardar_json_vacio_sobre_fichero_vacio_si_escribe(tmp_path):
    """Si no había nada, dejar el fichero vacío no pierde nada."""
    ruta = tmp_path / HALLAZGOS
    ruta.write_text("[]", encoding="utf-8")
    assert fase2._guardar_json(ruta, [], HALLAZGOS) is True


# ============ EL INCIDENTE, DE PUNTA A PUNTA: fase 2 no borra nada =========

def _entorno_fase2(tmp_path, monkeypatch, n_hallazgos=58):
    """BASE_DIR temporal con familia/corpus y un arbol_hallazgos.json previo."""
    copiar_entradas(tmp_path)
    (tmp_path / HALLAZGOS).write_text(
        json.dumps([{"persona": f"Hallazgo previo {i}"}
                    for i in range(n_hallazgos)], ensure_ascii=False),
        encoding="utf-8")
    for modulo in (fase2, fase1, gedcom):
        monkeypatch.setattr(modulo, "BASE_DIR", tmp_path)
    return tmp_path


def test_fase2_con_extraccion_vacia_no_borra_los_hallazgos_previos(
        tmp_path, monkeypatch, capsys):
    """La reproducción del 13/09: extracción que devuelve 0 hallazgos sobre un
    arbol_hallazgos.json con contenido. Ahora el fichero SE CONSERVA."""
    antes_proyecto = huellas_de_estado()
    _entorno_fase2(tmp_path, monkeypatch)
    # El modelo responde "aquí no hay nada" en todos los lotes.
    monkeypatch.setattr(fase2, "chat_json", lambda *a, **k: {"hallazgos": []})

    fase2.fase2(SimpleNamespace(sin_cache=False), conn=None)

    salida = capsys.readouterr().out
    assert "0 hallazgos extraídos" in salida
    assert "PROTECCIÓN" in salida
    guardados = json.loads((tmp_path / HALLAZGOS).read_text(encoding="utf-8"))
    assert len(guardados) == 58, "el incidente del 13/09 ha vuelto"
    # Y las salidas nuevas (árbol refinado + GEDCOM) sí se han escrito.
    assert (tmp_path / REFINADO).exists()
    assert (tmp_path / GED).exists()
    # Nada del proyecto real se ha tocado.
    assert huellas_de_estado() == antes_proyecto


def test_gedcom_guarda_el_anterior_como_bak(tmp_path, monkeypatch, capsys):
    _entorno_fase2(tmp_path, monkeypatch)
    (tmp_path / GED).write_text("0 HEAD\n1 NOTE arbol de ayer\n0 TRLR\n",
                                encoding="utf-8")

    gedcom.exportar_gedcom()

    nuevo = (tmp_path / GED).read_text(encoding="utf-8")
    anterior = (tmp_path / (GED + ".bak")).read_text(encoding="utf-8")
    assert "arbol de ayer" in anterior
    assert "arbol de ayer" not in nuevo
    assert "0 TRLR" in nuevo
    assert "copia de seguridad" in capsys.readouterr().out


def test_las_escrituras_de_estado_ya_no_van_directas():
    """Ninguna de las tres escrituras usa open(..., 'w') a pelo: todas pasan
    por la red de seguridad."""
    fuente_fase2 = Path(fase2.__file__).read_text(encoding="utf-8")
    assert 'open(BASE_DIR / HALLAZGOS_JSON, "w"' not in fuente_fase2
    assert 'open(BASE_DIR / REFINADO_JSON, "w"' not in fuente_fase2
    fuente_gedcom = Path(gedcom.__file__).read_text(encoding="utf-8")
    assert 'open(BASE_DIR / salida, "w"' not in fuente_gedcom
