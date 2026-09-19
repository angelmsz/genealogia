# COMPATIBILIDAD DE LAS DOS FICHAS DE BAUTISMO DE ARTXIBO.EUSKADI.EUS CON EL ÁRBOL CONOCIDO

**Fecha de consulta**: 19 de septiembre de 2026
**Portal**: `artxibo.euskadi.eus` (Dokuklik / IRARGI) — buscador de registros sacramentales que
publica el **Archivo Histórico Diocesano de Vitoria (AHDV-GEAH)**, con indexación nominal del
periodo **1481-1900**. Acceso libre y gratuito, sin registro.
**Ficheros crudos guardados** (para los tests del conector, bloque 2):

| Fichero | Qué es | Bytes |
|---|---|---|
| `tests/fixtures/artxibo_bautismo_6210597.html` | Ficha de bautismo `bauid=6210597` | 14.426 |
| `tests/fixtures/artxibo_bautismo_6210615.html` | Ficha de bautismo `bauid=6210615` | 14.468 |
| `tests/fixtures/artxibo_buscador_maintSimple.html` | Formulario del buscador (estructura de campos) | 77.995 |
| `tests/fixtures/artxibo_busqueda_bautismo_apellido_compuesto.json` | Respuesta real del buscador para el apellido completo (79 filas) | 92.328 |
| `tests/fixtures/artxibo_busqueda_bautismo_apellido_token.json` | Respuesta real para el token suelto `Saenz` (5 de 7.009 filas) | 5.974 |
| `tests/fixtures/artxibo_busqueda_bautismo_perez_de_palomares.json` | Los 2 únicos registros del otro apellido compuesto | 2.556 |
| `tests/fixtures/artxibo_busqueda_bautismo_1755_1765.json` | Ventana 1755-1765: localiza la parroquia de la ficha 2 | 8.567 |

> **AVISO DE PRIVACIDAD**: este documento nombra a personas de la familia del autor del
> repositorio (siglos XVIII-XX) y el repositorio es **público**. Es el mismo criterio que ya se
> aplicó a `docs/auditoria_2026-09.md`. Si se prefiere, se puede anonimizar antes de subirlo.

---

## 0. Resumen para decidir en 30 segundos

1. **La ficha 6210597 sí es la línea paterna**: es el bautismo de **Víctor Sáenz de Navarrete
   Dopico**, el **10-03-1885**, en la parroquia de **La Purísima Concepción de Navaridas**
   (Álava). Veredicto: **COMPATIBLE CON RESERVAS**.
2. Esa ficha es del **bisabuelo** (P0012), no del tatarabuelo, pero **nombra a los
   tatarabuelos**: *Eusebio Sáenz de Navarrete* y *Leocadia Dopico*. Ambos tienen además su
   propio bautismo indexado (1858 y 1863), así que la línea se puede subir **dos generaciones
   más** en cuanto se pidan las copias.
3. **La ficha 6210615 no es de nadie del árbol**: es un bautismo de **1760** en la **misma
   parroquia**, con el mismo apellido compuesto. Ni casa ni contradice: **INCOMPATIBLE con la
   frontera actual** (y desde luego no puede ser un tatarabuelo, que estarían hacia 1850-1865).
4. El dato que lo cambia todo es **la parroquia**: **Navaridas**, un pueblo de la Rioja Alavesa
   a 45 km de Vitoria. El árbol asumía «Vitoria» y el índice dice Navaridas.
5. **La clave del apellido compuesto, medida**: buscar «Sáenz de Navarrete» entero devuelve
   **79** registros (1850-1900); buscar «Sáenz» suelto devuelve **7.009**. La fragmentación que
   denunció la auditoría multiplica el ruido por **×89**.

---

## 1. Qué trae (y qué no trae) una ficha de artxibo

La ficha es HTML estático servido por el portal (`.../bautismo/getFicha?bauid=<id>`) y sus datos
vienen en filas `div.row` con una etiqueta (`div.tabla-titulo`) y un valor (`div.tabla-texto`).
Campos presentes en las dos fichas:

