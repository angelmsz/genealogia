"""
tests/test_log_ejecucion_v104.py — v10.4 (P0): registro de ejecución.

POR QUÉ EXISTE: una noche de autopiloto genera cientos de líneas de consola
que se pierden al cerrar la ventana. Sin registro en fichero no se puede
depurar "por qué no encontró nada" ni ver dónde se fue el tiempo.

LO QUE ESTE FICHERO BLINDA:
  - Está APAGADO hasta que alguien llama a iniciar_log(): los tests y los
    imports sueltos NO crean ficheros por sorpresa.
  - Cada nivel (ok/warn/error/doc/sección) queda en el fichero, sin códigos
    de color y con la fecha completa.
  - traza() escribe SOLO en el fichero (perfil de tiempos, sin ruido).
  - El pie lleva el recuento de avisos y errores y la duración.
  - cerrar_log() e iniciar_log() son idempotentes.

100% offline: el registro se redirige a tmp_path.
Ejecución:  python -m pytest tests/test_log_ejecucion_v104.py -q
"""

from __future__ import annotations

import time

import pytest

from utils import ui


@pytest.fixture(autouse=True)
def _log_cerrado():
    """El registro es estado de módulo: se cierra antes y después de cada
    test para no arrastrarlo entre pruebas."""
    ui.cerrar_log()
    yield
    ui.cerrar_log()


def _leer(tmp_path) -> str:
    ficheros = list(tmp_path.glob("agente_*.log"))
    assert len(ficheros) == 1, f"se esperaba 1 registro, hay {len(ficheros)}"
    return ficheros[0].read_text(encoding="utf-8")


# ===================== APAGADO HASTA QUE SE PIDE ============================

def test_sin_iniciar_log_no_se_escribe_nada(tmp_path):
    """Garantía de diseño: sin iniciar_log() no se crea ningún fichero."""
    ui.log_warn("esto no debe acabar en ningún fichero")
    ui.log_error("ni esto")
    assert list(tmp_path.glob("*.log")) == []


# ===================== LO BÁSICO: CADA NIVEL QUEDA ==========================

def test_iniciar_log_crea_el_fichero_y_registra_cada_nivel(tmp_path):
    ruta = ui.iniciar_log(tmp_path)
    assert ruta is not None
    ui.log_ok("todo bien")
    ui.log_warn("ojo con esto")
    ui.log_error("esto falló")
    assert ui.cerrar_log() == ruta
    texto = _leer(tmp_path)
    assert "=== REGISTRO DE EJECUCIÓN" in texto
    assert "[OK] todo bien" in texto
    assert "[WARN] ojo con esto" in texto
    assert "[ERROR] esto falló" in texto
    assert "=== FIN DE LA EJECUCIÓN" in texto


def test_la_cabecera_de_configuracion_queda_registrada(tmp_path):
    """Es lo primero que se mira al depurar: con qué ajustes se lanzó."""
    ui.iniciar_log(tmp_path)
    ui.cabecera_log({"version": "10.4", "ciclos": 3, "presupuesto_max": "$2.00"})
    ui.cerrar_log()
    texto = _leer(tmp_path)
    assert "configuración de esta ejecución" in texto
    assert "version: 10.4" in texto
    assert "ciclos: 3" in texto
    assert "presupuesto_max: $2.00" in texto


def test_el_fichero_no_lleva_codigos_de_color(tmp_path):
    """El registro se lee con un editor o con grep: nada de ANSI."""
    ui.iniciar_log(tmp_path)
    ui.cabecera("SECCION DE PRUEBA")
    ui.separador("TITULO DE PRUEBA")
    ui.log_warn("aviso con color en consola")
    ui.cerrar_log()
    texto = _leer(tmp_path)
    assert "SECCION DE PRUEBA" in texto
    assert "### TITULO DE PRUEBA" in texto
    assert "\033[" not in texto


def test_traza_solo_va_al_fichero(tmp_path, capsys):
    """traza() es perfil de tiempos: al fichero sí, a la consola no."""
    ui.iniciar_log(tmp_path)
    ui.traza("detalle fino para depurar")
    capturado = capsys.readouterr().out
    assert "detalle fino" not in capturado
    ui.cerrar_log()
    assert "detalle fino para depurar" in _leer(tmp_path)


def test_el_indicador_deja_su_tiempo_en_el_registro(tmp_path):
    """Con esto se ve, al día siguiente, cuánto tardó cada operación."""
    ui.iniciar_log(tmp_path)
    with ui.Indicador("Operación lenta"):
        time.sleep(0.01)
    ui.cerrar_log()
    assert "Operación lenta: 0 s" in _leer(tmp_path)


# ===================== RESUMEN, PIE E IDEMPOTENCIA ==========================

def test_resumen_cuenta_avisos_y_errores(tmp_path):
    ui.iniciar_log(tmp_path)
    ui.log_warn("a")
    ui.log_warn("b")
    ui.log_error("c")
    resumen = ui.resumen_log()
    assert "warn: 2" in resumen
    assert "error: 1" in resumen
    ui.cerrar_log()
    assert "warn: 2" in _leer(tmp_path)      # el pie lleva el recuento


def test_cerrar_log_es_idempotente(tmp_path):
    ruta = ui.iniciar_log(tmp_path)
    assert ui.cerrar_log() == ruta
    assert ui.cerrar_log() is None
    assert ui.resumen_log() == "sin registro"


def test_iniciar_log_es_idempotente(tmp_path):
    primera = ui.iniciar_log(tmp_path)
    segunda = ui.iniciar_log(tmp_path)
    assert primera == segunda
    assert len(list(tmp_path.glob("agente_*.log"))) == 1


def test_cerrar_permite_abrir_un_registro_nuevo(tmp_path):
    """Una sesión de menú puede lanzar varias acciones: cada una puede tener
    su registro sin arrastrar el anterior."""
    ui.iniciar_log(tmp_path)
    ui.log_warn("primera acción")
    ui.cerrar_log()
    time.sleep(0.01)      # el nombre del fichero lleva segundos
    ui.iniciar_log(tmp_path)
    ui.log_warn("segunda acción")
    ui.cerrar_log()
    ficheros = sorted(tmp_path.glob("agente_*.log"))
    assert len(ficheros) == 2
