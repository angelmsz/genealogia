# Agente de investigación genealógica v10.2

Refactor modular del monolito `agente_genealogia.py` (2000 líneas) con las
mejoras v4.0 (timeout duro radical, UI enriquecida, compartimentación,
estrategias genealógicas nuevas), las mejoras v4.1, las correcciones v4.2,
las correcciones v4.3 (verificación en vivo + test de integración), las
dos grandes novedades **v9.0**, la **revisión de fondo v9.1**, la v9.2, la
**v10.0 (OCR 100% local)** y la **v10.1 (llama.cpp multi-modelo)**:

- **Lanzador con menú interactivo** (`lanzador.py` / `lanzador.ps1`):
  todas las opciones del agente en un menú numerado, con valores por
  defecto sensatos, el comando equivalente visible antes de ejecutar
  (aprendes los flags sin memorizarlos), el gasto de la sesión siempre
  a la vista y Ctrl+C sin tracebacks feos.
- **v10.1 — llama.cpp MULTI-MODELO**: el backend `llamacpp` ahora sirve
  tres familias de modelo OCR con el mismo cliente HTTP — GLM-OCR (0.9B,
  nuevo default: español explícito, ~2.5 GB VRAM), HunyuanOCR-1.5 (SOTA,
  100+ idiomas) y olmOCR-2-7B (especialista en manuscrito anterior a
  1800, comportamiento v10.0 exacto) — vía `OCR_LLAMACPP_FAMILIA` en
  `.env`. Cada familia aporta su prompt, su reescalado y su limpieza;
  la caché OCR es coherente con la familia (`llamacpp-<familia>`), y las
  garantías v10 quedan intactas (0 nube, aviso único, transitorios sin
  cachear).
- **v10.0 — OCR 100% LOCAL**: Gemini y cualquier VLM de la nube salen
  de TODO el OCR y la transcripción de imágenes. Cascada nueva:
  pypdf → OCR local (llama.cpp vía llama-server si
  `OCR_BACKEND=llamacpp`; si no RapidOCR; PaddleOCR legacy) → fallo
  EXPLÍCITO (backend `ocr_local_fallido`). Los manuscritos solo se
  transcriben con el OCR local de llama.cpp o fallan igual de explícito;
  los fallos transitorios (servidor caído, motor no instalado) NO se
  cachean. El LLM de TEXTO (fases 1-2, OpenRouter) no es OCR y no
  cambia. `--importar-propios` también pasa a OCR local (documento
  pendiente con aviso si no hay motor, sin crash).
- **v10.0 — Salto de generación robusto**: el parser de padres entiende
  tres formatos ("Nombre (padre)", "hijo de X y Y" en otros_nombres y,
  como último recurso, en la cita literal); ante dos candidatos
  distintos para el mismo rol NO crea la ficha y avisa (nunca adivina).
- **v10.0 — SIGA sin fallback silencioso**: un municipio fuera del mapa
  de localidades de SIGA ya NO se busca en Vitoria por defecto: la
  consulta se omite con aviso y no se cachea.
- **v10.0 — .gitignore** desde el primer commit: claves (.env) y datos
  familiares NUNCA al repositorio.
- **OCR local con llama.cpp + Vulkan para GPU AMD (multi-modelo desde
  v10.1)**: si tu tarjeta no tiene soporte ROCm (RX 6700 XT), puedes
  transcribir manuscritos e impresos EN LOCAL, gratis y en tu propia GPU,
  con un llama-server que habla la API de OpenAI (GLM-OCR, HunyuanOCR-1.5
  u olmOCR-2, a elegir con OCR_LLAMACPP_FAMILIA).

Y todo lo de la v4.3 sigue igual (no se ha roto nada):

- **Instala en Windows**: RapidOCR (ONNX) sustituye a PaddleOCR, que no
  llegaba a instalar con Python moderno.
- **El bucle cierra de verdad**: la plausibilidad biológica solo marca
  sospecha ante contradicciones REALES (la falta de fechas de los padres
  ya no congela el árbol), las evidencias no se duplican en cada commit y
  el freno del autopiloto ("ciclo sin aportes -> paro") vuelve a funcionar.
- **La evidencia es fiable**: las citas se verifican contra el TEXTO
  ORIGINAL de cada página (guardado en el corpus), no contra el resumen
  que hizo el LLM de filtrado.
- **Ids estables de persona** (P0001...): abuelo y nieto tocayos ya no
  se funden en una sola ficha; 'Isidro Merillas' casa con 'Isidro
  Merillas Panero' vía matching difuso.
- **Seguridad de datos**: backups con marca de tiempo y rotación
  (`backups/`) + registro append-only (`registro_confirmaciones.jsonl`)
  que solo crece.
- **Gasto bajo control**: una sola búsqueda Tavily por consulta (con
  `include_domains`), reintentos LLM limitados (6 máx. en vez de 16) y
  coste ESTIMADO cuando el provider no devuelve usage (el tope de
  `--presupuesto-max` salta siempre).
- **GEDCOM 5.5.1 válido**: SUBM, URL a nivel 1 (WWW/PUBL), citas con
  CONC, líneas dentro del tope de 255 caracteres.
- **Manuscritos**: los PDFs de PARES/SIGA/ADDO solo se transcriben con
  el OCR local de llama.cpp (`OCR_BACKEND=llamacpp`; familia
  glm-ocr/hunyuan/olmocr2) o fallan de forma explícita (v10.0: nunca a
  la nube).
- **232 pruebas automáticas** (`python -m pytest tests/ -q`)
  que vigilan todas estas garantías (46 de la v4.2 + 20 de la v4.3 + 42
  de la v9.0 + 62 de la v9.1 + 12 de la v9.2 + 26 de la v10.0 + 24 de la
  v10.1: OCR 100% local con 0 llamadas a la nube, salto de generación en
  3 formatos, SIGA sin fallback y llama.cpp multi-modelo por familia).

## Inicio rápido (v9.0)

La forma más fácil de usar el agente es el **lanzador con menú**:

```
Windows:   doble clic en lanzador.ps1   (o en una terminal: python lanzador.py)
Linux/Mac: python3 lanzador.py
```

El menú cubre TODAS las opciones del agente (investigación completa,
fases sueltas, autopiloto, filtrar por personas, solicitudes, Ensenada,
diagnóstico, conectores, importar fotos, commit del árbol). Al arrancar
hace un chequeo silencioso (equivalente a `--diagnostico`, sin gastar
tokens) y te resume en una línea si está todo OK; antes de cada acción
que gasta dinero te enseña el **comando equivalente** de `main.py` y te
pide confirmación. Salir del menú es con la opción `0`.

> Consejo: si el chequeo de arranque te parece lento en tu máquina
> (arranca RapidOCR y Chromium), usa `python lanzador.py --sin-chequeo`
> y la opción 9 cuando quieras el diagnóstico completo.

Los comandos de `main.py` (ver "Uso" en el docstring de main.py y la
sección de abajo) siguen funcionando exactamente igual: el lanzador
llama internamente a las mismas funciones, no reemplaza nada.

## Menú interactivo — lanzador.py (v9.0, PARTE B)

Qué hace cada opción (el flag de `main.py` equivalente entre paréntesis):

| Opción | Flag equivalente | Gasta tokens |
|--------|------------------|--------------|
| 1. Investigación completa (fase 1 + 2) | (default / `--fase all`) | sí |
| 2. Solo fase 1 (búsqueda) | `--fase 1` | sí |
| 3. Solo fase 2 (refinado + GEDCOM) | `--fase 2` | sí |
| 4. Ver frontera priorizada | `--frontera` | no |
| 5. Autopiloto: N ciclos completos | `--ciclo N` | sí |
| 6. Filtrar por personas concretas | `--personas "A,B"` | sí |
| 7. Generar solicitudes de partidas | `--solicitudes` | no |
| 8. Hipótesis Catastro de Ensenada | `--ensenada` | algunos |
| 9. Diagnóstico completo | `--diagnostico` | solo con ping |
| 10. Probar conectores en vivo | `--probar-conectores` | no (solo red) |
| 11. Importar fotos propias | `--importar-propios` | no (OCR local, gratis) |
| 12. Aceptar verificados (commit) | `--aceptar` | no |
| 13. Reclasificar hallazgos existentes | `--reclasificar` | no (0 tokens, 0 red) |

Detalles de uso:

- **Valores por defecto**: Enter acepta el valor entre corchetes
  (`¿Cuántos ciclos? [3]:`, `¿Presupuesto máximo en $ ... [2.0]`).
- **Presupuesto**: se pregunta antes de cada acción que gasta; con `sin`
  no hay tope (comportamiento por defecto de `main.py`). El gasto de la
  cabecera es el de la SESIÓN del menú (todas las acciones comparten
  proceso, así que va acumulando; el control de gasto contabiliza en
  dólares, como siempre).
- **Opción 6**: lee `familia_conocida.json`, lista las personas numeradas
  y puedes elegir `1,3,5` (o rangos `2-4`) en vez de escribir los nombres.
- **Después de cada acción** vuelves al menú y se muestra un resumen de
  qué ficheros se han generado/actualizado y cuánto han crecido (p. ej.
  `arbol_hallazgos.json (hallazgos: 3 -> 15, +12)`).
- **Ctrl+C** en cualquier punto: "Cancelado. Volviendo al menú." — sin
  traceback. El progreso ya está guardado por las paradas seguras de
  las propias fases.
- `lanzador.ps1` (Windows) no contiene lógica: localiza Python, fuerza
  UTF-8 en la consola y llama a `lanzador.py` para poder hacer doble clic.

## OCR local con llama.cpp + Vulkan — multi-modelo (v9.0 PARTE A, v10.1)

