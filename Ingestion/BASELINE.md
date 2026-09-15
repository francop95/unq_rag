# Línea base: solo modelo de texto + OCR

Segunda ingesta de los mismos documentos sin modelo de visión, para comparar la
calidad de las respuestas contra el pipeline multimodal.

```
PDF → capa de texto (PyMuPDF) + OCR (Tesseract) → split por longitud → embeddings → Chroma
```

Corre **en paralelo**: índice propio, carpetas propias, manifiesto propio y su
propia instancia de la API. No modifica nada del pipeline actual.

---

## Qué se está comparando exactamente

**El OCR no es la diferencia** — el pipeline multimodal también lo usa. La
diferencia es el **modelo de visión**:

| | Línea base | Multimodal |
|---|---|---|
| Capa de texto del PDF | ✅ | ✅ |
| OCR de páginas escaneadas | ✅ | ✅ |
| OCR de imágenes embebidas | ✅ | ✅ |
| **Modelo de visión sobre la página** | ❌ | ✅ `gpt-4o` |
| Tablas como estructura (fila + encabezado) | ❌ | ✅ |
| Descripción de figuras y diagramas | ❌ | ✅ |
| Jerarquía (TOC, capítulos) | ❌ | ✅ |
| Índice visual CLIP | ❌ | ✅ |

Dicho de otra forma: de un diagrama eléctrico, la línea base transcribe **los
rótulos que Tesseract pueda leer**. El pipeline multimodal además entiende
**qué representa y cómo se conectan sus partes**.

Lo que se mantiene idéntico es el modelo de embeddings
(`text-embedding-3-large`) y el motor de retrieval. Así la diferencia que se
mide es de la ingesta.

### El modelo que redacta la respuesta

| | Modelo | Visión |
|---|---|---|
| Multimodal | `gpt-4.1` | sí |
| Línea base | `gpt-4-0613` | **no** |

La línea base responde con el GPT-4 original, que no tiene visión, para que sea
"sin nada multimodal" de punta a punta. En la práctica el cambio es simbólico
—esa API ya no le manda ninguna imagen al modelo— pero elimina la objeción.

Se eligió `gpt-4-0613` y no `gpt-3.5-turbo` para no meter un segundo factor:
con gpt-3.5 la comparación mediría además "modelo más flojo". Su límite es el
contexto (8k contra 128k), por eso `max_tokens` baja a 2000; medido, el prompt
ronda los 3k tokens y entra.

Se cambia con una variable, sin reconstruir nada:

```bash
BASELINE_OPENAI_MODEL=gpt-4.1 docker compose up -d --force-recreate api-baseline
```

**Cuidado al leer "fuentes" en la comparación.** Ese número son los contextos
que el MODELO dice haber citado (`cited_sources` en RetrieverQna), no los que
recuperó el índice. Medido con la misma pregunta y retrieval idéntico —10 chunks
sobre el umbral, verificado determinista—:

| Modelo de la base | Fuentes | Respuesta |
|---|---|---|
| `gpt-4.1` | 1 | intenta responder desde un contexto flojo (52%) |
| `gpt-4-0613` | 0 | dice que no puede determinarlo |

O sea que un 0 no significa "no recuperó nada": significa que el modelo declinó
responder y no citó ninguno. Es la conducta más honesta de las dos —gpt-4.1
llegó a alucinar un número de conector— pero conviene saberlo antes de sacar
conclusiones de ese contador.

---

## Lo que ya sabemos antes de indexar

Los dos planos eléctricos no tienen capa de texto. Con OCR **sí entran** al
índice, pero esto es literalmente lo que se recupera de ellos:

**`Plano distribucion electrica.pdf`** (confianza OCR 0.85)
```
3 4 | | 6 | 7 | Inter. Ter DP1213 Inter. Ter In: 190A (513515 inter. Ter
In: 254 DD In: 15A x Fuente de alimentacion Fuente de alimentacion |~~
220 VCA a 24 220 VCA a 5 Veci2A | GND OV DP1213 Servo 1 y nombre ...
```

