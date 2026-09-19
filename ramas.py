#!/usr/bin/env python3
"""
ramas.py — Una rama familiar, de una vez (v10.4.2, BLOQUE 3).

QUÉ HACE
    python ramas.py --rama alava     # LÍNEA PATERNA DE TU MADRE (Álava/Vitoria):
                                     # busca en artxibo, coteja con el árbol y
                                     # prepara las copias literales al AHDV
    python ramas.py --rama zamora    # LÍNEA DE TU PADRE (Merillas · López):
                                     # cartas al archivo diocesano de Zamora
    python ramas.py --rama palencia  # LÍNEA MATERNA DE TU MADRE (Pelaz · Merino):
                                     # cartas al archivo diocesano de Palencia

Las tres ramas se separan por PROVINCIA, que es lo que decide dónde se busca y a
quién se escribe. Antes de esta versión las dos últimas iban juntas bajo el
nombre "materna"; `--rama paterna` sigue valiendo como alias de `alava`.

Es lo que ejecutan las opciones 1 y 2 del menú. Se puede usar suelto: no pide
nada por teclado y NUNCA escribe en el árbol (`familia_conocida.json`) ni en los
`arbol_*`. Lo único que cambia:

  * `solicitudes_rama_<rama>.md` — el informe con las cartas listas para enviar.
  * `estado_investigacion.json` — añade las solicitudes a la lista
    `solicitudes` con estado "pendiente_envio" (dejando `.bak` antes).

Con `--solo-listar` no escribe nada: solo enseña lo que haría.

REGLA DE SIEMPRE: una partida se da por compatible con **≥2 datos
independientes**; si solo casa el apellido, o la fecha es de otra época, no se
pide nada (una tasa gastada en una partida que no se puede encajar es dinero
tirado). El cotejo lo hace agent/ramas.py, que es donde está la regla y sus
tests.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from agent import ramas                                    # noqa: E402
from config import AGENTE_MAX_FICHAS, AGENTE_MAX_LLM        # noqa: E402
from config import ARTXIBO_ANIO_MAX                         # noqa: E402
from config import BASE_DIR as DIR_PROYECTO                 # noqa: E402
from config import LINAJE_MAX_CONSULTAS                     # noqa: E402
from utils import ui                                        # noqa: E402


def _preparar_salida() -> None:
    """UTF-8 tolerante en stdout/stderr ANTES de imprimir nada.

    Misma lección que el FALLO 3 del sobremesa (lanzador.py): con la salida por
    tubería (un test, el log del menú, `python ramas.py > fichero`) la consola
    de Windows es cp1252 y los iconos del log (el engranaje de `ui.log`) la
    revientan con UnicodeEncodeError. Reconfigurar a UTF-8 con errors="replace"
    lo evita sin depender de que el que llama ponga PYTHONIOENCODING.
    """
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _buscar_en_artxibo(apellidos: list[str]) -> tuple[list[dict], list[str]]:
    """Busca cada apellido en el índice del AHDV (artxibo).

    Import PEREZOSO al módulo: así los tests parchean
    `scrapers.artxibo.buscar_apellido` y no se toca la red nunca.
    """
    from scrapers import artxibo
    filas: list[dict] = []
    avisos: list[str] = []
    for apellido in apellidos:
        try:
            encontradas, confianza = artxibo.buscar_apellido(apellido,
                                                             tipo="bautismo")
        except Exception as e:              # noqa: BLE001 (fallo de red/portal)
            avisos.append(f"'{apellido}': no se pudo consultar el índice "
                          f"({str(e)[:90]})")
            continue
        if confianza == artxibo.CONFIANZA_BAJA:
            avisos.append(f"'{apellido}': el apellido completo no dio "
                          f"resultados y se ha fragmentado "
                          f"({len(encontradas)} filas de confianza baja)")
        elif not encontradas:
            avisos.append(f"'{apellido}': sin resultados en el índice "
                          f"(1481-1900)")
        filas.extend(encontradas)
    return filas, avisos


def _escribir_informe(rama: str, texto: str,
                      base: Path | None = None) -> Path:
    base = base if base is not None else DIR_PROYECTO
    ruta = base / f"solicitudes_rama_{rama}.md"
    from config import escribir_con_backup
    escribir_con_backup(ruta, texto)
    return ruta


def _buscador_artxibo(max_filas: int = 100):
    """Buscador REAL del rastreo: el índice sacramental de Álava (artxibo).

    Es un doble para los tests (que parchean `scrapers.artxibo.\
buscar_sacramentales`), pero en producción es lo que consulta el portal: todas
    las búsquedas son gratis y públicas.
    """
    from scrapers import artxibo
    from config import sin_tildes

    def buscar(nombre, apellido1, apellido2="", anio_ini=None, anio_fin=None,
               **kwargs):
        filas = artxibo.buscar_sacramentales(
            tipo="bautismo", apellido1=apellido1, nombre=nombre,
            anio_ini=anio_ini, anio_fin=anio_fin, max_filas=max_filas)
        if not filas and sin_tildes(nombre) != (nombre or ""):
            # El índice guarda unas veces con tilde y otras sin ella: si no
            # sale nada, se prueba sin tildes antes de darlo por perdido.
            filas = artxibo.buscar_sacramentales(
                tipo="bautismo", apellido1=apellido1,
                nombre=sin_tildes(nombre), anio_ini=anio_ini,
                anio_fin=anio_fin, max_filas=max_filas)
        return filas

    return buscar


def _buscador_agente(max_filas: int):
    """Buscador del archivo por PRIMER APELLIDO (opción 1.3).

    Es el truco que funciona: pedir el apellido a secas ('Saenz de Navarrete')
    devuelve la lista larga del índice... y ahí están los familiares (hermanos,
    tíos, primos). Se prueban los tres sacramentos porque el matrimonio trae a
    los dos cónyuges.

    Doble para los tests (que parchean `scrapers.artxibo.buscar_sacramentales`).
    """
    from scrapers import artxibo
    from config import sin_tildes

    def buscar(apellido, tipo="bautismo", anio_ini=None, anio_fin=None,
               **kwargs):
        filas = artxibo.buscar_sacramentales(
            tipo=tipo, apellido1=apellido, anio_ini=anio_ini,
            anio_fin=anio_fin, max_filas=max_filas)
        if not filas and sin_tildes(apellido) != (apellido or ""):
            filas = artxibo.buscar_sacramentales(
                tipo=tipo, apellido1=sin_tildes(apellido), anio_ini=anio_ini,
                anio_fin=anio_fin, max_filas=max_filas)
        return filas

    return buscar


def _abridor_de_fichas():
    """Abre la FICHA de un registro (la página que dice el nombre del nacido y
    el de sus padres, y enlaza el libro digitalizado)."""
    from scrapers import artxibo

    def abrir(tipo, id_):
        return artxibo.ficha(tipo, id_=id_)

    return abrir


def ejecutar_agente(rama: str, base: Path | None = None,
                    max_llm: int | None = None,
                    max_consultas: int | None = None,
                    max_fichas: int | None = None, reiniciar: bool = False,
                    solo_listar: bool = False,
                    apellidos_extra: list[str] | None = None) -> int:
    """Opción 1.3: buscar FAMILIARES en el archivo vasco, con la IA leyendo.

    Busca solo en el buscador de registros sacramentales del AHDV (Álava,
    1481-1900): por el primer apellido, abriendo las fichas y repitiendo con
    todos los apellidos que aparecen (incluidos los de las madres). La IA
    (deepseek v4.1-flash) dice qué filas son familiares y qué buscar después;
    los nombres y las citas salen de las filas reales. GASTA dinero (pocos
    céntimos) en las llamadas a la IA.
    """
    from agent import agente_archivo as agente
    from config import (AGENTE_FICHAS_POR_APELLIDO, AGENTE_MAX_CONSULTAS,
                        AGENTE_MAX_FICHAS, AGENTE_MAX_LLM, ARTXIBO_MAX_FILAS)
    from utils.llm import PresupuestoExcedido
    try:
        rama = ramas.resolver_rama(rama)
    except ValueError as e:
        ui.log_error(str(e))
        return 1
    if rama != ramas.RAMA_ALAVA:
        ui.log_error("Esta búsqueda necesita un índice nominal online: hoy solo "
                     "lo tiene Álava (AHDV, 1481-1900). Para Zamora y Palencia "
                     "hay que pedir las partidas al archivo.")
        return 1
    base = base if base is not None else DIR_PROYECTO
    tope_llm = max_llm or AGENTE_MAX_LLM
    tope_consultas = max_consultas or AGENTE_MAX_CONSULTAS
    tope_fichas = max_fichas or AGENTE_MAX_FICHAS
    ui.cabecera("Familiares en el archivo vasco (AHDV) — con IA")

    estado = (agente.estado_vacio(rama) if reiniciar
              else agente.cargar_estado(base=base, rama=rama))
    antes = agente.resumen(estado)
    if antes["personas"] or antes["apellidos"]:
        ui.log(f"Se continúa la búsqueda anterior: {antes['personas']} "
               f"familiar(es) en la lista, {antes['apellidos']} apellido(s) "
               f"vistos, {antes['pendientes']} por buscar.")

    # Punto de partida: la gente de la línea (para contexto y para que la lista
    # no parezca vacía) y los apellidos de la línea, que es por donde empieza.
    personas = ramas.personas_de_rama(rama, base=base)
    for persona in personas:
        agente.anotar_conocido(
            estado, nombre=(persona.get("nombre") or "").split()[0],
            apellido1=persona.get("apellido_paterno", ""),
            apellido2=persona.get("apellido_materno", ""),
            anio=persona.get("anio"), municipio=persona.get("municipio", ""),
            parentesco=persona.get("origen", ""))
    for apellido in (ramas.apellidos_de_rama(personas)
                     + list(apellidos_extra or [])):
        if agente.apuntar_apellido(estado, apellido, "la línea de tu madre"):
            ui.log(f"Apellido de partida: «{apellido}»")
    if not estado["cola"]:
        ui.log_warn("No hay ningún apellido que buscar: revisa la rama de Álava "
                    "en familia_conocida.json o pasa --apellido.")
        return 1

    buscar = _buscador_agente(ARTXIBO_MAX_FILAS)
    abrir = _abridor_de_fichas()
    contador = {"n": 0}
    # Sin clave de OpenRouter: se busca igual en el archivo (gratis) y entra lo
    # que certifica la partida, pero nadie lee los resultados. Igual que el
    # "sin .env no muere" del resto del bot: avisa, no revienta.
    from config import OPENROUTER_API_KEY
    usar_ia = bool(OPENROUTER_API_KEY)
    if not usar_ia:
        ui.log_warn("No hay OPENROUTER_API_KEY: se buscará en el archivo SIN IA "
                    "(entra solo lo que certifica la propia partida). Copia "
                    ".env.example a .env para que la IA lea los resultados.")

    def avisar(texto: str) -> None:
        contador["n"] += 1
        ui.log(texto)

    ui.log(f"Buscando en el índice del AHDV (gratis) con la IA leyendo "
           f"(deepseek v4.1-flash): hasta {tope_llm} llamadas a la IA, "
           f"{tope_consultas} consultas al archivo y {tope_fichas} fichas. "
           f"Ctrl+C no pierde nada.")
    try:
        estado = agente.investigar(
            estado, buscar, abrir, None, max_llm=tope_llm,
            max_consultas=tope_consultas, max_fichas=tope_fichas,
            fichas_por_apellido=AGENTE_FICHAS_POR_APELLIDO, avisar=avisar,
            pausa=agente.AGENTE_DELAY, usar_ia=usar_ia, base=base)
    except PresupuestoExcedido as e:
        ui.log_warn(f"Presupuesto agotado ({e}): se guarda lo hecho.")
    except KeyboardInterrupt:
        ui.log_warn("Interrumpido: se guarda lo hecho y se puede continuar "
                    "otro día.")

    datos = agente.resumen(estado)
    ui.log_ok(f"{datos['personas']} familiar(es) en la lista: "
              f"{datos['partida']} los dice la partida, {datos['reservas']} con "
              f"2 datos y {datos['pistas']} son pistas.")
    ui.log(f"Apellidos buscados: {datos['buscados']} de {datos['apellidos']} · "
           f"consultas al archivo: {datos['consultas']} · fichas: "
           f"{datos['fichas']} · llamadas a la IA: {datos['llamadas_llm']} "
           f"(${datos['coste_llm']:.4f})")
    if datos["anio_min"]:
        ui.log(f"Años cubiertos: {datos['anio_min']} – {datos['anio_max']}")
    if datos["pendientes"]:
        ui.log(f"Quedan {datos['pendientes']} apellido(s) para la próxima "
               f"tanda (se continúa solo).")
    for persona in sorted(estado["personas"].values(),
                          key=lambda p: (p.get("nivel"), p.get("anio") or 0)):
        if persona.get("nivel") == agente.NIVEL_ARBOL:
            continue
        ui.log(f"   - {persona.get('anio') or '¿?'} · "
               f"{persona.get('nombre')} {persona.get('apellido1')} "
               f"{persona.get('apellido2')} · {persona.get('parentesco') or '—'}"
               f" · {agente.ETIQUETA_SELLO.get(persona.get('nivel'), '')}")
    if solo_listar:
        ui.log("(modo --solo-listar: no se ha escrito nada)")
        print(agente.informe(estado))
        return 0
    ruta_md = agente.escribir_informe(estado, base=base)
    respaldo = agente.guardar_estado(estado, base=base)
    ui.log_ok(f"Lista de familiares: {ruta_md}")
    ui.log(f"Estado reanudable: {base / agente.AGENTE_VENTANA}"
           + (f" (copia previa: {Path(respaldo).name})" if respaldo else ""))
    return 0


def ejecutar_linaje(rama: str, base: Path | None = None,
                    max_consultas: int | None = None,
                    reiniciar: bool = False, solo_listar: bool = False) -> int:
    """Rastreo del linaje hacia arriba (opción 1.2 del menú).

    Solo Álava: es el único índice sacramental online que hay (AHDV, 1481-1900).
    """
    from agent import linaje
    rama = ramas.resolver_rama(rama)
    if rama != ramas.RAMA_ALAVA:
        ui.log_error("El rastreo del linaje necesita un índice nominal online: "
                     "hoy solo lo tiene Álava (AHDV, 1481-1900). Para Zamora y "
                     "Palencia hay que pedir las partidas al archivo.")
        return 1
    base = base if base is not None else DIR_PROYECTO
    tope = max_consultas or linaje.LINAJE_MAX_CONSULTAS
    ui.cabecera("Rastreo del linaje — línea paterna de tu madre (Álava)")

    personas = ramas.personas_de_rama(rama, base=base)
    semillas = ramas.semillas_de_linaje(personas)
    # Que se VEA con quién se empieza: si una rama entra coja (p. ej. solo el
    # abuelo, porque las demás fichas no traen año ni provincia), hay que verlo
    # aquí y no en el informe final.
    sin_anio = ramas.personas_sin_anio(personas)
    if semillas:
        listado = " · ".join(
            f"{s['nombre']} {s.get('apellido1', '')} "
            f"{'~' if s.get('anio_estimado') else ''}{s.get('anio', '?')}"
            for s in semillas[:12])
        ui.log(f"Punto de partida: {len(semillas)} persona(s) — {listado}")
    if sin_anio:
        ui.log_warn(f"{len(sin_anio)} persona(s) de la rama sin año conocido "
                    f"(no se pueden buscar por ventana de fechas): "
                    f"{', '.join(sin_anio[:8])}")
    if not semillas:
        ui.log_warn("No hay ninguna persona con año en esta rama: no puedo "
                    "calcular la ventana de búsqueda.")
        return 1
    # El índice acaba en 1900: quien nazca después no está ahí, y buscarlo gasta
    # consultas para nada (fue el caso del abuelo, 1927: 2 consultas y 0
    # resultados). Se avisa y se manda al Registro Civil.
    dentro = [s for s in semillas if (s.get("ventana") or [None])[0]
              and s["ventana"][0] <= ARTXIBO_ANIO_MAX]
    fuera = [s for s in semillas if s not in dentro]
    if fuera:
        listado_fuera = ", ".join(f"{s['nombre']} {s.get('apellido1', '')} "
                                  f"({s.get('anio', '?')})" for s in fuera[:8])
        ui.log_warn(f"{len(fuera)} persona(s) nacida(s) después de "
                    f"{ARTXIBO_ANIO_MAX}: no están en el índice del AHDV; sus "
                    f"partidas se piden al Registro Civil — {listado_fuera}")
    if not dentro:
        ui.log_warn("Todas las personas de la rama nacieron después de "
                    f"{ARTXIBO_ANIO_MAX}: el índice del AHDV no llega. Para "
                    "estas generaciones hay que pedir el certificado al "
                    "Registro Civil (es gratis).")
        return 1
    semillas = dentro
    estado = (linaje.estado_vacio(rama) if reiniciar
              else linaje.cargar_estado(base=base, rama=rama))
    antes = dict(linaje.resumen(estado))
    if antes["personas"]:
        ui.log(f"Se continúa el rastreo anterior: {antes['personas']} persona(s) "
               f"ya vistas, {antes['consultas']} consultas hechas, "
               f"{antes['pendientes']} en la cola.")

    buscar = _buscador_artxibo()
    contador = {"n": 0}

    def avisar(texto: str) -> None:
        contador["n"] += 1
        ui.log(texto)
        if contador["n"] % 20 == 0 and not solo_listar:
            linaje.guardar_estado(estado, base=base)   # reanudable si se corta

    ui.log(f"Consultando el índice del AHDV en tandas de {tope} consultas "
           f"(gratis; Ctrl+C guarda y se puede seguir luego)...")
    try:
        estado = linaje.rastrear(semillas, buscar, estado=estado,
                                 max_consultas=tope, avisar=avisar)
    except KeyboardInterrupt:
        ui.log_warn("Interrumpido: se guarda lo hecho y se puede continuar "
                    "otro día.")
        if not solo_listar:
            linaje.guardar_estado(estado, base=base)
        return 0

    datos = linaje.resumen(estado)
    ui.log_ok(f"{datos['identificados']} antepasado(s) localizado(s) en "
              f"{datos['generacion_max']} generación(es) "
              f"({datos['anio_min'] or '?'}-{datos['anio_max'] or '?'}), "
              f"{datos['colaterales']} pariente(s) colateral(es).")
    ui.log(f"Consultas en total: {datos['consultas']} · pendientes: "
           f"{datos['pendientes']}")
    if datos["faltan"]:
        ui.log_warn(f"{datos['faltan']} eslabón(es) que faltan (son los que "
                    f"merecen una copia del archivo).")
    if solo_listar:
        ui.log("(modo --solo-listar: no se ha escrito nada)")
        print(linaje.redactar_markdown(estado))
        return 0
    ruta_md = linaje.escribir_informe(estado, base=base)
    respaldo = linaje.guardar_estado(estado, base=base)
    ui.log_ok(f"Informe: {ruta_md}")
    ui.log(f"Estado reanudable: {base / linaje.LINAJE_VENTANA}"
           + (f" (copia previa: {Path(respaldo).name})" if respaldo else ""))
    return 0


def ejecutar(rama: str, solo_listar: bool = False,
             base: Path | None = None) -> int:
    """Todo el trabajo de una rama. Devuelve el código de salida."""
    try:
        rama = ramas.resolver_rama(rama)
    except ValueError as e:
        ui.log_error(str(e))
        return 1
    base = base if base is not None else DIR_PROYECTO
    ui.cabecera(f"{ramas.TITULO[rama]}")

    personas = ramas.personas_de_rama(rama, base=base)
    if not personas:
        ui.log_warn(f"No hay ninguna persona de la rama '{rama}' en el árbol ni "
                    f"en la frontera. Revisa provincia/municipio en "
                    f"familia_conocida.json.")
        return 1
    ui.log(f"{len(personas)} persona(s) de la rama:")
    for persona in personas:
        ui.log(f"   - {persona['nombre']} · "
               f"{persona.get('municipio') or '¿?'} "
               f"({persona.get('provincia') or '¿?'}) · "
               f"~{persona.get('anio') or '¿?'} · {persona['origen']}")

    analisis: list[dict] = []
    if rama == ramas.RAMA_ALAVA:
        apellidos = ramas.apellidos_de_rama(personas)
        ui.log(f"Apellidos a buscar ENTEROS (nunca troceados): "
               f"{', '.join(apellidos) or '—'}")
        if not apellidos:
            ui.log_warn("No hay apellidos que buscar en esta rama.")
        else:
            filas, avisos = _buscar_en_artxibo(apellidos)
            for aviso in avisos:
                ui.log_warn(aviso)
            ui.log_ok(f"{len(filas)} partida(s) en el índice del AHDV.")
            analisis = ramas.analizar_filas(filas, personas)
            resumen: dict[str, int] = {}
            for candidato in analisis:
                veredicto = candidato["evaluacion"]["veredicto"]
                resumen[veredicto] = resumen.get(veredicto, 0) + 1
            for veredicto, cuenta in sorted(resumen.items()):
                ui.log(f"   {veredicto}: {cuenta}")
            for candidato in analisis:
                if candidato["evaluacion"]["veredicto"] not in (
                        ramas.VEREDICTO_COMPATIBLE, ramas.VEREDICTO_RESERVAS):
                    continue
                fila = candidato["fila"]
                ev = candidato["evaluacion"]
                ui.log_ok(
                    f"   {fila.get('fecha')} · {fila.get('persona', {}).get('completo')}"
                    f" · {fila.get('parroquia')}, {fila.get('localidad')}"
                    f" → {ev['veredicto']} ({ev['datos']:g} datos)")

    solicitudes = ramas.solicitudes_de_rama(rama, personas, analisis)
    texto = ramas.redactar_markdown(rama, personas, analisis, solicitudes)
    if not solicitudes:
        ui.log_warn("No hay ninguna solicitud que generar con lo que hay hoy.")
    else:
        ui.log_ok(f"{len(solicitudes)} solicitud(es) redactada(s):")
        for sol in solicitudes:
            ui.log(f"   - {sol['tipo']} · {sol['persona']}")
            ui.log(f"       se pide en: {sol.get('url_tramite') or '(email)'}"
                   f"  ·  {sol['contacto']}")

    if solo_listar:
        ui.log("(modo --solo-listar: NO se ha escrito nada)")
        print(texto)
        return 0

    ruta_md = _escribir_informe(rama, texto, base=base)
    ui.log_ok(f"Informe y cartas: {ruta_md}")
    resumen_estado = ramas.registrar_solicitudes(solicitudes, base=base)
    if resumen_estado["nuevas"]:
        ui.log_ok(f"{resumen_estado['nuevas']} solicitud(es) nueva(s) apuntada(s) "
                  f"en {Path(resumen_estado['ruta']).name} "
                  f"({resumen_estado['total']} en total, estado "
                  f"'{ramas.ESTADO_PENDIENTE}').")
    else:
        ui.log(f"Ninguna solicitud nueva que apuntar "
               f"({resumen_estado['ya_estaban']} ya estaban registradas).")
    if resumen_estado["bak"]:
        ui.log(f"Copia de seguridad previa: {Path(resumen_estado['bak']).name}")
    return 0


def _argumentos(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="ramas.py",
        description="Prepara la investigación y los trámites de una rama "
                    "familiar (no escribe en el árbol).")
    p.add_argument("--rama", type=ramas.resolver_rama,
                   default=ramas.RAMA_ALAVA, metavar="{alava,zamora,palencia}",
                   help="alava = línea paterna de tu madre (Álava/Vitoria); "
                        "zamora = línea de tu padre; palencia = línea materna "
                        "de tu madre")
    p.add_argument("--linaje", action="store_true",
                   help="en vez de preparar solicitudes, RASTREA el linaje "
                        "hacia arriba por el índice de Álava (gratis; se puede "
                        "cortar con Ctrl+C y continúa donde lo dejó)")
    p.add_argument("--agente-archivo", action="store_true",
                   help="BUSCA FAMILIARES en el buscador del archivo vasco "
                        "(AHDV) con la IA leyendo los resultados: por el primer "
                        "apellido, abriendo las fichas y repitiendo con los "
                        "apellidos de las madres. GASTA unos céntimos de IA; "
                        "también se puede cortar y continuar")
    p.add_argument("--apellido", action="append", default=None, metavar="APELLIDO",
                   help="apellido extra por el que empezar (se puede repetir). "
                        "Útil para arrancar en una línea concreta")
    p.add_argument("--max-llm", type=int, default=None,
                   help="llamadas a la IA en esta tanda (por defecto, "
                        f"{AGENTE_MAX_LLM})")
    p.add_argument("--max-fichas", type=int, default=None,
                   help="fichas abiertas en esta tanda (por defecto, "
                        f"{AGENTE_MAX_FICHAS})")
    p.add_argument("--max-consultas", type=int, default=None,
                   help="consultas al índice en esta tanda (por defecto, "
                        f"{LINAJE_MAX_CONSULTAS})")
    p.add_argument("--reiniciar", action="store_true",
                   help="empieza el rastreo de cero (ignora el estado guardado)")
    p.add_argument("--solo-listar", action="store_true",
                   help="enseña lo que haría sin escribir ni el informe ni el "
                        "estado")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _argumentos(argv)
    _preparar_salida()
    if args.agente_archivo:
        return ejecutar_agente(args.rama, max_llm=args.max_llm,
                               max_consultas=args.max_consultas,
                               max_fichas=args.max_fichas,
                               reiniciar=args.reiniciar,
                               solo_listar=args.solo_listar,
                               apellidos_extra=args.apellido)
    if args.linaje:
        return ejecutar_linaje(args.rama, max_consultas=args.max_consultas,
                               reiniciar=args.reiniciar,
                               solo_listar=args.solo_listar)
    return ejecutar(args.rama, solo_listar=args.solo_listar)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nCancelado.")
        sys.exit(0)
