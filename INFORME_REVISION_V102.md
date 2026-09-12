# INFORME DE REVISIÓN v10.2 — Corrección de los fallos del log de ejecución real

Fecha: 2026-09-11 · Sobre: genealogia_v10.1 (232 tests: 231 verdes + 1 roto por el .env)
Fuente de fallos: `cmd de la egecucion (analizar).txt` (noche completa del autopiloto:
`--ciclo 3 --presupuesto-max 3.0 --max-steps 10`, ~2 h 15 min)
Resultado: **246 tests, 0 fallos** (14 nuevos de regresión) · `--diagnostico` OK

---

## El fallo más caro: una noche entera sin las 3 fuentes principales

El log repetía en CADA objetivo de CADA ciclo:

```
[00:25:09] [!] recolector recolector_siga falló: recolector_siga() missing 1 required positional argument: 'objetivo'
[00:25:09] [!] recolector recolector_addo falló: recolector_addo() missing 1 required positional argument: 'objetivo'
[00:25:09] [!] recolector recolector_ensenada falló: recolector_ensenada() missing 1 required positional argument: 'objetivo'
```

45 avisos iguales. La causa está en `scrapers/archivos.py::recolectar()`:
HISPAGEN y FamilySearch iban envueltos en closures que capturaban
`objetivo` y `conn`, pero SIGA/ADDO/Ensenada se invocaban directamente
como `conector()` — sin argumentos. La excepción se tragaba con un
`log_warn` y la ejecución seguía... sin consultar NUNCA los
sacramentales de Álava (SIGA), el diocesano de Palencia (ADDO) ni el
Catastro de Ensenada (PARES). Es decir: la noche se fue entera en
Tavily + filtrado LLM, pagando tokens por lo que los conectores dan
gratis y estructurado.

**Por qué no lo pilló la suite**: `test_recolectar_incluye_los_nuevos_conectores`
parchea los tres conectores con lambdas `(o, c=None)`... y como
`recolectar()` las llamaba sin argumentos, reventaban DENTRO del
try/except que se traga el error: el test solo comprobaba que HISPAGEN
y FamilySearch aparecieran en `llamadas`, y pasaba igual. El nuevo test
de regresión exige que los CINCO conectores reciban exactamente
`(objetivo, conn)`.

## FIX 2 — el crash que mató el programa dos veces

```
RuntimeError: LLM inaccesible tras 3 intentos: Error code: 503 - ...
  File "...agent\fase2.py", line 601, in fase2
    consolidado = consolidar(datos_familia, hallazgos)
```

Pasó a las 02:35 (autopiloto) y otra vez a las 15:55 (reintento manual
`--fase 2`): `consolidar()` era el único punto de la fase 2 sin red de
seguridad, y su `RuntimeError` mataba TODO — sin GEDCOM, sin siguiente
ciclo, con traceback. Ahora:

- Los hallazgos ya extraídos y auditados se guardan SIEMPRE (lo hacían
  en el paso 4/6, antes de consolidar — no se pierde nada).
- `arbol_refinado.json` se escribe con un bloque determinista marcado
  `CONSOLIDACIÓN PENDIENTE` (el clasificador de evidencia v9.1 sigue
  funcionando: no depende del LLM).
- El GEDCOM se exporta igual (paso 6/6).
- En `--ciclo`, el autopiloto registra el fallo y pasa al ciclo
  siguiente.
- Reintentar luego es barato: la caché de extracción (`hallazgos_por_hash`)
  evita repetir los lotes ya extraídos.

El test de integración nuevo (`test_fase2_llm_caido_no_mata_el_programa`)
reproduce EXACTAMENTE el escenario en un subproceso: extracción OK,
consolidación `RuntimeError` → exit limpio, hallazgos en disco, cita
verificada por el pase determinista, GEDCOM exportado.

## FIX 3 — cuando OpenRouter dice "espera", hay que esperar

El 503 final traía este dato enterrado: `previous_errors` con 429 de
DeepInfra, Morph y Fireworks — `deepseek-v4.1-flash is temporarily
rate-limited upstream`. El modelo estaba saturado en TODOS los
proveedores a la vez. El backoff de `chat_json` (2-4 s) hacia que los
3 reintentos cayeran dentro de la misma racha de saturación.

- Nuevo `_es_rate_limit()`: 429, 503, "rate-limited", "too many
  requests" → backoff real: 15 s → 30 s → 60 s (máx).
- Se comprueba ANTES que los fallbacks de reasoning/schema (un 503 no
  es un rechazo de parámetros).
- `TIMEOUT_LLM` pasa a ser configurable por `.env`. La noche también
  mostró timeouts de 45 s sostenidos en v4.1-flash (lotes de 3
  fragmentos × 12000 car.): el `.env` entregado lo sube a 90. Si
  vuelves a ver "no respondió en Ns" seguidos, súbelo más.

## FIX 4 — HISPAGEN y las conexiones muertas

```
[!] HISPAGEN no respondió ('saenz Vitoria'): ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'
```

