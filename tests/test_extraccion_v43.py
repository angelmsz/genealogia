"""
tests/test_extraccion_v43.py — La extracción de hallazgos lee el TEXTO
ORIGINAL (v4.3), que es justo el texto contra el que la auditoría de
citas comprueba la cita_literal.

v4.2 extraía del RESUMEN que hizo el LLM de filtrado (texto_limpio):
  - citas 'reformateadas' por el filtro -> la auditoría contra el
    original las rechazaba -> hallazgos legítimos quedaban
    SIN_VERIFICAR y fuera del árbol;
  - datos perdidos: el resumen guarda 6000 car., el original 24000.
"""

from __future__ import annotations

import agent.fase2 as fase2

URL = "http://archivodeejemplo.es/libro/23"

# Texto ORIGINAL (lo que se descargó de verdad de la web):
ORIGINAL = ("Libro de bautismos de la parroquia de San Juan de Salas folio "
            "23. En dicho dia dos de mayo de mil ochocientos setenta yo el "
            "cura bautice a Isidro Merillas Panero hijo legítimo de Nazario "
            "Merillas y de Obdulia Pelaz naturales de este pueblo fueron "
            "padrinos Juan Fernandez y Maria Diez vecinos de Villarcayo")

# RESUMEN que hizo el LLM de filtrado (parafraseado, como hace un resumen):
LIMPIO = ("Bautismo de Isidro Merillas Panero, hijo de Nazario Merillas y "
          "Obdulia Pelaz, en Salas el 2 de mayo de 1870. " * 3)

# Frase que SOLO está en el original (cita literal correcta):
CITA_CORRECTA = ("yo el cura bautice a Isidro Merillas Panero hijo legítimo "
                 "de Nazario Merillas y de Obdulia Pelaz")

# Frase 'reformateada' que SOLO está en el resumen (la que producía la
# extracción v4.2 y la auditoría rechazaba):
CITA_RESUMIDA = ("Bautismo de Isidro Merillas Panero, hijo de Nazario "
                 "Merillas y Obdulia Pelaz, en Salas el 2 de mayo de 1870")


def _fragmento():
    return [{"hash": "h123", "persona": "Isidro Merillas Panero",
             "url": URL, "texto_limpio": LIMPIO,
             "texto_original": ORIGINAL}]


def _test_extrae_y_audita(cita_del_modelo):
    """Lanza extracción (chat_json simulado que devuelve la cita indicada)
    y audita después: devuelve el hallazgo ya auditado."""
    hallazgo = {
        "persona": "Isidro Merillas Panero", "tipo_evento": "bautismo",
        "fecha_valor": "1870-05-02", "fecha_precision": "exacta",
        "fecha_original": "dos de mayo de mil ochocientos setenta",
        "lugar": "Salas", "otros_nombres": [],
        "cita_literal": cita_del_modelo, "url_fuente": URL,
        "confianza": "alta", "justificacion": "partida literal",
    }

    payload_capturado: dict = {}

    def _chat_json(modelo, system, user, **kw):
        if "genealogista experto analizando" in system:
            payload_capturado["user"] = user
            return {"hallazgos": [hallazgo]}
        raise AssertionError(f"prompt inesperado: {system[:60]}")

    fase2.chat_json = _chat_json
    hallazgos = fase2.extraer_hallazgos(_fragmento(), conn=None)
    return hallazgos, payload_capturado


def test_la_extraccion_recibe_el_texto_original():
    """v4.3: el payload del LLM de extracción lleva el ORIGINAL (con la
    frase literal del acta), no solo el resumen del filtro."""
    hallazgos, payload = _test_extrae_y_audita(CITA_CORRECTA)
    assert payload, "el LLM de extracción no fue llamado"
    user = payload["user"]
    assert "yo el cura bautice" in user, \
        "el ORIGINAL debe llegar al extractor (v4.3)"
    assert ORIGINAL[:80] in user


def test_cita_literal_del_original_pasa_la_auditoria():
    """Pipeline coherente v4.3: cita extraída del original -> auditoría
    determinista la VERIFICA (en v4.2, la cita salía del resumen y la
    misma auditoría la rechazaba)."""
    hallazgos, _ = _test_extrae_y_audita(CITA_CORRECTA)
    assert hallazgos and hallazgos[0]["cita_literal"] == CITA_CORRECTA
    fase2.auditar_citas(hallazgos, _fragmento())
    assert hallazgos[0]["verificacion_cita"] == "VERIFICADA"
    assert hallazgos[0].get("verificacion_contra") == "texto_original"


