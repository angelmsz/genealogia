"""
tests/test_gedcom.py — Validez del GEDCOM 5.5.1 generado (punto 9) e
identidad de personas por xref propio (punto 6).

Comprueba en el fichero .ged generado:
  - Estructura obligatoria: HEAD, 1 SUBM + registro SUBM, 2 NAME bajo
    SOUR, 2 TIME bajo DATE, TRLR final.
  - Homónimos: abuelo y nieto con el mismo nombre son DOS registros INDI
    con xrefs distintos (antes se fundían en una sola ficha).
  - Citas: SOUR anidado a nivel correcto, continuación con CONC (no CONT,
    que mete saltos de línea en mitad de la cita textual).
  - Registros SOUR: el URL va a NIVEL 1 (WWW/PUBL), NO como '2 URL'
    colgando de '1 TITL' (anidamiento erróneo que perdía la fuente).
  - Todas las líneas <= 255 caracteres (tope del estándar).
  - Todos los xrefs usados (HUSB/WIFE/CHIL/FAMC/FAMS/SOUR) están
    definidos; ninguna persona es su propio padre.
"""

from __future__ import annotations

import json
import re

import agent.gedcom as gedcom
from agent.gedcom import exportar_gedcom

URL = "http://archivodeejemplo.es/partida/123"


def _familia() -> dict:
    return {"personas": [
        # abuelo y nieto tocayos (punto 6): DOS personas distintas
        {"id": "P0001", "nombre": "Isidro Merillas",
         "apellido_paterno": "Merillas", "sexo": "M",
         "nacimiento": {"fecha_aproximada": "1785"},
         "padre": "", "madre": "", "conyuge": "",
         "estado": "confirmado",
         "evidencias": [{"tipo": "nacimiento", "fecha": "1785",
                         "lugar": "Salas", "fuente_url": URL,
                         "cita": "Isidro Merillas bautizado en 1785 hijo de "
                                 "Manuel Merillas y de Juana Rubio segun "
                                 "consta en el libro de bautismos de la "
                                 "parroquia de Salas de los Infantes, "
                                 "partida numero cuarenta y dos, folio "
                                 "trece del ano mil setecientos ochenta "
                                 "y cinco, siendo sus padrinos Joseph "
                                 "Gomez y Maria Diaz vecinos de la dicha "
                                 "villa"}]},
        {"id": "P0002", "nombre": "Isidro Merillas",
         "apellido_paterno": "Merillas", "sexo": "M",
         "nacimiento": {"fecha_aproximada": "1870"},
         "padre": "Isidro Merillas", "madre": "Maria Perez",
         "conyuge": "", "estado": "confirmado", "evidencias": []},
        {"id": "P0003", "nombre": "Maria Perez",
         "apellido_paterno": "Perez", "sexo": "F",
         "nacimiento": {"fecha_aproximada": "1845"},
         "padre": "", "madre": "", "conyuge": "Isidro Merillas",
         "estado": "confirmado", "evidencias": []},
    ]}


def _hallazgos() -> list:
    return [{
        "persona": "Isidro Merillas", "persona_id": "P0002",
        "tipo_evento": "bautismo", "fecha_valor": "02-05-1870",
        "confianza": "alta", "lugar": "Salas",
        "cita_literal": "Isidro Merillas hijo de Isidro Merillas y "
                        "Maria Perez bautizado el 2 de mayo de 1870",
        "url_fuente": URL,
    }]


def _generar(tmp_path, monkeypatch) -> list[str]:
    monkeypatch.setattr(gedcom, "BASE_DIR", tmp_path)
    (tmp_path / "familia_conocida.json").write_text(
        json.dumps(_familia(), ensure_ascii=False, indent=2),
        encoding="utf-8")
    (tmp_path / "arbol_hallazgos.json").write_text(
        json.dumps(_hallazgos(), ensure_ascii=False, indent=2),
        encoding="utf-8")
    exportar_gedcom()
    texto = (tmp_path / "arbol.ged").read_text("utf-8")
    return texto.splitlines()


def test_estructura_obligatoria(tmp_path, monkeypatch):
    lineas = _generar(tmp_path, monkeypatch)
    assert lineas[0] == "0 HEAD"
    assert lineas[-1] == "0 TRLR"
    assert "1 SUBM @SUB1@" in lineas                 # faltaba (punto 9)
    assert "0 @SUB1@ SUBM" in lineas
    assert "2 NAME Agente de investigación genealógica" in lineas
    assert "1 GEDC" in lineas and "2 VERS 5.5.1" in lineas
    assert "1 CHAR UTF-8" in lineas
    assert re.search(r"^2 TIME \d{2}:\d{2}:\d{2}$", "\n".join(lineas),
                     re.MULTILINE)


