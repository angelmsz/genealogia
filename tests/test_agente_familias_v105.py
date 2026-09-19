"""
tests/test_agente_familias_v105.py — BLOQUE 6: reconstruir las FAMILIAS de un
apellido en una parroquia (opción 1.4 del menú).

Es "la forma de buscar" que se probó a mano en el AHDV, puesta en el bot:
  1. El apellido en los TRES sacramentos (bautismos, matrimonios, defunciones).
  2. Los BAUTISMOS agrupados por la PAREJA que declara cada partida: si N
     partidas dicen "hijo de Pablo y Josefa", esos N son hermanos, y lo dice el
     documento (no lo deduce nadie).
  3. Lo que FALTA: parejas con hijos pero SIN boda en el índice (los documentos
     que merece la pena pedir al archivo) y progenitores sin bautismo (la
     generación que queda por buscar).
  4. La salida: un informe para leer y un ÁRBOL PROVISIONAL en el formato del
     proyecto (`familia_conocida.json`), para revisar e importar a mano.

Dos cosas que no se pueden relajar:
  - **GRATIS**: esta opción NO llama a la IA. Solo lee el índice público.
  - **No toca el árbol real**: escribe ficheros aparte (`familias_archivo_vasco.md`
    y `arbol_archivo_vasco.json`).

Todo OFFLINE: el buscador es un doble (mismas claves que `parsear_resultados`)
y los ficheros van a un directorio temporal.
"""
from __future__ import annotations

import json
from pathlib import Path

import ramas as cli
from agent import agente_archivo as agente


# ====================== FILAS DEL ÍNDICE (como las reales) ==================

def _persona(triple) -> dict:
    if triple is None:
        return {}
    nombre, ape1, ape2 = triple
    return {"nombre": nombre, "apellido1": ape1, "apellido2": ape2,
            "completo": " ".join(x for x in (nombre, ape1, ape2) if x)}


