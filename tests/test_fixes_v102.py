"""
tests/test_fixes_v102.py — Tests de regresión de los fallos del log de
ejecución real ("cmd de la egecucion (analizar).txt", noche del
2026-09-11).

Cada test reproduce UNO de los fallos tal y como apareció en el log y
comprueba que ya no ocurre:

  FIX 1  recolector_X falló: missing 1 required positional argument
         'objetivo' (x45 en el log) -> recolectar() pasa objetivo+conn.
  FIX 2  Traceback ... RuntimeError: LLM inaccesible tras 3 intentos
         (x2: mataba el programa entero) -> fase 2 degrada y el GEDCOM
         se exporta igual (test de integración con subproceso).
  FIX 3  503 'rate-limited upstream' con backoff de 2-4 s insuficiente
         -> _es_rate_limit + backoff largo (15/30/60 s).
  FIX 4  HISPAGEN no respondió: RemoteDisconnected en TODAS las
         consultas -> reintento con conexión limpia (SESSION.close).
  FIX 5  pdf2image falló: 'Unable to get page count' (PDF que era HTML)
         -> _procesar_contenido detecta HTML bajo URL .pdf.
  FIX 6  llama.cpp 500 Server Error por max_tokens == contexto completo
         y mensaje truncado que mostraba ".../v1/chat/c" -> max_tokens
         4096 + recorte del error a 120 car.
  FIX 8  resumen_noche.py no existía ("can't open file") -> existe y
         resume offline (0 tokens).

Todo offline: sin red, sin LLM real, sin tocar los datos del usuario.
"""

from __future__ import annotations

import inspect
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import scrapers.archivos as archivos
import scrapers.hispagen as hispagen
import scrapers.web as web
import utils.llm as llm
from utils.llm import _es_rate_limit

import scrapers.familysearch as familysearch

RAIZ = Path(__file__).resolve().parent.parent


# ======================= FIX 1 — recolectar() ==============================

def test_recolectar_pasa_objetivo_y_conn_a_los_cinco_conectores(monkeypatch):
    """FIX 1: en el log, SIGA/ADDO/Ensenada fallaban en CADA objetivo con
    'missing 1 required positional argument: objetivo' porque
    recolectar() los invocaba sin argumentos. Ahora los cinco
    conectores reciben (objetivo, conn)."""
    llamadas: list[tuple] = []

    def _fab(nombre):
        def _f(o, c=None):
            llamadas.append((nombre, o, c))
            return []
        return _f

    objetivo = {"municipio": "Vitoria", "provincia": "alava",
                "apellido_paterno": "Merillas"}
    conn_falso = object()

    monkeypatch.setattr(archivos, "recolector_siga", _fab("siga"))
    monkeypatch.setattr(archivos, "recolector_addo", _fab("addo"))
    monkeypatch.setattr(archivos, "recolector_ensenada", _fab("ensenada"))
    monkeypatch.setattr(hispagen, "recolector_hispagen",
                        _fab("hispagen"))
    monkeypatch.setattr(familysearch, "recolector_familysearch",
                        _fab("familysearch"))

    docs = archivos.recolectar(objetivo, conn=conn_falso)

    assert docs == []
    assert [n for n, _, _ in llamadas] == ["siga", "addo", "ensenada",
                                           "hispagen", "familysearch"]
    for _, o, c in llamadas:
        assert o is objetivo, "cada conector debe recibir EL objetivo"
        assert c is conn_falso, "cada conector debe recibir EL conn"


# ======================= FIX 3 — rate-limit =================================

class _ErrorConCodigo(Exception):
    def __init__(self, msg, status_code=None):
        super().__init__(msg)
        self.status_code = status_code


def test_es_rate_limit_detecta_los_casos_del_log():
    """FIX 3: el 503 real del log venía de 'temporarily rate-limited
    upstream' (previous_errors 429 de DeepInfra/Morph/Fireworks)."""
    # El texto EXACTO que OpenRouter devolvió esa noche:
    e = _ErrorConCodigo(
        "Error code: 503 - {'error': {'message': 'Provider returned "
        "error', 'code': 503, 'metadata': {'raw': '...deepseek-v4.1-flash "
        "is temporarily rate-limited upstream. Please retry shortly...'}}}")
    assert _es_rate_limit(e)
    assert _es_rate_limit(_ErrorConCodigo("x", status_code=429))
    assert _es_rate_limit(_ErrorConCodigo("x", status_code=503))
    assert _es_rate_limit(_ErrorConCodigo("Too Many Requests"))
    # Lo que NO es rate-limit:
    assert not _es_rate_limit(llm.LLMTimeoutHard("timeout duro"))
    assert not _es_rate_limit(_ErrorConCodigo("response_format no soportado",
                                              status_code=400))
    assert not _es_rate_limit(_ErrorConCodigo("Error de red cualquiera"))