`Fondo`, `Diócesis`, `Territorio`, `Hijo` (bautizado: nombre + 2 apellidos), `Padre`,
`Madre`, `Título` (Bautizados), `Fecha del sacramento`, `Folio/Página`, `Nº Partida`,
`Código referencia`, `Signatura`, `Sig. Microfilm`, `Sig. Digital`, `Registro informático`,
`Observaciones`.

**Campos que NO traen** (importante para no inventar):

- **Parroquia / localidad / municipio**: no están en la ficha. Se obtienen de la **fila del
  buscador** (el portal los devuelve en el JSON interno `busquedaBautismo` como
  `bauparroquia`, `baulocalidad`, `baumunicipio`, `baudiocesis`, `bausignatura`).
- **Abuelos**: no están en la ficha. Solo aparecen en la **copia literal** del libro (o
  deducidos cruzando bautismos del índice, como se hace en el apartado 2.5, siempre marcado
  como inferencia).
- **Padrinos**: no están en la ficha. También son cosa de la copia literal.
- **Fecha de nacimiento**: la ficha da la **fecha del sacramento** (el bautismo), que en el
  siglo XIX solía ser días después del nacimiento. No es la fecha de nacimiento.

---

## 2. Ficha 1 — `bauid = 6210597`

- URL: `https://www.artxibo.euskadi.eus/webartxi00-container/es/ad53aArchivoHistoricoWar/bautismo/getFicha?bauid=6210597`
- Ficha equivalente en el AHDV: `http://internet.ahdv-geah.org/paginas/indexacion/n_ficha_bautismos.php?id_bautismo=577656`

### 2.1. Campos estructurados

| Campo | Valor |
|---|---|
| Hijo (bautizado) | **Víctor Sáenz de Navarrete Dopico** |
| Fecha del sacramento | **10-03-1885** |
| Padre | **Eusebio Sáenz de Navarrete** (sin segundo apellido en la ficha) |
| Madre | **Leocadia Dopico** (sin segundo apellido en la ficha) |
| Parroquia | **La Purísima Concepción** *(de la fila del buscador, no de la ficha)* |
| Localidad / Municipio | **Navaridas / Navaridas** *(de la fila del buscador)* |
| Territorio / Diócesis | **ÁLAVA / Vitoria** |
| Título | Bautizados |
| Fondo | **F006.329** |
| Signatura / Sig. digital | **0193200301 / 0193200301** |
| Folio / Página | **f.195 r.** |
| Código referencia | 23041 |
| Registro informático | 6210597 |
| Nº Partida / Sig. Microfilm / Observaciones | (vacío) |

### 2.2. Generación que aporta

```
Eusebio SÁENZ DE NAVARRETE  ⚭  Leocadia DOPICO          <- tatarabuelos (nombres nuevos)
  |
  +-- Víctor SÁENZ DE NAVARRETE DOPICO   baut. 10-03-1885, Navaridas
        (el P0012 del árbol)
```

Y del propio índice (apartado 2.5) se deduce su generación anterior:

```
Pablo SÁENZ DE NAVARRETE  ⚭  Josefa TELLAECHE           <- 3ª generación hacia atrás (inferido)
  +-- Eusebio SÁENZ DE NAVARRETE TELLAECHE   baut. 14-08-1858, Navaridas
```

### 2.3. Cotejo con `familia_conocida.json` y con la frontera (`estado_investigacion.json`)

