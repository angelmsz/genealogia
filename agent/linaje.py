"""
agent/linaje.py — RASTREO DEL LINAJE hacia arriba (v10.4.2, BLOQUE 4).

QUÉ HACE (el "trabajo sucio" de ir nombre a nombre)
---------------------------------------------------
Tira del hilo de una partida y no para: de la partida de una persona saca los
NOMBRES DE SUS PADRES, busca a esos padres en el índice, saca los suyos, y
sigue hacia arriba por TODAS las líneas (la del padre y la de la madre, o sea
también las líneas de las mujeres), apuntando además a los HERMANOS de cada
antepasado y a los hijos de los antepasados (tíos y primos).

    Víctor (b. 1885, Navaridas)
      → su partida dice: padre Eusebio, madre Leocadia
        → busca a Eusebio en la ventana 1850-1867 (un padre va 18-35 años
          por delante del hijo) → su bautismo (1858) → sus padres: Pablo + Josefa
          → busca a Leocadia (Dopico) en la misma ventana → 1863 → sus padres:
            Román María Dopico + Fermina Guzmán
            → y así hasta que el índice se queda seco (1481) o se agotan las
              consultas.

CÓMO DECIDE (sin inventar)
--------------------------
- Un eslabón SOLO se da por bueno si la propia partida lo dice ("hijo de X y de
  Y"): eso es evidencia, no una corazonada.
- Los años de los padres son ESTIMACIONES (hijo − 18 a 35 años) y van marcados.
- El candidato tiene que caer en la ventana de años y llamarse igual (nombre +
  primer apellido, con tolerancia de acentos y de apellidos compuestos). Se
  puntúa además si coincide la parroquia o el municipio:
      alta  = misma parroquia · media = mismo municipio · baja = otro sitio
- Cuando hay varias posibilidades, se eligen y SE LISTAN las alternativas: el
  bot no esconde las dudas.
- Los eslabones que no se pueden cerrar van a "ESLABONES QUE FALTAN": eso es
  exactamente lo que merece la pena pedir al archivo (la copia de pago).

QUÉ NO HACE
-----------
- No escribe NADA en el árbol (`familia_conocida.json`) ni en los `arbol_*`.
- No inventa padres que no estén en una partida.
- No sale de Álava: el índice online que hay es el del AHDV (1481-1900).

El estado se guarda en `linaje_alava.json` y es REANUDABLE: si se corta (o se
acaba la tanda de consultas), la siguiente vez sigue por donde iba.
"""

from __future__ import annotations

import json
import random
import time
from datetime import datetime
from pathlib import Path

from config import (BASE_DIR, LINAJE_DELAY, LINAJE_INFORME,
                    LINAJE_MAX_CONSULTAS, LINAJE_MAX_GENERACIONES,
                    LINAJE_MIN_ANIO, LINAJE_VENTANA, LINAJE_VENTANA_HIJOS,
                    LINAJE_VENTANA_PADRES, LINAJE_VENTANA_SEMILLA,
                    escribir_con_backup, mismo_apellido, mismo_nombre,
                    normalizar)
from utils import ui

# Papel de cada persona en el rastreo
ROL_LINEA = "linea"            # antepasado directo (sube la línea)
ROL_COLATERAL = "colateral"    # hermano/a, tío/a, primo/a: pariente, no línea
# Estado de cada persona de la cola
EST_PENDIENTE = "pendiente"
EST_IDENTIFICADO = "identificado"
EST_DUDOSO = "dudoso"
EST_SIN_RESULTADO = "sin_resultado"
EST_SIN_DATOS = "sin_datos_para_buscar"
# Confianza del eslabón
CONF_ALTA, CONF_MEDIA, CONF_BAJA = "alta", "media", "baja"

_ORDEN_CONFIANZA = {CONF_ALTA: 0, CONF_MEDIA: 1, CONF_BAJA: 2}


# ============================== PIEZAS PURAS ===============================

def ventana_padres(anio_hijo: int) -> tuple[int, int]:
    """Ventana de años en la que tiene que caer el bautismo de un progenitor:
    (hijo − 35, hijo − 18). Fuera de ahí no se acepta como candidato."""
    margen_arriba, margen_abajo = LINAJE_VENTANA_PADRES
    return anio_hijo - margen_abajo, anio_hijo - margen_arriba