def test_chat_json_backoff_largo_ante_rate_limit(monkeypatch):
    """FIX 3: ante un 429/503 el backoff pasa de 2-4 s a >=15 s (el
    intento fallido del log reintentaba dentro de la misma racha de
    saturación). Se comprueba durmiendo SIN dormir de verdad."""
    from types import SimpleNamespace

    dormidas: list[float] = []

    def _resp_ok():
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content='{"ok": true}'))],
            usage=None)

    intento = {"n": 0}

    def _create(**kwargs):
        intento["n"] += 1
        if intento["n"] == 1:
            raise _ErrorConCodigo(
                "Error code: 503 - Provider returned error "
                "(rate-limited upstream)", status_code=503)
        return _resp_ok()

    class _Completions:
        create = staticmethod(_create)

    class _Chat:
        completions = _Completions()

    class _LlmFalso:
        chat = _Chat()

    monkeypatch.setattr(llm, "llm", _LlmFalso())
    monkeypatch.setattr(llm.time, "sleep", lambda s: dormidas.append(s))

    resp = llm.chat_json("modelo/prueba", "sistema", "usuario",
                         intentos=3)
    assert resp == {"ok": True}
    assert dormidas and dormidas[0] >= 15, (
        f"el backoff ante rate-limit debe ser largo (>=15 s), "
        f"fue {dormidas}")


def test_chat_json_backoff_corto_ante_error_normal(monkeypatch):
    """Comportamiento clásico INTACTO: un error normal (p. ej. 500 sin
    rastro de rate-limit) sigue con el backoff corto de siempre (2-4 s)
    — la espera larga es SOLO para saturación."""
    from types import SimpleNamespace

    dormidas: list[float] = []

    def _resp_ok():
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content='{"ok": 1}'))],
            usage=None)

    intento = {"n": 0}

    def _create(**kwargs):
        intento["n"] += 1
        if intento["n"] == 1:
            raise _ErrorConCodigo("Internal Server Error raro",
                                  status_code=500)
        return _resp_ok()

    class _Completions:
        create = staticmethod(_create)

    class _Chat:
        completions = _Completions()

    class _LlmFalso:
        chat = _Chat()

    monkeypatch.setattr(llm, "llm", _LlmFalso())
    monkeypatch.setattr(llm.time, "sleep", lambda s: dormidas.append(s))

    assert llm.chat_json("modelo/prueba", "s", "u", intentos=2) == {"ok": 1}
    assert dormidas and dormidas[0] <= 4


# ======================= FIX 4 — HISPAGEN RemoteDisconnected ================

class _RespFake:
    def __init__(self, texto="<html><body></body></html>"):
        self.text = texto
        self.status_code = 200
        self.url = "https://www.hispagen.es/x"


def test_get_hispagen_reintenta_con_conexion_limia(monkeypatch):
    """FIX 4: el primer GET muere con RemoteDisconnected (cerrada por el
    servidor, como en TODAS las consultas del log); el reintento con el
    pool de conexiones cerrado debe funcionar."""
    cierres: list[int] = []
    llamadas = {"n": 0}

    class _SessionFake:
        def get(self, url, timeout=30, verify=False):
            llamadas["n"] += 1
            if llamadas["n"] == 1:
                import requests as _rq
                raise _rq.exceptions.ConnectionError(
                    "('Connection aborted.', RemoteDisconnected('Remote "
                    "end closed connection without response'))")
            return _RespFake()

        def close(self):
            cierres.append(1)

    monkeypatch.setattr(hispagen, "SESSION", _SessionFake())
    monkeypatch.setattr(hispagen.time, "sleep", lambda s: None)

    r = hispagen._get_hispagen("https://www.hispagen.es/x")
    assert r.status_code == 200
    assert llamadas["n"] == 2, "debe reintentar exactamente una vez"
    assert cierres, "debe cerrar el pool de conexiones antes de reintentar"