**Para quién**: si tu GPU AMD no tiene soporte ROCm oficial (RX 6700 XT,
gfx1031/RDNA2, 12 GB) y PaddleOCR nunca usó tu GPU de verdad. llama.cpp
compilado con **Vulkan** funciona en tarjetas AMD de consumo sin ROCm y,
desde la v10.1, sirve **tres familias de modelo de OCR** con el mismo
cliente HTTP (todas entrenadas específicamente para transcribir
documentos escaneados, impresos **y manuscritos**):

| Familia (`OCR_LLAMACPP_FAMILIA`) | Modelo | Arranque | Para qué |
|---|---|---|---|
| `glm-ocr` (**default v10.1**) | GLM-OCR 0.9B (ggml-org/GLM-OCR-GGUF, Q8_0 ~1 GB + mmproj; ~2-2.5 GB VRAM) | `llama-server -hf ggml-org/GLM-OCR-GGUF` | #1 OmniDocBench v1.5 (94.62), manuscrito 87.0, español EXPLÍCITO entre sus 8 idiomas, el más rápido (decoding MTP). **Recomendado: español y manuscrito 1800-1930** |
| `hunyuan` | HunyuanOCR-1.5 de Tencent (~0.5-1B, ggml-org/HunyuanOCR-GGUF) | `llama-server -hf ggml-org/HunyuanOCR-GGUF` | SOTA OmniDocBench v1.6 (94.74), 100+ idiomas, mejora "ancient-script". Alternativa general |
| `olmocr2` | olmOCR-2-7B (GGUF Q4_K_M + mmproj F16, ~7 GB VRAM; el cableado original de la v9.0) | comando manual GGUF+mmproj (ver abajo) | Especialista en **manuscrito histórico anterior a 1800** (entrenado con ellos). Comportamiento v10.0 exacto |
| `generico` | cualquier otro modelo multimodal | el que tú uses | prompt propio vía `OCR_LLAMACPP_PROMPT` (si falta, aviso y usa el de hunyuan) |

NOTA honesta sobre HunyuanOCR: existe un fork de llama.cpp con DFlash
(decodificación especulativa) para este modelo; tiene bugs conocidos sin
mergear. NO lo uses: solo el llama.cpp comunitario oficial.

Con el servidor arrancado, la cascada de OCR queda (v10.0 — 100% local):

```
pypdf  ->  OCR local (glm-ocr/hunyuan/olmocr2, gratis, privado)  ->  fallo explícito
```

y los PDFs de dominios de manuscritos (PARES/SIGA/ADDO) van por el MISMO
camino local. Si el servidor no está arrancado: un único aviso y el PDF
queda SIN TEXTO — v10.0 NO escala a ningún VLM de la nube. El resultado se
cachea en SQLite (`ocr_cache`) con backend `llamacpp-<familia>`; un fallo
de servidor NO se cachea (se reintenta en cuanto lo arranques), y una
entrada de otra familia tampoco se reutiliza al cambiar de familia.

### 0) Qué modelo elegir por tipo de documento

| Tipo de documento | Recomendación |
|---|---|
| Impreso (libros modernos, BOE, prensa) | RapidOCR (`OCR_BACKEND=rapidocr`, default): más ligero que cualquier LLM de visión |
| Manuscrito 1800-1930 (registros civiles, partidas de época moderna) | `glm-ocr` (español explícito, rápido, ~2.5 GB VRAM) |
| Manuscrito anterior a 1800 (libros parroquiales del Antiguo Régimen) | `olmocr2` (entrenado específicamente con manuscritos históricos) |
| Alternativa general / otros idiomas | `hunyuan` (100+ idiomas) |

**A/B con partidas reales**: la forma honesta de elegir es comparar el
MISMO PDF contra cada servidor con `--probar-ocr` (la cabecera muestra la
familia activa, el prompt exacto que se va a enviar y el modelo que el
servidor dice servir):

```bash
# Arranca el servidor de GLM-OCR y prueba tu partida más difícil
OCR_LLAMACPP_FAMILIA=glm-ocr python main.py --probar-ocr partida.pdf --manuscrito --sin-cache
# Arranca ahora el servidor de HunyuanOCR y repite
OCR_LLAMACPP_FAMILIA=hunyuan python main.py --probar-ocr partida.pdf --manuscrito --sin-cache
# Y con olmOCR-2 (comando manual de abajo)
OCR_LLAMACPP_FAMILIA=olmocr2 python main.py --probar-ocr partida.pdf --manuscrito --sin-cache
```

(`--sin-cache` es importante en el A/B: además, la caché OCR es coherente
con la familia — una entrada escrita con una familia no se reutiliza con
otra —, así que cada prueba vuelve a inferir de verdad.) Compara el texto
extraído de cada una y quédate con el que mejor lea TU escritura; el
`registro_confirmaciones.jsonl` y el corpus agradecen la diligencia.

**Nota Vulkan**: si al arrancar `llama-server` aparece el warning
`CLIP graph uses unsupported operators`, la codificación de la imagen irá
más lenta (parte en CPU): es una limitación del backend Vulkan de
llama.cpp con tu GPU/driver, NO un fallo del agente. Repórtalo si abres
una issue de llama.cpp, pero el OCR sigue funcionando.

### 1) Compilar llama.cpp con Vulkan (una sola vez)

```bash
git clone https://github.com/ggml-org/llama.cpp
cd llama.cpp
cmake -B build -DGGML_VULKAN=ON
cmake --build build --config Release -j
```

