"""
scrapers/archivos_provinciales.py — PARTES B y C (v9.1).

PARTE B — Catastro de Ensenada, RESPUESTAS PARTICULARES:
  Dónde están de verdad: en los Archivos Históricos Provinciales (AHP).
  PARES solo publica las Respuestas GENERALES; las Particulares (el
  censo nominal casa por casa: vecinos, edad, hijos, oficio, tierras)
  NO están digitalizadas en ningún portal público estable para nuestras
  tres provincias. Verificación en vivo (2026-09-11) del portal de
  Archivos de Castilla y León: SIEGA permite consulta en línea de
  algunos fondos, pero las Respuestas Particulares de Zamora/Palencia
  no están entre ellos: se piden al AHP.

  CORRECCIÓN FACTUAL incluida (importante): Álava quedó EXCLUIDA del
  Catastro de Ensenada (régimen foral: se rigió por sus propios
  catastros municipales). NO existen Respuestas Particulares para
  Vitoria. Las alternativas reales son: IRARGI (artxibo.euskadi.eus,
  registros sacramentales indexados SIN login), protocolos notariales
  del AHP de Álava (>100 años, web.araba.eus) y padrones municipales
  de Vitoria.

PARTE C — "archivos_a_consultar_in_situ":
  Genera (automáticamente, desde familia_conocida.json) la sección del
  informe_fase1.json con el AHP y el Archivo Municipal que corresponde
  a CADA municipio de la familia, con enlaces a catálogos online DONDE
  EXISTEN (verificados) y la marca "requiere visita o solicitud postal"
  donde no. Para capitulaciones matrimoniales, testamentos,
  vecindarios/censos y quintas — los fondos notariales y municipales
  que los buscadores nominales NO cubren.

Datos verificados en vivo (2026-09-11, curl directo al portal CyL):
  - Portal: https://archivoscastillayleon.jcyl.es (red de Archivos de
    Castilla y León; AHP Zamora y AHP Palencia dentro).
  - AHP Zamora: C/ Rúa de los Francos; atención mañanas L-V 9-14.
    https://archivoscastillayleon.jcyl.es/web/es/nuestros-archivos/
    archivo-historico-provincial-zamora.html
  - AHP Palencia: C/ Niños del Coro; atención mañanas L-V 9-14.
    https://archivoscastillayleon.jcyl.es/web/es/nuestros-archivos/
    archivo-historico-provincial-palencia.html
  - SIEGA (catálogo en línea de CyL):
    https://archivoscastillayleon.jcyl.es/web/es/servicios-ofrecemos/
    catalogo-linea-siega.html
  - AHP Álava: Paseo de la Florida 9, 01071 Vitoria; tel. 945 18 19 27;
    archivo@araba.eus (verificado en censo-guía y euskadi.eus).
    https://web.araba.eus/es/cultura/archivos-y-patrimonio-documental/
  - IRARGI: https://artxibo.euskadi.eus/irargi/consultar-sacramentales

NADA FALLA EN SILENCIO: cada entrada lleva "estado" con lo que se puede
hacer YA (catálogo online / solicitud por escrito / visita o postal).
"""

from __future__ import annotations

from config import (AHP_ALAVA_URL, AHP_PALENCIA_URL, AHP_ZAMORA_URL,
                    ARCHIVOS_CYL_URL, IRARGI_URL, MUNICIPIOS_EQUIVALENTES,
                    PROVINCIAS_SIN_ENSENADA, SALIDA_SOLICITUDES,
                    SALIDA_SOLICITUDES_MD, normalizar)
from utils import ui

# ============================== CATÁLOGO POR PROVINCIA ======================