| Dato | En la ficha | En el árbol / frontera | ¿Casa? |
|---|---|---|---|
| Nombre | Víctor Sáenz de Navarrete **Dopico** | P0012 = «Victor Saenz de Navarrete Dopico» | **SÍ, exacto** (nombre + los 2 apellidos) |
| Apellido 2 | Dopico (raro en Álava; de origen gallego, como anota P0012) | P0012, apellido_materno = «Dopico» | **SÍ** |
| Territorio | Álava (Navaridas) | Frontera de P0012: provincia «Alava» | **SÍ** (provincia) |
| Municipio | **Navaridas** | P0012: municipio vacío; la frontera asume «Vitoria» (por el hijo, n. 1927 en Vitoria) | **NO** (dato nuevo: el pueblo) |
| Fecha | bautismo **10-03-1885** | P0012: nacimiento **estimado 1895-1905**, «ESTIMACIÓN no confirmada» | **NO** (10-14 años de diferencia) |
| Padres | Eusebio Sáenz de Navarrete + Leocadia Dopico | P0012: padre y madre **vacíos** | dato nuevo |
| Cónyuge | no consta | Agustina Pérez de Palomares | sin dato |
| Hijos | no consta | Angel Saenz de Navarrete Perez de Palomares (n. 1927, Vitoria) | sin dato |

**Exclusividad del nombre, comprobada en todo el índice (1481-1900, archivo de Vitoria):**

- Bautizados llamados **Víctor** con primer apellido «Sáenz de Navarrete»: **2** en total
  (uno de 1878, con apellidos Ayala + Sáenz de Navarrete, que **no** es de esta línea).
- Bautizados llamados **Víctor Sáenz de Navarrete *Dopico***: **1** (esta ficha).
- Ventana 1895-1905 completa (por si el P0012 estimado fuera de ahí): **6 registros, ninguno
  llamado Víctor**.
- Bautizados **Eusebio Sáenz de Navarrete** en 1481-1900: **1** (el de 1858), y es el padre que
  figura en las cinco partidas de sus hijos (1885-1894).

### 2.4. Veredicto: **COMPATIBLE CON RESERVAS**

**Por qué COMPATIBLE**: casan **dos datos independientes** — el nombre completo (nombre + los
dos apellidos, con un segundo apellido poco frecuente) y el territorio — y en todo el índice
1481-1900 **existe un único** «Víctor Sáenz de Navarrete Dopico». La probabilidad de que sea
otra persona con exactamente esos tres elementos en la misma provincia es muy baja.

**Reservas (por qué no se puede certificar todavía)**:

1. La **fecha no encaja** con la estimación del árbol (1885 frente a 1895-1905). La estimación
   no estaba confirmada: salió de restar 28 años al nacimiento de su hijo (1927). Con la fecha
   real, Víctor tendría **42 años** al nacer Ángel: posible, pero es un dato a confirmar, no a
   asumir.
2. El **municipio del árbol («Vitoria») no es el de la ficha (Navaridas)**. Que los hijos
   nacieran en Vitoria es perfectamente compatible con que el padre se criara en Navaridas,
   pero vuelve a ser una hipótesis, no un dato.
3. **Falta la fuente que ata las dos personas**: el índice de artxibo **termina en 1900**
   (comprobado: bautismos 1901-1935 = **0** resultados), así que ni el matrimonio de Víctor con
   Agustina ni el nacimiento de Ángel (1927) están aquí. Sin ese eslabón, el vínculo
   bisabuelo ↔ esta partida no cumple la regla de **≥2 fuentes** del proyecto.

**Lo que convertiría esto en COMPATIBLE a secas** (cualquiera de las dos, mejor las dos):

- El **certificado literal de nacimiento de Ángel** (n. 1927, Vitoria): si nombra a sus padres
  y da la edad o la naturaleza de Víctor (p. ej. «de 42 años, natural de Navaridas»), la
  identificación queda cerrada. Es **gratis** (Registro Civil de Vitoria-Gasteiz).
- La **copia literal del bautismo de 1885**: dará los **abuelos paternos y maternos** del
  bautizado y los **padrinos**, con lo que se puede comprobar si la segunda generación encaja
  con el resto del clan (y si hubo dispensa o legitimación).

### 2.5. Familia que se puede encadenar con el índice (inferencias, marcadas como tales)

Todas en **La Purísima Concepción de Navaridas**:

