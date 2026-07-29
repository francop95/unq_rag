import json
import re
import logging
import Levenshtein as lev
import openai
import asyncio
import aiohttp
from time import perf_counter
import pandas as pd
from aiohttp_retry import RetryClient, ExponentialRetry
from configs.Configuration import Configuration
import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, "../../"))
sys.path.append(project_root)


logger = logging.getLogger('app.GPTQna')


# Initializing Configuration object
config = Configuration()

openai_exception_res = {"choices": [{"message": {"content": ""}}]}


class GPTQna:
    def __init__(self, data: dict):
        self.data = data
        self.query_id = data['query_id']
        self.statuses = {x for x in range(100, 600)}
        self.statuses.remove(200)
        self.retries = 5
        logger.info(f"[{self.query_id}] [GPTQna] Initialized GPTQna!")

    # GPT4 Chat Completion post method to hit the api
    async def azure_gpt4_openai_completion_post(self, session, url: str, payload: dict):
        """
        Send a request to openai.
        :param api_key: your api key
        :param payload: the request body, as detailed here: https://learn.microsoft.com/en-us/azure/cognitive-services/openai/quickstart?pivots=rest-api
        """
        logger.info(f"[{self.query_id}] [GPT] Inside azure_gpt4_openai_completion_post()")

        async with session.post(
            url, headers=self.headers, timeout=120, data=payload
        ) as r:
            logger.info(f"[{self.query_id}] [GPTQna] Response: {str(r)}")
            res = openai_exception_res
            # try:
            if r.status != 200:
                r.wait_for_status()
                raise RuntimeError
            res = await r.json()
            if res["choices"][0]["message"]["content"]:
                pass

            return res

    # To clean the response text
    def clean_response(self, response: str) -> str:
        """To clean the response to avoid different formats observed in openai response.

        Args:
            response (str): response string from openai

        Returns:
            str: cleaned response.
        """
        logger.info(f"[{self.query_id}] [GPT] Inside clean_response()")
        if re.match(r"\n*a\d{0,1}\:*\.*", response.lower()) or re.match(
            r"\n*answer\d{0,1}\:*\.*", response.lower()
        ):
            response = " ".join(response.split(" ")[1:])
        response = re.sub(r"\n*\s*\#{0,3}\n*\s*A\:+", "", response)
        response = response.strip()
        if response.strip()[0].islower():
            response = str(response.strip()[0].upper() + response[1:]).strip()

        return response

    # To find the levenshtein matching score
    def find_levenshtein(self, response, reference):
        clean_re = re.compile(r"[\"\.\,\']")
        response = re.sub(clean_re, "", response)
        reference = re.sub(clean_re, "", reference)
        ratio = lev.ratio(response.lower(), reference.lower()) * 100
        return ratio

    # To asynchronously hit azure OpenAI GPT4 Chat Completion api with different contexts and get the respective responses
    async def answer_using_gpt4(
        self, session: aiohttp.ClientSession, data: dict, model: str
    ) -> dict:
        """To find the response for the given query from openai using top n contexts.

        Args:
            session (aiohttp.ClientSession): Async session variable.
            query (str): Query string.
            df (str): embeddings dataframe.
            n (int): top n contexts.
            model (pkl): Completion model name.
            embeddings_model (str): embedding model name.
            use_openai (bool, optional): Flag to know whether to use openai or azure openai. Defaults to False.

        Returns:
            dict: Final response dict with openai reponse , pages, file names and similarirty scores.
        """
        logger.info(f"[{self.query_id}] [GPT] Inside answer_using_gpt4()")
        # To find the best fit context (top 3)
        post_tasks = []
        message_prompt = []

        similarities = None
        if data["context_found"]:
            similarities = data["context_dataframe"]
            similarities = similarities.head(data["gpt_top_n_contexts"])

        openai_exception_res["choices"][0]["message"][
            "content"
        ] = "No Context identified"
        responses = {}
        responses["response"] = [openai_exception_res]
        responses["page"] = [-1]
        responses["similarities"] = [0]
        responses["file_name"] = [""]

        if type(similarities) == pd.DataFrame:
            if similarities.empty:
                logger.info(f"[{data['query_id']}] [GPTQna] No context identified!")
                return responses
        else:
            if not similarities:
                logger.info(f"[{data['query_id']}] [GPTQna] No context identified!")
                return responses

        pages = list(similarities["Page Number"])

        responses = {}

        # try:
        start = perf_counter()
        for i in range(similarities.shape[0]):
            context = similarities["Text"].iloc[i]

            gpt_no_answer_str = data["gpt_no_answer_str"]
            prompt = data["gpt_qna_prompt"].format(
                gpt_no_answer_str,
                context,
                data["query"],
            )

            message_prompt = [
                {"role": "system", "content": data["gpt_sys_msg_content"]},
                {"role": "user", "content": prompt},
            ]

            endpoint_url = (
                openai.api_base
                + "openai/deployments/"
                + model
                + "/chat/completions?api-version="
                + str(openai.api_version)
            )
            payload = {
                "messages": message_prompt,
                "temperature": 0.7,
                "max_tokens": 4000,
                "top_p": 0.95,
                "frequency_penalty": 0,
                "presence_penalty": 0,
                "stop": None,
            }
            post_tasks.append(
                asyncio.create_task(
                    self.azure_gpt4_openai_completion_post(
                        session, endpoint_url, json.dumps(payload)
                    )
                )
            )

        batch_results = await asyncio.gather(*post_tasks, return_exceptions=True)
        logger.info(f"[{self.query_id}] [GPTQna] Time Taken Async OpenAI: {str(round(perf_counter() - start, 3))}"
        )
        logger.info(f"[{self.query_id}] [GPT] Inside answer_using_gpt4() : Batch Results: {str(batch_results)}")

        responses = {}
        responses["response"] = batch_results
        responses["page"] = pages
        responses["similarities"] = list(similarities["Similarity Score"])
        responses["file_name"] = list(similarities["File Name"])

        return responses

    # To set the azure OpenAI parameters
    def set_azure_openai_params(self,data):
        openai.api_key = data["azure_oai_api_key"]
        openai.api_type = data["llm_type"]
        openai.api_base = data["azure_oai_base"]
        openai.api_version = data["azure_oai_api_version"]
        MODEL = data["azure_oai_model"]
        self.headers = {
            "api-key": str(openai.api_key),
            "Content-Type": "application/json",
        }
        logger.info(f"[{self.query_id}] [GPTQna] Azure openai parameters are set")
        return MODEL

    # To get the response from gpt4 model for the input query
    async def get_response_from_gpt4(self, data) -> pd.DataFrame:
        """To parse the openai response and create a response dataframe.

        Args:
            query (str): Query string.

        Returns:
            pd.DataFrame: Result datfarme with parsed response, page, file name and similarity scores.
        """
        logger.info(f"[{self.query_id}] [GPT] Inside get_response_from_gpt4()")
        query = data["query"]

        logger.info(f"[{self.query_id}] [GPT] Query: {query}")
        results = []
        responses = []

        """
        if data["use_openai"]:
            MODEL = self.set_openai_params()
        else:
            MODEL = self.set_azure_openai_params()
        """
        MODEL = self.set_azure_openai_params(data)
        try:
            retry_options = ExponentialRetry(
                attempts=self.retries, statuses=self.statuses
            )
            async with aiohttp.ClientSession(raise_for_status=True) as session:
                retry_client = RetryClient(
                    client_session=session,
                    raise_for_status=False,
                    retry_options=retry_options,
                )
                start = perf_counter()
                results = await self.answer_using_gpt4(retry_client, data, MODEL)
                logger.info(f"[{self.query_id}] [GPTQna] Results: {str(results)}")
                for i in range(len(results["response"])):
                    try:
                        response_dict = {}
                        response_dict["answer"] = self.clean_response(
                            results["response"][i]["choices"][0]["message"]["content"]
                        )
                        logger.info(f"[{self.query_id}] [GPTQna] response answer : "+str(response_dict["answer"]))
                        response_dict["page"] = results["page"][i]
                        response_dict["similarity_score"] = round(
                            results["similarities"][i] * 100, 2
                        )
                        response_dict["file_name"] = results["file_name"][i]
                        response_dict["ans_type"] = data["gpt_ans_type"]
                        responses.append(response_dict)
                    except Exception as e:
                        response_dict["answer"] = data["gpt_no_answer_str"]
                        response_dict["page"] = results["page"][i]
                        response_dict["similarity_score"] = round(
                            results["similarities"][i] * 100, 2
                        )
                        response_dict["file_name"] = results["file_name"][i]
                        response_dict["ans_type"] = data["gpt_ans_type"]
                        responses.append(response_dict)

                logger.info(
                    f"[{self.query_id}] [GPTQna] Time Taken: {str(round(perf_counter() - start, 3))}"
                )
        except Exception as e:
            # logger.info(e)
            logger.error(f"[{self.query_id}][GPTQna] Parsing response failed: {str(e)}", exc_info=True)

        logger.info(f"[{self.query_id}] [GPT] Return responses!")
        logger.info(f"[{self.query_id}] [GPT] Total responses: {str(len(responses))}!")
        return responses
    
    def get_response_from_gemini(self, data) -> pd.DataFrame:
        """To parse the openai response and create a response dataframe.

        Args:
            query (str): Query string.

        Returns:
            pd.DataFrame: Result datfarme with parsed response, page, file name and similarity scores.
        """
        logger.info(f"[{self.query_id}] [GPT] Inside get_response_from_gpt4()")
        query = data["query"]

        logger.info(f"[{self.query_id}] [GPT] Query: {query}")
        results = []
        responses = []

        """
        if data["use_openai"]:
            MODEL = self.set_openai_params()
        else:
            MODEL = self.set_azure_openai_params()
        """
        MODEL = self.set_gemini(data)
        try:

                start = perf_counter()
                results = self.answer_using_gemini(MODEL, data)
                logger.info(f"[{self.query_id}] [GPTQna] Results: {str(results)}")
                for i in range(len(results["response"])):
                    try:
                        response_dict = {}
                        response_dict["answer"] = self.clean_response(
                            results["response"][i]
                        )
                        logger.info(f"[{self.query_id}] [GPTQna] response answer : "+str(response_dict["answer"]))
                        response_dict["page"] = results["page"][i]
                        response_dict["similarity_score"] = round(
                            results["similarities"][i] * 100, 2
                        )
                        response_dict["file_name"] = results["file_name"][i]
                        response_dict["ans_type"] = data["gpt_ans_type"]
                        responses.append(response_dict)
                    except Exception as e:
                        response_dict["answer"] = data["gpt_no_answer_str"]
                        response_dict["page"] = results["page"][i]
                        response_dict["similarity_score"] = round(
                            results["similarities"][i] * 100, 2
                        )
                        response_dict["file_name"] = results["file_name"][i]
                        response_dict["ans_type"] = data["gpt_ans_type"]
                        responses.append(response_dict)

                logger.info(
                    f"[{self.query_id}] [GPTQna] Time Taken: {str(round(perf_counter() - start, 3))}"
                )
        except Exception as e:
            # logger.info(e)
            logger.error(f"[{self.query_id}][GPTQna] Parsing response failed: {str(e)}", exc_info=True)

        logger.info(f"[{self.query_id}] [GPT] Return responses!")
        logger.info(f"[{self.query_id}] [GPT] Total responses: {str(len(responses))}!")
        return responses