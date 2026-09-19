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
