import json
import openai
import logging
import asyncio
import aiohttp
from aiohttp_retry import RetryClient, ExponentialRetry
from typing import Any, Dict, List
from time import perf_counter


# logging
logger = logging.getLogger('app.ModelCompletion')


class Completion:
    def __init__(self) -> None:
        self.gpt_chat_response = {"choices": [{"message": {"content": ""}}]}
        self.failure_statuses = {x for x in range(100, 600)}
        self.success_status = [200]
        self.failure_statuses.remove(200)
        self.retries = 5

    def generate_headers(self) -> Dict[str, str]:
        raise NotImplementedError()

    def generate_body(self, messages: List[str]) -> Dict[str, Any]:
        raise NotImplementedError()

    def get_response(self, messages_list: List[str]) -> Any:
        raise NotImplementedError()


class ModelCompletion(Completion):
    def __init__(self, data: Dict[str, Any], model_type: str, dynamic_params: Dict={}) -> None:
        super().__init__()
        self.model_type = model_type
        self.query_id = data["query_id"]
        self.initialize_params(data, dynamic_params)

    def initialize_params(self, data: Dict[str, Any], dynamic_params: Dict={}) -> None:
        logger.info(f"[{self.query_id}] [GPTCompletion] Initializing model parameters for model_type: {self.model_type}")
        if self.model_type == "azure":
            if dynamic_params:
                self.AZURE_MODEL = dynamic_params['azure_model']
                self.max_tokens = dynamic_params['max_tokens']
            else:
                self.AZURE_MODEL = data["azure_oai_model1"]
                if "max_tokens" in data.keys():
                    self.max_tokens = data["max_tokens"]
            self.AZURE_OAI_KEY = data["azure_oai_api_key"]
            self.AZURE_OAI_BASE = data["azure_oai_base"]
            self.AZURE_OAI_API_VERSION = data["azure_oai_api_version"]
            self.AZURE_EMB_MODEL = data["azure_oai_embedding_model"]
            # openai.api_type = "azure"
            self.model = openai.ChatCompletion(
                engine=self.AZURE_MODEL,
                api_token=self.AZURE_OAI_KEY,
                api_base=self.AZURE_OAI_BASE,
                deployment_id=self.AZURE_MODEL,
                api_version=self.AZURE_OAI_API_VERSION,
                temperature=0,
                top_p=0.9,
                frequency_penalty=0,
                presence_penalty=0,
                max_tokens=self.max_tokens,
                stop=None,
            )
            self.endpoint = (
                self.AZURE_OAI_BASE
                + "openai/deployments/"
                + self.AZURE_MODEL
                + "/chat/completions?api-version="
                + str(self.AZURE_OAI_API_VERSION)
            )
            self.headers = self.generate_headers(data["azure_oai_api_key"])
            self.payload = {
                "messages": "",
                "temperature": 0.7,
                "max_tokens": self.max_tokens,
                "top_p": 0.95,
                "frequency_penalty": 0,
                "presence_penalty": 0,
                "stop": None,
            }
            self.default_response_dict = {
                "query": "",
                "response": "",
                "model_type": self.model_type,
                "res_status": data["completion_failure"],
            }
            self.completion_failure = data["completion_failure"]
            self.completion_success = data["completion_success"]
        elif self.model_type == "openai":
            self.model = None
        elif self.model_type == "huggingface":
            self.model = None
        else:
            raise ValueError("Invalid model type")

    def generate_headers(self, api_key: str) -> Dict[str, str]:
        return {"Content-Type": "application/json", "api-key": api_key}

    def generate_body(self, message: List[str], data: Dict[str, Any]) -> Dict[str, Any]:
        payload = self.payload
        if self.model_type == "azure":
            payload["messages"] = message
            logger.info(f"[{data['query_id']}] [GPTCompletion] Payload {payload}")
        elif self.model_type == "openai":
            pass
        elif self.model_type == "huggingface":
            pass
        return payload

    async def get_response(
        self, messages_list: List[List], data: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        post_tasks = []
        if self.model_type == "azure":
            if type(messages_list[0]) != list:
                messages_list = [messages_list]
        logger.info(f"[{self.query_id}] [GPTCompletion] Azure OpenAI Chat Completion Start")
        start = perf_counter()
        retry_options = ExponentialRetry(
            attempts=self.retries, statuses=self.failure_statuses
        )
        async with aiohttp.ClientSession(raise_for_status=True) as session:
            retry_client = RetryClient(
                client_session=session,
                raise_for_status=False,
                retry_options=retry_options,
            )
            for message in messages_list:
                body = self.generate_body(message, data)
                post_tasks.append(
                    asyncio.create_task(
                        self.azure_openai_chat_completion(
                            retry_client, json.dumps(body)
                        )
                    )
                )
            responses = await asyncio.gather(*post_tasks, return_exceptions=True)
            time_taken = round(perf_counter() - start, 3)
            logger.debug(f"[{self.query_id}] [GPTCompletion] Azure OpenAI Chat responses: {responses}")
            logger.info(f"[{self.query_id}] [GPTCompletion] Azure OpenAI Chat Completion Ends.")
            logger.info(f"[{self.query_id}] [GPTCompletion] Chat Completion Time Taken: {str(time_taken)}")
            processed_response = self.process_response(responses, data["query"])
            responses[0]["time_taken"] = time_taken

        return processed_response, responses

    async def azure_openai_chat_completion(
        self, session: aiohttp.ClientSession, body: str
    ) -> Dict[str, Any]:
        """
        Send a request to openai.
        :param api_key: your api key
        :param payload: the request body, as detailed here: https://learn.microsoft.com/en-us/azure/cognitive-services/openai/quickstart?pivots=rest-api
        """
        logger.info(f"[{self.query_id}] [GPTCompletion] Inside azure_openai_chat_completion()")

        async with session.post(
            self.endpoint, headers=self.headers, timeout=120, data=body
        ) as r:
            logger.debug(f"[{self.query_id}] [GPTCompletion] Response: {str(r)}")
            res = self.gpt_chat_response
            if r.status not in self.success_status:
                r.wait_for_status()
                raise RuntimeError

            res = await r.json()
            if res["choices"][0]["message"]["content"]:
                pass

            return res

    def process_response(
        self,
        responses: List[Dict[str, Any]],
        query: str,
    ) -> List[Dict[str, Any]]:
        """To parse the the model response to a desired format based on the model_type.

        Args:
            responses (List[Dict[str, Any]]): List of responses from the model.
            query (str): Query input from the user.

        Returns:
            List[Dict[str, Any]]: _description_
        """
        logger.info(f"[{self.query_id}] [GPTCompletion] Inside process_response()")
        parsed_responses = []
        # Empty response from model api
        if not responses:
            parsed_responses.append(self.default_response_dict)
            return parsed_responses
        for i, res in enumerate(responses):
            response_dict = self.default_response_dict
            response_dict["query"] = query
            response_dict["response"] = str(res)
            if self.model_type == "azure":
                try:
                    response_dict["response"] = res["choices"][0]["message"]["content"]
                    response_dict["res_status"] = self.completion_success

                except Exception as e:
                    logger.error(
                        "[{}] [GPTCompletion] process_response failure. Error: {}. response: {}".format(
                           self.query_id, e, res
                        )
                    )
                parsed_responses.append(response_dict)
        logger.info(f"[{self.query_id}] [GPTCompletion] processed response")
        return parsed_responses


class DefaultCompletion(Completion):
    # The Azure implementation is not applicable for chat completion as it's a wrapper for OpenAI's API.
    pass