def _fila(nombre="", ape1="", ape2="", anio=None, *, tipo="bautismo", id_=1,
          padre=None, madre=None, conyuge=None, parroquia="La Purisima",
          municipio="Navaridas") -> dict:
    """Una fila con los MISMOS campos que `artxibo.parsear_resultados`."""
    fila = {"tipo": tipo, "id": id_, "anio": anio,
            "fecha": f"{anio}-03-10" if anio else "",
            "persona": _persona((nombre, ape1, ape2)),
            "parroquia": parroquia, "municipio": municipio,
            "localidad": municipio, "diocesis": "Vitoria", "fondo": "F006.329",
            "signatura": "0193200301", "folio": "f.064 v.", "num_partida": "",
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
    partes.append(f"Parroquia {parroquia}, {municipio}.")
    fila["texto"] = " ".join(partes)
    return fila


# Los Dopico de Navaridas: 11 hermanos y la boda de los padres (1860).
RAMON_MARIA = ("Ramon Maria", "Dopico", "Fernandez")
FERMINA = ("Fermina Ysabel", "Guzman", "Fuentes")
ANTONIA_1861 = _fila("Antonia", "Dopico", "Guzman", 1861, id_=5723962,
                     padre=RAMON_MARIA, madre=FERMINA)
LEOCADIA_1863 = _fila("Leocadia", "Dopico", "Guzman", 1863, id_=5723968,
                      padre=RAMON_MARIA, madre=FERMINA)
BODA_DOPICO_1860 = _fila("Ramon Maria", "Dopico", "Fernandez", 1860,
                         tipo="matrimonio", id_=1467924, conyuge=FERMINA)
# Otra pareja del mismo apellido, SIN boda en el índice: es lo que hay que pedir.
VITORES = ("Vitores", "Guzman", "Lopez")
GAVINA = ("Gavina", "Fuentes", "Miranda")
JUAN_1844 = _fila("Juan", "Guzman", "Fuentes", 1844, id_=5857823,
                  padre=VITORES, madre=GAVINA)
CARLOTA_1856 = _fila("Carlota", "Guzman", "Fuentes", 1856, id_=5857827,
                     padre=VITORES, madre=GAVINA)
DIFUNTA_1863 = _fila("Formeria", "Dopico", "Guzman", 1863, tipo="defuncion",
                     id_=5723967)


class BuscadorFalso:
    """Doble del buscador: devuelve las filas del tipo que le piden y apunta las
    llamadas, para poder comprobar que se PASA EL PUEBLO y que se piden los tres
    sacramentos. Mismas claves que devuelve `parsear_resultados`."""

    def __init__(self, por_tipo: dict | None = None):
        self.por_tipo = por_tipo or {}
        self.llamadas: list[tuple] = []

    def __call__(self, apellido, tipo="bautismo", municipio=None, **kwargs):
        self.llamadas.append((apellido, tipo, municipio))
        return list(self.por_tipo.get(tipo, []))


def _buscador_por_tipo(mapa: dict) -> BuscadorFalso:
    return BuscadorFalso(mapa)


# ================= 1 y 2. LAS FAMILIAS SALEN DE LAS PARTIDAS ================

def test_los_hermanos_salen_de_la_pareja_que_declara_cada_partida():
    """Dos bautismos de la misma pareja son hermanos; los de otra pareja, otra
    familia; una defunción no es una familia (no declara padres)."""
    familias = agente.familias_por_pareja([ANTONIA_1861, LEOCADIA_1863,
                                           JUAN_1844, DIFUNTA_1863])
    assert len(familias) == 2
    # El hijo mayor es el que ordena: la familia de Vitores (1844) va primero.
    assert familias[0]["padre"] == "Vitores Guzman Lopez"
    primera = familias[1]
    assert primera["padre"] == "Ramon Maria Dopico Fernandez"
    assert primera["madre"] == "Fermina Ysabel Guzman Fuentes"
    assert [h["nombre"] for h in primera["hijos"]] == [
        "Antonia Dopico Guzman", "Leocadia Dopico Guzman"]
    assert [h["anio"] for h in primera["hijos"]] == [1861, 1863]
    assert primera["anio_ini"] == 1861 and primera["anio_fin"] == 1863
    assert familias[0]["anio_ini"] == 1844
    # Y lo que se guarda de cada hijo es lo que dice la fila, con su cita.
    assert primera["hijos"][0]["ape1"] == "Dopico"
    assert primera["hijos"][0]["id"] == 5723962
    assert "fondo F006.329" in primera["hijos"][0]["cita"]


def test_una_pareja_con_boda_en_el_indice_no_es_un_documento_pendiente():
    """Parejas SIN boda = documentos que pedir. La boda sale igual si los
    cónyuges están en el orden contrario (el portal no siempre ordena igual)."""
    familias = agente.familias_por_pareja([ANTONIA_1861, JUAN_1844])
    assert len(agente.parejas_sin_boda(familias, [BODA_DOPICO_1860])) == 1
    pendiente = agente.parejas_sin_boda(familias, [BODA_DOPICO_1860])[0]
    assert pendiente["padre"] == "Vitores Guzman Lopez"
    # Al revés (ella como persona del registro, él como cónyuge): la familia de
    # los Dopico tampoco falta; la que sigue pendiente es la de Vitores.
    al_reves = _fila("Fermina Ysabel", "Guzman", "Fuentes", 1860,
                     tipo="matrimonio", id_=1, conyuge=RAMON_MARIA)
    assert [f["padre"] for f in agente.parejas_sin_boda(familias, [al_reves])] == [
        "Vitores Guzman Lopez"]
    # Con la boda de los Dopico, esa familia ya no está pendiente.
    assert [f["padre"] for f in agente.parejas_sin_boda(
        familias, [BODA_DOPICO_1860])] == ["Vitores Guzman Lopez"]


def test_los_progenitores_sin_bautismo_son_la_generacion_que_falta():
    """Los padres que salen nombrados pero cuyo bautismo no aparece: hay que
    buscarlos por su nombre de pila + apellido (o pedirlos al archivo)."""
    bautismos = [ANTONIA_1861, LEOCADIA_1863, JUAN_1844, CARLOTA_1856]
    familias = agente.familias_por_pareja(bautismos)
    faltan = agente.progenitores_sin_bautismo(familias, bautismos)
    assert "Ramon Maria Dopico" in faltan
    assert "Fermina Ysabel Guzman" in faltan
    assert "Vitores Guzman" in faltan
    assert "Gavina Fuentes" in faltan
    # Si el progenitor SÍ está bautizado en lo buscado, no falta.
    con_madre_bautizada = bautismos + [
        _fila("Fermina Ysabel", "Guzman", "Fuentes", 1842, id_=5857822,
              padre=VITORES, madre=GAVINA)]
    faltan = agente.progenitores_sin_bautismo(
        agente.familias_por_pareja(con_madre_bautizada), con_madre_bautizada)
    assert "Fermina Ysabel Guzman" not in faltan


# ===================== 3. LA BÚSQUEDA: TRES SACRAMENTOS ====================

def test_la_1_3_se_queda_en_dos_sacramentos_y_la_1_4_va_a_los_tres():
    """Quién consulta qué, que es una decisión MEDIDA y no un gusto:

    - La búsqueda con IA (1.3) solo mira bautismos y matrimonios: la defunción
      no trae padres ni cónyuge (no añade familiares) y sí dobla las consultas
      por apellido (5 en vez de 3), con lo que la tanda se queda antes sin
      presupuesto de consultas.
    - La reconstrucción (1.4) es gratis y busca la biografía del apellido en la
      parroquia: va a los TRES sacramentos, defunciones incluidas.
    """
    assert agente.TIPOS_BUSQUEDA == ("bautismo", "matrimonio")
    assert agente.TIPOS_RECONSTRUIR == ("bautismo", "matrimonio", "defuncion")

def test_reconstruir_barre_los_tres_sacramentos_sin_repetir_filas(monkeypatch):
    """Pide el apellido en los tres sacramentos, pasa el PUEBLO y no repite la
    misma fila si el portal la devuelve en dos búsquedas distintas."""
    monkeypatch.setattr(agente.time, "sleep", lambda _s: None)
    buscar = _buscador_por_tipo({"bautismo": [ANTONIA_1861, LEOCADIA_1863],
                                 "matrimonio": [BODA_DOPICO_1860, ANTONIA_1861],
                                 "defuncion": [DIFUNTA_1863]})
    recon = agente.reconstruir(buscar, "Dopico", municipio="Navaridas")
    assert buscar.llamadas == [("Dopico", "bautismo", "Navaridas"),
                              ("Dopico", "matrimonio", "Navaridas"),
                              ("Dopico", "defuncion", "Navaridas")]
    assert recon["consultas"] == 3
    ids = [f["id"] for f in recon["filas"]]
    assert len(ids) == len(set(ids))                  # sin la repetida
    assert len(recon["bautismos"]) == 2
    assert len(recon["bodas"]) == 1
    assert len(recon["defunciones"]) == 1
    assert (recon["anio_min"], recon["anio_max"]) == (1860, 1863)
    # La boda de los padres quita esa familia de la lista de pendientes.
    assert recon["sin_boda"] == []


def test_si_el_buscador_falla_no_se_tumba_y_se_sigue(monkeypatch):
    """El portal a veces no contesta: eso NO puede tumbar la reconstrucción
    (se avisa y se sigue con los sacramentos que sí respondan)."""
    monkeypatch.setattr(agente.time, "sleep", lambda _s: None)
    avisos: list[str] = []

    def buscar(apellido, tipo="bautismo", municipio=None, **kwargs):
        if tipo == "matrimonio":
            raise RuntimeError("el portal no responde")
        return [ANTONIA_1861]

    recon = agente.reconstruir(buscar, "Dopico", municipio="Navaridas",
                               avisar=avisos.append)
    assert recon["consultas"] == 3
    assert len(recon["bautismos"]) == 1
    assert any("no respondió" in a for a in avisos)


# ======================= 4. LA SALIDA: INFORME Y ÁRBOL =====================

def _recon_completo(monkeypatch) -> dict:
    monkeypatch.setattr(agente.time, "sleep", lambda _s: None)
    buscar = _buscador_por_tipo({"bautismo": [ANTONIA_1861, LEOCADIA_1863,
                                              JUAN_1844, CARLOTA_1856],
                                 "matrimonio": [BODA_DOPICO_1860],
                                 "defuncion": [DIFUNTA_1863]})
    return agente.reconstruir(buscar, "Dopico", municipio="Navaridas")


def test_el_informe_ensena_las_familias_los_hermanos_y_lo_que_falta(monkeypatch):
    recon = _recon_completo(monkeypatch)
    texto = agente.informe_familias(recon)
    assert "Familias reconstruidas en el archivo vasco" in texto
    assert "# Familias reconstruidas" in texto
    assert "Ramon Maria Dopico Fernandez y Fermina Ysabel Guzman Fuentes" in texto
    assert "**1861** — Antonia Dopico Guzman" in texto
    assert "sig. 0193200301" in texto                  # la cita del archivo
    # Lo que FALTA: la pareja sin boda y la generación por buscar.
    assert "FALTAN" in texto and "Vitores Guzman Lopez ✕ Gavina Fuentes Miranda" in texto
    assert "progenitores sin bautismo" in texto and "- Ramon Maria Dopico" in texto
    # Y el aviso de que esto NO es el árbol real.
    assert "Nada de esto se ha escrito en el árbol" in texto


def test_el_arbol_provisional_es_coherente_y_esta_en_el_formato_del_proyecto(
        monkeypatch):
    """El árbol provisional usa el formato de `familia_conocida.json` y se
    comprueba solo: cada `padre`/`madre` nombrado existe como persona y cada
    hijo está apuntado en la lista de sus padres."""
    arbol = agente.arbol_de_familias(_recon_completo(monkeypatch))
    personas = arbol["personas"]
    por_nombre = {p["nombre"]: p for p in personas}
    assert len(por_nombre) == len(personas)            # sin duplicados
    assert len({p["id"] for p in personas}) == len(personas)
    for persona in personas:
        for campo in ("id", "nombre", "apellido_paterno", "apellido_materno",
                      "nacimiento", "padre", "madre", "hijos", "notas"):
            assert campo in persona
        assert persona["nacimiento"]["provincia"] == "Alava"
        for progenitor in (persona["padre"], persona["madre"]):
            if progenitor:
                assert progenitor in por_nombre
        for hijo in persona["hijos"]:
            assert hijo in por_nombre
            ficha = por_nombre[hijo]
            assert persona["nombre"] in (ficha["padre"], ficha["madre"])
    # Los ocho: los cuatro padres (dos parejas) y los cuatro hermanos.
    assert len(personas) == 8
    leocadia = por_nombre["Leocadia Dopico Guzman"]
    assert leocadia["padre"] == "Ramon Maria Dopico Fernandez"
    assert leocadia["nacimiento"]["fecha_aproximada"] == "1863"
    assert leocadia["notas"].startswith("Bautismo 1863-03-10")
    ramon = por_nombre["Ramon Maria Dopico Fernandez"]
    assert sorted(ramon["hijos"]) == ["Antonia Dopico Guzman",
                                      "Leocadia Dopico Guzman"]
    assert "POR BUSCAR" in ramon["notas"]


def test_la_misma_persona_no_entra_dos_veces_como_hija_y_como_madre(monkeypatch):
    """Fallo visto EJECUTANDO en Navaridas: la misma mujer entraba dos veces,
    como hija (con el año de su bautismo) y como madre (sin año, porque así la
    nombran las partidas de sus hijos). La persona es la misma: nombre + primer
    apellido, y el año solo separa cuando los dos lo traen y no cuadra.

    Aquí Leocadia (1863) es hija de Ramon Maria y, además, la madre de Victor
    (1885): tiene que salir UNA sola vez, con sus dos papeles y conservando el
    año y los apellidos largos que da su propio bautismo.
    """
    victor_1885 = _fila("Victor", "Saenz de Navarrete", "Dopico", 1885,
                        id_=6210597, padre=("Eusebio", "Saenz de Navarrete",
                                            "Tellaeche"),
                        madre=("Leocadia", "Dopico", ""))   # sin 2º apellido
    monkeypatch.setattr(agente.time, "sleep", lambda _s: None)
    recon = agente.reconstruir(
        _buscador_por_tipo({"bautismo": [LEOCADIA_1863, victor_1885],
                            "matrimonio": [], "defuncion": []}),
        "Dopico", municipio="Navaridas")
    personas = agente.arbol_de_familias(recon)["personas"]
    leocadias = [p for p in personas if p["nombre"].startswith("Leocadia")]
    assert len(leocadias) == 1
    leocadia = leocadias[0]
    assert leocadia["nombre"] == "Leocadia Dopico Guzman"
    assert leocadia["nacimiento"]["fecha_aproximada"] == "1863"
    assert leocadia["padre"] == "Ramon Maria Dopico Fernandez"
    assert leocadia["hijos"] == ["Victor Saenz de Navarrete Dopico"]
    assert "Bautismo 1863" in leocadia["notas"]
    assert "También sale como madre" in leocadia["notas"]
    # Y el enlace se ve por los dos lados.
    ramon = [p for p in personas
             if p["nombre"] == "Ramon Maria Dopico Fernandez"][0]
    assert "Leocadia Dopico Guzman" in ramon["hijos"]


def test_dos_hermanos_con_el_mismo_nombre_son_dos_personas(monkeypatch):
    """En Navaridas hay DOS hermanos "Julian Dopico Guzman" (1871 y 1877). El
    formato del proyecto enlaza por NOMBRE, así que si se unieran se perdería
    uno: se quedan separados y se distinguen con el año."""
    julian_1871 = _fila("Julian", "Dopico", "Guzman", 1871, id_=600,
                        padre=RAMON_MARIA, madre=FERMINA)
    julian_1877 = _fila("Julian", "Dopico", "Guzman", 1877, id_=601,
                        padre=RAMON_MARIA, madre=FERMINA)
    monkeypatch.setattr(agente.time, "sleep", lambda _s: None)
    recon = agente.reconstruir(
        _buscador_por_tipo({"bautismo": [julian_1871, julian_1877],
                            "matrimonio": [], "defuncion": []}),
        "Dopico")
    personas = agente.arbol_de_familias(recon)["personas"]
    julianes = sorted(p["nombre"] for p in personas
                      if p["nombre"].startswith("Julian"))
    assert julianes == ["Julian Dopico Guzman (1871)",
                        "Julian Dopico Guzman (1877)"]
    ramon = [p for p in personas
             if p["nombre"] == "Ramon Maria Dopico Fernandez"][0]
    assert sorted(ramon["hijos"]) == julianes
    for julian in personas:
        if julian["nombre"].startswith("Julian"):
            assert julian["padre"] == "Ramon Maria Dopico Fernandez"
            assert "se distingue con el año" in julian["notas"]


def test_se_escriben_el_informe_y_el_arbol_con_copia_previa(monkeypatch, tmp_path):
    recon = _recon_completo(monkeypatch)
    ruta_md = agente.escribir_informe_familias(recon, base=tmp_path)
    ruta_json = agente.escribir_arbol_json(recon, base=tmp_path)
    assert ruta_md.name == "familias_archivo_vasco.md"
    assert ruta_json.name == "arbol_archivo_vasco.json"
    assert "Leocadia Dopico Guzman" in ruta_md.read_text(encoding="utf-8")
    datos = json.loads(ruta_json.read_text(encoding="utf-8"))
    assert datos["personas"][0]["id"].startswith("AV")
    # Segunda pasada: se guarda copia .bak (los datos de familia no se pierden).
    agente.escribir_informe_familias(recon, base=tmp_path)
    assert (tmp_path / "familias_archivo_vasco.md.bak").exists()


# ========================= 5. EL CLI Y LA OPCIÓN 1.4 =======================

def test_el_cli_entiende_reconstruir_el_apellido_y_el_pueblo():
    args = cli._argumentos(["--reconstruir", "--apellido", "Dopico",
                            "--pueblo", "Navaridas"])
    assert args.reconstruir is True
    assert args.apellido == ["Dopico"]
    assert args.pueblo == "Navaridas"
    # Sin nada, no se activa (y `--apellido` puede repetirse).
    args = cli._argumentos(["--reconstruir", "--apellido", "Dopico",
                            "--apellido", "Guzman"])
    assert args.reconstruir is True and args.pueblo == ""
    assert args.apellido == ["Dopico", "Guzman"]
    assert cli._argumentos([]).reconstruir is False


def test_ejecutar_reconstruir_escribe_los_dos_ficheros_y_no_toca_el_arbol(
        monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(agente.time, "sleep", lambda _s: None)
    monkeypatch.setattr(cli, "_buscador_agente", lambda _max: _buscador_por_tipo(
        {"bautismo": [ANTONIA_1861, LEOCADIA_1863],
         "matrimonio": [BODA_DOPICO_1860], "defuncion": []}))
    codigo = cli.ejecutar_reconstruir(["Dopico"], pueblo="Navaridas",
                                      base=tmp_path)
    assert codigo == 0
    assert (tmp_path / "familias_archivo_vasco.md").exists()
    assert (tmp_path / "arbol_archivo_vasco.json").exists()
    salida = capsys.readouterr().out
    assert "GRATIS" in salida or "gratis" in salida
    assert "nada de esto se ha escrito en el árbol real" in salida.lower()
    # Nada de la familia se ha escrito en el árbol de verdad: solo esos dos.
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "arbol_archivo_vasco.json", "familias_archivo_vasco.md"]


def test_con_solo_listar_no_escribe_nada(monkeypatch, tmp_path):
    monkeypatch.setattr(agente.time, "sleep", lambda _s: None)
    monkeypatch.setattr(cli, "_buscador_agente", lambda _max: _buscador_por_tipo(
        {"bautismo": [ANTONIA_1861], "matrimonio": [], "defuncion": []}))
    assert cli.ejecutar_reconstruir(["Dopico"], base=tmp_path,
                                    solo_listar=True) == 0
    assert list(tmp_path.iterdir()) == []


def test_sin_apellido_usa_los_de_la_linea_y_si_no_hay_ninguno_avisa(
        monkeypatch, tmp_path, capsys):
    """Para no obligar a escribir el apellido: sin `--apellido` se cogen los de
    la rama. Si la rama viene vacía, se dice claro y no se sigue."""
    monkeypatch.setattr(agente.time, "sleep", lambda _s: None)
    monkeypatch.setattr(cli, "_buscador_agente", lambda _max: _buscador_por_tipo(
        {"bautismo": [ANTONIA_1861], "matrimonio": [], "defuncion": []}))
    monkeypatch.setattr(cli.ramas, "personas_de_rama", lambda *a, **k: [
        {"nombre": "Leocadia Dopico Guzman", "apellido_paterno": "Dopico",
         "apellido_materno": "Guzman"}])
    assert cli.ejecutar_reconstruir([], base=tmp_path) == 0
    assert "Dopico" in capsys.readouterr().out

    monkeypatch.setattr(cli.ramas, "personas_de_rama", lambda *a, **k: [])
    assert cli.ejecutar_reconstruir([], base=tmp_path) == 1
    assert "apellido" in capsys.readouterr().out.lower()


def test_la_reconstruccion_solo_tiene_indice_en_alava(tmp_path, capsys):
    """Zamora y Palencia no tienen índice nominal online: se dice y no se
    promete nada (en vez de devolver una lista vacía sin explicación)."""
    assert cli.ejecutar_reconstruir(["Merillas"], rama="zamora",
                                    base=tmp_path) == 1
    assert "Álava" in capsys.readouterr().out