def test_cita_del_resumen_siguen_rechazandose():
    """La defensa de la v4.2 se mantiene: una cita que NO está en el
    original (solo en el resumen) NO se verifica."""
    hallazgos, _ = _test_extrae_y_audita(CITA_RESUMIDA)
    fase2.chat_json = lambda *a, **kw: {
        "resultados": [{"indice": 0, "aparece": False}]}
    fase2.auditar_citas(hallazgos, _fragmento())
    assert hallazgos[0]["verificacion_cita"] == "SIN_VERIFICAR"


def test_fallback_a_resumen_para_corpus_prev43():
    """Fragmentos guardados antes de la v4.3 sin texto_original siguen
    extrayéndose del texto_limpio (compatibilidad)."""
    frag = [{"hash": "h124", "persona": "X", "url": URL,
             "texto_limpio": LIMPIO}]
    capturado: dict = {}

    def _chat_json(modelo, system, user, **kw):
        if "genealogista experto analizando" in system:
            capturado["user"] = user
            return {"hallazgos": []}
        raise AssertionError("prompt inesperado")

    fase2.chat_json = _chat_json
    fase2.extraer_hallazgos(frag, conn=None)
    assert LIMPIO[:80] in capturado["user"]


# ================== v9.1 — NIVEL DE EVIDENCIA EN LA EXTRACCIÓN ==============
# (añadidos por la PARTE 0: los tests de arriba no se tocan)

def test_el_prompt_de_extraccion_clasifica_por_nivel():
    """El prompt pide al LLM clasificar cada hallazgo (con las 3 clases
    exactas) y justificar los 2+ datos independientes; el ancla
    'genealogista experto analizando' sigue igual para el dispatch."""
    from config import SYSTEM_PROMPT_HALLAZGOS
    assert "genealogista experto analizando" in SYSTEM_PROMPT_HALLAZGOS
    for nivel in ("confirmado", "candidato_fuerte", "coincidencia_debil"):
        assert nivel in SYSTEM_PROMPT_HALLAZGOS
    assert "datos_que_casan" in SYSTEM_PROMPT_HALLAZGOS


def test_el_schema_exige_nivel_y_datos_al_llm():
    """JSON_SCHEMA_HALLAZGOS incluye los campos nuevos como requeridos
    (el clasificador determinista los recalcula después; el valor del
    LLM es razonamiento de extracción, no fuente de verdad)."""
    from config import JSON_SCHEMA_HALLAZGOS
    item = JSON_SCHEMA_HALLAZGOS["properties"]["hallazgos"]["items"]
    assert "nivel_evidencia" in item["required"]
    assert "datos_que_casan" in item["required"]


def test_la_extraccion_deja_pasar_el_campo_del_llm_sin_tocarlo():
    """extraer_hallazgos NO clasifica (lo hace el pipeline después, con
    familia): el valor que trae el LLM pasa intacto hasta que el
    clasificador determinista lo sobrescribe."""
    hallazgo = {
        "persona": "Isidro Merillas Panero", "tipo_evento": "bautismo",
        "fecha_valor": "1870-05-02", "fecha_precision": "exacta",
        "fecha_original": "dos de mayo", "lugar": "Salas",
        "otros_nombres": [], "cita_literal": CITA_CORRECTA,
        "url_fuente": URL, "confianza": "alta",
        "justificacion": "partida literal",
        "nivel_evidencia": "candidato_fuerte",
        "datos_que_casan": [],
    }

    def _chat_json(modelo, system, user, **kw):
        return {"hallazgos": [hallazgo]}

    fase2.chat_json = _chat_json
    hallazgos = fase2.extraer_hallazgos(_fragmento(), conn=None)
    assert hallazgos[0]["nivel_evidencia"] == "candidato_fuerte"
    # ... y el clasificador determinista prevalece al invocarlo:
    from agent.evidencia import clasificar_hallazgo
    fam = {"personas": [{
        "nombre": "Isidro Merillas Panero", "padre": "Nazario Merillas",
        "madre": "Obdulia Pelaz",
        "nacimiento": {"fecha_aproximada": "1870", "municipio": "Salas"}}]}
    clasificar_hallazgo(hallazgos[0], fam)
    assert hallazgos[0]["nivel_evidencia"] == "confirmado"
    assert len(hallazgos[0]["datos_que_casan"]) >= 2
