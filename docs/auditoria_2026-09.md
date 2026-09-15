# INFORME DE AUDITORÍA DE ARQUITECTURA, CALIDAD Y CERTIFICACIÓN GENEALÓGICA
**Fecha**: Septiembre 2026  
**Rama auditada**: `main` (9 commits por delante de `origin/main`)  
**Rol**: Auditor de Arquitectura y Calidad (Solo lectura y análisis)  
**Documento**: `docs/auditoria_2026-09.md`

---

## 1. Resumen Ejecutivo y Alcance

El repositorio implementa un agente autónomo y modular de investigación genealógica en España (siglos XVI-XX). Tras incidentes críticos observados en ejecuciones reales (12 y 13 de septiembre de 2026) —donde se documentó pérdida de hallazgos por sobreescrituras vacías, envenenamiento de caché por fallos temporales de proveedores de LLM, ejecuciones accidentales con gasto real en suites de prueba y saturación de conexiones HTTP compartidas— se incorporaron 9 commits locales (`origin/main..HEAD`).

Esta auditoría evalúa de forma independiente:
1. **El lote de commits sin pushear (`origin/main..HEAD`)** como segundo revisor, catalogando riesgos concretos de regresión, fugas y efectos colaterales (IDs `R-01` a `R-09`).
2. **La arquitectura global, la calidad del código y la observancia del Genealogical Proof Standard** (criterio fundamental: no certificar filiaciones ni incorporar personas sin al menos 2 datos independientes no circulares), catalogando los hallazgos del sistema (IDs `A-01` a `A-17`).

---

## 2. Revisión de Commits sin Pushear (`origin/main..HEAD`) — Segundo Revisor

Lote de commits analizado:
- `5864c54` tests: harness aislado y dos tests que leian el estado real del usuario
- `8b4cbcf` BLOQUE 0: la cache de hallazgos ya no guarda un FALLO como si fuera un resultado
- `30571c4` BLOQUE 1 (arreglo 1): el bot funciona sin .env y exige las claves solo cuando va a gastar
- `bd16f4e` BLOQUE 2 (arreglo 2): una sesion HTTP por hilo y cierre de las del pool al apagarlo
- `40f3b83` proteccion de los ficheros de estado: .bak antes de escribir y nunca perder contenido por un resultado vacio
- `b080102` BLOQUE 3b: el menu se queda con lo que usaba el lanzador (opciones 12-15) y dice que ha generado cada accion
- `52ffef1` BLOQUE 3c: se retira el menu antiguo (lanzador.py) y queda menu_principal.py como unico menu
- `dd2ff8e` PROPUESTA 1: --limpiar-cache-hallazgos (desenvenenar la cache de extraccion, con copia previa y confirmacion)
- `dba5a8f` PROPUESTA 3 (parcial): los carteles de version salen de config.VERSION

### Catálogo de Riesgos del Diff (`R-01` a `R-09`)

#### [R-01] Aborto indiscriminado de sesiones en hilos concurrentes ajenos
- **Ubicación**: `config.py:990-1009` (`cerrar_sesiones_hijas()`) invocado por `sesiones_hilo_limpias()`.
- **Riesgo**: `cerrar_sesiones_hijas()` itera sobre `_SESIONES` cerrando y purgando **todas** las sesiones cuyo `ident != actual`. No discrimina si los hilos pertenecen al `ThreadPoolExecutor` que acaba de cerrar o si corresponden a otros hilos concurrentes del proceso (por ejemplo, hilos de OCR en segundo plano o hilos daemon de timeouts de LLM que sigan en curso). Si otro hilo está ejecutando una petición HTTP legítima, su socket keep-alive se cierra de golpe provocando un `RemoteDisconnected` o `ConnectionError` espurio.
- **Mitigación recomendada**: Que `sesiones_hilo_limpias()` o el executor registren el conjunto exacto de IDs de hilo que crearon, o que los propios hilos cierren su sesión al terminar mediante un hook de finalización de tarea.

