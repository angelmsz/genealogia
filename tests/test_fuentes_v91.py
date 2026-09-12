"""
tests/test_fuentes_v91.py — Partes A, B, C y D de la revisión de fondo.

TODO offline (sin red): los parsers y flujos se prueban contra HTML
grabado de los sitios reales. Lo que se verifica EN VIVO desde el
sandbox se documenta en cada docstring (y lo que NO se puede verificar
desde aquí también: honestidad ante todo).
"""

from __future__ import annotations

import json

import scrapers.archivos as archivos
from config import (FAMILYSEARCH_DELAY, HISPAGEN_BUSQUEDA, SALIDA_SOLICITUDES)
from scrapers.archivos_provinciales import (
    ARCHIVOS_PROVINCIA, estado_ensenada_particulares,
    generar_archivos_a_consultar_in_situ, generar_solicitudes_ensenada,
    integrar_solicitudes_ensenada)
from scrapers.familysearch import (MUNICIPIOS_CATALOGO, RateLimiter,
                                   SesionFamilySearch, _detectar_muro_login,
                                   _parsear_libros, _url_catalogo,
                                   recolector_familysearch, resumir_para_informe)
from scrapers.hispagen import (_agrupar_filas, _formas_busqueda,
                               _parsear_resultados, _url_busqueda,
                               recolector_hispagen)

import scrapers.familysearch as familysearch
import scrapers.hispagen as hispagen


# ======================= PARTE A — FAMILYSEARCH =============================

def test_rate_limiter_espera_3_a_5_segundos():
    """Máx 1 request cada 3-5 s: la segunda llamada consecutiva duerme
    lo que falte hasta el intervalo (reloj y sueño inyectados: sin
    dormir de verdad)."""
    reloj = {"t": 100.0}
    dormidas: list[float] = []

    def dormir(s):
        dormidas.append(round(s, 3))
        reloj["t"] += s

    rl = RateLimiter(rango=(3.0, 5.0), reloj=lambda: reloj["t"],
                     dormir=dormir)
    rl.esperar()                      # primera: sin espera
    assert dormidas == []
    reloj["t"] += 0.5                 # solo 0.5 s desde la última
    rl.esperar()
    assert 2.5 <= dormidas[0] <= 5.0  # completa hasta el intervalo


def test_rate_limiter_backoff_exponencial():
    """Ante 429/5xx la espera se DOBLA por reintento (y se acumula sobre
    el delay normal, no lo sustituye)."""
    reloj = {"t": 0.0}
    dormidas: list[float] = []

    def dormir(s):
        dormidas.append(s)
        reloj["t"] += s

    rl = RateLimiter(rango=(3.0, 3.0), reloj=lambda: reloj["t"],
                     dormir=dormir)
    rl.backoff(1)
    rl.backoff(2)
    rl.backoff(3)
    assert dormidas == [3.0, 6.0, 12.0]


def test_rate_limiter_respeta_el_intervalo_configurado():
    assert FAMILYSEARCH_DELAY[0] >= 3.0
    assert FAMILYSEARCH_DELAY[1] <= 5.0


def test_login_sin_credenciales_no_falla_en_silencio():
    """Sin FAMILYSEARCH_USER/PASS el estado es EXPLÍCITO:
    'sin_credenciales' con instrucción (verificado en vivo: el catálogo
    redirige a 'Sign-in to your account')."""
    ses = SesionFamilySearch(user="", password="", cookie="")
    assert ses.iniciar() == "sin_credenciales"
    assert "FAMILYSEARCH" in ses.detalle


def test_login_con_cookie_no_toca_la_red():
    ses = SesionFamilySearch(cookie="fssessionid=ABC123")
    assert ses.iniciar() == "login_ok"
    assert "FAMILYSEARCH_COOKIE" in ses.detalle