| Persona | Fecha | `bauid` | Qué relación se infiere | Certeza |
|---|---|---|---|---|
| **Eusebio Sáenz de Navarrete Tellaeche** | 14-08-1858 | 6211434 | padre de Víctor (hijo de Pablo Sáenz de Navarrete + Josefa Tellaeche) | alta (único Eusebio de 1481-1900; edad 27 al nacer Víctor) |
| **Leocadia Dopico Guzmán** | 10-12-1863 | 5723968 | madre de Víctor (hija de Román María Dopico + Fermina Guzmán) | alta (única Leocadia Dopico; edad 21) |
| Pedro Sáenz de Navarrete Dopico | 22-05-1887 | 6210598 | hermano de Víctor | alta (mismos padres) |
| Yluminado Sáenz de Navarrete Dopico | 14-05-1889 | 6210599 | hermano | alta (mismos padres) |
| Primo Sáenz de Navarrete Dopico | 08-10-1891 | 6210600 | hermano | alta (mismos padres; el padre consta «Eusevio») |
| Benito Sáenz de Navarrete Dopico | 22-04-1894 | 6210601 | hermano | alta (mismos padres) |
| Alejandro Navarrete Dopico | 29-11-1896 | 6052733 | hermano | media (padre escrito «Eusebio Navarrete», sin «de») |
| Claudia / Blas / Catalina / León / **Eusebio** / Gabriel Sáenz de Navarrete Tellaeche | 1846-1862 | 6211433, 6211430, 6211432, 6211429, **6211434**, 6211431 | hermanos del padre de Víctor (hijos de Pablo + Josefa Tellaeche) | alta (mismos padres en 6 partidas) |

> Los `bauid` de esta tabla **no** están todavía incorporados a ningún fichero de estado: este
> bloque es solo informe.

---

## 3. Ficha 2 — `bauid = 6210615`

- URL: `https://www.artxibo.euskadi.eus/webartxi00-container/es/ad53aArchivoHistoricoWar/bautismo/getFicha?bauid=6210615`
- Ficha equivalente en el AHDV: `http://internet.ahdv-geah.org/paginas/indexacion/n_ficha_bautismos.php?id_bautismo=585725`

### 3.1. Campos estructurados

| Campo | Valor |
|---|---|
| Hijo (bautizado) | **Joseph Simon Sáenz de Navarrete González Moreno** |
| Fecha del sacramento | **26-10-1760** |
| Padre | **Joseph Sáenz de Navarrete** |
| Madre | **Theresa González Moreno** |
| Parroquia | **La Purísima Concepción** *(de la fila del buscador)* |
| Localidad / Municipio | **Navaridas / Navaridas** *(de la fila del buscador)* |
| Territorio / Diócesis | **ÁLAVA / Calahorra y la Calzada** |
| Fondo | **F006.329** |
| Signatura / Sig. digital | **0193200103 / 0193200103** |
| Folio / Página | **f.011 r. - v.** |
| Código referencia | 23066 |
| Registro informático | 6210615 |
| Nº Partida / Sig. Microfilm / Observaciones | (vacío) |

### 3.2. Cotejo con el árbol y con la frontera

| Dato | En la ficha | En el árbol | ¿Casa? |
|---|---|---|---|
| Apellido 1 | Sáenz de Navarrete | P0012 y P0013 llevan ese apellido compuesto | **SÍ, pero sin valor** (ver 3.3) |
| Parroquia | La Purísima Concepción, Navaridas | — | **coincide con la ficha 1** |
| Fecha | 1760 | El antepasado más antiguo del árbol está hacia 1880-1885 | **NO**: ~6-7 generaciones antes |
| Diócesis | **Calahorra y la Calzada** | Las fichas de 1858-1900 son de la diócesis de **Vitoria** | **discrepancia explicable** (ver abajo) |
| Padres | Joseph Sáenz de Navarrete + Theresa González Moreno | — | no hay con quién compararlos |
| Nombre | Joseph Simon | — | no existe esa persona en el árbol |

