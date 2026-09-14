"""
tests/test_sesion_por_hilo_v105.py — BLOQUE 2 (arreglo 2): una sesión HTTP por
hilo, cierre al apagar el pool, cero errores cruzados (v10.4.2).

LA CARRERA QUE ARREGLA
----------------------
`config.SESSION` era UNA sola `requests.Session` para todo el proceso, y no es
thread-safe (pool de conexiones y cookies compartidos). Encima había un cierre a
traición: `scrapers/hispagen.py` llama a `SESSION.close()` para deshacerse de
sockets keep-alive envenenados, y ese close, ejecutado desde un hilo de
descarga, cerraba el pool que OTROS hilos estaban usando en ese instante
(`agent/fase1.py` descarga con `N_HILOS_DESCARGA=3` hilos). De ahí errores
cruzados: "Connection aborted" en una descarga ajena y respuestas atribuidas al
documento equivocado.

AHORA
-----
`config.sesion()` devuelve la sesión del HILO actual (se crea al primer uso) y
`config.SESSION` es un proxy que despacha a esa sesión, así que los sitios de
llamada no cambian. `SESSION.close()` cierra SOLO la del hilo que lo llama.
`sesiones_hilo_limpias()` (usado en fase1 alrededor del ThreadPoolExecutor)
cierra las sesiones de los hilos hijos cuando el pool ya se ha apagado.

Los tests son deterministas: hilos REALES, el envío HTTP de cualquier
`requests.Session` interceptado, y una barrera para forzar que las peticiones se
solapen de verdad. Uno de los hilos cierra su sesión a mitad (el fallo que
dispara el close de hispagen) y la aserción es que NINGÚN error cruce de un hilo
a otro.

Ejecución:  python -m pytest tests/test_sesion_por_hilo_v105.py -q
"""
from __future__ import annotations

import inspect
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import requests

import config
from agent import fase1
from tests.harness_aislado import lanzar

URL = "https://ejemplo.invalid/acta"


# ============================== HTTP DE MENTIRA =============================

def _respuesta(texto: str, url: str) -> requests.Response:
    r = requests.Response()
    r.status_code = 200
    r._content = texto.encode("utf-8")
    r.url = url
    r.encoding = "utf-8"
    r.request = requests.Request("GET", url).prepare()
    return r


@pytest.fixture
def http_falso(monkeypatch):
    """Intercepta el envío de CUALQUIER requests.Session y registra qué sesión
    (id) y qué hilo hizo cada petición. `gancho` permite meter código dentro del
    envío, que es donde se puede forzar el solape con una barrera."""
    estado: dict = {"registro": [], "gancho": None}

    def _send(self, request, **kwargs):
        estado["registro"].append((threading.current_thread().name, id(self)))
        if estado["gancho"] is not None:
            estado["gancho"](self, request)
        return _respuesta("ok", request.url)

    monkeypatch.setattr(requests.Session, "send", _send)
    return estado


def _lanzar_hilos(funcion, nombres) -> list[threading.Thread]:
    hilos = [threading.Thread(target=funcion, name=n, args=(n,))
             for n in nombres]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(timeout=15)
    assert not any(h.is_alive() for h in hilos), "un hilo se quedó colgado"
    # Igual que hace el bot al terminar una consulta: los hilos ya murieron, así
    # que se cierran sus sesiones (si no, este módulo iría dejando sesiones
    # registradas de hilos muertos y los tests se estorbarían entre ellos).
    config.cerrar_sesiones_hijas()
    return hilos


# ============================ 1. SESIÓN POR HILO ============================

def test_cada_hilo_usa_su_propia_sesion(http_falso, monkeypatch):
    """Tres hilos -> tres sesiones distintas (antes: la MISMA para todos)."""
    monkeypatch.setattr(config, "_nueva_sesion", config._nueva_sesion)
    visto: dict = {}

    def _trabajo(nombre):
        config.SESSION.get(URL)
        # Se guarda el OBJETO (no id()): al mantenerlo vivo, Python no puede
        # reutilizar su dirección de memoria y la comparación es fiable.
        visto[nombre] = config.sesion()

    _lanzar_hilos(_trabajo, ["a", "b", "c"])

    sesiones = [visto[n] for n in ("a", "b", "c")]
    assert len({id(s) for s in sesiones}) == 3, "los hilos comparten sesión"
    principal = config.sesion()
    assert all(s is not principal for s in sesiones)
    assert len(http_falso["registro"]) == 3


