"""
tests/test_frontera.py — La frontera priorizada no repite trabajo
(punto 4 del informe).

  - UNA entrada "hermanos" por nidada (binomio de padres): antes cada
    hermano confirmado añadía su propia entrada para la MISMA nidada y el
    ciclo rebuscaba a los mismos hermanos una y otra vez.
  - Entradas YA investigadas (estado "investigados") quedan fuera.
  - _clave_evidencia: la clave de dedupe ignora el timestamp (punto 3).
  - _nombre_casa: matching bidireccional por tokens.
"""

from __future__ import annotations

from agent.frontera import (_clave_evidencia, _nombre_casa,
                            calcular_frontera)


def _familia_nidada() -> dict:
    """Padres confirmados + 3 hijos confirmados: la nidada completa."""
    padre = {"nombre": "Nazario Merillas", "sexo": "M",
             "nacimiento": {"fecha_aproximada": "1845"},
             "conyuge": "Obdulia Pelaz",
             "evidencias": [{"tipo": "bautismo"}], "estado": "confirmado",
             "padre": "", "madre": ""}
    madre = {"nombre": "Obdulia Pelaz", "sexo": "F",
             "nacimiento": {"fecha_aproximada": "1848"},
             "conyuge": "Nazario Merillas",
             "evidencias": [{"tipo": "bautismo"}], "estado": "confirmado",
             "padre": "", "madre": ""}
    hijos = [
        {"nombre": f"Hijo {i} Merillas Pelaz", "sexo": "M",
         "nacimiento": {"fecha_aproximada": str(1870 + i)},
         "padre": "Nazario Merillas", "madre": "Obdulia Pelaz",
         "evidencias": [{"tipo": "bautismo"}], "estado": "confirmado"}
        for i in range(3)
    ]
    return {"personas": [padre, madre] + hijos}


def test_una_sola_entrada_hermanos_por_nidada():
    estado = {"ciclo": 1, "frontera": [], "candidatos": [],
              "descartados": [], "investigados": []}
    res = calcular_frontera(familia=_familia_nidada(),
                            estado_previo=estado)
    hermanos = [e for e in res["frontera"] if e["tipo"] == "hermanos"]
    assert len(hermanos) == 1, ("la nidada debe generar UNA entrada, no "
                                "una por hermano")
    assert hermanos[0]["clave_investigacion"].startswith("hermanos::")


def test_entradas_investigadas_quedan_fuera():
    estado = {"ciclo": 1, "frontera": [], "candidatos": [],
              "descartados": [], "investigados": [
                  {"clave": "hermanos::nazario merillas::obdulia pelaz",
                   "ciclo": 1}]}
    res = calcular_frontera(familia=_familia_nidada(),
                            estado_previo=estado)
    hermanos = [e for e in res["frontera"] if e["tipo"] == "hermanos"]
    assert hermanos == []


def test_cambio_de_tipo_reintroduce_entrada():
    """Si los padres del ancla pasan a confirmados, la clave cambia de
    'padres::x' a 'hermanos::...' y la entrada VUELVE a la frontera."""
    fam = _familia_nidada()
    # desconfirmamos a los padres: los hijos piden 'padres'
    for p in fam["personas"][:2]:
        p["evidencias"] = []
        p["estado"] = "memoria"
    estado = {"ciclo": 1, "frontera": [], "candidatos": [],
              "descartados": [], "investigados": [
                  {"clave": "padres::hijo 0 merillas pelaz", "ciclo": 1}]}
    res = calcular_frontera(familia=fam, estado_previo=estado)
    tipos = {(e["tipo"], e["ancla"]) for e in res["frontera"]}
    assert ("padres", "Hijo 1 Merillas Pelaz") in tipos
    assert ("padres", "Hijo 0 Merillas Pelaz") not in tipos  # investigado


def test_clave_evidencia_ignora_timestamp():
    """Punto 3: mismo contenido (aunque cambie 'commit') = misma clave."""
    a = {"tipo": "bautismo", "fecha": "1870", "lugar": "Salas",
         "fuente_url": "http://x/1", "cita": "cita textual",
         "origen": "web", "commit": "2026-01-01T00:00:00"}
    b = dict(a, commit="2027-12-31T23:59:59")
    assert _clave_evidencia(a) == _clave_evidencia(b)
    c = dict(a, fecha="1871")
    assert _clave_evidencia(a) != _clave_evidencia(c)


def test_nombre_casa_bidireccional():
    assert _nombre_casa("Isidro Merillas", "Isidro Merillas Panero")
    assert _nombre_casa("Isidro Merillas Panero", "Isidro Merillas")
    assert _nombre_casa("Isidro Merillas", "Isidro Merillas")
    assert not _nombre_casa("Isidro Merillas", "Obdulia Pelaz")
    assert not _nombre_casa("", "Isidro")
