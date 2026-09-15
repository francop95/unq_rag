"""
API de la línea base — misma aplicación, índice solo texto + OCR
===============================================================

Levanta EXACTAMENTE la misma app Flask que `app.py`, pero apuntada a la
colección que construye `Ingestion/src/main_text_baseline.py`. No duplica ni
una línea de la lógica de retrieval ni de generación: importa `app.py` tal
cual, después de redirigir la configuración.

La idea es poder abrir dos pestañas del frontend, una contra cada versión, y
comparar la respuesta a la misma pregunta.

    Terminal 1:  python app.py              → :5000  índice multimodal
    Terminal 2:  python app_baseline.py     → :5001  índice solo texto + OCR

Por qué un wrapper y no una variable de entorno: `Configuration.get()` devuelve
el atributo de clase cuando no es None, y `AppSettings__ChromaIndex` está fijo
en el código, así que el entorno no lo pisa. Se parchea la clase ANTES de
importar `app`, que corre `initialize()` al importarse.

Variables de entorno que acepta:

    BASELINE_INDEX_NAME    colección Chroma     (default: baseline_documents)
    BASELINE_INDEX_PATH    carpeta del índice   (default: Ingestion/data/chroma_index_baseline)
    BASELINE_PORT          puerto               (default: 5001)
    CORS_ALLOWED_ORIGIN    orígenes permitidos  (default: los Vite 5173 y 5174)
    API_TOKEN              igual que en app.py  (opcional)
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

INDEX_NAME = os.getenv("BASELINE_INDEX_NAME", "baseline_documents")
INDEX_PATH = os.path.abspath(os.getenv(
    "BASELINE_INDEX_PATH",
    os.path.join(HERE, "..", "Ingestion", "data", "chroma_index_baseline"),
))
PORT = int(os.getenv("BASELINE_PORT", "5001"))

if not os.path.isdir(INDEX_PATH):
    sys.exit(
        f"\n❌ No existe el índice de la línea base:\n   {INDEX_PATH}\n\n"
        "   Construilo primero:\n"
        "     cd Ingestion && python src/main_text_baseline.py\n"
    )

# El frontend de comparación corre en 5174; sin esto el navegador bloquea las
# llamadas. Se respeta lo que ya venga del entorno.
os.environ.setdefault(
    "CORS_ALLOWED_ORIGIN",
    "http://localhost:5174,http://127.0.0.1:5174,"
    "http://localhost:5173,http://127.0.0.1:5173",
)

# --- Redirigir la configuración ANTES de importar app --------------------
from configs.Configuration import Configuration  # noqa: E402

Configuration.AppSettings__ChromaIndex = INDEX_NAME
Configuration.chroma_local_path = INDEX_PATH

# --- Modelo que redacta la respuesta -------------------------------------
#
# La versión multimodal responde con gpt-4.1, que tiene visión. Acá se usa un
# modelo SIN visión, para que la línea base sea "sin nada multimodal" de punta a
# punta y no quede la duda de si el modelo pudo haber mirado algo.
#
# En la práctica el cambio es simbólico: esta API ya no le manda ninguna imagen
# al modelo (se cortó el adjuntado de planos más abajo, y las imágenes de los
# chunks nunca se envían, solo su descripción en texto). Pero explicitarlo
# elimina la objeción.
#
# Por qué gpt-4-0613 y no gpt-3.5-turbo: es el GPT-4 original, genuinamente sin
# visión, y mantiene una capacidad de razonamiento comparable. Con gpt-3.5 la
# comparación mediría además "modelo más flojo", que es un segundo factor.
#
# Su límite es el contexto: 8k contra los 128k de gpt-4.1. Medido, el prompt de
# esta API ronda los 3k tokens (1125 del prompt de QnA + sistema + las 1-2
# fuentes que recupera), así que entra — pero por eso se baja max_tokens de
# 4000 a 2000, para no pasarse de los 8192 al sumar la respuesta.
#
# Configurable: BASELINE_OPENAI_MODEL=gpt-3.5-turbo (16k) si preferís más
# contexto, o =gpt-4.1 para volver a igualar el modelo de las dos versiones y
# aislar solo el efecto de la ingesta.
BASELINE_MODEL = os.getenv("BASELINE_OPENAI_MODEL", "gpt-4-0613")
Configuration.OPENAI_MODEL = BASELINE_MODEL

# Los modelos de 8k no toleran 4000 tokens de salida sobre un prompt de 3k.
if BASELINE_MODEL.startswith(("gpt-4-0", "gpt-4-32k")):
    Configuration.RETRIEVER_QNA_MAX_TOKENS = int(
        os.getenv("BASELINE_MAX_TOKENS", "2000")
    )

# La caché de respuestas es una colección aparte dentro del mismo índice: si las
# dos APIs compartieran nombre, la respuesta cacheada de una le llegaría a la
# otra y la comparación quedaría contaminada.
Configuration.AppSettings__ChromaCacheIndex = os.getenv(
    "BASELINE_CACHE_INDEX", "cache-index-baseline"
)

# El índice visual no existe en esta versión. Ya viene en False, pero se deja
# explícito: es parte de lo que define a la línea base.
Configuration.USE_VISUAL_RETRIEVAL = False

import app as flask_app  # noqa: E402  (importarlo corre initialize())


# --- Cortar el adjuntado de planos al LLM ---------------------------------
#
# Esto NO es cosmético: sin ello la comparación no mide lo que dice medir.
#
# La API adjunta "Plano distribucion electrica.pdf" y "conexionadoTben.pdf" como
# `input_file` al modelo cuando la consulta es eléctrica, y además lo hace SIEMPRE
# que ningún candidato supera el gate de relevancia (ver ChromaConnector: en ese
# camino la clave queda sin setear y ModelCompletion aplica su default True).
#
# Ese es justo el caso de la línea base en las preguntas de planos: no recupera
# nada, se le adjuntan los PDF, y gpt-4.1 los lee CON VISIÓN. Medido: contestaba
# sobre el conector de Ethernet sin haber recuperado un solo chunk, o sea que la
# "versión sin modelo de visión" estaba respondiendo con un modelo de visión.
#
# Se corta en los dos consumidores de la bandera, que la leen por separado.
from models.ModelCompletion_multimodal import ModelCompletion  # noqa: E402
from qnas.RetrieverQna_multimodal import RetrieverQna  # noqa: E402

_orig_model_init = ModelCompletion.__init__


def _init_sin_planos(self, data, model_type, dynamic_params={}):
    _orig_model_init(self, data, model_type, dynamic_params)
    self.attach_electric_diagrams = False


ModelCompletion.__init__ = _init_sin_planos
RetrieverQna._attached_plan_sources = staticmethod(lambda data, used_plans: [])

# Objeto WSGI, para poder servirlo con gunicorn (`gunicorn app_baseline:app`).
# Importar este módulo ya aplicó los parches de arriba, así que el `app` que se
# expone acá es el de la línea base, no el multimodal.
app = flask_app.app


if __name__ == "__main__":
    print("=" * 66)
    print("  API — LÍNEA BASE (solo texto + OCR, sin modelo de visión)")
    print("=" * 66)
    print(f"  colección : {INDEX_NAME}")
    print(f"  índice    : {INDEX_PATH}")
    print(f"  modelo    : {BASELINE_MODEL}  (sin visión)")
    print(f"  max_tokens: {Configuration.RETRIEVER_QNA_MAX_TOKENS}")
    print(f"  caché     : {Configuration.AppSettings__ChromaCacheIndex}")
    print(f"  puerto    : {PORT}")
    print(f"  CORS      : {os.environ['CORS_ALLOWED_ORIGIN']}")
    print("=" * 66)
    print(f"\n  Frontend contra esta API:")
    print(f"    cd Frontend && VITE_API_BASE_URL=http://localhost:{PORT} "
          f"npm run dev -- --port 5174\n")
    flask_app.app.run(host="0.0.0.0", port=PORT, debug=False)
