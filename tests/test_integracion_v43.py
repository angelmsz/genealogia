"""
tests/test_integracion_v43.py — Test de INTEGRACIÓN del bucle completo del
autopiloto (v4.3): fase 1 -> fase 2 -> commit -> frontera -> freno.

Se ejecuta en una COPIA del proyecto dentro de tmp_path (subproceso
propio) para no tocar NADA de los datos reales del usuario:
  - LLM simulado: cada prompt (filtrado, expansión, extracción,
    consolidación) recibe una respuesta guionizada y realista.
  - Tavily simulado: devuelve una única página con la partida de
    bautismo.
  - Recolectores desactivados.

Lo que demuestra (lo que el usuario pidió: "comprueba que funciona"):
  1. El ciclo 1 encuentra la partida, extrae el hallazgo, la cita pasa
     la auditoría DETERMINISTA contra el texto original, se consolida y
     el COMMIT crea la evidencia y los PADRES (salto de generación).
  2. El GEDCOM se exporta con ids estables y las fuentes.
  3. El registro append-only y los backups se escriben.
  4. El ciclo 2 no re-apunta la misma evidencia (dedupe) y el FRENO del
     autopiloto se dispara: el árbol no crece -> se para sin quemar
     presupuesto.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent

FAMILIA = {
    "personas": [
        {
            "nombre": "Isidro Merillas Panero",
            "apellido_paterno": "Merillas",
            "apellido_materno": "Panero",
            "sexo": "M",
            "nacimiento": {"fecha_aproximada": "1870",
                           "municipio": "Salas", "provincia": "Burgos"},
            "padre": "Nazario Merillas",
            "madre": "Obdulia Pelaz",
            "notas": "bisabuelo de quien reconstruye el árbol",
            "estado": "memoria",
        }
    ]
}

DRIVER = r'''
# -*- coding: utf-8 -*-
"""Driver del test de integración: ejecuta main.py --ciclo 2 en ESTA
carpeta (una copia limpia del proyecto) con LLM y Tavily simulados y
devuelve un resumen JSON en la última línea de stdout."""
import json
import os
import sys
from pathlib import Path

PROYECTO = Path(__file__).resolve().parent
sys.path.insert(0, str(PROYECTO))

# Claves de mentira ANTES de importar config (igual que conftest.py).
os.environ.setdefault("TAVILY_API_KEY", "clave-de-prueba")
os.environ.setdefault("OPENROUTER_API_KEY", "clave-de-prueba")
os.environ.setdefault("OCR_BACKEND", "off")

URL = "http://archivodeejemplo.es/libro/23"
PAGINA = ("Parroquia de San Juan Bautista de Salas libro de bautismos "
          "folio 23. En dicho dia dos de mayo de mil ochocientos setenta "
          "yo el cura bautice a Isidro Merillas Panero hijo legitimo de "
          "Nazario Merillas y de Obdulia Pelaz naturales de este pueblo "
          "fueron padrinos Juan Fernandez y Maria Diez")
LIMPIO = ("Libro de bautismos de Salas: Isidro Merillas Panero, hijo de "
          "Nazario Merillas y de Obdulia Pelaz, bautizado el 2 de mayo "
          "de 1870. " * 2)
CITA = ("yo el cura bautice a Isidro Merillas Panero hijo legitimo de "
        "Nazario Merillas y de Obdulia Pelaz")

HALLAZGO = {
    "persona": "Isidro Merillas Panero",
    "tipo_evento": "bautismo",
    "fecha_valor": "1870-05-02",
    "fecha_precision": "exacta",
    "fecha_original": "dos de mayo de mil ochocientos setenta",
    "lugar": "Salas",
    "otros_nombres": ["Nazario Merillas (padre)",
                      "Obdulia Pelaz (madre)"],
    "cita_literal": CITA,
    "url_fuente": URL,
    "confianza": "alta",
    "justificacion": "partida parroquial copiada literalmente",
    "origen": "",
}

CONSOLIDADO = {
    "personas": [{
        "nombre": "Isidro Merillas Panero",
        "eventos_confirmados": [{
            "tipo": "bautismo", "fecha": "1870-05-02", "lugar": "Salas",
            "fuente_url": URL, "cita": CITA, "origen": "web"}],
        "eventos_estimados": [], "contradicciones": [],
        "nuevas_pistas": [],
    }],
    "personas_nuevas_candidatas": [],
    "resumen_general": "bautismo de Isidro verificado con partida literal",
}


def fake_chat_json(modelo, system, user, **kw):
    """Router de prompts: devuelve la respuesta guionizada que cada fase
    espera recibir de su LLM."""
    if "onomástica" in system:                    # variantes de apellido
        return {"variantes": []}
    if "extractor de texto" in system:            # filtro fase 1
        return [{"url": URL, "relevante": True, "texto_limpio": LIMPIO}]
    if "planificador de búsquedas" in system:     # expansión
        return {"queries": []}
    if "genealogista experto analizando" in system:   # extracción
        return {"hallazgos": [dict(HALLAZGO)]}
    if "genealogista profesional" in system:      # consolidación / fusión
        return json.loads(json.dumps(CONSOLIDADO))
    if "auditor de citas" in system:              # auditoría (no llegará)
        return {"resultados": [{"indice": 0, "aparece": True}]}
    raise AssertionError("prompt no reconocido: " + system[:80])


def fake_buscar_tavily(query, dominios=None, max_results=5, avanzado=False):
    return [{"url": URL, "title": "Parroquia de Salas - libro bautismos",
             "content": "Isidro Merillas Panero bautismo 1870 Salas"}]


def fake_descargar_texto(url, timeout=25, conn=None):
    return PAGINA, ""


def main():
    import utils.llm
    import agent.fase1
    import agent.fase2
    import main as main_mod

    # Parches en los espacios de nombres que cada módulo importó.
    utils.llm.chat_json = fake_chat_json
    agent.fase1.chat_json = fake_chat_json
    agent.fase2.chat_json = fake_chat_json
    agent.fase1.buscar_tavily = fake_buscar_tavily
    agent.fase1.descargar_texto = fake_descargar_texto
    agent.fase1.recolectar = lambda objetivo, conn=None: []

    sys.argv = ["main.py", "--ciclo", "2"]
    main_mod.main()

    # ---- resumen de comprobaciones para el test ----
    def _leer(nombre, por_defecto=None):
        ruta = PROYECTO / nombre
        if not ruta.exists():
            return por_defecto
        try:
            return json.loads(ruta.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return por_defecto

    familia = _leer("familia_conocida.json", {"personas": []})
    personas = familia.get("personas", [])
    por_nombre = {p.get("nombre", ""): p for p in personas}
    isidro = por_nombre.get("Isidro Merillas Panero", {})
    hallazgos = _leer("arbol_hallazgos.json", [])
    registro = (PROYECTO / "registro_confirmaciones.jsonl")
    n_registro = sum(1 for _ in registro.open(encoding="utf-8")) \
        if registro.exists() else 0
    ged = (PROYECTO / "arbol.ged").read_text(encoding="utf-8") \
        if (PROYECTO / "arbol.ged").exists() else ""
    estado = _leer("estado_investigacion.json", {})
    corpus = _leer("corpus_bruto.json", [])

    resumen = {
        "n_personas": len(personas),
        "isidro_estado": isidro.get("estado", ""),
        "isidro_evidencias": len(isidro.get("evidencias", []) or []),
        "isidro_nac_fecha": (isidro.get("nacimiento", {})
                             .get("fecha_aproximada", "")),
        "padre_ficha": ("Nazario Merillas" in por_nombre),
        "madre_ficha": ("Obdulia Pelaz" in por_nombre),
        "padres_documentados": all(
            (por_nombre.get(n, {}) or {}).get("estado") == "documentado"
            for n in ("Nazario Merillas", "Obdulia Pelaz")),
        "cita_verificada": any(
            h.get("verificacion_cita") == "VERIFICADA"
            for h in hallazgos if isinstance(h, dict)),
        "cita_contra_original": any(
            h.get("verificacion_contra") == "texto_original"
            for h in hallazgos if isinstance(h, dict)),
        "n_registro": n_registro,
        "ged_indis": ged.count(" INDI"),
        "ged_refn_p0001": "REFN P0001" in ged,
        "ged_fuente": "@S1@ SOUR" in ged,
        "corpus_con_original": any(f.get("texto_original") == PAGINA
                                   for f in corpus),
        "investigados": len(estado.get("investigados", []) or []),
        "ids_unicos": len({p.get("id") for p in personas if p.get("id")}),
        "backups": len(list((PROYECTO / "backups").glob("*.json")))
        if (PROYECTO / "backups").exists() else 0,
    }
    print("RESULTADO_JSON " + json.dumps(resumen, ensure_ascii=False))


if __name__ == "__main__":
    main()
'''


@pytest.fixture()
def proyecto_copia(tmp_path):
    """Copia limpia del proyecto en tmp_path, con familia de prueba y
    driver de integración."""
    proj = tmp_path / "genealogia_v43_int"
    proj.mkdir()
    for item in ("main.py", "config.py", "utils", "scrapers", "agent"):
        src = RAIZ / item
        if src.is_dir():
            shutil.copytree(src, proj / item,
                            ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(src, proj / item)
    (proj / "familia_conocida.json").write_text(
        json.dumps(FAMILIA, ensure_ascii=False, indent=2), encoding="utf-8")
    (proj / "driver_integracion.py").write_text(DRIVER, encoding="utf-8")
    return proj


def test_autopiloto_ciclo_completo(proyecto_copia):
    r = subprocess.run(
        [sys.executable, "-X", "utf8", "driver_integracion.py"],
        cwd=proyecto_copia, capture_output=True, text=True, timeout=300,
        encoding="utf-8")
    assert r.returncode == 0, (
        f"el driver de integración falló\n--- stdout ---\n{r.stdout[-3000:]}"
        f"\n--- stderr ---\n{r.stderr[-3000:]}")
    # El freno del autopiloto debe dispararse en el ciclo 2 (árbol
    # estancado tras el dedupe de evidencias):
    assert "estancada" in r.stdout, (
        "el freno del autopiloto no se disparó en el ciclo 2")

    linea = [l for l in r.stdout.strip().splitlines()
             if l.startswith("RESULTADO_JSON ")]
    assert linea, "el driver no imprimió el resumen JSON"
    res = json.loads(linea[-1][len("RESULTADO_JSON "):])

    # 1. El árbol creció: evidencia comprometida y padres creados
    assert res["isidro_estado"] == "confirmado"
    assert res["isidro_evidencias"] >= 1
    assert res["isidro_nac_fecha"], "la fecha de nacimiento debe constar"
    assert res["padre_ficha"] and res["madre_ficha"], \
        "el commit debe crear las fichas de los padres (salto de generación)"
    assert res["padres_documentados"]
    assert res["n_personas"] == 3

    # 2. La cita está VERIFICADA contra el texto ORIGINAL (la garantía
    #    de entrada al árbol) y el hallazgo existe
    assert res["cita_verificada"]
    assert res["cita_contra_original"]

    # 3. Seguridad de datos: registro append-only y backups
    assert res["n_registro"] >= 3   # bautismo + 2 menciones_en_partida
    assert res["backups"] >= 1

    # 4. GEDCOM exportado con ids estables y fuentes
    assert res["ged_indis"] >= 3
    assert res["ged_refn_p0001"]
    assert res["ged_fuente"]
    assert res["ids_unicos"] == 3

    # 5. Corpus con el texto original preservado para auditorías futuras
    assert res["corpus_con_original"]

    # 6. El autopiloto marcó lo investigado (no repetirá el trabajo)
    assert res["investigados"] >= 1
