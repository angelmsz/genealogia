"""
tests/test_nivel_evidencia.py — v9.1 PARTE 0: el método de evidencia.

PROBLEMA DE MÉTODO que corrige (caso real del usuario): de 716 hallazgos
de una ejecución, solo 1 era confirmación documental real; los otros 715
eran coincidencias de apellido+zona presentadas como progreso.

Regla implementada (agent/evidencia.py, determinista, prevalece sobre
el LLM): cada generación conecta con la siguiente mediante >=2 datos
INDEPENDIENTES que coincidan. Nunca apellido+geografía como prueba.
"""

from __future__ import annotations

import json

import agent.fase2 as fase2
import agent.frontera as frontera
from agent.evidencia import (NIVEL_CANDIDATO_FUERTE, NIVEL_COINCIDENCIA_DEBIL,
                             NIVEL_CONFIRMADO, clasificar_hallazgo,
                             clasificar_hallazgos, nivel_de_persona,
                             reclasificar_arbol, secciones_evidencia,
                             texto_resumen_evidencia)
from config import (JSON_SCHEMA_HALLAZGOS, SYSTEM_PROMPT_HALLAZGOS)

URL = "http://archivodeejemplo.es/partida/1"
CITA = "yo el cura bautice a Isidro Merillas Panero hijo de Nazario"


def _familia() -> dict:
    return {"personas": [{
        "id": "P0001", "nombre": "Isidro Merillas Panero",
        "apellido_paterno": "Merillas", "apellido_materno": "Panero",
        "padre": "Nazario Merillas", "madre": "Obdulia Pelaz",
        "nacimiento": {"fecha_aproximada": "1870", "municipio": "Salas",
                       "provincia": "Burgos"},
        "defuncion": {},
    }]}


def _hallazgo(**extra) -> dict:
    h = {"persona": "Isidro Merillas Panero", "tipo_evento": "bautismo",
         "fecha_valor": "1870-05-02", "fecha_precision": "exacta",
         "lugar": "Salas", "otros_nombres": [], "cita_literal": CITA,
         "url_fuente": URL, "confianza": "alta",
         "justificacion": "partida literal"}
    h.update(extra)
    return h


# ======================= PROMPT Y SCHEMA (config.py) ========================

def test_prompt_mantiene_la_frase_de_los_tests_viejos():
    """Los tests v4.x despachan por 'genealogista experto analizando':
    la sección nueva NO puede romper ese ancla (compatibilidad)."""
    assert "genealogista experto analizando" in SYSTEM_PROMPT_HALLAZGOS


def test_prompt_exige_clasificacion_con_los_3_niveles():
    """El prompt pide clasificar cada hallazgo y justificar los 2+ datos."""
    for nivel in ("confirmado", "candidato_fuerte", "coincidencia_debil"):
        assert nivel in SYSTEM_PROMPT_HALLAZGOS
    assert "2 datos" in SYSTEM_PROMPT_HALLAZGOS.replace(">=2", "2") \
        or "datos INDEPENDIENTES" in SYSTEM_PROMPT_HALLAZGOS
    # la regla de método, explícita
    assert "apellido+geografía" in SYSTEM_PROMPT_HALLAZGOS


def test_schema_pide_nivel_evidencia_y_datos():
    props = JSON_SCHEMA_HALLAZGOS["properties"]["hallazgos"]["items"]
    assert "nivel_evidencia" in props["properties"]
    assert "datos_que_casan" in props["properties"]
    assert "nivel_evidencia" in props["required"]
    enum = props["properties"]["nivel_evidencia"]["enum"]
    assert set(enum) == {"confirmado", "candidato_fuerte",
                         "coincidencia_debil"}


# ======================= CLASIFICADOR: CONFIRMADO ===========================

def test_confirmado_por_nombre_y_fecha():
    h = clasificar_hallazgo(_hallazgo(), _familia())
    assert h["nivel_evidencia"] == NIVEL_CONFIRMADO
    assert len(h["datos_que_casan"]) >= 2
    assert any("fecha" in d for d in h["datos_que_casan"])


def test_confirmado_por_nombre_y_lugar_sin_fecha_en_hallazgo():
    h = clasificar_hallazgo(_hallazgo(fecha_valor="sin fecha legible",
                                      fecha_precision="desconocida"),
                            _familia())
    assert h["nivel_evidencia"] == NIVEL_CONFIRMADO
    assert any("lugar" in d for d in h["datos_que_casan"])


