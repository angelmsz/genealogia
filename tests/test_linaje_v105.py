"""
tests/test_linaje_v105.py — BLOQUE 4: rastreo del linaje hacia arriba.

Lo que se fija aquí:
  1. La VENTANA de años de un progenitor: (hijo − 35, hijo − 18).
  2. Que los padres salen de lo que DICE la partida, no de una corazonada.
  3. La ELECCIÓN DE CANDIDATO con las filas reales del portal: Eusebio (1858)
     para la ventana 1850-1867, Víctor (1885) para la 1875-1915, y que se
     prefiere la misma parroquia y se listan las alternativas.
  4. El RASTREO completo con un buscador que REPRODUCE el portal (devuelve las
     filas del fixture como las devolvería el portal, incluido que su índice
     busca también por el nombre del padre):
       Víctor (1885) → padres Eusebio (1858) + Leocadia (1863)
       Eusebio → padres Pablo + Josefa (y de ahí, los hermanos de Eusebio)
       Leocadia → padres Román María Dopico + Fermina Guzmán
       y los HERMANOS de Víctor salen como colaterales.
  5. Los topes (consultas, generaciones), que el estado es REANUDABLE y que el
     informe dice la línea, los parientes y los eslabones que faltan.

Todo OFFLINE: el buscador es un doble construido con los fixtures reales del
bloque 1 (y una fila real de Leocadia Dopico documentada en
docs/compatibilidad_artxibo_2026-09.md). El módulo no toca la red ni el árbol.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent import linaje
from agent.linaje import (CONF_ALTA, EST_IDENTIFICADO, EST_SIN_RESULTADO,
                          ROL_COLATERAL, ROL_LINEA)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _filas(nombre: str) -> list[dict]:
    from scrapers import artxibo
    crudo = json.loads((FIXTURES / nombre).read_text(encoding="utf-8"))
    return artxibo.parsear_resultados(crudo, "bautismo")


# Fila REAL de la madre de Víctor, sacada del índice en vivo el 2026-09-19
# (está documentada en docs/compatibilidad_artxibo_2026-09.md: bauid 5723968).
LEOCADIA = {
    "tipo": "bautismo", "id": 5723968, "fecha": "1863-12-10", "anio": 1863,
    "persona": {"nombre": "Leocadia", "apellido1": "Dopico",
                "apellido2": "Guzman",
                "completo": "Leocadia Dopico Guzman"},
    "padre": {"nombre": "Roman Maria", "apellido1": "Dopico",
              "apellido2": "", "completo": "Roman Maria Dopico"},
    "madre": {"nombre": "Fermina", "apellido1": "Guzman", "apellido2": "",
              "completo": "Fermina Guzman"},
    "parroquia": "La Purísima Concepción", "localidad": "Navaridas",
    "municipio": "NAVARIDAS", "diocesis": "Vitoria", "fondo": "F006.329",
    "signatura": "0193200301", "folio": "f.100 r.", "cod_referencia": "23041",
    "url": "https://www.artxibo.euskadi.eus/x/bautismo/getFicha?bauid=5723968",
    "texto": "Bautismo de Leocadia Dopico Guzman (1863), Navaridas.",
}


def _todas_las_filas() -> list[dict]:
    return (_filas("artxibo_busqueda_bautismo_apellido_compuesto.json")
            + [LEOCADIA])


def buscador_de_fixture(filas=None):
    """Doble del buscador que REPRODUCE lo que hace el portal.

    El portal busca por el nombre del bautizado Y por el del padre/madre (está
    verificado en vivo: buscar 'Eusebio Saenz de Navarrete' devuelve su propio
    bautismo y las 4 partidas de sus hijos), y filtra por el año del
    sacramento.
    """
    filas = filas if filas is not None else _todas_las_filas()
    llamadas: list[tuple] = []

    def buscar(nombre, apellido1, apellido2="", anio_ini=None, anio_fin=None,
               **kwargs):
        llamadas.append((nombre, apellido1, anio_ini, anio_fin))
        salida = []
        for fila in filas:
            coincide = (linaje.es_la_persona(fila, nombre, apellido1)
                        or linaje.es_hijo_suyo(fila, nombre, apellido1, "padre")
                        or linaje.es_hijo_suyo(fila, nombre, apellido1, "madre"))
            if not coincide:
                continue
            anio = fila.get("anio")
            if anio and anio_ini and anio_fin and not (anio_ini <= anio <= anio_fin):
                continue
            salida.append(fila)
        return salida

    buscar.llamadas = llamadas
    return buscar


def _victor(anio=1895, generacion=0):
    return linaje.nueva_persona(
        nombre="Victor", apellido1="Saenz de Navarrete", apellido2="Dopico",
        anio=anio, anio_estimado=True,
        ventana=linaje.ventana_semilla(anio), generacion=generacion,
        rol=ROL_LINEA, notas="del árbol (estimación)")


# ============================== PIEZAS =====================================

def test_ventana_de_los_padres():
    """Un progenitor va 18-35 años por delante del hijo (nacido ~1885 → los
    padres se bautizan entre 1850 y 1867)."""
    assert linaje.ventana_padres(1885) == (1850, 1867)
    assert linaje.ventana_padres(1858) == (1823, 1840)


def test_los_padres_salen_de_la_partida():
    filas = _filas("artxibo_busqueda_bautismo_apellido_compuesto.json")
    victor = next(f for f in filas if f["id"] == 6210597)
    padres = linaje.padres_de(victor)
    assert [p["rol"] for p in padres] == ["padre", "madre"]
    assert padres[0]["nombre"] == "Eusebio"
    assert padres[0]["apellido1"] == "Saenz de Navarrete"
    assert padres[1]["nombre"] == "Leocadia"
    assert padres[1]["apellido1"] == "Dopico"


def test_una_partida_sin_madre_no_inventa():
    fila = {"persona": {"nombre": "X", "apellido1": "Y"},
            "padre": {"nombre": "Juan", "apellido1": "Perez"},
            "madre": {"nombre": "", "apellido1": ""}}
    padres = linaje.padres_de(fila)
    assert len(padres) == 1 and padres[0]["rol"] == "padre"


def test_el_portal_tambien_tiene_al_padre_indexado():
    """Buscar 'Eusebio Saenz de Navarrete' devuelve SU bautismo y las partidas
    de sus hijos: eso es lo que permite sacar hermanos sin consultas de más."""
    filas = _filas("artxibo_busqueda_bautismo_apellido_compuesto.json")
    suyos = [f for f in filas
             if linaje.es_hijo_suyo(f, "Eusebio", "Saenz de Navarrete")]
    nombres = {f["persona"]["nombre"] for f in suyos}
    assert {"Victor", "Pedro", "Yluminado", "Primo", "Benito"} <= nombres
    propios = [f for f in filas
               if linaje.es_la_persona(f, "Eusebio", "Saenz de Navarrete")]
    assert [f["id"] for f in propios] == [6211434]


def test_eleccion_de_candidato_prefiere_su_parroquia():
    filas = _filas("artxibo_busqueda_bautismo_apellido_compuesto.json")
    elegido, confianza, alternativas = linaje.elegir_candidato(filas, {
        "nombre": "Eusebio", "apellido1": "Saenz de Navarrete",
        "apellido2": "", "ventana": (1850, 1867),
        "parroquia": "La Purísima Concepción", "municipio": "Navaridas"})
    assert elegido["id"] == 6211434                  # el Eusebio de 1858
    assert elegido["anio"] == 1858
    assert confianza == CONF_ALTA                    # misma parroquia
    assert alternativas == []


def test_eleccion_de_candidato_fuera_de_ventana_no_vale():
    """El Pablo de 1794 NO puede ser el padre de Eusebio (1858): 64 años de
    diferencia. Fuera de la ventana = no es candidato."""
    pablo_1794 = {
        "tipo": "bautismo", "id": 6210605, "fecha": "1794-01-15", "anio": 1794,
        "persona": {"nombre": "Pablo", "apellido1": "Saenz de Navarrete",
                    "apellido2": "Garcia", "completo": "Pablo S."},
        "parroquia": "La Purísima Concepción", "localidad": "Navaridas",
        "municipio": "NAVARIDAS", "url": "u", "fondo": "", "signatura": "",
        "folio": ""}
    elegido, confianza, _ = linaje.elegir_candidato([pablo_1794], {
        "nombre": "Pablo", "apellido1": "Saenz de Navarrete",
        "apellido2": "", "ventana": (1823, 1840),
        "parroquia": "La Purísima Concepción", "municipio": "Navaridas"})
    assert elegido is None and confianza == linaje.CONF_BAJA


def test_eleccion_lista_las_alternativas():
    """Si hay dos candidatos, se elige el de la parroquia y el otro se LISTA
    (el bot no esconde las dudas)."""
    otro = {
        "tipo": "bautismo", "id": 999999, "fecha": "1855-01-01", "anio": 1855,
        "persona": {"nombre": "Eusebio", "apellido1": "Saenz de Navarrete",
                    "apellido2": "Larrauri", "completo": "Eusebio S. Larrauri"},
        "padre": {"nombre": "Juan", "apellido1": "Saenz de Navarrete",
                  "apellido2": "", "completo": "Juan S."},
        "madre": {"nombre": "Ana", "apellido1": "Larrauri", "apellido2": "",
                  "completo": "Ana Larrauri"},
        "parroquia": "San Andrés Apóstol", "localidad": "Elciego",
        "municipio": "ELCIEGO", "fondo": "F0", "signatura": "s", "folio": "f",
        "url": "u"}
    buenas = _filas("artxibo_busqueda_bautismo_apellido_compuesto.json")
    elegido, confianza, alternativas = linaje.elegir_candidato(
        buenas + [otro], {"nombre": "Eusebio",
                          "apellido1": "Saenz de Navarrete", "apellido2": "",
                          "ventana": (1850, 1867),
                          "parroquia": "La Purísima Concepción",
                          "municipio": "Navaridas"})
    assert elegido["anio"] == 1858                  # el de la misma parroquia
    assert alternativas and alternativas[0]["id"] == 999999
    assert alternativas[0]["localidad"] == "Elciego"


# ============================== RASTREO ====================================

def test_rastreo_sube_tres_generaciones():
    """El caso real: Víctor (1885) → Eusebio (1858) + Leocadia (1863) →
    Pablo/Josefa y Román María/Fermina."""
    buscar = buscador_de_fixture()
    estado = linaje.rastrear([_victor()], buscar, pausa=None)

    linea = {p["nombre"]: p for p in estado["personas"].values()
             if p["rol"] == ROL_LINEA}
    assert linea["Victor"]["anio"] == 1885          # su partida, no la estimación
    assert linea["Victor"]["id"] == 6210597
    assert linea["Victor"]["estado"] == EST_IDENTIFICADO
    assert linea["Eusebio"]["anio"] == 1858
    assert linea["Leocadia"]["anio"] == 1863
    # y una generación más arriba, los dos apellidos que declaran sus partidas
    padres = {p["nombre"] for p in estado["personas"].values()
              if p["generacion"] == 2}
    assert {"Pablo", "Josefa", "Roman Maria", "Fermina"} <= padres
    # la línea sube por el padre Y por la madre
    assert linea["Leocadia"]["padres"]
    assert linea["Eusebio"]["padres"]


def test_rastreo_saca_a_los_hermanos():
    """De la partida de Eusebio salen sus padres; buscando a esos padres salen
    TODOS sus hijos: Eusebio y sus hermanos. Y los hermanos de Víctor también."""
    buscar = buscador_de_fixture()
    estado = linaje.rastrear([_victor()], buscar, pausa=None)
    laterales = {p["nombre"] for p in estado["personas"].values()
                 if p["rol"] == ROL_COLATERAL}
    assert {"Pedro", "Yluminado", "Primo", "Benito"} <= laterales   # hnos. Víctor
    assert {"Blas", "Catalina", "Leon", "Gabriel"} <= laterales     # hnos. Eusebio
    relaciones = [p["notas"] for p in estado["personas"].values()
                  if p["rol"] == ROL_COLATERAL]
    assert any("hermano/a de Victor" in r for r in relaciones)


def test_los_colaterales_no_se_rastrean():
    buscar = buscador_de_fixture()
    estado = linaje.rastrear([_victor()], buscar, pausa=None)
    for clave in estado["cola"]:
        assert estado["personas"][clave]["rol"] == ROL_LINEA


def test_rastreo_marca_los_eslabones_que_faltan():
    """El Pablo padre de Eusebio no tiene bautismo en la ventana 1823-1840 (el
    que hay de 1794 sería 64 años mayor: imposible). El bot NO lo inventa: lo
    deja como dudoso (sí aparece como progenitor de sus hijos) para pedirlo."""
    buscar = buscador_de_fixture()
    estado = linaje.rastrear([_victor()], buscar, pausa=None)
    faltan = {p["nombre"]: p for p in linaje.eslabones_que_faltan(estado)}
    assert "Pablo" in faltan
    assert faltan["Pablo"]["estado"] == linaje.EST_DUDOSO
    assert "1823-1840" in faltan["Pablo"]["notas"]
    # y el informe lo cuenta en su sección
    texto = linaje.redactar_markdown(estado)
    assert "Eslabones que faltan" in texto
    assert "G2 · **Pablo" in texto


def test_tope_de_consultas_deja_pendientes_y_se_reanuda(tmp_path):
    """Con 2 consultas por tanda el rastreo no acaba: guarda el estado y la
    pasada siguiente sigue por donde iba (sin repetir lo ya hecho)."""
    buscar = buscador_de_fixture()
    estado = linaje.rastrear([_victor()], buscar, pausa=None, max_consultas=2)
    assert estado["consultas"] == 2
    assert estado["cola"]                      # quedan pendientes
    identificados_antes = [c for c, p in estado["personas"].items()
                           if p["estado"] == EST_IDENTIFICADO]
    assert len(identificados_antes) == 1       # solo Víctor
    linaje.guardar_estado(estado, base=tmp_path)
    assert (tmp_path / "linaje_alava.json").exists()

    recuperado = linaje.cargar_estado(base=tmp_path)
    assert recuperado["consultas"] == 2
    listo = linaje.rastrear([], buscar, estado=recuperado, pausa=None)
    assert listo["consultas"] > 2
    clave_eusebio = linaje.clave_de("Eusebio", "Saenz de Navarrete")
    assert listo["personas"][clave_eusebio]["estado"] == EST_IDENTIFICADO


def test_tope_de_generaciones():
    buscar = buscador_de_fixture()
    estado = linaje.rastrear([_victor()], buscar, pausa=None,
                             max_generaciones=1)
    # lo procesado no pasa de la generación 1 (lo de la 2 queda marcado)
    procesadas = {p["generacion"] for p in estado["personas"].values()
                  if p["estado"] == EST_IDENTIFICADO}
    assert procesadas <= {0, 1}
    assert any("tope de 1 generaciones" in (p["notas"] or "")
               for p in estado["personas"].values())


def test_un_progenitor_repetido_no_se_duplica():
    """Eusebio aparece como padre en 5 partidas y como bautizado en la suya:
    una sola ficha en el estado."""
    buscar = buscador_de_fixture()
    estado = linaje.rastrear([_victor()], buscar, pausa=None)
    eusebios = [c for c, p in estado["personas"].items()
                if p["nombre"] == "Eusebio"]
    assert len(eusebios) == 1


def _por_nombre(estado: dict, nombre: str) -> dict:
    for persona in estado["personas"].values():
        if persona["nombre"] == nombre:
            return persona
    raise AssertionError(f"{nombre} no está en el estado")


def test_un_fallo_del_buscador_no_tira_el_trabajo():
    """Si el portal falla, la persona vuelve a la cola y no se marca como
    'sin resultado' (que sería mentira)."""
    llamadas = {"n": 0}

    def buscar(**kwargs):
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            raise RuntimeError("timeout de prueba")
        return []

    estado = linaje.rastrear([_victor()], buscar, pausa=None, max_consultas=1)
    victor = _por_nombre(estado, "Victor")
    assert victor["estado"] == linaje.EST_PENDIENTE      # sigue pendiente
    assert "FALLO" in " ".join(estado["log"])


def test_los_hermanos_se_reconocen_aunque_el_cura_escriba_regular():
    """En la partida de Primo (1891) el padre está escrito 'Eusevio' y en la de
    Alejandro (1896) 'Navarrete' sin el 'Saenz de'. Siguen siendo hermanos de
    Víctor, no 'de otra unión'."""
    buscar = buscador_de_fixture()
    estado = linaje.rastrear([_victor()], buscar, pausa=None)
    primo = _por_nombre(estado, "Primo")
    assert primo["rol"] == ROL_COLATERAL
    assert "hermano/a de Victor" in primo["notas"], primo["notas"]


def test_los_padres_heredan_la_parroquia_del_hijo_para_puntuar():
    """La parroquia del hijo es la pista de dónde bautizaron al padre: así
    Eusebio (misma parroquia) sale con confianza alta, y si el candidato está
    en otro pueblo sale marcado como bajo (pero sale: es información)."""
    buscar = buscador_de_fixture()
    estado = linaje.rastrear([_victor()], buscar, pausa=None)
    eusebio = _por_nombre(estado, "Eusebio")
    leocadia = _por_nombre(estado, "Leocadia")
    assert eusebio["confianza"] == CONF_ALTA          # La Purísima, Navaridas
    assert leocadia["confianza"] == CONF_ALTA
    assert eusebio["parroquia"] == "La Purísima Concepción"


# ============================== INFORME ====================================

def test_el_informe_cuenta_la_linea_los_parientes_y_lo_que_falta(tmp_path):
    buscar = buscador_de_fixture()
    estado = linaje.rastrear([_victor()], buscar, pausa=None)
    ruta = linaje.escribir_informe(estado, base=tmp_path)
    texto = Path(ruta).read_text(encoding="utf-8")
    assert "La línea, generación a generación" in texto
    assert "### Generación 0" in texto and "### Generación 1" in texto
    assert "**Victor Saenz de Navarrete** Dopico" in texto
    assert "**Eusebio Saenz de Navarrete** Tellaeche" in texto
    assert "Posibles parientes" in texto
    assert "hermano/a de Victor" in texto
    assert "Eslabones que faltan" in texto
    assert "fondo F006.329" in texto                    # la cita va dentro
    assert "getFicha?bauid=6210597" in texto            # y el enlace
    assert "getFicha?bauid=6211434" in texto


def test_el_informe_dice_que_no_toca_el_arbol(tmp_path):
    buscar = buscador_de_fixture()
    estado = linaje.rastrear([_victor()], buscar, pausa=None)
    texto = linaje.redactar_markdown(estado)
    assert "Nada está escrito en el árbol" in texto
    assert (tmp_path / "familia_conocida.json").exists() is False