#### [R-02] Imposibilidad de vaciado intencional de ficheros de estado
- **Ubicación**: `agent/fase2.py:72-83` (`_guardar_json()`) y `config.py:388-394` (`tenia_contenido()`).
- **Riesgo**: Para evitar el incidente del 13/09 (donde `arbol_hallazgos.json` se sobreescribió con `[]`), la función rechaza escribir si los datos vienen vacíos y el fichero existente tenía contenido, devolviendo `False`. Esto genera un bloqueo: si un usuario o pipeline intencionadamente desea reiniciar la investigación o vaciar los hallazgos desde el código o CLI, el sistema se niega en silencio a persistir el vaciado sin ofrecer un parámetro `forzar=True`.
- **Mitigación recomendada**: Añadir un parámetro opcional `forzar: bool = False` a `_guardar_json()` para operaciones de reinicio explícito.

#### [R-03] Pérdida de extracciones vacías legítimas al limpiar la caché
- **Ubicación**: `agent/fase2.py:317-340` (`_fila_sin_hallazgos()` y `limpiar_cache_hallazgos()`).
- **Riesgo**: La función clasifica como "inútil" cualquier fila donde `hallazgos` sea `[]`. Sin embargo, no distingue entre una fila que quedó vacía por fallo del proveedor de IA y un documento que fue correctamente analizado por el LLM y que honestamente no contiene hechos genealógicos. Ejecutar `--limpiar-cache-hallazgos` borra ambos, forzando a pagar tokens nuevamente por textos irrelevantes en la siguiente pasada de fase 2.
- **Mitigación recomendada**: Diferenciar en la tabla `hallazgos_por_hash` el estado del lote (`exito_vacio` vs `fallo_transitorio`).

#### [R-04] Evaluación booleana anómala en proxy `Perezoso`
- **Ubicación**: `config.py:140-175` (`class Perezoso`).
- **Riesgo**: La clase proxy `Perezoso` solo delega atributos mediante `__getattr__`, pero no implementa `__bool__`. En Python, cualquier instancia de una clase sin `__bool__` ni `__len__` evalúa siempre como `True`. Si algún módulo realiza una comprobación de tipo `if tavily:` o `if llm:`, asumirá que el cliente está listo y configurado, y el error de credenciales ausentes estallará de manera diferida al intentar invocar un método.
- **Mitigación recomendada**: Implementar `__bool__` verificando la presencia de credenciales o configuración sin forzar la instanciación que lanza excepciones.

#### [R-05] Ruptura de scripts externos por eliminación de `lanzador.py` sin shim de compatibilidad
- **Ubicación**: `lanzador.py` (eliminado en commit `52ffef1`) y `lanzador.ps1`.
- **Riesgo**: Aunque `lanzador.ps1` fue redirigido a `menu_principal.py`, cualquier acceso directo existente en Windows, tarea programada, script de bash o llamada directa en terminal (`python lanzador.py`) fallará inmediatamente con `FileNotFoundError`.
- **Mitigación recomendada**: Mantener un archivo `lanzador.py` mínimo que imprima un aviso de migración y redirija la ejecución llamando a `menu_principal.main()`.

#### [R-06] Bloqueo de confirmación en entornos automatizados/no interactivos
- **Ubicación**: `main.py:632-640` (`_confirmar()`) en `--limpiar-cache-hallazgos`.
- **Riesgo**: `_confirmar()` lee de `sys.stdin` mediante `input()`. Si se ejecuta en entornos no interactivos (tuberías, cron, integración o subprocesos desatendidos), captura `EOFError` y asume el valor por defecto `False`, cancelando la operación sin posibilidad de pasar un modificador `--si` / `-y`.
- **Mitigación recomendada**: Añadir soporte para el argumento `--si` o flag `-y` en los comandos de mantenimiento de `main.py`.

