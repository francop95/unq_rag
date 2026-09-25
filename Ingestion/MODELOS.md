# Elección de modelos: qué se midió y qué se decidió

Este documento registra las mediciones que respaldan qué modelo usa cada etapa
del pipeline. Existe porque en este proyecto la intuición falló tres veces
seguidas —un reranker mejor empeoró el recall, un modelo de visión más caro no
lo mejoró, y un embedding más nuevo lo empeoró— y sin las mediciones anotadas
esas decisiones se vuelven a discutir cada seis meses.

Cada número de acá salió de una corrida real sobre el corpus del secadero. Lo
que no está medido se dice que no está medido.

---

## 1. Cómo se mide

### La métrica

**`respuesta presente en el texto recuperado`**: de las preguntas del set de
evaluación, en cuántas la respuesta correcta aparece en el contexto que el
retrieval le entrega al modelo.

Es la única métrica comparable entre dos ingestas distintas. La otra que
reporta el harness, `recall@k`, compara contra el documento y la página donde
vive la respuesta (`gold_doc`, `gold_pages`), y eso deja de funcionar apenas el
corpus se reorganiza: los documentos se renombran, la paginación cambia, y el
número se desploma sin que el retrieval haya empeorado.

Eso no es una hipótesis. Medido sobre 19 preguntas, cambiando **solo** el
corpus y dejando el stack igual:

| | recall@10 | respuesta presente |
|---|---|---|
| corpus viejo | 100% (19/19) | 100% (13/13) |
| corpus nuevo | **5.3%** (1/19) | **92.3%** (12/13) |

El recall cayó 95 puntos porque los archivos se llaman distinto. La métrica que
no mira nombres perdió una pregunta de trece.

### El set

59 preguntas escritas contra el corpus, 54 en tema y 5 fuera de tema para
verificar que el sistema sepa decir que no sabe. De las 54, el harness
considera **43 con clave verificable automáticamente**; las otras 11 se
excluyen del porcentaje.

**Sesgo conocido:** el set se generó a partir del corpus viejo, así que está
sesgado a su favor. Parte de cualquier caída al pasar al corpus nuevo puede ser
eso y no una degradación real. No se puede separar con el set actual.

### Sobre el recall, una vez reapuntado el set

`eval/remap_eval_set.py` reapunta las preguntas al corpus nuevo buscando su
`source_excerpt` en los chunks recién generados. Resultado sobre las 54
preguntas en tema: **35 reapuntadas** y 19 sin coincidencia, porque su excerpt
era una descripción generada por el modelo de visión (se reescribe en cada
corrida) o porque el documento fue reemplazado por su traducción.

Con el set reapuntado, contra el índice de la fila 4:

| | Antes de reapuntar | Después |
|---|---|---|
| recall@10 | 1,9% | **53,7%** (29/54) |

El 53,7% está limitado por construcción: las 19 preguntas sin reapuntar no
pueden acertar nunca, así que el techo es 35/54 = 64,8%. **Sobre las que sí se
reapuntaron el recall@10 es 29/35 = 82,9%**, contra el 88,9% histórico.

Esto refuerza por qué el análisis usa `respuesta presente`: esa métrica dio
93,0% sin depender de ningún reapuntado.

### Cómo reproducir

```bash
cd Ingestion/eval
CHROMA_INDEX=<colección> CHROMA_PATH=<carpeta> \
EMBEDDING_PROVIDER=<openai|bedrock> EMBEDDING_MODEL_NAME=<modelo> \
python run_eval.py
```

El harness embebe la consulta con el proveedor configurado, el mismo que usa la
API. Antes llamaba a OpenAI directo, lo que con el índice en Bedrock hacía que
midiera un sistema que no era el de producción.

---

## 2. Embeddings de texto

### Lo que se midió antes de cambiar

Tres pares reales del dominio (consulta, pasaje correcto, pasaje incorrecto),
por el margen de separación coseno:

| Modelo | Dims | Margen promedio |
|---|---|---|
| `text-embedding-3-large` (OpenAI) | 3072 | **+0.463** |
| `cohere.embed-v4:0` (Bedrock) | 1536 | +0.447 |
| `cohere.embed-multilingual-v3` (Bedrock) | 1024 | +0.267 |

Se leyó como un empate entre los dos primeros. **Fue un error de lectura.**

### Lo que se midió después de cambiar

Mismos chunks, mismo corpus, solo cambia el embedding:

| Embeddings | Respuesta presente |
|---|---|
| `text-embedding-3-large` | **90.7%** (39/43) |
| `cohere.embed-v4:0` | **83.7%** (36/43) |

**7 puntos de diferencia: 3 de las 4 respuestas perdidas.** Aquella diferencia
de 0.016 en el margen no era ruido.

### Por qué falla, en concreto

Consulta: *"¿En qué bit puedo ver si los parámetros están bloqueados en el
PowerFlex 4M?"*. La respuesta está en una tabla `Dirección 8448 | Bits |
Descripción`.

- El contenido **está** en el PDF nuevo y **está** en el índice (5 chunks lo
  mencionan, igual que en el índice viejo). No es un problema de ingesta.
- Con `embed-v4` ese chunk queda en **posición 22 de 40**, fuera del top-10.
- Lo desplaza contenido sobre "Desbloqueado/Bloqueado", que habla del
  *parámetro que bloquea* y no del *bit de estado*. Es una desambiguación fina
  que `text-embedding-3-large` resuelve y `embed-v4` no.

### Decisión

**`text-embedding-3-large`.** Se revierte el cambio a Bedrock para el índice
textual.

---

## 3. Embeddings de imagen

Caso opuesto al anterior: acá el modelo nuevo gana con claridad.

Sobre los cuatro planos embebidos en el Excel de inventario, por el margen
entre el acierto y el primer error:

| Consulta | CLIP `ViT-B-32` (512d) | `cohere.embed-v4` (1536d) |
|---|---|---|
| "plano de conexionado del TBEN" | +0.020 | **+0.126** |
| "diagrama de distribución eléctrica..." | +0.025 | +0.026 |
| "plano con los códigos QD01 QF01 rotulados" | **erró** | **+0.140** |

Márgenes de ~0.02 son ruido. CLIP falla la consulta que separa una copia
rotulada de su original; `embed-v4` pone el plano rotulado primero (0.378) y el
**mismo plano sin rotular último** (0.172), o sea que lee los códigos dibujados
encima. Esas dos hojas del Excel existen exactamente para esa distinción.

Verificado también sobre 77 imágenes reales de 5 documentos: para *"plano con
los códigos QD01 QF01 rotulados"* los cuatro primeros resultados son planos con
códigos, y los originales sin rotular no entran al top 5.

Ventaja adicional: `embed-v4` embebe imagen y texto en el **mismo espacio**, así
que una consulta escrita alcanza una figura directamente. El espacio de 512
dimensiones de CLIP no es comparable con el textual y obliga a una colección
aparte. Además, la API deja de necesitar `torch` para este camino.

### Decisión

**`cohere.embed-v4` para imágenes**, construido con
`scripts/rebuild_visual_index.py` en una colección separada.

**Pero sigue apagado.** `USE_VISUAL_RETRIEVAL = False`. Tener un mejor modelo de
imágenes no justifica prender el retrieval visual: falta medir si aporta recall
o solo mete ruido, que es la pregunta que nunca se respondió con CLIP y la razón
por la que está desactivado.

---

## 4. Modelo de chunking multimodal

### Consumo, medido

| | Entrada | Salida | Salida por página |
|---|---|---|---|
| `gpt-5`, PowerFlex (126 pág.) | 453.795 | **1.165.933** | 9.253 |
| `gpt-5`, TBEN (6 pág.) | 27.874 | 87.666 | 14.611 |
| `gpt-4o`, misma figura de prueba | 1.135 | **175** | — |

Para describir la misma figura, `gpt-5` devolvió 2.890 tokens de salida contra
175 de `gpt-4o`: **16 veces más**. Razona antes de responder.

Costo medido de `gpt-4o` sobre el corpus nuevo: **USD 0,0154 por página**
(USD 1,3534 por un documento de 88 páginas). Los mismos 126 páginas del
PowerFlex costarían USD 1,94.

`gpt-5` no tiene precio en la tabla del medidor, así que su costo se reporta en
tokens. **Si costara por token lo mismo que `gpt-4o`** —los modelos de
razonamiento suelen costar más— ese documento saldría USD 12,79: **7x**.

### Incompatibilidad a tener en cuenta

`gpt-5` rechaza el parámetro `temperature` con un 400. El pipeline lo pasaba en
los tres puntos donde llama al modelo, así que apuntar la ingesta a `gpt-5`
hacía fallar **todas** las llamadas. Resuelto en `task_utils/model_params.py`,
que además aprende del 400 para modelos futuros.

### Decisión

**`gpt-4o`.** Medido con el corpus como constante, `gpt-5` pierde 2,3 puntos y
cuesta unas 7 veces más. Ver la sección 5.

El mismo documento (PowerFlex, 126 páginas) con cada modelo:

| | Salida del modelo | Costo |
|---|---|---|
| `gpt-4o` | 191.030 + 181.723 tokens (páginas + figuras, corpus entero) | **USD 3,21** |
| `gpt-5` | **1.165.933** tokens solo este documento | sin tarifa; USD 12,79 al precio de gpt-4o |

`gpt-5` produjo además **más** chunks (12.965 vectores contra 10.376) y aun así
recuperó peor. Más troceado no es mejor troceado.

---

## 5. Comparación de extremo a extremo

| # | Chunking | Embeddings | Corpus | Vectores | Respuesta presente | Costo de ingesta |
|---|---|---|---|---|---|---|
| 1 | `gpt-4o` | `text-embedding-3-large` | viejo | 5.493 | **93,0%** (40/43) | — |
| 2 | `gpt-5` | `cohere.embed-v4` | nuevo | 12.612 | **83,7%** (36/43) | ~7x |
| 3 | `gpt-5` | `text-embedding-3-large` | nuevo | 12.965 | **90,7%** (39/43) | ~7x |
| 4 | **`gpt-4o`** | **`text-embedding-3-large`** | **nuevo** | 10.376 | **93,0%** (40/43) | **USD 7,36** |

Las cuatro configuraciones tienen los 13 documentos y el mismo set de 54
preguntas. Cada par aísla una variable:

- **2 → 3** aísla el embedding, con chunks y corpus constantes:
  **+7 puntos** al volver a `text-embedding-3-large`.
- **3 → 4** aísla el modelo de chunking, con embedding y corpus constantes:
  **+2,3 puntos** al volver a `gpt-4o`, que además cuesta 7 veces menos.
- **1 → 4** aísla el corpus, con el stack constante: **sin cambio** (93,0% en
  los dos). El corpus nuevo no cuesta calidad, y el sesgo del set hacia el
  corpus viejo resultó menor de lo temido.

### Conclusión

**Ningún modelo nuevo mejoró nada, y dos empeoraron.** La configuración que
gana es la que ya estaba en producción, aplicada al corpus nuevo (fila 4).

El ejercicio no fue inútil: costó unos USD 20 en total y dejó tres hechos que
antes eran opiniones —que `embed-v4` pierde 7 puntos en texto pero gana en
imagen, que `gpt-5` no compra nada a 7x, y que el corpus nuevo es neutro—
además de las herramientas para volver a medirlo (`--reusar-chunks`,
`--solo`, el medidor de consumo) y de los tres defectos que destapó: el corte
por falta de crédito que nunca cortaba, el benchmark que embebía con otro
modelo que producción, y una validación de dimensiones que verificaba una
constante en vez del comportamiento real.

---

## 6. Qué falta medir

**El sesgo del set.** Las preguntas se generaron contra el corpus viejo.
Regenerar un set contra el corpus actual con `eval/generate_eval_set.py` daría
una vara sin ese sesgo, a costa de perder comparabilidad con lo histórico.

---

## 7. Resumen de decisiones

| Etapa | Modelo | Estado |
|---|---|---|
| Chunking multimodal | `gpt-4o` | `gpt-5` medido: −2,3 puntos y 7x el costo |
| Pasada dedicada por figura | el mismo que el chunking | — |
| Enriquecimiento | `gpt-4o-mini` | sin cambios; es la etapa de más volumen |
| Embeddings de texto | `text-embedding-3-large` | `embed-v4` medido: −7 puntos |
| Embeddings de imagen | `cohere.embed-v4` | adoptado, **pero el retrieval visual sigue apagado** |
| Base vectorial | ChromaDB embebido | sin cambios; migrar a Qdrant es infraestructura, no calidad |

Todos se cambian por entorno: `multimodal_model`, `enrichment_model`,
`embedding_provider`, `embedding_model`, `embedding_region` en el `.env` de la
ingesta, y `EMBEDDING_PROVIDER` / `EMBEDDING_MODEL_NAME` en la API.

**El modelo de embeddings tiene que ser el mismo de los dos lados.** Si no
coinciden, el retrieval no se degrada: devuelve resultados arbitrarios sin
error, sin log y sin nada raro en la respuesta. La API compara al arrancar la
dimensión que produce contra la del índice y avisa.
