# Correr el sistema con Docker

Cuatro servicios, un comando. Reemplaza las tres o cuatro terminales que hacían
falta para levantar las APIs y el frontend a mano.

```
┌──────────────────────────────────────────────────────────────┐
│  web  ·  nginx  ·  :8080                                     │
│  sirve el bundle y hace de proxy inverso                     │
│                                                              │
│    /              → index.html (SPA)                         │
│    /api/          → api:5000            (índice multimodal)  │
│    /api-baseline/ → api-baseline:5001   (solo texto + OCR)   │
└──────────────────────────────────────────────────────────────┘
          │                              │
   ┌──────▼───────┐              ┌───────▼────────┐
   │     api      │              │  api-baseline  │
   │  gunicorn    │              │   gunicorn     │
   └──────┬───────┘              └───────┬────────┘
          │      lee (solo lectura)      │
          └──────────────┬───────────────┘
                         │
              ./Ingestion/data   ← lo escribe la ingesta
              chroma_index/  chroma_index_baseline/  media/
```

---

## Puesta en marcha

```bash
cp .env.docker.example .env        # y poner OPENAI_API_KEY
docker compose build               # ~4 min la primera vez
docker compose up -d
```

La app queda en **<http://localhost:8080>**.

Si es la primera vez y todavía no hay índices, hay que construirlos (batch, no
son servicios):

```bash
docker compose --profile ingest run --rm ingestion            # multimodal
docker compose --profile ingest run --rm ingestion-baseline   # solo texto + OCR
docker compose restart api api-baseline
```

Los PDF se leen de `Ingestion/data/raw_data/`, igual que sin Docker.

### Comandos habituales

```bash
docker compose ps                        # estado de los servicios
docker compose logs -f api               # seguir el log de una API
docker compose down                      # bajar todo (los datos quedan)
docker compose build --no-cache api      # reconstruir desde cero
```

---

## Decisiones que conviene conocer

### El navegador ya no habla directo con las APIs

nginx hace de proxy inverso y el bundle usa rutas relativas (`/api`,
`/api-baseline`). Tres consecuencias:

- **El CORS desaparece.** Todo sale del mismo origen. `CORS_ALLOWED_ORIGIN` solo
  importa si expones los puertos de las APIs a la red.
- **La imagen del frontend no está atada a un host.** Vite inyecta las `VITE_*`
  en tiempo de build: si fueran URLs absolutas, mover el sistema a otra máquina
  obligaría a reconstruir la imagen.
- Los puertos 5000 y 5001 se publican igual, para poder pegarles con `curl` o
  correr el eval desde el host.

### La imagen de la API no lleva torch

`requirements.txt` es un `pip freeze` del entorno local y no sirve para una
imagen: trae PyQt5, los SDK de Azure/Google/Office365, Jupyter, moviepy,
`tabula-py` (que necesita una JVM), camelot con Ghostscript, y dos paquetes que
**no instalan en Linux** — `infi.systray` (Windows) y `appnope` (macOS).

Las imágenes usan `requirements-docker.txt`, que son los paquetes que el código
importa de verdad, verificado importando la app y mirando `sys.modules`.

De ahí queda afuera **torch + sentence-transformers (~2,5 GB)**: solo los usan
el reranking cross-encoder y el retrieval visual CLIP, y los dos están en
`False` en `Configuration.py` porque se midieron y no mejoran el recall en este
corpus. Sin el paquete el código degrada solo —los imports están en
`try/except` y las guardas chequean disponibilidad— y deja un warning en el log.

Si los vas a activar, hay que reconstruir con torch:

```bash
WITH_ML=1 docker compose build api
```

La imagen de la **ingesta sí lleva torch**: el indexado dual necesita CLIP para
los embeddings visuales. En las dos se instala la rueda de CPU desde el índice
de PyTorch; la de PyPI arrastra CUDA y suma varios GB inútiles.

### El layout de directorios no es arbitrario

`Configuration.py` resuelve el índice como `../../Ingestion/data/chroma_index`
relativo a su propio archivo, y `app.py` sirve `/media` desde
`../Ingestion/data`. Por eso el código va a `/app/API` y los datos a
`/app/Ingestion/data`: replicando el layout del repo, **no hubo que tocar una
línea de código** para que las rutas resuelvan.

### `Ingestion/.env` se monta dentro del contenedor

Las APIs leen su configuración del entorno, así que el compose se la pasa con
`environment:`. El pipeline de ingesta **no puede**: `ConfigReader` usa
`dotenv_values()`, que lee el archivo y nunca el entorno del proceso. Sin
montarlo, la ingesta no vería ninguna clave de tuning (`chunk_size`, modelos,
flags de enriquecimiento).

