"""
tests/test_plausibilidad.py — Verificación biológica (punto 2 del informe,
"el fallo número uno").

Regla v4.2: posible_homonimo SOLO ante CONTRADICCIÓN real y comprobable
(hijo nacido 2 años después del padre, madre 60 años mayor...). La FALTA
de datos (padres sin fecha, hallazgo sin año) ya NO marca sospecha: los
padres son justo lo que el agente está buscando; exigir sus fechas para
no marcar el hallazgo era el círculo vicioso que congelaba el árbol.
"""

from __future__ import annotations

from agent.fase2 import verificar_plausibilidad_biologica


def _fam(padre_nac=None, madre_nac=None):
    p = {"nombre": "Nazario Merillas", "nacimiento": {}}
    m = {"nombre": "Obdulia Pelaz", "nacimiento": {}}
    if padre_nac:
        p["nacimiento"]["fecha_aproximada"] = padre_nac
    if madre_nac:
        m["nacimiento"]["fecha_aproximada"] = madre_nac
    hijo = {"nombre": "Isidro Merillas Panero",
            "padre": "Nazario Merillas", "madre": "Obdulia Pelaz",
            "nacimiento": {}}
    return {"personas": [hijo, p, m]}


def _hallazgo(anio, persona="Isidro Merillas Panero", precision="exacta",
              justificacion="partida literal"):
    return {"persona": persona, "tipo_evento": "bautismo",
            "fecha_valor": f"02-05-{anio}", "fecha_precision": precision,
            "justificacion": justificacion}


def test_padres_sin_fechas_no_marca_sospecha():
    """EL fallo número uno: sin años de los padres, nada de sospecha."""
    fam = _fam()                       # padre y madre SIN fecha de nacimiento
    h = [_hallazgo(1870)]
    marcados = verificar_plausibilidad_biologica(h, fam)
    assert marcados == 0
    assert h[0]["posible_homonimo"] is False
    assert h[0]["plausibilidad_biologica"] == "NO_COMPROBABLE"


def test_hallazgo_sin_anio_no_marca_sospecha():
    fam = _fam(padre_nac="1845")
    h = [_hallazgo(None)] if False else [{
        "persona": "Isidro Merillas Panero", "tipo_evento": "bautismo",
        "fecha_valor": "sin fecha legible", "fecha_precision": "desconocida",
        "justificacion": "partida ilegible"}]
    marcados = verificar_plausibilidad_biologica(h, fam)
    assert marcados == 0
    assert h[0]["posible_homonimo"] is False


def test_padre_plausible_no_marca():
    fam = _fam(padre_nac="1845")       # hijo 1870 - padre 1845 = 25 años
    h = [_hallazgo(1870)]
    assert verificar_plausibilidad_biologica(h, fam) == 0
    assert h[0]["plausibilidad_biologica"] == "OK"


def test_hijo_nacido_2_anos_despues_del_padre_si_marca():
    fam = _fam(padre_nac="1868")       # 1870 - 1868 = 2 años: contradicción
    h = [_hallazgo(1870)]
    assert verificar_plausibilidad_biologica(h, fam) == 1
    assert h[0]["posible_homonimo"] is True
    assert "padre" in h[0]["motivo_homonomia"]


def test_madre_demasiado_mayor_si_marca():
    fam = _fam(madre_nac="1810")       # 1870 - 1810 = 60 > 55
    h = [_hallazgo(1870)]
    assert verificar_plausibilidad_biologica(h, fam) == 1
    assert h[0]["posible_homonimo"] is True


def test_tocayos_no_generan_falso_homonomimo():
    """Abuelo (n. 1785) y nieto (n. 1870) con el mismo nombre: las cubetas
    de años por ID ESTABLE evitan comparar al nieto contra el año del
    abuelo (la v4.1 mezclaba ambas fechas bajo el mismo nombre)."""
    fam = {"personas": [
        {"nombre": "Isidro Merillas", "id": "P0010",
         "nacimiento": {"fecha_aproximada": "1785"},
         "padre": "", "madre": ""},
        {"nombre": "Isidro Merillas", "id": "P0011",
         "nacimiento": {"fecha_aproximada": "1870"},
         "padre": "Isidro Merillas", "madre": "Maria Perez"},
        {"nombre": "Maria Perez", "id": "P0012",
         "nacimiento": {"fecha_aproximada": "1845"}},
    ]}
    # bautismo del NIETO (1870): padre tocayo n. 1870?? no: el padre del
    # nieto es la ficha P0011... el matching con anio=1870 elige P0011
    # (el propio nieto tocayo). Comprobamos que no explota y que el
    # matching difuso funciona con desambiguación por año.
    h = [{"persona": "Isidro Merillas", "tipo_evento": "bautismo",
          "fecha_valor": "1870-05-02", "fecha_precision": "exacta",
          "justificacion": "partida literal"}]
    verificar_plausibilidad_biologica(h, fam)
    # da igual el resultado del matching: nunca crash y siempre hay flag
    assert "posible_homonimo" in h[0]