def ventana_semilla(anio: int) -> tuple[int, int]:
    """Ventana ancha alrededor del año estimado de una persona YA conocida (las
    estimaciones del árbol son a ojo: ±20 años)."""
    return anio - LINAJE_VENTANA_SEMILLA, anio + LINAJE_VENTANA_SEMILLA


def ventana_hijos(anio: int) -> tuple[int, int]:
    """Ventana en la que caen los HIJOS de alguien: (año + 18, año + 50).

    Hace falta porque la partida de un hijo lleva la fecha DEL HIJO: buscando a
    Eusebio (1858) solo en su propia ventana aparecería su bautismo, y sus
    hijos (1885-1894) quedarían fuera. Con esta ventana salen todos.
    """
    margen_abajo, margen_arriba = LINAJE_VENTANA_HIJOS
    return anio + margen_abajo, anio + margen_arriba


def anio_estimado_de_progenitor(anio_hijo: int) -> int:
    """Año estimado de un progenitor: el hijo menos 28 (el centro de la ventana
    18-35). Sirve para calcular la ventana de los HIJOS de ese progenitor
    —o sea, los hermanos del hijo conocido— cuando su propio bautismo no
    aparece en el índice."""
    margen_abajo, margen_arriba = LINAJE_VENTANA_PADRES
    return anio_hijo - (margen_abajo + margen_arriba) // 2


def clave_de(nombre: str, apellido1: str, anio=None, sufijo: str = "") -> str:
    """Clave estable de una persona del rastreo."""
    base = normalizar(f"{nombre} {apellido1}")
    if anio:
        return f"{base}::{anio}{sufijo}"
    return f"{base}{sufijo}"


def es_la_persona(fila: dict, nombre: str, apellido1: str) -> bool:
    """¿El BAUTIZADO de esta fila es esa persona? (nombre + primer apellido)

    El nombre se compara con `mismo_nombre`, que tolera la ortografía de la
    época ('Eusevio' es Eusebio, 'Yluminado' es Ylluminado).
    """
    suyo = fila.get("persona") or {}
    if not mismo_nombre(suyo.get("nombre", ""), nombre or ""):
        return False
    return mismo_apellido(suyo.get("apellido1", ""), apellido1 or "")


def es_hijo_suyo(fila: dict, nombre: str, apellido1: str,
                 rol: str = "padre") -> bool:
    """¿Esta fila es una partida de un hijo suyo? (aparece como progenitor)"""
    progenitor = fila.get(rol) or {}
    if not progenitor:
        return False
    if not mismo_nombre(progenitor.get("nombre", ""), nombre or ""):
        return False
    return mismo_apellido(progenitor.get("apellido1", ""), apellido1 or "")


def padres_de(fila: dict) -> list[dict]:
    """Padres que DECLARA la partida (padre y madre, de los que se sepa nombre).

    El índice los da como nombre + primer apellido + '--' cuando no consta el
    segundo: aquí se devuelven los que se pueden buscar.
    """
    padres = []
    for rol, etiqueta in (("padre", "padre"), ("madre", "madre")):
        persona = fila.get(rol) or {}
        if not persona.get("nombre"):
            continue
        padres.append({
            "rol": etiqueta,
            "nombre": persona["nombre"],
            "apellido1": persona.get("apellido1", ""),
            "apellido2": persona.get("apellido2", ""),
            "completo": persona.get("completo", ""),
        })
    return padres


def en_ventana(fila: dict, ini: int, fin: int) -> bool:
    anio = fila.get("anio")
    return bool(anio) and ini <= int(anio) <= fin


