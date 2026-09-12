# INFORME DE REVISIÓN v9.2 — Reclasificar gratis + DeepSeek V4.1 Flash

Fecha: 2026-09-11 · Sobre: genealogia_v9.1 (170 tests verdes)
Resultado: **182 tests, 0 fallos** · Diff completo: `v92_completo.diff` (972 líneas)

---

## PARTE 1 — `--reclasificar` conectado a un comando real

**El problema**: `agent/evidencia.py::reclasificar_arbol()` existía desde
la v9.1 pero no estaba conectada a ningún comando: para usarla había que
escribir código. Ahora es un comando de verdad.

### Qué se cambió

| Archivo | Cambio |
|---|---|
| `main.py` | Nueva función `reclasificar_comando()` + flag `--reclasificar` + dispatch en la sección de "modos que no gastan tokens ni red" |
| `lanzador.py` | Opción **13** del menú: "Reclasificar hallazgos existentes [gratis, no gasta tokens]" + `_opcion_13()` + conexión en `importar_proyecto()` |
| `tests/test_reclasificar_v92.py` | 5 tests nuevos (ver abajo) |

### Comportamiento exacto

1. Carga `arbol_refinado.json` y `arbol_hallazgos.json` **ya guardados**
   del directorio del proyecto (+ `familia_conocida.json` si existe).
   **Sin LLM, sin red, sin un token.**
2. Llama a `reclasificar_arbol()` (el clasificador determinista de la
   v9.1: regla de ≥2 datos independientes por generación).
3. Guarda el resultado **sobrescribiendo `arbol_refinado.json`**, tras
   dejar un backup `arbol_refinado.json.bak` con el contenido anterior
   exacto (verificado byte a byte en test).
4. Imprime el resumen: confirmados / candidatos fuertes / coincidencias
   débiles, más el nivelado de las personas_nuevas_candidatas.

Salida real capturada en la prueba end-to-end:

```
[✓] arbol_refinado.json reclasificado (backup del anterior en
    arbol_refinado.json.bak) — 0 tokens gastados
[⚙] Hallazgos (2 en arbol_hallazgos.json): confirmados: 1 |
    candidatos fuertes: 0 | coincidencias débiles: 1
[⚙] Personas nuevas candidatas: 1 (fuertes: 0, débiles: 1)
[⚙] Siguiente paso: --aceptar comete SOLO los confirmados al árbol.
```

### La garantía de "0 red, 0 LLM" está TESTEADA, no prometida

`tests/test_reclasificar_v92.py::test_reclasificar_cero_red_cero_llm`
**intercepta** `socket.socket.connect`, `socket.socket.connect_ex` y el
cliente LLM (`llm.chat.completions.create`): cualquier intento de
conexión o de llamada al modelo **lanza AssertionError y el test falla**.
También se comprueba que `GASTO.llamadas` y `GASTO.coste` no se muevan.
Otros 4 tests: backup exacto + sobrescritura, idempotencia (reclasificar
2 veces no acumula bloques ni cambia totales), ausencia limpia de árbol
(devuelve None → exit code 1, sin red), y humo del proceso real
(`python main.py --reclasificar` como subproceso, cableado del flag).

---

## PARTE 2 — FASE 2 con DeepSeek V4.1 Flash (vía OpenRouter)

### 1) Modelo por defecto

`config.py`:

```python
MODELO_FASE2 = os.getenv("MODELO_FASE2", "deepseek/deepseek-v4.1-flash")
```

Sigue siendo configurable por `.env` (no fijado a fuego). Existía desde
v9.0. Antes: `z-ai/glm-5.2`.

### 2) Precio REAL, verificado en vivo (fuente)

**Fuente: API pública de OpenRouter, `GET https://openrouter.ai/api/v1/models`,
consultada en vivo el 2026-09-11** (el modelo figura con fecha de creación
2026-09-10T06:21:25 UTC, contexto 1.048.576 tokens, modality text+image->text).

| Concepto | Precio por millón de tokens |
|---|---|
| Base (valle): entrada / salida | **$0.15 / $0.60** |
| **PUNTA** (laborables, 2 franjas UTC): entrada / salida | **$0.30 / $1.20** |
| Lectura de caché | $0.003 |

**OpenRouter SÍ cobra distinto en punta/valle** (campo `overrides` del
JSON de la API, en minutos UTC del día):

- Laborables UTC 00:00–01:40, 06:40–10:00 y 16:40–24:00 → tarifa base.
- **Laborables UTC 01:40–06:40 y 10:00–16:40 → el DOBLE** ($0.30/$1.20).
- Sábados y domingos (todo el día) → tarifa base.

Según tu instrucción, `PRECIO_MILLON_TOKENS` usa **el precio MÁS ALTO
($0.30 entrada / $1.20 salida)** con nota en el código: si la llamada
cae en valle el coste real será MENOR que el apuntado (nunca al revés);
el presupuesto nunca se infravalora. Comparativa: glm-5.2 hoy cuesta
$0.28/$0.88 — en valle V4.1 Flash es ~2× más barato; en punta, similar
(entrada algo más barata, salida algo más cara).

**Aprovechando la consulta en vivo, se re-actualizó TODA la tabla de
precios** (los valores anteriores eran del 2026-09-07): v4-flash había
subido casi al doble ($0.045→$0.087/$0.174 — afecta a FASE 1, que usa
ese modelo), v4-flash-0731 $0.065/$0.18, glm-5.3-flash al doble
($0.15/$0.50), glm-5.2 bajó ($0.28/$0.88), glm-4.6v y los Gemini sin
cambios. Sin esto, el control de gasto de la fase 1 quedaría a la mitad
del coste real.