ARCHIVOS_PROVINCIA = {
    "zamora": {
        "archivo": "Archivo Histórico Provincial de Zamora",
        "direccion": "C/ Rúa de los Francos, Zamora (atención L-V 9-14)",
        "portal": AHP_ZAMORA_URL,
        "catalogo_online": "https://archivoscastillayleon.jcyl.es/web/es/"
                           "servicios-ofrecemos/catalogo-linea-siega.html",
        "catalogo_online_nota": ("SIEGA consulta en línea: fondos "
                                 "parciales del AHPZ; las Respuestas "
                                 "Particulares NO están en línea"),
        "municipal": "Archivo Municipal de {municipio} (padrones, "
                     "vecindarios, quintas locales)",
        "ensenada_particulares": {
            "estado": "requiere_solicitud_escrita",
            "detalle": ("Las Respuestas Particulares del Catastro de "
                        "Ensenada de los pueblos de Zamora se conservan en "
                        "el AHPZ y se solicitan por escrito (sistema "
                        "--solicitudes) o se consultan en sala."),
        },
    },
    "palencia": {
        "archivo": "Archivo Histórico Provincial de Palencia",
        "direccion": "C/ Niños del Coro, Palencia (atención L-V 9-14)",
        "portal": AHP_PALENCIA_URL,
        "catalogo_online": "https://archivoscastillayleon.jcyl.es/web/es/"
                           "servicios-ofrecemos/catalogo-linea-siega.html",
        "catalogo_online_nota": ("SIEGA consulta en línea: fondos "
                                 "parciales del AHPP; las Respuestas "
                                 "Particulares NO están en línea"),
        "municipal": "Archivo Municipal de {municipio} (padrones, "
                     "vecindarios, quintas locales)",
        "ensenada_particulares": {
            "estado": "requiere_solicitud_escrita",
            "detalle": ("Las Respuestas Particulares del Catastro de "
                        "Ensenada de los pueblos de Palencia se conservan "
                        "en el AHPP y se solicitan por escrito (sistema "
                        "--solicitudes) o se consultan en sala."),
        },
    },
    "alava": {
        "archivo": "Archivo Histórico Provincial de Álava",
        "direccion": "Paseo de la Florida 9, 01071 Vitoria-Gasteiz · "
                     "tel. 945 18 19 27 · archivo@araba.eus",
        "portal": AHP_ALAVA_URL,
        "catalogo_online": IRARGI_URL,
        "catalogo_online_nota": ("IRARGI (artxibo.euskadi.eus): registros "
                                 "sacramentales indexados SIN login; los "
                                 "protocolos notariales >100 años se "
                                 "consultan en el AHP o por email"),
        "municipal": "Archivo Municipal de {municipio} (padrones "
                     "históricos: Vitoria conserva series del s. XIX)",
        # CORRECCIÓN FACTUAL: Álava NO está en el Ensenada.
        "ensenada_particulares": {
            "estado": "no_existe",
            "detalle": ("Álava quedó EXCLUIDA del Catastro de Ensenada "
                        "(régimen foral): NO existen Respuestas "
                        "Particulares para Vitoria. Alternativas reales: "
                        "IRARGI (sacramentales), protocolos notariales del "
                        "AHP Álava (>100 años) y padrones municipales."),
        },
    },
    # alias euskera
    "araba": None,   # se resuelve a alava en _datos_provincia()
}

# Qué buscar en cada archivo (los fondos que los buscadores nominales
# NO cubren): la lista exacta de tipos documentales de la PARTE C.
FONDOS_AHP = ["protocolos notariales (capitulaciones matrimoniales, "
              "testamentos)",
              "Respuestas Particulares del Catastro de Ensenada "
              "(donde existan)",
              "vecindarios y censos históricos"]
FONDOS_MUNICIPALES = ["padrones municipales",
                      "quintas y alistamientos",
                      "expedientes personales"]


def _datos_provincia(provincia: str) -> dict | None:
    prov = normalizar(provincia or "")
    if prov in ("alava", "araba"):
        return ARCHIVOS_PROVINCIA["alava"]
    return ARCHIVOS_PROVINCIA.get(prov)


# ============================== PARTE C =====================================

def generar_archivos_a_consultar_in_situ(familia: dict) -> list[dict]:
    """Sección "archivos_a_consultar_in_situ" de informe_fase1.json.

    Para cada municipio de familia_conocida.json: el AHP que le
    corresponde y su Archivo Municipal, con enlaces a catálogos online
    VERIFICADOS donde existen, y la marca 'requiere visita o solicitud
    postal' donde no hay catálogo. Ordenada por provincia y municipio;
    sin municipios repetidos."""
    vistos: set[str] = set()
    entradas: list[dict] = []
    for p in (familia or {}).get("personas", []):
        for evento in ("nacimiento", "defuncion"):
            ev = p.get(evento) or {}
            mun = (ev.get("municipio") or "").strip()
            prov = (ev.get("provincia") or "").strip()
            if not mun or not prov:
                continue
            clave = normalizar(f"{mun}|{prov}")
            if clave in vistos:
                continue
            vistos.add(clave)
            datos = _datos_provincia(prov)
            alias = MUNICIPIOS_EQUIVALENTES.get(normalizar(mun))
            if datos is None:
                entradas.append({
                    "municipio": mun, "provincia": prov,
                    "estado": "provincia_sin_catalogo_definido",
                    "detalle": ("define ARCHIVOS_PROVINCIA en "
                                "scrapers/archivos_provinciales.py para "
                                "esta provincia; entretanto, requiere "
                                "visita o solicitud postal"),
                })
                continue
            ens = datos["ensenada_particulares"]
            entradas.append({
                "municipio": mun,
                "provincia": prov,
                "municipio_alias": alias or "",
                "ahp": {
                    "archivo": datos["archivo"],
                    "direccion": datos["direccion"],
                    "portal": datos["portal"],
                    "catalogo_online": datos["catalogo_online"],
                    "nota_catalogo": datos["catalogo_online_nota"],
                    "fondos_a_buscar": FONDOS_AHP,
                },
                "archivo_municipal": {
                    "descripcion": datos["municipal"].format(municipio=mun),
                    "fondos_a_buscar": FONDOS_MUNICIPALES,
                    "catalogo_online": "",
                    "estado": ("requiere visita o solicitud postal "
                                "(sin catálogo online conocido)"),
                },
                "catastro_ensenada_particulares": {
                    "estado": ens["estado"],
                    "detalle": ens["detalle"],
                },
                "estado": "catálogo online parcial (AHP); municipal "
                          "requiere visita o solicitud postal",
            })
    entradas.sort(key=lambda e: (normalizar(e.get("provincia", "")),
                                 normalizar(e.get("municipio", ""))))
    return entradas