**`conexionadoTben.pdf`** (confianza OCR 0.82)
```
2 3 4 | | 6 | 7 | | E/S M12 Y X3 QOD (V1) DRIVER CO C4 DRIVER RESISTENCIAS
O3 C5 X2 EXRTACTOR C2 E 01 | © © C6 Sh | DP1213 Parada de O1 emergencia
C3 C7/ E No utilizado 4416-2M X+ | X+ P 1 mm. y Xl e = FE ETHERNET ...
```

Dos cosas para mirar, porque son el corazón de la comparación:

1. **Los dígitos se rompen.** `In: 190A` es casi seguro `In: 10A`, y `In: 254`
   es `In: 25A`. La pregunta del eval set es justamente *"¿qué interruptor
   térmico está antes del inversor, el de 10A o el de 25A?"*.
2. **No hay topología.** El OCR devuelve una bolsa de rótulos sin ninguna
   relación entre ellos. "Antes del inversor" es una propiedad del dibujo, no
   del texto, y ninguna cantidad de OCR la recupera.

En `conexionadoTben.pdf` se ve lo mismo: la respuesta correcta es el conector
`X1`, y el OCR leyó `Xl` (ele minúscula) y `X+`. El token está, la respuesta
no.

---

## Uso

### 1. Construir el índice

```bash
cd Ingestion
python src/main_text_baseline.py          # --force para reingestar
```

Requiere `tesseract` instalado (`brew install tesseract`; ya está el idioma
`spa`). Barato comparado con la ingesta multimodal: no hay llamadas de visión,
solo OCR local y embeddings.

Deja:

```
data/chroma_index_baseline/       índice (colección: baseline_documents)
data/chunks_data_baseline/        chunks, crops OCR y chunking_stats.json
data/embeddings_data_baseline/    vectores
data/baseline_manifest.json       hash por documento
```

### 2. Comparar en el frontend

Tres terminales y **una sola** pestaña del navegador:

```bash
# 1 — API multimodal      :5000
cd API && python app.py

# 2 — API línea base      :5001
cd API && python app_baseline.py

# 3 — Frontend            :5173
cd Frontend && npm run dev
```

En <http://localhost:5173>, la pestaña **"Comparar"** manda la misma pregunta a
las dos APIs en paralelo y muestra lado a lado:

- la respuesta generada por cada una,
- tiempo, cantidad de fuentes, cantidad de media y similitud del top-1,
- qué documentos recuperó cada una (con un cartel cuando **no coinciden**, que
  suele ser el hallazgo),
- cada contexto recuperado, desplegable para leer el texto exacto que recibió
  el LLM.

La pestaña "Asistente" queda igual que siempre, hablando solo con la API
multimodal.

`app_baseline.py` levanta **la misma app Flask** que `app.py`; lo único que
cambia es a qué colección apunta. No duplica lógica de retrieval ni de
generación, así que cualquier diferencia en la respuesta viene del índice.

### Dos cosas que hubo que neutralizar para que la comparación sea válida

**Caché separada.** La línea base usa `cache-index-baseline`. Con una caché
compartida, la respuesta de una versión se le serviría a la otra.

**Planos sin adjuntar.** La API sube `Plano distribucion electrica.pdf` y
`conexionadoTben.pdf` como `input_file` al modelo cuando la consulta es
eléctrica — y también **siempre que ningún candidato supera el gate de
relevancia**, porque en ese camino la bandera queda sin setear y
`ModelCompletion` aplica su default `True`.

Ese es exactamente el caso de la línea base en las preguntas de planos. Medido
antes de corregirlo: contestaba sobre el conector de Ethernet **sin haber
recuperado un solo chunk**, porque `gpt-4.1` estaba leyendo el PDF del plano con
visión. La "versión sin modelo de visión" respondía con un modelo de visión.

`app_baseline.py` corta las dos vías que leen esa bandera. Sin esto, la
comparación mide cualquier cosa menos lo que dice medir.

### 3. Comparar por métricas (opcional)

```bash
cd Ingestion && python eval/compare_baseline.py
```

Corre el eval set contra los dos índices y los pone lado a lado. La métrica que
vale es **"respuesta presente"**: el `recall@k` se mide por página y los dos
pipelines pagina y trocean distinto, así que no es comparable entre ingestas
(lo documenta `eval/answer_check.py` con los números).

---

## Preguntas para la demo

Del eval set, ordenadas de más a menos convincente:

**1. Figuras en documentos que SÍ tienen capa de texto** — las 24 de
`eval/eval_visual.jsonl`, sobre `variadorPowerFlex4M.pdf`, la Tesis y el
catálogo TBEN. Las dos versiones leen la página; solo una entiende la figura.
Nadie puede atribuirlo al escaneo. Ejemplo: la curva de frecuencia portadora vs
corriente de salida (variador, p.72).

**2. Tablas de parámetros** — 14 preguntas. Las dos ven la página; la
multimodal conserva fila + encabezado, la base trocea por longitud.
Ejemplo: *"¿En qué bit veo si los parámetros están bloqueados?"* → `Bit 11 de
la dirección 8448`.

**3. Los planos escaneados** — 3 preguntas. Con OCR la base ya no está vacía,
pero devuelve rótulos sueltos con dígitos rotos.
Ejemplo: *"¿A cuál de los conectores va el cable de red Ethernet?"* → `X1`.

**4. Texto plano** — 12 preguntas. **El control.** Acá deberían empatar, y eso
es lo que hace creíble todo lo demás: si la multimodal ganara también en prosa,
habría que sospechar que cambió otra cosa (el embedder, el retrieval) y no la
ingesta.

---

## Configuración

Lee el **mismo `.env`** que la ingesta multimodal, así que no hay que duplicar
la API key. Todas estas claves son opcionales:

| Clave en `.env` | Default | Qué hace |
|---|---|---|
| `baseline_index_name` | `baseline_documents` | Colección Chroma |
| `baseline_index_path` | `./data/chroma_index_baseline/` | Carpeta del índice |
| `baseline_chunks_path` | `./data/chunks_data_baseline/` | Chunks y crops |
| `baseline_embeddings_path` | `./data/embeddings_data_baseline/` | Vectores |
| `baseline_chunk_size` | `1000` | Tamaño de chunk |
| `baseline_chunk_overlap` | `200` | Solapamiento |
| `baseline_use_ocr` | `true` | OCR de páginas e imágenes |
| `baseline_ocr_page_zoom` | `3.0` | Zoom al rasterizar para OCR |
| `baseline_use_enrichment` | `false` | Ver abajo |

La API de la línea base acepta `BASELINE_INDEX_NAME`, `BASELINE_INDEX_PATH`,
`BASELINE_PORT`, `BASELINE_CACHE_INDEX` y las mismas `CORS_ALLOWED_ORIGIN` y
`API_TOKEN` que `app.py`.

### `baseline_use_enrichment` decide qué mide el experimento

**`false` (default)** — línea base clásica. La diferencia incluye *todo* lo que
agrega el otro pipeline: visión, tablas como estructura, descripción de figuras,
contextual retrieval, preguntas sintéticas y super-chunks. Responde *"¿cuánto
mejoró respecto de un RAG de manual?"*.

**`true`** — mantiene el enriquecimiento por LLM (que es texto puro) y deja
fuera solo lo multimodal. Responde *"¿cuánto aporta específicamente el modelo de
visión?"*.

Si el objetivo es justificar la inversión en multimodal, la segunda es la
honesta: la primera le atribuye a "multimodal" mejoras que en realidad vienen
del enriquecimiento por LLM. Para correr las dos, cambiá la clave, poné otro
`baseline_index_name` y reingestá con `--force`.

---

## Archivos

Todos nuevos; ninguno de los existentes fue modificado.

| Archivo | Qué es |
|---|---|
| `src/tasks/chunking_task_text.py` | Capa de texto + OCR de páginas e imágenes |
| `src/main_text_baseline.py` | Orquesta la ingesta de la línea base |
| `../API/app_baseline.py` | Misma app Flask, apuntada al índice base |
| `eval/compare_baseline.py` | Eval contra los dos índices, lado a lado |
| `BASELINE.md` | Este documento |

Las etapas de **embeddings** (`ChunksEmbeddings`) e **indexado**
(`AutomaticIndexer`) se reutilizan sin tocarlas, porque el chunker de texto
emite el mismo esquema. El OCR reutiliza `ElectricalDiagramProcessor`, incluido
su preprocesado (deskew, binarización, 300 DPI) y su gate de legibilidad, que
descarta el ruido de tokens sueltos.
