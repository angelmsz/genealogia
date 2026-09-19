"""
tests/test_agente_archivo_v105.py — BLOQUE 5: familiares en el archivo vasco
con IA (opción 1.3 del menú).

Cubre lo que decide si esto sirve o es una máquina de inventar familia:
  1. Que se busque por el PRIMER APELLIDO (la lista larga donde están los
     familiares) y no por el nombre completo.
  2. Que se abran las FICHAS (que dicen el nombre del nacido y el de sus
     padres) y que de TODOS los apellidos que aparecen —incluidos los de las
     MADRES— se vuelva a buscar: así entran las líneas de las mujeres.
  3. El cortafuegos contra la IA: solo aporta el NÚMERO de fila; el nombre, el
     año y la cita salen de la fila REAL. Un número que no existe o un apellido
     que no está en las filas se descartan.
  4. El sello de siempre (≥2 datos independientes): lo dice la partida / con 2
     datos / pista. La IA no puede subir un "pista" a "compatible".
  5. Topes, estado reanudable, presupuesto agotado y el informe.

Todo OFFLINE: el buscador, la apertura de fichas y la IA son dobles. Ni red ni
un solo token de verdad.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent import agente_archivo as agente
from utils.llm import PresupuestoExcedido


# ====================== FILAS DEL BUSCADOR (como las reales) ================

def _persona(triple) -> dict:
    if triple is None:
        return {}
    nombre, ape1, ape2 = triple
    return {"nombre": nombre, "apellido1": ape1, "apellido2": ape2,
            "completo": " ".join(x for x in (nombre, ape1, ape2) if x)}


def _fila(nombre="", ape1="", ape2="", anio=None, *, tipo="bautismo", id_=1,
          padre=None, madre=None, conyuge=None, parroquia="La Purisima",
          municipio="Navaridas", localidad="Navaridas", fondo="F006.329",
          signatura="0193200301", folio="f.195 r.") -> dict:
    """Una fila como las que devuelve scrapers/artxibo.py (mismos campos)."""
    fila = {"tipo": tipo, "id": id_, "anio": anio,
            "fecha": f"{anio}-03-10" if anio else "",
            "persona": _persona((nombre, ape1, ape2)),
            "parroquia": parroquia, "municipio": municipio,
            "localidad": localidad, "diocesis": "Vitoria", "fondo": fondo,
            "signatura": signatura, "folio": folio, "num_partida": "",
            "url": f"https://artxibo.euskadi.eus/ficha?{tipo}={id_}"}
    if tipo == "bautismo":
        fila["padre"] = _persona(padre)
        fila["madre"] = _persona(madre)
    if tipo == "matrimonio":
        fila["conyuge"] = _persona(conyuge)
    quien = fila["persona"]["completo"]
    partes = [f"{tipo.capitalize()} {fila['fecha']}.", f"{quien}."]
    if fila.get("padre", {}).get("nombre") or fila.get("madre", {}).get("nombre"):
        partes.append("Hijo de " + " y ".join(
            p["completo"] for p in (fila.get("padre"), fila.get("madre")) if p))
    partes.append(f"Parroquia {parroquia}, {localidad}.")
    partes.append(f"Índice del AHDV (fondo {fondo}, sig. {signatura}, "
                  f"folio {folio}).")
    fila["texto"] = " ".join(partes)
    return fila


# La partida real de Víctor (Navaridas, 1885): el punto de partida de todo.
VICTOR_1885 = _fila("Victor", "Saenz de Navarrete", "Dopico", 1885, id_=6210597,
                    padre=("Eusebio", "Saenz de Navarrete", "Tellaeche"),
                    madre=("Leocadia", "Dopico", "Guzman"))
# Un homónimo del siglo XVII: mismo apellido, nada que ver.
HOMONIMO_1620 = _fila("Juan", "Saenz de Navarrete", "", 1620, id_=99,
                      parroquia="San Pedro", municipio="Vitoria",
                      localidad="Vitoria")
# Un matrimonio de la familia (nombra al cónyuge).
BODA_1846 = _fila("Eusebio", "Saenz de Navarrete", "Tellaeche", 1846,
                  tipo="matrimonio", id_=111, conyuge=("Claudia", "Tellaeche",
                                                       "Aguirre"),
                  municipio="Amurrio", localidad="Amurrio")


def _ficha_falsa(tipo="bautismo", id_=None, **kwargs) -> dict:
    """Lo que devuelve artxibo.ficha(): campos y personas (el nacido y sus padres)."""
    return {"tipo": tipo, "id": id_,
            "url_ahdv": f"https://internet.ahdv-geah.org/ver?id_{id_}",
            "campos": {"Hijo": "Victor, Saenz de Navarrete, Dopico",
                       "Padre": "Eusebio, Saenz de Navarrete, Tellaeche",
                       "Madre": "Leocadia, Dopico, Guzman"},
            "personas": {
                "persona": _persona(("Victor", "Saenz de Navarrete", "Dopico")),
                "padre": _persona(("Eusebio", "Saenz de Navarrete", "Tellaeche")),
                "madre": _persona(("Leocadia", "Dopico", "Guzman")),
            }}


class BuscadorFalso:
    """Doble del buscador del archivo: apellido -> filas, y apunta las llamadas."""

    def __init__(self, por_apellido: dict | None = None):
        self.por_apellido = por_apellido or {}
        self.llamadas: list[tuple] = []

    def __call__(self, apellido, tipo="bautismo", anio_ini=None, anio_fin=None,
                 **kwargs):
        self.llamadas.append((apellido, tipo, anio_ini, anio_fin))
        filas = self.por_apellido.get(agente.clave_apellido(apellido), {})
        respuesta = filas.get(tipo, []) if isinstance(filas, dict) else filas
        if anio_ini or anio_fin:
            respuesta = [f for f in respuesta
                         if not f.get("anio")
                         or (anio_ini or 0) <= f["anio"] <= (anio_fin or 9999)]
        return list(respuesta)


def _ia_falsa(respuesta: dict | None = None):
    """Doble de la IA: devuelve siempre el mismo JSON y apunta lo que le llega."""
    caja = {"llamadas": [], "respuesta": respuesta or {}}

    def preguntar(estado, lote, apellido=""):
        caja["llamadas"].append({"apellido": apellido,
                                 "filas": [f["persona"]["completo"] for f in lote]})
        return caja["respuesta"]
    preguntar.caja = caja
    return preguntar


def _estado_con_victor() -> dict:
    estado = agente.estado_vacio()
    agente.anotar_conocido(estado, nombre="Victor",
                           apellido1="Saenz de Navarrete",
                           apellido2="Dopico", anio=1895,
                           municipio="Vitoria", parroquia="",
                           parentesco="bisabuelo (del árbol)")
    return estado


# ============ 1 y 2. LA BÚSQUEDA: PRIMER APELLIDO, FICHAS Y MUJERES =========

def test_se_busca_por_el_primer_apellido_y_se_abren_las_fichas():
    """El truco: pedir el apellido a secas devuelve la lista larga donde están
    los familiares; de los registros que tocan a la familia se abre la ficha."""
    buscador = BuscadorFalso({"saenz de navarrete": {"bautismo": [VICTOR_1885,
                                                                 HOMONIMO_1620]}})
    fichas: list[tuple] = []

    def abrir_ficha(tipo, id_):
        fichas.append((tipo, id_))
        return _ficha_falsa(tipo, id_)

    estado = _estado_con_victor()
    agente.apuntar_apellido(estado, "Saenz de Navarrete", "la línea")
    ia = _ia_falsa({"parientes": [{"fila": 1, "parentesco": "bisabuelo",
                                   "de_quien": "Victor", "confianza": "alta",
                                   "por_que": "los apellidos y el año cuadran"}],
                    "apellidos": [], "por_que_esos_apellidos": "seguir"})
    agente.investigar(estado, buscador, abrir_ficha, ia, max_llm=1,
                      max_consultas=2, avisar=lambda t: None, pausa=(0, 0))

    # Se ha buscado por el APELLIDO, nunca por un nombre completo
    assert buscador.llamadas
    assert all(apellido == "Saenz de Navarrete" for apellido, *_ in buscador.llamadas)
    # Se ha abierto la ficha del registro que toca a la familia (y solo esa)
    assert fichas == [("bautismo", 6210597)]
    # La persona se ha guardado con su cita del archivo y el enlace de la ficha
    victor = next(p for p in estado["personas"].values()
                  if p.get("anio") == 1885)
    assert victor["nombre"] == "Victor"
    assert victor["apellido1"] == "Saenz de Navarrete"
    assert victor["padres_texto"] == ["Eusebio Saenz de Navarrete Tellaeche",
                                      "Leocadia Dopico Guzman"]
    assert "F006.329" in victor["cita"] and "f.195 r." in victor["cita"]
    assert victor["ficha_url"].endswith("id_6210597")
    assert victor["nivel"] == agente.SELLO_RESERVAS


def test_los_apellidos_de_las_madres_tambien_se_buscan():
    """La mitad del árbol son mujeres: del registro salen los apellidos de la
    madre (Dopico, Guzman) y del padre (Tellaeche), y se vuelven a buscar."""
    buscador = BuscadorFalso({"saenz de navarrete": {"bautismo": [VICTOR_1885]}})
    estado = _estado_con_victor()
    agente.apuntar_apellido(estado, "Saenz de Navarrete", "la línea")
    ia = _ia_falsa({"parientes": [], "apellidos": [], "por_que_esos_apellidos": ""})
    agente.investigar(estado, buscador, None, ia, max_llm=1, max_consultas=4,
                      avisar=lambda t: None, pausa=(0, 0))

    cola = [estado["apellidos"][c]["apellido"] for c in estado["cola"]]
    assert "Dopico" in cola                       # la madre
    assert "Guzman" in cola                       # la madre (segundo apellido)
    assert "Tellaeche" in cola                    # el padre
    assert "Saenz de Navarrete" not in cola       # ya buscado
    # Y los apellidos de la madre se buscan de verdad en la siguiente vuelta
    agente.investigar(estado, buscador, None, ia, max_llm=3, max_consultas=6,
                      avisar=lambda t: None, pausa=(0, 0))
    assert any(a == "Dopico" for a, *_ in buscador.llamadas)


def test_la_ficha_tambien_aporta_apellidos_para_seguir():
    """La ficha es la que dice el nombre del nacido y el de sus padres: de ahí
    salen apellidos nuevos (los de la abuela, que en la fila no salían) para
    seguir buscando."""
    buscador = BuscadorFalso({"saenz de navarrete": {"bautismo": [VICTOR_1885]}})

    def ficha_con_abuela(tipo, id_):
        datos = _ficha_falsa(tipo, id_)
        datos["personas"]["abuela"] = _persona(("Josefa", "Aguirre", "Jáuregui"))
        datos["campos"]["Abuela"] = "Josefa, Aguirre, Jáuregui"
        return datos

    estado = _estado_con_victor()
    agente.apuntar_apellido(estado, "Saenz de Navarrete", "la línea")
    ia = _ia_falsa({"parientes": [], "apellidos": [], "por_que_esos_apellidos": ""})
    agente.investigar(estado, buscador, ficha_con_abuela, ia, max_llm=1,
                      max_consultas=4, avisar=lambda t: None,
                      pausa=(0, 0), fichas_por_apellido=1)
    assert estado["fichas"] == 1
    cola = [estado["apellidos"][c]["apellido"] for c in estado["cola"]]
    assert "Aguirre" in cola and "Jáuregui" in cola     # salen de la ficha


def test_una_busqueda_vacia_se_apunta_y_no_rompe():
    buscador = BuscadorFalso({"tellaeche": {"bautismo": []}})
    estado = _estado_con_victor()
    agente.apuntar_apellido(estado, "Tellaeche", "la línea")
    ia = _ia_falsa({"parientes": [], "apellidos": [], "por_que_esos_apellidos": ""})
    agente.investigar(estado, buscador, _ficha_falsa, ia, max_llm=1,
                      max_consultas=8, avisar=lambda t: None, pausa=(0, 0))
    ficha_ap = estado["apellidos"][agente.clave_apellido("Tellaeche")]
    assert ficha_ap["estado"] == "hecho" and ficha_ap["filas"] == 0
    assert estado["fichas"] == 0            # sin filas no hay ficha que abrir


# ============ 3. EL CORTAFUEGOS: LA IA NO PUEDE INVENTAR ====================

def test_la_ia_no_puede_inventar_filas_ni_apellidos():
    """La IA solo aporta el número de fila: un número que no existe y un
    apellido que no está en las filas se descartan."""
    buscador = BuscadorFalso({"saenz de navarrete": {"bautismo": [VICTOR_1885]}})
    estado = _estado_con_victor()
    agente.apuntar_apellido(estado, "Saenz de Navarrete", "la línea")
    ia = _ia_falsa({
        "parientes": [
            {"fila": 1, "parentesco": "bisabuelo", "de_quien": "Victor",
             "confianza": "alta", "por_que": "cuadra"},
            {"fila": 99, "parentesco": "hermano", "de_quien": "Victor",
             "confianza": "alta", "por_que": "invento"},
        ],
        "apellidos": ["Dopico", "Fernandezz", "Garcia"],
        "por_que_esos_apellidos": "seguir la línea de la madre"})
    agente.investigar(estado, buscador, None, ia, max_llm=1, max_consultas=4,
                      avisar=lambda t: None, pausa=(0, 0))

    guardados = [p for p in estado["personas"].values()
                 if p.get("nivel") != agente.NIVEL_ARBOL]
    assert len(guardados) == 1                     # la fila 99 no existe: fuera
    assert guardados[0]["nombre"] == "Victor"      # el nombre sale de la FILA
    assert not any("Fernandezz" in (p.get("nombre") or "")
                   for p in estado["personas"].values())
    cola = [estado["apellidos"][c]["apellido"] for c in estado["cola"]]
    assert "Fernandezz" not in cola and "Garcia" not in cola
    assert "Dopico" in cola                        # este sí está en la fila


def test_la_ia_no_puede_subir_un_pista_a_compatible():
    """El sello lo ponen los DATOS: si la fila solo comparte un apellido, se
    queda en 'pista' aunque la IA diga que es un hermano."""
    buscador = BuscadorFalso({"saenz de navarrete": {"bautismo": [
        _fila("Primo", "Saenz de Navarrete", "", 1891, id_=7,
              parroquia="Otra", municipio="Logrono", localidad="Logrono")]}})
    estado = _estado_con_victor()
    agente.apuntar_apellido(estado, "Saenz de Navarrete", "la línea")
    ia = _ia_falsa({"parientes": [{"fila": 1, "parentesco": "hermano tuyo",
                                   "de_quien": "Victor", "confianza": "alta",
                                   "por_que": "yo lo veo claro"}],
                    "apellidos": [], "por_que_esos_apellidos": ""})
    agente.investigar(estado, buscador, None, ia, max_llm=1, max_consultas=4,
                      avisar=lambda t: None, pausa=(0, 0))
    primo = next(p for p in estado["personas"].values() if p.get("nombre") == "Primo")
    assert primo["nivel"] == agente.SELLO_PISTA
    assert any("la IA" in m for m in primo["motivos"])   # queda su razón, pero no manda


# =================== 4. EL SELLO DE ≥2 DATOS (PIEZA PURA) ==================

def test_lo_que_dice_la_partida_entra_aunque_la_ia_no_diga_nada():
    """Si la fila nombra como padre a alguien ya identificado, ese pariente lo
    certifica el DOCUMENTO: entra en la lista aunque la IA no lo mencione (o
    aunque la IA falle). Es el caso de los hermanos: su bautismo nombra a los
    mismos padres."""
    estado = agente.estado_vacio()
    agente.anotar_conocido(estado, nombre="Eusebio",
                           apellido1="Saenz de Navarrete",
                           apellido2="Tellaeche", anio=1858,
                           municipio="Navaridas", parentesco="tatarabuelo")
    agente.apuntar_apellido(estado, "Saenz de Navarrete", "la línea")
    buscador = BuscadorFalso({"saenz de navarrete": {"bautismo": [VICTOR_1885]}})
    ia_muda = _ia_falsa({})            # la IA no dice nada de nadie
    agente.investigar(estado, buscador, None, ia_muda, max_llm=1,
                      max_consultas=2, avisar=lambda t: None, pausa=(0, 0))

    victor = next(p for p in estado["personas"].values() if p.get("anio") == 1885)
    assert victor["nivel"] == agente.SELLO_PARTIDA
    assert victor["parentesco"].startswith("hijo/a")
    assert "Eusebio" in victor["parentesco"]
    assert any("la partida nombra" in m for m in victor["motivos"])


def test_sin_clave_de_openrouter_se_busca_igual_pero_sin_ia():
    """Si no hay clave de la IA, no se muere: se busca en el archivo (gratis) y
    entra lo que certifica la partida. Nadie llama al LLM."""
    estado = agente.estado_vacio()
    agente.anotar_conocido(estado, nombre="Eusebio",
                           apellido1="Saenz de Navarrete", anio=1858,
                           municipio="Navaridas")
    agente.apuntar_apellido(estado, "Saenz de Navarrete", "la línea")
    buscador = BuscadorFalso({"saenz de navarrete": {"bautismo": [VICTOR_1885]}})

    def ia_que_no_debe_llamarse(*args, **kwargs):
        raise AssertionError("no se debe llamar a la IA sin clave")

    agente.investigar(estado, buscador, None, ia_que_no_debe_llamarse,
                      max_llm=5, max_consultas=4, usar_ia=False,
                      avisar=lambda t: None, pausa=(0, 0))
    assert estado["llamadas_llm"] == 0
    victor = next(p for p in estado["personas"].values() if p.get("anio") == 1885)
    assert victor["nivel"] == agente.SELLO_PARTIDA


def test_el_sello_cuenta_los_datos_independientes():
    estado = _estado_con_victor()
    # (a) La partida NOMBRA a alguien ya identificado: lo más fuerte.
    hija = _fila("Hija", "Saenz de Navarrete", "Aguirre", 1888,
                 padre=("Victor", "Saenz de Navarrete", "Dopico"))
    nivel, datos, motivos = agente.sello(hija, estado)
    assert nivel == agente.SELLO_PARTIDA and datos >= 2
    assert any("la partida nombra" in m for m in motivos)
    # (b) Dos apellidos de la familia + año en la ventana: con reservas.
    nivel, datos, _ = agente.sello(VICTOR_1885, estado)
    assert nivel == agente.SELLO_RESERVAS and datos >= 2
    # (c) Solo un apellido: pista.
    suelto = _fila("Alguien", "Saenz de Navarrete", "", 1700, id_=5,
                   parroquia="Otra", municipio="Zamora", localidad="Zamora")
    nivel, datos, _ = agente.sello(suelto, estado)
    assert nivel == agente.SELLO_PISTA and datos < 2


# ======================= 5. TOPES Y ESTADO REANUDABLE ======================

def test_los_topes_paran_y_el_estado_se_reanuda():
    buscador = BuscadorFalso({"dopico": {"bautismo": [VICTOR_1885]},
                              "guzman": {"bautismo": [VICTOR_1885]},
                              "tellaeche": {"bautismo": [VICTOR_1885]}})
    estado = _estado_con_victor()
    for apellido in ("Dopico", "Guzman", "Tellaeche"):
        agente.apuntar_apellido(estado, apellido, "la línea")
    ia = _ia_falsa({"parientes": [], "apellidos": [], "por_que_esos_apellidos": ""})

    agente.investigar(estado, buscador, None, ia, max_llm=1, max_consultas=10,
                      avisar=lambda t: None, pausa=(0, 0))
    assert estado["llamadas_llm"] == 1
    assert estado["buscados"] == 1
    # Los apellidos de la fila que ha salido (el nacido y sus padres) se han
    # puesto en la cola: Saenz de Navarrete no estaba, sale del registro.
    assert len(estado["cola"]) == 3
    assert any("llamadas a la IA alcanzadas" in n for n in estado["notas"])
    # Segunda tanda: sigue por donde iba
    agente.investigar(estado, buscador, None, ia, max_llm=1, max_consultas=10,
                      avisar=lambda t: None, pausa=(0, 0))
    assert estado["llamadas_llm"] == 2 and estado["buscados"] == 2
    assert len(estado["cola"]) == 2
    # Tope de consultas: también para y lo dice
    estado["cola"].append("dopico")
    agente.investigar(estado, buscador, None, ia, max_llm=99, max_consultas=0,
                      avisar=lambda t: None, pausa=(0, 0))
    assert any("consultas al archivo alcanzadas" in n for n in estado["notas"])


def test_el_estado_se_guarda_y_se_recupera(tmp_path: Path):
    estado = _estado_con_victor()
    agente.apuntar_apellido(estado, "Dopico", "de una fila de 1885")
    respaldo = agente.guardar_estado(estado, base=tmp_path)
    assert respaldo is None                      # la primera vez no hay .bak
    vuelto = agente.cargar_estado(base=tmp_path)
    assert vuelto["cola"] == ["dopico"]
    assert vuelto["apellidos"]["dopico"]["apellido"] == "Dopico"
    assert any(p["nombre"] == "Victor" for p in vuelto["personas"].values())
    # El .bak se crea al guardar por segunda vez (y no se pierde nada)
    respaldo2 = agente.guardar_estado(estado, base=tmp_path)
    assert respaldo2 and Path(respaldo2).exists()


def test_un_estado_roto_no_revienta(tmp_path: Path):
    (tmp_path / agente.AGENTE_VENTANA).write_text("{esto no es json",
                                                  encoding="utf-8")
    estado = agente.cargar_estado(base=tmp_path)
    assert estado["personas"] == {} and estado["cola"] == []


def test_presupuesto_agotado_para_limpio():
    estado = _estado_con_victor()
    agente.apuntar_apellido(estado, "Dopico", "la línea")
    buscador = BuscadorFalso({"dopico": {"bautismo": [VICTOR_1885]}})

    def ia_sin_dinero(estado_, lote, apellido=""):
        raise PresupuestoExcedido("gasto acumulado $0.0500 >= presupuesto $0.05")

    with pytest.raises(PresupuestoExcedido):
        agente.investigar(estado, buscador, None, ia_sin_dinero, max_llm=5,
                          max_consultas=10, avisar=lambda t: None)
    assert any("Presupuesto agotado" in n for n in estado["notas"])


# ============================= 6. EL INFORME ===============================

def test_el_informe_lista_los_familiares_con_su_cita():
    buscador = BuscadorFalso({"saenz de navarrete": {"bautismo": [VICTOR_1885]}})
    estado = _estado_con_victor()
    agente.apuntar_apellido(estado, "Saenz de Navarrete", "la línea")
    ia = _ia_falsa({"parientes": [{"fila": 1, "parentesco": "bisabuelo",
                                   "de_quien": "Victor", "confianza": "alta",
                                   "por_que": "cuadra"}],
                    "apellidos": ["Dopico"],
                    "por_que_esos_apellidos": "para seguir la línea de la madre"})
    agente.investigar(estado, buscador, _ficha_falsa, ia, max_llm=1,
                      max_consultas=4, avisar=lambda t: None)
    texto = agente.informe(estado)

    assert "# Familiares posibles en el archivo vasco" in texto
    assert "Victor Saenz de Navarrete Dopico" in texto      # el hallazgo
    assert "Eusebio Saenz de Navarrete Tellaeche" in texto  # los padres del registro
    assert "fondo F006.329" in texto and "f.195 r." in texto
    assert "La Purisima" in texto
    assert "compatible con reservas" in texto
    assert "Punto de partida: lo que ya sabías" in texto     # el árbol, aparte
    assert "| Saenz de Navarrete | 1 | 1 |" in texto         # la tabla de apellidos
    assert "para seguir la línea de la madre" in texto       # lo que dice la IA
    assert "se ha escrito en el árbol" in texto


def test_el_informe_dice_que_no_toca_el_arbol_y_cuenta_los_topes(tmp_path: Path):
    estado = _estado_con_victor()
    estado["coste_llm"] = 0.0123
    estado["llamadas_llm"] = 3
    estado["consultas"] = 9
    ruta = agente.escribir_informe(estado, base=tmp_path)
    texto = ruta.read_text(encoding="utf-8")
    assert ruta.name == agente.AGENTE_INFORME
    assert "coste $0.0123" in texto and "**3**" in texto and "**9**" in texto


# ======================= 7. MENÚ, CLI E INTEGRACIÓN ========================

def test_la_opcion_13_del_menu_existe_y_lanza_lo_correcto(monkeypatch):
    import menu_principal as menu
    assert "3" in menu.SUBMENUS["1"]["opciones"]
    assert menu.ACCIONES["1.3"] is menu.accion_agente_archivo
    assert menu.ETIQUETA_ACCION["1.3"] == "1 -> 3"
    lanzado: dict = {}

    def falso(argv, **kwargs):
        lanzado["argv"] = argv
        lanzado.update(kwargs)
        return 0

    monkeypatch.setattr(menu, "_lanzar_gastando", falso)
    assert menu.accion_agente_archivo("python.exe") == 0
    assert lanzado["argv"] == ["python.exe", "ramas.py", "--agente-archivo",
                               "--rama", "alava"]
    assert lanzado["opcion"] == "1.3"


def test_el_cli_entiende_las_opciones_nuevas():
    import ramas as cli
    args = cli._argumentos(["--agente-archivo", "--rama", "alava",
                            "--max-llm", "3", "--max-fichas", "2",
                            "--apellido", "Dopico"])
    assert args.agente_archivo is True and args.max_llm == 3
    assert args.max_fichas == 2 and args.apellido == ["Dopico"]


def test_el_agente_solo_vale_para_alava(tmp_path: Path):
    import ramas as cli
    assert cli.ejecutar_agente("zamora", base=tmp_path) == 1


def test_de_punta_a_punta_sin_red(tmp_path: Path, monkeypatch):
    """La opción 1.3 entera con el portal y la IA simulados: busca por apellido,
    abre la ficha, guarda la lista y escribe el informe."""
    familia = {"personas": [
        {"nombre": "Angel Saenz de Navarrete Perez de Palomares", "id": "P0006",
         "apellido_paterno": "Saenz de Navarrete",
         "apellido_materno": "Perez de Palomares",
         "nacimiento": {"fecha_aproximada": "1927", "municipio": "Vitoria",
                        "provincia": ""},
         "padre": "Victor Saenz de Navarrete Dopico", "madre": "",
         "hijos": [], "notas": ""},
        {"nombre": "Victor Saenz de Navarrete Dopico", "id": "P0012",
         "apellido_paterno": "Saenz de Navarrete", "apellido_materno": "Dopico",
         "nacimiento": {"fecha_aproximada": "", "municipio": "",
                        "provincia": ""},
         "padre": "", "madre": "", "hijos": [
             "Angel Saenz de Navarrete Perez de Palomares"],
         "notas": "hacia 1895"},
    ]}
    (tmp_path / "familia_conocida.json").write_text(
        json.dumps(familia, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "estado_investigacion.json").write_text("{}", encoding="utf-8")

    from scrapers import artxibo

    def buscar_falso(*, tipo="bautismo", apellido1="", **kwargs):
        if (agente.clave_apellido(apellido1) == "saenz de navarrete"
                and tipo == "bautismo"):
            return [dict(VICTOR_1885)]
        return []

    monkeypatch.setattr(artxibo, "buscar_sacramentales", buscar_falso)
    monkeypatch.setattr(artxibo, "ficha", lambda tipo, id_=None, **k:
                        _ficha_falsa(tipo, id_))
    from agent import agente_archivo as mod
    monkeypatch.setattr(mod, "AGENTE_DELAY", (0, 0))   # sin esperas en el test
    # La opción 1.3 solo llama a la IA si hay clave: en el test se pone una de
    # mentira para no depender de que exista .env (y no se gasta nada: la IA
    # está simulada).
    import config as config_mod
    monkeypatch.setattr(config_mod, "OPENROUTER_API_KEY", "clave-de-prueba")
    monkeypatch.setattr(mod, "preguntar_ia", _ia_falsa({        "parientes": [{"fila": 1, "parentesco": "bisabuelo", "de_quien": "Victor",
                       "confianza": "alta", "por_que": "cuadra"}],
        "apellidos": ["Dopico"], "por_que_esos_apellidos": "seguir"}))

    import ramas as cli
    codigo = cli.ejecutar_agente("alava", base=tmp_path, max_llm=2,
                                 max_consultas=6, max_fichas=2)
    assert codigo == 0
    informe = (tmp_path / agente.AGENTE_INFORME).read_text(encoding="utf-8")
    assert "Victor Saenz de Navarrete Dopico" in informe
    estado = agente.cargar_estado(base=tmp_path)
    assert estado["llamadas_llm"] >= 1 and estado["consultas"] >= 1
    # El árbol NO se ha tocado
    guardado = json.loads((tmp_path / "familia_conocida.json")
                          .read_text(encoding="utf-8"))
    assert len(guardado["personas"]) == 2      # el árbol sigue como estaba
    assert agente.clave_apellido("Dopico") in estado["apellidos"]