**La discrepancia de diócesis está explicada y no es un error**: Navaridas perteneció a la
diócesis de **Calahorra y la Calzada** hasta el reajuste de límites diocesanos del siglo XIX
(las parroquias de la Rioja Alavesa pasaron a Vitoria). Por eso la ficha de 1760 dice
«Calahorra y la Calzada» y la de 1885 dice «Vitoria», **en la misma parroquia**. Es un buen
ejemplo de por qué hay que buscar por parroquia y no por diócesis.

### 3.3. Veredicto: **INCOMPATIBLE** (con la frontera actual)

- **No puede ser un tatarabuelo**: un tatarabuelo del consultante está hacia **1850-1865**, no
  en 1760.
- **No identifica a nadie** del árbol: no hay ninguna persona nacida en el siglo XVIII en
  `familia_conocida.json` ni en la frontera con la que cotejar.
- **El apellido no es evidencia**: en la misma parroquia y en el mismo siglo hay **144**
  bautismos con el primer apellido «Sáenz de Navarrete» (1481-1900) y **79** solo en 1850-1900.
  Un apellido endémico de dos pueblos vecinos (Navaridas y Elciego) casa por azar tantas veces
  que la regla de ≥2 datos lo descarta como prueba. Por eso el diccionario del veredicto suma:
  **compartir apellido = 0 datos independientes**.

**Valor real que sí tiene** (no se descarta el documento, se descarta la identificación):

1. Sitúa la **raíz documental de la línea Sáenz de Navarrete en Navaridas al menos desde 1756**
   (los cuatro hermanos de Joseph Simón, hijos de los mismos Joseph + Theresa: 1756, 1758,
   1761 y 1764).
2. Da la **signatura exacta** (F006.329 / 0193200103, folio 11 r.-v.) por si algún día la
   investigación llega a esa generación: entonces será una pieza más, y habrá que pedirla junto
   con los libros de matrimonios y defunciones de la parroquia para reconstruir los eslabones.
3. Deja por escrito un nombre de la generación de 1794 que podría empalmar más arriba
   («Josef Sáenz de Navarrete», padre de un Pablo Sáenz de Navarrete García bautizado en
   Navaridas en 1794, `bauid` 6210605) — **sin ninguna prueba de parentesco con el Pablo
   Sáenz de Navarrete que en 1846-1862 bautiza hijos con Josefa Tellaeche**. Anotado como
   línea a explorar, no como ancestro.

---

## 4. La fragmentación de apellidos compuestos, medida en el portal real

Consultas reales al buscador de artxibo (archivo diocesano = Vitoria), sin filtro de años salvo
donde se indica:

| Apellido buscado | Registros | Comentario |
|---|---:|---|
| `Saenz de Navarrete` (completo) | **79** (1850-1900) / **144** (1481-1900) | el apellido entero funciona: 1 sola parroquia (Navaridas) + Elciego |
| `Saenz` (token) | **7.009** | **×89 más ruido** que el apellido completo |
| `Navarrete` (token) | **297** | tampoco es el apellido de la familia |
| `Perez de Palomares` (completo) | **2** | solo 2 en todo el índice: 1811 y 1820, **Salinas de Añana** |
| `Palomares` (token) | **204** | ×102 más ruido |
| `Pelaz` | **1** | apellido casi inexistente en Álava (la línea Pelaz es de Palencia) |

**Conclusión operativa**: buscar el apellido compuesto **primero** y solo recurrir a los tokens
como último recurso (y marcando esos resultados como de confianza baja) es lo que convierte
7.009 resultados en 79, y es exactamente el defecto que describía la auditoría estratégica
(los 56 homónimos de 1578-1646 del conector SIGA). La implementación va en el bloque 2.

Un aviso para la línea **Pérez de Palomares** (P0013, Agustina): en este índice **no está** —
solo aparecen 2 registros, y son de **Salinas de Añana** (1811 y 1820), como *madre*, no como
persona bautizada. Es decir: esa rama **no** está en Navaridas y habrá que buscarla por el
Registro Civil (Vitoria) o por el propio AHDV con otros criterios.

---

## 5. Cobertura real del índice (medido, no supuesto)

