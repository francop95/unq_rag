#  Using flask to make an api
# import necessary libraries and functions
import os
import uuid
import logging
from models.ModelSingleton import ModelSingleton
from utils.parser import parse_conv_history
from flask import Flask, request, jsonify, make_response, send_from_directory, abort
from logging import StreamHandler
from configs.Configuration import Configuration
from services.RagWorkflow import RagWorkflow
from utils.utils import sanitize_data


# creating a Flask app
app = Flask(__name__)

# keep stdout/stderr logging using StreamHandler
streamHandler = StreamHandler()
if not app.logger.handlers:
    app.logger.addHandler(streamHandler)

# define log level to DEBUG
app.logger.setLevel(logging.DEBUG)

# apply same formatter on all log handlers
for logHandler in app.logger.handlers:
  logHandler.setFormatter(logging.Formatter('[BOT_API.%(module)s][%(levelname)s]%(message)s'))

# Orígenes permitidos para el frontend (React), separados por coma.
#
# El default ya NO es "*": son los dos orígenes del Vite de desarrollo. Con "*" la API
# quedaba consultable desde cualquier página web que el usuario tuviera abierta, y esta
# API no tiene autenticación, así que "*" es también "cualquiera en la red puede gastar
# tu cuota de OpenAI". Para exponerla en una red de planta:
#   CORS_ALLOWED_ORIGIN="http://ip-del-servidor:5173" API_TOKEN="..." python app.py
CORS_ALLOWED_ORIGIN = os.getenv(
    "CORS_ALLOWED_ORIGIN", "http://localhost:5173,http://127.0.0.1:5173"
)
ALLOWED_ORIGINS = [o.strip() for o in CORS_ALLOWED_ORIGIN.split(",") if o.strip()]

# Token opcional. Si se define API_TOKEN, /get_response exige el header
# `Authorization: Bearer <token>`. Sin la variable, la API queda abierta como antes
# (que es lo razonable para correrla en localhost).
API_TOKEN = os.getenv("API_TOKEN", "").strip()

# Carpeta de media generada por Ingestion (imágenes/diagramas/tablas), servida
# de solo lectura para que el frontend pueda mostrar lo que citan las fuentes.
MEDIA_BASE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Ingestion", "data")
)


@app.after_request
def add_header(response):
    """
      Add headers to the response to prevent clickjacking and enable HSTS
    """
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
    response.headers["Content-Security-Policy"] = "default-src 'none'; script-src 'self'; connect-src 'none'; img-src 'self'; style-src 'self'; frame-ancestors 'none'; form-action 'self';"

    # Se refleja el Origin solo si está en la lista, en vez de mandar "*": así el
    # navegador bloquea a cualquier otra página. Con "*" configurado explícitamente se
    # mantiene el comportamiento abierto, para no romper a quien lo necesite.
    origin = request.headers.get("Origin", "")
    if "*" in ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = "*"
    elif origin in ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"

    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return response


def _token_ok() -> bool:
    """
    True si no hay API_TOKEN configurado (modo local abierto) o si el request trae el
    Bearer correcto. La API llama a OpenAI en cada consulta, así que sin token y con
    CORS abierto cualquiera en la red puede gastar la cuota.
    """
    if not API_TOKEN:
        return True
    auth = request.headers.get("Authorization", "")
    return auth.startswith("Bearer ") and auth[7:].strip() == API_TOKEN


@app.route("/media/<path:relpath>", methods=["GET", "OPTIONS"])
def serve_media(relpath):
    """
    Sirve archivos de Ingestion/data/media/ (imágenes/diagramas/tablas) referenciados
    en `sources[].media[].media_path` de la respuesta de /get_response.
    """
    if request.method == "OPTIONS":
        return make_response("", 204)

    full_path = os.path.abspath(os.path.join(MEDIA_BASE_DIR, relpath))
    if not full_path.startswith(MEDIA_BASE_DIR + os.sep):
        abort(403)
    if not os.path.isfile(full_path):
        abort(404)

    directory, filename = os.path.split(full_path)
    return send_from_directory(directory, filename)