def test_get_hispagen_deja_pasar_el_error_si_persiste(monkeypatch):
    """Si la conexión sigue muerta tras el reintento, la excepción sube
    al llamador (cooldown del conector), como manda la convención."""

    class _SessionFake:
        def get(self, url, timeout=30, verify=False):
            import requests as _rq
            raise _rq.exceptions.ConnectionError("Connection aborted.")

        def close(self):
            pass

    monkeypatch.setattr(hispagen, "SESSION", _SessionFake())
    monkeypatch.setattr(hispagen.time, "sleep", lambda s: None)
    import requests as rq
    with pytest.raises(rq.exceptions.ConnectionError):
        hispagen._get_hispagen("https://www.hispagen.es/x")


# ======================= FIX 5 — HTML servido como .pdf =====================

def test_procesar_contenido_html_en_url_pdf():
    """FIX 5: 'Listado_Registro_EASA_DO_STS-ES.pdf' servía HTML (empezaba
    por b'<!DOC') y pdf2image reventaba con 'Unable to get page count'.
    Ahora se procesa como HTML y se recupera el texto de la página."""
    html = (b"<!DOCTYPE html><html><head><title>Registro EASA</title>"
            b"</head><body><p>Contenido del visor</p></body></html>")
    texto, motivo = web._procesar_contenido(
        html, "application/pdf",
        "https://ejemplo.es/Files/Listado_Registro_EASA_DO_STS-ES.pdf",
        "ejemplo.es")
    assert motivo == ""
    assert "Contenido del visor" in texto


def test_procesar_contenido_pdf_real_sigue_siendo_pdf(tmp_path):
    """Un PDF de verdad (cabecera %PDF-) sigue entrando en la cascada
    OCR, aunque la URL no acabe en .pdf."""
    pdf_min = (b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"
               b"trailer\n<< /Root 1 0 R >>\n%%EOF")
    texto, motivo = web._procesar_contenido(
        pdf_min, "application/pdf", "https://ejemplo.es/doc.bin",
        "ejemplo.es")
    # pypdf no saca texto de un PDF vacío -> motivo de vacío conocido,
    # PERO pasó por la rama PDF (no lo trató como HTML).
    assert motivo in ("pdf_roto", "")


# ======================= FIX 6 — llama.cpp max_tokens y recorte =============

def test_ocr_llamacpp_max_tokens_deja_sitio_para_la_imagen():
    """FIX 6: max_tokens 8192 == contexto '-c 8192' completo dejaba 0
    tokens para imagen+prompt y llama-server respondía 500. Ahora se
    piden 4096: la mitad del contexto queda para la imagen."""
    src = inspect.getsource(web._ocr_llamacpp)
    assert '"max_tokens": 4096' in src


def test_ocr_llamacpp_error_no_corta_la_url():
    """FIX 6: el recorte a 80 caracteres cortaba la URL del endpoint y
    el log mostraba 'http://localhost:8080/v1/chat/c' (parecía un
    endpoint roto). El recorte del fallo de página es ahora de 120."""
    src = inspect.getsource(web._ocr_llamacpp)
    assert "({str(e)[:120]}); se continúa" in src, (
        "el mensaje de fallo de página debe recortar a 120 caracteres")
    assert "({str(e)[:80]}); se continúa" not in src, (
        "el recorte a 80 cortaba la URL del endpoint por la mitad")


# ======================= FIX 2 — crash de fase 2 (integración) ==============

FAMILIA_FIX2 = {
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
            "estado": "memoria",
        }
    ]
}

