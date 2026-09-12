"""
tests/test_probar_ocr_v90.py — Flag --probar-ocr (v9.0, cascada v10.0).

Prueba el modo "UN solo PDF sin gastar": la cascada se ejecuta tal cual
(sobre el MISMO scrapers.web._extraer_texto_pdf del agente) y el coste de
la prueba debe ser $0 — v10.0: el OCR es 100% LOCAL y no hay etapa de
pago que omitir (los tests que afirmaban la escalada a Gemini se
reescribieron para afirmar lo contrario: NUNCA se escala a la nube).

Ejecución:  python -m pytest tests/test_probar_ocr_v90.py -q
"""
from __future__ import annotations

import io

import pytest
import requests

import main
import scrapers.web as web


# Resetea el estado del cliente llamacpp entre tests (flags de "ya avisado").
@pytest.fixture(autouse=True)
def _reset_llamacpp():
    web._reset_llamacpp_estado()
    yield
    web._reset_llamacpp_estado()


# ============================== FIXTURES ====================================

def _pdf_con_texto(texto: str) -> bytes:
    """PDF mínimo VÁLIDO (xref correcto) con capa de texto: pypdf puede
    extraer `texto` de él (emula un PDF 'nacido digital')."""
    contenido = f"BT /F1 12 Tf 72 720 Td ({texto}) Tj ET".encode(
        "latin-1", "replace")
    objetos = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
         b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"),
        (b"<< /Length " + str(len(contenido)).encode()
         + b" >>\nstream\n" + contenido + b"\nendstream"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    salida = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objetos, start=1):
        offsets.append(len(salida))
        salida += f"{i} 0 obj ".encode() + obj + b"\nendobj\n"
    xref_pos = len(salida)
    salida += b"xref\n0 " + str(len(objetos) + 1).encode() + b"\n"
    salida += b"0000000000 65535 f \n"
    for off in offsets:
        salida += f"{off:010d} 00000 n \n".encode()
    salida += (b"trailer << /Size " + str(len(objetos) + 1).encode()
               + b" /Root 1 0 R >>\nstartxref\n" + str(xref_pos).encode()
               + b"\n%%EOF\n")
    return bytes(salida)


def _redirigir_bd(monkeypatch, tmp_path):
    """Redirige la caché SQLite a tmp_path (no tocar la BD real)."""
    import config
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)


# ============================== TESTS =======================================

def test_pdf_fixture_legible():
    """Sanity del fixture: pypdf extrae el texto del PDF mínimo."""
    import pypdf
    texto = "Partida de bautismo " + "datos " * 30
    lector = pypdf.PdfReader(io.BytesIO(_pdf_con_texto(texto)))
    assert len(lector.pages) == 1
    assert texto in (lector.pages[0].extract_text() or "")


def test_probar_ocr_pdf_no_existe(capsys):
    """Ruta inexistente -> exit 1 con mensaje claro, sin traceback."""
    rc = main._probar_ocr("/no/existe/acta.pdf", False, False)
    capturado = capsys.readouterr()
    assert rc == 1
    assert "No encuentro el PDF" in capturado.out + capturado.err


def test_probar_ocr_pypdf_gana_sin_gastar(monkeypatch, tmp_path, capsys):
    """PDF con capa de texto -> pypdf gana y coste $0. v10.0: no hay
    ninguna llamada de pago que interceptar — el OCR es 100% local."""
    _redirigir_bd(monkeypatch, tmp_path)
    pdf = tmp_path / "acta.pdf"
    pdf.write_bytes(_pdf_con_texto("Partida de bautismo " + "datos " * 30))

    rc = main._probar_ocr(str(pdf), manuscrito=False, sin_cache=False)
    capturado = capsys.readouterr()
    salida = capturado.out + capturado.err

    assert rc == 0
    assert "pypdf" in salida                # backend ganador
    assert "$0.0000" in salida              # coste exactamente cero
    assert "OCR 100% local" in salida       # mensaje de la cascada v10.0
    assert "TEXTO EXTRAÍDO" in salida       # muestra la transcripción


