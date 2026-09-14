"""
tests/test_sin_claves_v105.py — BLOQUE 1 (arreglo 1): el bot arranca SIN .env
y solo exige las claves que de verdad va a usar (v10.4.2).

QUÉ CAMBIA Y POR QUÉ
--------------------
Antes, `config.py` hacía ``raise SystemExit`` al importarse si faltaba
TAVILY_API_KEY u OPENROUTER_API_KEY. Consecuencias reales:

  - "no tengo claves" se convertía en "no puedo ni arrancar": ni --probar-ocr
    (OCR 100% local, no usa la nube), ni --frontera, ni --reclasificar, ni
    --diagnostico, ni resumen_noche.py funcionaban sin .env;
  - los tests y lanzador.py tenían que inventarse claves falsas solo para
    poder importar;
  - y sobre todo: la exigencia estaba en el sitio equivocado, así que un flujo
    que SÍ gasta habría muerto a mitad, no al principio.

Ahora la exigencia vive en una sola puerta, `config.validar_credenciales_api()`
(alias documentado: la única función que decide si falta algo), y `main.py` la
llama SOLO en los dos flujos que gastan: --ensenada (OpenRouter) y el flujo
principal fase 1/fase 2/--ciclo (Tavily si hay fase 1, y OpenRouter siempre).
El mensaje dice qué falta, para qué sirve y dónde se pone.

Los tests usan el harness aislado: ningún hijo lee el .env real ni escribe en
el proyecto (ver tests/harness_aislado.py).

Ejecución:  python -m pytest tests/test_sin_claves_v105.py -q
"""
from __future__ import annotations

import inspect

import pytest

import config
import main
from tests.harness_aislado import (copiar_entradas, huellas_de_estado, lanzar,
                                   lanzar_sin_claves)
# Reutiliza el PDF mínimo con capa de texto que ya usa el test de --probar-ocr
# (es un fixture compartido: se importa para no duplicar 25 líneas de PDF).
from tests.test_probar_ocr_v90 import _pdf_con_texto

# Un "entorno sin .env" de verdad: se anula la carga de dotenv ANTES de que
# config.py importe nada, y se quitan las variables del entorno.
PRE_SIN_ENV = (
    "import dotenv, os\n"
    "dotenv.load_dotenv = lambda *a, **k: None\n"
    "os.environ.pop('TAVILY_API_KEY', None)\n"
    "os.environ.pop('OPENROUTER_API_KEY', None)\n"
)
CLAVES_FALSAS = ("clave-de-prueba", "clave-de-prueba")


# ==================== 1. IMPORTAR SIN CLAVES NO MUERE =======================

def test_importar_main_sin_env_no_muere(tmp_path):
    """Sin .env y sin variables de entorno: importar config y main NO muere, y
    las claves se ven vacías (antes: SystemExit al importar config)."""
    extra = ("print('CLAVES_VISTAS:', repr(config.TAVILY_API_KEY), "
             "repr(config.OPENROUTER_API_KEY))\n"
             "print('FALTAN:', config.claves_faltantes())")
    r = lanzar(["--help"], tmp_path, claves=(None, None), pre=PRE_SIN_ENV,
               extra=extra)

    assert r.returncode == 0, r.stdout[-600:] + r.stderr[-600:]
    assert "CLAVES_VISTAS: None None" in r.stdout
    assert "FALTAN: ['TAVILY_API_KEY', 'OPENROUTER_API_KEY']" in r.stdout
    assert "Faltan claves" not in r.stderr
    assert "--probar-ocr" in r.stdout            # main() llegó a arrancar


# ==================== 2. LA PUERTA SOLO EN FLUJOS QUE GASTAN ================

def test_la_puerta_de_claves_se_llama_solo_en_flujos_que_gastan():
    """validar_credenciales_api() aparece exactamente 2 veces en main() y
    siempre DESPUÉS de los modos que no gastan (que salen antes)."""
    src = inspect.getsource(main.main)

    assert src.count("validar_credenciales_api(") == 2
    primera = src.index("validar_credenciales_api(")
    for modo_gratis in ("if args.diagnostico:", "if args.probar_conectores:",
                        "if args.frontera:", "if args.reclasificar:",
                        "if args.solicitudes:", "if args.probar_ocr:"):
        assert src.index(modo_gratis) < primera, modo_gratis
    # Y antes de la consulta de precios (que cuesta una llamada de red).
    assert primera < src.index("_fijar_precios_del_dia()")


@pytest.mark.parametrize("fase,ciclo,esperadas", [
    ("1", 0, ["TAVILY_API_KEY", "OPENROUTER_API_KEY"]),
    ("2", 0, ["OPENROUTER_API_KEY"]),
    ("all", 0, ["TAVILY_API_KEY", "OPENROUTER_API_KEY"]),
    ("2", 3, ["TAVILY_API_KEY", "OPENROUTER_API_KEY"]),   # --ciclo corre todo
])
def test_claves_necesarias_por_flujo(fase, ciclo, esperadas):
    """Cada flujo pide SOLO lo que usa: la fase 2 no busca en la web."""
    assert main._claves_necesarias(fase, ciclo) == esperadas


def test_validar_credenciales_api_dice_que_falta_y_donde(monkeypatch):
    """El mensaje nombra la clave que falta, para qué sirve y dónde se pone."""
    monkeypatch.setattr(config, "TAVILY_API_KEY", "")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "sk-presente")

    with pytest.raises(SystemExit) as excinfo:
        config.validar_credenciales_api(["TAVILY_API_KEY",
                                        "OPENROUTER_API_KEY"])

    mensaje = str(excinfo.value)
    assert "TAVILY_API_KEY" in mensaje
    assert "OPENROUTER_API_KEY" not in mensaje      # esa sí está
    assert ".env" in mensaje                        # dónde ponerla
    assert "config.py" in mensaje


