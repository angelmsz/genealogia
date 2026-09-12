"""
scrapers/familysearch.py — PARTE A (v9.1): conector del CATÁLOGO de
FamilySearch por localidad.
QUÉ ESTÁ VERIFICADO EN VIVO (2026-09-10, y de nuevo 2026-09-11):
  - https://www.familysearch.org/search/catalog/results?placeSearch=Vitoria
    redirige a "Sign-in to your account": el catálogo de microfilmación
    EXIGE sesión iniciada. Sin cuenta no hay listado de libros.
  - Existe una API oficial OAuth 2.0 (developers.familysearch.org) pero
    requiere registrar una aplicación propia: no es viable para un
    usuario particular sin trámite previo.

POR QUÉ CATÁLOGO POR LOCALIDAD Y NO BÚSQUEDA POR NOMBRE:
  el buscador nominal de FamilySearch parte los apellidos compuestos
  ("Saenz de Navarrete" no encuentra nada); el catálogo por placeSearch
  lista los LIBROS parroquiales microfilmados de cada municipio con sus
  rangos de fechas, que es justo lo que hace falta ANTES de leer nada.

ESTADOS EXPLÍCITOS (nada falla en silencio):
  - login_ok               -> sesión iniciada con user/pass del .env
  - login_fallo_credenciales -> 401/403 del formulario
  - login_manual_requerido -> el formulario pide captcha/2FA o cambió:
                              el usuario debe pegar su cookie de sesión
                              en FAMILYSEARCH_COOKIE del .env (F12 ->
                              Network -> Cookie: fssessionid=...)
  - libro online           -> imágenes navegables con sesión de Iglesia
                              o cuenta: se registra la URL del visor
                              para descarga manual -> cascada OCR
  - libro_restringido      -> "pendiente — requiere Centro de Historia
                              Familiar": solo consultable en un centro
                              físico o biblioteca afiliada
  - indice_solo            -> el índice es consultable pero las imágenes
                              no están publicadas

HONESTIDAD DE IMPLEMENTACIÓN: la descarga AUTOMÁTICA de las imágenes de
un libro NO está implementada. Razón: las imágenes van detrás del visor
web de FamilySearch (autenticado, con tokens de sesión por grupo de
imágenes) y sus términos de uso; descargar a ciegas con las credenciales
del usuario sería exactamente el "conector que falla en silencio" que este
proyecto se niega a ser. Lo que SÍ hace el conector: listar libros y
rangos ANTES de leer (como pide el método), registrar online/restringido
y dejar la URL del visor para que la descarga sea una decisión explícita.

SEGURIDAD DE CREDENCIALES:
  - FAMILYSEARCH_USER / FAMILYSEARCH_PASS del .env NUNCA se loguean
    (ui.log recibe solo el usuario ENMASCARADO si acaso) y NUNCA se
    persisten en cache_agente.db: las claves de caché de consultas son
    del tipo 'familysearch::catalogo::vitoria' (solo el municipio).
  - La cookie de sesión se guarda SOLO en memoria (self.sesion).

RATE LIMITING: máx 1 request cada 3-5 s (FAMILYSEARCH_DELAY) con
backoff exponencial ante 429/5xx, hasta FAMILYSEARCH_MAX_REINTENTOS.
"""

from __future__ import annotations

import os
import random
import re
import time
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from config import (FAMILYSEARCH_CATALOGO, FAMILYSEARCH_DELAY,
                    FAMILYSEARCH_LOGIN, FAMILYSEARCH_MAX_REINTENTOS,
                    FAMILYSEARCH_PASS, FAMILYSEARCH_URL, FAMILYSEARCH_USER,
                    SESSION, _conector_en_cooldown, _consulta_conector_hecha,
                    _marcar_conector, _marcar_conector_fallo, normalizar)
from utils import ui

# Clave de cookie de sesión de FamilySearch (fssessionid es la que usa el
# portal; se acepta también el valor completo de la cabecera Cookie).
COOKIE_SESION = "fssessionid"

# Municipios de la investigación con su forma canónica de búsqueda en
# el catálogo (FamilySearch indexa los topónimos con su forma oficial;
# el mapa normaliza variantes conocidas; cualquier otro municipio pasa
# tal cual: el conector es general, no solo para estos cuatro).
MUNICIPIOS_CATALOGO = {
    "vitoria": "Vitoria",
    "vitoria-gasteiz": "Vitoria-Gasteiz",
    "castrejon de la pena": "Castrejón de la Peña",
    "roscales de la pena": "Roscales de la Peña",
    "pobladura del valle": "Pobladura del Valle",
    "coreses": "Coreses",
}

