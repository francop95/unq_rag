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
    # chunk recuperado es realmente relevante.
    #
    # Calibrado midiendo sobre el índice real (10 consultas, text-embedding-3-large,
    # con Contextual Retrieval activo):
    #   - En tema:        similitud top-1 entre 0.640 y 0.947
    #   - Fuera de tema:  similitud top-1 entre 0.232 y 0.440
    # El valor anterior (0.35) quedaba DENTRO de la banda de fuera-de-tema: una
    # consulta como "cómo configuro un router wifi" daba 0.440 y pasaba el filtro
    # como si tuviera contexto relevante. 0.50 cae en el hueco entre ambas bandas,
    # con más margen del lado de no rechazar preguntas legítimas (rechazarlas es
    # el error más caro: el LLM pierde el contexto textual y responde solo con los
    # planos).
    #
    # VALIDADO después con el eval set (54 consultas en tema + 5 fuera de tema):
    #
    #   umbral   recall@10   gate fuera de tema
    #    0.35      88.9%          3/5   <- pasan 2 consultas fuera de tema
    #    0.50      88.9%          5/5   <- óptimo
    #    0.60      85.2%          5/5   <- pierde 2 consultas legítimas
    #
    # 0.50 da el mismo recall que 0.35 con el gate perfecto, y mejor recall que 0.60.
    # Reproducir con: python eval/run_eval.py --variant min_context_similarity_score=0.35
    MIN_CONTEXT_SIMILARITY_SCORE = 0.50

    #########################################################################################

    ### RETRIEVAL AVANZADO (paridad con Ingestion/scripts/hybrid_multimodal_search.py)

    # Índice visual (CLIP) con diagramas/imágenes indexados por Ingestion.
    #
    # DESACTIVADO por medición: igual que BM25, apagarlo no cambia ni un dígito del
    # eval. Ahorra cargar el modelo CLIP (~90 MB) en cada arranque de la API.
    #
    # Con una salvedad honesta: el eval son consultas de TEXTO, y CLIP existe para
    # buscar por semejanza visual ("el diagrama que tiene un contactor y tres
    # fusibles"). Lo que sí está medido es que, como está cableado, es inerte para
    # cualquier tipo de consulta: un candidato que solo encuentra CLIP no tiene
    # `dense_similarity` y el gate lo descarta siempre. Si algún día se quiere evaluar
    # de verdad, hace falta un eval con consultas visuales Y revisar ese cableado.
    #
    # Ingestion sigue construyendo la colección `visual_docs` (110 imágenes): es barato
    # y deja la puerta abierta.
    USE_VISUAL_RETRIEVAL = False
    VISUAL_INDEX_NAME = "visual_docs"
    CLIP_MODEL = "clip-ViT-B-32"
    VISUAL_TOP_K = 5

    # Cableado de la fusión. Ambos en False = comportamiento medido como mejor.
    #
    # FUSION_ADMITS_SPARSE: si un candidato que encontró solo BM25/CLIP puede pasar el
    #   gate de relevancia (que filtra por similitud densa, una escala que esos
    #   candidatos no tienen). Activarlo NO cambió nada: recall@10 88.9% igual.
    # FUSION_DECIDES_ORDER: si el orden final lo decide el score de fusión RRF en vez
    #   de la similitud densa. Activarlo EMPEORA: recall@10 87.0% (vs 88.9%),
    #   MRR 0.733 (vs 0.754), 7 consultas sin respuesta en vez de 6.
    #
    # O sea: el RRF se calcula y no se usa para ordenar, y está bien que así sea. Se
    # dejan como flags en vez de borrar el código porque con otro corpus —menos
    # preguntas sintéticas, o documentos con más códigos— la respuesta puede cambiar,
    # y ahora medirlo es un `--variant`.
    FUSION_ADMITS_SPARSE = False
    FUSION_DECIDES_ORDER = False

    # BM25 (sparse retrieval, keywords exactos: códigos de error, modelos, etc.)
    #
    # DESACTIVADO por medición: no aporta NADA en este corpus. Apagarlo da resultados
    # idénticos dígito por dígito sobre el eval set (recall@1/3/5/10 = 36/45/46/48,
    # MRR 0.754), y en un test de 8 consultas de código puro ("22B-D010N104", "A450",
    # "d012"...) mejoró 0 de 8.
    #
    # La causa era arquitectural, y arreglarla tampoco ayudó (ver FUSION_* abajo):
    # el motivo de fondo es que el índice es multi-vector con ~2900 vectores de
    # preguntas sintéticas escritas en lenguaje de usuario. Eso es cobertura de
    # paráfrasis metida en el índice, y le gana al matching léxico en su propio terreno.
    #
    # Apagarlo ahorra construir el índice en RAM en cada arranque. Reactivar es cambiar
    # esto a True; conviene re-medir con `eval/run_eval.py --variant use_bm25=true`.
    USE_BM25 = False
    BM25_TOP_K = 10

    # Reranking con cross-encoder sobre los candidatos fusionados (dense+BM25+visual).
    # DESACTIVADO a propósito: medido, en esta configuración perjudica.
    #
    # El pipeline arma un pool fusionado (denso 10 + BM25 10 → RRF) y después
    # corta en CHROMA_TOP_N=10 chunks, que son los que ve el LLM. Con reranking,
    # el corte lo decide el cross-encoder: no solo reordena, también ELIGE cuáles
    # 10 sobreviven. Y ahí es donde pierde.
    #
    # Medido replicando el pipeline real (53 consultas con ground truth):
    #
    #   orden                         recall@5  recall@10   MRR   expulsa/rescata
    #   RRF (sin reranker)              81.1%     94.3%    0.530        --
    #   bge-reranker-base               73.6%     84.9%    0.511      7 / 2
    #   mmarco-mMiniLMv2-L12            71.7%     84.9%    0.576      7 / 2
    #   ms-marco-MiniLM-L-6-v2          64.2%     83.0%    0.471      9 / 3
    #
    # "expulsa/rescata" = consultas donde el reranker saca el chunk correcto del
    # top-10 vs donde lo mete. Los tres modelos expulsan más de lo que rescatan:
    # en ~5 de 53 consultas el LLM deja de recibir la respuesta. recall@10 es
    # además la métrica más robusta acá: no le afecta la objeción de que "otro
    # chunk podría responder igual de bien", porque mide si el chunk correcto
    # llega o no al contexto.
    #
    # CONFIRMADO después con el eval set propio (54 preguntas parafraseadas en voz de
    # técnico, no las sintéticas del índice), y ahí el daño es MUCHO peor:
    #
    #                        recall@1  recall@10   MRR    nunca llega
    #   RRF (sin reranker)     66.7%     88.9%    0.754      6/54
    #   mmarco-mMiniLMv2-L12    7.4%     61.1%    0.193     21/54
    #
    # La categoría "proceso" pasa de 3/3 a 0/3. Reproducir con:
    #   python eval/run_eval.py --variant use_reranking=true
    USE_RERANKING = False
    RERANKER_MODEL = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
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
    # Nombre canónico: OPENAI_API_KEY. Se acepta `openai_key` como alias para no
    # romper los .env anteriores.
    #
    # Cae a "" y no a "None": antes era str(os.getenv(...)), que devolvía el string
    # "None" cuando la variable no existía. Ese string es truthy, así que anulaba
    # los `or` que hacen de fallback aguas abajo (LanguageModels) y la key inválida
    # llegaba hasta la llamada a OpenAI.
    OPENAI_KEY = os.getenv("OPENAI_API_KEY") or os.getenv("openai_key") or ""
    #########################################################################################

    # Followup
    #
    # ACTIVADO. Un asistente de mantenimiento sin conversación obliga a repetir el
    # contexto en cada pregunta: "y si eso no funciona, qué reviso?" no tenía forma de
    # saber a qué se refería "eso".
    #
    # El mecanismo NO es pasarle el historial al LLM que responde: QueryIntent clasifica
    # la consulta y, si es un follow-up, la REESCRIBE como pregunta autónoma antes del
    # retrieval (check_followup). Eso importa porque el retrieval es denso: buscar el
    # vector de "y si eso no funciona" no recupera nada, mientras que buscar el de "y si
    # cambiar P106 no hace arrancar el variador, qué reviso" sí.
    #
    # Costo: una llamada extra al LLM por consulta (la clasificación de intención), solo
    # cuando llega historial.
    AppSettings__FollowUpRequired = True
    QUERY_INTENT_CATEGORIES = ["generic", "new", "follow-up", "invalid"]
    # Cuántas preguntas anteriores se consideran para decidir si es follow-up
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
    AppSettings__Batchanswer = False

    # Caché de respuestas por coincidencia EXACTA de la pregunta (ver
    # qnas/ResponseCache.py). Una consulta repetida cuesta 10-30s y los tokens de dos
    # PDFs adjuntos; con el caché es instantánea y gratis.
    #
    # Es exacto y no semántico por medición, no por simplicidad: se comparó la similitud
    # coseno de pares de preguntas de este dominio y las bandas se solapan —
    # "corriente máxima de ENTRADA" vs "de SALIDA" da 0.916, mientras dos redacciones de
    # la MISMA pregunta dan 0.872 y 0.929. Con el umbral 0.95 que estaba configurado no
    # habría acertado ninguna reformulación, y bajarlo para capturarlas metería adentro
    # los pares que significan lo contrario: en mantenimiento eso devuelve el borne o el
    # parámetro equivocado.
    #
    # Antes esto leía de CachedQna sobre Azure Search, que además nunca tuvo ruta de
    # escritura: consultaba un índice que nadie poblaba, así que siempre fallaba.
    AppSettings__CacheEnabled = True
    RESPONSE_CACHE_PATH = os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "data", "response_cache.json"
    ))
    RESPONSE_CACHE_MAX_ENTRIES = 500
    # 0 = sin expiración (no None: Configuration.get() lanza excepción si el valor es
    # None y no se le pasa un default). Se invalida a mano tras re-ingestar, porque las
    # respuestas guardadas citan páginas y chunks del índice viejo.
    RESPONSE_CACHE_TTL_SECONDS = 0

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
    # Ojo: esta bandera elige el camino de respuesta (RetrieverQna, el multimodal
    # con fuentes/planos/media, vs el viejo GPTQna que no las devuelve). Ver
    # QuestionAnswer.answer_query. No es la que controla el selector LLM de abajo.
    Retriever_enabled = True
    RETRIEVER_MAX_RETRIES = 1

    # Selector LLM de secciones (Retriever_multimodal, invocado desde
    # ChromaConnection._candidates_to_df): una llamada extra al LLM que, dado el
    # top-N recuperado, elige qué secciones dejar pasar al QnA.
    #
    # DESACTIVADO. Medido en los logs: fallaba en el 100% de las consultas y
    # gastaba 2 llamadas al LLM por consulta (la original + el reintento) para
    # terminar en un no-op. Tenía dos defectos independientes:
    #
    #  1. Recibía `base`, el DataFrame ANTES de renombrar columnas, pero
    #     _build_section_metadata las busca por los nombres configurados
    #     (TEXT_COLUMN="Text", FILENAME_COLUMN="File Name"...). Ninguno existía en
    #     `base` (que trae "document"/"file_name"/"page_num"), así que mandaba
    #     `page_metadata: ""` para TODAS las secciones: el LLM tenía que elegir
    #     entre los ids 1..N sin ver nada de su contenido.
    #  2. RETRIEVER_PROMPT pide un formato inválido ({ { sections: [...] } }, con
    #     llave doble y clave sin comillas). El modelo lo devolvía tal cual y no
    #     lo parseaba ni json.loads ni el fallback por regex.
    #
    # No se arregla porque la etapa es redundante: el QnA ya recibe el texto
    # COMPLETO de los chunks y elige cuáles citar (referred_contexts), mientras
    # que este selector decidía sobre snippets de 280 caracteres. Filtrar chunks
    # antes de que el LLM que responde los vea solo puede bajar el recall — el
    # mismo motivo por el que se desactivó el reranker (ver USE_RERANKING).
    LLM_SECTION_SELECTOR_ENABLED = False

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
        - Si un CONTEXTO viene marcado con "[IMAGEN DISPONIBLE para el usuario en la interfaz...]", significa que existe una foto/imagen real de eso que el usuario SÍ va a poder ver (se muestra aparte, junto con tu respuesta, aunque vos no la puedas insertar en el texto). En ese caso NUNCA digas que "no hay fotos/imágenes disponibles" ni que "el material sólo tiene planos, no fotografías": en cambio, decí explícitamente que la imagen está disponible para ver junto con la respuesta (ej. "podés ver la foto del forzador de aire más abajo, junto con esta respuesta").

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

        "used_plans": true si para responder te apoyaste en alguno de los planos PDF
        adjuntos, false si no. Poné false cuando la pregunta no sea técnica o cuando
        respondas que no hay información suficiente: sirve para decidir si al usuario
        se le muestran los planos como fuente consultable, y listarlos cuando no los
        usaste lo confunde.

        9) FORMATO DE SALIDA (ESTRICTO)
        Devuelve la respuesta estrictamente en el siguiente FORMATO JSON:

        {{
        "output": {{
            "answer": <respuesta>,
            "referred_contexts": <lista_de_ids>,
            "used_plans": <true|false>
        }}
        }}

        CONTEXTOS:
        {}

        PREGUNTA:
        {}
        """



    # Clasificador de intención. Reescrito respecto del template original, que era
    # boilerplate genérico en inglés: clasificaba "¿Y si eso ya está bien configurado,
    # qué reviso después?" como "new" en vez de "follow-up", con lo cual la pregunta no
    # se reescribía y el retrieval buscaba el vector de un texto sin ningún ancla
    # técnica. La reescritura es lo único que hace funcionar un follow-up con retrieval
    # denso, así que clasificar mal acá es equivalente a no tener conversación.
    NEW_QUERY_INTENT_PROMPT = """Sos el clasificador de intención de un asistente técnico
    de mantenimiento de un secadero de pastas industrial (variadores de frecuencia,
    tableros eléctricos, sensores, PLC).

    pregunta_actual: {}
    pregunta_anterior: {}

    Clasificá la pregunta_actual en UNA de estas categorías:

    1. "generic" — es un saludo, agradecimiento, despedida o charla casual.
       response: <respondé de forma breve y cordial>

    2. "invalid" — es basura, ofensiva, o no tiene ningún sentido.
       response: <explicá brevemente y pedí una pregunta técnica concreta>

    3. "follow-up" — continúa la pregunta_anterior. ESTA ES LA CATEGORÍA POR DEFECTO
       cuando hay una pregunta_anterior y la pregunta_actual no se sostiene sola.
       Señales fuertes de follow-up:
         - Referencias sin antecedente propio: "eso", "esa", "ahí", "lo anterior",
           "ese parámetro", "esa tabla", "el mismo".
         - Continuidad: "y si no funciona", "y después", "y entonces", "qué más",
           "seguí", "otra opción", "algo más".
         - Preguntas elípticas que dan por dado el tema: "¿y el borne?", "¿cuánto?",
           "¿por qué?", "¿y en modo SRC?".
       response: <la pregunta_actual REESCRITA como pregunta autónoma y completa,
                  incorporando el tema de la pregunta_anterior, en español. No expliques
                  nada, devolvé solo la pregunta reescrita.>

    4. "new" — introduce un tema distinto y se entiende por sí sola sin la
       pregunta_anterior. Si dudás entre "new" y "follow-up" y hay pregunta_anterior,
       elegí "follow-up": reescribirla de más es inofensivo, no reescribirla rompe la
       búsqueda.
       response: <las palabras clave técnicas de la pregunta_actual>

    Devolvé SOLO un objeto JSON válido, sin texto alrededor:
    {{
        "question_type": "generic" | "invalid" | "follow-up" | "new",
        "response": "..."
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