Por eso `Ingestion/.env` se monta en solo lectura en los dos servicios de
ingesta. Es una característica del código, no del compose.

### Un worker, ocho hilos

Chroma abre SQLite sobre el directorio del índice, y varios procesos
escribiendo la caché de respuestas se pisan. La concurrencia va por hilos, que
además es lo que hace falta: el tiempo se va esperando a OpenAI, no en CPU.

El `timeout` de gunicorn y los de nginx están en 300 s. Con los 60 s por
defecto, nginx cortaba con 504 una consulta multimodal que adjunta los planos.

### `api-baseline` no se reinicia sola

`app_baseline.py` sale con un mensaje explícito si todavía no existe
`data/chroma_index_baseline`, y reintentar no lo arregla. Con
`restart: unless-stopped` eso sería un bucle que tapa el log, así que tiene
`restart: "no"`:

```bash
docker compose logs api-baseline     # dice exactamente qué falta
```

El frontend depende de ella con `service_started` y no con `service_healthy`, a
propósito: si la línea base no está lista, la pestaña "Comparar" muestra el
error en su columna y el resto de la app sigue funcionando.

---

## Los cuatro `.env` del proyecto

| Archivo | Quién lo lee |
|---|---|
| `.env` (raíz) | docker compose: claves, puertos, `WITH_ML` |
| `API/.env` | la API corriendo a mano, fuera de Docker |
| `Ingestion/.env` | el pipeline **siempre**, también en Docker (se monta) |
| `Frontend/.env` | solo `npm run dev`; en Docker las URLs son relativas |

Ninguno entra en las imágenes: `.dockerignore` excluye `**/.env`.

---

## Desarrollo

Docker es para correr el sistema, no para editarlo: el frontend se compila
dentro de la imagen y no tiene hot-reload. Para desarrollar sigue sirviendo lo
de siempre (ver [README.md](README.md)), con las APIs a mano y `npm run dev`.

Una forma cómoda es intermedia: las APIs en Docker y el frontend nativo.

```bash
docker compose up -d api api-baseline
cd Frontend && npm run dev     # :5173, apunta a localhost:5000 y :5001
```

Para eso el compose publica los puertos de las APIs y ellas mantienen su
`CORS_ALLOWED_ORIGIN`, que en ese caso hay que dejar en `http://localhost:5173`.

---

## Tamaños y tiempos

Medidos en esta máquina (arm64):

| Imagen | Tamaño |
|---|---|
| `unq-rag/web` | 77 MB |
| `unq-rag/api` | 901 MB |
| `unq-rag/ingestion` | 2,8 GB |

La API bajó de 1,17 GB a 901 MB al sacar también **scikit-learn y scipy**
(171 MB entre los dos): ningún módulo de la API los importa, estaban en el
entorno local porque los arrastra sentence-transformers. Verificado
desinstalándolos en la imagen y comprobando que `app.py`, `ChromaConnection` y
las consultas a Chroma siguen funcionando.

Lo que queda pesa sobre todo por `chromadb` (que trae onnxruntime, 59 MB),
`pandas` (73 MB) y `numpy` (36 MB).

La ingesta es grande porque lleva torch, OpenCV y Tesseract con los paquetes de
idioma. Es un batch que corre a demanda, así que el tamaño molesta poco.

---

## Dos cosas que solo aparecieron al probarlo

Quedan documentadas porque las dos dan síntomas confusos.

**El volumen de datos NO puede ir en `:ro`.** Suena a que las APIs solo leen,
pero montarlo en solo lectura hace que arranquen y devuelvan respuestas sin
contexto: Chroma 1.x falla con `Could not connect to tenant default_tenant`
porque necesita escribir el SQLite (WAL) para abrirlo. Y además la caché de
respuestas es una colección **dentro del mismo Chroma**
(`AppSettings__ChromaCacheIndex`), así que la API escribe ahí por diseño. El
síntoma es traicionero: 200 OK, pero 0 fuentes recuperadas y la respuesta
armada solo con los planos adjuntos.

**nginx cachea el DNS de los upstreams.** Resuelve los nombres de `proxy_pass`
una sola vez, al cargar la configuración. Medido: tras un
`docker compose up -d --force-recreate api api-baseline` los dos contenedores
**intercambiaron IP**, nginx siguió apuntando a la vieja y todo respondía 502
`connection refused`. Por eso `nginx.conf` declara
`resolver 127.0.0.11 valid=10s` y pone el upstream en una variable, con un
`rewrite` explícito para recortar el prefijo (con una variable, nginx ya no lo
hace solo). Verificado: reiniciar la API y volver a consultar devuelve 200.
