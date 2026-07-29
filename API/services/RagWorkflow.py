# import necessary libraries and functions
import logging
from results.Results import ResultProcessor
from qnas.QuestionAnswer import QuestionAnswer
from models.QueryIntent import QueryIntent
from contexts.ContextsMap import GetContext
from qnas.CachedQna import GetCacheQna
from utils.FilterResponse import filter_response, url_formatting
from utils.FormatResponse import format_response
import time
import csv
from pathlib import Path

# logging
logger = logging.getLogger('app.RagWorkflow')

class RagWorkflow:
    def __init__(self, data) -> None:
        self.query_id = data["query_id"]
        self.perf_csv_path = Path("perf_timings.csv")

    def _log_timings(self, data, timings: dict, stage: str) -> None:
        """
        Guarda en CSV los tiempos de cada etapa del workflow.
        """
        csv_path = self.perf_csv_path

        fieldnames = [
            "stage",
            "query_id",
            "project",
            "is_cache_enabled",
            "is_greeting_enabled",
            "is_followup",
            "total_time",
            "cache_time",
            "intent_time",
            "greeting_time",
            "followup_time",
            "context_time",
            "qna_time",
            "process_time",
        ]

        # Asegurar cabecera
        file_exists = csv_path.exists()
        with csv_path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()

            row = {
                "stage": stage,
                "query_id": data.get("query_id", getattr(self, "query_id", "")),
                "project": data.get("project", ""),
                "is_cache_enabled": data.get("is_cache_enabled", ""),
                "is_greeting_enabled": data.get("is_greeting_enabled", ""),
                "is_followup": data.get("is_followup", ""),
                "total_time": timings.get("total_time", 0.0),
                "cache_time": timings.get("cache_time", 0.0),
                "intent_time": timings.get("intent_time", 0.0),
                "greeting_time": timings.get("greeting_time", 0.0),
                "followup_time": timings.get("followup_time", 0.0),
                "context_time": timings.get("context_time", 0.0),
                "qna_time": timings.get("qna_time", 0.0),
                "process_time": timings.get("process_time", 0.0),
            }
            writer.writerow(row)

    def trigger_workflow(self, data):

        # --- inicializar timers ---
        t0 = time.perf_counter()
        timings = {
            "cache_time": 0.0,
            "intent_time": 0.0,
            "greeting_time": 0.0,
            "followup_time": 0.0,
            "context_time": 0.0,
            "qna_time": 0.0,
            "process_time": 0.0,
        }

        # if caching is enabled
        if data["is_cache_enabled"]:
            t_cache = time.perf_counter()
            logger.info(f"[{self.query_id}] [RagWorkflow] Checking Cached Qna")
            cache_obj = GetCacheQna(data)
            data = cache_obj.get_cache_qna(data)
            timings["cache_time"] = time.perf_counter() - t_cache

            if data["cache_found"]:
                logger.info(f"[{self.query_id}] [RagWorkflow] Cached Qna Found")
                t_proc = time.perf_counter()
                final_response_filtered = self.process_results(data)
                timings["process_time"] = time.perf_counter() - t_proc

                timings["total_time"] = time.perf_counter() - t0
                self._log_timings(data, timings, stage="cache_hit")

                return final_response_filtered

        # Call QueryIntent module to find the intent of query
        query_intent = None
        if data["is_followup"] or data["is_greeting_enabled"]:
            t_intent = time.perf_counter()
            logger.info(f"[{self.query_id}] [RagWorkflow] Getting Query Intent")
            query_intent = QueryIntent(data)
            data = query_intent.find_query_intent(data)
            data = query_intent.check_for_invalid_query(data)
            timings["intent_time"] = time.perf_counter() - t_intent

            if data["invalid_question_found"]:
                logger.info(f"[{self.query_id}][RagWorkflow] Generic or Invalid Query Found")
                t_proc = time.perf_counter()
                final_response_filtered = self.process_results(data)
                timings["process_time"] = time.perf_counter() - t_proc

                timings["total_time"] = time.perf_counter() - t0
                self._log_timings(data, timings, stage="invalid_query")

                return final_response_filtered

        # Generic or Greeting
        logger.info(f"[{self.query_id}][RagWorkflow] Generic or Greeting check")
        if data["is_greeting_enabled"] and query_intent is not None:
            t_greet = time.perf_counter()
            data = query_intent.check_for_generic_query(data)
            timings["greeting_time"] = time.perf_counter() - t_greet

            if data["generic_ans_found"]:
                logger.info(f"[{self.query_id}] [RagWorkflow] Generic or Greeting response Found")
                t_proc = time.perf_counter()
                final_response_filtered = self.process_results(data)
                timings["process_time"] = time.perf_counter() - t_proc

                timings["total_time"] = time.perf_counter() - t0
                self._log_timings(data, timings, stage="generic_answer")

                return final_response_filtered

        # Followup
        logger.info(f"[{self.query_id}] [RagWorkflow] Follow-up check")
        if data["is_followup"] and query_intent is not None:
            t_follow = time.perf_counter()
            data = query_intent.check_followup(data)
            timings["followup_time"] = time.perf_counter() - t_follow

        # call context connector for context dataframe
        logger.info(f"[{self.query_id}] [RagWorkflow] Getting context")
        t_context = time.perf_counter()
        context = GetContext(data)
        # calls the chosen context finder
        data = context.get_context(data)
        timings["context_time"] = time.perf_counter() - t_context

        # get qna mechanism
        t_qna = time.perf_counter()
        qnabot = QuestionAnswer(data)
        logger.info(f"[{self.query_id}] [RagWorkflow] Question-Answer Instance loaded")

        # response from bot
        data = qnabot.answer_query(data)
        timings["qna_time"] = time.perf_counter() - t_qna
        logger.info(f"[{self.query_id}] [RagWorkflow] Response obtained from QnA!")

        t_proc = time.perf_counter()
        final_response_filtered = self.process_results(data)
        timings["process_time"] = time.perf_counter() - t_proc

        timings["total_time"] = time.perf_counter() - t0
        self._log_timings(data, timings, stage="full_flow")

        return final_response_filtered


    def process_results(self, data):
        # process model response (parse answer, filename, page num, similarity)
        processor = ResultProcessor(data)
        self.default_response = processor.default_response
        data = processor.process_answers(data)
        final_response = self.get_final_response(data)
        logger.info(f"[{self.query_id}] [RagWorkflow] Final response: " + str(final_response))
        logger.info(data["all_results"])

        final_response_filtered = filter_response(final_response, self.query_id)
        logger.info(f"[{self.query_id}] [RagWorkflow] Final response Filtered: " + str(final_response_filtered))

        if data["url_formatting_enabled"]:
            final_response_filtered = url_formatting(final_response_filtered,self.query_id)
            logger.info(f"[{self.query_id}] [RagWorkflow] URL Formatted response: " + str(final_response_filtered)) 

        if data["response_formating_required"]:
            formatted_response = format_response(final_response_filtered, data['query_id'])
            logger.info(f"[{self.query_id}] [RagWorkflow] Final Formatted response: " + str(formatted_response))
            return formatted_response
        return final_response_filtered

    def get_final_response(self, data):
        # return default response if all are empty!
        if not data["all_results"]:
            logger.info(f"[{self.query_id}] [RagWorkflow] 3. return blank response!")
            return self.default_response

        try:
            # conditionally return based on what is enabled!

            # if Cache Enabled and cache answer found
            if data["is_cache_enabled"] & data["cache_found"]:
                if data["cache_qna_response"]:
                    logger.info(f'[{self.query_id}] [RagWorkflow] 1. Returning Cache answer!\n {data["cache_qna_response"]}')
                    return data["cache_qna_response"]
                
            # If invalid query found
            if data["invalid_question_found"]:
                if data["invalid_qna_response"]:
                    logger.info(f'[{self.query_id}][RagWorkflow] 2. Returning default response invalid query!\n {data["invalid_qna_response"]}')
                    return data["invalid_qna_response"]

            # If Greeting enabled and generic or greeting response found
            if data["is_greeting_enabled"] and data["generic_ans_found"]:
                if data["generic_qna_response"]:
                    logger.info(f'[{self.query_id}] [RagWorkflow] 2. Returning Greeting or Generic answer!\n {data["generic_qna_response"]}')
                    logger.info(data["generic_qna_response"])
                    return data["generic_qna_response"]
                
            if (
                data["is_gpt_enabled"] & data["gpt_ans_found"] & data["context_found"]
            ):
                if data["gpt_response"]:
                    logger.info(f'[{self.query_id}] [RagWorkflow] 1. returning GPT answer!\n {data["gpt_response"]}')
                    return data["gpt_response"]
            
            elif data["all_results"]:
                logger.info(f'[{self.query_id}] [RagWorkflow] 2. return clubbed results!\n {data["all_results"]}')
                return data["all_results"]
            else:
                logger.info(f"[{self.query_id}] [RagWorkflow] 3. return blank response!")
                return self.default_response

        except Exception as e:
            logger.error(f"[{self.query_id}] [RagWorkflow] Error processing response: {str(e)}")
