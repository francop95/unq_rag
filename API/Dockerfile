# API de consulta (Flask + Chroma).
#
# Sirve las dos variantes con la MISMA imagen: la multimodal (app.py) y la línea
# base solo texto + OCR (app_baseline.py). Lo único que cambia es el comando y
# las variables de entorno; ver docker-compose.yml.

FROM python:3.12-slim AS base

# WITH_ML=1 agrega torch + sentence-transformers para el reranking cross-encoder
# y el retrieval visual CLIP. Por defecto no van: están desactivados por
# medición y suman ~2,5 GB.
ARG WITH_ML=0

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# curl es solo para depurar desde adentro del contenedor; el healthcheck del
# compose usa el python que ya trae la imagen. build-essential no hace falta:
# todas las dependencias tienen rueda para linux/amd64 y arm64.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app/API

# Las dependencias primero y en su propia capa: el código cambia en cada commit,
# esta lista casi nunca, así que el rebuild no vuelve a bajar todo.
COPY API/requirements-docker.txt API/requirements-docker-ml.txt ./
RUN pip install -r requirements-docker.txt \
 && if [ "$WITH_ML" = "1" ]; then \
        pip install --index-url https://download.pytorch.org/whl/cpu torch==2.8.0 \
     && pip install -r requirements-docker-ml.txt ; \
    fi

# Solo lo que necesita el servicio web. tkinterapp/ queda afuera: es la app de
# escritorio, necesita Tk y no la usa el contenedor.
COPY API/app.py API/app_baseline.py ./
COPY API/configs/    ./configs/
COPY API/contexts/   ./contexts/
COPY API/models/     ./models/
COPY API/qnas/       ./qnas/
COPY API/results/    ./results/
COPY API/services/   ./services/
# Registro de ejecuciones y feedback. Se lista explícitamente como el resto:
# este COPY enumera paquetes uno por uno, así que un paquete nuevo que no se
# agregue acá compila igual y revienta al arrancar con ModuleNotFoundError.
COPY API/telemetria/ ./telemetria/
COPY API/utils/      ./utils/

# Punto de montaje de los datos que produce la ingesta. La ruta importa:
# Configuration.py resuelve el índice como ../../Ingestion/data/chroma_index
# relativo a su propio archivo, y app.py sirve /media desde ../Ingestion/data.
# Replicando el layout del repo, el código funciona sin cambios.
RUN mkdir -p /app/Ingestion/data

# Sin privilegios, y escribible por cualquier uid del grupo 0.
#
# El segundo requisito no es capricho: la app escribe TRES cosas dentro de su
# propio directorio —perf_timings.csv en el cwd, data/openai_file_ids.json y
# data/response_cache.json—, todas con rutas fijas en el código.
#
# En producción el contenedor tiene que correr con el uid del dueño de los datos
# del bind mount (si no, Chroma no puede escribir el WAL del índice), y ese uid
# depende del host. La solución estándar para "uid arbitrario" es dar la
# propiedad al grupo 0 con permiso de escritura: así el proceso puede correr con
# cualquier uid siempre que su gid sea 0, sin recurrir a chmod 777.
#
# Ver el `user:` de deploy/docker-compose.prod.yml.
RUN useradd --create-home --uid 10001 apiuser \
 && mkdir -p /app/API/data \
 && chown -R apiuser:0 /app \
 && chmod -R g+w /app
USER apiuser

EXPOSE 5000

# 1 worker: Chroma abre SQLite sobre el directorio del índice y varios procesos
# escribiendo la caché de respuestas se pisan. La concurrencia va por hilos, que
# es lo que hace falta acá porque el tiempo se va esperando a OpenAI.
# timeout alto por la misma razón: una respuesta multimodal con planos adjuntos
# puede tardar más de un minuto.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", \
     "--workers", "1", "--threads", "8", \
     "--timeout", "300", "--graceful-timeout", "30", \
     "--access-logfile", "-", "--error-logfile", "-", \
     "app:app"]
