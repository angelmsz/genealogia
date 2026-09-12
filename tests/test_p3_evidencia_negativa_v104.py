"""
tests/test_p3_evidencia_negativa_v104.py — v10.4 (P3): registro persistente de
búsquedas infructuosas (evidencia negativa).

POR QUÉ (informe de metodología profesional): los genealogistas "registran
búsquedas infructuosas (evidencia negativa)". Nosotros solo teníamos cachés
invisibles (consultas ya hechas, cooldowns de red de 6 h): nada legible que
dijera "a Eusebio Merillas en Salas ya se le buscó y no apareció nada".

LO QUE ESTE FICHERO BLINDA:
  - El registro es append-only y con fecha (un "no encontrado" de hace seis
    meses NO vale lo mismo que el de anoche).
  - Sin nombre no se registra nada ({} y ninguna escritura).
  - Las líneas corruptas se ignoran (es un histórico, no verdad crítica).
  - El informe de progreso lo muestra cuando hay datos y NO cambia cuando no
    los hay (una instalación recién empezada no se rompe).
  - La búsqueda infructuosa se registra desde el bucle real de fase 1.

100% offline: ficheros en un BASE_DIR temporal.
Ejecución:  python -m pytest tests/test_p3_evidencia_negativa_v104.py -q
"""

from __future__ import annotations

import json

import agent.evidencia_negativa as neg
import agent.frontera as frontera


def _ruta_tmp(tmp_path, monkeypatch):
    """El registro resuelve BASE_DIR en su propio módulo (igual que frontera
    o fase1): se redirige a tmp_path."""
    monkeypatch.setattr(neg, "BASE_DIR", tmp_path)


# ============================== REGISTRO ====================================

def test_registrar_y_cargar(tmp_path, monkeypatch):
    _ruta_tmp(tmp_path, monkeypatch)
    e = neg.registrar(ancla="Eusebio Merillas", municipio="Salas",
                      provincia="Burgos", consultas=15,
                      motivo="sin fragmentos relevantes en la web")
    assert e["ancla"] == "Eusebio Merillas"
    assert e["consultas"] == 15
    assert e["ts"]
    entradas = neg.cargar()
    assert len(entradas) == 1
    assert entradas[0]["municipio"] == "Salas"
    assert entradas[0]["provincia"] == "Burgos"
    assert (tmp_path / "evidencia_negativa.jsonl").exists()


def test_sin_ancla_no_registra_nada(tmp_path, monkeypatch):
    _ruta_tmp(tmp_path, monkeypatch)
    assert neg.registrar(ancla="   ") == {}
    assert neg.cargar() == []
    assert not (tmp_path / "evidencia_negativa.jsonl").exists()


def test_append_only_resumen_por_persona(tmp_path, monkeypatch):
    _ruta_tmp(tmp_path, monkeypatch)
    neg.registrar(ancla="Eusebio Merillas", municipio="Salas",
                  motivo="primera pasada")
    neg.registrar(ancla="Eusebio Merillas", municipio="Salas",
                  motivo="segunda pasada")
    neg.registrar(ancla="Otra Persona", municipio="Burgos")
    assert len(neg.cargar()) == 3          # solo crece: nada se sobrescribe
    resumen = neg.resumen_por_ancla()
    assert resumen["eusebio merillas"]["n"] == 2
    assert resumen["eusebio merillas"]["motivo"] == "segunda pasada"
    assert resumen["otra persona"]["n"] == 1


def test_las_lineas_corruptas_se_ignoran(tmp_path, monkeypatch):
    _ruta_tmp(tmp_path, monkeypatch)
    (tmp_path / "evidencia_negativa.jsonl").write_text(
        '{"ancla": "Uno", "ts": "2026-09-12T20:00:00"}\n'
        "esto no es json\n"
        "\n"
        '{"sin_ancla": true}\n'
        '{"ancla": "Dos", "ts": "2026-09-12T21:00:00"}\n',
        encoding="utf-8")
    assert [e["ancla"] for e in neg.cargar()] == ["Uno", "Dos"]


# ============================== INFORME =====================================

def test_texto_markdown_vacio_sin_registro(tmp_path, monkeypatch):
    _ruta_tmp(tmp_path, monkeypatch)
    assert neg.texto_markdown() == ""


def test_texto_markdown_con_datos(tmp_path, monkeypatch):
    _ruta_tmp(tmp_path, monkeypatch)
    neg.registrar(ancla="Eusebio Merillas", municipio="Salas", consultas=7,
                  motivo="sin fragmentos relevantes en la web")
    txt = neg.texto_markdown()
    assert "Búsquedas sin resultado" in txt
    assert "Eusebio Merillas" in txt
    assert "Salas" in txt
    assert "evidencia_negativa.jsonl" in txt