def test_validar_credenciales_api_no_hace_nada_si_estan(monkeypatch):
    monkeypatch.setattr(config, "TAVILY_API_KEY", "sk-x")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "sk-y")
    assert config.validar_credenciales_api() is None


# ============ 3. LOS MODOS GRATIS FUNCIONAN SIN .ENV (aislado) ==============

def test_frontera_sin_claves(tmp_path):
    copiar_entradas(tmp_path)
    r = lanzar_sin_claves(["--frontera"], tmp_path)

    assert r.returncode == 0, r.stdout[-700:] + r.stderr[-400:]
    assert "Faltan claves" not in r.stdout + r.stderr


def test_reclasificar_sin_claves(tmp_path):
    """Sin árbol en el entorno temporal: rc 1 con el aviso del fichero, pero
    NUNCA por falta de claves."""
    r = lanzar_sin_claves(["--reclasificar"], tmp_path)

    assert r.returncode == 1
    assert "arbol_refinado.json" in r.stdout + r.stderr
    assert "Faltan claves" not in r.stdout + r.stderr
    assert "Traceback" not in r.stderr


def test_diagnostico_sin_claves(tmp_path):
    """--diagnostico (sin --test-llm) corre sin claves y las reporta como
    problema, en vez de morir al importar."""
    copiar_entradas(tmp_path)
    r = lanzar_sin_claves(["--diagnostico"], tmp_path, timeout=180)

    salida = r.stdout + r.stderr
    assert "TAVILY_API_KEY ausente o vacía" in salida
    assert "OPENROUTER_API_KEY ausente o vacía" in salida
    assert "Traceback" not in r.stderr
    assert r.returncode == 1                      # hay problemas: los reporta


def test_resumen_noche_sin_claves(tmp_path):
    """resumen_noche.py es 100% offline: funciona sin .env."""
    copiar_entradas(tmp_path)
    r = lanzar_sin_claves([], tmp_path, programa="resumen_noche")

    assert r.returncode == 0, r.stdout[-700:] + r.stderr[-400:]
    assert "Faltan claves" not in r.stdout + r.stderr
    assert "100% offline" in r.stdout


def test_probar_ocr_sin_claves(tmp_path):
    """--probar-ocr usa el OCR local: sin .env debe funcionar y costar $0."""
    pdf = tmp_path / "acta.pdf"
    pdf.write_bytes(_pdf_con_texto("Partida de bautismo " + "datos " * 30))

    r = lanzar_sin_claves(["--probar-ocr", str(pdf), "--sin-cache"], tmp_path)

    salida = r.stdout + r.stderr
    assert r.returncode == 0, salida[-700:]
    assert "pypdf" in salida
    assert "$0.0000" in salida
    assert "Faltan claves" not in salida


# ====== 4. UN FLUJO QUE GASTA MUERE EN LA VALIDACIÓN (no a mitad) ==========

def test_fase_2_sin_claves_muere_antes_de_gastar_y_no_toca_nada(tmp_path):
    """El caso que importa: un flujo que gasta, sin claves, se para ANTES de
    la consulta de precios y sin escribir ningún fichero de estado."""
    copiar_entradas(tmp_path)
    antes = huellas_de_estado()

    r = lanzar_sin_claves(["--fase", "2"], tmp_path)

    salida = r.stdout + r.stderr
    assert r.returncode == 1
    assert "Faltan claves de API: OPENROUTER_API_KEY" in salida
    assert ".env" in salida
    assert "precio usado" not in salida          # ni la consulta gratis llegó
    assert "Traceback" not in r.stderr
    # No ha tocado NADA del proyecto ni ha creado salidas en la carpeta temporal.
    assert huellas_de_estado() == antes
    assert not (tmp_path / "arbol_hallazgos.json").exists()


def test_cada_flujo_pide_solo_su_clave(tmp_path):
    """Precisión del mensaje: --fase 2 no exige Tavily; --fase 1 sí las dos."""
    r2 = lanzar(["--fase", "2"], tmp_path, claves=("clave-tavily", ""))
    assert r2.returncode == 1
    assert "OPENROUTER_API_KEY" in r2.stdout + r2.stderr
    assert "TAVILY_API_KEY" not in r2.stdout + r2.stderr

    r1 = lanzar(["--fase", "1"], tmp_path, claves=("clave-tavily", ""))
    assert r1.returncode == 1
    assert "OPENROUTER_API_KEY" in r1.stdout + r1.stderr

    r1b = lanzar(["--fase", "1"], tmp_path, claves=("", "clave-openrouter"))
    assert r1b.returncode == 1
    assert "TAVILY_API_KEY" in r1b.stdout + r1b.stderr
    assert "OPENROUTER_API_KEY" not in r1b.stdout + r1b.stderr


def test_aceptar_e_importar_propios_no_exigen_claves(tmp_path):
    """--aceptar es offline y --importar-propios usa OCR local: ninguno de los
    dos debe morir por claves."""
    copiar_entradas(tmp_path)
    for argv in (["--aceptar"], ["--importar-propios"]):
        r = lanzar_sin_claves(argv, tmp_path)
        assert "Faltan claves" not in r.stdout + r.stderr, argv
        assert "Traceback" not in r.stderr, argv
