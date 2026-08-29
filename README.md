# unq_rag — Asistente de mantenimiento sobre documentación técnica

Sistema RAG multimodal que responde preguntas de mantenimiento industrial a partir de
manuales, planos eléctricos y tablas de especificaciones en PDF. Un operario pregunta
*"¿en qué bit veo si los parámetros están bloqueados en el PowerFlex 4M?"* y recibe la
respuesta con la tabla y la página del manual que la respaldan.

La diferencia con un RAG de documentos de texto es el material: la información que hace
falta está en tablas de parámetros, en diagramas de conexionado y en planos escaneados
sin capa de texto. Un pipeline que solo extrae párrafos no la encuentra.

![Ciclo RAG](RAGsystem.gif)

---

## Los tres servicios

El repositorio contiene tres servicios que se despliegan por separado, cada uno con su
propio entorno virtual y su propio `.env`:

| Servicio | Qué hace | Stack | Puerto |
|---|---|---|---|
| **[Ingestion](Ingestion/)** | Convierte los PDF en un índice vectorial: extrae texto, tablas y figuras, los enriquece y los indexa | Python 3.12, PyMuPDF, ChromaDB, CLIP | — (proceso batch) |
| **[API](API/)** | Recibe la pregunta, recupera el contexto y genera la respuesta | Python 3.12, Flask, ChromaDB | 5000 |
| **[Frontend](Frontend/)** | Interfaz de chat con panel de fuentes y galería de figuras | React 19, TypeScript, Vite, Tailwind | 5173 |

El acoplamiento entre ellos es un directorio, no una API interna: **Ingestion** escribe el
índice y los recortes de media en `Ingestion/data/`, y **API** lee de ahí.

```
  PDFs                    Ingestion/data/                      navegador
   │                    ┌──────────────────┐                       ▲
   ▼                    │  chroma_index/   │                       │
┌───────────┐  escribe  │  media/          │  lee   ┌─────┐  HTTP  │
│ Ingestion │ ────────► │  chunks_data/    │ ◄───── │ API │ ◄──────┴── Frontend
└───────────┘           └──────────────────┘        └─────┘
   batch                                            :5000            :5173
```

---

## Cómo funciona

### El flujo estándar, y dónde se queda corto

![RAG simple](ragflow.webp)

El esquema de manual: partir los documentos en fragmentos, embeberlos, buscar los más
parecidos a la pregunta y pasárselos al modelo. Funciona con prosa. Con documentación
técnica se rompe en tres puntos, y cada uno tiene una respuesta concreta en este
repositorio.

### 1. El fragmento no puede cortarse por longitud

![Chunking por caracteres](chunkingexample.webp)

*Un splitter por caracteres sobre un texto cualquiera: los cortes caen a mitad de palabra
y a mitad de frase, sin mirar el contenido.*

Sobre un manual esto parte la fila de una tabla de su encabezado, y el fragmento
resultante dice `8448 | 11 | 1 = Parámetros bloqueados` sin decir de qué parámetro habla.

**Lo que hace Ingestion:** el troceado lo decide un modelo multimodal (`gpt-4o`) que ve
la página rasterizada, no el flujo de texto. Devuelve cada bloque con su tipo —texto,
tabla o figura— y cada tipo se procesa aparte. Las tablas conservan su markdown y su
estructura JSON; las figuras se recortan y se describen en una pasada dedicada de visión.
Los planos escaneados, que no tienen capa de texto, pasan por OCR con preprocesado
(deskew, binarización, 300 DPI) y por un filtro de legibilidad que descarta el ruido.

### 2. Las tablas necesitan un tratamiento propio

![Estrategias con tablas](chunkingwtables.png)

*Tres formas de tratar un documento con tablas: meterlo entero en un modelo de contexto
largo, extraer las tablas de forma dirigida, o trocear el documento respetándolas.*

Este proyecto usa la tercera. Una tabla grande se divide preservando el encabezado en
cada parte, y los chunks de tabla nunca se fusionan con los de texto vecinos: el
rechunking semántico solo agrupa contenido de tipo texto. Perder esa distinción fue un
bug real, y hay un test de regresión por cada uno de esos casos.

### 3. La pregunta y el manual no usan las mismas palabras

Un operario describe un síntoma —*"el variador no arranca desde el teclado"*— y el manual
describe una especificación. Los dos vectores no se parecen, por bien troceado que esté
el documento. Ingestion cierra esa distancia en el momento de indexar:

- **Contextual retrieval:** antepone a cada fragmento un contexto generado por LLM, para
  que el chunk no dependa de explicarse solo.
- **Preguntas sintéticas:** indexa vectores adicionales con las preguntas que ese
  fragmento responde, en el lenguaje del usuario.

Ambas encarecen la ingesta y se pueden apagar desde el `.env`.

### 4. El mejor fragmento rara vez basta

![Expansión de contexto](chunkexample.webp)

*Si el fragmento **n** es el más parecido a la pregunta, sus vecinos **n-1** y **n+1**
suelen contener el resto de la respuesta.*

Es la técnica que la API aplica en cada consulta (`USE_CONTEXT_EXPANSION`): un
procedimiento de puesta en marcha o una tabla partida en dos casi nunca caben en un solo
fragmento.

### El camino de una consulta

```
Frontend  ──POST /get_response──►  API
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
              QueryIntent     ResponseCache    Retrieval denso
         reescribe el         coincidencia     Chroma + expansión
         follow-up como       exacta de la     de contexto
         pregunta autónoma    pregunta              │
                                                    ▼
                                          gate de similitud 0.50
                                                    │
                                                    ▼
                                          gpt-4.1 multimodal
                                          (contexto + planos)
                                                    │
   respuesta + fuentes + figuras  ◄──────────────────┘
```

