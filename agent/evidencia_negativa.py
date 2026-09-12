"""
agent/evidencia_negativa.py — v10.4 (P3): registro de búsquedas infructuosas.

POR QUÉ EXISTE
El informe de metodología profesional lo pide expresamente: los genealogistas
"documentan cada paso, mantienen notas de investigación, registran búsquedas
infructuosas (evidencia negativa) y actualizan continuamente su árbol y sus
hipótesis". Nosotros solo teníamos cachés INVISIBLES (consultas ya hechas,
cooldowns de red de 6 h): nada legible que dijera "a Eusebio Merillas en
Salas ya se le buscó el 12/09 y no apareció nada". Sin eso:
  - el humano no sabe dónde ya se pinchó (ni dónde merece la pena escribir);
  - el bot no puede distinguir "no lo he buscado" de "lo busqué y no está",
    así que no puede decidir qué reabrir (M2) sin repetir lo de ayer.

QUÉ ES Y QUÉ NO ES (filosofía de fallo explícito)
  - ES: "se buscó ESTO, en ESTOS sitios, en ESTA fecha, y la fuente respondió
    sin nada relevante".
  - NO ES prueba de que el documento no exista, y por eso el texto del
    informe lo dice con esas palabras.
  - NUNCA registra una caída de red ni un cooldown como si fuera un "no
    existe": eso ya lo distingue el conector de Ensenada desde la v4.3 (una
    caída de PARES nunca deja una nota negativa) y aquí se mantiene el mismo
    criterio: el llamador solo registra cuando la consulta se ejecutó.
  - Append-only, como registro_confirmaciones.jsonl: SOLO crece, y cada línea
    lleva su fecha, porque un "no encontrado" de hace seis meses no vale lo
    mismo que el de anoche.

100% offline, sin coste (0 tokens, 0 llamadas de red).
"""

from __future__ import annotations

import json
from datetime import datetime

from config import BASE_DIR, EVIDENCIA_NEGATIVA, normalizar
from utils import ui

# Recortes defensivos: el registro es un histórico legible, no un vertedero.
MAX_ANCLA = 120
MAX_MOTIVO = 300


def registrar(ancla: str, tipo: str = "", municipio: str = "",
              provincia: str = "", consultas=0, motivo: str = "",
              fuente: str = "agente") -> dict:
    """Añade UNA línea al registro de evidencia negativa y la devuelve.

    Devuelve {} y no escribe nada si no hay ancla (sin nombre no hay nada que
    recordar). NUNCA lanza: si el disco falla, se avisa y la noche sigue —
    perder una línea de histórico no puede tumbar una investigación.
    """
    ancla = (ancla or "").strip()
    if not ancla:
        return {}
    entrada = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "ancla": ancla[:MAX_ANCLA],
        "tipo": (tipo or "").strip(),
        "municipio": (municipio or "").strip(),
        "provincia": (provincia or "").strip(),
        "consultas": int(consultas or 0),
        "motivo": (motivo or "").strip()[:MAX_MOTIVO],
        "fuente": (fuente or "agente").strip(),
    }
    try:
        with open(BASE_DIR / EVIDENCIA_NEGATIVA, "a", encoding="utf-8") as f:
            f.write(json.dumps(entrada, ensure_ascii=False) + "\n")
    except OSError as e:
        ui.log_warn(f"no se pudo escribir {EVIDENCIA_NEGATIVA}: "
                    f"{str(e)[:80]} (la investigación continúa)")
    return entrada


def cargar() -> list[dict]:
    """Todas las entradas del registro, en orden de escritura. Devuelve []
    si el fichero no existe y salta las líneas ilegibles: es un histórico
    para leer, no una fuente de verdad crítica."""
    ruta = BASE_DIR / EVIDENCIA_NEGATIVA
    if not ruta.exists():
        return []
    try:
        contenido = ruta.read_text(encoding="utf-8")
    except OSError as e:
        ui.log_warn(f"{EVIDENCIA_NEGATIVA} ilegible: {str(e)[:80]}")
        return []
    salida: list[dict] = []
    for linea in contenido.splitlines():
        linea = linea.strip()
        if not linea:
            continue
        try:
            datos = json.loads(linea)
        except json.JSONDecodeError:
            continue
        if isinstance(datos, dict) and datos.get("ancla"):
            salida.append(datos)
    return salida


def resumen_por_ancla() -> dict[str, dict]:
    """{ancla_normalizada: {ancla, n, ultima, municipio, motivo}}.

    Agrupa por persona para que el informe diga "a Fulano se le buscó 4 veces
    y la última fue el 12/09" en vez de listar 4 líneas idénticas."""
    resumen: dict[str, dict] = {}
    for e in cargar():
        clave = normalizar(e.get("ancla", ""))
        if not clave:
            continue
        d = resumen.setdefault(clave, {"ancla": e.get("ancla", ""), "n": 0,
                                       "ultima": "", "municipio": "",
                                       "motivo": ""})
        d["n"] += 1
        if (e.get("ts") or "") >= d["ultima"]:
            d["ultima"] = e.get("ts") or ""
            d["municipio"] = e.get("municipio") or ""
            d["motivo"] = e.get("motivo") or ""
    return resumen


def texto_markdown(limite: int = 15) -> str:
    """Sección de evidencia negativa para informe_progreso.md. Devuelve ''
    si todavía no hay nada registrado (así el informe no cambia en una
    instalación recién empezada). Lo más reciente primero."""
    resumen = resumen_por_ancla()
    if not resumen:
        return ""
    filas = sorted(resumen.values(), key=lambda d: d["ultima"], reverse=True)
    lineas = [
        "## Búsquedas sin resultado (evidencia negativa)",
        "",
        f"Lo que ya se buscó y no apareció ({len(filas)} personas). Sirve "
        f"para NO repetir lo de ayer y para decidir dónde escribir o ir en "
        f"persona. **No** es prueba de que el documento no exista: la web "
        f"no lo tiene (todavía). Fichero completo: `{EVIDENCIA_NEGATIVA}`.",
        "",
        "| Persona | Dónde | Intentos | Última vez | Nota |",
        "|---|---|---|---|---|",
    ]
    for d in filas[:limite]:
        lineas.append(
            f"| {d['ancla']} | {d['municipio'] or '—'} | {d['n']} | "
            f"{(d['ultima'] or '')[:16].replace('T', ' ')} | "
            f"{d['motivo'] or '—'} |")
    if len(filas) > limite:
        lineas.append(f"| … | | | | (+{len(filas) - limite} personas más) |")
    return "\n".join(lineas) + "\n"
