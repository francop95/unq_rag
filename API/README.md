# Chatbot API

This api is used to find the context and there by get the response from openai given a query from the user.

Input json:
payload = {
         'query': query,
         'conv_history' : conv_history
         'context_type': "redisvss",
         'use_pandasai': True,
         'use_rag': False,
         'use_openai': False,
         'use_gpt': False,
         'use_cache': True,
         'use_followup': True
    }
eg:

{
    "query" : "How much does an employer contribute to medical insurance in Korea?",
    "use_openai" : true,
    'context_type': "redisvss",
    'use_pandasai': True,
    'use_rag': True,
    'use_openai': False,
    'use_gpt': True,
    'conv_history': str
}

## Logical flow

1. **Initialize:**
   - Read config values from the config reader.
   - Initialize data objects, including dataframes, a Pandas AI instance, an embedding model, an empty context dataframe, and an empty list of context dataframes.
   - data objects
      - dataframes (read from blob)
      - pandas ai instance
      - embedding model
      - context dataframes []
         -- to be read based on query similarity
      - context dataframe - None

2. **Handle Request:**
   - Receive a request, including the query, options for PAI, GPT, RAG, OpenAI, and context type.
   - Process the request parameters.
      - payload = {
               'query': query,
               'context_type': "redisvss",
               'use_pandasai': True,
               'use_rag': True,
               'use_openai': False,
               'use_gpt': True,
               'debug_mode': True,
         }

3. **Handle Flask Request:**
   - Read config values from the config reader.
   - Read structured data using a structured data reader.
      - structured data reader
         - data["dataframe"]
         - data["sdr_file_names"]
   - Initialize the Pandas AI model and the embedding model.
   - Create an empty context dataframe and an empty list of context dataframes.

4. **Get Context:**
   - Check if the context is available in Redis using a Redis connector.
   - If Redis is enabled, connect to it and retrieve the context dataframe.
   - If Redis is not enabled, set the context dataframe as an empty dataframe and set the context for GPT as False.
   - Expected context dataframe
      - ID, PID, File Name, Page Number, Text, Similarity Score
   - Append the context file names to the list of all file names.
   - Prepare the context mapping data with the fields: ID, PID, File Name, Page Number, Text, Similarity Score.
   - Parameters updated
         data["context_dataframe"] = a single dataframe with top k similar documents
         data["context_dataframes"] = [a list of context dataframes to be passed to Pandas AI]
         data["context_filenames"] = [list of all files in context dataframes]
         data["context_found"] = if redisvss or other context enabled then gpt can answer based on context

   4.1. ***Check Context Availability:***
      - If no context is available (e.g., Redis is not enabled)
         -- context dataframe as None
         -- context dataframes as an empty list
         -- context filenames as an empty list
         -- context for GPT as False

5. **Language Models:**
   - Initialize the Pandas AI and embedding model objects.
   - Get the embedding with backoff.


6. **QNA:**
   - Load the Pandas AI model.
   - Load the GPT model.
   - Process the QNA logic.
     - Set the PAI response as None.
     - Set the GPT response as None.
     - Set the RAG response as None.
     - Set the GPT answer found flag as False.
     - Set the PAI answer found flag as False.
     - Set the RAG answer found flag as False.
     - If a PAI answer is found, set the PAI answer found flag to True.
     - If a GPT answer is found, set the GPT answer found flag to True.
     - If a RAG answer is found, set the RAG answer found flag to True.

7. **RAG:**
   - Get the RAG answer.
     - Set the RAG response as the better answer.
     - Set the RAG answer found flag as True.
   - Determine the best answer among the available answers.

8. **Results:**
   - Return the answer.