def test_confirmado_por_padres_en_otros_nombres():
    h = clasificar_hallazgo(
        _hallazgo(otros_nombres=["Nazario Merillas (padre)",
                                 "Obdulia Pelaz (madre)"],
                  lugar="",
                  fecha_valor="1870"),
        _familia())
    assert h["nivel_evidencia"] == NIVEL_CONFIRMADO
    assert any("padre" in d or "madre" in d for d in h["datos_que_casan"])


def test_otros_nombres_con_el_propio_nombre_no_cuenta():
    """GUARD anti-circular: la fila del índice que repite el nombre del
    bautizado ('hijo de Isidro Merillas Panero' cuando la ficha no tiene
    padres) NO aporta un dato independiente."""
    fam = _familia()
    fam["personas"][0]["padre"] = ""
    fam["personas"][0]["madre"] = ""
    # la ficha declara como cónyuge a la MISMA persona del hallazgo
    fam["personas"][0]["conyuge"] = "Isidro Merillas Panero"
    h = clasificar_hallazgo(
        _hallazgo(otros_nombres=["Isidro Merillas Panero (cónyuge)"],
                  lugar="", fecha_valor=""),
        fam)
    assert h["nivel_evidencia"] != NIVEL_CONFIRMADO


def test_el_lugar_del_hallazgo_no_se_compara_consigo_mismo():
    """GUARD anti-circular de lugar: sin municipio conocido en la ficha,
    el lugar del hallazgo NO puede confirmarse a sí mismo."""
    fam = _familia()
    fam["personas"][0]["nacimiento"]["municipio"] = ""
    h = clasificar_hallazgo(_hallazgo(fecha_valor=""), fam)
    assert h["nivel_evidencia"] == NIVEL_CANDIDATO_FUERTE


# ======================= GUARDES DE CONTRADICCIÓN ===========================

def test_tocayo_54_anos_off_no_es_confirmado():
    """El fallo estrella: nombre idéntico + fecha contradictoria. En la
    ejecución real confirmaba por apellido+geografía."""
    h = clasificar_hallazgo(_hallazgo(fecha_valor="1816-05-02"), _familia())
    assert h["nivel_evidencia"] == NIVEL_COINCIDENCIA_DEBIL
    assert "contradice" in h["justificacion_evidencia"]
    assert h["datos_que_casan"] == []


def test_defuncion_antes_de_nacer_es_debil():
    h = clasificar_hallazgo(_hallazgo(tipo_evento="defuncion",
                                      fecha_valor="1860"), _familia())
    assert h["nivel_evidencia"] == NIVEL_COINCIDENCIA_DEBIL


def test_posible_homonimo_nunca_es_confirmado():
    """La plausibilidad biológica marcó contradicción real: el
    clasificador la respeta aunque nombre+fecha casen."""
    h = clasificar_hallazgo(_hallazgo(posible_homonimo=True), _familia())
    assert h["nivel_evidencia"] != NIVEL_CONFIRMADO


def test_defuncion_plausible_si_confirma():
    """Una defunción a los 78 años del nacimiento conocido SÍ es segundo
    dato (no todo fuera de ±8 es contradicción: depende del tipo)."""
    h = clasificar_hallazgo(_hallazgo(tipo_evento="defuncion",
                                      fecha_valor="1948-01-10"), _familia())
    assert h["nivel_evidencia"] == NIVEL_CONFIRMADO


# ================== PERSONA DESCONOCIDA: FUERTE vs DÉBIL ====================

def test_candidato_fuerte_apellido_compuesto_municipio_y_fecha():
    """Persona nueva con 2 apellidos de la familia + municipio exacto +
    fecha coherente con la generación."""
    h = clasificar_hallazgo(
        _hallazgo(persona="Nazario Merillas Pelaz",
                  tipo_evento="nacimiento", fecha_valor="1845",
                  lugar="Salas"),
        _familia())
    assert h["nivel_evidencia"] == NIVEL_CANDIDATO_FUERTE


def test_solo_apellido_y_zona_ampla_es_debil():
    h = clasificar_hallazgo(
        _hallazgo(persona="Fulgencio Merillas", tipo_evento="mencion",
                  fecha_valor="1880", lugar="Provincia de Burgos"),
        _familia())
    assert h["nivel_evidencia"] == NIVEL_COINCIDENCIA_DEBIL


