import json
import logging
from time import perf_counter
from typing import Any, Dict, List
import requests
from tenacity import retry, wait_random_exponential, stop_after_attempt
import os
import sys
import httpx

logger = logging.getLogger('app.ModelCompletion')
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, "../../"))
sys.path.append(project_root)

# --- OpenAI (SDK 1.x) ---
try:
    from openai import OpenAI as _OpenAI
    from openai import APIConnectionError, APITimeoutError, RateLimitError, APIStatusError
except Exception:
    _OpenAI = None
    APIConnectionError = APITimeoutError = RateLimitError = APIStatusError = Exception

# --- Google Gemini (SDK nuevo) ---
try:
    from google import genai as _genai
except Exception:
    _genai = None


class Completion:
    def __init__(self) -> None:
        self.gpt_chat_response = {"choices": [{"message": {"content": ""}}]}
        self.retries = 5

    def generate_headers(self) -> Dict[str, str]:
        raise NotImplementedError()

    def generate_body(self, messages: List[str]) -> Dict[str, Any]:
        raise NotImplementedError()

    def get_response(self, messages_list: List[str]) -> Any:
        raise NotImplementedError()


class ModelCompletion(Completion):
    def __init__(self, data: Dict[str, Any], model_type: str, dynamic_params: Dict = {}) -> None:
        super().__init__()
        self.model_type = str(model_type or "").lower()
        self.query_id = data["query_id"]
        self.initialize_params(data, dynamic_params)

    def initialize_params(self, data: Dict[str, Any], dynamic_params: Dict = {}) -> None:
        logger.info(f"[{self.query_id}] [GPTCompletion] Initializing params model_type={self.model_type}")
        self._file_id_cache = {}
        self.electric_diagram_path=data["electric_diagram_path"]
        self.tben_diagram_path=data["tben_diagram_path"]
        # Default True: si no se determinó nada (ej. sin contexto relevante),
        # los planos quedan como único fallback disponible para el LLM.
        self.attach_electric_diagrams = bool(data.get("attach_electric_diagrams", True))
        self.completion_failure = data["completion_failure"]
        self.completion_success = data["completion_success"]

        # -------- OPENAI (SDK 1.x) --------
        if self.model_type == "openai":
            if _OpenAI is None:
                raise RuntimeError("Falta instalar openai>=1.0.0 (pip install openai)")
            self.OPENAI_MODEL = dynamic_params.get("model") or dynamic_params.get("openai_model") \
                                or data.get("openai_model1") or data.get("openai_model") or "gpt-4o-mini"
            self.max_tokens = int(dynamic_params.get('max_tokens') or data.get("max_tokens", 1024))
            api_key = data.get("openai_keys") or os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY no configurado (env o data['openai_api_key'])")
            # timeout definido para evitar cuelgues
            http = httpx.Client(timeout=httpx.Timeout(connect=10.0, read=180.0, write=180.0, pool=5.0))
            self._openai = _OpenAI(api_key=api_key,http_client=http, max_retries=2)
            self._gemini = None
            self.headers = {}
            self.payload = {}

            self.default_response_dict = {
                "query": "",
                "response": "",
                "model_type": self.model_type,
                "res_status": self.completion_failure,
            }

    def generate_headers(self, api_key: str) -> Dict[str, str]:
        # headers para la rama Azure (requests)
        return {"Content-Type": "application/json", "api-key": api_key}

    def _normalize_messages(self, messages):
        """
        Asegura que 'messages' sea lista de dicts [{role, content}, ...]
        """
        if isinstance(messages, list) and messages and isinstance(messages[0], dict):
            return messages
        return messages if isinstance(messages, list) else [messages]

    def generate_body(self, message: List[Dict[str, str]], data: Dict[str, Any]) -> Dict[str, Any]:
        if self.model_type == "azure":
            payload = dict(self.payload)
            payload["messages"] = self._normalize_messages(message)
            logger.debug(f"[{data['query_id']}] [GPTCompletion] Payload {payload}")
            return payload
        # openai / gemini no usan este body
        return {}

    # ---- reintento REST solo para la rama Azure ----
    def _tenacity_retry_predicate(self, exception):
        return isinstance(exception, requests.exceptions.RequestException)

    @retry(wait=wait_random_exponential(multiplier=1, min=2, max=8),
           stop=stop_after_attempt(3),
           retry_error_callback=lambda x: x.outcome.result() if x.outcome else None,
           retry=lambda self, exc: ModelCompletion._tenacity_retry_predicate(self, exc))
    def make_openai_request(self, endpoint, headers, data):
        resp = requests.post(endpoint, headers=headers, json=data, timeout=30)
        resp.raise_for_status()
        return resp.json()
    
    def _messages_to_prompt(self, messages):
        parts = []
        for m in self._normalize_messages(messages):
            role = m.get("role", "user")
            content = m.get("content", "")
            parts.append(f"{role.upper()}: {content}")
        return "\n".join(parts).strip()

    def _is_responses_only_model(self, model: str) -> bool:
        # Heurística: modelos que suelen requerir Responses API y no siempre admiten sampling params
        prefixes = ("gpt-5", "o4", "o3")
        return any(model.startswith(p) for p in prefixes)
    
    def _normalize_response_to_chat_choice(self,r) -> dict:
        """
        Normaliza openai.types.responses.response.Response a un dict tipo ChatCompletion:
        {"choices":[{"message":{"content": "<texto>"}}]}
        """
        # 1) Camino feliz: helper del SDK
        try:
            text = getattr(r, "output_text", None)
            if text:
                return {"choices": [{"message": {"content": text}}]}
        except Exception:
            pass

        # 2) Recorrer items de salida y concatenar solo mensajes de assistant
        chunks = []
        try:
            for item in getattr(r, "output", []) or []:
                # Ignora reasoning items; toma mensajes
                if getattr(item, "type", None) == "message":
                    for c in getattr(item, "content", []) or []:
                        # c puede ser ResponseOutputText, etc.
                        t = getattr(c, "text", None)
                        if t:
                            chunks.append(t)
        except Exception:
            pass

        if chunks:
            return {"choices": [{"message": {"content": "\n".join(chunks)}}]}

        # 3) Último recurso: representación segura
        safe_text = ""
        try:
            safe_text = r.text or ""
        except Exception:
            pass
        if not safe_text:
            safe_text = repr(r)
        return {"choices": [{"message": {"content": safe_text}}]}

    def _call_openai(self, message: list[dict]) -> dict:
        """
        1) Si el modelo admite Chat Completions, úsalo (max_tokens).
        2) Si es de Responses-only o el servidor dice unsupported_parameter, usa Responses (max_output_tokens)
        y NO envíes temperature/top_p.
        """
        model = self.OPENAI_MODEL
        # --- Ruta Chat Completions, sólo si NO parece responses-only ---
        if not self._is_responses_only_model(model):
            try:
                resp = self._openai.chat.completions.create(
                    model=model,
                    messages=self._normalize_messages(message),
                    max_tokens=self.max_tokens,
                    temperature=0.7,
                    top_p=0.95,
                )
                content = resp.choices[0].message.content if resp and resp.choices else ""
                return {"choices": [{"message": {"content": content}}]}
            except APIStatusError as e:
                # Si el backend sugiere usar Responses o marca unsupported_parameter, caemos a Responses
                msg = (str(e) or "").lower()
                if "unsupported_parameter" not in msg and "use the responses api" not in msg and e.status_code != 400:
                    raise

        # --- Ruta Responses API (sin temperature/top_p) ---
        prompt = self._messages_to_prompt(message)
        try:
            r = self._openai.responses.create(
                model=model,
                input=prompt,
                max_output_tokens=self.max_tokens,  # Responses usa este nombre
                # OJO: no enviamos temperature/top_p si el modelo no los soporta
            )
        except APIStatusError as e:
            # último intento: reintentar quitando cualquier sampling param si quedara alguno
            r = self._openai.responses.create(
                model=model,
                input=prompt,
                max_output_tokens=self.max_tokens,
            )

        # Si quedó incompleto por tope de tokens, reintenta con más margen
        if getattr(r, "incomplete_details", None) and getattr(r.incomplete_details, "reason", "") == "max_output_tokens":
            # duplica o fija un techo alto controlado
            new_max = max(getattr(self, "max_tokens", 1024) * 2, 1024)
            r = self._openai.responses.create(
                model=self.OPENAI_MODEL,
                input=prompt,
                max_output_tokens=new_max,
            )

        return self._normalize_response_to_chat_choice(r)
    
    def _ensure_file_id(self, file_ref: str) -> str:
        """
        Acepta un file_id existente o un path local.
        Devuelve un file_id válido (<=64 chars).
        """
        # Si parece un file_id (corto) lo devolvemos
        if isinstance(file_ref, str) and len(file_ref) <= 64 and not os.path.exists(file_ref):
            return file_ref

        # Si es path, subimos (y cacheamos)
        if not os.path.exists(file_ref):
            raise FileNotFoundError(f"No existe el archivo: {file_ref}")

        if file_ref in self._file_id_cache:
            return self._file_id_cache[file_ref]

        created = self._openai.files.create(
            file=open(file_ref, "rb"),
            purpose="user_data",
        )
        self._file_id_cache[file_ref] = created.id
        return created.id

    def _build_responses_input(self, messages: list[dict], pdfs: list[str] | None = None):
        """
        Convierte tu estructura tipo chat en el formato 'input' de Responses
        y adjunta PDFs (paths o file_ids).
        """
        structured = []
        for m in messages:
            role = m["role"]
            content = m.get("content", "")
            if isinstance(content, str):
                content_parts = [{"type": "input_text", "text": content}]
            else:
                # si ya viene estructurado, lo respetamos
                content_parts = content
            structured.append({"role": role, "content": content_parts})

        if pdfs:
            pdf_ids = [self._ensure_file_id(p) for p in pdfs]
            # Adjunta todos los PDFs al último mensaje del user (o crea uno)
            idx = next((i for i in range(len(structured)-1, -1, -1) if structured[i]["role"] == "user"), None)
            if idx is None:
                structured.append({"role": "user", "content": []})
                idx = len(structured) - 1

            structured[idx]["content"].extend(
                [{"type": "input_file", "file_id": fid} for fid in pdf_ids]
            )

        return structured
    
    def _call_openai_multimodal(self, message: list[dict]) -> dict:
        """
        - Si NO hay PDFs y el modelo admite Chat Completions: usa ChatCompletions.
        - Si hay PDFs (file_ids) o el modelo es Responses-only: usa Responses (input estructurado) y NO envíes temperature/top_p.
        """
        model = self.OPENAI_MODEL
        pdfs = [self.electric_diagram_path, self.tben_diagram_path] if self.attach_electric_diagrams else []

        # Si hay PDFs, vamos directo a Responses (ChatCompletions no adjunta input_file)
        if pdfs:
            structured_input = self._build_responses_input(message, pdfs=pdfs)
            r = self._openai.responses.create(
                model=model,
                input=structured_input,
                max_output_tokens=self.max_tokens,
            )
            return self._normalize_response_to_chat_choice(r)

        # --- Ruta Chat Completions, sólo texto ---
        if not self._is_responses_only_model(model):
            try:
                resp = self._openai.chat.completions.create(
                    model=model,
                    messages=self._normalize_messages(message),
                    max_tokens=self.max_tokens,
                    temperature=0.7,
                    top_p=0.95,
                )
                content = resp.choices[0].message.content if resp and resp.choices else ""
                return {"choices": [{"message": {"content": content}}]}
            except APIStatusError as e:
                msg = (str(e) or "").lower()
                if "unsupported_parameter" not in msg and "use the responses api" not in msg and e.status_code != 400:
                    raise

        # --- Fallback Responses texto-only ---
        prompt = self._messages_to_prompt(message)
        r = self._openai.responses.create(
            model=model,
            input=prompt,
            max_output_tokens=self.max_tokens,
        )
        return self._normalize_response_to_chat_choice(r)


    def _call_gemini(self, message: List[Dict[str, str]]) -> Dict[str, Any]:
        """
        Llama a Gemini (google-genai) y normaliza a {"choices":[{"message":{"content": ...}}]}
        """
        # Convierte messages a un bloque de texto simple (system + user)
        parts = []
        for m in self._normalize_messages(message):
            c = m.get("content", "")
            if c:
                parts.append(str(c))
        prompt = "\n".join(parts).strip()

        try:
            resp = self._gemini.models.generate_content(model=self.GEMINI_MODEL, contents=prompt)
            # SDK suele exponer 'text'; si no, convierte a str
            content = getattr(resp, "text", None) or str(resp)
            return {"choices": [{"message": {"content": content}}]}
        except Exception as e:
            return {"choices": [{"message": {"content": ""}}], "error": str(e)}

    def get_response(self, messages_list: List[List], data: Dict[str, Any]) -> List[Dict[str, Any]]:
        # Normaliza para que sea una lista de prompts (lotes)
        if not messages_list:
            messages_list = [[]]
        if messages_list and not isinstance(messages_list[0], list):
            messages_list = [messages_list]

        logger.info(f"[{self.query_id}] [GPTCompletion] Chat Completion Start ({self.model_type})")
        start = perf_counter()
        responses: List[Dict[str, Any]] = []

        for i, message in enumerate(messages_list):
            if self.model_type == "azure":
                body = self.generate_body(message, data)
                res = self.make_openai_request(self.endpoint, self.headers, body)
                # Normaliza a formato "choices"
                try:
                    content = res["choices"][0]["message"]["content"]
                except Exception:
                    content = json.dumps(res, ensure_ascii=False)
                responses.append({"choices": [{"message": {"content": content}}]})
            elif self.model_type == "openai":
                res = self._call_openai_multimodal(message)
                responses.append(res)
            elif self.model_type == "gemini":
                res = self._call_gemini(message)
                responses.append(res)
            else:
                responses.append({"choices": [{"message": {"content": ""}}]})

            # time_taken por item
            responses[i]["time_taken"] = round(perf_counter() - start, 3)

        logger.info(f"[{self.query_id}] [GPTCompletion] Chat Completion Ends. items={len(responses)}")
        processed_response = self.process_response(responses, data["query"])
        return processed_response, responses

    def process_response(self, responses: List[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
        """
        Parsea cualquier rama (azure/openai/gemini) gracias a la normalización a 'choices[0].message.content'.
        """
        logger.info(f"[{self.query_id}] [GPTCompletion] Inside process_response()")
        parsed_responses: List[Dict[str, Any]] = []

        if not responses:
            parsed_responses.append({
                **self.default_response_dict,
                "query": query,
                "response": "",
            })
            return parsed_responses

        for res in responses:
            response_dict = dict(self.default_response_dict)
            response_dict["query"] = query
            try:
                content = res["choices"][0]["message"]["content"]
                response_dict["response"] = content
                response_dict["res_status"] = self.completion_success if content else self.completion_failure
            except Exception as e:
                logger.error(f"[{self.query_id}] [GPTCompletion] process_response error: {e}; res={res}")
                response_dict["response"] = json.dumps(res, ensure_ascii=False)
                response_dict["res_status"] = self.completion_failure

            parsed_responses.append(response_dict)

        logger.info(f"[{self.query_id}] [GPTCompletion] processed response")
        return parsed_responses


class DefaultCompletion(Completion):
    pass
