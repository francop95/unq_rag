# Pipeline de ingesta (batch).
#
# Una imagen para las dos ingestas: la multimodal (src/main_multimodal.py) y la
# línea base solo texto + OCR (src/main_text_baseline.py). No es un servicio:
# corre, procesa los PDF y termina.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Los modelos de sentence-transformers se bajan una vez y quedan en el
    # volumen que monta el compose; sin esto se rebajan en cada corrida.
    HF_HOME=/models \
    # Evita el warning de tokenizers al usar concurrencia en el enriquecimiento.
    TOKENIZERS_PARALLELISM=false

# tesseract con español e inglés: el OCR de planos escaneados y de figuras lo
# necesita. Sin el paquete de idioma, Tesseract falla al arrancar.
# libglib/libgomp son las bibliotecas de sistema que pide opencv-headless.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-spa \
        tesseract-ocr-eng \
        libglib2.0-0 \
        libgomp1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app/Ingestion

COPY Ingestion/requirements-docker.txt ./
# torch CPU primero y aparte: es la dependencia más pesada y la que menos
# cambia, así que conviene que quede en su propia capa cacheada.
#
# Pinneado a la versión del venv que produjo el índice actual. Sin pin, pip
# agarra la última (hoy 2.14) y el contenedor dejaría de ser reproducible: el
# pipeline genera embeddings CLIP que tienen que ser consistentes con los que ya
# están indexados, así que la versión del runtime de inferencia no es un detalle.
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch==2.11.0 \
 && pip install -r requirements-docker.txt

COPY Ingestion/src/     ./src/
COPY Ingestion/scripts/ ./scripts/
COPY Ingestion/eval/    ./eval/
COPY Ingestion/tests/   ./tests/

# data/ se monta por volumen: es la salida del pipeline y la entrada de la API.
# El código hace chdir a /app/Ingestion y usa rutas ./data/..., así que el
# layout tiene que coincidir con el del repo.
RUN mkdir -p /app/Ingestion/data /models

# Sin privilegios. Ojo con el volumen: los archivos que escriba el pipeline
# quedan con este UID. En Docker Desktop (macOS/Windows) da igual porque el
# mapeo es transparente; en Linux, si te molesta la propiedad, pasá
# `user: "${UID}:${GID}"` en el compose.
RUN useradd --create-home --uid 10001 ingestuser \
 && chown -R ingestuser:ingestuser /app /models
USER ingestuser

# Por defecto, la ingesta multimodal. El compose sobrescribe el comando para la
# línea base.
CMD ["python", "src/main_multimodal.py"]
