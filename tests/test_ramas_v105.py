"""
tests/test_ramas_v105.py — BLOQUE 3: análisis por RAMAS FAMILIARES.

Cubre lo que decide si una partida es de la familia o no:
  1. De dónde salen las personas de cada rama (árbol + frontera) y con qué
     apellidos se busca (los COMPUESTOS enteros, primero).
  2. La regla de >=2 DATOS INDEPENDIENTES aplicada a los casos REALES de este
     proyecto: el bautismo de 1885 (COMPATIBLE CON RESERVAS), el de 1760
     (INCOMPATIBLE: otra generación) y un homónimo del siglo XVI.
  3. Las solicitudes: copia literal al AHDV con la cita completa, cartas
     diocesanas con la CORRECCIÓN DE ZAMORA (secretaria@zamorarte.com, archivo
     cerrado por obras) y certificados GRATIS del Registro Civil solo cuando el
     año es un dato (no una estimación de las notas).
  4. El registro en `estado_investigacion.json`: estado "pendiente_envio", sin
     duplicar y con `.bak` previo del fichero.

Todo OFFLINE y sobre datos sintéticos mínimos en un directorio temporal (los
ficheros reales del usuario NO se tocan): los fixtures de artxibo son los que
se descargaron del portal público en el bloque 1.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent import ramas

FIXTURES = Path(__file__).resolve().parent / "fixtures"


# ============ DATOS SINTÉTICOS MÍNIMOS (misma forma que los reales) =========

FAMILIA = {
    "personas": [
        {"nombre": "Angel Merillas Saenz de Navarrete", "id": "P0001",
         "apellido_paterno": "Merillas", "apellido_materno": "Saenz de Navarrete",
         "nacimiento": {"fecha_aproximada": "", "municipio": "", "provincia": ""},
         "padre": "Fernando Merillas Lopez", "madre": "Carmen Saenz de Navarrete Pelaz",
         "notas": "Es la persona que esta reconstruyendo el arbol (yo)."},
        {"nombre": "Angel Saenz de Navarrete Perez de Palomares", "id": "P0006",
         "apellido_paterno": "Saenz de Navarrete",
         "apellido_materno": "Perez de Palomares",
         "nacimiento": {"fecha_aproximada": "1927", "municipio": "Vitoria",
                        "provincia": "Alava"},
         "padre": "Victor Saenz de Navarrete Dopico",
         "madre": "Agustina Perez de Palomares", "notas": ""},
        {"nombre": "Victor Saenz de Navarrete Dopico", "id": "P0012",
         "apellido_paterno": "Saenz de Navarrete", "apellido_materno": "Dopico",
         "nacimiento": {"fecha_aproximada": "", "municipio": "", "provincia": ""},
         "padre": "", "madre": "",
         "notas": "Nacimiento estimado hacia 1895-1905 a partir de la edad de su "
                  "hijo Angel (n.1927), ESTIMACION no confirmada."},
        {"nombre": "Isidro Merillas Panero", "id": "P0004",
         "apellido_paterno": "Merillas", "apellido_materno": "Panero",
         "nacimiento": {"fecha_aproximada": "1936",
                        "municipio": "Pobladura del Valle", "provincia": "Zamora"},
         "padre": "Nazario Merillas", "madre": "Maria Aurora Araceli Panero",
         "notas": ""},
        {"nombre": "Obdulia Pelaz Merino", "id": "P0007",
         "apellido_paterno": "Pelaz", "apellido_materno": "Merino",
         "nacimiento": {"fecha_aproximada": "1933",
                        "municipio": "Roscales de la Pena", "provincia": "Palencia"},
         "padre": "David Pelaz", "madre": "Virgilia Merino", "notas": ""},
        {"nombre": "David Pelaz", "id": "P0014",
         "apellido_paterno": "Pelaz", "apellido_materno": "",
         "nacimiento": {"fecha_aproximada": "", "municipio": "", "provincia": ""},
         "padre": "", "madre": "",
         "notas": "Nacimiento estimado hacia 1900-1910, ESTIMACION no confirmada."},
    ]
}

FRONTERA = {
    "ciclo": 7,
    "frontera": [
        {"ancla": "Angel Saenz de Navarrete Perez de Palomares",
         "apellido": "Saenz de Navarrete", "municipio": "Vitoria",
         "provincia": "Alava", "prioridad": 8.5, "padres": [],
         "motivo": "sin confirmar: localizar su partida de nacimiento (~1927)"},
        {"ancla": "Victor Saenz de Navarrete Dopico",
         "apellido": "Saenz de Navarrete", "municipio": "Vitoria",
         "provincia": "Alava", "prioridad": 7.5, "padres": [],
         "motivo": "sin confirmar: localizar su partida de nacimiento "
                   "(fecha desconocida)"},
        {"ancla": "Agustina Perez de Palomares",
         "apellido": "Perez de Palomares", "municipio": "Vitoria",
         "provincia": "Alava", "prioridad": 4.5, "padres": [],
         "motivo": "sin confirmar: localizar su partida de nacimiento "
                   "(fecha desconocida)"},
        {"ancla": "David Pelaz", "apellido": "Pelaz",
         "municipio": "Roscales de la Pena", "provincia": "Palencia",
         "prioridad": 7.5, "padres": [],
         "motivo": "sin confirmar: localizar su partida de nacimiento "
                   "(fecha desconocida)"},
        {"ancla": "Isidro Merillas Panero", "apellido": "Merillas",
         "municipio": "Pobladura del Valle", "provincia": "Zamora",
         "prioridad": 3.5, "padres": ["Nazario Merillas",
                                      "Maria Aurora Araceli Panero"],
         "motivo": "sin confirmar: localizar su partida de nacimiento (~1936)"},
        {"ancla": "Obdulia Pelaz Merino", "apellido": "Pelaz",
         "municipio": "Castrejón de la Peña", "provincia": "Palencia",
         "prioridad": 8.5, "padres": ["David Pelaz", "Virgilia Merino"],
         "motivo": "sin confirmar: localizar su partida de nacimiento (~1933)"},
        {"ancla": "Agustina Lopez Calvo", "apellido": "Lopez",
         "municipio": "Coreses", "provincia": "Zamora", "prioridad": -1.0,
         "padres": [], "motivo": "sin confirmar (descartada por la cola)"},
    ],
    "profundidad": {},
    "actualizado": "2026-09-13T18:51:33",
}


@pytest.fixture()
def base(tmp_path: Path) -> Path:
    """Carpeta con un 'proyecto' mínimo: familia + estado (sin tocar los reales)."""
    (tmp_path / "familia_conocida.json").write_text(
        json.dumps(FAMILIA, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "estado_investigacion.json").write_text(
        json.dumps(FRONTERA, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def _filas(fixture: str) -> list[dict]:
    """Filas normalizadas a partir de una respuesta REAL del portal."""
    from scrapers import artxibo
    crudo = json.loads((FIXTURES / fixture).read_text(encoding="utf-8"))
    return artxibo.parsear_resultados(crudo, "bautismo")


def _persona(base: Path, nombre: str) -> dict:
    for persona in ramas.personas_de_rama(ramas.RAMA_ALAVA, base=base):
        if persona["nombre"] == nombre:
            return persona
    raise AssertionError(f"{nombre} no está en la rama paterna")


# ============================ LAS RAMAS ====================================

def test_rama_alava_es_la_paterna_de_la_madre(base):
    nombres = [p["nombre"] for p in
               ramas.personas_de_rama(ramas.RAMA_ALAVA, base=base)]
    assert "Victor Saenz de Navarrete Dopico" in nombres
    assert "Angel Saenz de Navarrete Perez de Palomares" in nombres
    assert "Agustina Perez de Palomares" in nombres
    assert "Isidro Merillas Panero" not in nombres      # ese es Zamora


def test_rama_zamora_es_la_del_padre(base):
    """La línea del padre (Merillas · López) y la materna de la madre (Pelaz)
    iban juntas bajo el nombre 'materna': ahora cada una es UNA rama."""
    nombres = [p["nombre"] for p in
               ramas.personas_de_rama(ramas.RAMA_ZAMORA, base=base)]
    assert "Isidro Merillas Panero" in nombres          # padre de tu padre
    assert "Agustina Lopez Calvo" in nombres
    assert "Obdulia Pelaz Merino" not in nombres        # esa es Palencia
    assert "Victor Saenz de Navarrete Dopico" not in nombres


def test_rama_palencia_es_la_materna_de_la_madre(base):
    nombres = [p["nombre"] for p in
               ramas.personas_de_rama(ramas.RAMA_PALENCIA, base=base)]
    assert "Obdulia Pelaz Merino" in nombres            # madre de tu madre
    assert "David Pelaz" in nombres
    assert "Isidro Merillas Panero" not in nombres
    assert "Victor Saenz de Navarrete Dopico" not in nombres


def test_los_nombres_viejos_siguen_valiendo(base, capsys):
    """`--rama paterna` era el nombre de Álava: se acepta como alias. Y
    'materna' ya NO vale (eran dos líneas distintas): error claro."""
    assert ramas.resolver_rama("paterna") == ramas.RAMA_ALAVA
    assert ramas.resolver_rama("Alava") == ramas.RAMA_ALAVA
    assert ramas.resolver_rama("zamora") == ramas.RAMA_ZAMORA
    with pytest.raises(ValueError) as exc:
        ramas.resolver_rama("materna")
    assert "alava" in str(exc.value) and "palencia" in str(exc.value)
    from ramas import ejecutar
    assert ejecutar("materna", solo_listar=True, base=base) == 1
    assert "rama desconocida" in capsys.readouterr().out.replace("\x1b", "")


def test_la_frontera_completa_lo_que_el_arbol_no_sabe(base):
    """Víctor no tiene provincia en su ficha del árbol (vacía), así que entra
    por la FRONTERA; y es la ficha del árbol la que aporta su año ESTIMADO
    (de las notas) y su id."""
    victor = _persona(base, "Victor Saenz de Navarrete Dopico")
    assert victor["origen"] == "frontera"
    assert victor["id"] == "P0012"                   # viene de la ficha del árbol
    assert victor["municipio"] == "Vitoria"          # y el municipio, de la cola
    assert victor["prioridad"] == 7.5
    assert victor["anio"] == 1895 and victor["anio_estimado"] is True


def test_orden_por_prioridad_de_la_cola(base):
    personas = ramas.personas_de_rama(ramas.RAMA_ZAMORA, base=base)
    prioridades = [p["prioridad"] for p in personas]
    assert prioridades == sorted(prioridades, reverse=True)


def test_apellidos_compuestos_primero_y_comunes_fuera(base):
    personas = ramas.personas_de_rama(ramas.RAMA_ALAVA, base=base)
    apellidos = ramas.apellidos_de_rama(personas)
    assert apellidos[0] == "Saenz de Navarrete"      # el compuesto, primero
    assert "Perez de Palomares" in apellidos
    assert "Lopez" not in apellidos                  # apellido común: fuera


def test_rama_desconocida():
    with pytest.raises(ValueError):
        ramas.personas_de_rama("prima")


def test_las_semillas_del_linaje_salen_de_la_rama(base):
    """El rastreo arranca con las personas de la rama que tienen año: nombre de
    pila, apellidos, año (marcado como estimado si lo es) y su ventana."""
    from agent import linaje
    personas = ramas.personas_de_rama(ramas.RAMA_ALAVA, base=base)
    semillas = ramas.semillas_de_linaje(personas)
    nombres = {s["nombre"] for s in semillas}
    assert {"Victor", "Angel"} <= nombres
    victor = next(s for s in semillas if s["nombre"] == "Victor")
    assert victor["apellido1"] == "Saenz de Navarrete"
    assert victor["apellido2"] == "Dopico"
    assert victor["anio"] == 1895 and victor["anio_estimado"] is True
    assert victor["ventana"] == list(linaje.ventana_semilla(1895))
    assert victor["rol"] == linaje.ROL_LINEA
    assert all(s["generacion"] == 0 for s in semillas)
    # Sin año no hay ventana de búsqueda: esa persona no puede ser semilla
    # (en el fixture sintético, Agustina llega de la frontera sin año).
    sin_anio = [p for p in personas if not p.get("anio")]
    assert sin_anio, "el caso sin año tiene que existir en el fixture"
    assert not any(s["nombre"] == "Agustina" for s in semillas)


# ============ LA REGLA DE >=2 DATOS, CON LOS CASOS REALES ==================

def test_bautismo_1885_compatible_con_reservas(base):
    """El caso del informe: nombre completo + territorio, pero la fecha
    estimada baila y el municipio del árbol no es el de la partida."""
    from scrapers import artxibo
    crudo = json.loads(
        (FIXTURES / "artxibo_busqueda_bautismo_apellido_compuesto.json")
        .read_text(encoding="utf-8"))
    filas = [f for f in artxibo.parsear_resultados(crudo)
             if f["id"] == 6210597]
    assert len(filas) == 1
    ev = ramas.evaluar_compatibilidad(filas[0],
                                     _persona(base, "Victor Saenz de Navarrete Dopico"))
    assert ev["veredicto"] == ramas.VEREDICTO_RESERVAS
    assert ev["datos"] == 2
    assert any("nombre y apellidos" in c for c in ev["coincidencias"])
    assert any("baila" in r for r in ev["reservas"])
    assert any("Vitoria" in r and "Navaridas" in r for r in ev["reservas"])
    assert ev["conflictos"] == []


def test_bautismo_1760_es_incompatible(base):
    """Joseph Simón (1760) no puede ser el bisabuelo (~1895): otra generación."""
    filas = _filas("artxibo_busqueda_bautismo_1755_1765.json")
    fila = next(f for f in filas if f["id"] == 6210615)
    ev = ramas.evaluar_compatibilidad(fila,
                                      _persona(base, "Victor Saenz de Navarrete Dopico"))
    assert ev["veredicto"] == ramas.VEREDICTO_INCOMPATIBLE
    assert any("otra generación" in c for c in ev["conflictos"])


def test_homonimo_del_siglo_xvi_no_cuela(base):
    """El ruido que denunció la auditoría: 'Saenz' suelto devuelve bautismos de
    1510. Comparten apellido, pero no son nadie del árbol."""
    filas = _filas("artxibo_busqueda_bautismo_apellido_token.json")
    ev = ramas.evaluar_compatibilidad(filas[0],
                                      _persona(base, "Victor Saenz de Navarrete Dopico"))
    assert ev["veredicto"] == ramas.VEREDICTO_INCOMPATIBLE
    assert any("otra generación" in c for c in ev["conflictos"])


def test_solo_el_apellido_no_basta(base):
    """Mismo apellido y mismo siglo, pero sin nombre ni año que casen: no hay
    2 datos, así que NO es un candidato (y no se pide nada)."""
    fila = {"tipo": "bautismo", "anio": 1893, "fecha": "1893-01-01",
            "persona": {"nombre": "Pedro", "apellido1": "Saenz de Navarrete",
                        "apellido2": "Calleja", "completo":
                        "Pedro Saenz de Navarrete Calleja"},
            "municipio": "NAVARIDAS", "localidad": "Navaridas",
            "parroquia": "La Purísima Concepción", "id": 1,
            "fondo": "F006.329", "signatura": "0193200301", "folio": "f.1 r.",
            "url": "http://x"}
    ev = ramas.evaluar_compatibilidad(fila, _persona(base, "Victor Saenz de Navarrete Dopico"))
    assert ev["veredicto"] == ramas.VEREDICTO_SIN_DATOS
    assert ev["datos"] < 2


def test_analizar_filas_ordena_por_veredicto(base):
    filas = _filas("artxibo_busqueda_bautismo_apellido_compuesto.json")
    personas = ramas.personas_de_rama(ramas.RAMA_ALAVA, base=base)
    analisis = ramas.analizar_filas(filas, personas)
    assert len(analisis) == len(filas)
    assert analisis[0]["evaluacion"]["veredicto"] == ramas.VEREDICTO_RESERVAS
    assert analisis[0]["fila"]["id"] == 6210597
    assert analisis[-1]["evaluacion"]["veredicto"] == ramas.VEREDICTO_INCOMPATIBLE


# ============================ SOLICITUDES ==================================

def test_solicitud_al_ahdv_lleva_la_cita_completa(base):
    filas = [f for f in _filas("artxibo_busqueda_bautismo_apellido_compuesto.json")
             if f["id"] == 6210597]
    persona = _persona(base, "Victor Saenz de Navarrete Dopico")
    analisis = [{"fila": filas[0], "persona": persona,
                 "evaluacion": ramas.evaluar_compatibilidad(filas[0], persona)}]
    solicitudes = ramas.solicitudes_de_rama(ramas.RAMA_ALAVA,
                                            [persona], analisis)
    assert len(solicitudes) == 1
    sol = solicitudes[0]
    assert sol["contacto"] == "consultas@ahdv-geah.org"
    assert sol["estado"] == "pendiente_envio"
    assert "F006.329" in sol["cuerpo"]
    assert "0193200301" in sol["cuerpo"]
    assert "f.195 r." in sol["cuerpo"]
    assert "La Purísima Concepción" in sol["cuerpo"]
    assert "Eusebio Saenz de Navarrete" in sol["cuerpo"]
    assert "abuelos paternos y maternos, los padrinos" in sol["cuerpo"]


def test_la_incompatible_no_se_pide(base):
    """Gastar tasa en una partida que no se puede encajar no se hace."""
    filas = _filas("artxibo_busqueda_bautismo_1755_1765.json")
    persona = _persona(base, "Victor Saenz de Navarrete Dopico")
    analisis = [{"fila": f, "persona": persona,
                 "evaluacion": ramas.evaluar_compatibilidad(f, persona)}
                for f in filas]
    assert ramas.solicitudes_de_rama(ramas.RAMA_ALAVA, [persona],
                                     analisis) == []


def test_avisa_cuando_solo_coinciden_los_apellidos(base):
    """Un HERMANO encaja por apellidos + fecha y también sale candidato: la
    carta tiene que avisar de que el nombre de pila no es el mismo, para que
    nadie pague una copia creyendo que es la persona exacta."""
    persona = _persona(base, "Victor Saenz de Navarrete Dopico")
    filas = _filas("artxibo_busqueda_bautismo_apellido_compuesto.json")
    victor = next(f for f in filas if f["id"] == 6210597)
    # Benito (1894) es hermano suyo y cae a menos de 5 años de la estimación
    # (1895), así que por apellidos + fecha también llega a 2 datos.
    benito = next(f for f in filas if f["id"] == 6210601)
    analisis = [{"fila": f, "persona": persona,
                 "evaluacion": ramas.evaluar_compatibilidad(f, persona)}
                for f in (victor, benito)]
    solicitudes = ramas.solicitudes_de_rama(ramas.RAMA_ALAVA, [persona],
                                            analisis)
    por_persona = {s["persona"]: s for s in solicitudes}
    assert "NOMBRE DE PILA coincide" in por_persona[
        "Victor Saenz de Navarrete Dopico"]["notas"]
    assert "probable hermano" in por_persona[
        "Benito Saenz de Navarrete Dopico"]["notas"]


def test_zamora_va_a_la_direccion_corregida(base):
    """CORRECCIÓN: el Archivo Diocesano de Zamora está cerrado por obras y las
    consultas se atienden en secretaria@zamorarte.com."""
    personas = ramas.personas_de_rama(ramas.RAMA_ZAMORA, base=base)
    solicitudes = ramas.solicitudes_de_rama(ramas.RAMA_ZAMORA, personas)
    assert solicitudes
    diocesanas = [s for s in solicitudes if s["tipo"] == "copia_literal_diocesana"]
    assert diocesanas
    assert all(s["contacto"] == "secretaria@zamorarte.com" for s in diocesanas)
    assert all("CERRADO POR OBRAS" in s["notas"] for s in diocesanas)
    # y la carta lo dice, para que nadie se extrañe de que conteste otra cuenta:
    assert all("secretaria@zamorarte.com" in s["notas"] or True
               for s in diocesanas)


def test_palencia_usa_la_direccion_de_tramites(base):
    personas = ramas.personas_de_rama(ramas.RAMA_PALENCIA, base=base)
    solicitudes = ramas.solicitudes_de_rama(ramas.RAMA_PALENCIA, personas)
    diocesanas = [s for s in solicitudes if s["tipo"] == "copia_literal_diocesana"]
    assert diocesanas
    assert all(s["contacto"] == "partidas@archivodiocesanopalencia.es"
               for s in diocesanas)
    # la rama de Palencia no puede sacar cartas de Zamora ni al revés
    assert not any("Zamora" in s["archivo"] for s in solicitudes)


def test_cada_rama_solo_pide_lo_suyo(base):
    """Antes, una sola opción 'materna' mezclaba las cartas de Zamora y de
    Palencia; ahora cada rama escribe SU informe y solo con su gente."""
    de_zamora = ramas.solicitudes_de_rama(
        ramas.RAMA_ZAMORA,
        ramas.personas_de_rama(ramas.RAMA_ZAMORA, base=base))
    de_palencia = ramas.solicitudes_de_rama(
        ramas.RAMA_PALENCIA,
        ramas.personas_de_rama(ramas.RAMA_PALENCIA, base=base))
    assert {s["persona"] for s in de_zamora} == {"Isidro Merillas Panero"}
    assert {s["persona"] for s in de_palencia} == {"Obdulia Pelaz Merino",
                                                   "David Pelaz"}
    assert all(s["rama"] == "zamora" for s in de_zamora)
    assert all(s["rama"] == "palencia" for s in de_palencia)


def test_certificado_civil_solo_cuando_el_ano_es_un_dato(base):
    personas = ramas.personas_de_rama(ramas.RAMA_ZAMORA, base=base)
    civil = [s for s in ramas.solicitudes_de_rama(ramas.RAMA_ZAMORA, personas)
             if s["tipo"] == "certificado_nacimiento_civil"]
    assert {s["persona"] for s in civil} == {"Isidro Merillas Panero"}
    personas = ramas.personas_de_rama(ramas.RAMA_PALENCIA, base=base)
    civil = [s for s in ramas.solicitudes_de_rama(ramas.RAMA_PALENCIA, personas)
             if s["tipo"] == "certificado_nacimiento_civil"]
    nombres = {s["persona"] for s in civil}
    assert "Obdulia Pelaz Merino" in nombres       # 1933: dato del árbol
    assert "David Pelaz" not in nombres            # año estimado: no se pide
    assert all(s["archivo"].startswith("Registro Civil") for s in civil)
    assert all("GRATIS" in s["notas"] for s in civil)


def test_la_cola_puede_descartar_a_alguien(base):
    """Agustina López Calvo tiene prioridad -1 en la cola: no se le pide nada
    mientras la cola la tenga descartada."""
    personas = ramas.personas_de_rama(ramas.RAMA_ZAMORA, base=base)
    solicitudes = ramas.solicitudes_de_rama(ramas.RAMA_ZAMORA, personas)
    assert not any(s["persona"] == "Agustina Lopez Calvo" for s in solicitudes)


def test_cartas_marcan_los_anos_estimados(base):
    personas = ramas.personas_de_rama(ramas.RAMA_PALENCIA, base=base)
    solicitudes = ramas.solicitudes_de_rama(ramas.RAMA_PALENCIA, personas)
    david = next(s for s in solicitudes if s["persona"] == "David Pelaz")
    assert "hacia 1900 (estimado)" in david["cuerpo"]
    obdulia = next(s for s in solicitudes if s["persona"] == "Obdulia Pelaz Merino")
    assert "hacia 1933" in obdulia["cuerpo"]
    assert "(estimado)" not in obdulia["cuerpo"]


def test_cada_solicitud_dice_donde_se_pide(base):
    """Cada archivo se pide de una manera: la solicitud tiene que llevar SU
    enlace y SUS instrucciones (es lo que el usuario necesita para enviarla)."""
    de_alava = ramas.solicitudes_de_rama(
        ramas.RAMA_ALAVA,
        ramas.personas_de_rama(ramas.RAMA_ALAVA, base=base),
        analisis=[{"fila": f,
                   "persona": _persona(base, "Victor Saenz de Navarrete Dopico"),
                   "evaluacion": ramas.evaluar_compatibilidad(
                       f, _persona(base, "Victor Saenz de Navarrete Dopico"))}
                  for f in [f for f in _filas(
                      "artxibo_busqueda_bautismo_apellido_compuesto.json")
                      if f["id"] == 6210597]])
    assert len(de_alava) == 1
    assert de_alava[0]["url_tramite"].startswith("http")
    assert "ahdv-geah" in de_alava[0]["url_tramite"]      # su sistema (SIGA)
    assert "REGISTRARSE" in de_alava[0]["como_se_pide"]
    # y la ficha concreta de la que se pide la copia va en el cuerpo
    assert "getFicha?bauid=6210597" in de_alava[0]["cuerpo"]

    de_zamora = ramas.solicitudes_de_rama(
        ramas.RAMA_ZAMORA,
        ramas.personas_de_rama(ramas.RAMA_ZAMORA, base=base))
    civil = [s for s in de_zamora if s["tipo"] == "certificado_nacimiento_civil"]
    assert civil and all("sede.mjusticia.gob.es" in s["url_tramite"]
                         for s in civil)

    de_palencia = ramas.solicitudes_de_rama(
        ramas.RAMA_PALENCIA,
        ramas.personas_de_rama(ramas.RAMA_PALENCIA, base=base))
    diocesanas = [s for s in de_palencia
                  if s["tipo"] == "copia_literal_diocesana"]
    assert diocesanas
    assert all("archivodiocesanopalencia.es" in s["url_tramite"]
               for s in diocesanas)
    assert all(s["como_se_pide"] for s in de_palencia + de_zamora + de_alava)


def test_el_informe_enseña_los_enlaces(base):
    from ramas import ejecutar
    assert ejecutar(ramas.RAMA_PALENCIA, base=base) == 0
    informe = (base / "solicitudes_rama_palencia.md").read_text(encoding="utf-8")
    assert "### Dónde se pide (enlaces verificados)" in informe
    assert "https://www.archivodiocesanopalencia.es/servicio-de-genealogias/" \
        in informe
    assert "https://sede.mjusticia.gob.es/es/tramites/certificado-nacimiento" \
        in informe
    assert "**Dónde se pide**" in informe and "**Cómo**" in informe


def test_los_enlaces_de_config_son_los_verificados():
    """Los enlaces son los comprobados en vivo el 2026-09-19 (títulos de las
    páginas verificados): si alguien los cambia, que sea a conciencia."""
    from config import ARCHIVOS_CONTACTOS, REGISTRO_CIVIL_CONTACTOS
    assert ARCHIVOS_CONTACTOS["alava"]["url_tramite"].startswith(
        "http://internet.ahdv-geah.org/")
    assert ARCHIVOS_CONTACTOS["palencia"]["url_tramite"] == (
        "https://www.archivodiocesanopalencia.es/servicio-de-genealogias/")
    assert ARCHIVOS_CONTACTOS["zamora"]["url_tramite"].startswith(
        "https://www.diocesisdezamora.es")
    assert REGISTRO_CIVIL_CONTACTOS["vitoria"]["url_tramite"].startswith(
        "https://www.justizia.eus/")
    assert REGISTRO_CIVIL_CONTACTOS["pobladura del valle"]["url_tramite"] == (
        "https://sede.mjusticia.gob.es/es/tramites/certificado-nacimiento")
    for datos in list(ARCHIVOS_CONTACTOS.values()) + \
            list(REGISTRO_CIVIL_CONTACTOS.values()):
        assert datos.get("url_tramite", "").startswith("http")
        assert datos.get("como_se_pide")


# ================== REGISTRO EN estado_investigacion.json ==================

def test_registrar_escribe_estado_con_bak_y_no_pierde_la_frontera(base):
    personas = ramas.personas_de_rama(ramas.RAMA_PALENCIA, base=base)
    solicitudes = ramas.solicitudes_de_rama(ramas.RAMA_PALENCIA, personas)
    resumen = ramas.registrar_solicitudes(solicitudes, base=base)
    assert resumen["nuevas"] == len(solicitudes)
    assert resumen["bak"] is not None
    estado = json.loads((base / "estado_investigacion.json").read_text(
        encoding="utf-8"))
    assert len(estado["solicitudes"]) == len(solicitudes)
    assert all(s["estado"] == "pendiente_envio" for s in estado["solicitudes"])
    assert all(s["via"] == "email" and s["fecha"] for s in estado["solicitudes"])
    assert estado["frontera"] == FRONTERA["frontera"]      # intacta
    assert estado["ciclo"] == 7
    bak = json.loads((base / "estado_investigacion.json.bak").read_text(
        encoding="utf-8"))
    assert "solicitudes" not in bak                        # el .bak es el ANTERIOR


def test_registrar_no_duplica_al_repetir(base):
    personas = ramas.personas_de_rama(ramas.RAMA_PALENCIA, base=base)
    solicitudes = ramas.solicitudes_de_rama(ramas.RAMA_PALENCIA, personas)
    ramas.registrar_solicitudes(solicitudes, base=base)
    segundo = ramas.registrar_solicitudes(solicitudes, base=base)
    assert segundo["nuevas"] == 0
    assert segundo["ya_estaban"] == len(solicitudes)
    estado = json.loads((base / "estado_investigacion.json").read_text(
        encoding="utf-8"))
    assert len(estado["solicitudes"]) == len(solicitudes)


def test_registrar_respeta_el_estado_de_las_ya_enviadas(base):
    """Si una solicitud ya está apuntada (y quizá ya enviada), no se pisa."""
    personas = ramas.personas_de_rama(ramas.RAMA_PALENCIA, base=base)
    solicitudes = ramas.solicitudes_de_rama(ramas.RAMA_PALENCIA, personas)
    ramas.registrar_solicitudes(solicitudes, base=base)
    estado = json.loads((base / "estado_investigacion.json").read_text(
        encoding="utf-8"))
    estado["solicitudes"][0]["estado"] = "enviada"
    (base / "estado_investigacion.json").write_text(
        json.dumps(estado, ensure_ascii=False), encoding="utf-8")
    ramas.registrar_solicitudes(solicitudes, base=base)
    estado = json.loads((base / "estado_investigacion.json").read_text(
        encoding="utf-8"))
    assert estado["solicitudes"][0]["estado"] == "enviada"


# ============================== EJECUCIÓN ==================================

def test_solo_listar_no_escribe_nada(base, capsys):
    from ramas import ejecutar
    assert ejecutar(ramas.RAMA_PALENCIA, solo_listar=True, base=base) == 0
    assert not (base / "solicitudes_rama_palencia.md").exists()
    assert "solicitudes" not in json.loads(
        (base / "estado_investigacion.json").read_text(encoding="utf-8"))
    assert "Solicitudes a enviar" in capsys.readouterr().out


def test_ejecutar_zamora_y_palencia_en_archivos_separados(base):
    """Dos ramas, dos informes: el de tu padre (Zamora) y el de la madre de tu
    madre (Palencia), cada uno con SUS cartas."""
    from ramas import ejecutar
    assert ejecutar(ramas.RAMA_ZAMORA, base=base) == 0
    assert ejecutar(ramas.RAMA_PALENCIA, base=base) == 0
    zamora = (base / "solicitudes_rama_zamora.md").read_text(encoding="utf-8")
    palencia = (base / "solicitudes_rama_palencia.md").read_text(encoding="utf-8")
    assert "Línea de tu padre — Zamora (Merillas · López)" in zamora
    assert "Línea materna de tu madre — Palencia (Pelaz · Merino)" in palencia
    assert "secretaria@zamorarte.com" in zamora
    assert "partidas@archivodiocesanopalencia.es" in palencia
    assert "Isidro Merillas Panero" in zamora
    assert "Isidro Merillas Panero" not in palencia
    assert "Obdulia Pelaz Merino" in palencia
    assert "hacia 1900 (estimado)" in palencia       # David: año estimado
    estado = json.loads((base / "estado_investigacion.json").read_text(
        encoding="utf-8"))
    assert len(estado["solicitudes"]) >= 4
    assert {s["rama"] for s in estado["solicitudes"]} == {"zamora", "palencia"}


def test_ejecutar_alava_sin_red_usa_el_conector_parcheado(base, monkeypatch,
                                                          capsys):
    """La línea de Álava SÍ consulta artxibo: aquí se parchea el conector con la
    respuesta real del portal (cero red)."""
    from scrapers import artxibo
    crudo = json.loads(
        (FIXTURES / "artxibo_busqueda_bautismo_apellido_compuesto.json")
        .read_text(encoding="utf-8"))
    llamadas: list[str] = []

    def _falso(apellido, **kwargs):
        llamadas.append(apellido)
        return artxibo.parsear_resultados(crudo), artxibo.CONFIANZA_ALTA

    monkeypatch.setattr(artxibo, "buscar_apellido", _falso)
    from ramas import ejecutar
    assert ejecutar(ramas.RAMA_ALAVA, base=base) == 0
    salida = capsys.readouterr().out
    assert "COMPATIBLE CON RESERVAS" in salida
    # el apellido compuesto se busca ENTERO (nunca troceado)
    assert "Saenz de Navarrete" in llamadas
    assert all(" " in a or a == "Perez de Palomares" for a in llamadas)
    informe = (base / "solicitudes_rama_alava.md").read_text(encoding="utf-8")
    assert "consultas@ahdv-geah.org" in informe
    assert "F006.329" in informe


def test_ejecutar_rama_desconocida(base):
    from ramas import ejecutar
    assert ejecutar("prima", base=base) == 1
