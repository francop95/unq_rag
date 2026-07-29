import os
import sys
import asyncio
import logging

from qnas.GPTQna import GPTQna
from qnas.RetrieverQna_multimodal import RetrieverQna

# Get the path of the top-level directory
top_level_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Add the top-level directory to sys.path
sys.path.append(top_level_dir)

# append other directories to sys.path
sys.path.append(os.path.join(top_level_dir, "models"))
sys.path.append(os.path.join(top_level_dir, "contexts"))
sys.path.append(os.path.join(top_level_dir, "qnas"))

# logging
logger = logging.getLogger('app.QuestionAnswer')


class QuestionAnswer:
    def __init__(self, data: dict):
        """
        Initializes the QuestionAnswer instance.
        """
        self.gpt_enabled = data["is_gpt_enabled"]
        self.retriever_enabled = data["is_retriever"]
        self.query_id = data["query_id"]
        logger.info(f"[{self.query_id}] [QAns] Initialized QuestionAnswer!")

    def answer_query(self, data: dict) -> str:
        """
        Processes the data with different parameters and returns a data dictionary
        with the response (answer, file name, page, similarity score) included.

        Args:
            data (dict): A dictionary containing the necessary parameters and data.

        Returns:
            dict: A data dictionary with the response included.

        Example:
            qa = QuestionAnswer()
            data = {...}  # Data dictionary with necessary parameters
            processed_data = qa.process_data(data)
        """
        logger.info(f"[{self.query_id}] [QAns] Inside answer_query()")

        # set default values
        data["gpt_response"] = None
        data["gpt_ans_found"] = False

        try:
            if self.gpt_enabled:
                # call GPT
                logger.info(f"[{self.query_id}] [QAns] GPT enabled for default QNA!")
                if self.retriever_enabled:
                    # Retriever Qna
                    retriever_qna = RetrieverQna(data)
                    data = retriever_qna.get_response(data)
 
                    if data["gpt_ans_found"]:
                        logger.info(
                            f"[{self.query_id}] [QAns] Valid GPT response."
                        )
                    else:
                        logger.info(f"[{self.query_id}] [QAns] GPT Answer not found!")

                else:    
                    # call GPT
                    gpt_model = GPTQna(data)
                    # - Execute the coroutine coro and return the result.
                    # asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
                    gpt_response = gpt_model.get_response_from_gemini(data)
                    data["gpt_response"] = gpt_response

                    # set gpt answer found
                    for res in data["gpt_response"]:
                        if res["answer"] != data["gpt_no_answer_str"]:
                            data["gpt_ans_found"] = True
                            logger.info(f"[{self.query_id}] [QAns] Valid ans in GPT result: {str(True)}")
                            break
           
        except Exception as e:
            response = str(e)
            logger.error(f"[{self.query_id}] [QAns] Error getting answer: {response}")

        logger.info(
            f"[{self.query_id}] [QAns] Updated gpt_ans_found parameter!"
        )
        return data