En TODAS las consultas de la noche. El servidor de hispagen.es cierra
las conexiones keep-alive (con `Connection: keep-alive` en la SESSION
compartida), y la petición siguiente viaja por un socket ya muerto.
`_get_hispagen()` reintenta UNA vez cerrando el pool (`SESSION.close()`
fuerza sockets nuevos) tras 2 s; si persiste, el fallo sube al cooldown
del conector como siempre. Nota honesta: si el sitio está CAÍDO de
verdad, seguirá fallando (y el cooldown hará su trabajo) — pero ya no
morirá por un socket reciclado.

## FIX 5 — el PDF que era una página web

```
invalid pdf header: b'<!DOC'
[!] pdf2image falló al convertir PDF: Unable to get page count.
```

`Listado_Registro_EASA_DO_STS-ES.pdf` devolvía HTML. Como la URL
acababa en `.pdf`, la cascada lo trataba como PDF: pypdf fallaba en
silencio y pdf2image reventaba con un error que apuntaba a poppler (que
estaba perfectamente instalado). Un PDF real empieza SIEMPRE por
`%PDF-`: ahora, si el contenido es HTML, se procesa como página web y
se recupera su texto.

## FIX 6 — los 500 de llama.cpp y la URL que no existía

Dos detalles en el OCR local:

1. `max_tokens: 8192` == contexto total (`-c 8192`): 0 tokens para
   imagen+prompt → `500 Server Error` en páginas sueltas (la 1/30 del
   CCEP-Web). Ahora 4096: una página de partida rara vez pasa de
   ~2000-3000 tokens y la imagen tiene la mitad del contexto libre.
2. El error se recortaba a 80 caracteres y el log mostraba
   `http://localhost:8080/v1/chat/c` — parecía un endpoint roto cuando
   era solo el recorte. Ahora 120: la URL sale completa.

## FIX 7 — el test que reventaba con tu propio .env

`test_config_llamacpp_por_defecto` exigía
`OCR_LLAMACPP_TIMEOUT_INFERENCIA == 60.0`, pero tu `.env` pone 600 (lo
cual es legítimo). Ahora los defaults se comprueban SOLO si el `.env`
no los pisa; con override, se valida que sea un número > 0.

## FIX 8 — `resumen_noche.py` por fin existe

```
C:\...>python resumen_noche.py
python: can't open file '...resumen_noche.py': [Errno 2] No such file or directory
```

Creado: resumen 100% offline (0 tokens, 0 red) de la última tanda —
corpus por origen, hallazgos por nivel de evidencia con citas
verificadas, árbol (líneas y hasta qué año están confirmadas), frontera
pendiente, estado de las cachés (lo que NO se repetirá) y siguiente
paso recomendado. También es la **opción 14** del menú del lanzador.
Avisa si la última consolidación quedó PENDIENTE.

---

## Resumen de archivos tocados

| Archivo | Cambio |
|---|---|
| `scrapers/archivos.py` | FIX 1: los 5 conectores reciben `(objetivo, conn)` |
| `agent/fase2.py` | FIX 2: consolidación con degradación elegante |
| `main.py` | FIX 2: red de seguridad en el bucle de ciclos + versión |
| `utils/llm.py` | FIX 3: `_es_rate_limit()` + backoff 15/30/60 s |
| `config.py` | FIX 3: `TIMEOUT_LLM` por `.env` |
| `.env` / `.env.example` | FIX 3: `TIMEOUT_LLM=90` documentado |
| `scrapers/hispagen.py` | FIX 4: `_get_hispagen()` con reintento limpio |
| `scrapers/web.py` | FIX 5: HTML-bajo-.pdf como web · FIX 6: max_tokens 4096 + error 120 |
| `resumen_noche.py` | FIX 8: NUEVO, resumen offline |
| `lanzador.py` | FIX 8: opción 14 + versión 10.2 |
| `tests/test_fixes_v102.py` | NUEVO: 14 tests de regresión (uno por fix + integración) |
| `tests/test_llamacpp_v90.py` | FIX 7: robusto ante .env |
| `tests/test_fuentes_v91.py` | actualizado al helper `_get_hispagen` |
| `tests/test_lanzador_v90.py` | actualizado a 14 opciones / v10.2 |
| `README.md` | sección v10.2 completa |

## Qué esperar en la SIGUIENTE ejecución

1. **Saldrán datos de SIGA/ADDO/Ensenada** (o sus NOTAS NEGATIVAS
   fundadas): por primera vez desde la v9.1 los tres conectores van a
   consultar de verdad. La caché de conectores tiene 26 consultas
   previas — las que HISPAGEN/FamilySearch ya hizo no se repiten.
2. **Un LLM caído ya no tira la casa**: verás el aviso claro
   "Consolidación LLM no disponible... NO se pierde nada" y el
   proceso seguirá hasta el GEDCOM.
3. **Los rate-limits esperan 15-60 s** antes de rendirse (verás
   "saturado (rate-limit upstream); esperando Ns").
4. Al terminar (o al levantarte), `python resumen_noche.py` o la
   opción 14 del menú te dan el parte de la noche en un minuto.