# ============================== PARTE B =====================================

def estado_ensenada_particulares(provincia: str) -> dict:
    """PARTE B para una provincia: dónde están sus Respuestas
    Particulares y en qué estado de accesibilidad."""
    datos = _datos_provincia(provincia)
    if datos is None:
        return {"estado": "sin_datos",
                "detalle": "provincia sin catálogo definido"}
    return dict(datos["ensenada_particulares"])


def generar_solicitudes_ensenada(familia: dict) -> list[dict]:
    """Solicitudes por escrito de Respuestas Particulares para los
    municipios de la familia (se integrarán en el sistema --solicitudes
    existente: solicitudes.json + solicitudes.md).

    SOLO para provincias con estado 'requiere_solicitud_escrita': para
    Álava no se genera nada (no existen: excluida del Catastro), y eso
    queda explícito en el detalle de la entrada del informe, no como
    una solicitud que fallaría."""
    solicitudes = []
    for entrada in generar_archivos_a_consultar_in_situ(familia):
        ens = entrada.get("catastro_ensenada_particulares") or {}
        if ens.get("estado") != "requiere_solicitud_escrita":
            continue
        datos = _datos_provincia(entrada["provincia"])
        solicitudes.append({
            "tipo": "catastro_ensenada_respuestas_particulares",
            "municipio": entrada["municipio"],
            "provincia": entrada["provincia"],
            "archivo": datos["archivo"],
            "contacto": (f"{datos['portal']} · {datos['direccion']}"),
            "peticion": (f"Solicitud de consulta o copia de las Respuestas "
                         f"Particulares del Catastro de Ensenada "
                         f"(1749-1756) de {entrada['municipio']} "
                         f"({entrada['provincia']}): relación nominal de "
                         f"vecinos con edad, familia, oficio y bienes."),
            "motivo": "investigación genealógica de descendientes",
        })
    return solicitudes


def integrar_solicitudes_ensenada(familia: dict) -> int:
    """AÑADE las solicitudes de Ensenada a solicitudes.json (y las
    describe en solicitudes.md) reutilizando el sistema --solicitudes.
    Devuelve cuántas añadió. Idempotente: no repite municipios ya
    pedidos."""
    import json
    from pathlib import Path
    from config import BASE_DIR

    nuevas = generar_solicitudes_ensenada(familia)
    if not nuevas:
        return 0
    ruta = Path(BASE_DIR) / SALIDA_SOLICITUDES
    existentes: list[dict] = []
    if ruta.exists():
        try:
            existentes = json.loads(ruta.read_text(encoding="utf-8"))
            if not isinstance(existentes, list):
                existentes = []
        except (json.JSONDecodeError, OSError):
            existentes = []
    ya = {(normalizar(s.get("municipio", "")),
           s.get("tipo", "")) for s in existentes}
    anadir = [s for s in nuevas
              if (normalizar(s["municipio"]), s["tipo"]) not in ya]
    if not anadir:
        return 0
    existentes.extend(anadir)
    ruta.write_text(json.dumps(existentes, ensure_ascii=False, indent=2),
                    encoding="utf-8")

    # parte .md (append: las partidas sacramentales las escribe
    # generar_solicitudes de agent/gedcom.py; aquí solo se añaden las
    # peticiones de catastro)
    lineas = ["", "## Catastro de Ensenada — Respuestas Particulares", ""]
    for s in anadir:
        lineas += [f"### {s['municipio']} ({s['provincia']})",
                   f"- Archivo: {s['archivo']}",
                   f"- Contacto: {s['contacto']}",
                   f"- Petición: {s['peticion']}", ""]
    ruta_md = Path(BASE_DIR) / SALIDA_SOLICITUDES_MD
    if ruta_md.exists():
        with open(ruta_md, "a", encoding="utf-8") as f:
            f.write("\n".join(lineas))
    else:
        ruta_md.write_text("\n".join(lineas), encoding="utf-8")
    ui.log_ok(f"{len(anadir)} solicitudes de Respuestas Particulares "
              f"añadidas a {SALIDA_SOLICITUDES} (AHP, por escrito)")
    return len(anadir)