Certainly! Here's the algorithmic flow based on the provided code snippet:

   8.1. ***Check if RAG Answer Found:***
      - If `data["rag_ans_found"]` is True and `data["rag_response"]` exists:
      - Log the message "[Results] 1. Returning RAG answer!".
      - Log `data["rag_response"]`.
      - Return `data["rag_response"]`.

   8.2. ***Check if GPT Answer Found and Context for GPT Enabled:***
      - If both `data["gpt_ans_found"]` and `data["context_found"]` are True and `data["pai_ans_found"]` is False:
      - If `data["gpt_response"]` exists:
         - Log the message "[Results] 2. No RAG & PAI, returning GPT answer!".
         - Log `data["gpt_response"]`.
         - Return `data["gpt_response"]`.

   8.3. ***Check if PAI Answer Found:***
      - If `data["pai_ans_found"]` is True and `data["gpt_ans_found"]` and `data["context_found"]` are False:
      - If `data["pai_response"]` exists:
         - Log the message "[Results] 3. No RAG & GPT ans, return PAI answer!".
         - Log `data["pai_response"]`.
         - Return `data["pai_response"]`.

   8.4. ***Check if Clubbed Results Available:***
      - If `clubbed_results` exist:
      - Log the message "[Results] 4. No RAG, GPT, PAI, return clubbed results!".
      - Log `clubbed_results`.
      - Return `clubbed_results`.

   8.5. ***No Answer Found - Return Blank Response:***
      - Log the message "[Results] 5. No RAG, GPT, PAI, return blank response!".
      - Return `self.empty_response`.

   This algorithmic flow captures the logic for returning the appropriate response based on the conditions mentioned in the code snippet. You can integrate this flow into your existing code to handle the response generation process accordingly.

This algorithmic flow outlines the high-level steps involved in handling a request, retrieving context data, processing language models (PAI, GPT, RAG), and providing the results. You can further expand and customize these steps according to your specific requirements.

## Output json:

{"Results": [{'answer': 'In 2008, wellbore 15/9-F-12 was actively producing for a total of 7,213.21 hours.', 'file_name': 'Volve_Monthly_Production_Data.xlsx', 'page': 1, 'similarity_score': 100.0, 'context': 'In 2008, wellbore 15/9-F-12 was on stream for 7213.205009999999 hours.\nFile Name: Volve_Monthly_Production_Data.xlsx\nPage Number: 1\nSimilarity Score: 100.0', 'ans_type': 'pai'}]}

## Data dictionary:

```json
[{
   "keys": [
    "blob_connection_string",
    "blob_container_name",
    "blob_folder_name",
    "pai_ans_type",
    "gpt_ans_type",
    "rag_ans_type",
    "is_redis_enabled",
    "redis_host",
    "redis_port",
    "redis_password",
    "redis_index_name",
    "text_column",
    "filename_column",
    "similarity_column",
    "page_number_column",
    "azure_oai_model",
    "azure_oai_model_3",
    "azure_oai_api_key",
    "azure_oai_base",
    "azure_oai_api_version",
    "azure_oai_embedding_model",
    "pai_azure_oai_model",
    "pai_azure_oai_api_key",
    "pai_azure_oai_base",
    "pai_azure_oai_api_version",
    "pai_azure_oai_embedding_model",
    "pandas_ai_enabled",
    "pandas_ai_llm_type",
    "pandas_ai_cache",
    "pandas_ai_context_limit",
    "pandas_ai_context_in_response",
    "pai_model_instruction",
    "default_filename",
    "default_no_response",
    "is_rag_enabled",
    "is_gpt_enabled",
    "gpt_no_answer_str",
    "gpt_qna_prompt",
    "gpt_sys_msg_content",
    "gpt_generic_msg_content",
    "llm_type",
    "context_type",
    "gpt_top_n_contexts",
    "redis_top_n_contexts",
    "gpt_rag_best_of_4",
    "gpt_rag_best_of_3",
    "gpt_rag_best_of_2",
    "llm_rag_system_msg",
    "rag_llm",
    "dataframes",
    "sdr_file_names",
    "all_file_names",
    "pai_instance",
    "embedding_model",
    "context_dataframe",
    "context_dataframes",
    "query",
    "use_openai",
    "context_filenames",
    "context_found",
    "pai_response",
    "gpt_response",
    "rag_response",
    "gpt_ans_found",
    "pai_ans_found",
    "rag_ans_found",
    "pai_raw_response",
    "pai_last_error",
    "pai_code_output",
    "pai_last_code",
    "rag_raw_response",
    "all_results",
   ]
}]


## Sample data
[
    {
        "blob_connection_string": "DefaultEndpointsProtocol=https;AccountName=;AccountKey=++++==;EndpointSuffix=core.windows.net",
        "blob_container_name": "volvecontainer",
        "blob_folder_name": "volve",
        "pai_ans_type": "pai",
        "gpt_ans_type": "gpt",
        "rag_ans_type": "rag",
        "is_redis_enabled": true,
        "redis_host": ".eastus.redisenterprise.cache.azure.net",
        "redis_port": "30000",
        "redis_password": "--",
        "redis_index_name": "VOLVE_TEST_chunk_index",
        "filename_column": "File Name",
        "similarity_column": "Similarity Score",
        "azure_oai_model": "gpo-test-01",
        "azure_oai_model_3": "gpt-35-turbo",
        "azure_oai_api_key": "--",
        "azure_oai_base": "https://.openai.azure.com/",
        "azure_oai_api_version": "2023-03-15-preview",
        "azure_oai_embedding_model": "text-embedding-ada-002",
        "pai_azure_oai_model": "gpo-test-01",
        "pai_azure_oai_api_key": "--",
        "pai_azure_oai_base": "https://.openai.azure.com/",
        "pai_azure_oai_api_version": "2023-03-15-preview",
        "pai_azure_oai_embedding_model": "text-embedding-ada-002",
        "pandas_ai_enabled": true,
        "pandas_ai_llm_type": "pandasai-azure",
        "pandas_ai_cache": true,
        "pandas_ai_context_limit": 5000,
        "pandas_ai_context_in_response": True,
        "pai_model_instruction": "Answer the following question only based on the data provided. Do not use outside information to answer.\n    \n    Question: {}\n    \n    Provide the answer along with corresponding filename, page number, and similarity score as well.\n\n    ",
        "default_filename": "Site Summary - VolveF.pdf",
        "default_no_response": "Information is either 0 or blank or could not be found.",
        "is_rag_enabled": true,
        "is_gpt_enabled": true,
        "gpt_no_answer_str": "I dont know.",
        "gpt_qna_prompt": "Answer the given question based on the following instructions:",
        "gpt_sys_msg_content": "You are an expert assistant on Tax, Payroll, Company Registration, Labour Laws, Company Policies. And you can help people find information pertaining to your expertise.",
        "gpt_generic_msg_content": "How may I help you with Payroll related queries?",
        "llm_type": "azure",
        "context_type": "redisvss",
        "gpt_top_n_contexts": 3,
        "redis_top_n_contexts": 10,
        "gpt_rag_best_of_4": "",
        "gpt_rag_best_of_3": "",
        "gpt_rag_best_of_2": "",
        "llm_rag_system_msg": "You are an AI assistant that helps people find information.",
        "rag_llm": "azure",
        "dataframes": [],
        "sdr_file_names": [
            "Volve_Daily_Production_Data.xlsx",
            "Volve_Monthly_Production_Data.xlsx",
        ],
        "all_file_names": [
            "Volve_Daily_Production_Data.xlsx",
            "Volve_Monthly_Production_Data.xlsx",
            "Site Summary - VolveF.pdf",
        ],
        "pai_instance": "<pandasai.PandasAI object at ___>",
        "embedding_model": "<models.LanguageModels.LanguageModel object at ___>",
        "context_dataframe": "pd.DataFrame()",
        "query": "What is the TVD Reference for well F-7?",
        "use_openai": false,
        "debug_mode": true,
        "context_filenames": ["Site Summary - VolveF.pdf"],
        "context_found": true,
        "pai_response": [
            {
                "answer": "The TVD reference for well F-7 is actually the Mean Sea Level.",
                "file_name": "Site Summary - VolveF.pdf",
                "page": 13,
                "similarity_score": 100.0,
                "context": "TVD Reference for well F-7: Mean Sea Level (System)\nFile Name: Site Summary - VolveF.pdf\nPage Number: 13\nSimilarity Score: 100.0",
                "ans_type": "pai",
            }
        ],
        "gpt_response": [
            {
                "answer": "I dont know.",
                "page": 13,
                "similarity_score": 82.0,
                "file_name": "Site Summary - VolveF.pdf",
                "ans_type": "gpt",
            },
            {
                "answer": "I dont know.",
                "page": 8,
                "similarity_score": 81.0,
                "file_name": "Site Summary - VolveF.pdf",
                "ans_type": "gpt",
            },
            {
                "answer": "I dont know.",
                "page": 13,
                "similarity_score": 80.9,
                "file_name": "Site Summary - VolveF.pdf",
                "ans_type": "gpt",
            },
        ],
        "rag_response": null,
        "gpt_ans_found": false,
        "pai_ans_found": true,
        "rag_ans_found": false,
        "pai_raw_response": "TVD Reference for well F-7: Mean Sea Level (System)\nFile Name: Site Summary - VolveF.pdf\nPage Number: 13\nSimilarity Score: 100.0",
        "pai_last_error": "None",
        "pai_code_output": "TVD Reference for well F-7: Mean Sea Level (System)\nFile Name: Site Summary - VolveF.pdf\nPage Number: 13\nSimilarity Score: 100.0",
        "pai_last_code": "None",
        "all_results": [
            {
                "answer": "I dont know.",
                "page": 13,
                "similarity_score": 82.0,
                "file_name": "Site Summary - VolveF.pdf",
                "ans_type": "gpt",
            },
            {
                "answer": "I dont know.",
                "page": 8,
                "similarity_score": 81.0,
                "file_name": "Site Summary - VolveF.pdf",
                "ans_type": "gpt",
            },
            {
                "answer": "I dont know.",
                "page": 13,
                "similarity_score": 80.9,
                "file_name": "Site Summary - VolveF.pdf",
                "ans_type": "gpt",
            },
            {
                "answer": "The TVD reference for well F-7 is actually the Mean Sea Level.",
                "file_name": "Site Summary - VolveF.pdf",
                "page": 13,
                "similarity_score": 100.0,
                "context": "TVD Reference for well F-7: Mean Sea Level (System)\nFile Name: Site Summary - VolveF.pdf\nPage Number: 13\nSimilarity Score: 100.0",
                "ans_type": "pai",
            },
        ],
    }
]

---

## Cambios de esta iteración (medidos)

Todo lo de abajo se validó con `Ingestion/eval/run_eval.py` (54 consultas parafraseadas
en voz de técnico + 5 fuera de tema). El baseline y el estado final dan **idéntico**:
recall@10 88.9%, MRR 0.754, gate 5/5 — o sea, ninguna de estas mejoras costó calidad.

### Retrieval: lo que se apagó, y por qué

| Etapa | Estado | Medición |
|---|---|---|
| Reranking cross-encoder | **off** | recall@10 61.1% vs 88.9%; MRR 0.193 vs 0.754; 21 de 54 consultas se quedan sin respuesta |
| BM25 | **off** | apagarlo da resultados idénticos dígito por dígito; 0 de 8 consultas de código puro mejoran |
| Retrieval visual (CLIP) | **off** | ídem; además inerte por cableado (ver abajo) |
| Ordenar por fusión RRF | **off** | recall@10 87.0% vs 88.9% ordenando por similitud densa |
| Selector LLM de secciones | **off** | fallaba en el 100% de las consultas, 2 llamadas al LLM por consulta para un no-op |
| Umbral de relevancia | **0.50** | 0.35 → gate 3/5; 0.50 → 5/5 con el mismo recall; 0.60 → recall 85.2% |

El patrón es consistente: el índice multi-vector con ~2900 vectores de preguntas
sintéticas en lenguaje de usuario es tan fuerte que **toda etapa de fusión o reordenado
o no hace nada o empeora**. Los flags quedan para poder re-medir con otro corpus.

Por qué BM25 y CLIP eran inertes *por construcción*, más allá de no aportar:
1. El gate de relevancia filtraba por `dense_similarity`, y un candidato que solo
   encontró BM25/CLIP no tiene esa clave → default 0.0 → nunca pasaba. Medido con
   `"22B-D010N104"`: 0 candidatos solo-BM25 y 0 solo-CLIP sobrevivían.
2. El orden final se decidía por `similarity` (la similitud densa), descartando el
   resultado del RRF que se acababa de calcular.

### Bugs que estaban tapados

Cuatro defectos que no se veían porque su camino estaba apagado o porque fallaban en
silencio:

- **Todos los prompts llegaban al LLM escapados como HTML.** `sanitize_data` corría
  sobre el dict de configuración entero, así que la especificación de formato JSON
  llegaba como `&quot;question_type&quot;: &lt;question_type&gt;`. El escape XSS es para
  la entrada del usuario, que ya se sanitiza por separado.
- **`KeyError: 'azure_oai_model3'` en toda clasificación de intención exitosa.**
  Clasificaba bien y una línea después la excepción borraba el resultado, así que el
  follow-up nunca reescribía la pregunta. Parecía "clasifica mal".
- **`KeyError: 'gemini_model'`** en `QueryIntent`, con el modelo fijo a un proveedor que
  este proyecto no usa.
- **Los dos planos PDF se subían a OpenAI en cada consulta.** `_file_id_cache` era un
  atributo de instancia y `ModelCompletion` se crea por request. Latencia de una
  consulta con planos: **~15s → 7.7s**.

### Funcionalidad nueva

- **Caché de respuestas** (`qnas/ResponseCache.py`), por coincidencia EXACTA del texto
  normalizado. 13.0s → 0.0016s en una repetición. Es exacta y no semántica por
  medición: las bandas de similitud se solapan en este dominio ("corriente de ENTRADA"
  vs "de SALIDA" da 0.916; dos redacciones de la misma pregunta dan 0.872 y 0.929), así
  que no hay umbral que las separe y un caché semántico devolvería el borne equivocado.
  Reemplaza a `CachedQna`, que leía de Azure Search y no tenía ruta de escritura.
- **Conversación / follow-up activado.** No se le pasa el historial al LLM que responde:
  se clasifica la intención y se REESCRIBE el follow-up como pregunta autónoma antes del
  retrieval, que es lo único que funciona con búsqueda densa. El caché se saltea cuando
  llega historial: `"¿y si eso no funciona?"` es el mismo texto en conversaciones
  distintas.
- **Filtro de boilerplate en el context expander.** 40 chunks (encabezados, pies,
  bloques de contacto) dejan de inyectarse como contexto; 0 falsos positivos sobre 708.
- **CORS cerrado y token opcional.** El default pasó de `"*"` a los orígenes de Vite, y
  se refleja el `Origin` solo si está en la lista. Con `API_TOKEN` definido,
  `/get_response` exige `Authorization: Bearer`. Sin la variable, sigue abierta para uso
  local.

### Estado del índice y la media: van juntos

`sources[].media[].media_path` apunta a archivos bajo `Ingestion/data/media/`, y esos
nombres llevan un hash del contenido. Una re-ingesta los regenera con nombres distintos,
así que **el índice y `data/media/` son un solo estado y hay que moverlos juntos**.

Restaurar un índice con la media de otra corrida deja las referencias colgadas: pasó, y
quedaron 246 de 264 rotas. El síntoma en el frontend son recuadros vacíos en la galería —
y lo peor es que el eval no lo detecta, porque mide recall de páginas y no abre los
archivos. Marcaba 90.7% sobre un sistema que no podía mostrar casi ninguna imagen.

Chequeo después de restaurar o re-ingestar:

```bash
cd Ingestion && python -c "
import chromadb, os
col = chromadb.PersistentClient(path='data/chroma_index').get_collection('multimodal_documents')
media = {m.get('media_path') for m in col.get(include=['metadatas'])['metadatas'] if m.get('media_path')}
faltan = [p for p in media if not os.path.exists(os.path.join('data', p))]
print(f'media referenciada: {len(media)} | falta en disco: {len(faltan)}')"
```

### Cuota de OpenAI agotada

La API no reintenta un 429 de facturación como si fuera throttling. `Ingestion` tampoco
(ver `llm_json.is_quota_exhausted`): el SDK lanza `RateLimitError` para todo HTTP 429, y sin
separarlos una cuenta sin crédito se reintenta con backoff exponencial durante horas. Al
diagnosticar rate limits, chequear primero `grep insufficient_quota` en el log.

### Suite de regresión

```bash
./run_tests.sh        # desde la raíz del repo: 26 tests de retrieval + 37 de ingesta, ~4s
```

Cada test corresponde a un bug real que se encontró y arregló. Si alguno se rompe, ese bug
volvió.
