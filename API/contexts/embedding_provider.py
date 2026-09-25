"""
Proveedor de embeddings: OpenAI (default) o AWS Bedrock.

Por qué existe
--------------
El modelo de embeddings se usa en DOS lugares —la ingesta, que embebe los
chunks, y la API, que embebe la consulta— y tienen que ser el MISMO. Si no
coinciden, los vectores viven en espacios distintos y el retrieval no degrada:
deja de funcionar por completo, devolviendo resultados arbitrarios sin ningún
error visible.

Este módulo centraliza esa elección para que sea una sola variable en los dos
lados, en vez de dos lugares del código que hay que acordarse de tocar juntos.

Está duplicado en API/contexts/ e Ingestion/src/task_utils/ a propósito, por la
misma razón que advanced_retrieval.py: los dos servicios se despliegan por
separado, cada uno con su entorno.

Cambiar de proveedor obliga a REINDEXAR
---------------------------------------
No es un cambio en caliente. El índice queda ligado al modelo que lo generó,
así que el orden es: construir un índice nuevo con el proveedor nuevo, medirlo
contra el actual con eval/run_eval.py, y recién entonces apuntar la API.

Modelos disponibles en Bedrock (verificado en eu-west-1):
    cohere.embed-v4:0               el más nuevo; multimodal (texto + imagen)
    cohere.embed-multilingual-v3    1024 dims, buen español
    amazon.titan-embed-text-v2:0    1024 dims (configurable)
"""

import logging
import os
from typing import List, Optional

logger = logging.getLogger("app.EmbeddingProvider")

# Dimensiones conocidas, para poder detectar un desajuste contra el índice
# antes de que se traduzca en resultados sin sentido.
DIMENSIONES = {
    "text-embedding-3-large": 3072,
    "text-embedding-3-small": 1536,
    "cohere.embed-multilingual-v3": 1024,
    "cohere.embed-english-v3": 1024,
    "amazon.titan-embed-text-v2:0": 1024,
}