def test_las_cookies_no_se_cruzan_entre_hilos(http_falso):
    """El estado de cada sesión es privado del hilo: si el hilo A deja una
    cookie, el hilo B no la ve (con la sesión compartida sí la veía)."""
    visto: dict = {}

    def _trabajo(nombre):
        config.SESSION.cookies.set("quien", nombre)
        config.SESSION.get(URL)                 # fuerza la creación de la sesión
        visto[nombre] = dict(config.SESSION.cookies)

    _lanzar_hilos(_trabajo, ["a", "b"])

    assert visto == {"a": {"quien": "a"}, "b": {"quien": "b"}}


# ============ 2. EL close() DE UN HILO NO ROMPE A LOS DEMÁS =================

def test_el_close_de_un_hilo_no_rompe_a_los_demas(http_falso, monkeypatch):
    """El caso hispagen: un hilo cierra su sesión EN MITAD de las peticiones
    solapadas de los otros. Cero errores cruzados."""
    nombres = ["a", "b", "fallo"]
    barrera = threading.Barrier(len(nombres))
    errores: list = []
    correctas: list = []
    cerrada_por_hilo: dict = {}

    class _Espia(requests.Session):
        """Sesión que recuerda si la han cerrado (no se espía requests por
        dentro: se pregunta al objeto)."""

        cerrada = False

        def close(self):
            self.cerrada = True
            return super().close()

    monkeypatch.setattr(config, "_nueva_sesion", lambda: _Espia())

    def _gancho(sesion, request):
        # Esto corre DENTRO del envío: la barrera garantiza que las 3 peticiones
        # están vivas a la vez cuando una de ellas cierra su sesión.
        try:
            barrera.wait(timeout=10)
        except threading.BrokenBarrierError:
            errores.append(("barrera", "no se juntaron los hilos"))
            return
        if threading.current_thread().name == "fallo":
            config.SESSION.close()        # lo que hace hispagen.py al reintentar
            cerrada_por_hilo["fallo"] = sesion.cerrada

    http_falso["gancho"] = _gancho

    def _trabajo(nombre):
        try:
            r = config.SESSION.get(URL)
            assert r.status_code == 200 and r.text == "ok"
            if nombre != "fallo":
                # La sesión DE ESTE hilo sigue viva: el close del otro no cruzó.
                cerrada_por_hilo[nombre] = config.sesion().cerrada
            correctas.append(nombre)
        except Exception as e:                      # noqa: BLE001 (es la prueba)
            errores.append((nombre, f"{type(e).__name__}: {e}"))

    _lanzar_hilos(_trabajo, nombres)

    assert errores == [], f"errores cruzados entre hilos: {errores}"
    assert sorted(correctas) == nombres
    assert cerrada_por_hilo["fallo"] is True, "el close no cerró su sesión"
    # Y los otros dos hilos tienen la suya intacta: el close no cruzó.
    assert cerrada_por_hilo["a"] is False
    assert cerrada_por_hilo["b"] is False


def test_tras_cerrar_su_sesion_el_hilo_puede_seguir(http_falso):
    """Cerrar la sesión del hilo no lo deja inservible: la siguiente petición
    crea una nueva (es lo que necesita el reintento con conexión limpia)."""
    config.SESSION.get(URL)
    primera = config.sesion()               # se guarda el OBJETO: comparar
    assert config.SESSION.close() is True   # id() sería frágil (Python reusa
    #                                         direcciones de memoria al liberar)
    config.SESSION.get(URL)
    assert config.sesion() is not primera


# ============ 3. CIERRE DE LAS SESIONES AL APAGAR EL POOL (SIN FUGAS) =======

