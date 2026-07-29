import logging

# logging
logger = logging.getLogger('app.ResultProcessor')

class ResultProcessor:
    def __init__(self, data):
        self.query_id = data ["query_id"]
        self.answer = data["default_no_response"]
        self.file_name = data["default_filename"]
        self.page_number = [0]
        self.similarity = 0.0
        self.ans_type = "unknown"
        self.reference = "Context could not be found."
        self.updated_reference = self.reference
        self.default_response = [
            {
                "answer": self.answer,
                "file_name": self.file_name,
                "page": self.page_number,
                "similarity_score": self.similarity,
                "ans_type": self.ans_type,
                "context": self.reference
            }
        ]

    def validate_result_types(self, results: list) -> list:
        """
        Process a list of dictionaries containing results.

        Args:
            results (list): List of dictionaries representing results.

        Returns:
            list: Processed list of dictionaries with type-checked and converted values or default values.
        """
        # Set up logging
        logger.info(f"[{self.query_id}] [Results] Inside validate_result_types()")

        processed_results = []

        for res in results:
            processed_res = res

            try:
                # Check and convert values
                for key, default_value in self.default_response[0].items():
                    if key in res:
                        value = res[key]

                        if key=="file_name": #TODO: Repair chunking process in order to get file_name and page_num as lists instead of str
                            try:
                                processed_res[key] = [value]
                            except (ValueError, TypeError):
                                logger.error(
                                    f"[{self.query_id}] [Results] Cannot convert value of key '{key}': {value}. Setting default value."
                                )
                                processed_res[key] = default_value
                        elif key=="page": #TODO: Repair chunking process in order to get file_name and page_num as lists instead of str
                            try:
                                processed_res[key] = [value]
                            except (ValueError, TypeError):
                                logger.error(
                                    f"[{self.query_id}] [Results] Cannot convert value of key '{key}': {value}. Setting default value."
                                )
                                processed_res[key] = default_value
                        # Check if value has the same type as expected
                        elif not isinstance(value, type(default_value)):
                            # Try to convert value to expected type
                            try:
                                processed_res[key] = type(default_value)(value)
                            except (ValueError, TypeError):
                                logger.error(
                                    f"[{self.query_id}] [Results] Cannot convert value of key '{key}': {value}. Setting default value."
                                )
                                processed_res[key] = default_value
                    else:
                        # Key is missing in dictionary, set default value
                        processed_res[key] = default_value
            except Exception as e:
                logger.error(
                    f"[{self.query_id}] [Results] Error processing dictionary: {res}. Error: {str(e)}"
                )

            processed_results.append(processed_res)

        # return default response if empty
        if not processed_results:
            return self.default_response
        logger.info(f"[{self.query_id}][Results] Completed validate_result_types()!")

        return processed_results

    def validate_results(self, results: list) -> list:
        """
        Process a list of dictionaries containing results.

        Args:
            results (list): List of dictionaries representing results.

        Returns:
            list: Processed list of dictionaries with default values set for missing or None values.
        """
        logger.info(f"[{self.query_id}] [Results] Inside validate_results()!")

        processed_results = []

        # return default response if results is not a list
        if type(results) != list:
            return self.default_response

        for res in results:
            # return default response if res is not a dict
            if type(res) != dict:
                res = self.default_response[0]
            try:
                # Check for required keys
                for key in self.default_response[0].keys():
                    if key not in res:
                        logger.warning(
                            f"[{self.query_id}][Results] Key '{key}' is missing in dictionary: {res}"
                        )
                        res[key] = self.default_response[0][key]

                    # Set default values if corresponding values are None
                    if not res[key]:
                        res[key] = self.default_response[0][key]

                processed_results.append(res)
            except KeyError as e:
                logger.error(f"[{self.query_id}] [Results] {str(e)}")

        # return default response if empty
        if not processed_results:
            return self.default_response

        # validate value types
        processed_results = self.validate_result_types(processed_results)

        logger.info(f"[{self.query_id}][Results] Completed validate_results()!")

        return processed_results

    def process_answers(self, data: dict) -> str:
        """To process the response to avoid different formats observed in openai response.

        Args:
            data (dict)
                - response (str): response string from openai
                - all_files (list): list of all dataframes loaded

        Returns:
            str: formatted response.
        """

        clubbed_results = []
        data["all_results"] = []
        logger.info(f"[{self.query_id}] [Results] answer processing starts")

        # validate all answers

        if data["is_gpt_enabled"] and data["gpt_ans_found"]:
            data["gpt_response"] = self.validate_results(data["gpt_response"])
            if data["gpt_response"]:
                for answers in data["gpt_response"]:
                    clubbed_results.append(answers)

        if data["is_cache_enabled"] and data["cache_found"]:
            data["cache_qna_response"] = self.validate_results(data["cache_qna_response"])
            if data["cache_qna_response"]:
                for answers in data["cache_qna_response"]:
                    clubbed_results.append(answers)

        if data["is_greeting_enabled"] and data["generic_ans_found"]:
            data["generic_qna_response"] = self.validate_results(data["generic_qna_response"])
            if data["generic_qna_response"]:
                for answers in data["generic_qna_response"]:
                    clubbed_results.append(answers)

        if (data["is_greeting_enabled"] or data["is_followup"]) and data["invalid_question_found"]:
            data["invalid_qna_response"] = self.validate_results(data["invalid_qna_response"])
            if data["invalid_qna_response"]:
                for answers in data["invalid_qna_response"]:
                    clubbed_results.append(answers)

        data["all_results"] = clubbed_results
        logger.info(f"[{self.query_id}] [Results] answer processing ends")
        
        return data