#### [R-07] Histórico único en `escribir_con_backup` (.bak pisado en ráfagas)
- **Ubicación**: `config.py:353-370` (`escribir_con_backup()`).
- **Riesgo**: La función genera un único archivo con sufijo `.bak` mediante `shutil.copy2`. Si ocurren dos escrituras consecutivas en un breve lapso de tiempo (por ejemplo, guardado intermedio de fase 2 seguido del guardado final), el archivo `.bak` pasa a contener el estado intermedio y se destruye el respaldo del estado inicial de la sesión.
- **Mitigación recomendada**: Implementar rotación mínima o backup con marca temporal si el fichero `.bak` tiene menos de N minutos de antigüedad.

#### [R-08] Detección de cambios basada en mtime en `menu_principal.py`
- **Ubicación**: `menu_principal.py:68-80` (`FICHEROS_VIGILADOS`) y `ejecutar_comando_con_informe()`.
- **Riesgo**: El menú reporta ficheros como "generados o actualizados" basándose en variaciones de timestamp (`mtime`) y tamaño. Si un comando falla a mitad de camino dejando un fichero corrupto o vacío, el menú indicará al usuario que la acción "generó" el archivo, transmitiendo una falsa sensación de éxito.
- **Mitigación recomendada**: Supeditar el reporte de archivos modificados al código de salida exitoso (`returncode == 0`) del subproceso.

#### [R-09] Inconsistencias de versión en mensajes y documentación
- **Ubicación**: `config.py:326` (`VERSION = "10.4.1"`), `README.md`, `main.py`.
- **Riesgo**: El commit `dba5a8f` unificó los carteles con `config.VERSION`, pero múltiples docstrings, nombres de tests (`test_..._v105.py`) y secciones de documentación hacen referencia discontinua a "v10.2", "v10.4.1", "v10.4.2" y "v10.5", dificultando la trazabilidad técnica y la auditoría de versiones.
- **Mitigación recomendada**: Homogeneizar la referencia de versión en el repositorio y mantener una única variable canónica.

---

## 3. Catálogo Completo de Hallazgos de Arquitectura y Calidad (`A-01` a `A-17`)

### [A-01] Filiación confirmada entre padres no casados ni emparejados en el árbol
- **Fichero y línea**: `agent/evidencia.py:341-351` (y `agent/frontera.py:1158-1165`)
- **Síntoma**: `progenitores_confirmados()` valida la filiación de una persona nueva comprobando que el padre y la madre existan como fichas confirmadas en la familia, pero no verifica que ambos formen una pareja o matrimonio conocido, permitiendo que homónimos de diferentes ramas se unan y certifiquen hijos espurios.
- **Etiqueta**: `BUG`
- **Coste**: `M`
- **Toca lógica de certificación (>=2 datos independientes)**: `SÍ`
- **Arreglo mínimo propuesto**: En `progenitores_confirmados(h, familia)`, tras obtener `f_padre` y `f_madre`, comprobar que ambos progenitores estén vinculados en la memoria familiar: ya sea porque `normalizar(f_padre.get("conyuge", "")) == normalizar(f_madre.get("nombre", ""))` (o viceversa), o porque compartan al menos un nombre de hijo común en sus respectivas listas `hijos`. Si no existe dicho vínculo, devolver `None`.
- **Test que lo blindaría**:
  ```python
  def test_progenitores_confirmados_exige_pareja_vinculada():
      familia = {"personas": [
          {"nombre": "Juan Lopez", "conyuge": "Ana Perez", "evidencias": [{"tipo": "bautismo"}]},
          {"nombre": "Maria Garcia", "conyuge": "Pedro Ruiz", "evidencias": [{"tipo": "bautismo"}]}
      ]}
      h = {"tipo_evento": "bautismo", "persona": "Tomas Lopez Garcia",
           "otros_nombres": ["Juan Lopez (padre)", "Maria Garcia (madre)"]}
      assert progenitores_confirmados(h, familia) is None
  ```

