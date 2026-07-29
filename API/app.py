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

# Origen permitido para el frontend (React). "*" es cómodo para desarrollo local;
# en producción conviene restringirlo al dominio real vía esta env var.
CORS_ALLOWED_ORIGIN = os.getenv("CORS_ALLOWED_ORIGIN", "*")

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
    response.headers["Access-Control-Allow-Origin"] = CORS_ALLOWED_ORIGIN
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


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

        #santize data dict
        data  = sanitize_data(config_data)

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
