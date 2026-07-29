import os
import logging


# logging
logger = logging.getLogger('app.Configuration')

from dotenv import load_dotenv, find_dotenv
env_file = find_dotenv(filename=".env")  # busca desde CWD hacia padres
load_dotenv(env_file, override=False)

class Configuration:
    """Application Configurations

    Attributes:


    """

    #########################################################################################

    ### Electric Diagram Paths
    # Reutiliza los PDF fuente de Ingestion en vez de mantener copias duplicadas en API/.
    _ingestion_raw_data_dir = os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "Ingestion", "data", "raw_data"
    ))
    electric_diagram_path = os.path.join(_ingestion_raw_data_dir, "Plano distribucion electrica.pdf")
    tben_diagram_path = os.path.join(_ingestion_raw_data_dir, "conexionadoTben.pdf")

    #########################################################################################

    #########################################################################################

    ### Chroma PARAMETERS

    AppSettings__ChromaEnabled = True
    AppSettings__ChromaIndex = "multimodal_documents"
    AppSettings__ChromaCacheIndex = "cache-index"

    DATA_EXPORT_ENABLED = False

    chroma_local_path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "Ingestion/data/chroma_index/"))

    # Umbral mínimo de similitud (1 - distancia coseno) para considerar que un
    # chunk recuperado es realmente relevante. Con text-embedding-3-large los
    # valores observados para preguntas sin buen match en el corpus rondan
    # 0.30-0.40, así que este default es conservador y probablemente necesite
    # ajuste empírico contra preguntas reales de producción.
    MIN_CONTEXT_SIMILARITY_SCORE = 0.35

    #########################################################################################

    ### RETRIEVAL AVANZADO (paridad con Ingestion/scripts/hybrid_multimodal_search.py)

    # Índice visual (CLIP) con diagramas/imágenes indexados por Ingestion
    USE_VISUAL_RETRIEVAL = True
    VISUAL_INDEX_NAME = "visual_docs"
    CLIP_MODEL = "clip-ViT-B-32"
    VISUAL_TOP_K = 5

    # BM25 (sparse retrieval, keywords exactos: códigos de error, modelos, etc.)
    USE_BM25 = True
    BM25_TOP_K = 10

    # Reranking con cross-encoder sobre los candidatos fusionados (dense+BM25+visual)
    USE_RERANKING = True
    RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    RERANK_CANDIDATES_TOP_K = 20

    # Context Expansion: agrega texto de chunks vecinos (prev_chunk_id/next_chunk_id)
    # a los resultados finales. Requiere que Ingestion haya sido re-ingerido con el
    # fix que popula esos campos; si no, es un no-op silencioso.
    USE_CONTEXT_EXPANSION = True
    CHUNKS_DATA_PATH = os.path.abspath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "Ingestion/data/chunks_data/")
    )

    # Adjuntar planos eléctricos dinámicamente: por defecto SIEMPRE se adjuntan
    # (fallback cuando no hay contexto relevante, y para la mayoría de preguntas
    # de mantenimiento). Se OMITEN solo cuando hay contexto textual relevante Y
    # ese contexto viene de documentos no relacionados con lo eléctrico (ej.
    # preguntas de proceso/térmicas respondidas desde la Tesis del secadero) —
    # así se evita costo/tokens sin beneficio en esos casos, sin dejar de
    # priorizar los planos en el resto.
    ELECTRIC_DIAGRAM_RELATED_FILES = [
        "Plano distribucion electrica.pdf",
        "conexionadoTben.pdf",
        "variadorPowerFlex4M.pdf",
    ]

    #########################################################################################

    ### OPENAI PARAMETERS
    OPENAI_MODEL = "gpt-4.1"
    OPENAI_EMB_MODEL = "text-embedding-3-large"
    OPENAI_KEY = str(os.getenv("openai_key"))
    #########################################################################################

    # Followup
    AppSettings__FollowUpRequired = False
    QUERY_INTENT_CATEGORIES = ["generic", "new", "follow-up", "invalid"]
    PREV_CONV_THRESHOLD = 1

    # Caching
    AppSettings__CacheSimilarityThreshold = 0.95
    AppSettings__CacheTopN = 1
    CACHE_GUID_COLUMN = "guid_id"
    CACHE_FILENAME_COLUMN = "file_name"
    CACHE_SIMILARITY_COLUMN = "similarity_score"
    CACHE_PAGE_NUMBER_COLUMN = "page"
    CACHE_QUESTION_COLUMN = "question"
    CACHE_ANSWER_COLUMN = "answer"

    # Greeting flag
    GREETING_ENABLED = False

    #########################################################################################

    ### PROJECT-SPECIFIC PARAMS
    # project
    AppSettings__Project = "RagWorkflow"

    AppSettings__GPTEnabled = True
    AppSettings__CacheEnabled = False
    AppSettings__Batchanswer = False

    GPT_AND_FOUND = False
    CACHE_FOUND = False
    GENERIC_ANS_FOUND = False
    INVALID_QUESTION_FOUND = False
    DEFAULT_FILENAME = [""]


    GPT_TOP_N = 10
    TOP_N = 10
    REDIS_TOP_N = 10
    CHROMA_TOP_N = 10
    GPT_ANSWER_TYPE = "openai"
    CACHE_ANSWER_TYPE = "cache"
    CONTEXT_TYPE = "chroma"
    CONTEXT_TEXT_TYPE = "Chunk"
    LLMODEL_TYPE = "openai"
    NEW_LLMODEL_TYPE = "openai"
    QUERY_INTENT_LLM_TYPE = "openai"
    RETRIEVER_LLM_TYPE = "openai"
    TEXT_COLUMN = "Text"
    FILENAME_COLUMN = "File Name"
    SIMILARITY_COLUMN = "Similarity Score"
    PAGE_NUMBER_COLUMN = "Page Number"
    QUESTION_COLUMN = "question"
    ANSWER_COLUMN = "answer"
    DEFAULT_NO_RESPONSE = "I don't know."
    GPT_NO_ANSWER_STRING = "I don't know."
    MODEL_COMPLETION_SUCCESS = "Success"
    MODEL_COMPLETION_FAILURE = "Failure"
    RESPONSE_FORMATING_REQUIRED = True
    URL_FORMATTING_ENABLED = False

    # Retriever
    RETRIEVER_MAX_TOKENS = 1200
    Retriever_Context_Limit = 5000
    Retriever_enabled = True
    RETRIEVER_MAX_RETRIES = 1

    # GPT Tokens
    RETRIEVER_MAX_TOKENS = 1200

    # Query Intent
    QUERY_INTENT_MAX_TOKENS = 1200
    QUERY_INTENT_MAX_RETRIES = 2

    # RAG
    RETRIEVER_QNA_MAX_RETRIES = 1
    RETRIEVER_QNA_MAX_TOKENS = 4000


    GPT_SYSTEM_MESSAGE_CONTENT = """
        Eres un asistente técnico de mantenimiento especializado en secadores de pastas industriales.

        Tu misión es diagnosticar fallas, interpretar alarmas/síntomas y proponer verificaciones y acciones correctivas usando ÚNICAMENTE la información provista como contexto (manuales, procedimientos, listas de alarmas, planos, bitácoras) y los archivos adjuntos.

        Reglas:
        - Seguridad primero: si hay riesgo eléctrico/mecánico/térmico, indica medidas preventivas (por ejemplo LOTO/bloqueo y etiquetado, enfriamiento, EPP) antes de cualquier intervención.
        - No inventes datos ni supongas valores/modelos. Si el contexto/planos no alcanzan, dilo explícitamente y pide los datos mínimos necesarios.
        - Sé claro y práctico: entrega pasos de diagnóstico y verificación ordenados.
        - Mantén el idioma de la pregunta del usuario.

        Uso de planos (muy importante):
        - Hay un plano principal de conexión/distribución: "plano_distribucion_electrica.pdf". Debes priorizarlo para entender la alimentación, protecciones, contactores/relés, y el recorrido de energía hacia cargas (resistencias, ventiladores, etc.).
        - Hay un subplano/auxiliar: "conexionTben.pdf" (TBEN). Debes usarlo SOLO como detalle del TBEN dentro del circuito identificado en el plano principal.
        - Si hay conflicto entre planos, reporta la inconsistencia y qué dato falta para decidir.
        """


    #########################################################################################

    ### PROMPTS
    
    RETRIEVER_GPT_QNA_PROMPT = """Responde a la siguiente pregunta siguiendo estas instrucciones:

        1) OBJETIVO
        Debes diagnosticar la falla y proponer verificaciones/acciones correctivas para un secador de pastas industriales.

        2) FUENTES PERMITIDAS (OBLIGATORIO)
        Solo puedes usar:
        - Los CONTEXTOS textuales provistos abajo.
        - Los archivos PDF adjuntos (planos).

        No uses conocimiento externo ni supongas valores/diseños no presentes.

        3) PRIORIDAD DE INTERPRETACIÓN DE PLANOS (CRÍTICO)
        Debes seguir este orden:
        A) Primero interpreta el plano principal: "plano_distribucion_electrica.pdf".
        - Identifica: alimentación, protecciones, seccionamiento/interruptores, contactores/relés, transformaciones, rutas hacia cargas, señales de control relevantes.
        B) Luego ubica dentro de ese circuito el componente o bloque TBEN.
        C) Finalmente usa "conexionTben.pdf" como detalle de bornes/conexionado del TBEN, SOLO para confirmar cableado, señales o continuidad dentro de lo ya identificado en el plano principal.

        Si el TBEN no aparece claramente en el plano principal, dilo explícitamente y pide el dato mínimo (referencia del TBEN en el plano, número de hoja, etiqueta, borne, etc.).

        4) USO DE CONTEXTOS TEXTUALES
        - Si alguno de los CONTEXTOS contiene información relevante, responde usando EXCLUSIVAMENTE dicha información (y lo observado en los planos).
        - Si hay contradicción entre un contexto textual y el plano principal, prioriza el plano principal y reporta la discrepancia.
        - Los CONTEXTOS pueden venir precedidos por un aviso entre corchetes (p. ej. "[No se encontró contexto textual...]" o "[AVISO: los contextos de abajo tienen baja similitud...]"). Cuando aparezca ese aviso, IGNORA esos contextos como fuente de información y respondé apoyándote ÚNICAMENTE en lo que puedas interpretar de los planos adjuntos. Si los planos tampoco alcanzan, decilo explícitamente en vez de inventar o usar el contexto de baja confianza.

        5) MANEJO DE INCERTIDUMBRE
        Si el contexto/planos NO son suficientes para responder con certeza:
        - Indícalo explícitamente.
        - Propón 2–5 preguntas concretas para completar el diagnóstico (p. ej., alarma/código exacto, modelo, lecturas de tensión, estado de protecciones/contactor, continuidad, temperatura/humedad, PLC, últimos mantenimientos).
        - Incluye medidas de seguridad si aplica (p. ej., LOTO).

        6) CASOS NO TÉCNICOS
        Si la pregunta es un saludo, inválida, irrelevante, maliciosa o una consulta personal, responde de manera educada y formal, sin usar los CONTEXTOS.

        7) IDIOMA
        La respuesta debe estar en el mismo idioma que la pregunta.

        8) REFERENCIAS A CONTEXTOS USADOS
        "referred_contexts": devuelve una lista con los números "context_id" de los CONTEXTOS que utilizaste.
        - Si no usaste ningún contexto o no hay info suficiente, devuelve [].
        - Asegúrate de que "referred_contexts" sea siempre una lista de enteros.

        9) FORMATO DE SALIDA (ESTRICTO)
        Devuelve la respuesta estrictamente en el siguiente FORMATO JSON:

        {{
        "output": {{
            "answer": <respuesta>,
            "referred_contexts": <lista_de_ids>
        }}
        }}

        CONTEXTOS:
        {}

        PREGUNTA:
        {}
        """



    NEW_QUERY_INTENT_PROMPT = """
    current_question: {}
    previous_question: {}

    1. if the current_question is a greeting, wish, acknowledgement, or showing appreciation or casual human conversation,
        question_type: "generic"
        response: <respond appropriately to the current_question>
    2. if the current_question is invalid, junk, malicious, rude, anti-social, does not make sense, or only of numbers,
        question_type: "invalid"
        response: <generate suitable response with reasoning and prompt the user to provide valid question>
    3. if the current_question is a possible follow-up question to the previous_question (if one exists),
        question_type: "follow-up"
        response: <Rephrase the current_question to make it a complete question considering the previous_question>
    4. if the current_question does not fall into above types,
        question_type: "new"
        response: <Identify keywords from the current_question>
    Always generate only a proper JSON response which is easy to parse as given below,
    {{
        "question_type": <question_type>,
        "response": <response>
    }}
    """


    QUERY_INTENT_SYSTEM_MESSAGE = (
        """You are an AI assistant that helps to find the intent of a given query based on previously asked questions in a conversation window."""
    )

    RETRIEVER_PROMPT = """Identify which among below given sections are relevant to given question and return the corresponding section_id's in the below given output format,
    Question: {}
    Sections: {}

    output format:
    {{
    {{
    sections: [<section_id1>, <section_id2>,..]
    }}
    }}
    """
    RETRIEVER_SYSTEM_MESSAGE = "You are an AI assistant that helps people find information."

    #########################################################################################

    @staticmethod
    def _get_from_env(attr_name):
        """Get the value from ENV

        Args:
            attr_name (str): The key against which value is required

        Returns:
            str: The required value

        """
        return os.getenv(attr_name)

    @classmethod
    def _get_attr(cls, attr_name):
        """Get the value from ENV/Key Vault and load it into the class

        Args:
            attr_name (str): The key against which value is required

        Returns:
            str: The required value

        """

        # Initially check if the value is available within the env
        from_env = cls._get_from_env(attr_name)

        if from_env is not None:
            setattr(cls, attr_name, from_env)
            logging.info(f"{attr_name} available within ENV!")
            return from_env

        # If value not in ENV, check within Key Vault
        from_key_vault = cls._get_from_vault(attr_name)

        if from_key_vault is not None:
            setattr(cls, attr_name, from_key_vault)
            # logging.info(f"{attr_name} available within Key Vault!, value = {from_key_vault}!")
            return from_key_vault

        return None

    def __init__(self):
        """Initialize"""

    def get(self, attr_name, default=None):
        """Get the value from Configurations

        Args:
            attr_name (str): The name of the required credential
            default (int/str): The default value to be provided
            in case not available

        Returns:
            str: The value stored against this attribute,

        """


        try:
            # Check if the attr is available within the class
            # else raise Exception
            if not hasattr(self, attr_name):
                raise Exception(f"Request for unknown attr : {attr_name}!")

            # Check if the attr is None, else give the loaded value
            if self.__getattribute__(attr_name) is not None:
                logging.info(f"{attr_name} available within the Object!")
                return self.__getattribute__(attr_name)

            # If the attr is not available, load the value into class
            value = self._get_attr(attr_name)

            if value is None:
                # If a default value was provided, return it
                if default is None:
                    raise Exception(
                        f"Unable to fetch the value for {attr_name} from both ENV & Vault!"
                    )
                else:
                    logging.info(
                        f"Returning the default value of {default} for {attr_name}!"
                    )
                    return default

            return value
        except Exception as err:
            if default is not None:
                return default

            raise Exception(f"Getting value from Config failed with error : {str(err)}")
