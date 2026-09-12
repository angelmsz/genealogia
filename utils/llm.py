"""
utils/llm.py — Llamadas al LLM con timeout duro radical, control de gasto
y manejo limpio de JSON roto.

FASE 1 del refactor v4.0 — Bug crítico del cuelgue infinito:
  El log original mostraba `Consultando deepseek-v4-flash... (364s)`: la
  llamada se quedaba colgada pese a `timeout=45` en el SDK de OpenAI.
  Causa: el parámetro timeout del SDK se aplica al HTTP subyacente pero
  existen casos (proxies, keep-alive, chunks finales) en los que la
  conexión queda en limbo sin disparar el timeout del SDK.

  Solución radical: ejecutamos la llamada al LLM en un HILO DAEMON
  independiente. El hilo principal hace `hilo.join(timeout=N)` y, si
  sigue vivo, lanza `TimeoutError` inmediatamente sin esperar al SDK.
  El hilo daemon sigue (no se puede matar limpiamente un hilo que está
  bloqueado en I/O en Python) pero NO bloquea el proceso principal ni
  la salida del script.

  Diferencia con la implementación anterior de `_crear_con_timeout_hard`:
  aquí se RELANZA la excepción al llamador, en vez de tragársela. Así
  `chat_json` puede capturarla y reintentrar con backoff, o devolver un
  JSON limpio vacío según el contexto.

Otras mejoras:
  - Captura limpia de JSON roto: la excepción se registra en una sola
    línea con log_una_linea(nivel="warn") sin verter el stacktrace.
  - Control de gasto thread-safe (las descargas van en paralelo).
  - Detección de timeouts vs errores de esquema (structured outputs).
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from openai import OpenAI

from config import (BASE_DIR, FACTOR_COSTE_ABANDONADO, MAX_TOKENS_ESTIMADO,
                    MODELO_FASE1, MODELO_FASE2,
                    OPENROUTER_API_KEY, PRECIO_MILLON_TOKENS,
                    PRECIO_POR_DEFECTO, REASONING_ACTIVADO, TIMEOUT_LLM,
                    JSON_SCHEMA_VARIANTES,
                    SYSTEM_PROMPT_VARIANTES, DB_LOCK, MAX_REINTENTOS_LLM,
                    normalizar, sin_tildes,
                    extraer_json_de_respuesta)
from utils import ui


# ============================== EXCEPCIONES ================================

class PresupuestoExcedido(Exception):
    """Se lanza al superar --presupuesto-max. Los bucles la capturan para
    parar de forma segura guardando todo el progreso."""


class LLMTimeoutHard(Exception):
    """El LLM no respondió en el tiempo fijado (timeout radical). Es
    capturable y NO rompe el script: chat_json la reintenta con backoff."""


# ============================== CONTROL DE GASTO ============================

@dataclass
class Gasto:
    """Acumulador thread-safe de tokens y coste estimado por modelo.

    v4.2 (punto 8 del informe): cuando el provider NO devuelve el recuento
    de uso (usage=None), antes se apuntaba coste CERO y el tope de
    --presupuesto-max nunca saltaba. Ahora se ESTIMA el coste a partir de
    los caracteres de la petición/respuesta (~4 caracteres por token) y se
    marca la llamada como estimada: el presupuesto funciona siempre, aunque
    sea con aproximación conservadora.
    """
    presupuesto_max: Optional[float] = None
    llamadas: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    coste: float = 0.0
    busquedas_tavily: int = 0
    llamadas_estimadas: int = 0   # v4.2: llamadas sin usage del provider
    # v10.4.1 (tarea E): intentos ABANDONADOS por timeout. El cliente deja de
    # esperar a los N segundos, pero el proveedor sigue generando y FACTURA
    # (lo dice el propio _llamada_con_hard_timeout). Antes no se contaban:
    # en la noche del 12/09 el bot dijo $0.1344 y OpenRouter cobró $0.38.
    llamadas_abandonadas: int = 0
    coste_abandonado: float = 0.0
    por_modelo: dict = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock,
                                   repr=False, compare=False)

    @staticmethod
    def _tokens_usage(usage):
        """(prompt_tokens, completion_tokens) de un objeto usage de OpenAI
        o de un dict (OpenRouter a veces devuelve dict). (None, None) si el
        recuento falta o es cero en ambos."""
        if usage is None:
            return None, None
        pt = getattr(usage, "prompt_tokens", None)
        ct = getattr(usage, "completion_tokens", None)
        if pt is None and isinstance(usage, dict):
            pt = usage.get("prompt_tokens")
            ct = usage.get("completion_tokens")
        try:
            pt = int(pt) if pt is not None else None
            ct = int(ct) if ct is not None else None
        except (TypeError, ValueError):
            return None, None
        if not pt and not ct:
            return None, None
        return pt or 0, ct or 0

    def registrar_llamada(self, modelo: str, usage=None,
                          texto_entrada: str = "",
                          texto_salida: str = "",
                          n_imagenes: int = 0) -> None:
        """Acumula tokens/coste de una llamada y lanza PresupuestoExcedido
        si se supera el límite fijado con --presupuesto-max.

        v4.2 — Estimación cuando falta usage: se cuentan los caracteres de
        entrada/salida entre 4 y se añade un coste conservador por imagen
        (~1000 tokens de entrada por imagen de página). La llamada queda
        contada como `llamadas_estimadas` para que el resumen de gasto sea
        honesto sobre qué es medido y qué estimado.
        """
        with self._lock:
            precio = PRECIO_MILLON_TOKENS.get(modelo, PRECIO_POR_DEFECTO)
            pt, ct = self._tokens_usage(usage)
            estimado = False
            if pt is None:
                estimado = True
                pt = max(1, len(texto_entrada or "") // 4)
                ct = max(0, len(texto_salida or "") // 4)
                if n_imagenes:
                    pt += n_imagenes * 1000  # estimación conservadora/página
            coste = (pt / 1_000_000 * precio["entrada"]
                     + ct / 1_000_000 * precio["salida"])
            self.llamadas += 1
            if estimado:
                self.llamadas_estimadas += 1
            self.prompt_tokens += pt
            self.completion_tokens += ct
            self.coste += coste
            d = self.por_modelo.setdefault(modelo, {
                "llamadas": 0, "prompt_tokens": 0,
                "completion_tokens": 0, "coste": 0.0, "estimadas": 0})
            d["llamadas"] += 1
            if estimado:
                d["estimadas"] = d.get("estimadas", 0) + 1
            d["prompt_tokens"] += pt
            d["completion_tokens"] += ct
            d["coste"] += coste
            if (self.presupuesto_max is not None
                    and self.coste >= self.presupuesto_max):
                raise PresupuestoExcedido(
                    f"gasto acumulado ${self.coste:.4f} >= presupuesto "
                    f"${self.presupuesto_max:.2f}")

    def registrar_abandonada(self, modelo: str, texto_entrada: str = "",
                             max_tokens_salida: int | None = None) -> float:
        """v10.4.1 (tarea E) — Cuenta un intento ABANDONADO por timeout.

        POR QUÉ: `_llamada_con_hard_timeout` deja de esperar a los N segundos
        y sigue con la vida, pero la petición ya está EN MARCHA en el
        proveedor: si termina, OpenRouter la factura. En la noche real del
        12/09 eso fueron 38 intentos de lote + 3 de consolidación + 5 de fase
        1 = 46 peticiones facturadas que el resumen no veía (decía $0.1344
        cuando se cobraron $0.38: 65 % de gasto invisible). Peor: el tope de
        `--presupuesto-max` se calculaba sobre esa cifra optimista, así que
        NO protegía.

        CÓMO (conservador por diseño): el coste se estima con datos que YA
        conocemos antes de enviar la petición —el prompt lo escribimos
        nosotros— usando el precio MÁS ALTO de la tabla:
            entrada = len(prompt) // 4  (misma regla que la estimación v4.2)
            salida  = max_tokens de la llamada (o MAX_TOKENS_ESTIMADO si no
                      se fijó tope)
        Es una estimación POR ARRIBA de lo que el proveedor puede cobrar por
        ese intento, así que el presupuesto se queda corto antes que largo:
        exactamente lo contrario del bug que arreglamos.

        `config.FACTOR_COSTE_ABANDONADO` (defecto 1.0) permite calibrar esa
        estimación a la baja con datos reales: con la noche del 12/09 el peor
        caso daba $0.4805 y lo cobrado de verdad fueron $0.2456 (la mitad de
        los intentos no llegó a facturarse), así que un factor ~0.5 cuadra.
        Por defecto se deja en 1.0: si el tope se queda corto, es del lado
        seguro.

        Devuelve el coste estimado añadido. Lanza PresupuestoExcedido si con
        esta suma se supera el tope (parada segura: el llamador lo propaga).
        """
        with self._lock:
            precio = PRECIO_MILLON_TOKENS.get(modelo, PRECIO_POR_DEFECTO)
            pt = max(1, len(texto_entrada or "") // 4)
            ct = int(max_tokens_salida or MAX_TOKENS_ESTIMADO)
            coste = ((pt / 1_000_000 * precio["entrada"]
                      + ct / 1_000_000 * precio["salida"])
                     * FACTOR_COSTE_ABANDONADO)
            self.llamadas_abandonadas += 1
            self.coste_abandonado += coste
            self.coste += coste
            d = self.por_modelo.setdefault(modelo, {
                "llamadas": 0, "prompt_tokens": 0,
                "completion_tokens": 0, "coste": 0.0, "estimadas": 0})
            d["abandonadas"] = d.get("abandonadas", 0) + 1
            d["coste_abandonado"] = d.get("coste_abandonado", 0.0) + coste
            if (self.presupuesto_max is not None
                    and self.coste >= self.presupuesto_max):
                raise PresupuestoExcedido(
                    f"gasto acumulado ${self.coste:.4f} >= presupuesto "
                    f"${self.presupuesto_max:.2f} (incluye "
                    f"{self.llamadas_abandonadas} intento(s) abandonado(s) "
                    f"por timeout)")
            return coste


GASTO = Gasto()


def presupuesto_agotado() -> bool:
    return (GASTO.presupuesto_max is not None
            and GASTO.coste >= GASTO.presupuesto_max)


def resumen_gasto() -> None:
    ui.log_stats(f"=== RESUMEN DE GASTO ===")
    ui.log(f"Llamadas LLM: {GASTO.llamadas}"
           + (f" ({GASTO.llamadas_estimadas} sin usage del provider, coste "
              f"ESTIMADO)" if GASTO.llamadas_estimadas else "")
           + f" | Búsquedas Tavily: {GASTO.busquedas_tavily}")
    for modelo, d in sorted(GASTO.por_modelo.items()):
        nota = " (est.)" if d.get("estimadas") else ""
        aband = (f" + {d['abandonadas']} abandonada(s)"
                 if d.get("abandonadas") else "")
        # El coste de la línea es el MEDIDO; las abandonadas se suman aparte
        # para que ningún número de esta tabla mezcle medido con estimado.
        aband_coste = (f" + ${d.get('coste_abandonado', 0.0):.4f} (abandonadas)"
                       if d.get("abandonadas") else "")
        ui.log(f"  {modelo}: {d['llamadas']} llamadas{nota}{aband}, "
               f"{d['prompt_tokens']} tok. entrada + {d['completion_tokens']} tok. "
               f"salida = ${d['coste']:.4f}{aband_coste}")
    # v10.4.1 (tarea E): los intentos abandonados por timeout se facturan en
    # el proveedor y NO se pueden medir (el usage nunca llega). Se declaran
    # aparte, con su coste estimado POR ARRIBA, para que el total sea
    # comparable con la "Activity" de OpenRouter.
    if GASTO.llamadas_abandonadas:
        ui.log_warn(
            f"{GASTO.llamadas_abandonadas} intento(s) ABANDONADO(S) por "
            f"timeout (el proveedor los factura igual): coste ESTIMADO por "
            f"arriba +${GASTO.coste_abandonado:.4f}")
    total_tok = GASTO.prompt_tokens + GASTO.completion_tokens
    medido = GASTO.coste - GASTO.coste_abandonado
    desglose = (f" (medido ${medido:.4f} + abandonado "
                f"${GASTO.coste_abandonado:.4f})"
                if GASTO.llamadas_abandonadas else "")
    ui.log(f"Tokens totales: {total_tok} | COSTE ESTIMADO: ${GASTO.coste:.4f}"
           + desglose
           + (f" (presupuesto máx.: ${GASTO.presupuesto_max:.2f})"
              if GASTO.presupuesto_max is not None else ""))


# ============================== CLIENTE LLM =================================

# v4.2 (punto 8 del informe): max_retries del SDK de 3 a 1. Antes:
# 4 intentos de chat_json x (1 + 3 reintentos SDK) = hasta 16 llamadas
# reales por consulta cuando la red iba lenta (la causa de los cuelgues de
# 6 minutos). Ahora: 3 intentos x (1 + 1 reintento SDK) = 6 como máximo.
# v10.4.1 (tarea E): max_retries de 1 a 0. Con el reintento del SDK, un
# intento que se pasaba del timeout podía lanzar una SEGUNDA petición
# huérfana desde el hilo abandonado (nadie la espera, nadie la cuenta y el
# proveedor la factura). Nuestro propio bucle de chat_json ya reintenta con
# backoff, así que el reintento del SDK solo añadía gasto invisible.
llm = OpenAI(
    api_key=OPENROUTER_API_KEY,
    base_url="https://openrouter.ai/api/v1",
    max_retries=0,
)


# Modelos que rechazaron response_format json_schema (fallback a prompt).
# Se rellena en caliente la primera vez que un provider devuelve error.
_MODELOS_SIN_SCHEMA: set = set()

# v9.2 — Modelos que rechazaron el parámetro "reasoning" de OpenRouter.
# Mismo patrón que _MODELOS_SIN_SCHEMA: se rellena en caliente ante un 400
# del provider y a partir de ahí no se les vuelve a enviar (no rompe la
# llamada a modelos que no lo soportan aunque REASONING_ACTIVADO=true).
_MODELOS_SIN_REASONING: set = set()


# ============================== TIMEOUT DURO ================================
# FASE 1 — Bug crítico. Llamada radical: el LLM se ejecuta en un hilo
# daemon; si no termina en N segundos, se lanza LLMTimeoutHard al llamador.
# El hilo sigue (no se puede matar un hilo bloqueado en I/O), pero NO
# bloquea el proceso principal ni la salida.

def _llamada_con_hard_timeout(fn, *, hard_timeout: float, modelo: str = "",
                              texto_entrada: str = "",
                              max_tokens_salida: int | None = None,
                              **kwargs):
    """Ejecuta fn(**kwargs) en un hilo daemon. Si no termina en
    `hard_timeout` segundos, lanza LLMTimeoutHard inmediatamente: el
    llamador recibe el error y puede continuar (reintentar, saltar,
    devolver []).

    El parámetro se llama `hard_timeout` (no `timeout`) a propósito:
    así no entra en conflicto con el `timeout=` que el SDK de OpenAI
    recibe para su HTTP timeout. Llamamos a ambos: el HTTP timeout del
    SDK y nuestro hard timeout de hilo. El hard timeout es el verdadero
    seguro: aunque el SDK no dispare su timeout (proxies, keep-alive,
    chunks finales en limbo), el join() del hilo lo hará.

    El hilo daemon se limpiará al cerrar el proceso. La conexión HTTP
    subyacente puede quedar en limbo unos segundos, pero el proceso
    principal sigue: eso es lo que importa para no colgar el bucle.
    Nota: El hilo sigue corriendo tras un timeout y el proveedor puede
    llegar a cobrar tokens si finalmente procesa la request en background.

    v10.4.1 (tarea E): cuando se abandona, ese cobro YA NO ES INVISIBLE. Si
    se pasa `modelo`, se registra el intento con coste estimado POR ARRIBA
    (ver Gasto.registrar_abandonada) y ese importe cuenta para
    `--presupuesto-max`. Medido en la noche del 12/09: 46 intentos
    abandonados = $0.38 cobrados en OpenRouter frente a $0.1344 contados.
    """
    caja: dict = {"valor": None, "error": None, "hecho": False}

    def _runner():
        try:
            # Propagate the timeout to the underlying SDK/request
            if "timeout" not in kwargs:
                kwargs["timeout"] = hard_timeout
            caja["valor"] = fn(**kwargs)
        except BaseException as e:  # capturamos todo incluido KeyboardInterrupt
            caja["error"] = e
        finally:
            caja["hecho"] = True

    hilo = threading.Thread(target=_runner, daemon=True)
    hilo.start()
    hilo.join(timeout=hard_timeout)

    if not caja["hecho"]:
        # El hilo sigue corriendo pero el llamador ya no espera: lanza.
        if modelo:
            # v10.4.1 (E): contabilizar el abandono ANTES de lanzar. Si con
            # esta suma se supera el presupuesto, sube PresupuestoExcedido
            # (parada segura) en vez del timeout: el tope manda.
            GASTO.registrar_abandonada(modelo, texto_entrada,
                                       max_tokens_salida)
        raise LLMTimeoutHard(
            f"LLM no respondió en {hard_timeout}s (hard timeout radical)")
    if caja["error"] is not None:
        raise caja["error"]
    return caja["valor"]


# ============================== UTILIDADES DE ERROR ========================

def _es_timeout(e: Exception) -> bool:
    """True si la excepción es un timeout de red/API (no un rechazo de
    esquema): los timeouts se reintentan con la MISMA configuración."""
    if isinstance(e, LLMTimeoutHard):
        return True
    if "timeout" in type(e).__name__.lower():
        return True
    texto = str(e).lower()
    return "timed out" in texto or "timeout" in texto


def _es_rate_limit(e: Exception) -> bool:
    """v10.2 — True si el error es un rate-limit (429) o un 503 con
    'rate-limited upstream' de OpenRouter.

    En el log de ejecución real, el 503 final de la consolidación venía
    de un 'deepseek-v4.1-flash is temporarily rate-limited upstream'
    con previous_errors 429 de hasta TRES providers distintos
    (DeepInfra, Morph, Fireworks): el modelo estaba saturado EN TODOS
    los proveedores. Con el backoff corto de siempre (2-4 s) los 3
    reintentos caían dentro de la misma racha de saturación y la fase
    2 moría. Estos errores se comprueban ANTES que _es_error_schema:
    un 503 no es un rechazo de response_format ni del reasoning."""
    codigo = getattr(e, "status_code", None)
    if codigo in (429, 503):
        return True
    texto = str(e).lower()
    return ("rate-limited" in texto or "rate limit" in texto
            or "rate_limit" in texto or "too many requests" in texto)


def _es_error_schema(e: Exception) -> bool:
    """True si el error indica que el provider rechaza response_format
    json_schema (entonces toca fallback a prompt+reintento)."""
    if getattr(e, "status_code", None) in (400, 422):
        return True
    texto = str(e).lower()
    return any(p in texto for p in ("response_format", "json_schema",
                                    "structured output", "bad request",
                                    "invalid_request", "unsupported"))


def _es_error_reasoning(e: Exception) -> bool:
    """v9.2 — El provider rechazó el parámetro "reasoning" (modelos sin
    soporte): el error menciona 'reasoning' junto a alguna fórmula de
    'parámetro no soportado'. Se comprueba ANTES que _es_error_schema
    porque un mismo 400 puede contener ambas palabras."""
    texto = str(e).lower()
    return "reasoning" in texto and any(
        p in texto for p in ("unsupported", "not support", "invalid",
                             "bad request", "invalid_request"))


# ============================== CHAT_JSON ==================================

def chat_json(modelo: str, system: str, user: str, temperatura: float = 0.0,
              intentos: int = MAX_REINTENTOS_LLM, json_schema: dict | None = None,
              schema_name: str = "respuesta", timeout: float | None = None,
              max_tokens: int | None = None):
    """Llama al LLM y devuelve la respuesta ya parseada como JSON.

    - TIMEOUT DURO (FASE 1): la llamada se ejecuta en un hilo daemon; si
      no responde en `timeout` segundos (def. TIMEOUT_LLM), se aborta de
      forma radical lanzando LLMTimeoutHard. chat_json captura ese error,
      reintenta con backoff (2s, 4s...) hasta `intentos` veces.
    - Si se pasa `json_schema`, pide structured outputs vía response_format.
      Si el provider lo rechaza, fallback automático a prompt+reintento
      sin romper la ejecución. Un timeout NO desactiva el esquema.
    - v9.2: si config.REASONING_ACTIVADO=true, añade al payload el
      parámetro de OpenRouter `reasoning: {"enabled": true}` (DeepSeek
      V4.1 y otros modelos de razonamiento). Si el modelo lo rechaza
      (400), se desactiva para esa sesión y se reintenta sin él — igual
      que el fallback de response_format, no rompe modelos sin soporte.
      Nota: la preservación de "reasoning_details" en multi-turno NO
      aplica aquí: cada extracción es un turno nuevo [system, user] y
      jamás se reenvían mensajes del asistente (ver README v9.2).
    - Tras cada llamada acumula tokens y coste en GASTO; si se supera
      --presupuesto-max lanza PresupuestoExcedido (NO se traga aquí).
      v4.2: si el provider no devuelve usage, el coste se ESTIMA desde
      los caracteres de petición/respuesta (nunca coste cero).
      v10.4.1 (E): cada intento ABANDONADO por timeout también se cuenta
      (coste estimado por arriba: ver Gasto.registrar_abandonada). El
      proveedor cobra esas peticiones aunque el cliente deje de esperar.
    - v10.4.1 (E) `max_tokens`: techo de SALIDA de la respuesta. Es la
      palanca que hace calculable el coste máximo de un intento (entrada
      conocida + max_tokens x precio punta) y el que evita que una respuesta
      desbocada se lleve por delante tiempo y dinero. Si es None se deja al
      valor por defecto del modelo (y el coste del abandono se estima con
      MAX_TOKENS_ESTIMADO).
    - JSON roto: en vez de escupir el stacktrace, se registra en una sola
      línea con nivel="warn" y se reintenta pidiendo JSON estricto.
    """
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    usar_schema = json_schema is not None and modelo not in _MODELOS_SIN_SCHEMA
    # v9.2 — razonamiento opcional SOLO si config lo activa (por defecto
    # va APAGADO: los tokens de razonamiento se cobran como salida y en
    # extracción estructurada no suele compensar). Nunca se envía
    # "reasoning" a un modelo que ya lo rechazó en esta sesión.
    usar_reasoning = (REASONING_ACTIVADO
                      and modelo not in _MODELOS_SIN_REASONING)
    nombre_corto = modelo.split("/")[-1]
    timeout_efectivo = timeout or TIMEOUT_LLM
    ultimo_error: Optional[Exception] = None

    for intento in range(intentos):
        kwargs = {}
        if usar_reasoning:
            kwargs["reasoning"] = {"enabled": True}
        if usar_schema:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "schema": json_schema,
                    "strict": True,
                },
            }
        # v10.4.1 (E): techo de salida (acota tiempo Y coste del intento).
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        try:
            with ui.Indicador(f"Consultando {nombre_corto}", nivel="llm"):
                # Pasamos DOS timeouts:
                #   - timeout=  : HTTP timeout del SDK de OpenAI (cubre la
                #                 mayoría de los casos)
                #   - hard_timeout=  : join() del hilo daemon (cubre los
                #                     casos en que el SDK no dispara)
                # v10.4.1 (E): además se le dan al hard timeout el modelo y
                # el prompt para poder contabilizar el intento si se abandona.
                resp = _llamada_con_hard_timeout(
                    llm.chat.completions.create,
                    hard_timeout=timeout_efectivo,
                    modelo=modelo,
                    texto_entrada=system + "\n" + user,
                    max_tokens_salida=max_tokens,
                    model=modelo,
                    temperature=temperatura,
                    messages=messages,
                    timeout=timeout_efectivo,
                    **kwargs,
                )
            # Si la llamada devolvió respuesta, registramos el gasto.
            # v4.2: pasamos los textos para poder ESTIMAR el coste si el
            # provider no devolvió usage (nunca apuntar coste cero).
            contenido = (resp.choices[0].message.content or "").strip()
            GASTO.registrar_llamada(modelo, getattr(resp, "usage", None),
                                    texto_entrada=system + "\n" + user,
                                    texto_salida=contenido)
            if not contenido:
                raise ValueError("el modelo devolvió una respuesta vacía")
            return extraer_json_de_respuesta(contenido)
        except PresupuestoExcedido:
            raise  # parada segura: no reintentar, que el bucle guarde y pare
        except LLMTimeoutHard as e:
            # Una sola línea: no queremos verter stacktrace por terminal.
            ui.log_warn(f"{nombre_corto} no respondió en {timeout_efectivo}s "
                        f"(intento {intento + 1}/{intentos}); backoff...")
            ultimo_error = e
        except (ValueError, json.JSONDecodeError) as e:
            # JSON roto: aviso limpio de una línea + reintento pidiendo
            # JSON estricto, sin verter el contenido crudo del error.
            ui.log_warn(f"{nombre_corto} devolvió JSON inválido "
                        f"(intento {intento + 1}/{intentos}); pidiendo JSON "
                        f"estricto. Causa: {str(e)[:120]}")
            ultimo_error = e
            messages.append({
                "role": "user",
                "content": "Tu respuesta anterior no era JSON válido. "
                           "Devuelve ÚNICAMENTE el JSON, sin comentarios ni "
                           "texto adicional."
            })
        except Exception as e:
            ultimo_error = e
            # v10.2 — primero el rate-limit: un 429/503 'rate-limited
            # upstream' NO se arregla desactivando reasoning/schema ni con
            # backoff de 2-4 s; toca esperar de verdad (ver
            # _es_rate_limit). Se comprueba antes que los rechazos de
            # parámetros porque un mismo 4xx/5xx puede mencionar varias
            # cosas y el rate-limit manda sobre las demás lecturas.
            if _es_rate_limit(e):
                espera = min(15 * (2 ** intento), 60)
                ui.log_warn(f"{nombre_corto} saturado (rate-limit "
                            f"upstream; intento {intento + 1}/{intentos}); "
                            f"esperando {espera} s antes de reintentar...")
                if intento < intentos - 1:
                    time.sleep(espera)
                continue
            # v9.2 — primero el rechazo de "reasoning" (un 400 puede
            # mencionar ambas cosas; el orden evita desactivar el schema
            # por culpa de un error que era del reasoning).
            if usar_reasoning and not _es_timeout(e) and _es_error_reasoning(e):
                ui.log_warn(f"{modelo} rechazó el parámetro reasoning; se "
                            f"desactiva para esta sesión y se reintenta "
                            f"sin él. ({str(e)[:140]})")
                _MODELOS_SIN_REASONING.add(modelo)
                usar_reasoning = False
                continue
            if usar_schema and not _es_timeout(e) and _es_error_schema(e):
                # El provider rechaza response_format json_schema:
                # fallback automático a prompt+reintento (sin esperar).
                ui.log_warn(f"{modelo} rechazó response_format; fallback a "
                            f"prompt+reintento. ({str(e)[:140]})")
                _MODELOS_SIN_SCHEMA.add(modelo)
                usar_schema = False
                continue
            if _es_timeout(e):
                ui.log_warn(f"{nombre_corto} timeout por SDK "
                            f"(intento {intento + 1}/{intentos}); backoff...")
            else:
                ui.log_warn(f"{nombre_corto} error "
                            f"(intento {intento + 1}/{intentos}): "
                            f"{str(e)[:140]}")
        # v4.2 (punto 8): backoff acotado a 4 s y SIN espera tras el último
        # intento — no tiene sentido dormir para luego rendirse igual.
        if intento < intentos - 1:
            time.sleep(min(2 * (2 ** intento), 4))

    # Tras agotar intentos: una sola línea, sin stacktrace.
    ui.log_error(f"LLM inaccesible tras {intentos} intentos: "
                 f"{str(ultimo_error)[:140] if ultimo_error else 'desconocido'}")
    raise RuntimeError(f"LLM inaccesible tras {intentos} intentos: "
                       f"{ultimo_error}")


# ============================== OCR 100% LOCAL (v10.0) =====================
# chat_vision y transcribir_imagen_llm se ELIMINAN: la transcripción de
# imágenes (OCR, documentos propios) es 100% LOCAL (scrapers/web.py) y ya
# no se manda ninguna imagen a un modelo de visión de la nube. El LLM de
# texto (chat_json, OpenRouter) sigue exactamente igual: NO es OCR.


# ============================== VARIANTES DE APELLIDO ======================
# Cachea en SQLite las variantes ortográficas de un apellido (con/sin
# tilde y grafías históricas) usando el LLM barato UNA vez por apellido.

def variantes_apellido(apellido: str, conn=None) -> list[str]:
    """Variantes ortográficas de un apellido (con/sin tilde). Si se pasa
    `conn` (SQLite), pregunta al LLM barato UNA vez por apellido y cachea
    el resultado en la tabla variantes_apellidos para no repetirlo.
    Sin `conn` (--diagnostico) no hace llamadas al LLM."""
    apellido = (apellido or "").strip()
    if not apellido:
        return []
    variantes = {apellido, sin_tildes(apellido)}
    if conn is not None:
        clave = normalizar(apellido)
        with DB_LOCK:
            fila = conn.execute(
                "SELECT variantes FROM variantes_apellidos WHERE clave=?",
                (clave,)).fetchone()
        if fila:
            for v in json.loads(fila[0]):
                if isinstance(v, str) and v.strip():
                    variantes.add(v.strip())
        else:
            nuevas = []
            try:
                resp = chat_json(MODELO_FASE1, SYSTEM_PROMPT_VARIANTES,
                                 f"Apellido: {apellido}",
                                 json_schema=JSON_SCHEMA_VARIANTES,
                                 schema_name="variantes_apellido",
                                 intentos=2)
                nuevas = [v.strip() for v in resp.get("variantes", [])
                          if isinstance(v, str) and v.strip()][:4]
            except PresupuestoExcedido:
                raise
            except Exception as e:
                ui.log_warn(f"variantes de {apellido!r}: {str(e)[:100]}")
            with DB_LOCK:
                conn.execute(
                    "INSERT OR REPLACE INTO variantes_apellidos VALUES (?,?)",
                    (clave, json.dumps(nuevas, ensure_ascii=False)))
                conn.commit()
            variantes.update(nuevas)
    return list(dict.fromkeys(v for v in variantes if v))