### [A-02] Promoción indebida de `candidato_fuerte` a confirmación en commit
- **Fichero y línea**: `agent/frontera.py:944-955` (`_es_verificador`)
- **Síntoma**: `_es_verificador()` solo excluye hallazgos con nivel `coincidencia_debil`, permitiendo que un hallazgo con nivel `candidato_fuerte` (que por definición solo tiene 1 dato coincidente como el nombre, sin corroboración de fecha ni lugar) sea aceptado como verificador y cometa eventos y stubs al árbol.
- **Etiqueta**: `BUG`
- **Coste**: `S`
- **Toca lógica de certificación (>=2 datos independientes)**: `SÍ`
- **Arreglo mínimo propuesto**: Modificar `_es_verificador(h)` para exigir explícitamente `h.get("nivel_evidencia") == NIVEL_CONFIRMADO` (o `len(h.get("datos_que_casan", [])) >= 2`), impidiendo que candidatos no certificados documenten eventos definitivos en `familia_conocida.json`.
- **Test que lo blindaría**:
  ```python
  def test_commit_rechaza_candidato_fuerte_como_verificador(tmp_path, monkeypatch):
      h = {"persona": "Isidro Merillas", "tipo_evento": "bautismo",
           "nivel_evidencia": "candidato_fuerte", "verificacion_cita": "VERIFICADA",
           "cita_literal": "bautizado Isidro Merillas", "posible_homonimo": False}
      assert not _es_verificador(h)
  ```

### [A-03] Falsa corroboración parental por homonimia de testigos o padrinos
- **Fichero y línea**: `agent/evidencia.py:243-267` (`_dato_relacionados`)
- **Síntoma**: `_dato_relacionados()` busca los nombres de los padres o cónyuge en la cadena completa de `otros_nombres` sin comprobar el rol sintáctico, certificando una partida como válida cuando un pariente conocido aparece meramente como padrino, madrina o testigo.
- **Etiqueta**: `BUG`
- **Coste**: `M`
- **Toca lógica de certificación (>=2 datos independientes)**: `SÍ`
- **Arreglo mínimo propuesto**: Requerir que la coincidencia del nombre del familiar en `otros_nombres` corresponda a la etiqueta de rol respectiva (p. ej., que si se busca el padre, el elemento contenga `(padre)` o `hijo de`, y no etiquetas como `(padrino)` o `(testigo)`).
- **Test que lo blindaría**:
  ```python
  def test_dato_relacionados_ignora_rol_padrino_como_padre():
      p = {"nombre": "Antonio Pelaz", "padre": "Manuel Pelaz"}
      h = {"persona": "Antonio Pelaz", "otros_nombres": ["Manuel Pelaz (padrino)"]}
      assert _dato_relacionados(h, p) is None
  ```

### [A-04] Envenenamiento permanente de consultas en FamilySearch sin credenciales
- **Fichero y línea**: `scrapers/familysearch.py:378-385` (`recolector_familysearch`)
- **Síntoma**: Cuando se invoca FamilySearch sin credenciales o con sesión caída (`sin_credenciales`, `login_manual_requerido`, `login_fallo_credenciales`), se llama a `_marcar_conector(conn, clave)` registrando la consulta como completada para siempre en SQLite, impidiendo que el usuario vuelva a buscar en ese municipio cuando configure su cookie.
- **Etiqueta**: `BUG`
- **Coste**: `S`
- **Toca lógica de certificación (>=2 datos independientes)**: `NO`
- **Arreglo mínimo propuesto**: Llamar a `_marcar_conector(conn, clave)` exclusivamente cuando `res["estado"] == "catalogo_ok"`. En los estados donde falte autenticación, registrar aviso en log pero no persistir en `consultas_conectores` (o marcar como fallo temporal).
- **Test que lo blindaría**:
  ```python
  def test_familysearch_sin_credenciales_no_marca_consulta_hecha(conn_memoria):
      obj = {"municipio": "Vitoria"}
      recolector_familysearch(obj, conn=conn_memoria)
      assert not _consulta_conector_hecha(conn_memoria, "familysearch::catalogo::vitoria")
  ```

