"""
Retrieval avanzado para la API de producción: BM25 (sparse), reranking con
cross-encoder y context expansion (chunks vecinos vía prev_chunk_id/next_chunk_id).

Adaptado de Ingestion/src/task_utils/advanced_retrieval.py. Se duplica en vez de
importarse entre repos porque API/ e Ingestion/ son servicios desplegables por
separado, cada uno con su propio entorno/dependencias.
"""

import os
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("app.AdvancedRetrieval")

try:
    from sentence_transformers import CrossEncoder
    CROSSENCODER_AVAILABLE = True
except ImportError:
    CROSSENCODER_AVAILABLE = False
    logger.warning("sentence-transformers no disponible: reranking deshabilitado")

try:
    from rank_bm25 import BM25Okapi
    BM25_AVAILABLE = True
except ImportError:
    BM25_AVAILABLE = False
    logger.warning("rank-bm25 no disponible: BM25 deshabilitado")


class CrossEncoderReranker:
    """Reordena una lista de (doc_id, texto, metadata) por relevancia real a la query."""

    def __init__(self, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"):
        if not CROSSENCODER_AVAILABLE:
            raise ImportError("sentence-transformers no disponible para cross-encoder")
        logger.info(f"[AdvancedRetrieval] Cargando cross-encoder: {model_name}")
        self.model = CrossEncoder(model_name)
        logger.info("[AdvancedRetrieval] Cross-encoder listo")

    def rerank(
        self,
        query: str,
        candidates: List[Dict[str, Any]],
        text_key: str = "text",
        top_k: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        candidates: lista de dicts con al menos `text_key`. Devuelve la misma
        lista (copias) con "rerank_score" agregado, ordenada descendente y
        truncada a top_k.
        """
        if not candidates:
            return []

        pairs = [(query, c.get(text_key, "") or "") for c in candidates]
        scores = self.model.predict(pairs)

        for c, score in zip(candidates, scores):
            c["rerank_score"] = float(score)

        ranked = sorted(candidates, key=lambda c: c["rerank_score"], reverse=True)
        return ranked[:top_k]


class BM25Index:
    """Índice BM25 en memoria sobre el corpus textual ya indexado en Chroma."""

    def __init__(self):
        if not BM25_AVAILABLE:
            raise ImportError("rank-bm25 no disponible")
        self.doc_ids: List[str] = []
        self.corpus: List[str] = []
        self.metadatas: List[Dict[str, Any]] = []
        self.bm25: Optional["BM25Okapi"] = None

    def build(self, doc_ids: List[str], documents: List[str], metadatas: List[Dict[str, Any]]):
        self.doc_ids = doc_ids
        self.corpus = documents
        self.metadatas = metadatas
        tokenized = [(doc or "").lower().split() for doc in documents]
        self.bm25 = BM25Okapi(tokenized)
        logger.info(f"[AdvancedRetrieval] Índice BM25 construido: {len(documents)} documentos")

    def search(self, query: str, top_k: int = 10) -> List[Tuple[str, float, str, Dict[str, Any]]]:
        """Devuelve [(doc_id, score, texto, metadata), ...] ordenado por score desc."""
        if self.bm25 is None:
            return []
        tokenized_query = query.lower().split()
        scores = self.bm25.get_scores(tokenized_query)
        results = list(zip(self.doc_ids, scores, self.corpus, self.metadatas))
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]


class ContextExpander:
    """
    Agrega el texto de chunks vecinos (prev_chunk_id/next_chunk_id) al contenido
    de un chunk. Sin efecto (no rompe nada) si esos campos vienen vacíos, que es
    el caso hasta que se re-ingiera con el fix que los popula.
    """

    def __init__(self, window_size: int = 1):
        self.window_size = window_size
        self._chunk_cache: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def _composite_chunk_id(chunk: Dict[str, Any]) -> str:
        return f"{chunk.get('file_name','')}_{chunk.get('page_num','')}_{chunk.get('chunk_id','')}"

    def load_chunks_from_directory(self, chunks_dir: str):
        chunks_dir = Path(chunks_dir)
        if not chunks_dir.is_dir():
            return
        for page_folder in chunks_dir.iterdir():
            if not page_folder.is_dir():
                continue
            for json_file in page_folder.glob("*.json"):
                try:
                    with open(json_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    chunks = data if isinstance(data, list) else [data]
                    for chunk in chunks:
                        if chunk.get("chunk_id"):
                            self._chunk_cache[self._composite_chunk_id(chunk)] = chunk
                except Exception as e:
                    logger.warning(f"[AdvancedRetrieval] Error cargando {json_file}: {e}")

    def load_latest_per_document(self, chunks_base_path: str):
        """Carga solo la corrida más reciente de cada documento en chunks_base_path."""
        if not os.path.isdir(chunks_base_path):
            logger.warning(f"[AdvancedRetrieval] chunks_base_path no existe: {chunks_base_path}")
            return
        for doc_name in sorted(os.listdir(chunks_base_path)):
            doc_path = os.path.join(chunks_base_path, doc_name)
            if not os.path.isdir(doc_path):
                continue
            timestamps = sorted(
                d for d in os.listdir(doc_path) if os.path.isdir(os.path.join(doc_path, d))
            )
            if not timestamps:
                continue
            self.load_chunks_from_directory(os.path.join(doc_path, timestamps[-1]))
        logger.info(f"[AdvancedRetrieval] Context expander: {len(self._chunk_cache)} chunks cacheados")

    def expand(self, file_name: str, page_num: Any, chunk_id: str) -> Tuple[str, str]:
        """Devuelve (prev_text, next_text) para el chunk identificado, o ("","") si no hay vecinos."""
        composite_id = f"{file_name}_{page_num}_{chunk_id}"
        chunk = self._chunk_cache.get(composite_id)
        if not chunk:
            return "", ""

        prev_text, next_text = "", ""
        prev_id = chunk.get("prev_chunk_id")
        if prev_id and prev_id in self._chunk_cache:
            prev_text = str(self._chunk_cache[prev_id].get("original_chunk", ""))

        next_id = chunk.get("next_chunk_id")
        if next_id and next_id in self._chunk_cache:
            next_text = str(self._chunk_cache[next_id].get("original_chunk", ""))

        return prev_text, next_text


def rrf_score(rank: int, k: int = 60) -> float:
    """Reciprocal Rank Fusion: score de un resultado según su posición (1-based) en una lista."""
    return 1.0 / (k + rank)