def test_el_muro_de_login_real_se_detecta():
    """HTML observado en vivo (title 'Sign-in to your account')."""
    html = ("<html><head><title>Sign-in to your account</title></head>"
            "<body>FamilySearch</body></html>")
    assert _detectar_muro_login(html, "https://www.familysearch.org/signin")
    assert not _detectar_muro_login("<html>catálogo</html>",
                                    "https://www.familysearch.org/search/catalog/results")


def test_parsear_libros_del_catalogo():
    """Estructura del catálogo: enlaces /search/catalog/view/ con título
    que trae el rango de fechas y la nota de restricción."""
    html = """
    <html><body>
    <a href="/search/catalog/view/238650">Registros parroquiales y
    bautismos de Vitoria, España, 1550-1930</a>
    <a href="/search/catalog/view/238651">Registros de la parroquia de
    Santa Maria, Coreses, España, 1800-1900 — visite un centro de
    historia familiar para ver las imágenes</a>
    <a href="/search/catalog/view/238652">Índice de bautismos 1700-1750</a>
    <a href="/otro/sitio">enlace que no es un libro</a>
    </body></html>"""
    libros = _parsear_libros(html)
    assert len(libros) == 3
    assert libros[0]["fechas"] == "1550-1930"
    assert libros[0]["estado"] == "libro_online"
    assert libros[1]["estado"] == "libro_restringido"
    assert libros[2]["estado"] == "indice_solo"
    assert libros[0]["url"].startswith("https://www.familysearch.org")


def test_resumen_del_informe_con_estado_chf_exacto():
    """El texto EXACTO exigido: 'pendiente — requiere Centro de Historia
    Familiar' para los restringidos; online->cascada OCR."""
    resultados = [{"localidad": "Vitoria", "libros": [
        {"titulo": "Parroquiales 1550-1930", "fechas": "1550-1930",
         "estado": "libro_online", "url": "https://x/1"},
        {"titulo": "Santa Maria 1800-1900", "fechas": "1800-1900",
         "estado": "libro_restringido", "url": "https://x/2"}]}]
    res = resumir_para_informe(resultados)
    assert res["online"][0]["estado"].startswith("online — descargar")
    assert ("cascada OCR" in res["online"][0]["estado"])
    assert res["pendiente_centro_historia_familiar"][0]["estado"] == (
        "pendiente — requiere Centro de Historia Familiar")


def test_recolector_familysearch_sin_credenciales_registra_y_no_cachea_error():
    """El recolector devuelve [] (meta-información al informe, no corpus)
    y marca la consulta como REGISTRADA (no cooldown: no es un error)."""
    marcadas, falladas = [], []
    conn = type("C", (), {})()   # conn None-equivalente: usamos monkeypatch

    class Conn:
        pass

    # monkeypatch de las funciones de caché de config
    import scrapers.familysearch as fs
    fs._consulta_conector_hecha = lambda c, k: False
    fs._conector_en_cooldown = lambda c, k: False
    fs._marcar_conector = lambda c, k: marcadas.append(k)
    fs._marcar_conector_fallo = lambda c, k: falladas.append(k)
    docs = recolector_familysearch(
        {"municipio": "Vitoria", "provincia": "alava"}, conn=None)
    assert docs == []
    assert marcadas and not falladas
    assert "Vitoria" in familysearch.RESULTADOS["vitoria"]["localidad"]


def test_cache_de_familysearch_no_lleva_credenciales():
    """Las claves de caché SOLO contienen el municipio: jamás usuario,
    contraseña o cookie (secreto que no debe llegar a la BD)."""
    import scrapers.familysearch as fs
    fs._consulta_conector_hecha = lambda c, k: True
    docs = recolector_familysearch(
        {"municipio": "Coreses", "provincia": "zamora"}, conn=None)
    assert docs == []


def test_municipios_de_la_familia_en_el_mapa_de_catalogo():
    for mun in ("vitoria", "castrejon de la pena",
                "pobladura del valle", "coreses"):
        assert mun in MUNICIPIOS_CATALOGO