def test_tocayos_son_dos_registros_distintos(tmp_path, monkeypatch):
    """Punto 6: abuelo (1785) y nieto (1870) con el mismo nombre NO se
    fusionan: dos INDI con xrefs distintos y REFN propios."""
    lineas = _generar(tmp_path, monkeypatch)
    indis = [l for l in lineas if l.endswith(" INDI")]
    assert len(indis) >= 4          # abuelo + nieto + Maria + fantasma
    refns = [l.split()[-1] for l in lineas if l.startswith("1 REFN P")]
    assert "P0001" in refns and "P0002" in refns
    xrefs = [l.split()[1] for l in indis]
    assert len(set(xrefs)) == len(xrefs)   # xrefs únicos


def test_fuentes_sin_anidado_erroneo(tmp_path, monkeypatch):
    """Punto 9: prohibido '2 URL' colgando de '1 TITL' (el anidamiento
    que hacía perder las fuentes en Gramps/Ancestry)."""
    lineas = _generar(tmp_path, monkeypatch)
    for i, l in enumerate(lineas):
        if l.startswith("1 TITL"):
            siguiente = lineas[i + 1] if i + 1 < len(lineas) else ""
            assert not siguiente.startswith("2 URL"), \
                "URL anidado bajo TITL: el bug del punto 9 sigue ahí"
            assert not siguiente.startswith("2 FILE"), \
                "FILE anidado bajo TITL: el bug del punto 9 sigue ahí"
    # el URL va a nivel 1, en WWW y PUBL
    assert any(l.startswith("1 WWW http") for l in lineas)
    assert any(l.startswith("1 PUBL Disponible en:") for l in lineas)


def test_citas_con_conc_y_no_cont(tmp_path, monkeypatch):
    """Punto 9: las citas largas se parten con CONC (sin salto de línea),
    un nivel por debajo de la línea que continúan (regla del estándar);
    CONT solo para saltos intencionales ('Fuente: ...')."""
    lineas = _generar(tmp_path, monkeypatch)
    # hay al menos una cita larga (la del abuelo, >200 chars) partida
    assert any(l.startswith("4 CONC ") for l in lineas)
    # el bug de la v4.1: '3 CONT' (salto de línea en mitad de la cita,
    # al mismo nivel que PAGE) ya no puede aparecer
    assert not any(l.startswith("3 CONT ") for l in lineas)
    assert not any(l.startswith("4 CONT ") for l in lineas)
    # CONT solo donde el salto es intencional (la línea 'Fuente: URL')
    assert any(l.startswith("2 CONT Fuente:") for l in lineas)


def test_lineas_dentro_del_tope(tmp_path, monkeypatch):
    lineas = _generar(tmp_path, monkeypatch)
    for l in lineas:
        assert len(l) <= 255, f"línea de {len(l)} chars supera el tope: {l[:60]}..."


def test_xrefs_usados_definidos_y_sin_autopaternidad(tmp_path, monkeypatch):
    lineas = _generar(tmp_path, monkeypatch)
    definidos = {l.split()[1] for l in lineas
                 if l.startswith("0 @") and l.count("@") >= 2}
    usados = set()
    for l in lineas:
        m = re.match(r"^\d+ (?:HUSB|WIFE|CHIL|FAMC|FAMS|SOUR) (@\S+@)$", l)
        if m:
            usados.add(m.group(1))
    assert usados <= definidos, f"xrefs sin definir: {usados - definidos}"
    # FAMC apunta a una FAM (no al propio INDI): ninguna persona es su
    # propio padre
    for l in lineas:
        m = re.match(r"^0 (@I\d+@) INDI", l)
        if m:
            indi = m.group(1)
            idx = lineas.index(l)
            for sub in lineas[idx:idx + 40]:
                if sub.startswith("0 "):
                    break
                if sub.startswith("1 FAMC ") and sub.split()[2] == indi:
                    raise AssertionError("persona como su propio padre")


def test_hallazgo_adherido_por_persona_id(tmp_path, monkeypatch):
    """Punto 6: el NOTE del hallazgo va al INDI del nieto (P0002), no al
    del abuelo aunque compartan nombre."""
    lineas = _generar(tmp_path, monkeypatch)

    def _bloque_de(refn: str) -> list[str]:
        """Líneas del INDI que lleva ese REFN (hasta el próximo registro)."""
        idx = lineas.index(f"1 REFN {refn}")
        fin = idx
        while fin < len(lineas) and not lineas[fin].startswith("0 "):
            fin += 1
        # retrocede hasta el '0 @I..@ INDI' y avanza hasta el siguiente '0 '
        ini = idx
        while ini > 0 and not lineas[ini].startswith("0 @"):
            ini -= 1
        return lineas[ini:fin]

    trozo_nieto = "\n".join(_bloque_de("P0002"))
    assert "bautismo" in trozo_nieto and "2 de mayo de 1870" in trozo_nieto
    trozo_abuelo = "\n".join(_bloque_de("P0001"))
    assert "2 de mayo de 1870" not in trozo_abuelo
