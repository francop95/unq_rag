# chroma_connection.py
import logging
from typing import Any, Dict, List, Optional
import os

import numpy as np
import pandas as pd
import chromadb

from contexts.advanced_retrieval import (
    BM25Index,
    CrossEncoderReranker,
    ContextExpander,
    rrf_score,
)

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None

try:
    from models.Retriever_multimodal import Retriever
except Exception:
    Retriever = None  # por si no lo usas


logger = logging.getLogger("app.ChromaConnection")


class ChromaConnection:
    """
    Conector para Chroma persistente (duckdb+parquet).
    - Requiere: chroma_path (carpeta), chroma_collection (nombre).
    - Recibe un embedding precomputado (misma familia que usaste al indexar).
    - Fusiona dense (OpenAI) + BM25 (sparse) + visual (CLIP), re-rankea con
      cross-encoder y expande contexto con chunks vecinos antes de devolver
      un DataFrame con columnas configurables.
    """

    def __init__(self, data: Dict[str, Any]):
        self.is_enabled: bool = bool(data.get("is_chroma_enabled", True))
        self.chroma_path: str = os.path.abspath(data["chroma_path"])
        self.collection_name: str = data["chroma_index_name"]

        # columnas de salida / comportamiento
        self.text_column: str = data["text_column"]
        self.filename_column: str = data["filename_column"]
        self.similarity_column: str = data["similarity_column"]
        self.page_number_column: str = data["page_number_column"]

        ctype = str(data.get("context_text_type", "chunk")).strip().lower()
        self.context_text_type: str = "page" if ctype == "page" else "chunk"

        self.retriever_context_limit: int = int(data.get("retriever_context_limit", 10))
        self.use_retriever: bool = bool(data.get("is_retriever", False))

        # umbral de relevancia (gate para "usar solo los planos" en RetrieverQna)
        self.min_context_similarity_score: float = float(data.get("min_context_similarity_score", 0.0))

        # retrieval avanzado (todos opcionales vía config, degradan a solo-dense si fallan)
        self.use_bm25: bool = bool(data.get("use_bm25", False))
        self.bm25_top_k: int = int(data.get("bm25_top_k", 10))

        self.use_visual_retrieval: bool = bool(data.get("use_visual_retrieval", False))
        self.visual_index_name: str = data.get("visual_index_name", "visual_docs")
        self.clip_model_name: str = data.get("clip_model", "clip-ViT-B-32")
        self.visual_top_k: int = int(data.get("visual_top_k", 5))

        self.use_reranking: bool = bool(data.get("use_reranking", False))
        self.reranker_model_name: str = data.get("reranker_model", "cross-encoder/ms-marco-MiniLM-L-6-v2")
        self.rerank_candidates_top_k: int = int(data.get("rerank_candidates_top_k", 20))

        self.use_context_expansion: bool = bool(data.get("use_context_expansion", False))
        self.chunks_data_path: str = data.get("chunks_data_path", "")

        # Documentos cuyo contenido justifica adjuntar los planos eléctricos al LLM
        self.electric_diagram_related_files: List[str] = data.get("electric_diagram_related_files") or []

        self.client: Optional[chromadb.PersistentClient] = None
        self.collection = None
        self.visual_collection = None
        self.bm25_index: Optional[BM25Index] = None
        self.clip_model = None
        self.reranker: Optional[CrossEncoderReranker] = None
        self.context_expander: Optional[ContextExpander] = None

        self.retriever_cls = Retriever
        self._warm = False

    # ---------- conexión ----------
    def connect(self) -> bool:
        try:
            if not self.is_enabled:
                logger.info("[Chroma] disabled (is_chroma_enabled=False)")
                return False

            if self._warm:
                return True

            logger.info(f"[Chroma] path={self.chroma_path} coll={self.collection_name}")
            self.client = chromadb.PersistentClient(path=self.chroma_path)
            self.collection = self.client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"},
            )

            self._connect_visual()
            self._build_bm25_index()
            self._load_clip_model()
            self._load_reranker()
            self._load_context_expander()

            self._warm = True
            return True
        except Exception as e:
            logger.exception(f"[Chroma] connect error: {e}")
            return False

    def _connect_visual(self):
        if not self.use_visual_retrieval:
            return
        try:
            self.visual_collection = self.client.get_or_create_collection(
                name=self.visual_index_name,
                metadata={"hnsw:space": "cosine"},
            )
            logger.info(f"[Chroma] Colección visual conectada: {self.visual_index_name}")
        except Exception as e:
            logger.warning(f"[Chroma] No se pudo conectar colección visual: {e}")
            self.visual_collection = None

    def _build_bm25_index(self):
        if not self.use_bm25:
            return
        try:
            all_docs = self.collection.get(include=["documents", "metadatas"])
            doc_ids = all_docs.get("ids", [])
            documents = all_docs.get("documents", [])
            metadatas = all_docs.get("metadatas", [])
            if not documents:
                logger.warning("[Chroma] Colección textual vacía: BM25 no se construye todavía")
                return
            self.bm25_index = BM25Index()
            self.bm25_index.build(doc_ids, documents, metadatas)
        except Exception as e:
            logger.warning(f"[Chroma] No se pudo construir índice BM25: {e}")
            self.bm25_index = None

    def _load_clip_model(self):
        if not self.use_visual_retrieval or SentenceTransformer is None:
            return
        try:
            logger.info(f"[Chroma] Cargando modelo CLIP: {self.clip_model_name}")
            self.clip_model = SentenceTransformer(self.clip_model_name)
        except Exception as e:
            logger.warning(f"[Chroma] No se pudo cargar CLIP: {e}")
            self.clip_model = None

    def _load_reranker(self):
        if not self.use_reranking:
            return
        try:
            self.reranker = CrossEncoderReranker(model_name=self.reranker_model_name)
        except Exception as e:
            logger.warning(f"[Chroma] No se pudo cargar cross-encoder: {e}")
            self.reranker = None

    def _load_context_expander(self):
        if not self.use_context_expansion:
            return
        try:
            self.context_expander = ContextExpander(window_size=1)
            self.context_expander.load_latest_per_document(self.chunks_data_path)
        except Exception as e:
            logger.warning(f"[Chroma] No se pudo cargar context expander: {e}")
            self.context_expander = None

    # ---------- búsqueda ----------
    def search_vectors(self, data: Dict[str, Any], query_vector, top_k: int = 10) -> pd.DataFrame:
        """
        `data` aquí SÍ es el diccionario de la request (viene del flujo principal),
        e incluye cosas como query_id, flags, etc.
        """
        qid = data.get("query_id", "no-qid")
        try:
            if not self.connect():
                logger.warning(f"[{qid}][Chroma] connect() returned False")
                return pd.DataFrame()

            query_vector = self._to_list(query_vector)
            query_text = (data.get("updated_query") if data.get("is_followup") else data.get("query")) or ""

            # 1) Dense (texto, OpenAI embeddings)
            logger.info(f"[{qid}][Chroma] query dense top_k={top_k}")
            dense_res = self.collection.query(
                query_embeddings=[query_vector],
                n_results=top_k,
                include=["documents", "metadatas", "distances"],
            )
            candidates = self._chroma_res_to_candidates(dense_res)
            for rank, c in enumerate(candidates, start=1):
                c["dense_similarity"] = c["similarity"]
                c["dense_rank"] = rank

            # 2) BM25 (sparse) — se fusiona por doc_id
            if self.use_bm25 and self.bm25_index is not None and query_text:
                bm25_hits = self.bm25_index.search(query_text, top_k=self.bm25_top_k)
                by_id = {c["doc_id"]: c for c in candidates}
                for rank, (doc_id, score, text, meta) in enumerate(bm25_hits, start=1):
                    if doc_id in by_id:
                        by_id[doc_id]["bm25_rank"] = rank
                    else:
                        new_c = {
                            "doc_id": doc_id, "text": text, "metadata": meta or {},
                            "similarity": 0.0, "dense_similarity": 0.0, "bm25_rank": rank,
                        }
                        candidates.append(new_c)
                        by_id[doc_id] = new_c

            # 3) Visual (CLIP) — resultados aparte, informativos (no participan del
            #    gate de relevancia textual, pero sí del fusionado/reranking si hay texto)
            if self.use_visual_retrieval and self.visual_collection is not None and self.clip_model is not None and query_text:
                try:
                    query_visual_emb = self.clip_model.encode(
                        query_text, convert_to_tensor=False, show_progress_bar=False
                    )
                    visual_res = self.visual_collection.query(
                        query_embeddings=[query_visual_emb.tolist()],
                        n_results=self.visual_top_k,
                        include=["documents", "metadatas", "distances"],
                    )
                    visual_candidates = self._chroma_res_to_candidates(visual_res)
                    by_id = {c["doc_id"]: c for c in candidates}
                    for rank, vc in enumerate(visual_candidates, start=1):
                        vc["dense_similarity"] = 0.0  # es similitud visual, no textual
                        if vc["doc_id"] in by_id:
                            by_id[vc["doc_id"]]["visual_rank"] = rank
                        else:
                            vc["visual_rank"] = rank
                            candidates.append(vc)
                except Exception as e:
                    logger.warning(f"[{qid}][Chroma] Error en búsqueda visual: {e}")

            if not candidates:
                logger.info(f"[{qid}][Chroma] sin resultados")
                return pd.DataFrame()

            # 4) Fusión RRF (rank de cada lista, no las escalas de score originales,
            #    que no son comparables entre dense/BM25/visual)
            for c in candidates:
                score = 0.0
                if "dense_rank" in c:
                    score += rrf_score(c["dense_rank"])
                if "bm25_rank" in c:
                    score += rrf_score(c["bm25_rank"])
                if "visual_rank" in c:
                    score += rrf_score(c["visual_rank"])
                c["fused_score"] = score

            candidates.sort(key=lambda c: c["fused_score"], reverse=True)

            # 5) Reranking con cross-encoder sobre el pool fusionado
            if self.use_reranking and self.reranker is not None:
                candidates = self.reranker.rerank(
                    query_text, candidates, text_key="text", top_k=self.rerank_candidates_top_k
                )

            # 6) Context expansion (chunks vecinos vía prev/next_chunk_id)
            if self.use_context_expansion and self.context_expander is not None:
                for c in candidates:
                    meta = c.get("metadata", {})
                    prev_text, next_text = self.context_expander.expand(
                        meta.get("file_name"), meta.get("page_num"), meta.get("chunk_id")
                    )
                    if prev_text or next_text:
                        parts = []
                        if prev_text:
                            parts.append(f"[CONTEXTO PREVIO]\n{prev_text}")
                        parts.append(c["text"])
                        if next_text:
                            parts.append(f"[CONTEXTO SIGUIENTE]\n{next_text}")
                        c["text"] = "\n\n".join(parts)

            # 7) Gate de relevancia real: al menos un candidato con similitud DENSA
            #    (no BM25/visual, que no tienen escala comparable) por encima del
            #    umbral configurado. Se descartan filas que no lo superan.
            relevant = [c for c in candidates if c.get("dense_similarity", 0.0) >= self.min_context_similarity_score]
            if not relevant:
                logger.info(
                    f"[{qid}][Chroma] Ningún candidato superó el umbral de relevancia "
                    f"({self.min_context_similarity_score}) -> se descarta el contexto"
                )
                # Sin contexto relevante: se deja "attach_electric_diagrams" sin
                # setear, así ModelCompletion usa su default (True) -> los planos
                # quedan como único fallback, que es justamente el caso pedido.
                return pd.DataFrame()

            # Planos dinámicos: se adjuntan si el contexto relevante viene de
            # documentos eléctricos/de cableado, o si no se pudo determinar la
            # fuente. Para preguntas de proceso/térmicas respondidas desde otros
            # documentos (ej. la Tesis del secadero), no hace falta adjuntarlos.
            relevant_files = {c.get("metadata", {}).get("file_name") for c in relevant}
            data["attach_electric_diagrams"] = bool(
                relevant_files & set(self.electric_diagram_related_files)
            ) if self.electric_diagram_related_files else True
            logger.info(
                f"[{qid}][Chroma] Documentos relevantes: {relevant_files} -> "
                f"attach_electric_diagrams={data['attach_electric_diagrams']}"
            )

            return self._candidates_to_df(data, top_k, relevant)
        except Exception as e:
            logger.exception(f"[{qid}][Chroma] search error: {e}")
            return pd.DataFrame()

    # ---------- helpers ----------
    @staticmethod
    def _to_list(query_vector) -> List[float]:
        if isinstance(query_vector, np.ndarray):
            return query_vector.astype(float).tolist()
        if isinstance(query_vector, (list, tuple)):
            return [float(x) for x in query_vector]
        raise ValueError("query_vector debe ser list[float] o np.ndarray")

    @staticmethod
    def _chroma_res_to_candidates(res: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not res or not res.get("ids") or not res["ids"][0]:
            return []
        out = []
        for doc_id, doc, meta, dist in zip(
            res["ids"][0], res.get("documents", [[]])[0],
            res.get("metadatas", [[]])[0], res.get("distances", [[]])[0]
        ):
            out.append({
                "doc_id": str(doc_id),
                "text": str(doc or ""),
                "metadata": meta or {},
                "similarity": 1.0 - float(dist),
            })
        return out

    def _candidates_to_df(self, data: Dict[str, Any], n: int, candidates: List[Dict[str, Any]]) -> pd.DataFrame:
        qid = data.get("query_id", "no-qid")

        rows = []
        for i, c in enumerate(candidates):
            meta = c.get("metadata", {})
            # Score mostrado al usuario/log: rerank_score si hubo reranking
            # (escala distinta a similitud coseno, pero es la señal de calidad
            # real usada para ordenar), si no la similitud densa/fusionada.
            display_score = c.get("rerank_score", c.get("dense_similarity", c.get("fused_score", 0.0)))
            # media_path: copia canónica en Ingestion/data/media/ (tablas e imágenes/diagramas).
            # image_path: crop original de la página (más específico para diagramas). Ambos
            # opcionales según el content_type del chunk (ver DualIndexer en Ingestion).
            rows.append([
                i, c["doc_id"], c["text"],
                meta.get("file_name"), meta.get("page_num"), meta.get("chunk_id"),
                display_score,
                meta.get("content_type"), meta.get("media_path"), meta.get("image_path"),
            ])

        base = pd.DataFrame(rows, columns=[
            "query_idx", "doc_id", "document", "file_name", "page_num", "chunk_id", "similarity",
            "content_type", "media_path", "image_path",
        ])

        # Retriever LLM opcional (selección adicional de secciones relevantes)
        if self.use_retriever and self.retriever_cls is not None:
            try:
                d = dict(data)
                d["data_df"] = base
                # Esta llamada solo elige secciones relevantes por texto; no
                # necesita ver los planos (ahorra la subida de los PDFs acá).
                d["attach_electric_diagrams"] = False

                retriever_obj = self.retriever_cls(d)
                d = retriever_obj.get_relevant_sections(d)

                if d.get("retriever_output") and (not d["retriever_out_df"].empty):
                    base = d["retriever_out_df"]
            except Exception as e:
                logger.exception(f"[{qid}][Chroma] retriever error: {e}")

        media_cols = ["content_type", "media_path", "image_path"]

        if self.context_text_type == "page":
            def _non_null_list(x):
                vals = [v for v in x if v not in (None, "", "nan")]
                return vals

            agg = (
                base.groupby(["file_name", "page_num"], as_index=False)
                    .agg(
                        pid=("doc_id", lambda x: "_###_".join(map(str, x))),
                        all_text=("document", lambda x: "_#####_".join(map(str, x))),
                        sim=("similarity", "max"),
                        media_paths=("media_path", _non_null_list),
                        image_paths=("image_path", _non_null_list),
                        content_types=("content_type", _non_null_list),
                    )
                    .sort_values("sim", ascending=False)
            )
            agg[self.text_column] = agg["all_text"]
            out = agg.rename(columns={
                "pid": "PID",
                "file_name": self.filename_column,
                "page_num": self.page_number_column,
                "sim": self.similarity_column,
            })[["PID", self.filename_column, self.page_number_column, self.text_column,
                self.similarity_column, "content_types", "media_paths", "image_paths"]]
        else:
            base = base.sort_values("similarity", ascending=False)
            out = base.rename(columns={
                "doc_id": "PID",
                "file_name": self.filename_column,
                "page_num": self.page_number_column,
                "document": self.text_column,
                "similarity": self.similarity_column,
            })[["PID", self.filename_column, self.page_number_column, self.text_column,
                self.similarity_column] + media_cols]

        logger.info(f"[{qid}][Chroma] returned {len(out)} rows")
        return out.head(n).reset_index(drop=True)