class EmbeddingProvider:
    """
    Interfaz mínima común: `embed(texts, input_type) -> List[List[float]]`.

    `input_type` solo lo usan los modelos de Cohere, que distinguen entre
    embeber un documento y embeber una consulta; ignorarlo cuesta recall, así
    que se propaga aunque OpenAI no lo use.
    """

    def __init__(self, provider: str, model: str, region: Optional[str] = None,
                 openai_client=None, output_dimension: Optional[int] = None):
        self.provider = (provider or "openai").strip().lower()
        self.model = model
        self.region = region or os.getenv("AWS_REGION") or "eu-west-1"
        self.output_dimension = output_dimension
        self._openai = openai_client
        self._bedrock = None

        if self.provider not in ("openai", "bedrock"):
            raise ValueError(f"proveedor de embeddings desconocido: {self.provider!r}")

        if self.provider == "bedrock":
            try:
                import boto3
            except ImportError as e:
                raise ImportError(
                    "El proveedor 'bedrock' necesita boto3 (pip install boto3)"
                ) from e
            self._bedrock = boto3.client("bedrock-runtime", region_name=self.region)
            logger.info(f"[Embeddings] Bedrock {self.model} en {self.region}")
        else:
            logger.info(f"[Embeddings] OpenAI {self.model}")

    # ------------------------------------------------------------------ API
    def embed(self, texts: List[str], input_type: str = "search_document") -> List[List[float]]:
        if not texts:
            return []
        if self.provider == "bedrock":
            return self._embed_bedrock(texts, input_type)
        return self._embed_openai(texts)

    def dimension(self) -> Optional[int]:
        """Dimensión esperada, si se conoce. Sirve para validar contra el índice."""
        if self.output_dimension:
            return self.output_dimension
        return DIMENSIONES.get(self.model)

    # -------------------------------------------------------------- OpenAI
    def _embed_openai(self, texts: List[str]) -> List[List[float]]:
        if self._openai is None:
            from openai import OpenAI
            self._openai = OpenAI(
                api_key=os.getenv("OPENAI_API_KEY") or os.getenv("openai_key")
            )
        resp = self._openai.embeddings.create(model=self.model, input=texts)
        return [d.embedding for d in resp.data]

    # ------------------------------------------------------------- Bedrock
    def _embed_bedrock(self, texts: List[str], input_type: str) -> List[List[float]]:
        import json

        if self.model.startswith("cohere."):
            cuerpo = {"texts": texts, "input_type": input_type}
            if self.output_dimension:
                cuerpo["output_dimension"] = self.output_dimension
            datos = self._invoke(json.dumps(cuerpo))
            emb = datos.get("embeddings")
            # embed-v4 devuelve {"embeddings": {"float": [[...]]}}; v3 devuelve
            # {"embeddings": [[...]]}. Se aceptan las dos formas.
            if isinstance(emb, dict):
                emb = emb.get("float") or next(iter(emb.values()))
            return list(emb or [])

        if self.model.startswith("amazon.titan-embed"):
            # Titan embebe de a un texto por llamada.
            salida = []
            for t in texts:
                cuerpo = {"inputText": t}
                if self.output_dimension:
                    cuerpo["dimensions"] = self.output_dimension
                salida.append(self._invoke(json.dumps(cuerpo))["embedding"])
            return salida

        raise ValueError(f"modelo de Bedrock no soportado: {self.model!r}")

    # --------------------------------------------------------------- imágenes
    # Modelos que embeben imágenes en el MISMO espacio que el texto. Es la
    # propiedad que importa: permite que una consulta escrita recupere una
    # figura directamente, sin el índice visual separado que hace falta con
    # CLIP, cuyo espacio de 512 dimensiones no se puede comparar con el textual.
    MODELOS_MULTIMODALES = ("cohere.embed-v4",)

    def soporta_imagenes(self) -> bool:
        return self.provider == "bedrock" and any(
            self.model.startswith(p) for p in self.MODELOS_MULTIMODALES
        )

    def embed_images(self, image_paths: List[str]) -> List[List[float]]:
        """
        Un vector por imagen, en el mismo espacio que `embed()`.

        Bedrock las recibe de a una: el campo `images` acepta una lista, pero
        embed-v4 rechaza más de un elemento por llamada.
        """
        if not self.soporta_imagenes():
            raise ValueError(
                f"{self.model!r} no embebe imágenes; usar un modelo multimodal "
                f"({', '.join(self.MODELOS_MULTIMODALES)}) o CLIP."
            )

        import base64
        import json
        import mimetypes

        salida: List[List[float]] = []
        for ruta in image_paths:
            tipo = mimetypes.guess_type(ruta)[0] or "image/png"
            with open(ruta, "rb") as fh:
                uri = f"data:{tipo};base64," + base64.b64encode(fh.read()).decode()
            cuerpo = {"images": [uri], "input_type": "image"}
            if self.output_dimension:
                cuerpo["output_dimension"] = self.output_dimension
            datos = self._invoke(json.dumps(cuerpo))
            emb = datos.get("embeddings")
            if isinstance(emb, dict):
                emb = emb.get("float") or next(iter(emb.values()))
            salida.append(list(emb)[0] if emb else [])
        return salida

    def _invoke(self, body: str) -> dict:
        import json

        r = self._bedrock.invoke_model(modelId=self.model, body=body)
        return json.loads(r["body"].read())


def from_config(data: dict, openai_client=None) -> EmbeddingProvider:
    """Construye el proveedor desde el dict de configuración de la API."""
    return EmbeddingProvider(
        provider=data.get("embedding_provider", "openai"),
        model=(data.get("embedding_model_name")
               or data.get("openai_emb_model")
               or "text-embedding-3-large"),
        region=data.get("embedding_region"),
        openai_client=openai_client,
        output_dimension=data.get("embedding_output_dimension") or None,
    )