def elegir_candidato(filas: list[dict], busqueda: dict) -> tuple:
    """De las filas que devuelve el buscador, cuál es esa persona.

    Devuelve (fila_elegida|None, confianza, alternativas). Solo se aceptan
    filas donde el BAUTIZADO sea la persona buscada y el año caiga en la
    ventana; la puntuación premia que coincida la parroquia (lo más fuerte),
    el municipio y el segundo apellido si se conocía.
    """
    ini, fin = busqueda["ventana"]
    candidatas = [f for f in filas
                  if es_la_persona(f, busqueda["nombre"], busqueda["apellido1"])
                  and en_ventana(f, ini, fin)]
    if not candidatas:
        return None, CONF_BAJA, []
    centro = (ini + fin) / 2

    def _puntos(fila: dict) -> int:
        puntos = 0
        parroquia = normalizar(busqueda.get("parroquia", ""))
        if parroquia and normalizar(fila.get("parroquia", "")) == parroquia:
            puntos += 3
        municipio = normalizar(busqueda.get("municipio", "")
                               or busqueda.get("localidad", ""))
        if municipio and normalizar(fila.get("municipio", "")) == municipio:
            puntos += 2
        if busqueda.get("apellido2") and mismo_apellido(
                fila["persona"].get("apellido2", ""), busqueda["apellido2"]):
            puntos += 2
        return puntos

    candidatas.sort(key=lambda f: (-_puntos(f), abs((f.get("anio") or 0) - centro),
                                   f.get("id") or 0))
    elegida = candidatas[0]
    puntos = _puntos(elegida)
    confianza = (CONF_ALTA if puntos >= 3 else
                 CONF_MEDIA if puntos >= 2 else CONF_BAJA)
    alternativas = [{"id": f.get("id"), "fecha": f.get("fecha"),
                     "persona": f["persona"]["completo"],
                     "localidad": f.get("localidad"), "url": f.get("url")}
                    for f in candidatas[1:6]]
    return elegida, confianza, alternativas


# ============================== ESTADO =====================================

def estado_vacio(rama: str = "alava") -> dict:
    return {"rama": rama, "actualizado": datetime.now().isoformat(timespec="seconds"),
            "consultas": 0, "personas": {}, "cola": [], "parejas": {},
            "log": []}


def cargar_estado(base: Path | None = None, rama: str = "alava") -> dict:
    base = base if base is not None else BASE_DIR
    ruta = base / LINAJE_VENTANA
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return estado_vacio(rama)
    if not isinstance(datos, dict) or "personas" not in datos:
        return estado_vacio(rama)
    datos.setdefault("cola", [])
    datos.setdefault("parejas", {})
    datos.setdefault("log", [])
    datos.setdefault("consultas", 0)
    return datos


def guardar_estado(estado: dict, base: Path | None = None) -> str | None:
    """Guarda el estado (con `.bak` de la versión anterior)."""
    base = base if base is not None else BASE_DIR
    estado["actualizado"] = datetime.now().isoformat(timespec="seconds")
    return escribir_con_backup(base / LINAJE_VENTANA,
                               json.dumps(estado, ensure_ascii=False, indent=1))


def anotar(estado: dict, texto: str, avisar=None) -> None:
    linea = f"[{datetime.now():%H:%M:%S}] {texto}"
    estado.setdefault("log", []).append(linea)
    if avisar is not None:
        avisar(texto)


def _clave_persona(estado: dict, nombre: str, apellido1: str,
                   anio=None) -> str:
    """Clave de una persona, reutilizando la que ya exista si es la misma.

    Un mismo antepasado se descubre dos veces (como progenitor de un hijo y
    como bautizado en su propia partida) y no puede acabar duplicado.
    """
    base = clave_de(nombre, apellido1)
    for clave, persona in estado["personas"].items():
        if persona["nombre"].lower() != (nombre or "").lower():
            continue
        if not mismo_apellido(persona.get("apellido1", ""), apellido1 or ""):
            continue
        if anio and persona.get("anio") and abs(persona["anio"] - anio) > 15:
            continue          # mismo nombre pero otra época: son dos personas
        return clave
    return clave_de(nombre, apellido1, anio)


def nueva_persona(nombre: str, apellido1: str, apellido2: str = "",
                  anio=None, anio_estimado: bool = True, ventana=None,
                  generacion: int = 0, rol: str = ROL_LINEA,
                  desde: str = "", notas: str = "",
                  anio_estimado_padre=None, municipio: str = "",
                  parroquia: str = "") -> dict:
    return {"nombre": nombre, "apellido1": apellido1, "apellido2": apellido2,
            "anio": anio, "anio_estimado": anio_estimado,
            "anio_estimado_padre": anio_estimado_padre,
            "ventana": list(ventana or (None, None)),
            "parroquia": parroquia, "municipio": municipio,
            "generacion": generacion,
            "rol": rol, "estado": EST_PENDIENTE, "desde": desde,
            "id": None, "fecha": "", "cita": "", "url": "",
            "confianza": "", "padres": [], "alternativas": [], "notas": notas}