def test_apellido_compuesto_pero_fecha_incoherente_es_debil():
    """300 años antes de la línea: ni compuesto+municipio salvan al
    hallazgo (el rango de fechas debe ser coherente con la generación)."""
    h = clasificar_hallazgo(
        _hallazgo(persona="Nazario Merillas Pelaz",
                  tipo_evento="nacimiento", fecha_valor="1505",
                  lugar="Salas"),
        _familia())
    assert h["nivel_evidencia"] == NIVEL_COINCIDENCIA_DEBIL


def test_sin_fecha_no_hay_candidato_fuerte():
    h = clasificar_hallazgo(
        _hallazgo(persona="Nazario Merillas Pelaz", fecha_valor="",
                  lugar="Salas"),
        _familia())
    assert h["nivel_evidencia"] != NIVEL_CANDIDATO_FUERTE


# ======================= DETERMINISMO (PREVALECE AL LLM) ====================

def test_el_clasificador_prevalece_sobre_el_llm():
    """El LLM dice 'confirmado' sin base: el clasificador determinista lo
    degrada. La fuente de verdad es el código, no el modelo."""
    h = _hallazgo(persona="Fulgencio Merillas", tipo_evento="mencion",
                  lugar="Provincia de Burgos",
                  nivel_evidencia=NIVEL_CONFIRMADO,      # alucinación del LLM
                  datos_que_casan=["porque sí"])
    clasificar_hallazgo(h, _familia())
    assert h["nivel_evidencia"] == NIVEL_COINCIDENCIA_DEBIL
    assert h["datos_que_casan"] == []


def test_recuento_y_valor_desconocido_se_degrada():
    hs = [_hallazgo(),
          _hallazgo(persona="Fulano Merillas", tipo_evento="mencion",
                    lugar="Burgos"),
          _hallazgo(persona="X", tipo_evento="mencion",
                    nivel_evidencia="seguro_al_100")]
    r = clasificar_hallazgos(hs, _familia())
    assert r[NIVEL_CONFIRMADO] == 1
    assert r[NIVEL_COINCIDENCIA_DEBIL] == 2
    assert hs[2]["nivel_evidencia"] == NIVEL_COINCIDENCIA_DEBIL


# ======================= RESÚMENES Y ÁRBOL ==================================

def test_texto_resumen_con_las_3_secciones():
    hs = [clasificar_hallazgo(_hallazgo(), _familia())]
    texto = texto_resumen_evidencia(hs)
    assert "Confirmado documentalmente" in texto
    assert "Candidatos fuertes a verificar" in texto
    assert "Coincidencias débiles" in texto


def test_secciones_reparten_correctamente():
    hs = [clasificar_hallazgo(_hallazgo(), _familia()),
          clasificar_hallazgo(_hallazgo(persona="Fulgencio Merillas",
                                        tipo_evento="mencion",
                                        lugar="Burgos"), _familia())]
    secs = secciones_evidencia(hs)
    assert len(secs[NIVEL_CONFIRMADO]) == 1
    assert secs[NIVEL_CONFIRMADO][0]["datos_que_casan"]
    assert len(secs[NIVEL_COINCIDENCIA_DEBIL]) == 1


def test_caso_estrella_716_hallazgos():
    """Reproducción del caso real: 716 hallazgos de los que SOLO 1 era
    confirmación documental. Ahora la señal queda separada del ruido."""
    fam = _familia()
    hs = [_hallazgo()]          # el único real
    for i in range(715):
        hs.append(_hallazgo(persona=f"Fulano{i} Merillas",
                            tipo_evento="mencion",
                            fecha_valor=str(1700 + (i % 260)),
                            lugar="Provincia de Burgos"))
    r = clasificar_hallazgos(hs, fam)
    assert r == {NIVEL_CONFIRMADO: 1, NIVEL_CANDIDATO_FUERTE: 0,
                 NIVEL_COINCIDENCIA_DEBIL: 715}


def test_nivel_de_persona_para_candidatas():
    hs = [clasificar_hallazgo(_hallazgo(persona="Nazario Merillas Pelaz",
                                        tipo_evento="nacimiento",
                                        fecha_valor="1845",
                                        lugar="Salas"), _familia())]
    assert nivel_de_persona("Nazario Merillas Pelaz", hs) == NIVEL_CANDIDATO_FUERTE
    assert nivel_de_persona("Desconocido Cualquiera", hs) is None