| Comprobación | Resultado |
|---|---|
| Bautismos «Saenz de Navarrete» 1481-1900 (archivo de Vitoria) | 144 |
| Bautismos «Saenz de Navarrete» 1901-1910 / 1911-1935 / 1901-1935 | **0 / 0 / 0** |
| Último bautismo localizado de la línea | 1900-10-08 (Elciego) |
| Matrimonios «Saenz de Navarrete» 1890-1930 | 4 (volúmenes cuyo año inicial es 1807-1899) |
| Defunciones «Saenz de Navarrete» 1880-1960 | 15 (todas de volúmenes iniciados entre 1821 y 1896) |

**Consecuencia**: el buscador sirve para **1481-1900**; todo lo del siglo XX (el matrimonio de
Víctor, el nacimiento de Ángel en 1927, las defunciones) hay que pedirlo al Registro Civil o al
AHDV como copia. Esto no invalida la ficha: la hace **necesitar compañía**.

---

## 6. Regla de ≥2 datos independientes: qué se certifica y qué no

| Ficha | Dato 1 | Dato 2 | Dato en contra | Veredicto |
|---|---|---|---|---|
| 6210597 | Nombre completo exacto (Víctor + Sáenz de Navarrete + Dopico), único en el índice | Territorio Álava (provincia de la frontera) | Fecha estimada 1895-1905 ≠ 1885; municipio asumido Vitoria ≠ Navaridas; sin fuente de 1927 | **COMPATIBLE CON RESERVAS** |
| 6210615 | Apellido compuesto (endémico: 144 casos en el mismo siglo y parroquia) | Parroquia (compartida con la ficha 1) | 1760 ≠ generación de ningún tatarabuelo; sin persona con la que cotejar; sin abuelos/padrinos | **INCOMPATIBLE** (con la frontera actual) |

---

## 7. Siguientes pasos, por orden de coste y de valor

| # | Acción | Dónde | Coste | Qué desbloquea |
|---|---|---|---|---|
| 1 | **Certificado literal de nacimiento de Angel Saenz de Navarrete Perez de Palomares** (n. 1927, Vitoria) | Registro Civil de Vitoria-Gasteiz — `RegistroCivilVitoria-Gasteiz@justizia.eus` (sede judicial electrónica, con DNI) | **0 €** | Cierra la identidad de la ficha 1: si el acta dice la edad o la naturaleza del padre, el vínculo queda probado |
| 2 | **Copia literal del bautismo de Víctor (1885)** | AHDV-GEAH — borrador en el apartado 8 | ~3-10 € | Da **abuelos paternos y maternos, padrinos y legitimidad** (4-6 datos nuevos de una vez) |
| 3 | Copias de **Eusebio (1858)** y **Leocadia (1863)** | AHDV-GEAH (misma parroquia, mismo fondo) | ~3-10 € cada una | Sube dos generaciones más: Pablo Sáenz de Navarrete + Josefa Tellaeche, y Román María Dopico + Fermina Guzmán |
| 4 | Búsqueda de la línea **Pérez de Palomares** | AHDV (Navaridas no la tiene) + Registro Civil de Vitoria | 0-10 € | Ábrela únicamente esta línea (P0013) |
| 5 | Índices y libros de Navaridas **1760-1850** (para empalmar la ficha 2) | AHDV, consulta presencial o por correo | tasa | Solo cuando 1-3 estén cerrados |

> Nota metodológica: **no** se toca `familia_conocida.json`, `arbol_hallazgos.json` ni ningún
> fichero de estado en este bloque. Las personas nuevas (Eusebio, Leocadia, Pablo, Josefa,
> Román María, Fermina) quedan **documentadas en este informe** a la espera de la copia literal
> que las certifique con ≥2 datos.

---

## 8. Borrador de solicitud de copia literal al AHDV (ficha 1) — listo para enviar

**Para**: `consultas@ahdv-geah.org` — **CC**: `archivo@ahdv-geah.org`
**Asunto**: Solicitud de copia literal de partida de bautismo — Parroquia de La Purísima
Concepción de Navaridas (Álava) — año 1885

