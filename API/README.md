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