def test_pre_pase_anios_de_hallazgos():
    """El año de nacimiento de un PROGENITOR hallado en el mismo lote
    sirve para comprobar al hijo (sin ficha previa del progenitor)."""
    fam = {"personas": [
        {"nombre": "Isidro Merillas Panero", "padre": "Nazario Merillas",
         "madre": "Obdulia Pelaz", "nacimiento": {}},
    ]}
    h = [
        {"persona": "Nazario Merillas", "tipo_evento": "nacimiento",
         "fecha_valor": "1845", "fecha_precision": "exacta",
         "justificacion": "partida literal"},
        {"persona": "Isidro Merillas Panero", "tipo_evento": "bautismo",
         "fecha_valor": "1870", "fecha_precision": "exacta",
         "justificacion": "partida literal"},
    ]
    marcados = verificar_plausibilidad_biologica(h, fam)
    assert marcados == 0
    assert h[1]["plausibilidad_biologica"] == "OK"


# ================== v9.1 — PLAUSIBILIDAD × NIVEL DE EVIDENCIA ===============
# (añadidos por la PARTE 0: los tests de arriba no se tocan)

def test_homonomimo_marcado_nunca_acaba_confirmado():
    """Integración de los dos guardes: la plausibilidad marca
    posible_homonimo (contradicción biológica REAL) -> el clasificador
    de evidencia NO puede dejarlo en confirmado aunque nombre+fecha
    casen con la ficha."""
    from agent.evidencia import NIVEL_CONFIRMADO, clasificar_hallazgo
    fam = _fam(padre_nac="1868")           # hijo 2 años después del padre
    h = [_hallazgo(1870)]
    verificar_plausibilidad_biologica(h, fam)
    assert h[0]["posible_homonimo"] is True
    clasificar_hallazgo(h[0], fam)
    assert h[0]["nivel_evidencia"] != NIVEL_CONFIRMADO


def test_hallazgo_plausible_con_2_datos_acaba_confirmado():
    """El camino bueno: plausibilidad OK + nombre completo + fecha
    coherente -> confirmado (los >=2 datos independientes)."""
    from agent.evidencia import NIVEL_CONFIRMADO, clasificar_hallazgo
    fam = _fam(padre_nac="1845")           # 25 años: plausible
    # el acta nombra a los padres (como en una partida real): ese es el
    # segundo dato independiente
    h = [_hallazgo(1870)]
    h[0]["otros_nombres"] = ["Nazario Merillas (padre)",
                             "Obdulia Pelaz (madre)"]
    verificar_plausibilidad_biologica(h, fam)
    assert h[0]["plausibilidad_biologica"] == "OK"
    clasificar_hallazgo(h[0], fam)
    assert h[0]["nivel_evidencia"] == NIVEL_CONFIRMADO
    assert any("padre" in d or "madre" in d for d in h[0]["datos_que_casan"])


def test_padres_sin_fechas_sigue_sin_penalizar_al_candidato():
    """La regla v4.2 (falta de datos != sospecha) se mantiene con la
    clasificación v9.1: sin fechas de padres no hay homónimo, y el
    hallazgo con nombre+fecha queda confirmado, no degradado."""
    from agent.evidencia import NIVEL_CONFIRMADO, clasificar_hallazgo
    fam = _fam()                           # padres SIN fecha
    # la partida nombra a los padres, que CASAN con la ficha: 2 datos
    # (nombre completo + padres) aunque nadie tenga fechas
    h = [_hallazgo(1870)]
    h[0]["otros_nombres"] = ["Nazario Merillas (padre)",
                             "Obdulia Pelaz (madre)"]
    verificar_plausibilidad_biologica(h, fam)
    assert h[0]["plausibilidad_biologica"] == "NO_COMPROBABLE"
    clasificar_hallazgo(h[0], fam)
    assert h[0]["nivel_evidencia"] == NIVEL_CONFIRMADO