# Acumulador de la ejecución: municipio normalizado -> resultado del
# catálogo. Lo lee main.fase1() para escribir la sección "familysearch"
# del informe_fase1.json (estados explícitos, nunca en silencio).
RESULTADOS: dict[str, dict] = {}


# ============================== RATE LIMITER ================================

class RateLimiter:
    """Máx 1 request cada min..max segundos, con reloj inyectable para
    poder testearlo sin dormir de verdad. En fallos (429/5xx) el backoff
    es exponencial: espera * 2^intento, y esa espera NO sustituye al
    delay normal: se acumula."""

    def __init__(self, rango: tuple[float, float] = FAMILYSEARCH_DELAY,
                 reloj=time.monotonic, dormir=time.sleep):
        self.rango = rango
        self.ultimo = None
        self.reloj = reloj
        self.dormir = dormir

    def esperar(self) -> float:
        """Duerme lo que haga falta y devuelve los segundos esperados."""
        if self.ultimo is not None:
            transcurrido = self.reloj() - self.ultimo
            objetivo = random.uniform(*self.rango)
            falta = objetivo - transcurrido
            if falta > 0:
                self.dormir(falta)
                esperado = falta
            else:
                esperado = 0.0
        else:
            esperado = 0.0
        self.ultimo = self.reloj()
        return esperado

    def backoff(self, intento: int) -> float:
        """Espera exponencial tras un 429/5xx: base * 2^intento."""
        base = random.uniform(*self.rango)
        espera = base * (2 ** max(0, intento - 1))
        self.dormir(espera)
        self.ultimo = self.reloj()
        return espera


# ============================== SESIÓN ======================================

class SesionFamilySearch:
    """Sesión del catálogo. Orden de intento:
    1. cookie pegada a mano en .env (FAMILYSEARCH_COOKIE) — el camino
       REALISTA para un particular (el login web lleva captcha/2FA);
    2. login programático user/pass (formulario ident.familysearch.org);
    3. sin credenciales -> estado 'sin_credenciales' (no falla en
       silencio: se informa y se registra como pendiente).

    El estado queda en self.estado con una de las claves de arriba.
    """

    def __init__(self, user: str = FAMILYSEARCH_USER,
                 password: str = FAMILYSEARCH_PASS,
                 cookie: str | None = None,
                 limitador: RateLimiter | None = None):
        self.user = user
        self.password = password
        # v9.2 — FAMILYSEARCH_COOKIE ya se lee DE VERDAD del .env (antes
        # solo estaba documentada; la cookie había que pasarla por código).
        self.cookie = (cookie
                       or os.getenv("FAMILYSEARCH_COOKIE", "")).strip()
        self.limitador = limitador or RateLimiter()
        self.estado = "sin_credenciales"
        self.detalle = ""

    def _aplicar_cookie(self) -> bool:
        if not self.cookie:
            return False
        valor = self.cookie
        if "=" not in valor:      # pegaron solo el valor de fssessionid
            valor = f"{COOKIE_SESION}={valor}"
        SESSION.cookies.update({"fssessionid": valor.split("=", 1)[1]
                                if valor.startswith(COOKIE_SESION)
                                else valor})
        self.estado = "login_ok"
        self.detalle = "sesión vía FAMILYSEARCH_COOKIE (.env)"
        return True

    def iniciar(self) -> str:
        """Deja la sesión lista (o registra honestamente por qué no)."""
        if self._aplicar_cookie():
            return self.estado
        if not (self.user and self.password):
            self.estado = "sin_credenciales"
            self.detalle = ("falta FAMILYSEARCH_USER/FAMILYSEARCH_PASS "
                            "(o FAMILYSEARCH_COOKIE) en el .env")
            return self.estado
        # login programático: formulado con máximas precapciones; si
        # FamilySearch pide captcha/2FA o cambia el formulario, NO se
        # simula éxito: estado login_manual_requerido con instrucciones.
        try:
            self.limitador.esperar()
            r = SESSION.get(FAMILYSEARCH_LOGIN, timeout=30)
            sopa = BeautifulSoup(r.text, "html.parser")
            form = sopa.find("form")
            campos = {i.get("name"): i.get("value", "")
                      for i in form.find_all("input")
                      if i.get("name")} if form else {}
            campos.update({"username": self.user, "password": self.password})
            accion = (form.get("action") if form else "") or FAMILYSEARCH_LOGIN
            if not accion.startswith("http"):
                accion = FAMILYSEARCH_URL + accion
            self.limitador.esperar()
            r2 = SESSION.post(accion, data=campos, timeout=30)
            if r2.status_code in (401, 403):
                self.estado = "login_fallo_credenciales"
                self.detalle = f"HTTP {r2.status_code} al validar"
            elif "sign-in" in r2.url or "signin" in r2.url:
                self.estado = "login_manual_requerido"
                self.detalle = ("el formulario exigió captcha/2FA o cambió: "
                                "pega tu cookie de sesión en "
                                "FAMILYSEARCH_COOKIE del .env "
                                "(F12 -> Network -> Cookie: fssessionid=...)")
            else:
                self.estado = "login_ok"
                self.detalle = "login programático correcto"
        except Exception as e:  # red caída, TLS, etc.
            self.estado = "login_manual_requerido"
            self.detalle = (f"login inalcanzable ({str(e)[:70]}); usa "
                            "FAMILYSEARCH_COOKIE del .env")
        # NUNCA loguear la contraseña; el usuario solo en forma enmascarada
        ui.log(f"FamilySearch: {self.estado}"
               + (f" (usuario {'*' * (len(self.user) - 2)}"
                  f"{self.user[-2:]})" if self.user else ""))
        return self.estado