def encolar(estado: dict, persona: dict) -> str:
    clave = _clave_persona(estado, persona["nombre"], persona["apellido1"],
                           persona.get("anio"))
    if clave in estado["personas"]:
        # Si ya estaba pendiente, se conserva lo que se sabía; si la nueva
        # información es más concreta (ventana, parroquia), se completa.
        previa = estado["personas"][clave]
        if not previa.get("parroquia") and persona.get("parroquia"):
            previa["parroquia"] = persona["parroquia"]
        if not previa.get("municipio") and persona.get("municipio"):
            previa["municipio"] = persona["municipio"]
        if previa["estado"] != EST_PENDIENTE:
            return clave
        return clave
    estado["personas"][clave] = persona
    if persona["estado"] == EST_PENDIENTE:
        estado["cola"].append(clave)
    return clave


def _enlazar_pareja(estado: dict, padres: list[dict], clave_hijo: str) -> None:
    """Apunta qué hijo de la pareja es de la LÍNEA (para poder decir después
    'hermano/a de <él>')."""
    if len(padres) < 2:
        return
    clave_pareja = _clave_pareja(padres[0]["nombre"], padres[0]["apellido1"],
                                 padres[1]["nombre"], padres[1]["apellido1"])
    pareja = estado["parejas"].setdefault(
        clave_pareja, {"padre": "", "madre": "", "hijo_linea": "",
                       "hijos": []})
    for p in padres:
        pareja[p["rol"]] = p["completo"]
        # Se guardan también las piezas, para reconocer la MISMA pareja en
        # otras partidas aunque el cura escriba el nombre de otra manera
        # ('Eusevio'/'Eusebio', 'Navarrete'/'Saenz de Navarrete').
        pareja[f"{p['rol']}_nombre"] = p["nombre"]
        pareja[f"{p['rol']}_apellido1"] = p["apellido1"]
    if clave_hijo not in pareja["hijos"]:
        pareja["hijos"].append(clave_hijo)
    if not pareja["hijo_linea"]:
        pareja["hijo_linea"] = clave_hijo


def _mismos_padres(pareja: dict, fila: dict) -> bool:
    """¿Los padres de esta partida son los de esa pareja ya conocida?"""
    for rol in ("padre", "madre"):
        nombre = pareja.get(f"{rol}_nombre", "")
        apellido = pareja.get(f"{rol}_apellido1", "")
        suyo = fila.get(rol) or {}
        if not nombre and not suyo.get("nombre"):
            continue
        if not nombre or not suyo.get("nombre"):
            return False
        if not mismo_nombre(nombre, suyo.get("nombre", "")):
            return False
        if not mismo_apellido(apellido, suyo.get("apellido1", "")):
            return False
    return True


def _pareja_de_fila(estado: dict, fila: dict) -> dict:
    """La pareja YA REGISTRADA que corresponde a los padres de esta partida.

    Se compara con tolerancia a propósito: si no, un 'Eusevio' o un 'Navarrete'
    suelto hacía que un hermano se quedara como 'de otra unión'.
    """
    for pareja in estado["parejas"].values():
        if _mismos_padres(pareja, fila):
            return pareja
    return {}


def _clave_pareja(nombre_p: str, ape_p: str, nombre_m: str, ape_m: str) -> str:
    """Clave de una pareja, en orden alfabético y sin depender de quién sea el
    padre y quién la madre (así la partida del hijo y los padres que declara
    dan la MISMA clave)."""
    return "||".join(sorted((clave_de(nombre_p, ape_p),
                             clave_de(nombre_m, ape_m))))


def _clave_pareja_de_fila(fila: dict) -> str:
    padre = (fila.get("padre") or {}).get("nombre", "")
    ape_p = (fila.get("padre") or {}).get("apellido1", "")
    madre = (fila.get("madre") or {}).get("nombre", "")
    ape_m = (fila.get("madre") or {}).get("apellido1", "")
    if not madre:
        return ""
    return _clave_pareja(padre, ape_p, madre, ape_m)