### [A-05] Certificación espuria por (Nombre + Municipio) en personas sin año de nacimiento
- **Fichero y línea**: `agent/evidencia.py:423-431` (`clasificar_hallazgo`)
- **Síntoma**: Si la ficha de una persona no tiene año de nacimiento (`anio_persona(p) is None`), el clasificador comprueba el nombre (Dato 1) y el lugar (Dato 2b); al cumplirse ambos, suma 2 datos y clasifica como `NIVEL_CONFIRMADO`, cayendo en la trampa de homonimia histórica entre antepasados y descendientes homónimos separados por siglos en el mismo pueblo.
- **Etiqueta**: `BUG`
- **Coste**: `M`
- **Toca lógica de certificación (>=2 datos independientes)**: `SÍ`
- **Arreglo mínimo propuesto**: Si `anio_persona(p)` es `None`, exigir que la fecha del hallazgo caiga dentro del rango generacional deducible (`_anios_linea`) o que concurra un dato parental/conyugal explícito; no considerar (Nombre + Municipio) por sí solos como suficientes para otorgar `NIVEL_CONFIRMADO`.
- **Test que lo blindaría**:
  ```python
  def test_clasificar_hallazgo_sin_anio_ficha_no_confirma_solo_por_municipio():
      familia = {"personas": [{"nombre": "Juan Merillas", "nacimiento": {"municipio": "Vitoria"}}]}
      h = {"persona": "Juan Merillas", "lugar": "Vitoria", "fecha_valor": "1620", "tipo_evento": "defuncion"}
      clasificar_hallazgo(h, familia)
      assert h["nivel_evidencia"] != "confirmado"
  ```

### [A-06] Omisión de desambiguación temporal en eventos que no son bautismos
- **Fichero y línea**: `agent/evidencia.py:397-400`
- **Síntoma**: `clasificar_hallazgo` solo suministra `anio=anio_h` a `emparejar_persona` si el evento es "nacimiento" o "bautismo", enviando `anio=None` en matrimonios y defunciones, lo que hace fracasar inmediatamente la desambiguación entre abuelo y nieto con el mismo nombre y descarta la vinculación legítima.
- **Etiqueta**: `BUG`
- **Coste**: `S`
- **Toca lógica de certificación (>=2 datos independientes)**: `SÍ`
- **Arreglo mínimo propuesto**: Enviar `anio=anio_h` siempre que `anio_h` esté presente, permitiendo que `emparejar_persona` considere la holgura biológica de vida según el tipo de evento en vez de renunciar a la desambiguación.
- **Test que lo blindaría**:
  ```python
  def test_clasificar_hallazgo_desambigua_defuncion_por_anio():
      familia = {"personas": [
          {"nombre": "Pedro Ruiz", "nacimiento": {"fecha_aproximada": "1800"}},
          {"nombre": "Pedro Ruiz", "nacimiento": {"fecha_aproximada": "1870"}}
      ]}
      h = {"persona": "Pedro Ruiz", "tipo_evento": "defuncion", "fecha_valor": "1882"}
      clasificar_hallazgo(h, familia)
      assert "1800" in h.get("justificacion_evidencia", "") or len(h.get("datos_que_casan", [])) > 0
  ```