DRIVER_FIX2 = r'''
# -*- coding: utf-8 -*-
"""Driver FIX 2: la consolidación LLM está CAÍDA (RuntimeError como en
el log). main.py --fase 2 NO debe morir con traceback: hallazgos
guardados, árbol refinado de emergencia y GEDCOM exportado."""
import json
import os
import sys
from pathlib import Path

PROYECTO = Path(__file__).resolve().parent
sys.path.insert(0, str(PROYECTO))
os.environ.setdefault("TAVILY_API_KEY", "clave-de-prueba")
os.environ.setdefault("OPENROUTER_API_KEY", "clave-de-prueba")

URL = "http://archivodeejemplo.es/libro/23"
TEXTO = ("Parroquia de Salas libro de bautismos folio 23. En dicho dia "
         "dos de mayo de mil ochocientos setenta yo el cura bautice a "
         "Isidro Merillas Panero hijo legitimo de Nazario Merillas y de "
         "Obdulia Pelaz")
CITA = ("yo el cura bautice a Isidro Merillas Panero hijo legitimo de "
        "Nazario Merillas y de Obdulia Pelaz")
HALLAZGO = {
    "persona": "Isidro Merillas Panero",
    "tipo_evento": "bautismo",
    "fecha_valor": "1870-05-02",
    "fecha_precision": "exacta",
    "lugar": "Salas",
    "cita_literal": CITA,
    "url_fuente": URL,
    "confianza": "alta",
    "justificacion": "partida literal",
    "origen": "",
}


def fake_chat_json(modelo, system, user, **kw):
    if "genealogista experto analizando" in system:   # extracción: OK
        return {"hallazgos": [dict(HALLAZGO)]}
    if "genealogista profesional" in system:          # consolidación: CAÍDA
        raise RuntimeError("LLM inaccesible tras 3 intentos: Error code: "
                           "503 - rate-limited upstream")
    if "auditor de citas" in system:                  # no debería llegar
        return {"resultados": []}
    raise AssertionError("prompt no reconocido: " + system[:60])


def main():
    import agent.fase2
    import main as main_mod
    agent.fase2.chat_json = fake_chat_json

    sys.argv = ["main.py", "--fase", "2"]
    try:
        main_mod.main()
        exit_ok = True
    except Exception as e:
        exit_ok = False
        print("EXCEPCION_NO_CAPTURADA " + type(e).__name__ + ": "
              + str(e)[:200])

    def _leer(nombre, por_defecto=None):
        ruta = PROYECTO / nombre
        if not ruta.exists():
            return por_defecto
        try:
            return json.loads(ruta.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return por_defecto

    hallazgos = _leer("arbol_hallazgos.json", [])
    refinado = _leer("arbol_refinado.json", {})
    ged_existe = (PROYECTO / "arbol.ged").exists()
    ged_vacio = (ged_existe
                 and (PROYECTO / "arbol.ged").stat().st_size == 0)
    res = {
        "exit_ok": exit_ok,
        "n_hallazgos": len(hallazgos),
        "cita_verificada": any(
            h.get("verificacion_cita") == "VERIFICADA"
            for h in hallazgos if isinstance(h, dict)),
        "refinado_existe": isinstance(refinado, dict) and bool(refinado),
        "pendiente": ("CONSOLIDACIÓN PENDIENTE" in
                      (refinado.get("resumen_general") or "")),
        "ged_existe": ged_existe,
        "ged_vacio": ged_vacio,
    }
    print("RESULTADO_JSON " + json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
'''


