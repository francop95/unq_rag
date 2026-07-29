from tenacity import retry, stop_after_attempt, wait_random_exponential, retry_if_exception_type
import logging
import sys
import os

# --- OpenAI SDK 1.x ---
try:
    from openai import OpenAI as _OpenAI, APIConnectionError, APITimeoutError, RateLimitError, APIStatusError
except Exception:
    _OpenAI = None

# --- Gemini SDK nuevo (si lo usas) ---
try:
    from google import genai as _genai
    from google.genai import types as _gtypes
except Exception:
    _genai = None
    _gtypes = None


# logging
logger = logging.getLogger('app.LanguageModel')
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, "../../"))
sys.path.append(project_root)

#from ApiManager.GeminiAPIManager import GeminiRoundRobinClient


class LanguageModel:
    """
    Unifica acceso a modelos por tipo:
      - "openai": OpenAI SDK 1.x (embeddings y chat)
      - "azure":  (tu rama actual; ojo que usa API vieja para chat)
      - "gemini": (si lo reactivas)
      - "huggingface": placeholder
    """

    def __init__(self, data, model_type: str):
        self.model_type = (model_type or "").lower()

        # -------- OPENAI --------
        if self.model_type == "openai":
            if _OpenAI is None:
                raise RuntimeError("Falta instalar openai>=1.0.0 (pip install openai)")
            # modelos (con defaults razonables)
            self.OPENAI_MODEL = data.get("openai_model", "gpt-5")
            self.OPENAI_EMB_MODEL = data.get("openai_emb_model", "text-embedding-3-large")
            # API key: prioridad data → env
            api_key = data.get("openai_keys") or os.getenv("openai_key")
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY no configurado (env o data['openai_api_key'])")
            self._openai = _OpenAI(api_key=api_key,
                                    timeout=30.0,          # <- muy importante
                                    max_retries=0)          # <- dejamos reintentos a tenacity (más controlados))
            self.model = self._openai  # para mantener una referencia pública

        # -------- AZURE (tu código original; ojo: API antigua para chat) --------
        elif self.model_type == "azure":
            import openai  # tu rama legada usa el paquete con API vieja
            self.AZURE_MODEL = data["azure_oai_model1"]
            self.AZURE_OAI_KEY = data["azure_oai_api_key"]
            self.AZURE_OAI_BASE = data["azure_oai_base"]
            self.AZURE_OAI_API_VERSION = data["azure_oai_api_version"]
            self.AZURE_EMB_MODEL = data["azure_oai_embedding_model"]

            # Setea variables globales del paquete openai (API <1.0)
            openai.api_key = self.AZURE_OAI_KEY
            openai.api_type = "azure"
            openai.api_base = self.AZURE_OAI_BASE
            openai.api_version = self.AZURE_OAI_API_VERSION

            # ⚠️ Esto es API legacy para Chat; si te migas a openai>=1.x en Azure,
            # deberías usar AzureOpenAI del SDK nuevo.
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
                max_tokens=8000,
                stop=None,
            )

        # -------- GEMINI (opcional; dejé placeholder si lo quieres reactivar) --------
        elif self.model_type == "gemini":
            if _genai is None:
                raise RuntimeError("Falta instalar google-genai (pip install google-genai)")
            self.GEMINI_MODEL = data["gemini_model"]
            self.GEMINI_EMB_MODEL = data["gemini_emb_model"]
            api_key = data.get("gemini_api_key") or os.getenv("GEMINI_API_KEY")
            if not api_key:
                raise RuntimeError("GEMINI_API_KEY no configurado")
            self._gemini = _genai.Client(api_key=api_key)
            self.model = self._gemini

        elif self.model_type == "huggingface":
            self.model = None

        else:
            raise ValueError(f"Invalid model type: {self.model_type}")

    # ---------------- Embeddings con reintento ----------------
    @retry(
    wait=wait_random_exponential(min=1, max=10),     # reintentos más cortos
    stop=stop_after_attempt(5),
    retry=retry_if_exception_type((
        APIConnectionError, APITimeoutError, RateLimitError, APIStatusError
    )),
    reraise=True
)
    def _embed_openai(self, text: str) -> list[float]:
        # Asegure input str o list[str]
        payload = text if isinstance(text, str) else str(text)

        # Log de diagnóstico
        logger.debug(f"[embed] model={self.OPENAI_EMB_MODEL} len={len(payload)}")

        resp = self._openai.embeddings.create(
            model=self.OPENAI_EMB_MODEL,       # "text-embedding-3-large"
            input=payload                      # puede ser list[str] también
            # dimensions=3072,                 # opcional (por defecto 3072 en v3-large)
        )

        # Validaciones defensivas
        if not resp or not getattr(resp, "data", None):
            raise RuntimeError("Embedding response vacío")

        emb = resp.data[0].embedding
        if not emb or not isinstance(emb, list):
            raise RuntimeError("Embedding ausente o con formato inesperado")

        return emb

    @retry(wait=wait_random_exponential(min=1, max=60), stop=stop_after_attempt(10))
    def _embed_azure(self, text: str):
        # Tu rama legacy (openai.Embedding.create)
        import openai
        resp = openai.Embedding.create(input=text, engine=self.AZURE_EMB_MODEL)
        return resp["data"][0]["embedding"]

    @retry(wait=wait_random_exponential(min=1, max=60), stop=stop_after_attempt(10))
    def _embed_gemini(self, text: str):
        cfg = _gtypes.EmbedContentConfig(task_type="SEMANTIC_SIMILARITY") if _gtypes else None
        emb = self._gemini.models.embed_content(model=self.GEMINI_EMB_MODEL, contents=text, config=cfg)
        # Normaliza a lista de floats según versión del SDK
        if isinstance(emb, list):
            return emb
        if hasattr(emb, "embedding"):
            return emb.embedding
        return emb  # último recurso

    def get_embedding(self, text: str, query_id: str):
        """
        Devuelve el embedding del texto según el model_type.
        """
        logger.info(f"[{query_id}] [LLMs] Inside get_embedding()")

        try:
            if self.model_type == "openai":
                vector = self._embed_openai(text)

            elif self.model_type == "azure":
                vector = self._embed_azure(text)

            elif self.model_type == "gemini":
                vector = self._embed_gemini(text)

            else:
                raise ValueError(f"model_type no soportado: {self.model_type}")

            logger.info(f"[{query_id}] [LLMs] Returning embedding vector")
            return vector

        except Exception as e:
            logger.error(f"[{query_id}] [LLMs] Embedding creation failed: {e}", exc_info=True)
            raise RuntimeError("Embedding creation of query failed") from e