def test_probar_ocr_fallo_local_avisado(monkeypatch, tmp_path, capsys):
    """v10.0 (reescrito del viejo test de '[GEMINI OMITIDO]'): manuscrito
    + llama-server caído -> fallo EXPLÍCITO, $0.0000 y NINGUNA mención a
    escalada a la nube. El PDF queda sin texto y sin cachear."""
    _redirigir_bd(monkeypatch, tmp_path)
    monkeypatch.setattr(web, "OCR_BACKEND", "llamacpp")
    monkeypatch.setattr(web, "_pdf_a_imagenes",
                        lambda b, m: ([b"img"], 1, 1))
    monkeypatch.setattr(web, "_ocr_llamacpp", lambda imgs: ("", 0.0))
    # El cliente detectó el servidor caído (fracaso transitorio).
    monkeypatch.setattr(web, "_llamacpp_servidor_caido", lambda: True)
    # Header: llamacpp_disponible -> conexión rechazada.
    monkeypatch.setattr(
        requests, "get",
        lambda url, timeout=None, **k: (_ for _ in ()).throw(
            requests.exceptions.ConnectionError("refused")))

    pdf = tmp_path / "partida.pdf"
    pdf.write_bytes(b"pdf-falso")   # pypdf no puede leerlo (sin capa)
    rc = main._probar_ocr(str(pdf), manuscrito=True, sin_cache=True)
    capturado = capsys.readouterr()
    salida = capturado.out + capturado.err

    assert rc == 1                         # la cascada local no sacó texto
    assert "ocr_local_fallido" in salida   # backend de fallo explícito
    assert "$0.0000" in salida
    assert "NO responde" in salida         # estado del servidor arriba
    assert "MANUSCRITO" in salida
    assert "OCR 100% local" in salida
    # v10.0: ya no existe el parche "[GEMINI OMITIDO]" NI la escalada.
    assert "GEMINI OMITIDO" not in salida
    assert "escalar" not in salida.lower() or "no escala" in salida.lower()


def test_probar_ocr_cachea_y_avisa(monkeypatch, tmp_path, capsys):
    """La 2ª ejecución con el mismo PDF sale de la caché (sufijo _cache y
    aviso de que se puede re-procesar con --sin-cache)."""
    _redirigir_bd(monkeypatch, tmp_path)
    pdf = tmp_path / "acta.pdf"
    pdf.write_bytes(_pdf_con_texto("Acta de matrimonio " + "testigo " * 30))

    rc1 = main._probar_ocr(str(pdf), False, False)
    capsys.readouterr()
    rc2 = main._probar_ocr(str(pdf), False, False)
    capturado = capsys.readouterr()
    salida = capturado.out + capturado.err

    assert rc1 == 0 and rc2 == 0
    assert "pypdf_cache" in salida
    assert "CACHÉ" in salida
    assert "$0.0000" in salida


def test_probar_ocr_sin_cache_reprocesa(monkeypatch, tmp_path, capsys):
    """--sin-cache: la 2ª ejecución NO lee la caché (conn=None) y vuelve
    a ganar pypdf desde cero."""
    _redirigir_bd(monkeypatch, tmp_path)
    pdf = tmp_path / "acta.pdf"
    pdf.write_bytes(_pdf_con_texto("Certificado de defunción " + "x " * 60))

    main._probar_ocr(str(pdf), False, False)
    capsys.readouterr()
    rc2 = main._probar_ocr(str(pdf), False, sin_cache=True)
    capturado = capsys.readouterr()
    salida = capturado.out + capturado.err

    assert rc2 == 0
    assert "pypdf_cache" not in salida
    assert "DESACTIVADA" in salida


def test_flag_probar_ocr_existe(monkeypatch, capsys):
    """El CLI registra --probar-ocr (con --manuscrito). v10.0: el flag
    --con-gemini YA NO EXISTE (no hay escalada a la nube que permitir)."""
    import contextlib
    monkeypatch.setattr("sys.argv", ["main.py", "--help"])
    with contextlib.suppress(SystemExit):
        main.main()
    ayuda = capsys.readouterr().out
    assert "--probar-ocr" in ayuda
    assert "--manuscrito" in ayuda
    assert "--con-gemini" not in ayuda