# ============================== CATÁLOGO ====================================

def _url_catalogo(localidad: str) -> str:
    return (f"{FAMILYSEARCH_CATALOGO}"
            f"?placeSearch={quote_plus(localidad)}")


def _detectar_muro_login(html: str, url_final: str) -> bool:
    """True si la respuesta es el muro de login (verificado en vivo:
    title 'Sign-in to your account')."""
    return ("sign-in" in (url_final or "").lower()
            or "Sign-in to your account" in (html or ""))


def _parsear_libros(html: str) -> list[dict]:
    """Convierte la página de resultados del catálogo (una por localidad)
    en libros: {titulo, fechas, estado, url}.

    El HTML real del catálogo cambia con frecuencia: el parser es
    tolerante (busca patrones de título de libro con rango de fechas) y
    todo lo que no reconozca queda con estado 'desconocido' — visible en
    el informe, nunca descartado en silencio."""
    sopa = BeautifulSoup(html or "", "html.parser")
    libros: list[dict] = []
    vistos: set[str] = set()
    for a in sopa.find_all("a", href=True):
        titulo = a.get_text(" ", strip=True)
        if not titulo or len(titulo) < 8 or titulo in vistos:
            continue
        href = a["href"]
        if "/search/catalog/view/" not in href:
            continue
        vistos.add(titulo)
        # el rango de fechas suele venir en el propio título del resultado
        # del catálogo ("Registros parroquiales ... 1550-1930") o al lado
        m = re.search(r"(1[4-9]\d{2}|19\d{2}|20\d{2})\s*[-–]\s*"
                      r"(1[4-9]\d{2}|19\d{2}|20\d{2})", titulo)
        fechas = m.group(0) if m else ""
        texto_zona = " ".join(titulo.split())
        estado = "desconocido"
        h = texto_zona.lower()
        if "centro de historia familiar" in h or "family history center" in h:
            estado = "libro_restringido"
        elif "restringido" in h or "privacy" in h:
            estado = "libro_restringido"
        elif "índice" in h or "index" in h:
            estado = "indice_solo"
        elif fechas:
            estado = "libro_online"
        libros.append({
            "titulo": texto_zona[:160],
            "fechas": fechas,
            "estado": estado,
            "url": (FAMILYSEARCH_URL + href if href.startswith("/")
                    else href),
        })
    return libros