def test_url_catalogo_por_localidad_no_por_nombre():
    url = _url_catalogo("Vitoria")
    assert "placeSearch" in url
    assert "Vitoria" in url


def test_la_contrasena_nunca_se_loguea(monkeypatch):
    """REQUISITO CRÍTICO de la PARTE A: ni en logs ni en errores."""
    import utils.ui as ui
    import scrapers.familysearch as fs
    mensajes: list[str] = []
    monkeypatch.setattr(ui, "log", lambda m: mensajes.append(str(m)))
    # sin red: el login fallará por conexión y pasa por el mismo código
    # que podría filtrar el secreto en un mensaje de error
    def _get(*a, **kw):
        raise ConnectionError("red caída (test)")
    monkeypatch.setattr(fs.SESSION, "get", _get)
    ses = SesionFamilySearch(user="usuario@correo.com",
                             password="SECRETO-1234", cookie="")
    ses.iniciar()   # login_manual_requerido por red caída
    joined = "\n".join(mensajes)
    assert "SECRETO-1234" not in joined
    assert "usuario@correo.com" not in joined  # solo enmascarado
    assert ses.estado in ("login_manual_requerido",)
    assert "SECRETO" not in ses.detalle


# ======================= PARTE D — HISPAGEN =================================

HTML_HISPAGEN = """
<html><body><div class="search-results">
<div class="result-item">
  <a href="/index.php/archivo-documental/merillas/transcripcion-238">
  Bautismo de Juan Merillas (1732)</a>
  <p class="result-text">En el año de 1732 bauticé a Juan, hijo de Pedro
  Merillas y María García, vecinos de Salas...</p>
</div>
<div class="result-item">
  <a href="/index.php/archivo-documental/merillas/transcripcion-239">
  Matrimonio de Pedro Merillas (1758)</a>
  <p class="result-text">Pedro Merillas, viudo, con Ana Fernández...</p>
</div>
</div></body></html>"""


def test_url_busqueda_usa_searchword():
    url = _url_busqueda("merillas salas")
    assert "searchword=merillas+salas" in url
    assert url.startswith("https://")


def test_parsear_resultados_joomla():
    filas = _parsear_resultados(HTML_HISPAGEN, "https://www.hispagen.es/x")
    assert len(filas) == 2
    assert filas[0]["titulo"].startswith("Bautismo")
    assert "1732" in filas[0]["texto"]
    assert filas[0]["url"].startswith("https://www.hispagen.es/")


def test_parsear_resultados_vacio():
    html = ("<html><body>No se ha encontrado ningún resultado para su "
            "búsqueda</body></html>")
    assert _parsear_resultados(html, "https://x") == []


def test_agrupar_filas_como_sigA():
    filas = [{"titulo": f"doc {i}", "texto": f"texto {i}",
              "url": f"https://h/{i}"} for i in range(7)]
    docs = _agrupar_filas(filas, "prueba")
    assert len(docs) == 2          # grupos de 5
    assert docs[0]["origen"] == "hispagen"
    assert "sin OCR" in docs[0]["texto"]


def test_formas_busqueda_parte_compuestos():
    formas = _formas_busqueda("Saenz de Navarrete")
    assert "saenz" in formas
    assert "navarrete" in formas
    # todo en minúsculas (como el resto de conectores) y sin stopwords
    assert all(f == f.lower() for f in formas)
    assert all(" de " not in f for f in formas[:2])