### [A-07] Escritura sin respaldo (.bak) en `familia_conocida.json`
- **Fichero y línea**: `main.py:98-99` (`_asegurar_ids_estables`) y `agent/gedcom.py:829-831` (`importar_documentos_propios`)
- **Síntoma**: Ambas funciones escriben directamente en `familia_conocida.json` mediante `Path.write_text()` sin generar copia previa `.bak` ni utilizar `config.escribir_con_backup`, arriesgando la corrupción o truncado del fichero maestro ante cortes de ejecución.
- **Etiqueta**: `RIESGO`
- **Coste**: `S`
- **Toca lógica de certificación (>=2 datos independientes)**: `NO`
- **Arreglo mínimo propuesto**: Reemplazar las llamadas directas `(BASE_DIR / FAMILIA_JSON_PATH).write_text(...)` por `escribir_con_backup(BASE_DIR / FAMILIA_JSON_PATH, ...)`.
- **Test que lo blindaría**:
  ```python
  def test_asegurar_ids_estables_genera_backup(tmp_path, monkeypatch):
      ruta = tmp_path / "familia_conocida.json"
      ruta.write_text('{"personas": [{"nombre": "Ana"}]}', encoding="utf-8")
      monkeypatch.setattr("config.BASE_DIR", tmp_path)
      _asegurar_ids_estables()
      assert (tmp_path / "familia_conocida.json.bak").exists()
  ```

### [A-08] Conector ADDO ignora el apellido materno y omite municipios de Palencia no etiquetados
- **Fichero y línea**: `scrapers/archivos.py:439-445` (`recolector_addo`)
- **Síntoma**: `recolector_addo` solo consulta el `apellido_paterno` (descartando investigaciones por rama materna) y aborta si la clave `provincia` no dice literalmente `palencia`, omitiendo búsquedas de localidades típicamente palentinas (como Castrejón de la Peña o Roscales) que carecen de etiqueta provincial explícita.
- **Etiqueta**: `BUG`
- **Coste**: `M`
- **Toca lógica de certificación (>=2 datos independientes)**: `NO`
- **Arreglo mínimo propuesto**: Incluir resolución de provincia a partir de topónimos conocidos de Palencia e iterar sobre los apellidos paterno y materno no vacíos.
- **Test que lo blindaría**:
  ```python
  def test_recolector_addo_busca_apellido_materno_y_municipio_palentino(monkeypatch):
      obj = {"apellido_paterno": "Perez", "apellido_materno": "Merillas",
             "municipio": "Castrejón de la Peña", "provincias": []}
      consultas = []
      monkeypatch.setattr("config.SESSION.get", lambda url, **kw: consultas.append(url))
      recolector_addo(obj, conn=None)
      assert any("Merillas" in u for u in consultas)
  ```

### [A-09] Cierre indiscriminado de sockets en `cerrar_sesiones_hijas`
- **Fichero y línea**: `config.py:990-1009` (`cerrar_sesiones_hijas`)
- **Síntoma**: `cerrar_sesiones_hijas()` destruye las sesiones HTTP de cualquier hilo registrado en `_SESIONES` que no sea el hilo llamador, abortando transferencias activas en caso de que existan hilos de trabajo paralelos.
- **Etiqueta**: `RIESGO`
- **Coste**: `S`
- **Toca lógica de certificación (>=2 datos independientes)**: `NO`
- **Arreglo mínimo propuesto**: Modificar `sesiones_hilo_limpias()` para registrar los `thread.ident` de los hilos generados por el contexto actual y cerrar únicamente las sesiones asociadas a esos identificadores.
- **Test que lo blindaría**: Test de concurrencia asegurando que un hilo independiente no ve su sesión cerrada por la finalización de un pool ajeno.

### [A-10] Fuga de hilos daemon y consumo oculto ante timeouts de LLM
- **Fichero y línea**: `utils/llm.py:358-371` (`_llamada_con_hard_timeout`)
- **Síntoma**: Cada timeout lanza un hilo daemon huérfano que permanece bloqueado en la llamada de red de OpenAI; si se acumulan reintentos o modelos colgados, se satura el pool de descriptores del sistema operativo y pueden facturarse peticiones completadas tardíamente.
- **Etiqueta**: `DEUDA`
- **Coste**: `M`
- **Toca lógica de certificación (>=2 datos independientes)**: `NO`
- **Arreglo mínimo propuesto**: Reutilizar un `ThreadPoolExecutor` de tamaño acotado en lugar de instanciar hilos daemon libres en cada llamada, o cancelar activamente la petición HTTP subyacente.
- **Test que lo blindaría**: Test de invocación con timeout que verifique que el número de hilos vivos no crece linealmente con los reintentos.