Requisitos: `git`, `cmake` y un compilador (Linux: build-essential;
Windows: Visual Studio Build Tools + el
[Vulkan SDK](https://vulkan.lunarg.com/sdk/home) con la variable de
entorno `VULKAN_SDK` configurada). No se instala nada más: el binario
queda en `build/bin/llama-server` (en Windows,
`build\bin\Release\llama-server.exe`).

### 2) Arrancar el servidor: GLM-OCR o HunyuanOCR (la vía fácil, v10.1)

Con `-hf` el propio servidor descarga el modelo solo (1-2 GB a disco) y
aplica el chat template correcto automáticamente:

```bash
# GLM-OCR (0.9B, ~2.5 GB VRAM; recomendado para español y 1800-1930)
./build/bin/llama-server -hf ggml-org/GLM-OCR-GGUF -ngl 99 --host 0.0.0.0 --port 8080

# HunyuanOCR-1.5 (alternativa SOTA, 100+ idiomas)
./build/bin/llama-server -hf ggml-org/HunyuanOCR-GGUF -ngl 99 --host 0.0.0.0 --port 8080
```

Si cargas GGUFs a mano en lugar de `-hf`, consulta
`docs/multimodal.md` de llama.cpp por si el modelo que usas exige
`--chat-template <nombre>` (con `-hf` no hace falta: lo aplica él solo).

### 3) Arrancar el servidor: olmOCR-2-7B (manuscrito anterior a 1800)

olmOCR-2-7B es el más grande de los tres (~7 GB VRAM con Q4_K_M) y el
único entrenado deliberadamente con manuscritos históricos. Los nombres
de fichero EXACTOS están verificados contra HuggingFace el 2026-09-10
(re-comprobados uno a uno vía su API, con tamaños: los tres repos existen
y esos son literalmente sus ficheros):

```bash
pip install -U "huggingface_hub[cli]"   # habilita huggingface-cli

# Opción recomendada (Q4_K_M, 4.7 GB + visor 1.35 GB ~ 6 GB de VRAM):
huggingface-cli download lmstudio-community/olmOCR-2-7B-1025-GGUF \
  --include "olmOCR-2-7B-1025-Q4_K_M.gguf" "mmproj-olmOCR-2-7B-1025-F16.gguf" \
  --local-dir ./models

# Alternativa Q5_K_M (5.4 GB, algo más de calidad; también cabe en 12 GB):
huggingface-cli download bartowski/allenai_olmOCR-2-7B-1025-GGUF \
  --include "allenai_olmOCR-2-7B-1025-Q5_K_M.gguf" "mmproj-allenai_olmOCR-2-7B-1025-f16.gguf" \
  --local-dir ./models

# NOTA honesta: richardyoung/olmOCR-2-7B-1025-GGUF (el repo citado
# originalmente) SOLO trae Q8_0 (8.1 GB) + su visor: cabe en 12 GB pero
# deja menos margen de contexto. Si lo prefieres igualmente:
huggingface-cli download richardyoung/olmOCR-2-7B-1025-GGUF \
  --include "olmOCR-2-7B-1025-Q8_0.gguf" "mmproj-olmOCR-2-7B-1025-vision.gguf" \
  --local-dir ./models
```

Y el arranque es manual (GGUF + visor):

```bash
./build/bin/llama-server -m ./models/olmOCR-2-7B-1025-Q4_K_M.gguf \
  --mmproj ./models/mmproj-olmOCR-2-7B-1025-F16.gguf \
  -ngl 99 -c 8192 --host 0.0.0.0 --port 8080
```

- `-ngl 99`: todas las capas a la GPU (vía Vulkan).
- `-c 8192`: contexto para una página densa + su transcripción (~1 GB
  extra de VRAM; con Q4_K_M queda margen de sobra en 12 GB).
- `--host 0.0.0.0` deja el servidor visible en tu red local; si solo lo
  vas a usar desde la misma máquina, `--host 127.0.0.1` es más prudente.

### 4) Activarlo en el agente

En `.env`:

```
OCR_BACKEND=llamacpp
OCR_LLAMACPP_FAMILIA=glm-ocr   # glm-ocr | hunyuan | olmocr2 | generico
# OCR_LLAMACPP_URL=http://localhost:8080   (ya es el default)
# OCR_LLAMACPP_PROMPT=                     (vacío: prompt por defecto de la familia)
# OCR_LLAMACPP_MAX_LADO=                   (vacío: default por familia)
```

Un valor inválido de `OCR_LLAMACPP_FAMILIA` no rompe nada: error en el
log y fallback conservador a `olmocr2` (el comportamiento exacto de la
v10.0).

Comprueba que el agente lo ve (el chequeo de arranque del lanzador y el
punto 7f de `main.py --diagnostico` muestran la familia configurada y el
modelo que el servidor dice servir, o el aviso con instrucciones si no
está). Si el modelo reportado no parece corresponder a la familia
configurada verás un log_warn INFORMATIVO (no bloqueante: tú decides).

Cómo funciona por dentro (sin sorpresas):

- **API**: el cliente usa `POST /v1/chat/completions` (la API compatible
  con OpenAI que expone llama-server), una página por petición. El
  protocolo es IDÉNTICO para las tres familias; lo que cambia por
  familia (tabla `FAMILIAS_LLAMACPP` en `scrapers/web.py`) es el prompt
  de usuario, el reescalado y la limpieza de salida.
- **Prompts**: las reglas de paleógrafo (`[ilegible]`, no interpretar,
  nombres tal cual, anti-inyección de prompts desde la imagen) van
  SIEMPRE (en olmocr2 como texto de usuario junto al prompt oficial del
  modelo — comportamiento v10.0 exacto —; en glm-ocr/hunyuan/generico
  como mensaje system). El prompt de usuario es: `"OCR"` literal en
  glm-ocr (es con lo que el modelo fue entrenado); un prompt en español
  de transcripción literal en hunyuan; y `OCR_LLAMACPP_PROMPT` si lo
  defines (sustituye al de CUALQUIER familia, para genérico y A/B).
- **Imágenes**: SOLO con olmocr2 se reescalan al lado mayor de **1288 px**
  (la resolución de su entrenamiento); glm-ocr/hunyuan/generico no
  reescalan por defecto (el projector del llama-server ya gestiona la
  resolución). Un `OCR_LLAMACPP_MAX_LADO` explícito en `.env` manda
  sobre el default de familia.
- **Salida**: solo olmOCR-2 devuelve un front matter YAML (idioma,
  rotación, tabla/diagrama) que se descarta; con las demás familias la
  salida va tal cual. El texto va al corpus y a la caché con backend
  `llamacpp-<familia>` (o `llamacpp_manuscrito-<familia>` en
  PARES/SIGA/ADDO); las entradas de la v10.0 sin familia
  (`llamacpp`/`llamacpp_manuscrito`) se tratan como `olmocr2`.
- **Timeouts**: 5 s de conexión (detectar servidor caído sin bloquear) y
  60 s de inferencia por página (ajustables en `.env`:
  `OCR_LLAMACPP_TIMEOUT_CONEXION` / `OCR_LLAMACPP_TIMEOUT_INFERENCIA`).
- **Confianza**: 0.85 fija si devuelve texto (la API no da score; es un
  estimador conservador, no un score medido).
- **Coste**: 0 — la inferencia es local y NO se registra en GASTO (v10.0:
  el OCR ya no escala nunca a la nube, no hay nada que contabilizar).

### 5) Comprobarlo con UN PDF tuyo (antes de soltar el autopiloto)

`--probar-ocr` ejecuta la MISMA cascada del agente (pypdf ->
OCR local -> fallo explícito) con un solo PDF de tu disco: sin
lanzar fases, sin tocar la frontera y SIN coste — v10.0: el OCR es 100%
local y no hay etapa de pago que omitir:

```bash
# PDF "impreso" normal (libro con capa de texto o escaneado moderno)
python main.py --probar-ocr ruta/al/libro.pdf

# Partida parroquial: simula que el PDF viene de PARES/SIGA (la rama de
# manuscritos que usará el agente con los libros parroquiales)
python main.py --probar-ocr partida.pdf --manuscrito

# Ignorar la caché OCR (re-procesar de cero)
python main.py --probar-ocr partida.pdf --manuscrito --sin-cache
```

La salida muestra: `OCR_BACKEND` activo, la **familia** activa con el
**prompt exacto** que se va a enviar y el **modelo servido** por el
llama-server (v10.1), qué etapa ganó, confianza, caracteres, duración,
coste (siempre $0.0000 — OCR 100% local) y el texto extraído (primeros
1.500 caracteres). El resultado se cachea igual que en el agente (la
segunda vez verás el sufijo `_cache`): puedes **precalentar la caché
gratis** pasando tus PDFs de archivos uno a uno antes del autopiloto.

Interpretación rápida del backend ganador:

| Backend | Qué significa |
|---------|---------------|
| `pypdf` | el PDF ya tiene capa de texto (nacido digital) |
| `llamacpp-<familia>` / `llamacpp_manuscrito-<familia>` | el OCR local con esa familia funcionó: gratis y en tu GPU (p. ej. `llamacpp-glm-ocr`) |
| `ocr_local` | RapidOCR (impreso) llegó al umbral de confianza |
| `ocr_local_low` | RapidOCR dio texto con confianza baja: se conserva, marcado como poco fiable (sin escalada a nube) |
| `ocr_local_fallido` / `pypdf_poor` / `fallido` | las etapas locales no dieron texto: fallo EXPLÍCITO, nada escala a la nube (los fallos transitorios no se cachean) |

Si la prueba te convence (el texto extraído se lee bien), ya puedes
lanzar el autopiloto con tranquilidad: la parte OCR hará exactamente lo
que acabas de ver.

## Revisión de fondo v9.1 (método de evidencia + 4 fuentes nuevas)

### PARTE 0 — El problema de MÉTODO (va primero)

De una ejecución real con 716 hallazgos, solo 1 era confirmación
documental real: los otros 715 eran personas con apellido parecido en la
misma zona, cada una con "conexión no probada" en su propia descripción,
presentadas como progreso. Eso violaba el principio genealógico básico.

**Regla v9.1**: cada generación conecta con la siguiente mediante
**al menos 2 datos INDEPENDIENTES que coincidan** (nombre completo +
fecha aproximada, o nombre + cónyuge/padres ya conocidos, o nombre +
lugar exacto). Una coincidencia de apellido+geografía **nunca** es
prueba.

Implementación (`agent/evidencia.py`, clasificador DETERMINISTA que
prevalece sobre lo que diga el LLM):

- Cada hallazgo lleva ahora `"nivel_evidencia"`:
  - `confirmado`: nombre completo que casa con persona de
    `familia_conocida.json` (o confirmada en árbol) **y** ≥1 dato
    verificable independiente coincide (fecha, cónyuge, padres, lugar
    exacto), sin contradicciones.
  - `candidato_fuerte`: apellido compuesto completo + municipio exacto
    + rango de fechas coherente con la generación, sin segundo dato.
  - `coincidencia_debil`: solo apellido o zona amplia.
- Guardes contra las confirmaciones falsas: contradicción de fechas
  (tocayo con el mismo nombre 54 años antes) => débil; comparación
  circular (el lugar del hallazgo solo cuenta contra el municipio
  CONOCIDO de la ficha, nunca contra sí mismo; los "padres" de
  `otros_nombres` no pueden ser el propio nombre) => no cuentan como
  segundo dato.
- El informe de progreso y el `resumen_general` del árbol separan las
  3 secciones: "Confirmado documentalmente" / "Candidatos fuertes a
  verificar" / "Coincidencias débiles (descartables salvo nueva
  evidencia)".
- El COMMIT no deja entrar al árbol hallazgos `coincidencia_debil`
  aunque su cita esté VERIFICADA (la cita prueba que el documento
  existe, no que la persona sea de la familia). Los hallazgos
  anteriores a v9.1 (sin el campo) siguen siendo elegibles.
- `agent/evidencia.py` incluye `reclasificar_arbol()`: reclasifica un
  `arbol_refinado.json` ya existente sin re-ejecutar la fase 2. Desde la
  v9.2 es un comando real: `python main.py --reclasificar` (gratis, ver
  la sección v9.2) y opción 13 del lanzador.

### PARTE A — FamilySearch (catálogo por localidad)

Verificado en vivo: el catálogo de microfilmación
(`/search/catalog/results`) **exige login** ("Sign-in to your account").
El conector (`scrapers/familysearch.py`) usa `placeSearch` por municipio
(nunca búsqueda por nombre, que falla con apellidos compuestos) y lista
los libros parroquiales con sus rangos de fechas ANTES de leer nada.
En `.env`:

```
FAMILYSEARCH_USER=tu_usuario      # opcional: login programático
FAMILYSEARCH_PASS=tu_contraseña   # SECRETO: jamás se loguea ni se guarda en la BD
FAMILYSEARCH_COOKIE=fssessionid=… # alternativa realista: pega la cookie de tu
                                  # navegador (F12 -> Network -> Cookie) si el
                                  # login pide captcha/2FA
```

Estados explícitos (nada falla en silencio): libros online (URL del
visor para descarga manual -> cascada OCR) / "pendiente — requiere
Centro de Historia Familiar" para los restringidos / `sin_credenciales`
si no hay configuración. Rate limiting: máx 1 request cada 3-5 s con
backoff exponencial ante 429/5xx.

### PARTE B — Catastro de Ensenada, Respuestas Particulares

Están en los **AHP provinciales**, no en PARES (que solo da Respuestas
Generales). Zamora y Palencia: sin digitalización pública estable ->
`--solicitudes` genera ahora también la **petición por escrito** al AHP
correspondiente. **Corrección factual**: Álava quedó EXCLUIDA del
Catastro (régimen foral): NO existen Respuestas Particulares para
Vitoria; alternativas reales: IRARGI (sacramentales sin login) +
protocolos del AHP Álava + padrones municipales.

### PARTE C — `archivos_a_consultar_in_situ`

`informe_fase1.json` incluye la sección generada automáticamente desde
`familia_conocida.json`: AHP + Archivo Municipal de cada municipio con
enlaces verificados (portal CyL, web.araba.eus) o la marca "requiere
visita o solicitud postal" — para capitulaciones, testamentos,
vecindarios y quintas, que los buscadores nominales no cubren.

### PARTE D — HISPAGEN

Transcripciones colaborativas públicas y gratis (sin OCR):
`scrapers/hispagen.py` busca apellido+municipio con el com_search de
Joomla (GET `searchword`), mismo patrón que SIGA. OJO: el certificado de
www.hispagen.es está mal emitido -> el conector usa `verify=False` y
prueba también el dominio apex.

### Tests v9.1

`python -m pytest tests/ -q` → **170 tests, 0 fallos** (108 originales
intactos + 62 nuevos: 28 de nivel de evidencia + 28 de fuentes + 6
añadidos a las suites de extracción y plausibilidad).

## Revisión v9.2 — Reclasificar gratis + DeepSeek V4.1 Flash

Dos cambios (ver `INFORME_REVISION_V92.md` para el detalle honesto):
### PARTE 1 — `--reclasificar`: la reclasificación es un comando real

`agent/evidencia.py::reclasificar_arbol()` existía desde la v9.1 pero no
estaba conectada a ningún comando. Ahora:

```bash
python3 main.py --reclasificar     # o opción 13 del lanzador
```

- Carga `arbol_refinado.json` + `arbol_hallazgos.json` +
  `familia_conocida.json` ya guardados y reclasifica con el clasificador
  DETERMINISTA de evidencia (regla >=2 datos independientes).
- **100% offline**: ni un token, ni una llamada de red, ni LLM (el test
  `test_reclasificar_v92.py` intercepta cualquier intento de conexión y
  FALLA si se produce alguno).
- Guarda el resultado sobrescribiendo `arbol_refinado.json` con un
  backup `arbol_refinado.json.bak` del anterior, por si acaso.
- Imprime el resumen: cuántos hallazgos quedan como confirmados /
  candidatos fuertes / coincidencias débiles.
- En el lanzador es la opción 13: "Reclasificar hallazgos existentes
  [gratis, no gasta tokens]".

### PARTE 2 — FASE 2 con DeepSeek V4.1 Flash (vía OpenRouter)

- `MODELO_FASE2` pasa de `z-ai/glm-5.2` a `deepseek/deepseek-v4.1-flash`
  (lanzado el 2026-09-10). Sigue siendo configurable:
  `MODELO_FASE2=...` en `.env`.
- **Precios actualizados en vivo** (fuente: API pública de OpenRouter
  `openrouter.ai/api/v1/models`, consultada el 2026-09-11): v4.1-flash
  cuesta $0.15/$0.60 por M de tokens en valle y **$0.30/$1.20 en punta**
  (lunes-viernes, dos franjas UTC). El control de GASTO usa el precio
  MÁS ALTO para no infravalorar el gasto (nota en `config.py`).
- `chat_json()` soporta el parámetro opcional `reasoning` de OpenRouter
  (DeepSeek V4.1 soporta `reasoning`/`reasoning_effort`), activado SOLO
  con `REASONING_ACTIVADO=true` en `.env`. Si un modelo lo rechaza, se
  desactiva para esa sesión y se reintenta sin él (mismo patrón que el
  fallback de `response_format`): no rompe modelos sin soporte.
- **`REASONING_ACTIVADO=false` por defecto — nota honesta**: el
  razonamiento consume tokens de salida adicionales (más coste) y para
  una tarea de extracción estructurada probablemente no compensa el
  gasto extra. **Actívalo solo si notas que la extracción falla en casos
  complejos; por defecto está apagado para minimizar coste.**
- **`reasoning_details` multi-turno: NO aplica aquí y no se implementa.**
  La API de OpenRouter exige devolver sin modificar el bloque
  `reasoning_details` del asistente al reenviar su mensaje en turnos
  posteriores de una MISMA conversación (útil sobre todo en tool
  calling). `chat_json()` hace llamadas de UN SOLO TURNO por extracción:
  construye `[system, user]` fresco en cada llamada y jamás reenvía
  mensajes del asistente (el reintento de JSON roto solo añade un
  mensaje de usuario). No hay nada que preservar → no hay nada que
  implementar. Documentado para no volver a dudarlo.
- Extra: `FAMILYSEARCH_COOKIE` ahora se lee de verdad del `.env` (estaba
  documentada desde la v9.1 pero sin cablear) y el proyecto incluye por
  fin un `.env.example` completo.

### Tests v9.2

`python -m pytest tests/ -q` → **182 tests, 0 fallos** (los 170 de la
v9.1 intactos + 12 nuevos: 5 de `--reclasificar` offline + 7 de
reasoning/precios/gasto).

## v10.0 — OCR 100% local, salto de generación robusto, SIGA sin fallback

### PARTE 1 — OCR 100% LOCAL (fuera Gemini/nube de TODO el OCR)

- **Cascada nueva**: `pypdf` → OCR local (olmOCR-2 vía llama-server si
  `OCR_BACKEND=llamacpp`; si no RapidOCR; PaddleOCR legacy) → **fallo
  explícito** (backend `ocr_local_fallido` + `log_warn`). Ninguna etapa
  del OCR llama a la nube.
- **Fallos transitorios NO se cachean**: llama-server caído o motor no
  instalado dejan el documento SIN TEXTO y SIN marcar la caché, para
  reintentarlo en cuanto el motor esté disponible.
- **Manuscritos** (PARES/SIGA/ADDO/FamilySearch...): solo olmOCR-2 local
  o fallo explícito. El OCR clásico de impreso no se intenta con ellos.
- **`--importar-propios`**: transcribe fotos de certificados con OCR
  local; sin motor, cada documento queda PENDIENTE con aviso claro (sin
  crash, sin nube) y se reintenta luego.
- **Eliminado**: `_ocr_gemini`/`_escalar_a_gemini` (web.py),
  `chat_vision`/`transcribir_imagen_llm` (utils/llm.py), los backends
  `gemini`/`vlm_manuscrito`, `MODELO_VISION`, `OCR_MODO` ("solo_vision"),
  `TIMEOUT_VISION`, `GEMINI_OCR_LOTE_MAX`, los precios gemini de la tabla
  y el flag `--con-gemini` de `main.py`. El LLM de TEXTO (chat_json vía
  OpenRouter) queda EXACTAMENTE igual: no es OCR.
- **Tests**: `tests/test_ocr_local_v10.py` espía `utils.llm.llm` y
  verifica 0 llamadas a `chat.completions.create` en cada fallo local,
  gasto intacto, fallos transitorios sin cachear y .gitignore presente.

### PARTE 2 — Salto de generación robusto (_extraer_progenitor)

El parser solo entendía "Nombre (padre)" en `otros_nombres`, pero el
prompt no exigía ese formato: cuando el modelo escribía "hijo de X y Y",
el árbol NO crecía. Arreglo doble:

- `SYSTEM_PROMPT_HALLAZGOS` exige ahora el formato exacto
  ("Nombre Apellidos (padre)" / "(madre)" / "(cónyuge)"..., con ejemplos).
- Parser tolerante: acepta además "hijo/hija de X y Y" en
  `otros_nombres` y, como ÚLTIMO RECURSO, en `cita_literal` (X→padre,
  Y→madre, limpiando puntuación). Si hay dos candidatos DISTINTOS para
  el mismo rol, no crea ninguno y lanza `log_warn` (nunca adivina).
- Tests (`tests/test_salto_generacion_v10.py`): los 3 formatos crean
  las fichas de los padres vía `cometer_confirmaciones`; la ambigüedad
  no crea nada y avisa; tras crear los padres, `calcular_frontera`
  genera la entrada correspondiente.

### PARTE 3 — SIGA sin fallback silencioso

`_siga_localidad` devolvía 55 (Vitoria) para CUALQUIER municipio fuera
del mapa: las búsquedas se hacían en la localidad EQUIVOCADA y quedaban
cacheadas como hechas (búsquedas muertas para siempre). Ahora:

- Devuelve `None` si el municipio no está en `SIGA_LOCALIDADES`
  (Vitoria solo si ES Vitoria; "Vitoria" se acepta como alias explícito
  de "vitoria-gasteiz").
- `recolector_siga` ante `None`: `log_warn` explícito, consulta OMITIDA
  y NO cacheada como hecha.

### PARTE 4 — .gitignore y publicación

`.gitignore` creado ANTES del primer `git add` (el repo va a GitHub
público): claves (`.env`), base de datos, datos familiares, backups,
cachés, modelos (*.gguf), documentos propios y TODAS las salidas de
investigación quedan fuera del repositorio. `generar_dossier.py`
genera `DOSSIER_PARA_QWEN.txt` con el código completo + las secciones
PYTEST / GIT_LS_FILES / DIAGNOSTICO para revisiones externas.

## v10.2 — Corrección de los fallos del log de ejecución real (2026-09-11)

Siete fallos sacados del log de una noche completa del autopiloto
(`--ciclo 3 --presupuesto-max 3.0`, ~2 h 15 min de ejecución). Cada
arreglo lleva su test de regresión en `tests/test_fixes_v102.py`.

### FIX 1 (crítico) — SIGA/ADDO/Ensenada llevaban toda la ejecución MUERTOS

El log repetía en CADA objetivo: `recolector recolector_siga falló:
recolector_siga() missing 1 required positional argument: 'objetivo'`
(lo mismo con `recolector_addo` y `recolector_ensenada`, 45 veces en
total). Causa: `recolectar()` invocaba los tres conectores como
`conector()` sin argumentos (los otros dos, HISPAGEN y FamilySearch,
iban envueltos en closures que sí capturaban `objetivo` y `conn`).
Consecuencia: las tres fuentes de datos ESTRUCTURADOS más valiosas del
proyecto no se consultaron NI UNA VEZ. Ahora los cinco conectores van
envueltos igual y reciben `(objetivo, conn)`.

### FIX 2 (crítico) — el crash con traceback cuando el LLM de fase 2 cae

Dos veces terminó la noche con `RuntimeError: LLM inaccesible tras 3
intentos` SIN capturar: moría el programa entero (sin GEDCOM, sin
siguiente ciclo). Ahora la consolidación degrada con elegancia: los
hallazgos ya guardados NO se pierden, `arbol_refinado.json` se escribe
con un bloque determinista marcado `CONSOLIDACIÓN PENDIENTE`, el
GEDCOM se exporta igual y (en modo `--ciclo`) el autopiloto pasa al
siguiente ciclo. Reintentar la fase 2 luego es barato: la caché de
extracción evita repetir los lotes ya extraídos.

### FIX 3 (alto) — rate-limit 429/503 de OpenRouter con backoff de juguete

El 503 final del log venía de un `deepseek-v4.1-flash is temporarily
rate-limited upstream` con `previous_errors` 429 de hasta TRES
providers (DeepInfra, Morph, Fireworks): el modelo estaba saturado en
todos lados. El backoff de siempre (2-4 s) hacia que los 3 reintentos
cayeran dentro de la misma racha. Ahora `_es_rate_limit()` detecta
429/503/'rate-limited' y esos casos esperan de verdad (15 s, 30 s,
máx. 60 s). Además `TIMEOUT_LLM` es configurable por `.env` (el 45 s
por defecto se quedaba corto con los lotes largos de v4.1-flash; el
`.env` entregado lo sube a 90).

### FIX 4 (medio) — HISPAGEN muerto por RemoteDisconnected

TODAS las consultas a hispagen.es fallaban con `('Connection aborted.',
RemoteDisconnected(...))`: el servidor cierra las conexiones
keep-alive y la SESSION compartida reusaba sockets ya muertos. Ahora
`_get_hispagen()` reintenta UNA vez cerrando el pool de conexiones
(conexión limpia) antes de rendirse; si persiste, el fallo sube al
cooldown del conector como siempre.

### FIX 5 (medio) — URLs .pdf que sirven HTML rompían pdf2image

`Listado_Registro_EASA_DO_STS-ES.pdf` devolvía una página HTML
(`b'<!DOC'`): la cascada la trataba como PDF y pdf2image reventaba con
el confuso `Unable to get page count` + warnings de poppler. Un PDF
real empieza SIEMPRE por `%PDF-`: si el contenido es HTML, se procesa
como página web y se recupera su texto.

### FIX 6 (bajo) — llama.cpp: 500 por contexto completo y URL truncada

`max_tokens: 8192` igualaba el contexto total (`-c 8192`) y dejaba 0
tokens para la imagen: llama-server respondía 500 en páginas sueltas
(página 1/30 del CCEP-Web del log). Ahora se piden 4096. Y el mensaje
de error se recortaba a 80 caracteres, que cortaba la URL del endpoint
por la mitad y el log mostraba `http://localhost:8080/v1/chat/c`
(¿endpoint roto?): el recorte pasa a 120.

### FIX 7-8 — test frágil y `resumen_noche.py`

- `test_config_llamacpp_por_defecto` reventaba con el `.env` del
  usuario (`OCR_LLAMACPP_TIMEOUT_INFERENCIA=600`): ahora comprueba los
  defaults SOLO si el `.env` no los pisa.
- `python resumen_noche.py` decía "can't open file": el script NO
  existía. Ahora existe y es 100% offline (0 tokens, 0 red): corpus por
  origen, hallazgos por nivel de evidencia, árbol, frontera, cachés y
  siguiente paso recomendado. También es la opción **14** del menú del
  lanzador.

## Correcciones de la v4.3 (verificadas en vivo el 2026-09-10)

Cada punto se comprobó contra los sitios reales (sonda de conectividad +
parser ejecutado contra el HTML de hoy) y/o con el nuevo test de
integración que ejecuta el bucle COMPLETO del autopiloto.

### Punto 1 — La fase 1 ya no revienta (bug REAL de la v4.2)

`agent/fase1.py` usaba `MAX_CHARS_TEXTO` (para guardar el texto original
de cada fragmento) **sin importarla de config.py**: la fase 1 lanzaba
`NameError` en cuanto una página pasaba el filtro, es decir, en la
primera búsqueda con resultados. Las 46 pruebas unitarias no lo veían
porque ninguna ejecutaba `ejecutar_fase1` completa; el nuevo test de
integración (punto 5) sí y lo cazó. Arreglado: se importa.

### Punto 2 — PARES caído ya no envenena la investigación

Verificado en vivo: el portal PARES respondía HTTP 200 pero con
"Problema encontrado: Ha ocurrido un error al conectar con la Base de
Datos". La v4.2 lo interpretaba como "0 localidades": cacheaba la
consulta como hecha (jamás se reintentaba) y escribía en el corpus una
NOTA NEGATIVA FALSA ("el pueblo no está en el Catastro de Ensenada").
- Nueva excepción `ParesNoDisponible`: distingue "el servidor ha
  fallado" de "no hay resultados" (que sí es un dato genealógico).
- Los fallos de conectores (PARES, ADDO, SIGA) van a COOLDOWN con TTL
  de 6 h (`fail::` en SQLite): se reintentan solos, sin repetirlos en
  cada objetivo de la misma ejecución (ADDO caído costaba 30 s de
  timeout POR OBJETIVO).
- ADDO ya no marca la consulta como hecha ANTES de pedirla.
- `--ensenada` y `--probar-conectores` informan de la caída y dejan el
  municipio "sin concluir" (reintentar), no "no encontrado".

### Punto 3 — Extracción desde el TEXTO ORIGINAL (más hallazgos verificados)

La v4.2 extraía los hallazgos del RESUMEN que hizo el LLM de filtrado
(`texto_limpio`, 6000 car.) pero auditaba la cita contra el ORIGINAL
(24000 car.): las citas "reformateadas" por el filtro acababan
SIN_VERIFICAR (hallazgos legítimos fuera del árbol) y se perdían datos.
Ahora la extracción lee el ORIGINAL cuando existe: lo que el modelo
extrae es literalmente lo que la auditoría comprueba.

### Punto 4 — La frontera reconoce a los padres con matching difuso

`padres_confirmados` usaba matching EXACTO: la partida nombra
"Nazario Merillas" pero la ficha se llama "Nazario Merillas Uribarri"
-> la pareja nunca constaba como confirmada -> la entrada "padres" se
regeneraba en CADA ciclo (trabajo y presupuesto quemados sin avance).
Ahora usa `emparejar_persona` (difuso, con desambiguación por año).

### Punto 5 — SIGA sin filas duplicadas + test de integración end-to-end

- `siga_buscar` deduplica filas entre páginas y corta la paginación
  cuando una página no aporta filas nuevas (verificado contra el sitio
  real: antes devolvía el mismo matrimonio 3 veces).
- **`tests/test_integracion_v43.py`**: ejecuta `--ciclo 2` COMPLETO en
  una copia aislada del proyecto con LLM/Tavily simulados y comprueba:
  hallazgo verificado contra el original, commit con evidencia, fichas
  de los padres creadas (salto de generación), GEDCOM con ids estables
  y fuentes, registro append-only, backups, dedupe de evidencias y
  DISPARO DEL FRENO del autopiloto en el ciclo 2. Es la primera prueba
  que cubre el bucle agéntico entero.

### Punto 6 — Paradas seguras del autopiloto

`PresupuestoExcedido` que saltara dentro de la fase 1 o fase 2 de un
ciclo dejaba un traceback; ahora se captura con mensaje claro, sin
marcar nada como investigado (el progreso ya está guardado).

- **OCR 100% local** (RapidOCR / PaddleOCR / llama.cpp+olmOCR-2; v10.0:
  sin escalada a ningún VLM de la nube) para PDFs escaneados que pypdf no
  puede extraer.
- **Extracción con cálculos** (edades en actas → fechas de nacimiento
  estimadas, marcadas siempre como `aproximada` y con el cálculo en la
  justificación para auditoría).
- **Reintento anti-Cloudflare** con Playwright headless, ahora
  **OPT-IN** (`REINTENTO_HEADLESS=false` por defecto): saltarse un
  bloqueo puede acabar con la IP vetada; antes prueba `--solicitudes`.

## Estructura del proyecto

```
genealogia_v4/
├── lanzador.py         # v9.0: MENÚ INTERACTIVO (el punto de entrada diario)
├── lanzador.ps1        # v9.0: wrapper de Windows para doble clic
├── main.py             # CLI (argparse) + bucle de ciclos autopiloto (modo avanzado)
├── config.py           # Constantes, prompts, schemas, DOMINIOS_IGNORADOS, SQLite
├── utils/
│   ├── __init__.py
│   ├── ui.py            # Colores ANSI, iconos, embudo de descargas, spinner
│   ├── llm.py           # chat_json + timeout duro + Gasto (v10: sin VLM)
│   └── personas.py      # v4.2: ids estables + emparejamiento difuso de nombres
├── scrapers/
│   ├── __init__.py
│   ├── web.py           # Tavily + descargar_texto + OCR 100% LOCAL (v10.0:
│   │                    #   RapidOCR/llama.cpp/olmOCR-2, fallo explícito) + headless opt-in
│   └── archivos.py     # SIGA (Álava), ADDO (Palencia), PARES (Ensenada)
├── agent/
│   ├── __init__.py
│   ├── fase1.py         # Búsqueda exhaustiva + estrategias genealógicas nuevas
│   ├── fase2.py         # Extracción, consolidación, auditoría contra el ORIGINAL
│   ├── frontera.py      # Cola priorizada + commit con dedupe + backups con fecha
│   └── gedcom.py        # GEDCOM 5.5.1 + solicitudes + Ensenada + OCR local
│                        #   de documentos propios (v10) + diagnóstico
└── tests/               # 232 pruebas automáticas (pytest)
    ├── conftest.py      # entorno de pruebas (claves y módulos de mentira)
    ├── test_personas.py     # ids estables, tocayos, matching difuso
    ├── test_plausibilidad.py# punto 2: solo contradicciones reales marcan
    ├── test_auditoria.py    # punto 5: cita contra el TEXTO ORIGINAL
    ├── test_commit.py       # puntos 3/6/7: dedupe, difuso, backups, registro
    ├── test_gedcom.py       # punto 9: estructura válida + homónimos separados
    ├── test_gasto.py        # punto 8: coste estimado sin usage + presupuestos
    ├── test_frontera.py     # punto 4: nidada única + investigados
    ├── test_conectores_v43.py   # v4.3: PARES caído, cooldown, dedup SIGA
    ├── test_extraccion_v43.py   # v4.3: extracción desde el texto ORIGINAL
    ├── test_frontera_v43.py     # v4.3: padres confirmados con matching difuso
    ├── test_integracion_v43.py  # v4.3: bucle COMPLETO del autopiloto
    │                            (fase1→fase2→commit→frontera→freno)
    ├── test_llamacpp_v90.py    # v9.0: cliente llama.cpp + cascada + caídas
    ├── test_llamacpp_multi_v101.py  # v10.1: prompts/reescalado/YAML/caché por
    │                            #  familia, fallback de familia inválida,
    │                            # aviso de modelo no coincidente
    ├── test_lanzador_v90.py    # v9.0: helpers del menú + humo del proceso
    │                            (arranque y salida limpios)
    ├── test_probar_ocr_v90.py  # v9.0/v10.0: --probar-ocr (PDF único, $0,
    │                            cascada local, caché)
    ├── test_ocr_local_v10.py    # v10.0: OCR 100% local (0 llamadas nube,
    │                            fallos transitorios sin cachear, .gitignore)
    ├── test_salto_generacion_v10.py  # v10.0: parser de padres tolerante
    └── test_siga_v10.py         # v10.0: SIGA sin fallback a Vitoria
```

## Instalación

```bash
cd genealogia_v4
pip install -r requirements.txt
cp .env.example .env      # pega tus claves de Tavily y OpenRouter

# Requisitos opcionales para OCR y reintento headless:
#   1. Poppler (para pdf2image):    apt install poppler-utils
#      (Windows: descargar poppler y añadirlo al PATH; macOS: brew install poppler)
#   2. Navegador de Playwright:     playwright install chromium
#   3. OCR local: ya viene en requirements.txt (rapidocr_onnxruntime,
#      instala limpio en Windows/macOS/Linux sin compilar nada)
#   4. v9.0/v10.1 OCR con llama.cpp + Vulkan (GPU AMD; GLM-OCR/HunyuanOCR/
#      olmOCR-2): NO es un pip, se compila aparte y el modelo se descarga
#      solo con -hf — ver la sección "OCR local con llama.cpp + Vulkan".

python3 lanzador.py           # v9.0: el menú (chequeo rápido incluido)
python3 main.py --diagnostico # comprueba todo antes de gastar tokens
python -m pytest tests/ -q    # las 232 pruebas de garantía
```

Las dependencias opcionales **no rompen el agente si no están**: si
RapidOCR o Playwright no están instalados, el agente sigue funcionando
con el comportamiento básico (pypdf + requests), con un `log_warn` una
sola vez. v10.0: sin motor de OCR local, los PDFs escaneados quedan SIN
TEXTO con un fallo explícito — NO hay fallback en la nube.

### Variables de `.env` (OCR)

```
OCR_BACKEND=rapidocr     # "rapidocr" (defecto) | "paddleocr" (legacy) |
                         #  "llamacpp" (llama.cpp, manuscritos) | "off"
REINTENTO_HEADLESS=false # reintentar 403/503 con navegador headless (opt-in:
                         #  riesgo de IP bloqueada; prueba antes --solicitudes)
```

(v10.0: `OCR_MODO` se ELIMINA — ya no hay "modo solo visión" ni modelo
de visión; los manuscritos van al OCR local de llama.cpp o fallan
explícito.)

### v9.0/v10.1 — Variables de `.env` (backend llama.cpp)

```
OCR_BACKEND=llamacpp                         # activa el OCR local vía llama-server
OCR_LLAMACPP_URL=http://localhost:8080       # URL del llama-server (default)
OCR_LLAMACPP_TIMEOUT_CONEXION=5.0            # s: detectar servidor caído
OCR_LLAMACPP_TIMEOUT_INFERENCIA=60.0         # s: inferencia real por página
OCR_LLAMACPP_FAMILIA=glm-ocr                 # v10.1: glm-ocr (default) | hunyuan |
                                             #  olmocr2 | generico
OCR_LLAMACPP_PROMPT=                         # v10.1: vacío = prompt de la familia;
                                             #  no vacío la SUSTITUYE (A/B)
OCR_LLAMACPP_MAX_LADO=                       # v10.1: vacío = default por familia
                                             #  (olmocr2=1288, resto=0); un número
                                             #  manda sobre el default
```

Con `OCR_BACKEND=llamacpp`, la cascada de OCR es 100% local: pypdf → OCR
local (la familia que elijas) → fallo explícito, y los manuscritos van por
el mismo camino. Ver la sección "OCR local con llama.cpp + Vulkan" para
compilar/arrancar el servidor de cada familia.

## Mejoras de la v4.0

### FASE 1 — Resolución de bugs críticos (estabilidad)

1. **Timeout duro radical**: la función `_llamada_con_hard_timeout` en
   `utils/llm.py` ejecuta cada llamada al LLM en un hilo daemon. Si no
   termina en N segundos (def. 45s, configurable), se lanza
   `LLMTimeoutHard` inmediatamente — el proceso principal sigue y la
   llamada puede reintentarse con backoff o saltarse. Ya no hay
   "Consultando deepseek-v4-flash... (364s)" colgados.

2. **Filtro de dominios basura (`DOMINIOS_IGNORADOS`)**: en `config.py`
   se define un set con ~30 dominios que sistemáticamente nos hacen
   perder tiempo (Facebook, Tripadvisor, PubMed, Dateas, Amazon,
   Wikipedia, etc.). `scrapers/web.py` los descarta ANTES de intentar
   descargarlos. La función `descargar_texto` devuelve `("", "dominio_ignorado")`
   para que el caller lo cuente en el embudo.

3. **JSON roto limpio**: cuando el LLM devuelve JSON inválido, se
   registra en UNA sola línea con `log_warn("... devolvió JSON inválido
   (intento 1/4); pidiendo JSON estricto. Causa: ...")` sin verter el
   contenido crudo del error ni el stacktrace. Tras 4 intentos fallidos
   se lanza `RuntimeError` capturable.

### FASE 2 — UI agéntica (terminal viva y bonita)

- **Colores ANSI**: verde (éxitos), amarillo (avisos), rojo (errores),
  cian (acciones), magenta (LLM/búsqueda). Se autodesactivan si la
  salida no es TTY (pipes, redirección, tests).
- **Iconos Unicode**: `[⚙]` trabajando, `[✓]` éxito, `[x]` error,
  `[!]` aviso, `[🔍]` buscando, `[🧹]` limpieza, `[📊]` estadísticas,
  `[🎯]` objetivo, `[🌲]` árbol/GEDCOM, `[📄]` documento.
- **Transparencia del embudo**: por cada consulta Tavily se imprime una
  sola línea con el resumen: `10 URLs encontradas -> 3 descartadas
  (basura) + 1 (duplicadas) -> 4 descargadas, 2 fallidas -> 2 superan
  filtro local "query..."`.

### FASE 3 — Compartimentación (adiós al monolito)

Las 2000 líneas se dividen en 10 archivos con responsabilidades claras:
- `config.py`: constantes, prompts, JSON schemas, helpers de texto
  (`normalizar`, `sin_tildes`, `envolver_fuente`, `variantes_compuesto`),
  SQLite, URLs de archivos.
- `utils/llm.py`: `chat_json`, `Gasto`, `PresupuestoExcedido`,
  `LLMTimeoutHard`, `_llamada_con_hard_timeout`, `variantes_apellido`.
- `utils/ui.py`: `log_ok/warn/error/search/llm/clean/stats/target/tree/doc`,
  `Indicador` (spinner), `FunelDescargas`.
- `scrapers/web.py`: `buscar_tavily`, `descargar_texto`, `url_descartable`.
- `scrapers/archivos.py`: `recolector_siga/addo/ensenada`,
  `siga_buscar`, `buscar_localidades_ensenada`, `probar_conectores`,
  helpers de apellidos (`_tokens_apellido`, `_formas_siga`, `con_comodin`).
- `agent/fase1.py`: `generar_objetivos_busqueda` (con estrategias nuevas),
  `ejecutar_fase1`, `llamar_filtro_llm`, `pedir_expansion`,
  `cargar_corpus`, `guardar_corpus`.
- `agent/fase2.py`: `extraer_hallazgos`, `consolidar`,
  `verificar_plausibilidad_biologica`, `auditar_citas`, `fase2`.
- `agent/frontera.py`: `calcular_frontera`, `cometer_confirmaciones`,
  `cargar_estado`, `guardar_estado`, `mostrar_frontera`,
  `generar_informe_progreso`, `cargar_familia`.
- `agent/gedcom.py`: `exportar_gedcom`, `generar_solicitudes`,
  `generar_candidatos_ensenada`, `importar_documentos_propios`,
  `diagnostico`, `fecha_gedcom`, `transcribir_imagen`.
- `main.py`: `argparse`, `fase1`, `ejecutar_ciclos`, `main`.

### FASE 4 — Nuevas estrategias genealógicas

1. **Búsqueda de la Nidada (hermanos)** — Cuando una persona tiene padres
   confirmados, el agente añade automáticamente a la frontera el objetivo
   de buscar a sus HERMANOS. Las partidas de hermanos revelan datos
   colaterales vitales: abuelos (en el expediente matrimonial de los
   padres), origen de la familia, edades, etc. Se implementa en:
   - `agent/frontera.py::calcular_frontera` añade entradas tipo
     `"hermanos"` cuando persona + padres están confirmados.
   - `agent/fase1.py::generar_objetivos_busqueda` genera semillas
     específicas: `"bautismo hijo de [Padre] y [Madre]" "[Municipio]"`,
     `"hijo de [Padre] y [Madre]" "[Municipio]" "bautismo"`, etc.

2. **Expansión geográfica automática** — Si tras `EXPANSION_GEO_INTENTOS`
   (def. 3) consultas un municipio devuelve 0 resultados, el agente añade
   automáticamente nuevas consultas a nivel provincial (sin municipio)
   para atrapar a familias que emigraron a un pueblo vecino. Se
   implementa en `agent/fase1.py::ejecutar_fase1` con el contador
   `consultas_vacias_municipio` y la bandera `expansion_geo_activa`.

3. **Flexibilidad de apellidos compuestos** — Los apellidos como
   "Sáenz de Navarrete" se trocean y se prueban todas las combinaciones
   razonables en las búsquedas Tavily: `"Sáenz"`, `"Navarrete"`,
   `"Sáenz-Navarrete"`, `"Sáenz Navarrete"`, `"Navarrete Sáenz"`,
   `"Navarrete-Sáenz"`. Se implementa en `config.py::variantes_compuesto`
   y se consume tanto en las semillas de Tavily (fase 1) como en las
   formas de búsqueda de SIGA (scrapers/archivos.py).

## Mejoras de la v4.1

### MEJORA 1 — OCR (histórico v4.1; v10.0: 100% local)

Cascada para PDFs escaneados (libros parroquiales, BOE antiguos):

1. **pypdf** (rápido, gratis): si extrae ≥100 caracteres útiles, listo.
2. **OCR local**: RapidOCR (defecto, ONNX) / PaddleOCR (legacy) /
   llama.cpp+olmOCR-2 (`OCR_BACKEND=llamacpp`, el único que lee
   manuscritos). Convierte cada página a PNG con `pdf2image`.
3. **Fallo explícito** (v10.0): backend `ocr_local_fallido` + `log_warn`.
   La etapa 3 de la v4.1 (escalada a Gemini Flash Lite en la nube por
   confianza baja) se ELIMINÓ en la v10.0: nada de OCR va a la nube.

Caché por hash del PDF en SQLite (tabla `ocr_cache`): un documento ya
procesado no repite OCR entre ejecuciones. Los fallos transitorios
(llama-server caído, motor no instalado) NO se cachean.

Log de una línea por documento con icono `[📄]`:
```
[📄] pares.cultura.gob.es/catastro -> 12 págs, rapidocr (conf. 0.84), $0.00
[📄] pares.cultura.gob.es/catastro -> 3 págs, llamacpp/olmOCR-2 (conf. 0.85), $0.00
```

Variables (en `config.py` o `.env`):
```
OCR_BACKEND=rapidocr       # "rapidocr" | "paddleocr" | "llamacpp" | "off"
OCR_USE_GPU=true           # false = forzar CPU (PaddleOCR legacy)
OCR_IDIOMA=es
OCR_CONFIANZA_MIN=0.70     # bajo el umbral: texto conservado como 'ocr_local_low'
```

(v10.0: `MODELO_VISION` se ELIMINA de config y `.env.example`; el gasto
de OCR es cero y `GASTO` solo contabiliza los LLM de texto.)

### MEJORA 2 — Extracción con cálculos (fechas, edades)

El `SYSTEM_PROMPT_HALLAZGOS` (`config.py`) ahora pide explícitamente al
LLM que **calcule fechas de nacimiento aproximadas** a partir de:

- Edades en actas de defunción ("difunto de 78 años en 1850" → ~1772).
- Edad en bautismos tardíos ("bautizado a los 3 días el 5 de marzo" →
  nacido el 2 de marzo).
- Edad en matrimonios ("casado a los 25 años en 1900" → ~1875).
- Edad en padrones y quintas.

Las fechas derivadas se marcan siempre con `fecha_precision="aproximada"`
y la fórmula del cálculo va en `justificacion`, para auditoría.

`verificar_plausibilidad_biologica()` en `agent/fase2.py` detecta estos
hallazgos derivados (por la palabra clave `->` o `nacimiento estimado` en
la justificación) y **relaja los umbrales** en ±5 años: una fecha
estimada por cálculo no debe penalizarse como posible_homonimo por
diferencias pequeñas. Sí se marca homónimo si la diferencia es absurda
(>80 años padre-hijo o >70 madre-hijo).

### MEJORA 3 — Reintento anti-Cloudflare (Playwright headless)

`scrapers/web.py::descargar_texto()` ahora reintenta con Chromium
headless cuando `requests` recibe **403/503 específicamente** (no en
404, ni en timeout, ni en otros errores):

1. `requests.get(...)` (rápido) — comportamiento por defecto.
2. Si status == 403 o 503: reintenta UNA vez con Playwright headless,
   esperando a `networkidle` (carga completa del JS de Cloudflare).
3. Si playwright no está instalado o falla: cae al comportamiento
   anterior (descartar URL, contarla en el embudo de descargas).

El navegador headless es un singleton inicializado lazy: no se arranca
si no se necesita. Si no hay playwright o falta `playwright install
chromium`, se registra un `log_warn` UNA sola vez y se desactiva el
reintento para el resto de la ejecución.

**Solo se aplica a sitios legítimos de archivo/gobierno** que activan
protecciones por exceso de tráfico. Los dominios de la lista
`DOMINIOS_IGNORADOS` (myheritage, ancestry, etc.) siguen descartándose
antes de cualquier descarga.

## Correcciones de la v4.2 (revisión de fallos)

Cada corrección cita el punto del informe de revisión que la motivó.

### Punto 1 — Instalación (RapidOCR en vez de PaddleOCR)
`paddlepaddle` no termina de instalar en Windows con Python moderno. La
cascada OCR ahora usa **RapidOCR** (los mismos modelos PP-OCR convertidos
a ONNX, `pip install rapidocr_onnxruntime`, sin compilar nada).
`OCR_BACKEND=paddleocr` mantiene el comportamiento antiguo (legacy) para
quien ya lo tenga funcionando.

### Punto 2 — El agente ya confirma (el "fallo número uno")
`verificar_plausibilidad_biologica` solo marca `posible_homonimo` ante una
**contradicción real y comprobable** (hijo nacido 2 años después del
padre, madre 60 años mayor). Que falten las fechas de los padres — justo
lo que el agente está buscando — ya no es sospecha: se anota
`plausibilidad_biologica: "NO_COMPROBABLE"` (transparente, sin veto) y el
hallazgo sigue siendo elegible. La puerta de entrada al árbol sigue
siendo la cita verificada, que es la garantía correcta.

### Punto 3 — El freno del autopiloto funciona
El dedupe de evidencias usa una **clave estable sin el timestamp del
commit** (tipo+fecha+lugar+url+cita+origen). La misma prueba ya no se
re-apunta en cada ejecución, `familia_conocida.json` deja de llenarse de
líneas repetidas y el contador de "evidencias NUEVAS" vuelve a 0 cuando
un ciclo no aporta nada: el freno salta y no se quema presupuesto.

### Punto 4 — No repite trabajo, no pierde hallazgos
- Clave de caché de fase 1 por **nombre normalizado** de la persona (no
  por el índice `p{NN}` de la pseudo-familia de la frontera): las
  búsquedas ya no se relanzan cada ciclo porque cambien los índices.
- UNA entrada "hermanos" por nidada (binomio de padres): antes cada
  hermano confirmado añadía su propia entrada para la MISMA nidada.
- El estado guarda `investigados` (clave ancla+tipo): lo ya investigado
  queda fuera de la frontera salvo que el tipo cambie (p. ej. al
  confirmarse los padres, "padres" pasa a "hermanos").
- `corpus_bruto.json` corrupto se RENOMBRA con marca de tiempo para poder
  recuperar hallazgos a mano (antes se descartaba en silencio).

### Punto 5 — La verificación de citas verifica de verdad
Fase 1 guarda el **texto original** descargado de cada página
(`texto_original` del fragmento). La auditoría de fase 2 comprueba cada
`cita_literal` contra ESE texto, no contra el resumen que hizo el LLM de
filtrado. Si la primera IA se equivocó o "rellenó" un nombre, la cita no
está en el original y el hallazgo queda `SIN_VERIFICAR` (no entra al
árbol como hecho probado). Los fragmentos pre-v4.2 caen al texto_limpio
como fallback. Además abarata: el pase determinista contra el original
resuelve más casos sin gastar tokens.

### Punto 6 — Identidad de personas (ids estables)
Nuevo módulo `utils/personas.py`:
- `asignar_ids()`: cada ficha recibe un id estable ("P0001"...) que se
  persiste y NUNCA se reutiliza (ni siquiera si la persona se borra).
- `emparejar_persona()`: matching exacto, por subconjunto de tokens en
  cualquier dirección ("Isidro Merillas" ~ "Isidro Merillas Panero") y
  desambiguación de tocayos por año. Sin desambiguación posible NO se
  fusiona: queda como pista con aviso (nunca en silencio).
- Integrado en: hallazgos de fase 2 (`persona_id`), commit de la
  frontera, frontera->fase 1, GEDCOM (xrefs por id: abuelo y nieto
  tocayos son DOS registros INDI) y arranque de `main.py`.

### Punto 7 — Seguridad de tus datos
- Backups con **marca de tiempo** en `backups/` y rotación (se conservan
  las últimas 30). La copia única `.bak` que se machacaba en cada
  ejecución, eliminada.
- `registro_confirmaciones.jsonl`: **append-only** (solo crece). Cada
  confirmación se apunta como línea JSON con persona_id, cita y fuente:
  aunque un commit corrompiera el árbol, el histórico siempre es
  recuperable a mano.
- El proyecto bajo control de versiones (git) recomendado desde el
  primer día (punto 1 del orden de actuación del informe).

### Punto 8 — Gasto y presupuesto
- Una sola llamada Tavily por consulta: la restricción a fuentes
  archivísticas usa `include_domains` con TODOS los dominios a la vez
  (antes: una búsqueda por dominio, hasta 8-9 por consulta).
- Reintentos LLM: `chat_json` baja a 3 intentos y el SDK de OpenAI a 1
  reintento (máx. 6 llamadas reales por consulta en vez de 16: la causa
  de los cuelgues de 6 minutos).
- `Gasto.registrar_llamada` **estima** el coste (~4 chars/token, ~1000
  tokens por imagen) cuando el provider no devuelve `usage`: nunca coste
  cero, el tope de `--presupuesto-max` salta siempre. El resumen de gasto
  distingue lo medido de lo estimado.

### Punto 9 — GEDCOM 5.5.1 válido (probado en importadores)
- `1 SUBM` + registro `0 @SUB1@ SUBM`, `2 NAME` bajo SOUR y `2 TIME`
  bajo DATE: lo que exigían los validadores y faltaba.
- Registros SOUR: `TITL`, `ABBR` y el URL a **nivel 1** (`1 WWW` +
  `1 PUBL` "Disponible en: ..."): antes `2 URL` colgaba de `1 TITL`
  (mal anidado; Gramps/Ancestry podían perder la fuente).
- Citas largas partidas con `CONC` (continuación sin salto de línea);
  `CONT` solo donde el salto es intencional.
- Ninguna línea supera 255 caracteres (tope del estándar).
- `1 REFN P0001` en cada INDI: trazabilidad GEDCOM <-> JSON.

### Punto 10 — Manuscritos (v10.0: olmOCR-2 local o fallo explícito)
`DOMINIOS_MANUSCRITOS` (PARES, SIGA, ADDO, FamilySearch...): sus PDFs
escaneados son manuscritos de 1600-1900 y el OCR clásico (pensado para
impreso) no lee nada útil — a veces alucina con "alta confianza". Para
esos dominios SOLO se intenta olmOCR-2 local (`OCR_BACKEND=llamacpp`);
si no está disponible, el fallo es EXPLÍCITO (backend
`ocr_local_fallido`, sin cachear si es transitorio). v10.0: nunca se
mandan a un modelo de visión en la nube (el antiguo `OCR_MODO`
`solo_vision` desaparece con ellos).

### Punto 11 — 66 pruebas automáticas (histórico v4.3)
`python -m pytest tests/ -q`. Cubren: ids estables y tocayos, plausibilidad
(sin datos ≠ sospecha), auditoría contra el original, dedupe y freno del
commit, backups/rotación/registro append-only, estructura GEDCOM
(SUBM/WWW/CONC/255/xrefs), gasto estimado y tope de presupuesto, nidada
única e investigados; v4.3 añade conectores con fallo transitorio, la
extracción desde el original, la frontera difusa y el test de integración
del bucle completo. `conftest.py` inyecta claves y módulos de mentira
para que la suite corra sin red en cualquier máquina.

### Extras de la revisión
- **Hemerotecas desbloqueadas**: la lista `DOMINIOS_IGNORADOS` ya no
  bloquea la prensa (elmundo, elpais, abc, 20minutos...): es justo donde
  están las ESQUELAS que el agente busca. El filtro local + el LLM de
  fase 1 descartan la prensa irrelevante.
- **Headless OPT-IN** (`REINTENTO_HEADLESS=false`): saltarse un bloqueo
  403/503 puede acabar con la IP vetada y va contra las condiciones de
  uso de algunos archivos. Con 403/503 se avisa y se sugiere pedir la
  copia al archivo por email (`--solicitudes` genera las plantillas).

## Uso (modo avanzado)

Lo cotidiano va con `lanzador.py` (ver "Inicio rápido"); estos comandos
son el modo avanzado — cada uno tiene su equivalente numerado en el menú:

```bash
# Diagnóstico rápido (no gasta tokens salvo con --test-llm)
python3 main.py --diagnostico
python3 main.py --diagnostico --test-llm

# Verificar conectores de archivos en vivo
python3 main.py --probar-conectores

# Probar la cascada OCR 100% local con UN PDF tuyo (coste $0)
python3 main.py --probar-ocr partida.pdf --manuscrito

# Ver la frontera priorizada (qué investigar primero)
python3 main.py --frontera

# Reclasificar los hallazgos ya guardados con el clasificador de
# evidencia (GRATIS: 0 tokens, 0 red; backup .bak del anterior)
python3 main.py --reclasificar

# Fase 1 (búsqueda exhaustiva) + Fase 2 (refinado + GEDCOM)
python3 main.py

# Solo una fase
python3 main.py --fase 1
python3 main.py --fase 2

# Más profundidad por persona
python3 main.py --max-steps 20

# Filtrar a personas concretas
python3 main.py --personas "Isidro Merillas Panero,Obdulia Pelaz Merino"

# AUTOPILOTO: N ciclos completos (fase1 -> fase2 -> commit -> frontera)
# Imprescindible combinarlo con --presupuesto-max para paradas seguras.
python3 main.py --ciclo 5 --presupuesto-max 5.0

# COMMIT manual de lo verificado en el último arbol_refinado.json
python3 main.py --aceptar

# Generar emails de solicitud de partidas no online
python3 main.py --solicitudes

# Hipótesis de tatarabuelos a partir del Catastro de Ensenada (1752)
python3 main.py --ensenada

# Transcribir fotos de certificados propios (documentos_propios/) con OCR
# local (v10.0: gratis; sin motor quedan PENDIENTES con aviso)
python3 main.py --importar-propios

# Repetir búsquedas aunque estén en caché
python3 main.py --sin-cache
```

## Archivos que genera

| Archivo | Contenido |
|---|---|
| `corpus_bruto.json` | fragmentos relevantes limpios (entrada de fase 2) |
| `informe_fase1.json` | consultas ejecutadas por objetivo |
| `arbol_hallazgos.json` | hallazgos estructurados (modelo de fase 2) |
| `arbol_refinado.json` | consolidación: confirmado/estimado/contradicciones |
| `arbol.ged` | árbol en GEDCOM 5.5.1 con citas SOUR |
| `cache_agente.db` | URLs vistas, consultas hechas, variantes, hallazgos por hash |
| `estado_investigacion.json` | frontera priorizada + candidatos (bucle agéntico) |
| `informe_progreso.md` | profundidad por línea + próximo paso recomendado |
| `solicitudes.json/.md` | plantillas de email para partidas no online |
| `candidatos_ensenada.json` | hipótesis de tatarabuelos del catastro de 1752 |

## Compatibilidad con la v3.x

- **Formato de `familia_conocida.json`**: sin cambios. Tu JSON existente
  funciona tal cual.
- **CLI existente**: todos los flags de la v3.x siguen funcionando
  (`--fase`, `--max-steps`, `--personas`, `--diagnostico`,
  `--solicitudes`, `--presupuesto-max`, `--ciclo`, `--frontera`,
  `--aceptar`, `--ensenada`, `--importar-propios`,
  `--probar-conectores`, `--sin-cache`, `--test-llm`).
- **Caché SQLite (`cache_agente.db`)**: compatible al 100%. Las
  consultas ya cacheadas en la v3.x no se repiten.
- **Modelos por defecto**: `deepseek/deepseek-v4-flash` (fase 1) y
  `deepseek/deepseek-v4.1-flash` (fase 2, desde la v9.2), precios
  verificados en OpenRouter el 2026-09-11. Desde la v10.0 NO hay modelo
  de visión: el OCR es 100% local.
- **v10.0**: `--con-gemini` desaparece (no hay escalada a la nube que
  activar) y `OCR_MODO` ya no existe.

## Migración desde v3.x

1. Copia tu `familia_conocida.json` y `.env` a la carpeta `genealogia_v4/`.
2. Copia también `cache_agente.db` si quieres conservar la caché.
3. `pip install -r requirements.txt`
4. `python3 main.py --diagnostico` para verificar que todo sigue OK.
5. Si todo pasa, ya puedes ejecutar `python3 main.py --ciclo 5
   --presupuesto-max 5.0` y dejarlo trabajar.
