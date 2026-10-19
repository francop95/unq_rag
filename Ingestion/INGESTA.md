# Cómo funciona la ingesta

Qué hace el pipeline que convierte el corpus en el índice que consulta la API:
qué lee, qué le hace a cada tipo de documento, qué pasa cuando encuentra una
imagen, y qué quedó indexado hoy.

Lo de acá está escrito leyendo el código, no la documentación previa. Donde el
pipeline **no** hace algo que podría parecer que hace, se dice.

---

## Contenido

| | |
|---|---|
| [1. La idea en una línea](#1-la-idea-en-una-línea) | dos mitades con economías opuestas |
| [2. Qué entra](#2-qué-entra) | 14 documentos, 3 formatos |
| [3. Cuándo se reprocesa](#3-cuándo-se-reprocesa-un-documento) | hash SHA-256, no fecha |
| [4. Las ocho etapas](#4-las-ocho-etapas-en-orden) | de archivo a vector |
| [5. PDF](#5-pdf-el-camino-largo) | el camino largo |
| [6. Qué pasa con una imagen](#6-qué-pasa-cuando-encuentra-una-imagen) | **la pregunta concreta** |
| [7. Excel](#7-excel-dos-lectores-en-el-mismo-archivo) | tablas y planos incrustados |
| [8. Python](#8-python-la-segmentación-la-puso-el-autor) | celdas del autor |
| [9. Los siete tipos de chunk](#9-los-siete-tipos-de-chunk) | qué produce cada camino |
| [10. El índice resultante](#10-el-índice-resultante) | **el listado final** |
| [11. Lo que el pipeline no hace solo](#11-lo-que-el-pipeline-no-hace-solo) | los huecos honestos |
| [12. Cómo se corre](#12-cómo-se-corre) | comandos |

---

## 1. La idea en una línea

La ingesta es cara y se corre una vez; la consulta es barata y se corre siempre.
Procesar el corpus entero cuesta **USD 7,36**, porque un modelo de visión mira
cada página. Responder una pregunta cuesta **USD 0,0221**, y solo lee el índice
que la ingesta dejó.

Eso explica el diseño: la ingesta hace trabajo caro **una vez** —describir cada
figura, generar preguntas sintéticas, resumir el contexto de cada fragmento—
para que la consulta no tenga que hacer nada de eso.

---

## 2. Qué entra

El corpus vive en `data/raw_data/`, **incluidas las subcarpetas**. Hoy son 14
documentos en tres formatos:

| Formato | Archivos | Lector |
|---|---|---|
| `.pdf` | 10 (367 páginas) | `ChunkingTask` (multimodal, con visión) |
| `.xlsx` | 1 | `XlsxChunkingTask` |
| `.py` | 3 | `PythonChunkingTask` |

El registro de lectores es explícito, en `src/main_multimodal.py`:

```python
CHUNKERS = {
    ".pdf":  (ChunkingTask,       "pdf_path"),
    ".xlsx": (XlsxChunkingTask,   "xlsx_path"),
    ".py":   (PythonChunkingTask, "py_path"),
}
```

**Una extensión que no esté en ese diccionario se ignora sin aviso.** Si mañana
se agrega un `.docx` al corpus, la ingesta va a terminar "bien" y ese documento
no va a estar. No hay error ni advertencia.

El recorrido es recursivo por una razón concreta: antes era un listado plano y
solo de PDF, así que al organizar el corpus en `hardware/` quedaron **tres
manuales sin indexar en silencio**, uno de 127 páginas. El nombre del documento
es el del archivo sin extensión, así que dos archivos con el mismo nombre en
carpetas distintas colisionarían.

---

## 3. Cuándo se reprocesa un documento

Por **hash SHA-256 del archivo**, no por fecha de modificación. `data/ingestion_manifest.json`
guarda, por documento, su hash y el resultado de la última corrida.

| Situación | Qué pasa |
|---|---|
| El hash coincide y la última corrida fue exitosa | se omite |
| El hash cambió | se reprocesa entero |
| La última corrida falló | se reintenta |
| Documento nuevo | se procesa |

Tocar un archivo sin cambiar su contenido no cuesta nada, y cambiar un byte lo
reprocesa completo. No hay reprocesado parcial por páginas.

### Y cuándo queda superado por otro

El hash responde *¿este archivo cambió?*. No responde *¿otro archivo lo
reemplaza?*, que es una pregunta distinta y la que falló de verdad: al renombrar
el corpus, la copia vieja del plano siguió en su lugar y el sistema adjuntaba una
revisión anterior sin que nada avisara.

El manifiesto guarda ahora, además del hash, el **documento lógico** y su
**revisión**, leídos del nombre:

| Nombre del archivo | Documento lógico | Revisión |
|---|---|---|
| `1_Plano distribucion electrica_v1` | `plano distribucion electrica` | 1 |
| `Plano distribucion electrica` | `plano distribucion electrica` | — |
| `Secadero v01 On TBEN-L4-8IOL` | `secadero v01 on tben-l4-8iol` | — |

Ingestar una revisión más nueva marca las anteriores como `vigente: false` y lo
dice mientras corre. La lectura está anclada al final del nombre a propósito: el
`v01` de la tercera fila va en el medio, no es una revisión.

Se lee del nombre y no del contenido porque es una heurística declarada,
revisable de un vistazo, en vez de una inferencia sobre el PDF.

---

## 4. Las ocho etapas, en orden

Por cada documento:

| # | Etapa | Qué hace |
|---|---|---|
| 1 | **Troceado** | el lector del formato convierte el archivo en chunks con metadatos |
| 2 | **Validación técnica** | reglas de dominio; marca advertencias, no descarta |
| 3 | **Calidad y deduplicación** | descarta chunks vacíos o duplicados |
| 4 | **Contextual Retrieval + preguntas sintéticas** | agrega `context_summary` y hasta 8 preguntas por chunk |
| 5 | **Embeddings (pasada 1)** | vectores que necesita el paso siguiente |
| 6 | **Re-chunking semántico** | une chunks contiguos muy parecidos en *super-chunks* |
| 7 | **Embeddings (pasada 2)** | el set final, ya con super-chunks |
| 8 | **Indexado dual** | escribe en el índice textual y en el visual |

Hay **dos pasadas de embeddings** porque el re-chunking semántico necesita
vectores para decidir qué unir: compara chunks por similitud real (umbral 0,85,
máximo 3.000 caracteres por super-chunk) y no por heurística de páginas. Los
super-chunks nacen después, así que necesitan su propio vector.

Los chunks originales **no se descartan** al crear un super-chunk: conviven. Por
eso `superchunk` es un tipo más en el índice y no un reemplazo.

### El enriquecimiento, que es la etapa que infla el índice

Cada chunk recibe:

- **`context_summary`**: una o dos frases que sitúan el fragmento en su
  documento, generadas por `gpt-4o-mini`. Se anteponen al texto antes de
  embeberlo, para que un fragmento que dice "el valor máximo es 99,9" sepa de
  qué parámetro habla.
- **Hasta 8 preguntas sintéticas** (`max_synthetic_questions = 4`), cada una
  indexada **como vector propio** con `parent_chunk_id` apuntando al original.

Esa segunda parte es la que define la forma del índice: **el 76,8% de los
vectores son preguntas sintéticas**. Cuando una consulta encuentra una de ellas,
el retrieval recupera el chunk padre.

Fueron 8 por chunk hasta que se midió el volumen. Con 4 el índice da el mismo
93,0% de respuesta presente con 44% menos vectores, así que la mitad eran
redundantes.

---

## 5. PDF: el camino largo

Por cada página:

1. **Se analiza la página**: ¿tiene texto nativo? ¿tiene imágenes? ¿tiene
   vectores de dibujo?
2. **Si tiene imágenes pero no texto** → es un plano escaneado → se le pasa
   **OCR previo** con Tesseract y el resultado se antepone como
   `[OCR del plano]` al texto que recibirá el modelo.
3. **Se renderiza la página a PNG** y se manda al modelo de visión (`gpt-4o`)
   junto con el texto nativo. El modelo devuelve los chunks de esa página: qué
   es texto, qué es tabla, qué es figura, y el **bounding box** de cada figura.
4. **Si la página tiene imágenes o vectores**, el prompt se enriquece con
   instrucciones específicas para diagramas técnicos.
5. **Se extraen las imágenes incrustadas** del PDF por separado, deduplicadas
   por hash: un logo que se repite en 126 páginas se extrae una vez. Sin esa
   deduplicación, cada repetición entraba como figura nueva.
6. **Se recortan los bounding boxes** a archivos PNG en `crops/`.
7. **Cada figura pasa por una segunda llamada al modelo**, dedicada solo a ella.
   Esto es lo caro: una página con cuatro figuras son cinco llamadas, no una.

El orden documental se restaura explícitamente antes de seguir. El listado del
sistema de archivos devuelve un orden arbitrario, y el re-chunking semántico
agrupa por proximidad: sin ese ordenamiento se generaban super-chunks que unían
la página 106 con la 1.

---

## 6. Qué pasa cuando encuentra una imagen

Esta es la pregunta concreta, así que va el camino completo.

### Paso 1 — ¿Es una imagen?

Un chunk es imagen si el modelo de visión lo marcó como tal (`content_type:
"image"`), o si vino de la extracción de imágenes incrustadas del PDF.

### Paso 2 — ¿Es un diagrama eléctrico?

`ElectricalDiagramProcessor.is_diagram()` lo decide mirando el tipo y las notas
que dejó el modelo. Una foto cualquiera no es un diagrama; un esquema de
conexiones sí.

### Paso 3 — Si es diagrama, se abre en dos facetas

Un diagrama entra al índice como **dos chunks distintos**, no uno:

| Faceta | `content_type` | Qué contiene |
|---|---|---|
| `<chunk_id>_visual` | `diagram_visual` | la descripción que escribió el modelo de visión, más la ruta a la imagen |
| `<chunk_id>_ocr` | `diagram_text` | el texto que el OCR leyó **dentro** del dibujo: rótulos, códigos, referencias |

La faceta de OCR existe porque los códigos rotulados en un plano (`QD01`,
`VFD01`) son texto dibujado, no texto del PDF: sin OCR no serían buscables.

### Paso 4 — El OCR solo entra si es legible

Y acá hay una regla que vale explicar, porque nació de un fallo real.

`_ocr_is_legible()` exige que el OCR contenga **una racha de palabras largas
consecutivas**, no una proporción global de caracteres válidos. Si no la hay, la
faceta de texto se descarta.

El motivo: el OCR de planos de línea escaneados falla casi siempre y devuelve
tokens sueltos. El problema no era que el chunk fuera inútil, sino que **el
enriquecedor le generaba preguntas sintéticas a partir del rótulo genérico**
(*"Diagrama eléctrico — Página N"*), y salían plantillas idénticas entre páginas
distintas que coincidían con cualquier consulta eléctrica. El efecto visible era
que **la portada del manual aparecía como respuesta a "mostrame el diagrama de
bornes de control"**.

Se mide por rachas y no por proporción para no descartar un epígrafe legible
rodeado de ruido, que es justamente el caso que sirve.

### Paso 5 — La imagen se copia y se indexa dos veces

El recorte se copia a `data/media/` (hoy **1.802 archivos, 140 MB**) y queda
referenciado en el metadato `media_path`. Es lo que permite que la respuesta
muestre la figura al lado del texto.

El chunk visual se indexa en **las dos colecciones**:

- en el **índice textual**, por su descripción, para que una consulta escrita lo
  encuentre;
- en el **índice visual**, por el contenido de la imagen.

### Paso 6 — Y el índice visual está apagado en la consulta

La ingesta lo construye igual (313 vectores), pero la API no lo consulta:
`USE_VISUAL_RETRIEVAL = False`. Está medido en `MODELOS.md` §7 — su aporte
exclusivo sobre consultas visuales es **cero**, porque los chunks de imagen ya
llevan la descripción del modelo de visión y el índice de texto los encuentra
por su contenido.

Construirlo es barato y deja la puerta abierta a un corpus con más gráficos.

---

## 7. Excel: dos lectores en el mismo archivo

El Excel de inventario tiene hojas de dos naturalezas distintas, y cada una se
lee diferente.

### Hojas tabulares

Se detecta la fila de encabezado (necesita al menos 2 celdas con contenido), y a
partir de ahí las filas se convierten a Markdown y se agrupan **por presupuesto
de caracteres** (`PRESUPUESTO_CHUNK_TABLA = 1800`), repitiendo el encabezado en
cada chunk.

El encabezado se repite a propósito: una tabla cortada al medio sin su
encabezado deja filas que no responden nada, porque las columnas pierden su
nombre.

### Hojas de planos

Un Excel puede traer **imágenes incrustadas**, que como texto son invisibles. Se
extraen con `ws._images`, se guardan como PNG y se emiten con
`content_type: "image"` — con lo cual entran al mismo camino de la sección 6:
pasada de visión, detección de diagrama, facetas, OCR.

Los cuatro planos del inventario viven ahí dentro. Sin este lector, el archivo
habría entrado como una tabla más y los planos no existirían para el sistema.

---

## 8. Python: la segmentación la puso el autor

Los tres programas de control se parten en dos niveles:

1. **Por celdas `#%%` de Spyder**, que es como el autor ya los segmentó. Cada
   marca trae un comentario que dice qué hace el bloque (*"#%% Cálculo de humedad
   absoluta"*). Esa división es información, no formato: la escribió quien
   entiende el programa.
2. **Si una celda es muy grande**, por límites de función o clase obtenidos del
   AST.

**Nunca se parte una función por la mitad.** Partir un cuerpo produce dos chunks
que no responden nada: ni el que tiene la firma sin la lógica, ni el que tiene la
lógica sin saber de qué función es.

Se emite el mismo esquema de chunk que el pipeline multimodal, así que el resto
de las etapas no sabe ni necesita saber que el origen era código.

---

## 9. Los siete tipos de chunk

| `content_type` | Origen | Qué es |
|---|---|---|
| `text` | todos | prosa |
| `table` | PDF, Excel | tabla en Markdown, con su encabezado |
| `image` | PDF, Excel | imagen que no se clasificó como diagrama |
| `diagram_visual` | PDF, Excel | la descripción de un diagrama, con su recorte |
| `diagram_text` | PDF, Excel | el texto leído por OCR dentro del dibujo |
| `superchunk` | re-chunking | varios chunks contiguos y parecidos, unidos |
| `synthetic_question` | enriquecimiento | una pregunta que apunta a un chunk padre |

---

## 10. El índice resultante

### Composición

Colección `multimodal_documents_v4_4o`, **5.864 vectores**:

| Tipo | Vectores | % |
|---|---|---|
| `synthetic_question` | 4.506 | **76,8%** |
| `text` | 701 | 12,0% |
| `diagram_visual` | 306 | 5,2% |
| `table` | 244 | 4,2% |
| `superchunk` | 55 | 0,9% |
| `diagram_text` | 45 | 0,8% |
| `image` | 7 | 0,1% |

Colecciones visuales, 313 vectores cada una: `visual_docs_v4_4o` (CLIP, 512
dimensiones) y `visual_docs_v4_4o_mm` (`cohere.embed-v4`, 1536). Las dos
contienen lo mismo: 306 `diagram_visual` y 7 `image`.

Ese 76,8% explica por qué el reranking empeora el resultado: el cross-encoder
puntúa el contenido padre, no la pregunta que lo encontró.

Lo que **no** explica es que un documento grande acapare el top-k. Eso viene de
su tamaño: bajar las preguntas de 8 a 4 deja la dominancia mediana en 92%, igual
que antes, porque todos los documentos pierden la mitad y las proporciones no
cambian. Los dos resultados están medidos en `MODELOS.md` §7.

### Listado final de documentos indexados

**14 documentos**, ordenados por peso en el índice:

| Vectores | Documento |
|---:|---|
| 2.064 | `manual powerflex 4m.pdf` |
| 2.010 | `ARGEE_Reference_Manual.pdf` |
| 936 | `0_Construcción y automatización de un secadero…pdf` |
| 201 | `Inventario_secadero_revision_Pablo_v2.xlsx` |
| 168 | `tben-L4-8ioL.pdf` |
| 126 | `Propuesta de Trabajo Final…pdf` |
| 117 | `control_secadero_251124.py` |
| 69 | `Poster_didactico_secadero_componentes_y_planos.pdf` |
| 66 | `osciloscopio_secadero_251023_1.py` |
| 48 | `Secadero v01 On TBEN-L4-8IOL.pdf` |
| 37 | `2_Planos_secadero_codificados_propuesta.pdf` |
| 10 | `servosyrele300924.py` |
| 6 | `1_Plano distribucion electrica_v1.pdf` |
| 6 | `conexionadoTben.pdf` |

**La asimetría es enorme y tiene consecuencias.** Los dos manuales de fabricante
son el 70% del índice entre los dos; el programa de control que efectivamente
corre en la planta son 83 vectores. Por eso la API reserva 2 de los 10 asientos
de contexto para documentos distintos al dominante: sin eso, una pregunta sobre
el programa instalado se responde con el ejemplo genérico del manual.

---

## 11. Lo que el pipeline no hace solo

Tres cosas que conviene saber antes de correr una ingesta nueva.

**El refinado de recortes es un paso manual aparte.** Las reglas que evitan que
el recorte de una figura se trague a la figura vecina viven en
`scripts/recortar_sin_cortar_texto.py`, que **el pipeline no invoca**. Una
ingesta limpia produce los bounding boxes que dio el modelo de visión; hay que
correr ese script después para re-recortar desde los bbox guardados, sin volver
a llamar al modelo.

**El etiquetado de entidades es otro paso aparte.** El índice recién construido
no sabe que `PowerFlex 4M`, `INV1` y `VFD01` son el mismo equipo: eso lo escribe
`scripts/etiquetar_entidades.py`, que el pipeline **no invoca**. Hay que correrlo
después de cada ingesta o las etiquetas quedan en el índice anterior.

**Un formato desconocido se ignora sin error.** Ver la sección 2.

**Una copia vieja fuera del corpus no la ve el manifiesto.** El manifiesto solo
conoce los archivos que ingesta. Si queda una copia anterior en otro lado y la
configuración de la API le apunta, no se entera nadie. Para eso está
`scripts/verificar_vigencia.py`, que compara las tres listas que nada más
compara: lo que el índice contiene, lo que el manifiesto declara vigente y lo que
la configuración de la API nombra.

**El índice visual se construye siempre, aunque esté apagado en la consulta.**
Es barato y deliberado, pero significa que el conteo de vectores del archivo
(6.177) no es el de la colección que sirve la API (5.864).

---

## 12. Cómo se corre

```bash
cd Ingestion
python src/main_multimodal.py                      # todo lo que cambió
python src/main_multimodal.py --solo "Secadero v01" # un documento
python src/main_multimodal.py --dry-run             # qué haría, sin hacerlo
python src/main_multimodal.py --reusar-chunks       # re-embebe sin volver a trocear
```

`--reusar-chunks` es el que permite comparar modelos de embedding sin pagar otra
vez el troceado, que es la parte cara.

Dos scripts que no corren solos y conviene conocer:

```
python scripts/verificar_vigencia.py     # índice, manifiesto y config coinciden
python scripts/generar_inventario.py     # el Excel a registros con clave por código
python scripts/recortar_sin_cortar_texto.py   # re-recorta figuras sin llamar al modelo
```

Modelos, todos cambiables por entorno:

| Etapa | Variable | Valor hoy |
|---|---|---|
| Troceado y figuras | `multimodal_model` | `gpt-4o` |
| Enriquecimiento | `enrichment_model` | `gpt-4o-mini` |
| Embeddings | `embedding_provider` / `embedding_model` | `openai` / `text-embedding-3-large` |

Cada corrida deja su consumo medido en dólares y un reporte de calidad. El
manifiesto se actualiza solo al terminar bien, así que una corrida interrumpida
no marca documentos como procesados.

---

Las decisiones de modelo y las técnicas medidas están en
[MODELOS.md](MODELOS.md). La línea base de solo texto, en
[BASELINE.md](BASELINE.md).