### [A-11] Verificación de verdad falseada en proxy `Perezoso`
- **Fichero y línea**: `config.py:140-175` (`class Perezoso`)
- **Síntoma**: `bool(tavily)` o `bool(llm)` siempre retorna `True` porque la clase proxy no define `__bool__`, burlando guardas defensivas en código que intente comprobar si el cliente está disponible.
- **Etiqueta**: `BUG`
- **Coste**: `S`
- **Toca lógica de certificación (>=2 datos independientes)**: `NO`
- **Arreglo mínimo propuesto**: Definir `__bool__(self)` verificando de forma segura si las claves necesarias existen en el entorno sin disparar excepciones de inicialización.
- **Test que lo blindaría**:
  ```python
  def test_perezoso_evaluacion_booleana_con_clave_vacia():
      p = Perezoso(lambda: None, "test")
      # Debe retornar False si no está inicializado ni configurado
  ```

### [A-12] Petición redundante de apertura de sesión en cada consulta a PARES
- **Fichero y línea**: `scrapers/archivos.py:492-504` (`_pares_sesion`)
- **Síntoma**: Cada consulta individual a Ensenada ejecuta una petición `GET` completa a `PARES_CATASTRO` para obtener cookies, ignorando si la sesión HTTP actual ya tiene cookies válidas en caché.
- **Etiqueta**: `DEUDA`
- **Coste**: `S`
- **Toca lógica de certificación (>=2 datos independientes)**: `NO`
- **Arreglo mínimo propuesto**: Comprobar si `SESSION.cookies` ya posee las cookies de sesión requeridas antes de emitir la llamada GET de inicialización.
- **Test que lo blindaría**: Comprobar que en dos llamadas consecutivas `_pares_sesion` solo realiza una petición GET.

### [A-13] Atribución ambigua de progenitores en partidas matrimoniales
- **Fichero y línea**: `agent/evidencia.py:316-335`
- **Síntoma**: Si un extracto matrimonial solo rescata a los padres de uno de los contrayentes, `progenitores_confirmados()` asume ciegamente que corresponden a `h["persona"]`, pudiendo atribuir los padres del novio a la novia.
- **Etiqueta**: `BUG`
- **Coste**: `M`
- **Toca lógica de certificación (>=2 datos independientes)**: `SÍ`
- **Arreglo mínimo propuesto**: En eventos de matrimonio, verificar que el rol de los padres en el texto o extracto indique explícitamente a cuál de los dos contrayentes filian antes de vincularlos.
- **Test que lo blindaría**: Comprobar que padres del contrayente masculino no se asignen a la contrayente femenina en partidas matrimoniales asimétricas.

### [A-14] Falta de copia de seguridad en `candidatos_ensenada.json`
- **Fichero y línea**: `scrapers/archivos_provinciales.py:180-195` y `main.py:866-872`
- **Síntoma**: La exportación del Catastro de Ensenada sobreescribe el fichero JSON sin utilizar la rutina `escribir_con_backup`.
- **Etiqueta**: `RIESGO`
- **Coste**: `S`
- **Toca lógica de certificación (>=2 datos independientes)**: `NO`
- **Arreglo mínimo propuesto**: Usar `escribir_con_backup` al volcar `candidatos_ensenada.json`.
- **Test que lo blindaría**: Verificar creación de `.bak` tras ejecutar `generar_candidatos_ensenada()`.

