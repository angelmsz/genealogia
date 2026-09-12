# INFORME DE LA REVISIÓN DE FONDO v9.1 — honestidad total

Proyecto: genealogia_v9_0 → **v9.1**
Fecha: 2026-09-11
Entorno de verificación: sandbox Linux (Python 3.12.14, pytest 9.0.2) sobre
TU zip real `genealogia_v9_0 (1).zip` (subido 2026-09-10 10:13).

---

## 1. Recuento EXACTO de tests (lo que pediste, con número exacto)

**SUITE COMPLETA: 170 tests, 170 PASSED, 0 FAILED, 0 SKIPPED** (13,6 s)

| Suite | Tests | Estado |
|---|---|---|
| test_auditoria.py | 6 | original, intacto |
| test_commit.py | 6 | original, intacto |
| test_conectores_v43.py | 12 | original, intacto |
| test_extraccion_v43.py | 7 | 4 originales + **3 añadidos v9.1** |
| test_frontera.py | 5 | original, intacto |
| test_frontera_v43.py | 3 | original, intacto |
| **test_fuentes_v91.py** | 28 | **NUEVO** (Partes A–D) |
| test_gasto.py | 6 | original, intacto |
| test_gedcom.py | 7 | original, intacto |
| test_integracion_v43.py | 1 | original, intacto (autopiloto end-to-end) |
| test_lanzador_v90.py | 15 | original, intacto |
| test_llamacpp_v90.py | 18 | original, intacto (cascada OCR no tocada) |
| **test_nivel_evidencia.py** | 28 | **NUEVO** (PARTE 0) |
| test_personas.py | 9 | original, intacto |
| test_plausibilidad.py | 10 | 7 originales + **3 añadidos v9.1** |
| test_probar_ocr_v90.py | 9 | original, intacto |
| **TOTAL** | **170** | **108 base + 62 nuevos** |

La BASE se midió ANTES de tocar nada: **108 passed, 0 failed** (tras
instalar en el sandbox `openai` y `tavily-python`, que faltaban aquí pero
tú ya los tienes según requirements.txt). Los 108 originales no se han
tocado: solo se han AÑADIDO tests a test_extraccion_v43.py y
test_plausibilidad.py, como pediste.

La cascada OCR v9.0 (pypdf → RapidOCR/olmOCR-2 → Gemini) NO se ha
modificado: los 27 tests de OCR/llama.cpp siguen verdes sin cambios de
código en utils/llm.py ni en el flujo de `_probar_ocr`.

---

## 2. PARTE 0 — El arreglo de MÉTODO (hecho y verificado)

Caso estrella reproducido en test: **716 hallazgos → 1 confirmado +
715 coincidencia_debil**, separados automáticamente.

Qué se implementó (exactamente lo especificado):

1. `nivel_evidencia` en el schema de hallazgos (`config.py`,
   `_PROPS_HALLAZGO`) con los 3 valores exactos: `confirmado`,
   `candidato_fuerte`, `coincidencia_debil` (+ `datos_que_casan`).
2. Prompt `SYSTEM_PROMPT_HALLAZGOS` (config.py) con la clasificación
   explícita y la justificación de los 2+ datos independientes. El ancla
   "genealogista experto analizando" se mantiene (los tests v4.x
   despachan por esa frase).
3. **Clasificador DETERMINISTA** (`agent/evidencia.py`) que prevalece
   sobre el LLM: recalcula y sobrescribe el nivel de TODOS los hallazgos
   (también los de caché pre-v9.1). Guardes: contradicción de fechas
   (tocayo) → débil; comparación NO circular de lugares (el lugar del
   hallazgo se compara contra el municipio CONOCIDO de la ficha) y de
   padres/cónyuge (el nombre de `otros_nombres` no puede ser el propio).