def test_las_sesiones_de_los_hilos_se_cierran_al_apagar_el_pool(
        http_falso, monkeypatch):
    """Al salir de `sesiones_hilo_limpias()` (que envuelve el ThreadPoolExecutor
    de fase 1) las sesiones de los hilos hijos están cerradas y desregistradas:
    no quedan pools de conexiones abiertos."""
    cerradas: list = []
    creadas: list = []          # referencias vivas: evitan el reciclado de id()

    class _Espia(requests.Session):
        def __init__(self):
            super().__init__()
            creadas.append(self)

        def close(self):
            cerradas.append(id(self))
            return super().close()

    monkeypatch.setattr(config, "_nueva_sesion", lambda: _Espia())
    config.SESSION.close()          # parte de cero: cierra la del hilo de pytest
    antes = config.sesiones_abiertas()
    assert antes == 0

    def _trabajo(_):
        config.SESSION.get(URL)

    with config.sesiones_hilo_limpias(), ThreadPoolExecutor(max_workers=3) as ex:
        list(ex.map(_trabajo, range(3)))

    # OJO: el pool puede reutilizar hilos para varias tareas (con 3 tareas usa
    # los hilos que le da la gana), así que lo correcto no es "3 cierres" sino
    # "TODAS las sesiones de los hilos del pool están cerradas y ninguna queda
    # registrada". Se comparan los ids vistos en el envío con los cerrados.
    de_hilos = {sid for nombre, sid in http_falso["registro"]
                if nombre != "MainThread"}
    assert de_hilos, "los hilos del pool no llegaron a pedir nada"
    assert set(cerradas) == de_hilos, "quedó alguna sesión de hilo sin cerrar"
    assert set(cerradas) == {id(s) for s in creadas}
    assert config.sesiones_abiertas() == antes


def test_fase1_cierra_las_sesiones_del_pool():
    """fase1 envuelve su ThreadPoolExecutor con el cierre de sesiones, y en el
    orden correcto (el executor se apaga antes de cerrar)."""
    src = inspect.getsource(fase1.ejecutar_fase1)
    assert "sesiones_hilo_limpias()" in src
    assert (src.index("sesiones_hilo_limpias()")
            < src.index("ThreadPoolExecutor("))


# ============== 4. EL PATRÓN ANTIGUO NO QUEDA EN NINGÚN SCRAPER ============

def test_todos_los_scrapers_usan_la_sesion_por_hilo():
    """Nadie se fabrica su propia sesión global: todos usan el proxy por hilo
    que reparte config. Se comprueba en el código de scrapers/."""
    import scrapers.archivos
    import scrapers.familysearch
    import scrapers.hispagen
    import scrapers.web

    for modulo in (scrapers.archivos, scrapers.familysearch,
                   scrapers.hispagen, scrapers.web):
        # No se compara con `is config.SESSION` a propósito: hay un test que
        # recarga config (test_llamacpp_multi_v101) y en ese caso config.SESSION
        # es un proxy NUEVO mientras los módulos ya importados conservan el
        # suyo. Lo que importa es que ninguno use un objeto suelto.
        assert modulo.SESSION.__class__.__name__ == "_SesionHilo", modulo.__name__
        assert not isinstance(modulo.SESSION, requests.Session), modulo.__name__

    carpeta = Path(scrapers.web.__file__).parent
    for fichero in sorted(carpeta.glob("*.py")):
        codigo = fichero.read_text(encoding="utf-8")
        assert "requests.Session()" not in codigo, fichero.name


def test_no_se_crea_ninguna_sesion_al_importar(tmp_path):
    """La sesión es perezosa: importar el bot no abre ninguna conexión."""
    extra = "print('SESIONES_AL_IMPORTAR:', config.sesiones_abiertas())"
    r = lanzar(["--help"], tmp_path, extra=extra)

    assert r.returncode == 0, r.stdout[-600:] + r.stderr[-600:]
    assert "SESIONES_AL_IMPORTAR: 0" in r.stdout