### [A-15] Expresión regular `RE_ANIO` omite dataciones medievales previas al siglo XV
- **Fichero y línea**: `agent/evidencia.py:99` y `utils/personas.py:40`
- **Síntoma**: El patrón `r"\b(1[4-9]\d{2}|20\d{2})\b"` descarta cualquier año anterior a 1400, ignorando dataciones de los siglos XIII y XIV en pleitos de hidalguía antiguos de PARES.
- **Etiqueta**: `DEUDA`
- **Coste**: `S`
- **Toca lógica de certificación (>=2 datos independientes)**: `NO`
- **Arreglo mínimo propuesto**: Ajustar el regex a `r"\b(1[2-9]\d{2}|20\d{2})\b"`.
- **Test que lo blindaría**: `assert _anio_de("nacido en 1385") == 1385`.

### [A-16] Inyección de script dinámico en subproceso en `menu_principal.py`
- **Fichero y línea**: `menu_principal.py:173-190` (`CODIGO_DEPENDENCIAS`)
- **Síntoma**: La verificación de dependencias genera un bloque de código Python en un string formateado y lo pasa como argumento `-c` a Python, en lugar de invocar una función limpia o módulo específico.
- **Etiqueta**: `COSMÉTICO`
- **Coste**: `S`
- **Toca lógica de certificación (>=2 datos independientes)**: `NO`
- **Arreglo mínimo propuesto**: Encapsular el chequeo de módulos en un script auxiliar o comando de inspección estructurado.
- **Test que lo blindaría**: Test unitario del reporte de dependencias en subproceso.

### [A-17] Referencia anacrónica a `lanzador.py` en `resumen_noche.py`
- **Fichero y línea**: `resumen_noche.py:241`
- **Síntoma**: El texto de recomendación sigue sugiriendo arrancar con `lanzador.py`, fichero que ha sido eliminado del repositorio.
- **Etiqueta**: `COSMÉTICO`
- **Coste**: `S`
- **Toca lógica de certificación (>=2 datos independientes)**: `NO`
- **Arreglo mínimo propuesto**: Actualizar el mensaje a "menú (Menu.bat o lanzador.ps1)".
- **Test que lo blindaría**: `assert "lanzador.py" not in resumen_siguiente()`.

---

## 4. Matriz de Priorización e Impacto

| ID | Fichero:Línea | Categoría | Coste | Certificación (>=2 datos) | Urgencia |
|---|---|---|:---:|:---:|:---:|
| **A-01** | `agent/evidencia.py:341` | BUG | M | **SÍ** | **Crítica (Urgente 1)** |
| **A-02** | `agent/frontera.py:944` | BUG | S | **SÍ** | **Crítica (Urgente 2)** |
| **A-04** | `scrapers/familysearch.py:378` | BUG | S | NO | **Crítica (Urgente 3)** |
| **A-03** | `agent/evidencia.py:243` | BUG | M | **SÍ** | Alta |
| **A-05** | `agent/evidencia.py:423` | BUG | M | **SÍ** | Alta |
| **A-06** | `agent/evidencia.py:397` | BUG | S | **SÍ** | Alta |
| **A-07** | `main.py:98` | RIESGO | S | NO | Media |
| **A-08** | `scrapers/archivos.py:439` | BUG | M | NO | Media |
| **A-09** | `config.py:990` | RIESGO | S | NO | Media |
| **A-11** | `config.py:140` | BUG | S | NO | Media |
| **A-13** | `agent/evidencia.py:316` | BUG | M | **SÍ** | Media |
| **A-10** | `utils/llm.py:358` | DEUDA | M | NO | Baja |
| **A-12** | `scrapers/archivos.py:492` | DEUDA | S | NO | Baja |
| **A-14** | `scrapers/archivos_provinciales.py:180` | RIESGO | S | NO | Baja |
| **A-15** | `agent/evidencia.py:99` | DEUDA | S | NO | Baja |
| **A-16** | `menu_principal.py:173` | COSMÉTICO | S | NO | Baja |
| **A-17** | `resumen_noche.py:241` | COSMÉTICO | S | NO | Baja |
