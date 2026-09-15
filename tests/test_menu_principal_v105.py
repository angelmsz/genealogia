"""
tests/test_menu_principal_v105.py — Menú de tareas (menu_principal.py).

Cubre lo que el menú TIENE que garantizar para ser fiable:
  1. Cada opción lanza EXACTAMENTE el comando documentado.
  2. `git pull` solo con el árbol limpio, y siempre `--ff-only` (sin merges).
  3. Las opciones que gastan o modifican estado piden confirmación EXPLÍCITA
     y Enter cancela (fase 2 y --aceptar).
  4. Presupuesto inválido cancela; el válido va al comando tal cual.
  5. `--probar-ocr` valida el fichero ANTES de lanzar nada y pasa la ruta como
     argumento suelto (rutas con espacios).
  6. El log del menú: cabecera (fecha/version/intérprete/opción/comando), salida
     completa, pie (código y duración), rotación a 30 y SIN secretos.
  7. Intérprete: el .venv si existe; si no, sys.executable.
  8. Guardia contra deriva de flags: si main.py renombra un flag, avisa el test
     (y no el usuario a medianoche).
  9. La VERSION sale de config (con salida elegante si config no carga).

Todo sin red, sin git real, sin pip real y sin LLM: los ejecutores
(`_correr`, `capturar`) y `subprocess.Popen` se sustituyen por dobles.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

import menu_principal as menu

RAIZ = Path(__file__).resolve().parent.parent


# ============================== DOBLES DE PRUEBA ===========================

class Grabador:
    """Sustituye a _correr: apunta los argv y devuelve el código que le digan."""

    def __init__(self, codigo: int = 0) -> None:
        self.llamadas: list[list[str]] = []
        self.codigo = codigo

    def __call__(self, argv, *, opcion="", descripcion="", sin_log=False):
        self.llamadas.append(list(argv))
        return self.codigo

    @property
    def ultimo(self) -> list[str]:
        return self.llamadas[-1] if self.llamadas else []


def _respuestas(monkeypatch, valores: list[str]):
    """input() devuelve valores en orden (y graba los prompts)."""
    cola = list(valores)
    prompts: list[str] = []

    def _input(prompt: str = "") -> str:
        prompts.append(prompt)
        if not cola:
            raise EOFError
        return cola.pop(0)

    monkeypatch.setattr("builtins.input", _input)
    return prompts


def _capturar_fijo(monkeypatch, codigo: int, salida: str) -> list[list[str]]:
    llamadas: list[list[str]] = []

    def _fake(argv, timeout: int = 60):
        llamadas.append(list(argv))
        return codigo, salida

    monkeypatch.setattr(menu, "capturar", _fake)
    monkeypatch.setattr(menu, "resolver_git", lambda: "git")
    return llamadas


@pytest.fixture()
def py() -> str:
    return r"C:\proyecto\.venv\Scripts\python.exe"


# ============================== 1. COMANDOS ================================

def test_comandos_simples_son_exactos(monkeypatch, py):
    """Opciones 2, 3, 6 y 7: el argv es exactamente el documentado.

    La opción 3 pregunta si además se hace el ping real (--test-llm): aquí se
    responde Enter (= no), así que el comando es el de siempre.
    """
    _respuestas(monkeypatch, [""])                 # Enter: sin --test-llm
    rec = Grabador()
    monkeypatch.setattr(menu, "_correr", rec)

    menu.accion_pytest(py)
    menu.accion_diagnostico(py)
    menu.accion_frontera(py)
    menu.accion_resumen(py)

    assert rec.llamadas == [
        [py, "-m", "pytest", "tests", "-q"],
        [py, "main.py", "--diagnostico"],
        [py, "main.py", "--frontera"],
        [py, "resumen_noche.py"],
    ]


def test_guardia_contra_deriva_de_flags():
    """Todo flag que usa el menú existe en main.py (si lo renombran, avisa el
    test). Y los ficheros que el menú lanza existen."""
    texto = (RAIZ / "main.py").read_text(encoding="utf-8")
    flags_main = set(re.findall(r'add_argument\(\s*"(--[a-z0-9\-]+)"', texto))
    flags_menu = {"--fase", "--presupuesto-max", "--aceptar", "--frontera",
                  "--diagnostico", "--probar-ocr", "--manuscrito",
                  "--sin-cache", "--max-steps", "--test-llm", "--ciclo",
                  "--personas"}
    assert flags_menu <= flags_main, (
        f"flags del menú que ya no existen en main.py: {flags_menu - flags_main}")
    assert (RAIZ / "resumen_noche.py").is_file()
    assert (RAIZ / "tests").is_dir()


# ============================== 2. GIT PULL ================================

def test_git_pull_con_arbol_sucio_no_hace_pull(monkeypatch, capsys):
    """Con cambios locales: aviso, nada de pull y sugerencia de commit/stash."""
    _capturar_fijo(monkeypatch, 0, " M config.py\n?? notas.txt\n")
    rec = Grabador()
    monkeypatch.setattr(menu, "_correr", rec)

    codigo = menu.accion_git_pull("py")

    assert rec.llamadas == []                      # NO se lanzó el pull
    assert codigo == 1
    salida = capsys.readouterr().out
    assert "NO hago 'git pull'" in salida
    assert "stash" in salida
    assert "config.py" in salida


def test_git_pull_con_arbol_limpio_usa_ff_only(monkeypatch):
    """Con el árbol limpio: 'git pull --ff-only' (sin merges automáticos)."""
    _capturar_fijo(monkeypatch, 0, "")
    rec = Grabador()
    monkeypatch.setattr(menu, "_correr", rec)

    menu.accion_git_pull("py")

    assert rec.llamadas == [["git", "pull", "--ff-only"]]


def test_git_pull_sin_git_no_lanza_nada(monkeypatch, capsys):
    monkeypatch.setattr(menu, "resolver_git", lambda: None)
    rec = Grabador()
    monkeypatch.setattr(menu, "_correr", rec)

    assert menu.accion_git_pull("py") == 1
    assert rec.llamadas == []
    assert "No encuentro 'git'" in capsys.readouterr().out


def test_git_pull_si_status_falla_no_hace_pull(monkeypatch):
    _capturar_fijo(monkeypatch, 128, "fatal: not a git repository\n")
    rec = Grabador()
    monkeypatch.setattr(menu, "_correr", rec)

    assert menu.accion_git_pull("py") == 128
    assert rec.llamadas == []


# ============================== 3. FASE 2 ==================================

def test_fase2_confirmacion_denegada_no_lanza_subproceso(monkeypatch, py):
    _respuestas(monkeypatch, ["", "", "n"])        # $ + max-steps + 'n'
    rec = Grabador()
    monkeypatch.setattr(menu, "_correr", rec)

    assert menu.accion_fase2(py) == 0
    assert rec.llamadas == []


def test_fase2_presupuesto_invalido_cancela(monkeypatch, py, capsys):
    _respuestas(monkeypatch, ["abc"])              # ni se llega a confirmar
    rec = Grabador()
    monkeypatch.setattr(menu, "_correr", rec)

    assert menu.accion_fase2(py) == 0
    assert rec.llamadas == []
    assert "CANCELADA" in capsys.readouterr().out


def test_fase2_presupuesto_cero_cancela(monkeypatch, py):
    _respuestas(monkeypatch, ["0"])
    rec = Grabador()
    monkeypatch.setattr(menu, "_correr", rec)
    assert menu.accion_fase2(py) == 0
    assert rec.llamadas == []


def test_fase2_presupuesto_valido_lanza_comando_exacto(monkeypatch, py,
                                                       capsys):
    """Enter = 0.5, Enter = sin --max-steps y confirmación 's' -> comando
    exacto."""
    prompts = _respuestas(monkeypatch, ["", "", "s"])
    rec = Grabador()
    monkeypatch.setattr(menu, "_correr", rec)

    menu.accion_fase2(py)

    assert rec.llamadas == [[py, "main.py", "--fase", "2",
                             "--presupuesto-max", "0.5"]]
    salida = capsys.readouterr().out
    assert "Comando exacto" in salida                  # se enseña ANTES
    assert any("gastar dinero en OpenRouter" in p for p in prompts)
    assert any("Enter = n" in p for p in prompts)


def test_fase2_presupuesto_personalizado(monkeypatch, py):
    _respuestas(monkeypatch, ["1,25", "", "s"])
    rec = Grabador()
    monkeypatch.setattr(menu, "_correr", rec)

    menu.accion_fase2(py)

    assert rec.ultimo == [py, "main.py", "--fase", "2",
                          "--presupuesto-max", "1.25"]     # coma decimal ok


def test_fase2_max_steps_opcional(monkeypatch, py):
    """--max-steps solo se añade si se escribe un número; Enter = no añadirlo."""
    _respuestas(monkeypatch, ["", "25", "s"])
    rec = Grabador()
    monkeypatch.setattr(menu, "_correr", rec)

    menu.accion_fase2(py)

    assert rec.ultimo == [py, "main.py", "--fase", "2",
                          "--presupuesto-max", "0.5",
                          "--max-steps", "25"]


def test_fase2_max_steps_invalido_cancela(monkeypatch, py, capsys):
    _respuestas(monkeypatch, ["", "muchas", "s"])
    rec = Grabador()
    monkeypatch.setattr(menu, "_correr", rec)

    with pytest.raises(menu.MenuCancelado):
        menu.accion_fase2(py)

    assert rec.llamadas == []


# ============================== 4. --ACEPTAR ===============================

def test_aceptar_enter_cancela(monkeypatch, py):
    _respuestas(monkeypatch, [""])                 # Enter = n
    rec = Grabador()
    monkeypatch.setattr(menu, "_correr", rec)

    assert menu.accion_aceptar(py) == 0
    assert rec.llamadas == []


def test_aceptar_solo_s_lanza(monkeypatch, py, capsys):
    prompts = _respuestas(monkeypatch, ["s"])
    rec = Grabador()
    monkeypatch.setattr(menu, "_correr", rec)

    menu.accion_aceptar(py)

    assert rec.llamadas == [[py, "main.py", "--aceptar"]]
    assert any("puede aceptar candidatos y modificar estado" in p
               for p in prompts)
    assert any("Enter = n" in p for p in prompts)


def test_aceptar_palabra_random_no_lanza(monkeypatch, py):
    _respuestas(monkeypatch, ["vale"])
    rec = Grabador()
    monkeypatch.setattr(menu, "_correr", rec)
    assert menu.accion_aceptar(py) == 0
    assert rec.llamadas == []


# ============================== 5. PROBAR-OCR ==============================

def test_ocr_ruta_inexistente_no_lanza(monkeypatch, py, capsys, tmp_path):
    _respuestas(monkeypatch, [str(tmp_path / "no-existe.pdf")])
    rec = Grabador()
    monkeypatch.setattr(menu, "_correr", rec)

    assert menu.accion_probar_ocr(py) == 0
    assert rec.llamadas == []
    assert "No existe el fichero" in capsys.readouterr().out


def test_ocr_ruta_con_espacios_va_como_argumento_suelto(monkeypatch, py,
                                                        tmp_path):
    pdf = tmp_path / "partida de bautismo 1870.pdf"
    pdf.write_bytes(b"%PDF-1.4 falso")
    _respuestas(monkeypatch, [f'"{pdf}"', "n", "n"])   # con comillas (arrastre)
    rec = Grabador()
    monkeypatch.setattr(menu, "_correr", rec)

    menu.accion_probar_ocr(py)

    assert rec.ultimo == [py, "main.py", "--probar-ocr", str(pdf)]
    assert rec.ultimo[3] == str(pdf)                   # un solo argumento


def test_ocr_flags_opcionales(monkeypatch, py, tmp_path):
    pdf = tmp_path / "libro.pdf"
    pdf.write_bytes(b"%PDF-1.4 falso")
    _respuestas(monkeypatch, [str(pdf), "s", "s"])
    rec = Grabador()
    monkeypatch.setattr(menu, "_correr", rec)

    menu.accion_probar_ocr(py)

    assert rec.ultimo == [py, "main.py", "--probar-ocr", str(pdf),
                          "--manuscrito", "--sin-cache"]


def test_ocr_enter_en_los_flags_no_los_añade(monkeypatch, py, tmp_path):
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    _respuestas(monkeypatch, [str(pdf), "", ""])       # Enter = n en ambos
    rec = Grabador()
    monkeypatch.setattr(menu, "_correr", rec)
    menu.accion_probar_ocr(py)
    assert rec.ultimo == [py, "main.py", "--probar-ocr", str(pdf)]


# ============================== 6. INTÉRPRETE ==============================

def test_interprete_usa_el_venv_si_existe(tmp_path):
    venv = tmp_path / ".venv" / "Scripts"
    venv.mkdir(parents=True)
    exe = venv / "python.exe"
    exe.write_bytes(b"")
    ruta, origen = menu.resolver_interprete(tmp_path)
    assert ruta == str(exe)
    assert "venv" in origen


def test_interprete_sin_venv_cae_en_sys_executable(tmp_path):
    ruta, origen = menu.resolver_interprete(tmp_path)
    assert ruta == sys.executable
    assert "sys.executable" in origen


# ============================== 7. VERSION =================================

def test_version_sale_de_config():
    texto = menu.version_config()
    assert re.match(r"^\d+\.\d+", texto), texto
    assert texto.startswith("10.4")


def test_version_cae_al_literal_si_config_no_importa(monkeypatch):
    """Si config no se puede importar (dependencia a medias), se lee el
    literal de config.py: el menú sigue mostrando la versión."""
    monkeypatch.setitem(sys.modules, "config", None)   # import -> ImportError
    assert "10.4" in menu.version_config()


def test_version_avisa_si_no_hay_nada(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "config", None)
    monkeypatch.setattr(menu, "BASE_DIR", tmp_path)     # sin config.py
    assert menu.version_config() == "(config no disponible)"


# ============================== 8. LOGS ====================================

def test_nombre_log_y_rotacion(tmp_path):
    assert menu.nombre_log().startswith("menu_")
    assert menu.nombre_log().endswith(".log")
    # 35 logs del menú + 1 del bot: se conservan los 30 más nuevos del menú.
    for i in range(35):
        f = tmp_path / f"menu_2026010{i:02d}_000000.log"
        f.write_text("x", encoding="utf-8")
        os.utime(f, (1000 + i, 1000 + i))
    agente = tmp_path / "agente_20260912_222149.log"
    agente.write_text("caja negra", encoding="utf-8")

    borrados = menu.rotar_logs(tmp_path, conservar=30)

    assert len(borrados) == 5
    quedan = sorted(p.name for p in tmp_path.glob("menu_*.log"))
    assert len(quedan) == 30
    assert quedan[0] == "menu_202601005_000000.log"     # sobreviven los nuevos
    assert agente.is_file()                             # el del bot no se toca


def test_registro_cabecera_salida_y_pie(tmp_path):
    reg = menu.RegistroMenu(opcion="6", descripcion="main.py --frontera",
                            argv=["py", "main.py", "--frontera"], base=tmp_path)
    reg.abrir()
    reg.linea("15 entradas en la frontera\n")
    reg.cerrar(0, 1.25)

    assert reg.ruta is not None and reg.ruta.parent == tmp_path
    texto = reg.ruta.read_text(encoding="utf-8")
    assert "opcion     : 6 — main.py --frontera" in texto
    assert "comando    : py main.py --frontera" in texto
    assert re.search(r"fecha\s+:\s+\d{4}-\d{2}-\d{2}", texto)
    assert "version    :" in texto and "interprete :" in texto
    assert "15 entradas en la frontera" in texto
    assert "codigo_salida : 0" in texto
    assert "duracion      : 1.2 s" in texto


def test_registro_redacta_secretos(tmp_path):
    reg = menu.RegistroMenu(opcion="2", descripcion="d", argv=["x"],
                            base=tmp_path)
    reg.abrir()
    reg.linea("clave sk-abcdef123456 y tvly-xyz987654\n")
    reg.linea("Authorization: Bearer abcd1234efgh5678\n")
    reg.linea("TAVILY_API_KEY=tvly-secreto123456\n")
    reg.cerrar(0, 0.1)
    texto = reg.ruta.read_text(encoding="utf-8")
    assert "sk-abcdef123456" not in texto
    assert "tvly-xyz987654" not in texto
    assert "abcd1234efgh5678" not in texto
    assert "tvly-secreto123456" not in texto
    assert "***" in texto


def test_redactar_no_rompe_texto_normal():
    for texto in ("15 entradas en la frontera",
                  "PDF con 355 páginas: OCR local solo de las 30 primeras",
                  "codigo_salida : 0"):
        assert menu.redactar(texto) == texto


def test_sin_log_no_crea_ficheros(tmp_path):
    reg = menu.RegistroMenu(opcion="1", descripcion="d", argv=["x"],
                            sin_log=True, base=tmp_path)
    reg.abrir()
    reg.linea("nada\n")
    reg.cerrar(0, 0.0)
    assert reg.ruta is None
    assert list(tmp_path.iterdir()) == []


class _ProcFalso:
    """Popen mínimo: iterar la salida y devolver un código."""

    def __init__(self, lineas: list[str], codigo: int = 0) -> None:
        self.stdout = iter(lineas)
        self._codigo = codigo
        self.terminado = False

    def wait(self, timeout: int | None = None) -> int:
        return self._codigo

    def terminate(self) -> None:
        self.terminado = True

    def kill(self) -> None:
        self.terminado = True


def test_correr_escribe_salida_completa_en_el_log(monkeypatch, tmp_path):
    monkeypatch.setattr(menu, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(menu.subprocess, "Popen",
                        lambda *a, **k: _ProcFalso(["linea 1\n", "linea 2\n"], 3))
    codigo = menu._correr(["py", "algo"], opcion="2", descripcion="prueba")
    assert codigo == 3
    logs = list(tmp_path.glob("menu_*.log"))
    assert len(logs) == 1
    texto = logs[0].read_text(encoding="utf-8")
    assert "linea 1" in texto and "linea 2" in texto   # salida COMPLETA
    assert "codigo_salida : 3" in texto


def test_correr_con_log_desactivado(monkeypatch, tmp_path):
    monkeypatch.setattr(menu, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(menu.subprocess, "Popen",
                        lambda *a, **k: _ProcFalso(["x\n"]))
    assert menu._correr(["py", "algo"], opcion="2", descripcion="d",
                        sin_log=True) == 0
    assert list(tmp_path.glob("*.log")) == []


def test_correr_ejecutable_inexistente_da_127(monkeypatch, tmp_path):
    monkeypatch.setattr(menu, "LOGS_DIR", tmp_path)

    def _explota(*a, **k):
        raise FileNotFoundError("no existe")

    monkeypatch.setattr(menu.subprocess, "Popen", _explota)
    assert menu._correr(["nope"], opcion="9", descripcion="d") == 127


# ============================== 9. OTRAS OPCIONES ==========================

def test_dependencias_usa_el_interprete_elegido(monkeypatch, py):
    rec = Grabador()
    monkeypatch.setattr(menu, "_correr", rec)
    menu.accion_dependencias(py)
    assert rec.ultimo[0] == py
    assert rec.ultimo[1] == "-c"
    assert "find_spec" in rec.ultimo[2]
    assert "fontTools" in rec.ultimo[2]          # opcional, informado aparte


def test_fonttools_pide_confirmacion(monkeypatch, py, capsys):
    _respuestas(monkeypatch, ["n"])
    rec = Grabador()
    monkeypatch.setattr(menu, "_correr", rec)
    assert menu.accion_fonttools(py) == 0
    assert rec.llamadas == []
    assert "requirements.txt no se toca" in capsys.readouterr().out


def test_fonttools_instala_y_verifica(monkeypatch, py, capsys):
    _respuestas(monkeypatch, ["s"])
    rec = Grabador()
    monkeypatch.setattr(menu, "_correr", rec)
    capturas = _capturar_fijo(monkeypatch, 0, "fontTools 4.50.0\n")
    menu.accion_fonttools(py)
    assert rec.llamadas == [[py, "-m", "pip", "install", "fonttools"]]
    assert capturas[-1][0] == py and "fontTools" in capturas[-1][2]
    assert "fontTools disponible" in capsys.readouterr().out


def test_ultimo_log_sin_logs(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(menu, "LOGS_DIR", tmp_path)
    assert menu.accion_ultimo_log("py") == 0
    assert "no hay ningún log" in capsys.readouterr().out


def test_ultimo_log_muestra_el_rabajo_reciente(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(menu, "LOGS_DIR", tmp_path)
    viejo = tmp_path / "agente_20260901_000000.log"
    viejo.write_text("viejo\n", encoding="utf-8")
    nuevo = tmp_path / "menu_20260913_010000.log"
    nuevo.write_text("nuevo-1\nnuevo-2\n", encoding="utf-8")
    os.utime(viejo, (1000, 1000))
    os.utime(nuevo, (2000, 2000))
    _respuestas(monkeypatch, [""])                 # Enter = el más reciente
    assert menu.accion_ultimo_log("py") == 0
    salida = capsys.readouterr().out
    assert "menu_20260913_010000.log" in salida
    assert "nuevo-2" in salida


# ============================== 10. MENÚ / PAUSA ===========================

def test_pausa_tras_cada_opcion(monkeypatch, py):
    prompts = _respuestas(monkeypatch, ["6", "", "0"])
    rec = Grabador()
    monkeypatch.setattr(menu, "_correr", rec)
    assert menu.main(["--sin-log"]) == 0
    assert any("Pulsa Enter" in p for p in prompts)


def test_sin_pausa_no_pausa(monkeypatch, py):
    prompts = _respuestas(monkeypatch, ["6", "0"])
    rec = Grabador()
    monkeypatch.setattr(menu, "_correr", rec)
    assert menu.main(["--sin-pausa", "--sin-log"]) == 0
    assert not any("Pulsa Enter" in p for p in prompts)


def test_opcion_desconocida_no_revienta(monkeypatch, capsys):
    _respuestas(monkeypatch, ["99", "0"])
    assert menu.main(["--sin-pausa", "--sin-log"]) == 0
    assert "no reconocida" in capsys.readouterr().out


def test_menu_muestra_todas_las_opciones(capsys, py):
    menu._pintar_menu("10.4.1", py)
    salida = capsys.readouterr().out
    assert "VERSION: 10.4.1" in salida
    for numero in menu.ORDEN_OPCIONES + ("0",):
        etiqueta = menu.OPCIONES[numero][0]
        assert etiqueta in salida
    assert "\x1b[" not in salida            # texto plano: sin códigos ANSI


# ============ 12-15: lo que solo hacía el lanzador (v10.4.2) ===============

def test_diagnostico_pide_el_ping_real(monkeypatch, py):
    """--test-llm es una subpregunta: con 's' se añade, con Enter no."""
    _respuestas(monkeypatch, ["s"])
    rec = Grabador()
    monkeypatch.setattr(menu, "_correr", rec)
    menu.accion_diagnostico(py)
    assert rec.ultimo == [py, "main.py", "--diagnostico", "--test-llm"]


def test_ciclo_pide_ciclos_presupuesto_y_confirma(monkeypatch, py):
    prompts = _respuestas(monkeypatch, ["2", "1,5", "s"])
    rec = Grabador()
    monkeypatch.setattr(menu, "_correr", rec)

    menu.accion_ciclo(py)

    assert rec.ultimo == [py, "main.py", "--ciclo", "2",
                          "--presupuesto-max", "1.5"]
    assert any("ciclos" in p for p in prompts)
    assert any("gastar dinero" in p for p in prompts)


def test_ciclo_enter_en_la_confirmacion_no_lanza_nada(monkeypatch, py):
    _respuestas(monkeypatch, ["1", "", ""])       # Enter también en el $
    rec = Grabador()
    monkeypatch.setattr(menu, "_correr", rec)

    assert menu.accion_ciclo(py) == 0
    assert rec.llamadas == []


def test_ciclo_numero_invalido_cancela(monkeypatch, py):
    _respuestas(monkeypatch, ["muchos"])
    rec = Grabador()
    monkeypatch.setattr(menu, "_correr", rec)

    with pytest.raises(menu.MenuCancelado):
        menu.accion_ciclo(py)
    assert rec.llamadas == []


def test_fase1_construye_su_comando(monkeypatch, py):
    _respuestas(monkeypatch, ["0.25", "", "s"])
    rec = Grabador()
    monkeypatch.setattr(menu, "_correr", rec)

    menu.accion_fase1(py)

    assert rec.ultimo == [py, "main.py", "--fase", "1",
                          "--presupuesto-max", "0.25"]


def test_personas_sin_nombres_cancela(monkeypatch, py, capsys):
    _respuestas(monkeypatch, [""])
    rec = Grabador()
    monkeypatch.setattr(menu, "_correr", rec)

    assert menu.accion_personas(py) == 0
    assert rec.llamadas == []
    assert "CANCELADA" in capsys.readouterr().out


def test_personas_van_como_un_solo_argumento(monkeypatch, py):
    """Los nombres con espacios NO parten el comando: van en un argv."""
    _respuestas(monkeypatch,
                ["Isidro Merillas Panero, Obdulia Pelaz Merino", "", "", "s"])
    rec = Grabador()
    monkeypatch.setattr(menu, "_correr", rec)

    menu.accion_personas(py)

    assert rec.ultimo == [py, "main.py", "--personas",
                          "Isidro Merillas Panero,Obdulia Pelaz Merino",
                          "--presupuesto-max", "0.5"]


def test_chuleta_enseña_los_comandos_avanzados_y_no_ejecuta_nada(monkeypatch,
                                                                py, capsys):
    rec = Grabador()
    monkeypatch.setattr(menu, "_correr", rec)

    assert menu.accion_chuleta(py) == 0

    assert rec.llamadas == []                     # NADA se ejecuta
    salida = capsys.readouterr().out
    for comando in ("--solicitudes", "--importar-propios", "--reclasificar",
                    "--ensenada", "--probar-conectores"):
        assert comando in salida
    assert "solo una chuleta" in salida


def test_ejecutar_con_resumen_dice_que_ha_cambiado(monkeypatch, tmp_path,
                                                   capsys):
    """Al terminar una acción se dice QUÉ ficheros han cambiado y cuánto."""
    monkeypatch.setattr(menu, "BASE_DIR", tmp_path)
    (tmp_path / "arbol_hallazgos.json").write_text("[]", encoding="utf-8")

    def _correr_falso(argv, *, opcion="", descripcion="", sin_log=False):
        (tmp_path / "arbol_hallazgos.json").write_text(
            json.dumps([{"persona": "P"}] * 3), encoding="utf-8")
        (tmp_path / "arbol.ged").write_text("0 HEAD\n0 TRLR\n",
                                            encoding="utf-8")
        return 0

    monkeypatch.setattr(menu, "_correr", _correr_falso)

    codigo = menu._ejecutar_con_resumen([py, "main.py", "--fase", "2"],
                                        opcion="4", descripcion="prueba")

    salida = capsys.readouterr().out
    assert codigo == 0
    assert "arbol_hallazgos.json" in salida
    assert "3 elementos" in salida and "(+3)" in salida
    assert "CREADO" in salida and "arbol.ged" in salida


def test_resumen_generados_sin_cambios_no_dice_nada(monkeypatch, tmp_path):
    monkeypatch.setattr(menu, "BASE_DIR", tmp_path)
    (tmp_path / "corpus_bruto.json").write_text("[]", encoding="utf-8")
    antes = menu.instantanea_salidas()
    assert menu.resumen_generados(antes) == []


def test_mostrar_argv_entrecomilla_solo_si_hay_espacios():
    assert menu.mostrar_argv(["py", "main.py", "--frontera"]) == \
        "py main.py --frontera"
    assert menu.mostrar_argv(["py", "a b.pdf"]) == 'py "a b.pdf"'
    # El programa de la opción 9 va con -c y saltos de línea: la cabecera del
    # log debe quedar en UNA línea.
    assert "\n" not in menu.mostrar_argv(["py", "-c", "import x\nprint(1)"])
    assert "import x print(1)" in menu.mostrar_argv(
        ["py", "-c", "import x\nprint(1)"])


# ============================== 11. HUMO REAL ==============================

def test_smoke_arranca_y_sale_con_cero():
    """Proceso real: --sin-pausa --sin-log con stdin '0' -> código 0, sin
    traceback y con la VERSION del proyecto en la cabecera."""
    resultado = subprocess.run(
        [sys.executable, "menu_principal.py", "--sin-pausa", "--sin-log"],
        input="0\n", cwd=RAIZ, capture_output=True, text=True,
        encoding="utf-8", timeout=120)
    assert resultado.returncode == 0, resultado.stderr[-800:]
    assert "MENÚ DE TAREAS" in resultado.stdout
    assert "VERSION: 10.4" in resultado.stdout
    assert "Elige una opción" in resultado.stdout
    assert "Traceback" not in resultado.stdout
    assert "Traceback" not in resultado.stderr