def _apuntar_colateral(estado: dict, fila: dict, generacion: int,
                       desde: str, nota: str) -> str:
    """Apunta un pariente colateral (hermano, tío, primo) con su partida."""
    suyo = fila["persona"]
    clave = encolar(estado, nueva_persona(
        nombre=suyo.get("nombre", ""), apellido1=suyo.get("apellido1", ""),
        apellido2=suyo.get("apellido2", ""), anio=fila.get("anio"),
        anio_estimado=False, generacion=generacion, rol=ROL_COLATERAL,
        desde=desde, notas=nota))
    persona = estado["personas"][clave]
    persona.update({"estado": EST_IDENTIFICADO, "id": fila.get("id"),
                    "fecha": fila.get("fecha", ""),
                    "parroquia": fila.get("parroquia", ""),
                    "municipio": fila.get("localidad", ""),
                    "url": fila.get("url", ""), "confianza": CONF_ALTA,
                    "cita": _cita(fila)})
    if clave in estado["cola"]:
        estado["cola"].remove(clave)      # un colateral no se rastrea
    return clave


def _cita(fila: dict) -> str:
    trozos = []
    if fila.get("fondo"):
        trozos.append(f"fondo {fila['fondo']}")
    if fila.get("signatura"):
        trozos.append(f"sig. {fila['signatura']}")
    if fila.get("folio"):
        trozos.append(f"folio {fila['folio']}")
    return ", ".join(trozos)


# ============================== EL RASTREO =================================

