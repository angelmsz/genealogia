"""
tests/test_conectores_v103.py — El FIX 1 de la v10.2 arregló la INSTANCIA
del fallo más caro del log; esto cubre la CLASE.

En el log de la noche del 2026-09-11 los tres conectores estructurados
fallaban en CADA objetivo con 'recolector_siga() missing 1 required
positional argument: objetivo' (45 avisos amarillos idénticos) y la
ejecución seguía como si nada: nadie se enteró de que SIGA/ADDO/Ensenada
estaban muertos. Estos tests exigen que un error de PROGRAMACIÓN (firma,
import, atributo) NO se confunda con un fallo de red: nivel ERROR, aviso
ÚNICO por ejecución y contador, sin romper la tanda.

Todo offline: sin red, sin LLM, sin tocar los datos del usuario.
"""

from __future__ import annotations

import scrapers.archivos as archivos
import scrapers.familysearch as familysearch
import scrapers.hispagen as hispagen
import utils.ui as ui

OBJETIVO = {"municipio": "Vitoria", "provincia": "alava",
            "apellido_paterno": "Merillas"}


def _silencio(monkeypatch) -> tuple[list, list]:
    """Espías de log + los otros cuatro conectores mudos (sin red)."""
    avisos: list[str] = []
    errores: list[str] = []
    monkeypatch.setattr(ui, "log_warn", lambda m: avisos.append(str(m)))
    monkeypatch.setattr(ui, "log_error", lambda m: errores.append(str(m)))
    monkeypatch.setattr(archivos, "recolector_addo", lambda o, c=None: [])
    monkeypatch.setattr(archivos, "recolector_ensenada", lambda o, c=None: [])
    monkeypatch.setattr(hispagen, "recolector_hispagen", lambda o, c=None: [])
    monkeypatch.setattr(familysearch, "recolector_familysearch",
                        lambda o, c=None: [])
    return avisos, errores


def test_typeerror_de_firma_sale_como_error_y_solo_una_vez(monkeypatch):
    """El TypeError de firma de la v10.2 (el que dejó las 3 fuentes
    estructuradas muertas toda la noche) debe salir como log_error UNA
    vez, no como aviso amarillo por objetivo, y no romper la ejecución."""
    archivos.reiniciar_contador_abortos()
    avisos, errores = _silencio(monkeypatch)

    def _siga_roto(o, c=None):
        raise TypeError("recolector_siga() missing 1 required positional "
                        "argument: 'objetivo'")

    monkeypatch.setattr(archivos, "recolector_siga", _siga_roto)

    assert archivos.recolectar(OBJETIVO, conn=None) == []   # objetivo 1
    assert archivos.recolectar(OBJETIVO, conn=None) == []   # objetivo 2

    assert TypeError in archivos.ERRORES_PROGRAMACION
    assert len(errores) == 1, (
        f"el bug se avisa UNA vez por ejecución, no por objetivo: {errores}")
    assert "TypeError" in errores[0] and "missing 1 required" in errores[0]
    assert not avisos, (
        f"un error de PROGRAMACIÓN no es un aviso de red: {avisos}")
    assert archivos._ABORTOS_PROGRAMACION["_recolector_siga"] == 2


def test_fallo_de_red_sigue_siendo_aviso_amarillo(monkeypatch):
    """Regresión v10.2 INTACTA: un fallo de red (sitio caído) sigue siendo
    log_warn y NO toca el contador de bugs de programación."""
    import requests

    archivos.reiniciar_contador_abortos()
    avisos, errores = _silencio(monkeypatch)

    def _siga_caido(o, c=None):
        raise requests.exceptions.ConnectionError("red caída (test)")

    monkeypatch.setattr(archivos, "recolector_siga", _siga_caido)

    assert archivos.recolectar(OBJETIVO, conn=None) == []
    assert avisos and "recolector _recolector_siga falló" in avisos[0]
    assert not errores, "un fallo de red NO es un bug: sigue en amarillo"
    assert "_recolector_siga" not in archivos._ABORTOS_PROGRAMACION


def test_un_conector_roto_no_impide_a_los_demas(monkeypatch):
    """El aborto es de ESE conector, no de la tanda: los otros cuatro se
    ejecutan igual."""
    archivos.reiniciar_contador_abortos()
    _silencio(monkeypatch)
    llamadas: list[str] = []

    def _siga_roto(o, c=None):
        raise TypeError("recolector_siga() missing 1 required positional "
                        "argument: 'objetivo'")

    monkeypatch.setattr(archivos, "recolector_siga", _siga_roto)
    monkeypatch.setattr(archivos, "recolector_addo",
                        lambda o, c=None: (llamadas.append("addo"), [])[1])
    monkeypatch.setattr(archivos, "recolector_ensenada",
                        lambda o, c=None: (llamadas.append("ensenada"),
                                           [])[1])
    monkeypatch.setattr(hispagen, "recolector_hispagen",
                        lambda o, c=None: (llamadas.append("hispagen"),
                                           [])[1])
    monkeypatch.setattr(familysearch, "recolector_familysearch",
                        lambda o, c=None: (llamadas.append("familysearch"),
                                           [])[1])

    archivos.recolectar(OBJETIVO, conn=None)
    assert llamadas == ["addo", "ensenada", "hispagen", "familysearch"]