def _familia_minima() -> dict:
    return {"personas": [{"id": "P0001", "nombre": "Isidro Merillas Panero",
                          "apellido_paterno": "Merillas",
                          "apellido_materno": "Panero",
                          "nacimiento": {"fecha_aproximada": "1870"},
                          "defuncion": {}, "hijos": [],
                          "evidencias": []}]}


def _preparar_informe(tmp_path, monkeypatch):
    monkeypatch.setattr(frontera, "BASE_DIR", tmp_path)
    _ruta_tmp(tmp_path, monkeypatch)
    (tmp_path / "familia_conocida.json").write_text(
        json.dumps(_familia_minima(), ensure_ascii=False), encoding="utf-8")
    (tmp_path / "arbol_hallazgos.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(frontera, "cargar_estado",
                        lambda: {"frontera": [], "candidatos": []})


def test_informe_progreso_incluye_la_seccion(tmp_path, monkeypatch):
    _preparar_informe(tmp_path, monkeypatch)
    neg.registrar(ancla="Eusebio Merillas", municipio="Salas", consultas=3,
                  motivo="sin fragmentos relevantes en la web")
    from config import INFORME_PROGRESO_MD
    frontera.generar_informe_progreso()
    texto = (tmp_path / INFORME_PROGRESO_MD).read_text(encoding="utf-8")
    assert "Búsquedas sin resultado" in texto
    assert "Eusebio Merillas" in texto
    assert "| Línea |" in texto            # la tabla de siempre sigue ahí


def test_informe_progreso_sin_registro_no_cambia(tmp_path, monkeypatch):
    _preparar_informe(tmp_path, monkeypatch)
    from config import INFORME_PROGRESO_MD
    frontera.generar_informe_progreso()
    texto = (tmp_path / INFORME_PROGRESO_MD).read_text(encoding="utf-8")
    assert "Búsquedas sin resultado" not in texto
    assert "| Línea |" in texto


# ================== ENGANCHADO AL BUCLE REAL DE FASE 1 ======================

def test_fase1_registra_la_busqueda_infructuosa(tmp_path, monkeypatch):
    """Integración: un objetivo que se ejecuta y no saca ni un fragmento
    queda apuntado en el registro de evidencia negativa."""
    import main as main_mod
    import agent.fase1 as fase1
    from agent.fase1 import ResultadoFase1

    monkeypatch.setattr(main_mod, "BASE_DIR", tmp_path)
    monkeypatch.setattr(fase1, "BASE_DIR", tmp_path)
    _ruta_tmp(tmp_path, monkeypatch)
    monkeypatch.setattr(main_mod, "cargar_corpus", lambda: [])
    monkeypatch.setattr(main_mod, "guardar_corpus", lambda corpus: None)
    monkeypatch.setattr(main_mod, "generar_objetivos_busqueda",
                        lambda **kw: [{"descripcion": "Eusebio Merillas "
                                                       "en Salas",
                                       "nombre": "Eusebio Merillas",
                                       "municipio": "Salas",
                                       "provincia": "Burgos",
                                       "queries": ["x"]}])
    monkeypatch.setattr(
        main_mod, "ejecutar_fase1",
        lambda *a, **kw: ResultadoFase1(apellido="Merillas",
                                        municipio="Salas"))

    args = main_mod.argparse.Namespace(personas="", max_steps=3,
                                       sin_cache=False)
    main_mod.fase1(args, conn=None)

    entradas = neg.cargar()
    assert len(entradas) == 1
    assert entradas[0]["ancla"] == "Eusebio Merillas"
    assert entradas[0]["municipio"] == "Salas"
    assert entradas[0]["motivo"].startswith("sin fragmentos")


def test_fase1_con_fragmentos_no_registra_nada(tmp_path, monkeypatch):
    import main as main_mod
    import agent.fase1 as fase1
    from agent.fase1 import ResultadoFase1

    monkeypatch.setattr(main_mod, "BASE_DIR", tmp_path)
    monkeypatch.setattr(fase1, "BASE_DIR", tmp_path)
    _ruta_tmp(tmp_path, monkeypatch)
    monkeypatch.setattr(main_mod, "cargar_corpus", lambda: [])
    monkeypatch.setattr(main_mod, "guardar_corpus", lambda corpus: None)
    monkeypatch.setattr(main_mod, "generar_objetivos_busqueda",
                        lambda **kw: [{"descripcion": "Eusebio Merillas "
                                                       "en Salas",
                                       "nombre": "Eusebio Merillas",
                                       "municipio": "Salas",
                                       "provincia": "Burgos",
                                       "queries": ["x"]}])

    def _con_fragmentos(*a, **kw):
        r = ResultadoFase1(apellido="Merillas", municipio="Salas")
        r.fragmentos.append({"url": "http://ejemplo.es", "texto_limpio": "algo"})
        return r

    monkeypatch.setattr(main_mod, "ejecutar_fase1", _con_fragmentos)
    args = main_mod.argparse.Namespace(personas="", max_steps=3,
                                       sin_cache=False)
    main_mod.fase1(args, conn=None)
    assert neg.cargar() == []
