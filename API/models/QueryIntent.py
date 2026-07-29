import re
import logging
import asyncio
import json
from typing import Dict, List, Any

from models.ModelCompletion_multimodal import ModelCompletion
from utils.utils import calculate_num_of_tokens

# logging
logger = logging.getLogger('app.QueryIntent')

class QueryIntent:
    def __init__(self, data: Dict[str, Any]):
        """
        Initializes the QueryIntent class.

        Args:
            data (Dict[str, Any]): A dictionary containing the data required for the QueryIntent.
        """
        self.query_id = data["query_id"]
        logger.info(f"[{self.query_id}] [QueryIntentNew] Completion object")
        self.model_type = data["query_intent_model_type"]
        data["max_tokens"] = data["query_intent_max_tokens"]
        self.chat_obj = None
        self.buffer_tokens = 75
        self.query_intent_prompt = data["new_query_intent_prompt"]
        self.query_intent_sys_msg = data["query_intent_sys_msg"]
        self.query_intent_categories = data["query_intent_categories"]
        self.prev_conv_threshold = data["prev_conv_threshold"]
        self.query_intent = None
        self.default_query_intent = None
        self.completion_failure = data["completion_failure"]
        self.completion_success = data["completion_success"]
        self.default_response_dict = {
            "question": data["query"],
            "answer": "I don't know.",
            "file_name": "",
            "page": 0,
            "similarity_score": 0,
            "ans_type": data["gpt_ans_type"],
        }
        self.model_selected = data["gemini_model"]
        self.retry_count = 0
        self.max_retries = data["query_intent_max_retries"]

    def get_message_prompt(self, data: Dict[str, Any]) -> List[Dict[str, str]]:
        """
        Generates the message prompt based on the data provided.

        Args:
            data (Dict[str, Any]): A dictionary containing the data required for the message prompt.

        Returns:
            List[Dict[str, str]]: A list of dictionaries representing the message prompt.
        """
        logger.info(f"[{self.query_id}] [QueryIntentNew] Inside get_message_prompt()")
        if (not data["conv_history_df"].empty):
            logger.info(f"[{self.query_id}] [QueryIntentNew] conv_history_df question : "+str(list(data["conv_history_df"]["question"])))
            prev_query_string = "\n".join(list(data["conv_history_df"]["question"])[-self.prev_conv_threshold:])
        else:
            prev_query_string = ""

        prompt = self.query_intent_prompt.format(str(data["query"]), prev_query_string)
        message_prompt = [
            {"role": "system", "content": str(self.query_intent_sys_msg)},
            {"role": "user", "content": str(prompt)}]
        logger.debug(f"[{self.query_id}] [QueryIntent] Message Prompt: {message_prompt}")

        return message_prompt

    def find_query_intent(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Finds the query intent and updates the data dictionary.

        Args:
            data (Dict[str, Any]): A dictionary containing the data required to find the query intent.

        Returns:
            Dict[str, Any]: The updated data dictionary.
        """
        logger.info(f"[{self.query_id}] [QueryIntent] Query Intent prompt completion start")
        try:
            self.message_prompt = self.get_message_prompt(data)
            token_cnt = calculate_num_of_tokens(str(self.message_prompt), data["query_id"])
            retry_flag = True

            while retry_flag:
                dynamic_params = {
                    'model': self.model_selected, "max_tokens": token_cnt + self.buffer_tokens
                    }
                self.chat_obj = ModelCompletion(data, self.model_type, dynamic_params=dynamic_params)
                query_intent, query_intent_raw_response = self.chat_obj.get_response(self.message_prompt, data)
                data["query_intent_raw_gpt_response"] = query_intent_raw_response

                data["query_intent_raw"] = query_intent
                data = self.parse_query_intent(data)
                if ((not data["query_intent"]) and self.retry_count < self.max_retries):
                    if self.retry_count == self.max_retries - 1:
                        self.model_selected = data["azure_oai_model2"]
                    logger.info(f"[{self.query_id}] [QueryIntent] Response not found, Retrying with Model: {self.model_selected}, Retry Attempt: {self.retry_count + 1}")
                    self.retry_count += 1
                else:
                    retry_flag = False
                    self.retry_count = 0
                    self.model_selected = data["azure_oai_model3"]
        except Exception as e:
            logger.error("[{}][QueryIntentNew] Error in getting query intent: {}".format(self.query_id, e))
            data["query_intent"] = None
            data["query_intent_raw_gpt_response"] = str(e)
            return data

        logger.info(f"[{self.query_id}][QueryIntent] Query Intent prompt completion and Parsing Ends.")
        return data

    def parse_query_intent(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parse the query intent and updates the data dictionary.

        Args:
            data (Dict[str, Any]): A dictionary containing the data required to find the query intent.

        Returns:
            Dict[str, Any]: The updated data dictionary.
        """
        logger.debug("[{}] [QueryIntent] Parsing query intent response in progress {}".format(self.query_id, data["query_intent_raw"]))
        data["query_intent"] = self.default_query_intent
        if (data["query_intent_raw"][0]["res_status"] == self.completion_failure):
            logger.info("[{}] [QueryIntent] Intent Identification Failure".format(self.query_id))
            return data
        try:
            query_intent_res = json.loads(data["query_intent_raw"][0]["response"],strict=False)
        except Exception as e:
            logger.error("[{}][QueryIntent] Parsing Failure, Error: {} and query_intent: {}".format(self.query_id, e, data["query_intent_raw"]))
            query_intent_res = self.extract_response(data["query_intent_raw"][0]["response"])
        try:
            if ("question_type" in query_intent_res.keys() and "response" in query_intent_res.keys()):
                self.query_intent = query_intent_res
                data["query_intent"] = self.query_intent
                logger.info("[{}] [QueryIntent] Query Intent Response: {}".format(self.query_id,self.query_intent))
            else:
                logger.info("[{}] [QueryIntent] Query intent response does not have required info".format(self.query_id))
        except Exception as e:
            logger.error("[{}][QueryIntent]Processing Parsed query intent failure, Error: {} and parsed query_intent: {}".format(self.query_id, e, query_intent_res))
            pass
        return data
    
    def extract_response(self, response):
        logger.info(f"[{self.query_id}][QueryIntent]Inside Extract response for regex parsing")
        try:
            query_intent_res = None
            res = re.search('{\n\s*"question_type"', response)
            if res:
                start = res.span()[0]
                res2 = re.search('\}', response[start:])
                if res2:
                    end = start + res2.span()[1]
                    query_intent_res = json.loads(response[start:end])
            if query_intent_res:
                logger.info(f"[{self.query_id}] [QueryIntent] Query Intent response successfully parsed")
            else:
                logger.info(f"[{self.query_id}] [QueryIntent] Could not parse Query Intent response")
        except Exception as e:
            logger.error(f"[{self.query_id}] [QueryIntent] Regex parsing failure {e}")
            query_intent_res = None
        return query_intent_res

    def check_followup(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Checks if the current query is a follow-up and updates the data dictionary.

        Args:
            data (Dict[str, Any]): A dictionary containing the data required to check for follow-up.

        Returns:
            Dict[str, Any]: The updated data dictionary.
        """
        try:
            # When query intent is None
            if not data["query_intent"]:
                logger.info(f"[{self.query_id}] [QueryIntent] Query intent is None, something went wrong with query intent Identification")
                data["updated_query"] = data["query"]
                return data
            # Case - Follow-up
            elif (data["query_intent"]["question_type"] == self.query_intent_categories[2]):
                logger.info(f"[{self.query_id}] [QueryIntent] Case - Followup")
                logger.info("[{}] [QueryIntent] Incoming Query: {}".format(self.query_id, data["query"]))

                data["actual_query"] = data["query"]
                # When response is valid
                if data["query_intent"]["response"]:
                    updated_query = str(data["query_intent"]["response"])
                    data["query"] = updated_query
                else:
                    updated_query = list(data["conv_history_df"]["question"])[0] + "\n" + data["query"]
                data["updated_query"] = updated_query
                logger.info("[{}] [QueryIntent] Actual query : {}".format(self.query_id, data["actual_query"]))
                logger.info("[{}] [QueryIntent] Query : {}".format(self.query_id, data["query"]))
                logger.info("[{}] [QueryIntent] Followup updated query : {}".format(self.query_id, data["updated_query"]))
                return data
            else:
                return data
        except Exception as e:
            logger.error("[{}][QueryIntent] Followupcheck failed due to Error: {}".format(self.query_id, e))
            return data


    def check_for_generic_query(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Checks if the current query is a follow-up and updates the data dictionary.

        Args:
            data (Dict[str, Any]): A dictionary containing the data required to check for follow-up.

        Returns:
            Dict[str, Any]: The updated data dictionary.
        """
        try:
            data["generic_qna_response"] = []
            data["generic_ans_found"] = False
            # When query intent is None
            if not data["query_intent"]:
                logger.info(f"[{self.query_id}][QueryIntent] Query intent is None, something went wrong with query intent Identification")
                return data
            # Case - Generic
            elif (data["query_intent"]["question_type"] == self.query_intent_categories[0]):
                logger.info(f"[{self.query_id}] [QueryIntent] Case - Generic")
                logger.info("[{}] [QueryIntent] Incoming Query: {}".format(self.query_id,data["query"]))
                data["generic_ans_found"] = True
                if str(data["query_intent"]["response"]).strip():
                    data["generic_qna_response"] = [
                                                        {
                                                            "answer": str(data["query_intent"]["response"]).strip(),
                                                            "file_name": "",
                                                            "page": 0,
                                                            "similarity_score": 0.0,
                                                            "ans_type": "gpt",
                                                        }
                                                ]
                else:
                    data["generic_qna_response"] = [
                                                        {
                                                            "answer": str(data["gpt_generic_msg_content"]).strip(),
                                                            "file_name": "",
                                                            "page": 0,
                                                            "similarity_score": 0.0,
                                                            "ans_type": "gpt",
                                                        }
                                                ]

                logger.info("[{}] [QueryIntent] Generic Response: {}".format(self.query_id, data["generic_qna_response"]))
                return data
            else:
                return data
        except Exception as e:
            logger.error("[{}] [QueryIntent] Generic or Greeting check failed due to Error: {}".format(self.query_id, e))
            return data


    def check_for_invalid_query(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Checks if the current query is a inavlid input and updates the data dictionary.

        Args:
            data (Dict[str, Any]): A dictionary containing the data required to check for follow-up.

        Returns:
            Dict[str, Any]: The updated data dictionary.
        """
        try:
            data["invalid_qna_response"] = [self.default_response_dict]
            data["invalid_question_found"] = False
            # When query intent is None
            if not data["query_intent"]:
                logger.info("[{}] [QueryIntent] Query intent is None, something went wrong with query intent Identification".format(self.query_id))
                return data
            # Case - Generic
            elif (data["query_intent"]["question_type"] == self.query_intent_categories[3]):
                logger.info("[{}] [QueryIntent] Case - Invalid".format(self.query_id))
                logger.info("[{}] [QueryIntent] Incoming Query: {}".format(self.query_id, data["query"]))
                data["invalid_question_found"] = True
                data["invalid_qna_response"][0]["answer"] = str(data["query_intent"]["response"]).strip()
                logger.info("[{}] Invalid Question Response: {}".format(self.query_id, data["invalid_qna_response"]))
                return data
            else:
                return data
        except Exception as e:
            logger.error("[{}] [QueryIntent] Invalid check failed due to Error: {}".format(self.query_id, e))
            return data