def rastrear(semillas: list[dict], buscar, estado: dict | None = None,
             max_consultas: int = LINAJE_MAX_CONSULTAS,
             max_generaciones: int = LINAJE_MAX_GENERACIONES,
             pausa: tuple = LINAJE_DELAY, avisar=None,
             tope_anio: int = LINAJE_MIN_ANIO) -> dict:
    """Tira del hilo hacia arriba hasta que no se pueda más.

    ``buscar(nombre, apellido1, apellido2, anio_ini, anio_fin)`` devuelve filas
    normalizadas del índice (scrapers.artxibo.buscar_sacramentales). ``pausa``
    es la cortesía entre consultas al portal (todas son gratis).
    """
    estado = estado if estado is not None else estado_vacio()
    for semilla in semillas:
        encolar(estado, semilla)
    consultas_antes = estado["consultas"]

    while estado["cola"]:
        if estado["consultas"] - consultas_antes >= max_consultas:
            anotar(estado, f"tope de {max_consultas} consultas por tanda: "
                           f"quedan {len(estado['cola'])} pendientes "
                           f"(se siguen en la próxima pasada)", avisar)
            break
        clave = estado["cola"].pop(0)
        persona = estado["personas"][clave]
        if persona["estado"] != EST_PENDIENTE:
            continue
        if persona["generacion"] > max_generaciones:
            persona["estado"] = EST_SIN_RESULTADO
            persona["notas"] = f"tope de {max_generaciones} generaciones"
            continue
        if not persona["nombre"] or not persona["apellido1"]:
            persona["estado"] = EST_SIN_DATOS
            persona["notas"] = "la partida no daba su nombre o su apellido"
            continue
        ini, fin = persona["ventana"]
        if not ini or not fin:
            persona["estado"] = EST_SIN_DATOS
            persona["notas"] = "sin año con el que calcular la ventana"
            continue
        if fin < tope_anio:
            persona["estado"] = EST_SIN_RESULTADO
            persona["notas"] = (f"su ventana ({ini}-{fin}) es anterior a "
                                f"{tope_anio}: el índice ya casi no da padres")
            continue

        etiqueta = (f"g{persona['generacion']} {persona['nombre']} "
                    f"{persona['apellido1']} ({ini}-{fin})")
        try:
            filas = buscar(nombre=persona["nombre"],
                           apellido1=persona["apellido1"],
                           apellido2=persona["apellido2"],
                           anio_ini=ini, anio_fin=fin)
        except Exception as e:            # noqa: BLE001 (fallo de red/portal)
            anotar(estado, f"{etiqueta}: FALLO del buscador "
                           f"({str(e)[:80]}) — se reintentará", avisar)
            persona["notas"] = "fallo del buscador (se reintentará)"
            estado["cola"].insert(0, clave)   # vuelve a la cola, sin gastar
            break
        estado["consultas"] += 1
        if pausa:
            time.sleep(random.uniform(*pausa))

        elegido, confianza, alternativas = elegir_candidato(filas, persona)
        persona["alternativas"] = alternativas
        if elegido is None:
            persona["estado"] = EST_SIN_RESULTADO
            persona["notas"] = f"sin candidatos en la ventana {ini}-{fin}"
            anotar(estado, f"{etiqueta}: sin resultados", avisar)
        else:
            # --- identificado: se rellena con su propia partida ---
            # (y se completa el segundo apellido, que casi nunca viene en la
            #  partida del hijo: ahí es donde aparece 'Tellaeche', 'Guzman'...)
            suyo = elegido["persona"]
            persona.update({"estado": EST_IDENTIFICADO, "id": elegido.get("id"),
                            "fecha": elegido.get("fecha", ""),
                            "anio": elegido.get("anio"),
                            "anio_estimado": False,
                            "nombre": suyo.get("nombre") or persona["nombre"],
                            "apellido1": suyo.get("apellido1")
                                         or persona["apellido1"],
                            "apellido2": suyo.get("apellido2")
                                         or persona["apellido2"],
                            "parroquia": elegido.get("parroquia", ""),
                            "municipio": elegido.get("localidad", ""),
                            "url": elegido.get("url", ""),
                            "cita": _cita(elegido), "confianza": confianza})
            anotar(estado, f"{etiqueta}: {elegido.get('fecha')} · "
                           f"{elegido['persona']['completo']} · "
                           f"{elegido.get('parroquia')}, "
                           f"{elegido.get('localidad')} [{confianza}]", avisar)

            # --- sus padres (generación siguiente) y la pareja ---
            padres = padres_de(elegido)
            claves_padres = []
            for padre in padres:
                clave_padre = encolar(estado, nueva_persona(
                    nombre=padre["nombre"], apellido1=padre["apellido1"],
                    apellido2=padre["apellido2"],
                    anio=None, anio_estimado=True,
                    anio_estimado_padre=anio_estimado_de_progenitor(
                        persona["anio"] or 0),
                    ventana=ventana_padres(persona["anio"] or 0),
                    # La parroquia del hijo es la pista de dónde bautizaron al
                    # padre (la familia vive ahí). Es solo para PUNTUAR: si el
                    # candidato está en otro pueblo, sale igual pero marcado.
                    parroquia=persona.get("parroquia", ""),
                    generacion=persona["generacion"] + 1, rol=ROL_LINEA,
                    desde=clave,
                    notas=f"{padre['rol']} de {persona['nombre']} "
                          f"({persona['anio'] or '¿?'})"))
                claves_padres.append(clave_padre)
            persona["padres"] = claves_padres
            if padres:
                _enlazar_pareja(estado, padres, clave)

        # --- 2. SUS HIJOS: la partida de un hijo lleva la fecha del hijo, así
        #     que hay que buscar en la ventana (año+18, año+50). Con eso se sabe
        #     quién es hermano de quién y salen los parientes colaterales.
        #     Si su propio bautismo no apareció, se usa el año ESTIMADO (hijo
        #     conocido − 28): así salen igualmente los hermanos de la línea. ---
        anio_ref = (persona["anio"] or persona.get("anio_estimado_padre")
                    or (elegido.get("anio") if elegido else None))
        if not anio_ref:
            continue
        h_ini, h_fin = ventana_hijos(anio_ref)
        etiqueta_hijos = (f"g{persona['generacion']} hijos de "
                          f"{persona['nombre']} {persona['apellido1']} "
                          f"({h_ini}-{h_fin})")
        try:
            filas_hijos = buscar(nombre=persona["nombre"],
                                 apellido1=persona["apellido1"],
                                 apellido2=persona["apellido2"],
                                 anio_ini=h_ini, anio_fin=h_fin)
        except Exception as e:            # noqa: BLE001
            anotar(estado, f"{etiqueta_hijos}: FALLO del buscador "
                           f"({str(e)[:80]})", avisar)
            continue
        estado["consultas"] += 1
        if pausa:
            time.sleep(random.uniform(*pausa))
        hijos = [f for f in filas_hijos
                 if es_hijo_suyo(f, persona["nombre"], persona["apellido1"],
                                 "padre")
                 or es_hijo_suyo(f, persona["nombre"], persona["apellido1"],
                                 "madre")]
        if hijos:
            anotar(estado, f"{etiqueta_hijos}: {len(hijos)} partida(s) de "
                           f"hijos suyos", avisar)
        if hijos and persona["estado"] != EST_IDENTIFICADO:
            persona["estado"] = EST_DUDOSO
            persona["notas"] = (f"no aparece su propio bautismo en la ventana "
                                f"{ini}-{fin}, pero SÍ figura como progenitor "
                                f"en {len(hijos)} partida(s)")
        for hijo in hijos[:30]:
            pareja = _pareja_de_fila(estado, hijo)
            linea = pareja.get("hijo_linea")
            clave_hijo = clave_de(hijo["persona"].get("nombre", ""),
                                  hijo["persona"].get("apellido1", ""))
            # la clave del hijo de la línea puede llevar el año: se compara la
            # parte del nombre para no confundirlo con un hermano
            es_hijo_de_la_linea = bool(linea) and clave_hijo == linea.split(
                "::")[0]
            if linea and not es_hijo_de_la_linea and linea in estado["personas"]:
                referencia = estado["personas"][linea]
                nota = (f"hermano/a de {referencia['nombre']} "
                        f"{referencia['apellido1']}")
            elif estado["parejas"] and not linea:
                nota = (f"hijo/a de {persona['nombre']} "
                        f"{persona['apellido1']} (con otra pareja, o es un "
                        f"homiónimo: el índice no lo distingue)")
            else:
                nota = f"hijo/a de {persona['nombre']} {persona['apellido1']}"
            _apuntar_colateral(estado, hijo, persona["generacion"] - 1,
                               clave, nota)
    return estado