### 3) `reasoning` en `chat_json()` (utils/llm.py)

- Nuevo interruptor en `config.py`: `REASONING_ACTIVADO`
  (`REASONING_ACTIVADO=true|false` en `.env`, **default false**).
- Con `true`, el payload lleva `reasoning: {"enabled": true}`. Con
  `false` (default), el payload es IDÉNTICO al de la v9.1 — ningún
  modelo notará diferencia (test específico).
- La API de OpenRouter lista `reasoning`, `reasoning_effort` e
  `include_reasoning` en `supported_parameters` de V4.1 Flash: el
  modelo lo soporta (verificado en vivo en la misma consulta).
- **No se rompe nada si un modelo no lo soporta**: si el provider
  devuelve 400 mencionando "reasoning", `chat_json()` lo desactiva para
  esa sesión (`_MODELOS_SIN_REASONING`, mismo patrón que el fallback de
  `response_format`) y reintenta sin él. El detector `_es_error_reasoning`
  se comprueba ANTES que `_es_error_schema` para que un 400 ambiguo no
  desactive el schema por culpa del reasoning (test específico).

### 4) `REASONING_ACTIVADO=false` por defecto — nota honesta

El razonamiento genera **tokens de salida adicionales que se cobran**
(más coste por extracción) y para una tarea de extracción estructurada
de campos con schema JSON probablemente no compensa. Documentado en
README, `.env.example` y `config.py`: *"actívalo solo si notas que la
extracción falla en casos complejos; por defecto está apagado para
minimizar coste"*.

### 5) `reasoning_details` multi-turno — NO aplica, y esta es la razón

La doc oficial de OpenRouter (docs/use-cases/reasoning-tokens, consultada
hoy) exige devolver **sin modificar** el bloque `reasoning_details` del
mensaje del asistente **al reenviar ese mensaje en turnos posteriores de
una misma conversación** (requisito sobre todo para tool calling: el
modelo retoma el razonamiento donde lo dejó).

**Nuestro caso**: `chat_json()` hace llamadas de **un solo turno por
extracción** — construye `[system, user]` fresco en cada llamada y
**jamás reenvía mensajes del asistente**. El único "multi-turno" interno
es el reintento de JSON roto, que solo AÑADE un mensaje de usuario sin
incluir la respuesta anterior del asistente. No hay bloque
`reasoning_details` que circular → **no hay nada que preservar ni que
implementar**. Implementarlo sería código muerto.

### 6) `.env.example`

Creado (no existía en el zip pese a que README y lanzador lo citaban):
claves API, los 3 modelos con los defaults nuevos, `REASONING_ACTIVADO`
con su nota, FamilySearch (user/pass/cookie), ADDO, OCR local completo y
`REINTENTO_HEADLESS`.

### 7) Control de presupuesto con precios nuevos

- `test_gasto.py` (v4.2) pasa intacto: estima coste cuando falta usage,
  mide cuando está, y `PresupuestoExcedido` salta igual.
- Nuevo test de regresión: con usage de 1000+200 tokens y precio punta,
  `Gasto.coste == 1000·0.30/1e6 + 200·1.20/1e6` exacto.
- Nuevo test: el default de fase 2 tiene entrada de precio (nunca cae al
  `PRECIO_POR_DEFECTO`) y nunca por debajo de la tarifa base (política
  anti-infravaloración documentada como test).

---

## Extras encontrados por el camino (honestidad de alcance)

1. **`FAMILYSEARCH_COOKIE` estaba documentada pero NO cableada**: seis
   mensajes del código y el README la citan como variable de `.env`,
   pero nadie la leía del entorno (había que pasarla por parámetro).
   Arreglado: `SesionFamilySearch.__init__` ahora hace
   `os.getenv("FAMILYSEARCH_COOKIE", "")`. Los 28 tests de fuentes v9.1
   siguen verdes.
2. **La tabla de precios tenía 4 días**: v4-flash (modelo de FASE 1)
   casi al doble del precio apuntado → el GASTO de la fase 1 quedaba
   infravalorado a la mitad. Corregido con los valores de hoy.

## Qué NO se ha verificado (honestidad)

- **No se ha hecho una llamada real de pago a V4.1 Flash** desde el
  sandbox (no hay clave de OpenRouter aquí): el payload de `reasoning`
  sigue el formato documentado por OpenRouter y el soporte del modelo
  está confirmado en `supported_parameters`, pero la primera extracción
  real contigo ya nos dirá si el provider acepta
  `response_format`+`reasoning` a la vez (si no, el fallback automático
  lo gestiona y queda registrado en el log como warn, no como fallo).
- Las franjas punta/valle se transcriben tal cual las devuelve la API
  (minutos UTC de lunes a viernes); si OpenRouter las refina, la
  política de "precio máximo" sigue siendo segura por construcción.
- El resto de la cascada OCR (27 tests) no se ha tocado: sigue intacta.

## Recuento final de tests

| Suite | v9.1 | v9.2 |
|---|---|---|
| Base original (v4.x–v9.0) | 108 | 108 |
| Nivel evidencia + fuentes v9.1 | 62 | 62 |
| **Reclasificar v9.2** (nuevo) | — | **5** |
| **Reasoning/precios/gasto v9.2** (nuevo) | — | **7** |
| **TOTAL** | **170** | **182 PASSED / 0 FAILED** |

(`test_menu_tiene_todas_las_opciones` se actualizó de 12 a 13 opciones:
el menú cubre un flag MÁS que antes — es el reflejo de la funcionalidad
nueva, no una rotura.)