def test_reclasificar_arbol_existente():
    """El árbol de la ejecución real (sin re-ejecutar fase 2) se
    reclasifica: candidatas con nivel y resumen con 3 secciones."""
    fam = _familia()
    hs = [clasificar_hallazgo(_hallazgo(), fam)]
    for i in range(5):
        hs.append(clasificar_hallazgo(
            _hallazgo(persona=f"Fulano{i} Merillas", tipo_evento="mencion",
                      lugar="Burgos"), fam))
    arbol = {
        "personas": [],
        "personas_nuevas_candidatas": [
            {"nombre": f"Fulano{i} Merillas", "motivo": "conexión no probada",
             "fuente_url": URL} for i in range(5)],
        "resumen_general": "párrafo del LLM sobre el estado",
    }
    reclasificar_arbol(arbol, hs, fam)
    for c in arbol["personas_nuevas_candidatas"]:
        assert c["nivel_evidencia"] == NIVEL_COINCIDENCIA_DEBIL
    assert "Confirmado documentalmente" in arbol["resumen_general"]
    assert "párrafo del LLM" in arbol["resumen_general"]  # se conserva
    tot = arbol["resumen_evidencia"]["totales"]
    assert tot[NIVEL_CONFIRMADO] == 1
    assert tot[NIVEL_COINCIDENCIA_DEBIL] == 5


def test_aplicar_evidencia_al_arbol_fase2():
    """Post-proceso del consolidado de fase 2: candidatas con nivel,
    resumen_general prefijado y resumen_evidencia estructurado."""
    fam = _familia()
    hs = [clasificar_hallazgo(_hallazgo(), fam)]
    consolidado = {
        "personas": [],
        "personas_nuevas_candidatas": [
            {"nombre": "Fulano Merillas", "motivo": "conexión no probada",
             "fuente_url": URL}],
        "resumen_general": "texto del LLM",
    }
    fase2._aplicar_evidencia_al_arbol(consolidado, hs)
    assert consolidado["personas_nuevas_candidatas"][0][
        "nivel_evidencia"] == NIVEL_COINCIDENCIA_DEBIL
    assert consolidado["resumen_general"].startswith("RESUMEN POR NIVEL")
    assert "Confirmado documentalmente" in consolidado["resumen_general"]
    assert "texto del LLM" in consolidado["resumen_general"]
    assert "resumen_evidencia" in consolidado


# ======================= COMMIT: LOS DÉBILES NO ENTRAN ======================

CITA_COMMIT = ("Isidro Merillas Panero hijo de Nazario Merillas y de "
               "Obdulia Pelaz")


def _familia_commit() -> dict:
    return {"personas": [{
        "id": "P0001", "nombre": "Isidro Merillas Panero",
        "apellido_paterno": "Merillas", "apellido_materno": "Panero",
        "sexo": "M", "nacimiento": {"fecha_aproximada": "1870"},
        "defuncion": {}, "padre": "", "madre": "",
        "estado": "memoria", "evidencias": [],
    }]}


def _refinado_commit() -> dict:
    return {"personas": [{
        "nombre": "Isidro Merillas Panero",
        "eventos_confirmados": [{
            "tipo": "bautismo", "fecha": "02-05-1870",
            "lugar": "Salas de los Infantes", "fuente_url": URL,
            "cita": CITA_COMMIT}],
        "eventos_estimados": [], "contradicciones": [],
        "nuevas_pistas": [],
    }]}


def _hallazgo_commit(**extra) -> dict:
    h = {"persona": "Isidro Merillas", "persona_id": "P0001",
         "tipo_evento": "bautismo", "fecha_valor": "02-05-1870",
         "cita_literal": CITA_COMMIT, "url_fuente": URL,
         "verificacion_cita": "VERIFICADA", "posible_homonimo": False,
         "otros_nombres": ["Nazario Merillas (padre)",
                           "Obdulia Pelaz (madre)"]}
    h.update(extra)
    return h


def _preparar(tmp_path, monkeypatch, hallazgos):
    monkeypatch.setattr(frontera, "BASE_DIR", tmp_path)
    (tmp_path / "familia_conocida.json").write_text(
        json.dumps(_familia_commit(), ensure_ascii=False), encoding="utf-8")
    (tmp_path / "arbol_refinado.json").write_text(
        json.dumps(_refinado_commit(), ensure_ascii=False), encoding="utf-8")
    (tmp_path / "arbol_hallazgos.json").write_text(
        json.dumps(hallazgos, ensure_ascii=False), encoding="utf-8")