@pytest.fixture()
def proyecto_fix2(tmp_path):
    """Copia del proyecto + corpus con UN fragmento (la cita está en el
    texto original: la auditoría determinista la verifica sin LLM)."""
    proj = tmp_path / "genealogia_fix2"
    proj.mkdir()
    for item in ("main.py", "config.py", "resumen_noche.py",
                 "utils", "scrapers", "agent"):
        src = RAIZ / item
        if src.is_dir():
            shutil.copytree(src, proj / item,
                            ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(src, proj / item)
    (proj / "familia_conocida.json").write_text(
        json.dumps(FAMILIA_FIX2, ensure_ascii=False, indent=2),
        encoding="utf-8")
    URL = "http://archivodeejemplo.es/libro/23"
    TEXTO = ("Parroquia de Salas libro de bautismos folio 23. En dicho "
             "dia dos de mayo de mil ochocientos setenta yo el cura "
             "bautice a Isidro Merillas Panero hijo legitimo de Nazario "
             "Merillas y de Obdulia Pelaz")
    (proj / "corpus_bruto.json").write_text(
        json.dumps([{"url": URL, "titulo": "Libro bautismos Salas",
                     "texto_limpio": TEXTO, "texto_original": TEXTO,
                     "persona": "Isidro Merillas Panero"}],
                    ensure_ascii=False), encoding="utf-8")
    (proj / "driver_fix2.py").write_text(DRIVER_FIX2, encoding="utf-8")
    return proj


def test_fase2_llm_caido_no_mata_el_programa(proyecto_fix2):
    """FIX 2 (integración, subproceso): la consolidación LLM revienta
    igual que en el log (RuntimeError tras 3 intentos). ANTES: traceback
    y proceso muerto sin GEDCOM. AHORA: exit limpio, hallazgos en disco,
    árbol refinado de emergencia marcado como PENDIENTE y GEDCOM
    exportado."""
    r = subprocess.run(
        [sys.executable, "-X", "utf8", "driver_fix2.py"],
        cwd=proyecto_fix2, capture_output=True, text=True, timeout=300,
        encoding="utf-8")
    assert r.returncode == 0, (
        f"el driver murió\n--- stdout ---\n{r.stdout[-3000:]}"
        f"\n--- stderr ---\n{r.stderr[-3000:]}")
    assert "EXCEPCION_NO_CAPTURADA" not in r.stdout, (
        "main() dejó escapar una excepción que en el log mataba el "
        "proceso entero")

    linea = [l for l in r.stdout.strip().splitlines()
             if l.startswith("RESULTADO_JSON ")]
    assert linea, "el driver no imprimió el resumen JSON"
    res = json.loads(linea[-1][len("RESULTADO_JSON "):])

    assert res["exit_ok"], "main() debe terminar LIMPIO (sin traceback)"
    assert res["n_hallazgos"] >= 1, "los hallazgos extraídos se guardan"
    assert res["cita_verificada"], ("la auditoría determinista funciona "
                                    "aunque el LLM de consolidación esté "
                                    "caído")
    assert res["refinado_existe"], "arbol_refinado.json se escribe igual"
    assert res["pendiente"], ("el resumen deja claro que la consolidación "
                              "quedó pendiente")
    assert res["ged_existe"] and not res["ged_vacio"], (
        "el GEDCOM se exporta aunque la consolidación falle")


# ======================= FIX 8 — resumen_noche.py ===========================

def test_resumen_noche_existe_y_es_offline():
    """FIX 8: 'python resumen_noche.py' decía 'can't open file'. El
    script existe, no importa chat_json (0 LLM) y tiene main()."""
    ruta = RAIZ / "resumen_noche.py"
    assert ruta.exists(), "resumen_noche.py debe existir en la raíz"
    src = ruta.read_text(encoding="utf-8")
    assert "chat_json" not in src, "debe ser 100% offline (0 tokens)"
    assert "def main() -> int:" in src


@pytest.fixture()
def proyecto_resumen(tmp_path):
    """Copia mínima del proyecto con UN corpus y SIN hallazgos, para el
    resumen offline."""
    proj = tmp_path / "genealogia_resumen"
    proj.mkdir()
    for item in ("config.py", "resumen_noche.py", "utils", "scrapers"):
        src = RAIZ / item
        if src.is_dir():
            shutil.copytree(src, proj / item,
                            ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(src, proj / item)
    (proj / "corpus_bruto.json").write_text(
        json.dumps([{"url": "http://x.es/1", "texto_limpio": "abc",
                     "origen": "siga", "texto_original": "abc"}]),
        encoding="utf-8")
    (proj / "familia_conocida.json").write_text(
        json.dumps(FAMILIA_FIX2, ensure_ascii=False), encoding="utf-8")
    return proj


def test_resumen_noche_arranca_y_resume(proyecto_resumen):
    """El script corre en un proyecto con datos y sale con código 0
    pintando el corpus."""
    r = subprocess.run(
        [sys.executable, "-X", "utf8", "resumen_noche.py"],
        cwd=proyecto_resumen, capture_output=True, text=True, timeout=120,
        encoding="utf-8")
    assert r.returncode == 0, (
        f"fallo inesperado\n--- stdout ---\n{r.stdout[-2000:]}"
        f"\n--- stderr ---\n{r.stderr[-2000:]}")
    assert "1. Corpus" in r.stdout
    assert "siga" in r.stdout, "cuenta el origen de los fragmentos"


def test_resumen_noche_proyecto_vacio_devuelve_1(tmp_path):
    """Sin artefactos que resumir, avisa y devuelve código 1 (para
    scripts/lanzador), sin traceback."""
    proj = tmp_path / "genealogia_vacia"
    proj.mkdir()
    for item in ("config.py", "resumen_noche.py", "utils", "scrapers"):
        src = RAIZ / item
        if src.is_dir():
            shutil.copytree(src, proj / item,
                            ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(src, proj / item)
    r = subprocess.run(
        [sys.executable, "-X", "utf8", "resumen_noche.py"],
        cwd=proj, capture_output=True, text=True, timeout=120)
    assert r.returncode == 1
    assert "Traceback" not in r.stderr