# ============================== INFORME ====================================

def linea_ascendente(estado: dict) -> dict:
    """Por generación, las personas de la LÍNEA con su partida."""
    por_generacion: dict[int, list] = {}
    for clave, persona in estado["personas"].items():
        if persona["rol"] != ROL_LINEA:
            continue
        por_generacion.setdefault(persona["generacion"], []).append((clave,
                                                                     persona))
    return {g: sorted(v, key=lambda par: (par[1]["estado"] != EST_IDENTIFICADO,
                                          par[1]["nombre"]))
            for g, v in sorted(por_generacion.items())}


def colaterales(estado: dict) -> list[dict]:
    return [p for p in estado["personas"].values()
            if p["rol"] == ROL_COLATERAL]


def eslabones_que_faltan(estado: dict) -> list[dict]:
    """Lo que no se ha podido cerrar: es lo que merece una copia del archivo."""
    return [p for p in estado["personas"].values()
            if p["rol"] == ROL_LINEA
            and p["estado"] in (EST_SIN_RESULTADO, EST_DUDOSO, EST_SIN_DATOS)]


def resumen(estado: dict) -> dict:
    personas = estado["personas"]
    linea = [p for p in personas.values() if p["rol"] == ROL_LINEA]
    identificados = [p for p in linea if p["estado"] == EST_IDENTIFICADO]
    anios = [p["anio"] for p in identificados if p.get("anio")]
    return {"personas": len(personas), "linea": len(linea),
            "identificados": len(identificados),
            "colaterales": len(colaterales(estado)),
            "faltan": len(eslabones_que_faltan(estado)),
            "pendientes": len(estado["cola"]), "consultas": estado["consultas"],
            "generacion_max": max((p["generacion"] for p in linea), default=0),
            "anio_min": min(anios) if anios else None,
            "anio_max": max(anios) if anios else None}