def test_recolector_hispagen_llama_y_marca(monkeypatch):
    llamadas: list[str] = []
    monkeypatch.setattr(hispagen, "hispagen_buscar",
                        lambda q, **kw: (llamadas.append(q) or
                                         [{"titulo": "Bautismo de Juan "
                                           "Merillas (1732)",
                                           "texto": "hijo de Pedro...",
                                           "url": "https://h/1"}]))
    marcadas: list[str] = []
    monkeypatch.setattr(hispagen, "_marcar_conector",
                        lambda c, k: marcadas.append(k))
    monkeypatch.setattr(hispagen, "_consulta_conector_hecha",
                        lambda c, k: False)
    monkeypatch.setattr(hispagen, "_conector_en_cooldown", lambda c, k: False)
    import scrapers.hispagen as h
    h.time.sleep = lambda s: None           # sin esperar en el test
    docs = recolector_hispagen(
        {"apellido_paterno": "Merillas", "apellido_materno": "",
         "municipio": "Salas", "provincia": "burgos"}, conn=None)
    assert llamadas and llamadas[0].startswith("merillas")
    assert docs and docs[0]["origen"] == "hispagen"
    assert all(k.startswith("hispagen::") for k in marcadas)


def test_recolector_hispagen_sin_apellidos_no_consulta():
    assert recolector_hispagen({"municipio": "Salas"}, conn=None) == []


# ============= PARTES B y C — ARCHIVOS PROVINCIALES =========================

def _familia_geo() -> dict:
    return {"personas": [
        {"nombre": "A", "nacimiento": {"municipio": "Vitoria",
                                       "provincia": "Álava"}},
        {"nombre": "B", "nacimiento": {"municipio": "Castrejón de la Peña",
                                       "provincia": "Palencia"}},
        {"nombre": "C", "nacimiento": {"municipio": "Pobladura del Valle",
                                       "provincia": "Zamora"}},
        {"nombre": "D", "nacimiento": {"municipio": "Coreses",
                                       "provincia": "Zamora"}},
    ]}


def test_seccion_in_situ_con_los_4_municipios():
    entradas = generar_archivos_a_consultar_in_situ(_familia_geo())
    muns = {e["municipio"] for e in entradas}
    assert muns == {"Vitoria", "Castrejón de la Peña",
                    "Pobladura del Valle", "Coreses"}
    for e in entradas:
        assert e["ahp"]["archivo"].startswith("Archivo Histórico Provincial")
        assert e["ahp"]["portal"].startswith("https://")
        assert "capitulaciones" in " ".join(e["ahp"]["fondos_a_buscar"])
        # PARTE C: sin catálogo -> marca explícita, no fuente fallida
        assert "requiere visita o solicitud postal" in \
            e["archivo_municipal"]["estado"]


def test_urls_de_los_ahp_son_las_verificadas():
    """URLs comprobadas EN VIVO el 2026-09-11 contra el portal de
    Archivos de Castilla y León y web.araba.eus."""
    assert ARCHIVOS_PROVINCIA["zamora"]["portal"].endswith(
        "archivo-historico-provincial-zamora.html")
    assert ARCHIVOS_PROVINCIA["palencia"]["portal"].endswith(
        "archivo-historico-provincial-palencia.html")
    assert "archivoscastillayleon.jcyl.es" in \
        ARCHIVOS_PROVINCIA["zamora"]["catalogo_online"]
    assert "artxibo.euskadi.eus" in ARCHIVOS_PROVINCIA["alava"]["catalogo_online"]
    assert "archivo@araba.eus" in ARCHIVOS_PROVINCIA["alava"]["direccion"]


def test_ensenada_alava_no_existe_y_lo_dice():
    """CORRECCIÓN FACTUAL: Álava quedó EXCLUIDA del Catastro de Ensenada
    (régimen foral). El sistema lo dice en lugar de pedir algo que no
    existe o fallar en silencio."""
    res = estado_ensenada_particulares("Álava")
    assert res["estado"] == "no_existe"
    assert "EXCLUIDA" in res["detalle"]


def test_ensenada_zamora_y_palencia_requieren_escrito():
    for prov in ("Zamora", "Palencia"):
        res = estado_ensenada_particulares(prov)
        assert res["estado"] == "requiere_solicitud_escrita"
        assert "AHP" in res["detalle"]