@app.route("/get_response", methods=["POST", "OPTIONS"])
def process_request():
    """
    Processes a request based on the provided parameters and returns a JSON response.

    Args:
        request_params (dict): A dictionary containing the Flask API request parameters.
            - query (str): The query or input text.
            - conv_history : coversation history in the current session of the bot.
            - conversation_id (str): current conversation id.
            - message_id (str): message id or query id.
            - tenant_id (str): tenant id for multi tenancy

    Returns:
        dict: A dictionary representing the JSON response.

    Example:
        request_params = {
            'query': 'Hello, how are you?',
            'conversation_id': <conversation_id>,
            'message_id': <message_id>,
            'conv_history': <conv_history>,
            'tenant_id': <tenant_id>
        }
        response = process_request(request_params)
    """
    if request.method == "OPTIONS":
        return make_response("", 204)

    if not _token_ok():
        return make_response(jsonify({"error": "No autorizado"}), 401)

    request_params = request.json
    try:
        app.logger.info("[MAIN] Initialize web app")
        init_data = ModelSingleton.getInstance()

        # set configurable items
        config_data  = init_data.copy()
        
        #process request params
        req_key_mappings = [("conv_id", "conversation_id", ""),
                            ("conv_history","conv_history",{}),
                        ("query_id", "message_id", str(uuid.uuid4())),
                        ("query", "query", None)]

        for data_key, req_key, default_value in req_key_mappings:
            if req_key in request_params:
                config_data[data_key] = sanitize_data(request_params[req_key])
            else:
                config_data[data_key] = default_value
        query_id = config_data["query_id"]
        if config_data["query"] is None:
            raise ValueError("Missing required parameter: query")
        config_data["updated_query"] = config_data["query"]

        # Los parámetros que vienen del request YA se sanitizaron uno por uno en el loop
        # de arriba, que es donde corresponde: el escape XSS es para lo que escribe el
        # usuario, no para la configuración propia.
        #
        # Acá antes había un `sanitize_data(config_data)` sobre el dict COMPLETO, que
        # escapaba también todos los prompts de Configuration. El efecto era que el LLM
        # recibía sus instrucciones corruptas: la especificación de formato del prompt de
        # intención llegaba como `&quot;question_type&quot;: &lt;question_type&gt;` en vez
        # de `"question_type": <question_type>`. Además re-escapaba la entrada del
        # usuario, convirtiendo un `&` en `&amp;amp;`.
        data = config_data

        app.logger.info(f"[{query_id}] [MAIN] Conv Id: " + str(data["conv_id"]))
        app.logger.info(f"[{query_id}] [MAIN] Query: " + str(data["query"]))
        app.logger.info(f"[{query_id}] [MAIN] Request params: {(str(sanitize_data(request_params)))}")

        # Conversation History
        if "conv_history" in request_params.keys():
            data["conv_history_df"] = parse_conv_history(sanitize_data(request_params["conv_history"]), data['query_id'])

        final_response_filtered = []

        project = data['project']
        query_id = data['query_id']
        app.logger.info(f"{query_id} [MAIN] Project: {project}")

        if project == "RagWorkflow":
            use_case_obj = RagWorkflow(data)
            final_response_filtered = use_case_obj.trigger_workflow(data)

        else:
            app.logger.info(f"{query_id} [MAIN] Unknwon project: {project}")

    except Exception as e:
        app.logger.error(f"[{query_id}] [MAIN] Exception: {str(e)}")


    result = make_response(jsonify({"Results": final_response_filtered}))
    result.headers["Content-Type"] = "application/json"

    app.logger.info(f"[{query_id}] [MAIN] Query ID Execution Ends!")
    app.logger.info(f"[{query_id}] [MAIN] End!")
    data = None


    return result


def initialize():
    """
    Performs initialization tasks for the application.

    This function is loaded once and performs tasks such as loading models,
    reading Excel files from a blob, and retrieving application settings from a configuration.
    It does not accept any input parameters and returns None.

    Example:
        initialize()
    """
    app.logger.info(f"[MAIN] Inside initialize()")

    # get bot instance
    ModelSingleton.getInstance()

# initialize
initialize()

# driver function
if __name__ == "__main__":
    app.run(debug=False)