4. Las 3 secciones separadas ("Confirmado documentalmente" / "Candidatos
   fuertes a verificar" / "Coincidencias débiles (descartables salvo
   nueva evidencia)") en:
   - el `resumen_general` del árbol (bloque determinista prefijado +
     clave estructurada `resumen_evidencia`),
   - `generar_informe_progreso()` (agent/frontera.py, informe_progreso.md).
5. COMMIT: los `coincidencia_debil` ya NO entran al árbol (los hallazgos
   pre-v9.1 sin el campo siguen siendo elegibles: compatibilidad).
6. `reclasificar_arbol()` en evidencia.py: reclasifica tu
   `arbol_refinado.json` de 716 hallazgos SIN re-ejecutar fase 2.
7. Tests: 28 nuevos en test_nivel_evidencia.py + 3+3 añadidos a
   extracción/plausibilidad.

Dónde se toca el pipeline de fase 2 (nada de lo anterior se rompe):
paso nuevo "2/6 Clasificación de nivel de evidencia" entre la asociación
de ids y la plausibilidad, con reclasificación tras la plausibilidad
(los homónimos marcados demuestran); post-proceso determinista del
consolidado antes de escribir arbol_refinado.json.

---

## 3. PARTES A–D — Fuentes nuevas, con honestidad de acceso

### PARTE A — FamilySearch: HECHA con estados explícitos

**Verificado EN VIVO (dos sesiones)**: el catálogo
`/search/catalog/results?placeSearch=Vitoria` redirige a
"Sign-in to your account" → **el catálogo exige login**. Existe API
OAuth 2.0 oficial (developers.familysearch.org) pero requiere registrar
una aplicación: no es viable para un particular sin trámite.

Implementado (`scrapers/familysearch.py`, 573 líneas):
- Login dual: `FAMILYSEARCH_USER`/`FAMILYSEARCH_PASS` del .env (login
  programático) **o** `FAMILYSEARCH_COOKIE` (pegas tu cookie de sesión
  del navegador: el camino REALISTA si piden captcha/2FA).
- Catálogo por LOCALIDAD (placeSearch) para tus municipios: Vitoria,
  Castrejón de la Peña, Pobladura del Valle, Coreses (más variantes
  normalizadas; el conector es general para cualquier municipio).
- Libros listados con rangos de fechas ANTES de leer nada; parser
  tolerante con estados: `libro_online` / `libro_restringido` /
  `indice_solo` / `desconocido` (nunca descartado en silencio).
- Informe: online → URL del visor para descarga manual → cascada OCR;
  restringidos → **"pendiente — requiere Centro de Historia Familiar"**
  (texto exacto de tu especificación).
- Rate limiting: máx 1 request cada 3–5 s (FAMILYSEARCH_DELAY) con
  backoff exponencial ante 429/5xx (RateLimiter con reloj inyectable,
  testeado sin dormir).
- Credenciales: NUNCA logueadas (test específico que captura los logs y
  busca el secreto), NUNCA en cache_agente.db (claves de caché solo con
  el municipio: test específico).

**HONESTIDAD**: la descarga AUTOMÁTICA de imágenes de libros NO está
implementada: van detrás del visor autenticado con tokens por grupo de
imágenes y sus términos de uso. Simularla sería "un conector que falla
en silencio". Lo que hay: listado + URL del visor + instrucción de
descarga manual → cascada OCR (tu flujo `--probar-ocr` / importar
propios). Además, desde ESTE sandbox FamilySearch devuelve 403
(Cloudflare bloquea la IP del sandbox): el login programático está
implementado según el formulario observado pero **no he podido probarlo
contra la web real desde aquí** — está cubierto por tests offline con
los HTML observados; desde tu máquina (IP residencial española) es
donde se validará de verdad.

### PARTE B — Catastro de Ensenada, Respuestas Particulares: HECHA como solicitud

**Verificado EN VIVO**: portal de Archivos de Castilla y León
(archivoscastillayleon.jcyl.es) respondiendo; páginas reales del AHP
Zamora (C/ Rúa de los Francos) y AHP Palencia (C/ Niños del Coro)
descargadas hoy; SIEGA es el catálogo en línea pero las Respuestas
Particulares NO están digitalizadas en ningún portal público estable.

- Zamora y Palencia → estado `requiere_solicitud_escrita`: **el sistema
  `--solicitudes` existente genera ahora también las 3 peticiones por
  escrito** (Castrejón de la Peña, Pobladura del Valle, Coreses) con
  texto formal listo para enviar, añadidas a solicitudes.json/md de
  forma idempotente.
- **CORRECCIÓN FACTUAL (importante)**: Álava quedó EXCLUIDA del Catastro
  de Ensenada (régimen foral, ya estaba en PROVINCIAS_SIN_ENSENADA del
  proyecto): NO existen Respuestas Particulares para Vitoria. El informe
  lo dice con estado `no_existe` y alternativas reales (IRARGI
  sacramentales sin login + protocolos AHP Álava + padrones municipales
  de Vitoria), y NO se genera solicitud para Vitoria (no pediría algo
  que no existe).

### PARTE C — `archivos_a_consultar_in_situ`: HECHA

`informe_fase1.json` pasa de lista a dict:
`{"objetivos": [...], "archivos_a_consultar_in_situ": [...],
"familysearch": {...}, "catastro_ensenada_particulares": {...}}`.

La sección se genera automáticamente desde familia_conocida.json
(verificado end-to-end con tus 4 municipios): AHP + Archivo Municipal
por municipio, con las URLs verificadas hoy (portal CyL para Zamora y
Palencia, web.araba.eus para Álava con tel. 945 18 19 27 y
archivo@araba.eus), fondos a buscar (capitulaciones, testamentos,
vecindarios, quintas, padrones) y la marca "requiere visita o solicitud
postal" donde no hay catálogo — nunca como "fuente fallida". Compatibilidad: lanzador.py actualizado para el formato dict.

### PARTE D — HISPAGEN: HECHA (con una advertencia de red)

Conector `scrapers/hispagen.py` (patrón SIGA): búsqueda por
apellido+municipio vía com_search de Joomla (GET `searchword`,
verificado en vivo en la sesión anterior con HTTP 200), tokens para
apellidos compuestos, caché/cooldown/dedup como el resto, agrupación de
filas en documentos.

**HONESTIDAD**: desde ESTE sandbox hispagen.es es inalcanzable
(bloqueo de salida de red), y además el subdominio www tiene el
certificado mal emitido (ERR_CERT_COMMON_NAME_INVALID, observado hoy):
el conector usa `verify=False` (como SIGA/ADDO) y está probado offline
con HTML grabado. Desde tu máquina debería funcionar; si no, quedará en
cooldown y registrado (no "consulta hecha").

---

## 4. Diff completo

`v91_completo.diff` (3.045 líneas): 6 archivos NUEVOS + 8 MODIFICADOS,
ninguno eliminado.

NUEVOS:
- agent/evidencia.py (clasificador determinista, PARTE 0)
- scrapers/familysearch.py (PARTE A)
- scrapers/archivos_provinciales.py (PARTES B+C)
- scrapers/hispagen.py (PARTE D)
- tests/test_nivel_evidencia.py (28 tests)
- tests/test_fuentes_v91.py (28 tests)

MODIFICADOS:
- config.py (schema + prompt + constantes + env vars + URLs AHP verificadas)
- agent/fase2.py (paso 2/6 de clasificación + meta + post-proceso del árbol)
- agent/frontera.py (commit filtra débiles + propaga nivel + informe 3 secciones)
- scrapers/archivos.py (recolectar + probar_conectores con los 2 nuevos)
- main.py (informe_fase1 dict + --solicitudes con Ensenada)
- lanzador.py (comentario + resumen del formato nuevo)
- tests/test_extraccion_v43.py (+3 tests)
- tests/test_plausibilidad.py (+3 tests)

## 5. Bugs propios encontrados por los tests durante el desarrollo

(Honestidad también conmigo mismo):
1. `SIN_RESULTADOS` de HISPAGEN era una cadena concatenada: `any(... for
   marca in CADENA)` iteraba CARACTERES y cualquier letra del HTML casaba
   → siempre "sin resultados". Corregido a lista + test de regresión.
2. Selector doble de BeautifulSoup (.result-item + div[class*=result])
   devolvía el mismo bloque dos veces → filas duplicadas. Dedup por URL.
3. Tokens de apellido ordenados alfabéticamente perdían la posición del
   nombre de pila ("Nazario Merillas" → tomaba "nazario" como apellido).
4. URLs del AHP de mi primer borrador (rutas /web/jcyl/AHPZamora/es)
   devuelven 400: reemplazadas por las verificadas hoy.
5. La premisa de 2 tests míos era incorrecta (ficha sin fecha ni
   municipio): corregidos los tests, no el clasificador (que actuó bien).

## 6. Qué NO está automatizable (resumen ejecutivo)

| Fuente | Estado | Por qué |
|---|---|---|
| FamilySearch: descarga automática de imágenes | NO automatizada | visor autenticado + ToS; queda URL del visor + descarga manual → cascada OCR |
| FamilySearch: login programático | Implementado, SIN verificación en vivo posible desde el sandbox | Cloudflare 403 a la IP del sandbox; usar FAMILYSEARCH_COOKIE como camino realista |
| Respuestas Particulares Zamora/Palencia | NO digitalizadas | petición por escrita generada (--solicitudes), URLs y direcciones verificadas |
| Respuestas Particulares Vitoria | NO EXISTEN | Álava excluida del Catastro de Ensenada |
| HISPAGEN desde el sandbox | inalcanzable aquí | bloqueo de red del sandbox; conector probado offline con HTML grabado |
| AHP Álava protocolos | catálogo parcial (IRARGI online para sacramentales) | el resto: email archivo@araba.eus verificado, queda en el informe |

Todo lo anterior queda como entrada EXPLÍCITA en el informe, con estado
y detalle — nunca como fuente fallida en silencio.