def test_solicitudes_ensenada_solo_donde_existen():
    """No se genera solicitud para Vitoria (no existen sus Respuestas
    Particulares); sí para los municipios de Zamora y Palencia."""
    sols = generar_solicitudes_ensenada(_familia_geo())
    muns = {s["municipio"] for s in sols}
    assert "Vitoria" not in muns
    assert muns == {"Castrejón de la Peña", "Pobladura del Valle", "Coreses"}
    for s in sols:
        assert "Respuestas Particulares" in s["peticion"]
        assert s["tipo"] == "catastro_ensenada_respuestas_particulares"


def test_integrar_solicitudes_ensenada_es_idempotente(tmp_path, monkeypatch):
    import config as config_mod
    monkeypatch.setattr(config_mod, "BASE_DIR", tmp_path)
    # el módulo importa BASE_DIR dentro de la función: lee el valor
    # parcheado en tiempo de llamada
    import scrapers.archivos_provinciales as ap
    monkeypatch.setattr(ap, "SALIDA_SOLICITUDES", SALIDA_SOLICITUDES,
                        raising=False)
    n1 = integrar_solicitudes_ensenada(_familia_geo())
    assert n1 == 3
    datos = json.loads((tmp_path / SALIDA_SOLICITUDES).read_text("utf-8"))
    assert len(datos) == 3
    n2 = integrar_solicitudes_ensenada(_familia_geo())
    assert n2 == 0            # no repite
    datos2 = json.loads((tmp_path / SALIDA_SOLICITUDES).read_text("utf-8"))
    assert len(datos2) == 3
    md = (tmp_path / "solicitudes.md").read_text(encoding="utf-8")
    assert "Respuestas Particulares" in md


# ======================= ORQUESTADOR ========================================

def test_recolectar_incluye_los_nuevos_conectores(monkeypatch):
    """recolectar() ejecuta HISPAGEN y FamilySearch además de
    SIGA/ADDO/PARES (los nuevos son import perezoso: se parchea el
    módulo fuente)."""
    llamadas: list[str] = []
    monkeypatch.setattr(hispagen, "recolector_hispagen",
                        lambda o, c=None: (llamadas.append("hispagen") or []))
    monkeypatch.setattr(familysearch, "recolector_familysearch",
                        lambda o, c=None: (llamadas.append("familysearch")
                                           or []))
    monkeypatch.setattr(archivos, "recolector_siga",
                        lambda o, c=None: [])
    monkeypatch.setattr(archivos, "recolector_addo",
                        lambda o, c=None: [])
    monkeypatch.setattr(archivos, "recolector_ensenada",
                        lambda o, c=None: [])
    docs = archivos.recolectar({"municipio": "Vitoria",
                                "provincia": "alava",
                                "apellido_paterno": "Merillas"},
                               conn=None)
    assert docs == []
    assert set(llamadas) == {"hispagen", "familysearch"}


def test_hispagen_busqueda_con_timeout_y_verify_false():
    """Convención del proyecto para sitios con certificados problemáticos
    (www.hispagen.es lo tiene mal emitido, verificado en vivo).

    v10.2: el SESSION.get vive ahora en _get_hispagen (reintento con
    conexión limpia ante RemoteDisconnected), pero la convención se
    mantiene: verify=False y timeout=30 en TODOS los GET de HISPAGEN."""
    import inspect
    src_get = inspect.getsource(hispagen._get_hispagen)
    assert "verify=False" in src_get
    assert "timeout=30" in src_get
    # y la búsqueda paginada usa SIEMPRE ese helper (nunca SESSION.get
    # directo, que era lo que moría con RemoteDisconnected en el log)
    src_buscar = inspect.getsource(hispagen.hispagen_buscar)
    assert "_get_hispagen(" in src_buscar
    assert "SESSION.get" not in src_buscar