def test_commit_los_debiles_no_entran_al_arbol(tmp_path, monkeypatch):
    """PARTE 0 en el COMMIT: un hallazgo coincidencia_debil con cita
    VERIFICADA NO da fe de filiación (la cita prueba que el documento
    existe, no que la persona sea de la familia)."""
    _preparar(tmp_path, monkeypatch,
              [_hallazgo_commit(nivel_evidencia=NIVEL_COINCIDENCIA_DEBIL)])
    res = frontera.cometer_confirmaciones(aplicar=True)
    assert res["evidencias"] == 0
    fam = json.loads((tmp_path / "familia_conocida.json").read_text("utf-8"))
    assert len(fam["personas"]) == 1


def test_commit_confirmado_y_pre_v91_sigen_entrando(tmp_path, monkeypatch):
    """Backward compatible: confirmados entran; hallazgos pre-v9.1 (sin
    campo nivel_evidencia) siguen siendo elegibles (los tests v4.x ya lo
    prueban con la suite original; aquí el confirmado v9.1)."""
    _preparar(tmp_path, monkeypatch,
              [_hallazgo_commit(nivel_evidencia=NIVEL_CONFIRMADO),
               _hallazgo_commit()])   # pre-v9.1: sin el campo
    res = frontera.cometer_confirmaciones(aplicar=True)
    assert res["evidencias"] == 1


def test_commit_propaga_nivel_a_candidatas(tmp_path, monkeypatch):
    """El candidato del estado lleva su nivel de evidencia para que el
    informe de progreso y la frontera separen señal de ruido."""
    refinado = _refinado_commit()
    refinado["personas_nuevas_candidatas"] = [
        {"nombre": "Fulano Merillas", "motivo": "conexión no probada",
         "fuente_url": URL, "nivel_evidencia": NIVEL_CANDIDATO_FUERTE}]
    monkeypatch.setattr(frontera, "BASE_DIR", tmp_path)
    (tmp_path / "familia_conocida.json").write_text(
        json.dumps(_familia_commit(), ensure_ascii=False), encoding="utf-8")
    (tmp_path / "arbol_refinado.json").write_text(
        json.dumps(refinado, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "arbol_hallazgos.json").write_text("[]", encoding="utf-8")
    estado = {"candidatos": []}
    monkeypatch.setattr(frontera, "cargar_estado", lambda: estado)
    monkeypatch.setattr(frontera, "guardar_estado", lambda e: None)
    res = frontera.cometer_confirmaciones(aplicar=True)
    assert res["candidatos"][0]["nivel_evidencia"] == NIVEL_CANDIDATO_FUERTE


# ======================= INFORME DE PROGRESO ================================

def test_informe_progreso_con_las_3_secciones(tmp_path, monkeypatch):
    """generar_informe_progreso separa Confirmado / Candidatos fuertes /
    Coincidencias débiles (además de la tabla de siempre)."""
    monkeypatch.setattr(frontera, "BASE_DIR", tmp_path)
    fam = _familia()
    fam["personas"][0]["evidencias"] = []
    (tmp_path / "familia_conocida.json").write_text(
        json.dumps(fam, ensure_ascii=False), encoding="utf-8")
    hs = [clasificar_hallazgo(_hallazgo(), fam),
          clasificar_hallazgo(_hallazgo(persona="Fulano Merillas",
                                        tipo_evento="mencion",
                                        lugar="Burgos"), fam)]
    (tmp_path / "arbol_hallazgos.json").write_text(
        json.dumps(hs, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(frontera, "cargar_estado",
                        lambda: {"frontera": [], "candidatos": [
                            {"nombre": "Fulano Merillas", "motivo": "x",
                             "fuente_url": URL,
                             "nivel_evidencia": NIVEL_CANDIDATO_FUERTE}]})
    from config import INFORME_PROGRESO_MD
    frontera.generar_informe_progreso()
    texto = (tmp_path / INFORME_PROGRESO_MD).read_text(encoding="utf-8")
    assert "Confirmado documentalmente" in texto
    assert "Candidatos fuertes a verificar" in texto
    assert "Coincidencias débiles" in texto
    assert "(1 hallazgos · 0 candidatas)" in texto
    assert "fuertes: 1" in texto
    # la tabla de líneas de siempre sigue ahí
    assert "| Línea |" in texto