```
Estimados señores:

Me dirijo al Archivo Histórico Diocesano de Vitoria para solicitar copia
literal (o reproducción digital certificada) de la siguiente partida de
bautismo, localizada en su buscador de registros sacramentales:

  - Bautizado:        Víctor Sáenz de Navarrete Dopico
  - Fecha del
    sacramento:       10 de marzo de 1885
  - Padre:            Eusebio Sáenz de Navarrete
  - Madre:            Leocadia Dopico
  - Parroquia:        La Purísima Concepción, Navaridas (Álava)
  - Diócesis:         Vitoria
  - Fondo:            F006.329
  - Signatura:        0193200301
  - Sig. digital:     0193200301
  - Folio:            f.195 r.
  - Código de
    referencia:       23041
  - Registro
    informático:      6210597
  - Referencia en
    su web:           n_ficha_bautismos.php?id_bautismo=577656

Motivo: investigación genealógica familiar. Víctor Sáenz de Navarrete Dopico
es mi bisabuelo (el dato se encuentra a más de 100 años de antigüedad, por
lo que entiendo que no le afecta la normativa de protección de datos).

Les agradecería que la copia incluya, si el asiento lo recoge, los nombres
de los abuelos paternos y maternos, los padrinos y cualquier nota marginal
(dispensa, legitimación, enmienda). Si el folio o la signatura no
correspondieran, les ruego me indiquen la referencia correcta.

Quedo a su disposición para abonar la tasa que corresponda: indíquenme, por
favor, el importe y la forma de pago (transferencia o PayPal) y la dirección
de envío. Si disponen de la copia ya digitalizada, agradecería el envío en
PDF por correo electrónico.

Muchas gracias por su trabajo.

Atentamente,

  Nombre y apellidos: [RELLENAR]
  DNI/NIE:            [RELLENAR]
  Dirección postal:   [RELLENAR]
  Teléfono:           [RELLENAR]
  Correo:             [RELLENAR]
  Parentesco:         bisnieto del bautizado
```

**Datos de contacto del archivo** (de `docs/fuentes_reales_2026-09.md`): Archivo Histórico
Diocesano de Vitoria (AHDV-GEAH), Seminario Diocesano, C/ Beato Tomás de Zumárraga 67,
01008 Vitoria-Gasteiz · tel. 945 213 871 / 945 213 872 / 945 213 873 · consulta online
gratuita en el propio portal; copia certificada ~3-10 €; respuesta estimada 5-10 días.

**Para la ficha 2 no se redacta solicitud**: su veredicto es INCOMPATIBLE con la frontera
actual, así que pedir su copia ahora sería gastar tasa sin poder encajarla en el árbol. Su
referencia queda guardada arriba para el día en que la línea llegue al siglo XVIII.

---

## 9. Anexo: cómo se obtuvo cada dato

| Dato | De dónde sale |
|---|---|
| Campos de las dos fichas | HTML crudo guardado en `tests/fixtures/artxibo_bautismo_62105*.html`, extraído con BeautifulSoup por filas `tabla-titulo`/`tabla-texto` |
| Parroquia, localidad, municipio, diócesis | Respuesta JSON del buscador (`busquedaBautismo`): campos `bauparroquia`, `baulocalidad`, `baumunicipio`, `baudiocesis` |
| Recuentos (79, 144, 7.009, 297, 204, 2, 1, 0) | Campo `recordsTotal` de la respuesta JSON del buscador, una consulta por valor |
| Unicidad de Víctor / Eusebio / Leocadia | Consultas dirigidas por nombre + apellido en la ventana 1481-1900 |
| Límite de cobertura en 1900 | Bautismos 1901-1910, 1911-1935 y 1901-1935 = 0 resultados |
| Fechas y folios | Campos `Fecha del sacramento` y `Folio/Página` de cada ficha |

*El portal se consultó con una sesión HTTP normal (sin credenciales, sin captchas, sin
`verify=False`). Todo el material de este bloque es público y gratuito.*
