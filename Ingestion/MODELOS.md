# Elección de modelos: qué se midió y qué se decidió

Este documento registra las mediciones que respaldan qué modelo usa cada etapa
del pipeline. Existe porque en este proyecto la intuición falló tres veces
seguidas —un reranker mejor empeoró el recall, un modelo de visión más caro no
lo mejoró, y un embedding más nuevo lo empeoró— y sin las mediciones anotadas
esas decisiones se vuelven a discutir cada seis meses.

Cada número de acá salió de una corrida real sobre el corpus del secadero. Lo
que no está medido se dice que no está medido.

---

## Contenido

| | |
|---|---|
| [1. Cómo se mide](#1-cómo-se-mide) | la métrica, el set y su sesgo conocido |
| [2. Embeddings de texto](#2-embeddings-de-texto) | OpenAI vs Cohere: −7 puntos |
| [3. Embeddings de imagen](#3-embeddings-de-imagen) | CLIP vs Cohere: gana el nuevo |
| [4. Modelo de chunking](#4-modelo-de-chunking-multimodal) | gpt-4o vs gpt-5: −2,3 puntos y 7× el costo; qué se deduplica |
| [5. Comparación de extremo a extremo](#5-comparación-de-extremo-a-extremo) | las cuatro configuraciones en una tabla |
| [6. ¿El multimodal se justifica?](#6-el-pipeline-multimodal-se-justifica) | contra la línea base: 340× el costo |
| [7. Técnicas de retrieval](#7-técnicas-de-retrieval-8-evaluadas-3-desactivadas) | gates, cupo, reranking, BM25, visual |
| [7bis. Qué cuesta una pregunta](#7bis-qué-cuesta-responder-una-pregunta) | USD 0,0221 de promedio, medido |
| [8. Qué falta medir](#8-qué-falta-medir) | lo que no se sabe |
| [9. Resumen de decisiones](#9-resumen-de-decisiones) | la tabla final |

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

### Con qué modelo se midió, y por qué importa decirlo

Todo lo de este documento se midió con **`gpt-4.1`** redactando la respuesta,
corriendo la API de forma nativa.

Eso no era lo que respondía en el servidor. `docker compose` inyectaba
`OPENAI_MODEL` **definida y vacía**, que no es lo mismo que ausente:
`os.getenv` no aplica su default ante una cadena vacía, y el valor vacío bajaba
por la cadena de resolución del modelo hasta su último eslabón, `gpt-4o-mini`,
sin una línea de log. Todo lo que corría en contenedor —el despliegue incluido—
respondía y clasificaba intención con ese modelo.

El síntoma visible era el clasificador de intención marcando preguntas técnicas
legítimas como `invalid` y devolviendo *"formulá una pregunta técnica concreta"*.
Las cifras de acá nunca estuvieron mal; lo que estaba mal era suponer que el
sistema desplegado era el sistema medido.

Queda anotado porque es el modo de fallo más caro de este proyecto y ya apareció
tres veces: el eval embebiendo con otro proveedor que el índice (§1), la
colección visual vacía creada en silencio (§7) y esto. Las tres veces el sistema
no falló: siguió respondiendo, con otra cosa.

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

### Qué se deduplica y qué no

La ingesta descarta chunks casi idénticos por similitud de texto, para sacar el
boilerplate que repiten los manuales de fabricante. El umbral tenía un supuesto
equivocado: que dos textos muy parecidos dicen lo mismo.

Lo destapó revisar si un documento nuevo entraba limpio. Las páginas 3 y 4 del
export de configuración del TBEN traen su programa ARGEE, con reglas de la forma
`Semaforo = 0 → salida 7 := 0` y `Semaforo > 0 → salida 7 := 1`. Son **96%
idénticas como texto** y dicen cosas opuestas. La deduplicación se comía la
segunda: el índice habría sabido cuándo la salida se apaga y no cuándo se
enciende. Media regla de control, perdida en silencio.

Ahora la similitud solo descarta si además coinciden **los números y los
operadores de comparación**. En documentación técnica esa diferencia *es* el
contenido: pasa igual con tablas de parámetros cuyas filas difieren solo en un
valor, y con nombres de señal que difieren solo en el índice.

Medido en las dos direcciones, que es lo que importa en un cambio así:

| | Antes | Después |
|---|---|---|
| Chunks del export TBEN | 8 | **9** |
| Duplicados reales en PowerFlex | 25 | 25 |
| Duplicados reales en su edición vieja | 21 | 21 |
| Duplicados reales en la referencia ARGEE | 14 | 14 |

Lo único que se sigue perdiendo del TBEN es `Alias Variables (hidden)`, 24
caracteres de nada. El boilerplate que el filtro existe para cazar sigue yéndose.

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

## 6. ¿El pipeline multimodal se justifica?

La otra pregunta que este proyecto tiene que poder responder: si describir cada
figura con un modelo de visión paga, comparado con extraer solo texto y pasarle
OCR a las imágenes (`main_text_baseline.py`).

Ambos índices sobre **el mismo corpus de 13 documentos**:

| | Multimodal | Línea base (texto + OCR) |
|---|---|---|
| Costo de ingesta | **USD 7,36** | **USD 0,0217** |
| Vectores | 10.376 | 771 |
| Preguntas de texto (54): respuesta presente | **93,0%** | 88,4% |
| Preguntas de texto: recall@10 | 53,7% | 51,9% |
| **Preguntas visuales (24): recall@10** | **54,2%** (13/24) | 41,7% (10/24) |
| Preguntas visuales: respuesta presente | **20,8%** | 8,3% |
| Chunks con imagen adjunta (mediana de 10) | **8** | **0** |

El multimodal cuesta **340 veces más** y en preguntas de texto gana 4,6 puntos.
Si el sistema fuera solo para buscar parámetros en manuales, no se justificaría.

Donde sí se justifica es en lo visual. De las 24 preguntas visuales, 13 se
pudieron reapuntar al corpus nuevo, y **el multimodal acierta las 13** —el
techo alcanzable— contra 10 de la línea base. Y la fila que más importa para el
uso real: el multimodal entrega 8 de cada 10 chunks **con su imagen adjunta**,
la línea base ninguno. Un técnico que pregunta por un plano recibe el plano.

Esa es la decisión: el costo se paga por las figuras y los planos, no por el
texto. En un corpus sin contenido gráfico la línea base sería la opción
correcta.

## 7. Técnicas de retrieval: 8 evaluadas, 3 desactivadas

Las mismas mediciones, aplicadas a las técnicas del pipeline de consulta. Están
acá porque la pregunta que responden es idéntica —¿esto aporta o no?— y tenerlas
en otro archivo obliga a buscar en dos lugares para defender una decisión.

| Técnica | Estado | Medición |
|---|---|---|
| Contextual Retrieval | **activa** | prepende 1-2 frases de contexto antes de embeber |
| Preguntas sintéticas (multi-vector) | **activa** | excluirlas cuesta 93,0% → 83,7% de respuesta presente |
| Expansión de contexto (prev/next) | **activa** | recupera el chunk vecino cuando el recuperado queda corto |
| Gate de relevancia (0.50) + gate cruzado | **activos** | el cruzado evita que documentos ajenos llenen el top-k |
| Cupo para otros documentos (2 de 10) | **activo** | ver abajo: la métrica no se mueve, pero arregla un fallo real |
| **Cross-encoder reranking** | **desactivado** | ver abajo |
| **BM25 híbrido** | **desactivado** | no aporta en este corpus |
| **Retrieval visual** | **desactivado** | medido contra consultas visuales: aporte exclusivo 0 |
| Query expansion | no implementada | el índice multi-vector ya cubre la paráfrasis |

### Por qué hay un cupo reservado para otros documentos

Los dos gates **quitan** candidatos malos. Este **reserva asientos**, y hace
falta porque resuelve el problema opuesto: un documento grande puede llenar el
top-k con chunks perfectamente legítimos —que ningún gate tiene motivo para
descartar— y dejar afuera al documento que sí tiene la respuesta.

El tamaño no es un detalle del corpus, es el factor que decide:

| Documento | Vectores |
|---|---|
| `ARGEE_Reference_Manual.pdf` (manual del fabricante) | 3973 |
| `manual powerflex 4m.pdf` | 3726 |
| `Secadero v01 On TBEN-L4-8IOL.pdf` (el programa instalado) | 83 |

**El caso que lo motivó.** A la pregunta *"¿Cómo se calcula la salida hacia el
variador en el programa ARGEE?"* el sistema contestaba con un ejemplo genérico
del manual del fabricante, `32767 - Input_value`, en vez de la fórmula del
programa que corre en la planta, `4000 + (Variador * 160)`. En la telemetría el
embudo no filtraba nada —37 candidatos entraban y 37 salían de los tres gates—
y las 12 primeras posiciones eran todas del manual, entre 0.72 y 0.64. El chunk
correcto estaba recuperado, en la posición 15, con 0.64. Lo mataba el corte por
`top_k`, no un gate. No era ruido ganándole a la señal: eran chunks buenos de un
documento 48 veces más grande.

La monopolización es la norma, no la excepción. Sobre las consultas registradas
en telemetría, 7 de 18 tienen un solo documento ocupando más del 70% de los 40
candidatos del over-fetch, una lo ocupa entero, y la mediana es 48%.

**Lo medido.** Sobre `eval_set` (54 preguntas en tema), con cupo 0, 2 y 3:

| Cupo | Respuesta presente | recall@10 | MRR | Gate fuera de tema |
|---|---|---|---|---|
| 0 (apagado) | 93,0% | 29/54 | 0,425 | 5/5 |
| 2 | 93,0% | 29/54 | 0,425 | 5/5 |
| 3 | 93,0% | 29/54 | 0,425 | 5/5 |

La métrica no se mueve. Cambia el contexto entregado en 3 de 59 preguntas del
set, ninguna para peor. No se mueve más porque el gate cruzado ya descartó a los
otros documentos antes de que el cupo pueda reservarles lugar: solo alcanza a
los que están a menos de 0.10 del top-1 —el caso ARGEE—, y eso es deliberado,
porque no tiene que resucitar lo que el gate mató a propósito.

**Entonces por qué se activa si no mueve la métrica.** Porque arregla un fallo
real que el set no contiene, sin costar nada en lo que el set sí mide. Medido de
punta a punta sobre la pregunta del variador: con cupo 0 la respuesta es el
ejemplo del manual; con cupo 2 es la fórmula del programa instalado, con la
aritmética correcta. Es el mismo criterio con el que están apagados el
reranking y BM25 —no aportan—, aplicado al revés: esto no resta y suma en un
caso que importa.

Vale leerlo también como un límite del set: 48 de sus 59 preguntas traen menos
de 2 documentos distintos en el top-10, así que es un set que no castiga la
monopolización. Un set regenerado contra el corpus actual (ver §8) probablemente
sí la castigaría.

### Por qué el retrieval visual está apagado

Durante mucho tiempo acá decía "sin medir". La objeción era legítima: el eval son
consultas de texto, y un índice visual existe para buscar por semejanza de imagen
—*"el diagrama que tiene un contactor y tres fusibles"*—, así que medirlo con
preguntas de texto no probaba nada.

Se midió con consultas visuales. El set visual se reapuntó al corpus actual en
`eval/eval_visual_v4_4o.jsonl`: **18 de 24** preguntas. Las 5 de
`variadorPowerFlex4M.pdf` se remapearon a `manual powerflex 4m.pdf` porque los dos
archivos son byte-idénticos, un renombre y nada más. Las 6 restantes se
descartaron: apuntan al catálogo TBEN viejo y a la tesis vieja, que **no** son los
documentos actuales renombrados sino archivos distintos, y remapear sus páginas
habría sido inventar datos.

Lo primero que aparece es que casi no hay margen. Con el visual **apagado**, sobre
consultas puramente visuales, el índice de texto ya da recall@10 17/18 y recall@1
14/18. La razón es el pipeline multimodal: cada chunk de imagen lleva la
descripción que le generó el modelo de visión, así que el denso encuentra el
diagrama por su contenido sin necesitar mirarlo.

| Configuración | Aporte exclusivo | recall@10 | recall@1 | MRR | media |
|---|---|---|---|---|---|
| apagado (base) | — | 17/18 | 14/18 | 0,810 | 8 |
| CLIP, `fusion_admits_sparse=false` | **0** | 17/18 | 14/18 | 0,810 | 8 |
| CLIP, `fusion_admits_sparse=true` | 14 | 17/18 | 14/18 | 0,810 | 8 |
| CLIP + `fusion_decides_order=true` | 14 | 17/18 | 14/18 | 0,811 | 8 |

*Aporte exclusivo* son los ítems del contexto final que trajo CLIP y el denso no.
Con `admits_sparse` apagado es **cero**: los 13 que CLIP encuentra ya los había
traído el denso, es puramente redundante. Con él encendido entran 14 ítems
exclusivos en 9 de las 18 preguntas y **no mueven ninguna métrica**: ocupan
asientos del `top_k` sin ganárselos.

**La colección embed-v4 no es consultable hoy.** `visual_docs_v4_4o_mm` tiene 1536
dimensiones, pero el proveedor de texto de la API es OpenAI, `soporta_imagenes()`
da `False` y la consulta visual cae a CLIP, que produce 512. Chroma rechaza por
dimensión —el mismo error que en §2, esta vez atrapado en un `try` y degradado a
warning— así que la búsqueda se saltea. Habilitarla exige pasar el texto a Cohere,
que cuesta 7 puntos (§2). Esa puerta está cerrada mientras el texto siga en OpenAI.

Dos trampas que la medición destapó y quedaron corregidas: el default de
`VISUAL_INDEX_NAME` era `visual_docs`, un nombre viejo, y el conector usaba
`get_or_create_collection`, que ante un nombre inexistente devuelve una colección
**vacía** en vez de fallar. Encender el visual consultaba la nada en silencio.

### El grafo de componentes: medido con y sin

El grafo estaba activo sin haber demostrado que mejora nada, que es el mismo
pecado que se le critica al reranking. Se midió.

**Primero hubo que revisar lo que afirma.** Las 17 relaciones extraídas por un
modelo llevaban todas `confianza: alta`, asignada por el mismo modelo que las
extrajo, y ninguna había sido verificada. Leídas una por una contra su cita:

| Veredicto | Cuántas | Qué significa |
|---|---|---|
| `directa` | 13 | el plano dibuja esa flecha entre esos dos componentes |
| `inferida` | 4 | una frase genérica convertida en emparejamientos 1 a 1 que no dice |
| `evidencia_no_corresponde` | 2 | la cita describe otra relación; se dejan fuera del prompt |

Ninguna cita era inventada. Y apareció una arista faltante: una cita nombra el
rango `SSR01-SSR03` y solo se había expandido `SSR01`.

**Después se midió el aporte.** Sobre `eval_set`, el grafo dispara en **4 de 43**
preguntas, y cuando dispara mete 23 a 27 componentes en el prompt, porque el
contexto recuperado trae un chunk de plano que lista todos los códigos.

| | Respuesta correcta en la salida |
|---|---|
| Con grafo | 36/43 (83,7%) |
| Sin grafo | 38/43 (88,4%) |

Esa diferencia de dos preguntas es ruido: **tres de las cuatro que cambian son
preguntas donde el grafo ni siquiera dispara**, y la cuarta cambia por un falso
positivo del verificador, no por una mejora real.

**El set no contiene las preguntas para las que el grafo existe**, así que se
escribieron: `eval/eval_grafo.jsonl`, 12 preguntas de recorrido —*"si se dispara
QF02, ¿qué deja de funcionar?"*, *"¿cuál es el camino desde el diferencial hasta
una resistencia?"*—. Resultado: **12/12 con grafo y 12/12 sin él**, y las
respuestas son equivalentes leídas una al lado de la otra.

**Por qué.** El grafo se construyó desde los mismos chunks que el retrieval
recupera: el chunk del plano contiene literalmente `Conexiones: QD01 → QF01,
QF01 → QF02…`. Es una **re-codificación de contenido recuperable**, no
conocimiento nuevo. Ganaría su lugar si tuviera aristas que ningún chunk
contiene —cruces entre dos documentos, un historial de mantenimiento, datos de
un CMMS—, y hoy las 30 salen de un chunk indexado.

Queda activo porque no resta y porque hace algo que el retrieval no puede:
afirmar que una lista de conexiones es **completa**, lo que habilita responder
"no hay otra conexión documentada". Ese uso no está medido.

### Por qué el reranking está apagado

El pipeline arma un pool y después corta en 10 chunks. Con reranking, ese corte
lo decide el cross-encoder: no solo reordena, **elige cuáles 10 sobreviven**. Ahí
es donde pierde.

| Orden | recall@5 | recall@10 | MRR | expulsa / rescata |
|---|---|---|---|---|
| **Sin reranker (RRF)** | **81,1%** | **94,3%** | 0,530 | — |
| `bge-reranker-base` | 73,6% | 84,9% | 0,511 | 7 / 2 |
| `mmarco-mMiniLMv2-L12` | 71,7% | 84,9% | 0,576 | 7 / 2 |
| `ms-marco-MiniLM-L-6-v2` | 64,2% | 83,0% | 0,471 | 9 / 3 |

Un caso concreto: para *"el variador no arranca desde el teclado integrado"*, la
tabla con las acciones correctivas quedaba **última al 2,5%** con reranker; sin
él aparece **primera al 91,2%**.

Se probó después `bge-reranker-v2-m3`, el cross-encoder multilingüe abierto más
fuerte, para descartar que el problema fuera un modelo débil. Salió **peor**:
recall@1 64,8% → 20,4%, MRR 0,735 → 0,377.

**El diagnóstico, que es lo que importa:** dos rerankers de calidad muy distinta
empeoran, y el mejor empeora más, así que la causa es el índice y no el modelo.
Sobre una consulta, 8 de 10 candidatos llegan por un vector de pregunta
sintética, pero el cross-encoder puntúa el contenido PADRE. Hacer que el
reranking funcione acá es un cambio de pipeline, no un cambio de modelo.

### Cómo se sigue una consulta

La telemetría registra el embudo completo de cada consulta (ver
`API/README.md`): cuántos candidatos trae la búsqueda densa y cuántos quedan
después de cada etapa, más los candidatos crudos con su score y si sobrevivieron.
Es lo que permite distinguir "el chunk correcto no se recuperó" de "se recuperó y
lo filtró un gate", que son problemas distintos con arreglos distintos.

---

## 7bis. Qué cuesta responder una pregunta

La ingesta tenía medidor desde hace tiempo; la API no tenía ninguno. Cada
respuesta de OpenAI trae su `usage` y los tres puntos de llamada lo tiraban, así
que la pregunta "¿cuánto sale una consulta?" no tenía respuesta. Ahora
`API/telemetria/consumo.py` acumula los tokens por `query_id` y los deja en el
`extra_json` de la ejecución.

Ocho preguntas representativas, con `gpt-4.1`:

| Tipo | Entrada | Salida | USD |
|---|---|---|---|
| Eléctrica, con planos adjuntos | 12.586 | 512 | 0,0293 |
| Falla | 10.864 | 1.014 | 0,0298 |
| Parámetro de manual | 11.027 | 456 | 0,0257 |
| Programa de control | 9.796 | 329 | 0,0222 |
| Proceso | 8.222 | 627 | 0,0215 |
| Fuera de tema, rechazada | 2.134 | 45 | **0,0046** |
| **Promedio** | **9.241** | **449** | **0,0221** |

Tres cosas que el promedio solo no dice:

- **La entrada domina 20 a 1.** Se paga el contexto que entra, no la respuesta
  que sale. Recuperar de más es lo caro; redactar de más, casi gratis.
- **Los planos adjuntos cuestan 3.220 tokens y USD 0,006 por consulta**, un 26%
  de una pregunta eléctrica. Medido encendiendo y apagando el adjunto sobre la
  misma pregunta, no estimado.
- **Rechazar sale un quinto que responder.** El gate de fuera de tema hace una
  sola llamada y corta antes del retrieval: además de evitar respuestas
  inventadas, ahorra.

### Contra el costo de construir el índice

La ingesta completa son USD 7,36 por única vez. A USD 0,0221 la pregunta, la
operación supera a la construcción del índice a las **333 preguntas**. Y hasta
unas 750 preguntas mensuales el servidor (~USD 17/mes) cuesta más que el modelo.

### Por página, los tres niveles

El mismo documento (PowerFlex, 126 páginas) procesado de tres maneras:

| Troceado | Por página | Las 126 páginas | Contra la base |
|---|---|---|---|
| Texto + OCR (línea base) | USD 0,00006 | USD 0,008 | 1x |
| **`gpt-4o` con visión** | **USD 0,0154** | **USD 1,94** | **257x** |
| `gpt-5` con visión | USD 0,1015 | USD 12,79 | 1.690x |

El salto grande no es de `gpt-4o` a `gpt-5`: es de no mirar la página a mirarla.

---

## 8. Qué falta medir

**Las 6 preguntas visuales descartadas.** Apuntan al catálogo TBEN y a la tesis
viejos, documentos que ya no están y cuyos reemplazos son archivos distintos.
Regenerarlas con `eval/generate_visual_eval.py` devolvería el set a 24.

**El sesgo del set.** Las preguntas se generaron contra el corpus viejo.
Regenerar un set contra el corpus actual con `eval/generate_eval_set.py` daría
una vara sin ese sesgo, a costa de perder comparabilidad con lo histórico.

---

## 9. Resumen de decisiones

| Etapa | Modelo | Estado |
|---|---|---|
| Chunking multimodal | `gpt-4o` | `gpt-5` medido: −2,3 puntos y 7x el costo |
| Pasada dedicada por figura | el mismo que el chunking | — |
| Enriquecimiento | `gpt-4o-mini` | sin cambios; es la etapa de más volumen |
| Embeddings de texto | `text-embedding-3-large` | `embed-v4` medido: −7 puntos |
| Embeddings de imagen | `cohere.embed-v4` | adoptado, **pero el retrieval visual sigue apagado** |
| Base vectorial | ChromaDB embebido | sin cambios; migrar a Qdrant es infraestructura, no calidad |
| **Redacción de la respuesta** | **`gpt-4.1`** | con el que se midió todo acá; ver §1 sobre el entorno |
| Clasificación de intención | `gpt-4.1` | el mismo; con un modelo más débil rechaza preguntas válidas |

Todos se cambian por entorno: `multimodal_model`, `enrichment_model`,
`embedding_provider`, `embedding_model`, `embedding_region` en el `.env` de la
ingesta, y `EMBEDDING_PROVIDER` / `EMBEDDING_MODEL_NAME` en la API.

**El modelo de embeddings tiene que ser el mismo de los dos lados.** Si no
coinciden, el retrieval no se degrada: devuelve resultados arbitrarios sin
error, sin log y sin nada raro en la respuesta. La API compara al arrancar la
dimensión que produce contra la del índice y avisa.