def redactar_markdown(estado: dict) -> str:
    datos = resumen(estado)
    lineas = ["# Linaje ascendente — línea de Álava (Sáenz de Navarrete)", "",
              f"Generado: {datetime.now():%Y-%m-%d %H:%M} · "
              f"{datos['consultas']} consultas al índice del AHDV (gratis)", "",
              "Este fichero lo ha construido el bot tirando del hilo partida a "
              "partida: de cada persona saca los padres que **dice su propia "
              "partida** y sigue hacia arriba, por la línea del padre y por la "
              "de la madre. **Nada está escrito en el árbol**: aquí está la "
              "lista para que la revises.", "",
              "| Qué | Cuánto |", "|---|---|",
              f"| Antepasados directos localizados | {datos['identificados']} |",
              f"| Generaciones hacia arriba | {datos['generacion_max']} |",
              f"| Años cubiertos | "
              f"{datos['anio_min'] or '?'} - {datos['anio_max'] or '?'} |",
              f"| Parientes colaterales (hermanos, tíos, primos) | "
              f"{datos['colaterales']} |",
              f"| Eslabones que faltan (candidatos a copia) | {datos['faltan']} |",
              f"| Pendientes para la próxima pasada | {datos['pendientes']} |",
              ""]

    lineas += ["## La línea, generación a generación", ""]
    for generacion, gente in linea_ascendente(estado).items():
        lineas.append(f"### Generación {generacion} "
                      f"({len(gente)} persona(s))")
        for clave, persona in gente:
            if persona["estado"] == EST_IDENTIFICADO:
                marca = (f"**{persona['nombre']} {persona['apellido1']}** "
                         f"{persona['apellido2']}".strip())
                lineas.append(f"- {marca} · {persona['fecha']} · "
                              f"{persona['parroquia']}, {persona['municipio']} · "
                              f"confianza {persona['confianza']}")
                lineas.append(f"  - Cita: {persona['cita'] or '—'} · "
                              f"{persona['url']}")
                if persona["notas"]:
                    lineas.append(f"  - Cómo se llegó: {persona['notas']}")
                if persona["alternativas"]:
                    otras = "; ".join(
                        f"{a['fecha']} {a['persona']} ({a['localidad']})"
                        for a in persona["alternativas"])
                    lineas.append(f"  - Otras posibilidades descartadas: {otras}")
            else:
                lineas.append(f"- {persona['nombre']} {persona['apellido1']} "
                              f"· ventana {persona['ventana'][0]}-"
                              f"{persona['ventana'][1]} · "
                              f"**{persona['estado']}**"
                              + (f" — {persona['notas']}" if persona["notas"]
                                 else ""))
        lineas.append("")

    laterales = colaterales(estado)
    if laterales:
        lineas += [f"## Posibles parientes ({len(laterales)})", "",
                   "Hermanos, tíos y primos que han aparecido al tirar del "
                   "hilo. No suben la línea, pero son familia y muchos tienen "
                   "su partida indexada (y su enlace).", "",
                   "| Fecha | Persona | Parroquia | Relación | Enlace |",
                   "|---|---|---|---|---|"]
        for persona in sorted(laterales, key=lambda p: (p["generacion"],
                                                        p.get("anio") or 0)):
            if not persona.get("url"):
                continue
            lineas.append(f"| {persona.get('fecha') or '—'} | "
                          f"{persona['nombre']} {persona['apellido1']} "
                          f"{persona['apellido2']} | {persona.get('parroquia')} | "
                          f"{persona['notas']} | [ficha]({persona['url']}) |")
        lineas.append("")

    faltan = eslabones_que_faltan(estado)
    if faltan:
        lineas += [f"## Eslabones que faltan ({len(faltan)}) — lo que merece "
                   f"una copia del archivo", "",
                   "Aquí el índice ya no llega: o la partida no está, o no dice "
                   "los padres. **Esta es la lista que vale dinero**: pidiendo "
                   "la copia literal de estas partidas se cierra el siguiente "
                   "escalón.", ""]
        for persona in sorted(faltan, key=lambda p: p["generacion"]):
            lineas.append(f"- G{persona['generacion']} · **{persona['nombre']} "
                          f"{persona['apellido1']}** · ventana "
                          f"{persona['ventana'][0]}-{persona['ventana'][1]} · "
                          f"{persona['estado']}: {persona['notas']}")
        lineas.append("")

    if estado["cola"]:
        lineas += [f"## Pendiente para la próxima pasada ({len(estado['cola'])})",
                   ""]
        for clave in estado["cola"][:40]:
            persona = estado["personas"].get(clave)
            if persona:
                lineas.append(f"- G{persona['generacion']} · {persona['nombre']} "
                              f"{persona['apellido1']} · ventana "
                              f"{persona['ventana'][0]}-{persona['ventana'][1]}")
        lineas.append("")
    return "\n".join(lineas)


def escribir_informe(estado: dict, base: Path | None = None) -> Path:
    base = base if base is not None else BASE_DIR
    ruta = base / LINAJE_INFORME
    escribir_con_backup(ruta, redactar_markdown(estado))
    return ruta