def catalogo_localidad(localidad: str,
                       sesion: SesionFamilySearch | None = None) -> dict:
    """Listado de libros parroquiales microfilmados de UNA localidad, con
    rangos de fechas, ANTES de leer ninguna imagen.

    Devuelve {"localidad", "estado", "detalle", "libros": [...],
    "url_consulta"}. Nunca lanza: cualquier problema queda como estado
    explícito en el resultado (la llamadora lo guarda en el informe)."""
    sesion = sesion or SesionFamilySearch()
    out = {"localidad": localidad, "estado": "", "detalle": "",
           "libros": [], "url_consulta": _url_catalogo(localidad)}
    if sesion.estado != "login_ok":
        est = sesion.iniciar()
        if est != "login_ok":
            out["estado"] = est
            out["detalle"] = sesion.detalle
            return out
    url = _url_catalogo(localidad)
    for intento in range(1, FAMILYSEARCH_MAX_REINTENTOS + 1):
        try:
            sesion.limitador.esperar()
            r = SESSION.get(url, timeout=30, verify=False)
            if r.status_code in (429, 500, 502, 503, 504):
                sesion.limitador.backoff(intento)
                continue
            if _detectar_muro_login(r.text, str(r.url)):
                out["estado"] = "login_manual_requerido"
                out["detalle"] = ("el catálogo pidió login con la respuesta "
                                  "(cookie caducada): pega una nueva en "
                                  "FAMILYSEARCH_COOKIE del .env")
                return out
            r.raise_for_status()
            out["libros"] = _parsear_libros(r.text)
            out["estado"] = "catalogo_ok"
            out["detalle"] = f"{len(out['libros'])} libros listados"
            return out
        except Exception as e:
            if intento == FAMILYSEARCH_MAX_REINTENTOS:
                out["estado"] = "catalogo_inalcanzable"
                out["detalle"] = f"red/HTTP: {str(e)[:90]}"
            else:
                sesion.limitador.backoff(intento)
    return out


# ============================== RECOLECTOR ==================================

def resumir_para_informe(resultados: list[dict]) -> dict:
    """Aplana los resultados por localidad en la sección del informe:
    separa online / restringidos (Centro de Historia Familiar) / otros,
    con el texto EXACTO que exige la especificación de la PARTE A."""
    online, chf, otros = [], [], []
    for res in resultados:
        for libro in res.get("libros", []):
            entrada = {"localidad": res["localidad"],
                       "titulo": libro["titulo"],
                       "fechas": libro["fechas"],
                       "url": libro["url"]}
            if libro["estado"] == "libro_restringido":
                entrada["estado"] = ("pendiente — requiere Centro de "
                                     "Historia Familiar")
                chf.append(entrada)
            elif libro["estado"] == "libro_online":
                entrada["estado"] = ("online — descargar manualmente y "
                                     "soltar en la cascada OCR "
                                     "(--probar-ocr / importar propios)")
                online.append(entrada)
            else:
                entrada["estado"] = libro["estado"] or "desconocido"
                otros.append(entrada)
    return {"online": online,
            "pendiente_centro_historia_familiar": chf,
            "otros": otros}


def recolector_familysearch(objetivo: dict, conn=None) -> list[dict]:
    """Recolector estándar del agente: para el municipio del objetivo,
    lista los libros del catálogo ANTES de leer nada. NO devuelve
    fragmentos de corpus (el listado es meta-información de fuente, no
    hechos genealógicos): su salida se acumula en RESULTADOS y llega al
    informe_fase1.json vía main.fase1(). Con claves de caché SIN
    credenciales (solo municipio).

    Mantenida la convención de errores transitorios: fallo de red ->
    cooldown y reintento tras TTL (nunca 'consulta hecha')."""
    mun_crudo = (objetivo.get("municipio") or "").strip()
    mun = normalizar(mun_crudo)
    if not mun:
        return []
    localidad = MUNICIPIOS_CATALOGO.get(mun, mun_crudo)
    clave = f"familysearch::catalogo::{mun}"
    if _conector_en_cooldown(conn, clave) or _consulta_conector_hecha(conn,
                                                                      clave):
        return []
    res = catalogo_localidad(localidad)
    RESULTADOS[mun] = res      # para la sección del informe
    if res["estado"] in ("catalogo_ok", "login_manual_requerido",
                         "login_fallo_credenciales", "sin_credenciales"):
        _marcar_conector(conn, clave)      # resultado REGISTRADO (no error)
    else:
        _marcar_conector_fallo(conn, clave)  # transitorio: reintentar TTL
        ui.log_warn(f"FamilySearch '{localidad}': {res['estado']} "
                    f"({res['detalle'][:70]}) — queda en cooldown")
        return []
    n = len(res.get("libros", []))
    if n:
        ui.log_ok(f"FamilySearch '{localidad}': {n} libros parroquiales "
                  f"en el catálogo (rango de fechas listo ANTES de leer)")
    elif res["estado"] == "catalogo_ok":
        ui.log(f"FamilySearch '{localidad}': 0 libros en el catálogo "
               f"para ese topónimo (prueba la forma oficial del lugar)")
    return []
