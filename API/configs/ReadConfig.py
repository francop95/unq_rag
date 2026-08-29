from configs.Configuration import Configuration


class ReadConfig:
    """
    A class to read configuration values from a file.

    Example:
        config = ReadConfig('config.ini')
        value = config.get_config_value('section_name', 'key_name')
    """

    def __init__(self, config_file_path=None):
        """
        Initializes the ReadConfig instance with the specified config file path.

        Args:
            config_file_path (str): The path to the configuration file.
        """
        self.config_file_path = config_file_path

    def getConfigSettings(
        self,
    ) -> dict:
        """
        Retrieves the value for the specified section and key from the configuration file.

        Args:
            None

        Returns:
            dict: The dictionary with key-value pair of settings.

        Raises:
            ValueError: If the specified section or key is not found in the configuration file.
        """
        try:
            # Initializing Configuration object
            config = Configuration()

            # diagrams paths
            electric_diagram_path=config.get("electric_diagram_path")
            tben_diagram_path=config.get("tben_diagram_path")
            # get answer types
            gpt_ans_type = config.get("GPT_ANSWER_TYPE")
            cache_ans_type = config.get("CACHE_ANSWER_TYPE")

            # Ans found success flags
            gpt_ans_found = config.get("GPT_AND_FOUND")
            generic_ans_found = config.get("GENERIC_ANS_FOUND")
            invalid_question_found = config.get("INVALID_QUESTION_FOUND")

            top_n_contexts = int(config.get("TOP_N"))

            # get config values
            text_column = config.get("TEXT_COLUMN")
            filename_column = config.get("FILENAME_COLUMN")
            similarity_column = config.get("SIMILARITY_COLUMN")
            page_number_column = config.get("PAGE_NUMBER_COLUMN")
            question_column = config.get("QUESTION_COLUMN")
            answer_column = config.get("ANSWER_COLUMN")
            context_type = config.get("CONTEXT_TYPE")
            context_text_type = config.get("CONTEXT_TEXT_TYPE")
            llm_type = config.get("LLMODEL_TYPE")

            # gpt enabled
            is_gpt_enabled = config.get("AppSettings__GPTEnabled")
            gpt_top_n_contexts = int(config.get("GPT_TOP_N"))

            # GPT prompts
            gpt_no_answer_str = config.get("GPT_NO_ANSWER_STRING")
            gpt_sys_msg_content = config.get("GPT_SYSTEM_MESSAGE_CONTENT")

            # get default no response
            default_no_response = config.get("DEFAULT_NO_RESPONSE")

            # Query Intent prompt
            query_intent_sys_msg = config.get("QUERY_INTENT_SYSTEM_MESSAGE")
            query_intent_model_type = config.get("QUERY_INTENT_LLM_TYPE")
            query_intent_max_retries = config.get("RETRIEVER_MAX_RETRIES")

            new_query_intent_prompt = config.get("NEW_QUERY_INTENT_PROMPT")

            # Followup
            is_followup = config.get("AppSettings__FollowUpRequired")
            query_intent_categories = config.get("QUERY_INTENT_CATEGORIES")
            prev_conv_threshold = config.get("PREV_CONV_THRESHOLD")

            # Cache
            is_cache_enabled = config.get("AppSettings__CacheEnabled")
            response_cache_path = config.get("RESPONSE_CACHE_PATH")
            response_cache_max_entries = config.get("RESPONSE_CACHE_MAX_ENTRIES")
            response_cache_ttl_seconds = config.get("RESPONSE_CACHE_TTL_SECONDS")
            min_cache_similarity = config.get("AppSettings__CacheSimilarityThreshold")
            cache_top_n = config.get("AppSettings__CacheTopN")
            cache_guid_column = config.get("CACHE_GUID_COLUMN")
            cache_filename_column = config.get("CACHE_FILENAME_COLUMN")
            cache_similarity_column = config.get("CACHE_SIMILARITY_COLUMN")
            cache_page_number_column = config.get("CACHE_PAGE_NUMBER_COLUMN")
            cache_question_column = config.get("CACHE_QUESTION_COLUMN")
            cache_answer_column = config.get("CACHE_ANSWER_COLUMN")

            # Greeting flag
            is_greeting_enabled = config.get("GREETING_ENABLED")

            # Project
            project = config.get("AppSettings__Project")

            # Model Completion
            completion_success = config.get("MODEL_COMPLETION_SUCCESS")
            completion_failure = config.get("MODEL_COMPLETION_FAILURE")

            # get model input params for qna
            #pandas_ai_model_instruction = config.get("PAI_MODEL_INSTRUCTION")
            default_filename = config.get("DEFAULT_FILENAME")

            # response formating
            response_formating_required = config.get("RESPONSE_FORMATING_REQUIRED")

            # custom retriever
            retriever_prompt = config.get("RETRIEVER_PROMPT")
            retriever_sys_msg = config.get("RETRIEVER_SYSTEM_MESSAGE")
            retriever_model_type = config.get("RETRIEVER_LLM_TYPE")
            retriever_context_limit = config.get("Retriever_Context_Limit")
            retriever_gpt_qna_prompt = config.get("RETRIEVER_GPT_QNA_PROMPT")
            is_retriever = config.get("Retriever_enabled")
            use_llm_section_selector = config.get("LLM_SECTION_SELECTOR_ENABLED")
            retriever_max_retries = config.get("RETRIEVER_MAX_RETRIES")

            #RAG
            retriever_qna_max_retries = config.get("RETRIEVER_QNA_MAX_RETRIES")

            # max tokens
            retriever_max_tokens = config.get("RETRIEVER_MAX_TOKENS")
            retriever_qna_max_tokens = config.get("RETRIEVER_QNA_MAX_TOKENS")
            query_intent_max_tokens = config.get("QUERY_INTENT_MAX_TOKENS")

            # url formatting flag
            url_formatting_enabled = config.get("URL_FORMATTING_ENABLED")

            # data dict export 
            data_dict_export_enabled = config.get("DATA_EXPORT_ENABLED")

            #OpenAI Models
            openai_model = config.get("OPENAI_MODEL")
            openai_emb_model = config.get("OPENAI_EMB_MODEL")
            openai_keys = config.get("OPENAI_KEY")

            # Chroma params
            is_chroma_enabled = config.get("AppSettings__ChromaEnabled")
            chroma_index_name = config.get("AppSettings__ChromaIndex")
            chroma_top_n_contexts = int(config.get("CHROMA_TOP_N"))
            chroma_local_path = config.get("chroma_local_path")
            min_context_similarity_score = float(config.get("MIN_CONTEXT_SIMILARITY_SCORE"))

            # Retrieval avanzado
            use_visual_retrieval = bool(config.get("USE_VISUAL_RETRIEVAL"))
            visual_index_name = config.get("VISUAL_INDEX_NAME")
            clip_model = config.get("CLIP_MODEL")
            visual_top_k = int(config.get("VISUAL_TOP_K"))
            use_bm25 = bool(config.get("USE_BM25", False))
            fusion_admits_sparse = bool(config.get("FUSION_ADMITS_SPARSE", False))
            fusion_decides_order = bool(config.get("FUSION_DECIDES_ORDER", False))
            bm25_top_k = int(config.get("BM25_TOP_K"))
            use_reranking = bool(config.get("USE_RERANKING"))
            reranker_model = config.get("RERANKER_MODEL")
            rerank_candidates_top_k = int(config.get("RERANK_CANDIDATES_TOP_K"))
            use_context_expansion = bool(config.get("USE_CONTEXT_EXPANSION"))
            chunks_data_path = config.get("CHUNKS_DATA_PATH")
            electric_diagram_related_files = config.get("ELECTRIC_DIAGRAM_RELATED_FILES")

            #Batch Answer
            is_batch_response_enabled = config.get("AppSettings__Batchanswer")

        except Exception as e:
            raise Exception("Error occurred while retrieving config values: " + str(e))
        finally:
            # return data
            data = {
                "electric_diagram_path":electric_diagram_path,
                "tben_diagram_path":tben_diagram_path,
                "gpt_ans_type": gpt_ans_type,
                "cache_ans_type": cache_ans_type,
                "text_column": text_column,
                "filename_column": filename_column,
                "similarity_column": similarity_column,
                "page_number_column": page_number_column,
                "question_column": question_column,
                "answer_column": answer_column,
                "default_filename": default_filename,
                "default_no_response": default_no_response,
                "is_gpt_enabled": is_gpt_enabled,
                "gpt_no_answer_str": gpt_no_answer_str,
                "gpt_sys_msg_content": gpt_sys_msg_content,
                "llm_type": llm_type,
                "context_type": context_type,
                "context_text_type": context_text_type,
                "gpt_top_n_contexts": gpt_top_n_contexts,
                "query_intent_sys_msg": query_intent_sys_msg,
                "query_intent_model_type": query_intent_model_type,
                "query_intent_categories": query_intent_categories,
                "query_intent_max_retries": query_intent_max_retries,
                "prev_conv_threshold": prev_conv_threshold,
                "is_followup": is_followup,
                "completion_success": completion_success,
                "completion_failure": completion_failure,
                "is_cache_enabled": is_cache_enabled,
                "response_cache_path": response_cache_path,
                "response_cache_max_entries": response_cache_max_entries,
                "response_cache_ttl_seconds": response_cache_ttl_seconds,
                "min_cache_similarity": min_cache_similarity,
                "cache_top_n": cache_top_n,
                "cache_guid_column": cache_guid_column,
                "cache_filename_column": cache_filename_column,
                "cache_similarity_column": cache_similarity_column,
                "cache_page_number_column": cache_page_number_column,
                "cache_question_column": cache_question_column,
                "cache_answer_column": cache_answer_column,
                "project": project,
                "is_greeting_enabled": is_greeting_enabled,
                "gpt_ans_found": gpt_ans_found,
                "generic_ans_found": generic_ans_found,
                "response_formating_required": response_formating_required,
                "retriever_prompt": retriever_prompt,
                "retriever_sys_msg": retriever_sys_msg,
                "retriever_model_type": retriever_model_type,
                "retriever_context_limit": retriever_context_limit,
                "retriever_gpt_qna_prompt": retriever_gpt_qna_prompt,
                "is_retriever": is_retriever,
                "use_llm_section_selector": use_llm_section_selector,
                "retriever_max_retries": retriever_max_retries,
                "retriever_qna_max_retries": retriever_qna_max_retries,
                "url_formatting_enabled": url_formatting_enabled,
                "top_n_contexts": top_n_contexts,
                "new_query_intent_prompt": new_query_intent_prompt,
                "data_dict_export_enabled": data_dict_export_enabled,
                "invalid_question_found": invalid_question_found,
                "retriever_max_tokens": retriever_max_tokens,
                "retriever_qna_max_tokens": retriever_qna_max_tokens,
                "query_intent_max_tokens": query_intent_max_tokens,
                "openai_model" : openai_model,
                "openai_emb_model" : openai_emb_model,
                "openai_keys" : openai_keys,
                "is_chroma_enabled": is_chroma_enabled,
                "chroma_index_name": chroma_index_name,
                "chroma_top_n_contexts" : chroma_top_n_contexts,
                "chroma_path" : chroma_local_path,
                "min_context_similarity_score": min_context_similarity_score,
                "use_visual_retrieval": use_visual_retrieval,
                "visual_index_name": visual_index_name,
                "clip_model": clip_model,
                "visual_top_k": visual_top_k,
                "use_bm25": use_bm25,
                "fusion_admits_sparse": fusion_admits_sparse,
                "fusion_decides_order": fusion_decides_order,
                "bm25_top_k": bm25_top_k,
                "use_reranking": use_reranking,
                "reranker_model": reranker_model,
                "rerank_candidates_top_k": rerank_candidates_top_k,
                "use_context_expansion": use_context_expansion,
                "chunks_data_path": chunks_data_path,
                "electric_diagram_related_files": electric_diagram_related_files,
                "is_batch_response_enabled" : is_batch_response_enabled,
            }

        return data