Dos decisiones que no son las de manual y están documentadas en el código:

- **La caché es por texto exacto, no semántica.** Se midió la similitud entre pares de
  preguntas de este dominio y las bandas se solapan: *"corriente máxima de entrada"* vs
  *"de salida"* da 0.92, y dos redacciones de la misma pregunta dan 0.93. No hay umbral
  que las separe, y una falsa coincidencia devuelve el número de parámetro equivocado.
- **Tres técnicas de retrieval están apagadas tras medirlas.** Reranking con cross-encoder
  y BM25 no mejoran el recall en este corpus, y la búsqueda visual por CLIP tampoco; están
  implementadas y se activan con una flag, pero el default es `False`. Query expansion no
  llegó a implementarse porque el índice multi-vector ya cubre la paráfrasis.

---

## Puesta en marcha

**Requisitos:** Python 3.12, Node 20+, una API key de OpenAI y `tesseract` instalado si
vas a procesar planos escaneados (`brew install tesseract` en macOS).

El orden importa: **Ingestion** tiene que correr al menos una vez antes de levantar la
API, porque la API no arranca conversación sin un índice del que leer.

### Paso 1 — Crear los tres `.env`

Cada servicio se configura por separado. Ninguno de los tres `.env` está en el
repositorio (los ignora git, para que ninguna clave termine publicada), así que hay que
crearlos a partir de las plantillas:

```bash
cp Ingestion/.env.example Ingestion/.env
cp API/.env.example       API/.env
cp Frontend/.env.example  Frontend/.env
```

Cada plantilla lista **todas** las variables que lee el código, con su valor por defecto
y una explicación. De todas ellas, solo estas tres hay que rellenarlas a mano:

| Fichero | Variable | Qué poner |
|---|---|---|
| `Ingestion/.env` | `OPENAI_API_KEY` | Tu clave de OpenAI (`sk-...`). Sin ella el pipeline no puede leer los PDF ni generar embeddings |
| `API/.env` | `OPENAI_API_KEY` | La misma clave. Sin ella la API no puede generar respuestas |
| `Frontend/.env` | `VITE_API_BASE_URL` | La URL de la API. En local ya viene puesta como `http://localhost:5000`; solo se cambia si la API corre en otra máquina |

El resto de variables tienen valores por defecto que funcionan. Se tocan para ajustar el
pipeline (tamaño de chunk, modelos, concurrencia) o para exponer la API fuera de local,
y están documentadas una por una en las plantillas.

> **Nunca pongas una clave en `Frontend/.env`.** Todo lo que empieza por `VITE_` se
> inserta en el bundle que descarga el navegador, así que sería pública.

### Paso 2 — Ingestion: construir el índice

```bash
cd Ingestion
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp tus_manuales/*.pdf data/raw_data/     # los PDF a indexar
python src/main_multimodal.py
```

Deja el índice en `data/chroma_index/` y los recortes de figuras y tablas en `data/media/`.
Es un proceso batch y cuesta dinero: cada página pasa por un modelo de visión y el
enriquecimiento añade llamadas por chunk. Corre una vez, y se repite solo cuando cambian
los documentos.

Para probar consultas contra el índice sin levantar nada más:

```bash
python scripts/hybrid_multimodal_search.py "diagrama de conexiones del motor"
```

### Paso 3 — API: servir las respuestas

En otra terminal:

```bash
cd API
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python app.py                            # http://localhost:5000
```

Lee el índice que dejó Ingestion. Sin `API_TOKEN` definido queda abierta, que es lo
razonable en local. Para exponerla en una red hay que poner el token y acotar los
orígenes, o cualquiera con acceso a la red gasta tu cuota de OpenAI:

```bash
CORS_ALLOWED_ORIGIN="http://ip-del-servidor:5173" API_TOKEN="un-secreto" python app.py
```

Comprobar que responde:

```bash
curl -X POST http://localhost:5000/get_response \
  -H "Content-Type: application/json" \
  -d '{"query": "¿cómo reseteo el variador a valores de fábrica?"}'
```

### Paso 4 — Frontend: la interfaz

En una tercera terminal:

```bash
cd Frontend
npm install
npm run dev                              # http://localhost:5173
```

Abre <http://localhost:5173> y pregunta. Si el chat responde pero no aparecen las
figuras, casi siempre es que `VITE_API_BASE_URL` apunta a un sitio distinto del que
sirve `/media`.

---

## Tests

88 tests de invariantes, sin índice ni API key ni red. Cada uno corresponde a un bug que
llegó a producirse: si alguno se rompe, ese bug volvió.

```bash
./run_tests.sh
```

Para medir la calidad del retrieval hay además un set de evaluación de 59 preguntas con
respuesta y fuente conocidas, más 24 consultas visuales, en [Ingestion/eval/](Ingestion/eval/):

```bash
cd Ingestion && python eval/run_eval.py
```

---

## Estructura

```
├── Ingestion/          pipeline de ingesta
│   ├── src/tasks/          chunking → embeddings → indexado
│   ├── src/task_utils/     tablas, diagramas, OCR, enriquecimiento, validadores
│   ├── eval/               set de evaluación y scripts de medición
│   └── docs/              cómo funciona la ingesta, el camino de una pregunta
├── API/                servicio de consulta
│   ├── services/           orquestación del workflow RAG
│   ├── contexts/           acceso a Chroma y retrieval avanzado
│   └── qnas/               retriever y caché de respuestas
├── Frontend/           cliente web
└── run_tests.sh        ambas suites de tests
```

Cada servicio tiene su propio README con el detalle: [Ingestion](Ingestion/README.md)
documenta el pipeline y las mediciones detrás de cada decisión, y [API](API/README.md)
la configuración y el flujo de una consulta.
