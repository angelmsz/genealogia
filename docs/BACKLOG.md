# BACKLOG DEL PROYECTO GENEALÓGICO

---

## Hallazgos de Auditoría (Septiembre 2026)

### Riesgos del Lote sin Pushear (origin/main..HEAD)
- [ ] **[R-01]** (`config.py:990`): Acotar `cerrar_sesiones_hijas()` para no cerrar sesiones HTTP de hilos activos ajenos al pool actual.
- [ ] **[R-02]** (`agent/fase2.py:72`): Añadir parámetro `forzar: bool = False` a `_guardar_json()` para permitir vaciados intencionados de estado.
- [ ] **[R-03]** (`agent/fase2.py:317`): Diferenciar en `hallazgos_por_hash` entre extracciones vacías legítimas y fallos transitorios antes de limpiar la caché.
- [ ] **[R-04]** (`config.py:140`): Implementar `__bool__` en `Perezoso` para reflejar con precisión si el cliente dispone de credenciales.
- [ ] **[R-05]** (`lanzador.py`): Añadir shim `lanzador.py` con aviso informativo y redirección limpia a `menu_principal.py`.
- [ ] **[R-06]** (`main.py:632`): Añadir flag no interactivo (`-y` / `--si`) para comandos de mantenimiento con confirmación por teclado.
- [ ] **[R-07]** (`config.py:353`): Añadir rotación o salvaguarda temporal en `escribir_con_backup` para evitar pisado de `.bak` en ráfagas.
- [ ] **[R-08]** (`menu_principal.py:68`): Supeditar el reporte de ficheros generados al código de salida exitoso (`returncode == 0`) del subproceso.
- [ ] **[R-09]** (`config.py:326`): Unificar menciones dispersas de versión (v10.2 / v10.4.1 / v10.4.2 / v10.5) en torno a `config.VERSION`.

### Hallazgos de Arquitectura, Calidad y Certificación
- [ ] **[A-01]** (`agent/evidencia.py:341`): [CRÍTICO] `progenitores_confirmados()` debe exigir que padre y madre estén formalmente casados o emparejados en el árbol.
- [ ] **[A-02]** (`agent/frontera.py:944`): [CRÍTICO] `_es_verificador()` debe exigir `NIVEL_CONFIRMADO` (>=2 datos) y rechazar `candidato_fuerte` en el commit.
- [ ] **[A-03]** (`agent/evidencia.py:243`): `_dato_relacionados()` debe filtrar por el rol sintáctico e ignorar menciones de padrinos o testigos como si fueran padres.
- [ ] **[A-04]** (`scrapers/familysearch.py:378`): [CRÍTICO] `recolector_familysearch()` no debe llamar a `_marcar_conector()` cuando falten credenciales o falle la sesión.
- [ ] **[A-05]** (`agent/evidencia.py:423`): `clasificar_hallazgo()` no debe otorgar `NIVEL_CONFIRMADO` solo por (Nombre + Municipio) en fichas sin año de nacimiento conocido.
- [ ] **[A-06]** (`agent/evidencia.py:397`): `clasificar_hallazgo()` debe pasar `anio` en matrimonios y defunciones para permitir desambiguar homónimos intergeneracionales.
- [ ] **[A-07]** (`main.py:98`, `agent/gedcom.py:829`): Proteger escrituras en `familia_conocida.json` utilizando `config.escribir_con_backup`.
- [ ] **[A-08]** (`scrapers/archivos.py:439`): `recolector_addo()` debe consultar también el apellido materno e inferir provincia Palencia desde topónimos conocidos.
- [ ] **[A-09]** (`config.py:990`): Evitar cierre de sockets de hilos paralelos activos en `cerrar_sesiones_hijas`.
- [ ] **[A-10]** (`utils/llm.py:358`): Controlar la acumulación de hilos daemon huérfanos tras timeouts en `_llamada_con_hard_timeout`.
- [ ] **[A-11]** (`config.py:140`): Corregir evaluación de verdad en `Perezoso` para que `bool(cliente)` no devuelva siempre `True`.
- [ ] **[A-12]** (`scrapers/archivos.py:492`): Evitar llamada GET redundante a `_pares_sesion()` si la sesión ya tiene cookies activas.
- [ ] **[A-13]** (`agent/evidencia.py:316`): En matrimonios, verificar filiación explícita de padres a su respectivo contrayente antes de confirmar.
- [ ] **[A-14]** (`scrapers/archivos_provinciales.py:180`): Utilizar `escribir_con_backup` al volcar `candidatos_ensenada.json`.
- [ ] **[A-15]** (`agent/evidencia.py:99`): Ampliar `RE_ANIO` a `1[2-9]\d{2}` para soportar dataciones medievales (siglos XIII-XIV).
- [ ] **[A-16]** (`menu_principal.py:173`): Sustituir inyección dinámica de string en subproceso por función de diagnóstico modular.
- [ ] **[A-17]** (`resumen_noche.py:241`): Corregir texto de recomendación sustituyendo `lanzador.py` por las vías vigentes de menú.
