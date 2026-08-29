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

    # Cuánto más que top_k se le pide al índice denso para compensar que es
    # multi-vector: varios vectores (contenido + preguntas sintéticas) apuntan al
    # mismo contenido y colapsan en un único resultado.
    DENSE_OVERFETCH_FACTOR = 4

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
        # Bandera propia (LLM_SECTION_SELECTOR_ENABLED), NO "is_retriever": esa
        # además elige el camino de respuesta (RetrieverQna vs GPTQna) en
        # QuestionAnswer, así que apagarla acá se llevaría puestas las fuentes,
        # la media y los planos de la respuesta. Ver Configuration.
        self.use_retriever: bool = bool(data.get("use_llm_section_selector", False))

        # umbral de relevancia (gate para "usar solo los planos" en RetrieverQna)
        self.min_context_similarity_score: float = float(data.get("min_context_similarity_score", 0.0))

        # retrieval avanzado (todos opcionales vía config, degradan a solo-dense si fallan)
        # Ver el comentario del gate de relevancia y FUSION_* en Configuration: sin
        # estas dos, BM25 y el retrieval visual son inertes por construcción.
        self.fusion_admits_sparse: bool = bool(data.get("fusion_admits_sparse", False))
        self.fusion_decides_order: bool = bool(data.get("fusion_decides_order", False))

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
            self._build_content_metadata_index()
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

    def _build_content_metadata_index(self):
        """
        Mapa (file_name, page_num, chunk_id) -> metadata de los chunks de CONTENIDO.

        Hace falta porque el índice es multi-vector: además del chunk de contenido se
        indexa un vector por cada pregunta sintética que ese chunk responde, y esos
        vectores guardan el TEXTO del padre pero su propia metadata
        (`content_type: synthetic_question`, sin `media_path`). Como la mayoría de los
        chunks se recupera a través de una de sus preguntas, sin este mapa la fila que
        llega al frontend dice content_type "synthetic_question" y pierde el
        `media_path`: la imagen o la tabla del chunk se descarta en silencio y nunca
        se muestra, aunque el chunk sí haya sido recuperado.

        Se arma una sola vez al conectar (no por consulta) y es independiente de BM25.
        """
        self.content_by_id: Dict[Any, Dict[str, Any]] = {}
        try:
            all_docs = self.collection.get(include=["metadatas", "documents"])
            metadatas = all_docs.get("metadatas", []) or []
            documents = all_docs.get("documents", []) or []
            for meta, doc in zip(metadatas, documents):
                meta = meta or {}
                if meta.get("content_type") == "synthetic_question":
                    continue
                chunk_id = meta.get("chunk_id")
                if not chunk_id:
                    continue
                key = (meta.get("file_name"), str(meta.get("page_num")), str(chunk_id))
                self.content_by_id[key] = {"metadata": meta, "document": doc or ""}
            logger.info(
                f"[Chroma] Contenido indexado por chunk_id: {len(self.content_by_id)} "
                f"chunks (para recuperar media/texto de los vectores de preguntas)"
            )
        except Exception as e:
            logger.warning(f"[Chroma] No se pudo indexar el contenido por chunk_id: {e}")

    @classmethod
    def _drop_hallucinated_ocr_questions(cls, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Descarta los vectores de pregunta sintética que cuelgan de la faceta `_ocr` de
        una figura.

        Ingestion indexa cada figura en tres facetas y le genera ~5 preguntas
        sintéticas a cada una. Para la faceta `_ocr` eso sale mal: el OCR del recorte
        es ilegible en el 88% de los casos (medido: 69 de 78 chunks con menos del 20%
        de palabras reales, del estilo "Texto extraído: as a =Q ada 218"), así que el
        LLM no tuvo contenido del cual partir y generó las preguntas a partir del
        rótulo "Diagrama eléctrico - Página N". El resultado son plantillas genéricas,
        repetidas TEXTUALMENTE entre páginas distintas:

            "¿Qué información proporciona el diagrama eléctrico sobre el variador?"
                -> páginas 66, 41 y 89, las tres con similitud 0.725
            "¿Qué símbolos eléctricos se utilizan en el diagrama del variador?"
                -> páginas 58, 74, 100 y 107, las cuatro en 0.693

        Como el texto es idéntico, el embedding y el score también, y el desempate
        entre páginas queda arbitrario. Son 335 vectores (10% del índice) que matchean
        por igual cualquier consulta eléctrica genérica: para "mostrame el diagrama de
        conexionado de los bornes de control", 21 de los 25 mejores eran facetas OCR y
        la PORTADA del manual (pág. 1, un collage de fotos industriales) le ganaba al
        diagrama real de bornes de control (pág. 27).

        Se descartan solo las PREGUNTAS, no los chunks `_ocr` en sí: el vector del
        texto propio es inofensivo (siendo ilegible no matchea nada) y las facetas
        `_structured` y `_visual` conservan sus preguntas, que sí están fundamentadas
        en la descripción real de la figura.
        """
        kept = [
            c for c in candidates
            if not (
                (c.get("metadata") or {}).get("content_type") == "synthetic_question"
                and str((c.get("metadata") or {}).get("parent_chunk_id") or "").endswith("_ocr")
            )
        ]
        dropped = len(candidates) - len(kept)
        if dropped:
            logger.info(
                f"[Chroma] Preguntas sintéticas de facetas OCR descartadas: {dropped} "
                f"(no están fundamentadas en el contenido de la figura)"
            )
        return kept

    def _restore_parent_metadata(self, candidates: List[Dict[str, Any]]) -> None:
        """
        Repara in-place los candidatos que son vectores de pregunta sintética:
        les pone la metadata de su chunk de CONTENIDO, conservando la pregunta que
        matcheó. Sin esto se pierden `content_type`, `media_path`/`image_path` y el
        `chunk_id` real del contenido recuperado (ver _build_content_metadata_index).

        Además, cuando el contenido es una faceta de figura que no es la visual, se
        promueve a la faceta `_visual` de la misma figura: es la que lleva la imagen,
        y su texto es la descripción en vez del OCR ilegible del recorte. El caso
        frecuente es el `_ocr`, cuyas preguntas sintéticas se generaron a partir de
        la descripción y por eso rankean bien, arrastrando al contexto un chunk sin
        imagen y con texto inservible.
        """
        if not getattr(self, "content_by_id", None):
            return

        restored = promoted = 0
        for cand in candidates:
            meta = cand.get("metadata") or {}
            file_name = meta.get("file_name")
            page = str(meta.get("page_num"))
            is_question = meta.get("content_type") == "synthetic_question"

            content_id = meta.get("parent_chunk_id") or meta.get("chunk_id")
            if not content_id:
                continue

            # Promoción a la faceta visual de la figura, si existe.
            visual_entry = None
            figure_base = self._figure_base_id(content_id)
            if figure_base and not str(content_id).endswith("_visual"):
                visual_entry = self.content_by_id.get(
                    (file_name, page, f"{figure_base}_visual")
                )

            entry = visual_entry or (
                self.content_by_id.get((file_name, page, str(content_id))) if is_question else None
            )
            if not entry:
                continue

            merged = dict(entry["metadata"])
            # La pregunta que matcheó no está en la metadata del contenido y es la
            # señal de por qué se recuperó el chunk (la usa _collapse_by_parent).
            if meta.get("question"):
                merged["question"] = meta["question"]
            cand["metadata"] = merged

            if visual_entry:
                # El texto del candidato es el de la faceta vieja (OCR o descripción
                # duplicada); se reemplaza por el de la visual, que es la descripción.
                if entry["document"]:
                    cand["text"] = entry["document"]
                promoted += 1
            else:
                restored += 1

        if restored or promoted:
            logger.info(
                f"[Chroma] Contenido reasociado: {restored} candidatos recuperados vía "
                f"pregunta sintética, {promoted} facetas de figura promovidas a su "
                f"chunk visual (con imagen)"
            )

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

            # Los vectores de preguntas sintéticas almacenan el texto de su chunk
            # padre, así que incluirlos repetiría cada contenido 4-5 veces en el
            # corpus: eso distorsiona el IDF de BM25 (un término frecuente parece
            # más raro/común de lo que es) y le da masa extra al contenido que
            # simplemente generó más preguntas. Aportan al retrieval DENSO, no al
            # léxico, así que se excluyen de BM25.
            filtered = [
                (doc_id, doc, meta)
                for doc_id, doc, meta in zip(doc_ids, documents, metadatas)
                if (meta or {}).get("content_type") != "synthetic_question"
            ]
            if not filtered:
                logger.warning("[Chroma] Sin documentos para BM25 tras filtrar preguntas")
                return

            skipped = len(documents) - len(filtered)
            if skipped:
                logger.info(
                    f"[Chroma] BM25: {len(filtered)} documentos "
                    f"({skipped} vectores de preguntas excluidos del corpus léxico)"
                )

            # El texto que se indexa en BM25 se ENRIQUECE con campos que ayudan a la
            # búsqueda léxica exacta y que no están en el `document`:
            #  - searchable_text: celdas de tabla aplanadas (sin pipes ni guiones de
            #    markdown). Es lo que permite encontrar "480V" o "22B-D010N104", que
            #    en el markdown quedan pegados a la sintaxis de la tabla.
            #  - question: las preguntas que ese chunk responde aportan sinónimos en
            #    el lenguaje del usuario ("no arranca") frente al del manual.
            # El vector denso NO cambia: esto es solo el corpus léxico.
            bm25_ids, bm25_docs, bm25_metas, bm25_scoring = [], [], [], []
            enriched_count = 0
            for doc_id, doc, meta in filtered:
                meta = meta or {}
                extras = [e for e in (meta.get("searchable_text"),) if e and str(e).strip()]
                if extras:
                    enriched_count += 1
                bm25_ids.append(doc_id)
                bm25_docs.append(doc or "")          # lo que se devuelve como contenido
                bm25_scoring.append(" ".join([doc or ""] + [str(e) for e in extras]).strip())
                bm25_metas.append(meta)

            if enriched_count:
                logger.info(
                    f"[Chroma] BM25: {enriched_count} tablas puntuadas también por "
                    f"su searchable_text (celdas aplanadas)"
                )

            self.bm25_index = BM25Index()
            self.bm25_index.build(
                bm25_ids, bm25_docs, bm25_metas, scoring_texts=bm25_scoring
            )
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
            # Se pide MÁS de top_k a propósito: el índice es multi-vector (cada
            # contenido tiene su vector + uno por cada pregunta sintética que
            # responde), y esos vectores de pregunta dominan el top-k para
            # consultas formuladas como pregunta. Medido: 6 candidatos densos
            # colapsaban a solo 4 contenidos distintos. Sin el over-fetch, la
            # variedad real del contexto que llega al LLM queda a la mitad.
            dense_fetch = top_k * self.DENSE_OVERFETCH_FACTOR
            logger.info(f"[{qid}][Chroma] query dense top_k={top_k} (fetch={dense_fetch})")
            dense_res = self.collection.query(
                query_embeddings=[query_vector],
                n_results=dense_fetch,
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

            # 4.4) Sacar los vectores de pregunta que no están fundamentados en el
            #      contenido de su figura. Tiene que ir ANTES de la promoción a la
            #      faceta visual: después ya no se distingue de qué faceta venían.
            candidates = self._drop_hallucinated_ocr_questions(candidates)

            # 4.5) Recuperar la metadata del chunk de contenido en los candidatos que
            #      son vectores de pregunta sintética. Va acá, antes de todo lo que
            #      mira content_type/media_path (dedup por contención, armado del
            #      DataFrame, media que se manda al frontend).
            self._restore_parent_metadata(candidates)

            # 5) Reranking con cross-encoder sobre el pool fusionado
            if self.use_reranking and self.reranker is not None:
                candidates = self.reranker.rerank(
                    query_text, candidates, text_key="text", top_k=self.rerank_candidates_top_k
                )

            # 6) Context expansion (chunks vecinos vía prev/next_chunk_id)
            if self.use_context_expansion and self.context_expander is not None:
                # Si un vecino YA está entre los candidatos por mérito propio, no se
                # inyecta como contexto: si no, el mismo chunk viaja dos veces al LLM
                # (una como resultado y otra dentro del "[CONTEXTO PREVIO]" de su
                # vecino) y en la UI se ven dos fuentes aparentemente idénticas.
                #
                # Se indexa por parent_chunk_id, NO por chunk_id: un contenido puede
                # estar presente a través de uno de sus vectores de pregunta
                # sintética (chunk_5_q1), cuyo chunk_id no coincide con el del
                # contenido (chunk_5) ni existe en el caché del expander.
                present_ids = set()
                for c in candidates:
                    meta = c.get("metadata") or {}
                    present_ids.add(self.context_expander._composite_chunk_id({
                        "file_name": meta.get("file_name"),
                        "page_num": meta.get("page_num"),
                        "chunk_id": meta.get("parent_chunk_id") or meta.get("chunk_id"),
                    }))
                for c in candidates:
                    meta = c.get("metadata", {})
                    # Se expande sobre el chunk PADRE: si el candidato es un vector
                    # de pregunta sintética, su propio chunk_id no está en el caché
                    # y la expansión no se aplicaba nunca (aunque el contenido que
                    # representa sí tiene vecinos).
                    prev_text, next_text = self.context_expander.expand(
                        meta.get("file_name"), meta.get("page_num"),
                        meta.get("parent_chunk_id") or meta.get("chunk_id"),
                        skip_ids=present_ids,
                    )
                    if prev_text or next_text:
                        parts = []
                        if prev_text:
                            parts.append(f"[CONTEXTO PREVIO]\n{prev_text}")
                        parts.append(f"[CHUNK RELEVANTE]\n{c['text']}")
                        if next_text:
                            parts.append(f"[CONTEXTO SIGUIENTE]\n{next_text}")
                        c["text"] = "\n\n".join(parts)

            # 6.5) Colapsar multi-vector: un mismo chunk está indexado varias veces
            #      (su propio contenido + un vector por cada pregunta sintética que
            #      responde). Sin colapsar, un solo chunk puede ocupar todo el top-k
            #      con resultados idénticos y desplazar al resto del contexto.
            candidates = self._collapse_by_parent(candidates)

            # 6.6) Descartar candidatos cuyo texto ya está contenido, palabra por
            #      palabra, en el de otro candidato. El índice tiene contenido
            #      solapado por diseño (un superchunk contiene a sus chunks hijos,
            #      y un título corto de sección se indexa además por separado), así
            #      que sin esto un mismo párrafo de una página ocupa 3 de los 10
            #      lugares del contexto y se ve como 3 fuentes casi idénticas en la UI.
            candidates = self._drop_contained_duplicates(candidates)

            # 7) Gate de relevancia real: al menos un candidato con similitud DENSA
            #    (no BM25/visual, que no tienen escala comparable) por encima del
            #    umbral configurado. Se descartan filas que no lo superan.
            # El gate tiene dos trabajos distintos que antes estaban colapsados en uno:
            #
            #  (a) DECIDIR si hay contexto relevante. Si nada supera el umbral denso, se
            #      devuelve vacío y el LLM responde solo con los planos. Eso está bien.
            #  (b) FILTRAR qué filas pasan. Acá estaba el problema: filtrar por
            #      `dense_similarity` descarta a todo candidato que encontró BM25 o CLIP
            #      y el denso no, porque esos no tienen esa clave (default 0.0). Medido:
            #      para la consulta "22B-D010N104" —un código de producto, el caso
            #      exacto para el que existe BM25— sobrevivían 0 candidatos solo-BM25 y
            #      0 solo-CLIP. Las dos etapas eran inertes por construcción: solo
            #      podían reordenar lo que el denso ya había traído.
            has_relevant = any(
                c.get("dense_similarity", 0.0) >= self.min_context_similarity_score
                for c in candidates
            )
            if self.fusion_admits_sparse and has_relevant:
                # Se conserva el candidato si pasa el umbral denso O si llegó por otra
                # señal de retrieval. Es seguro respecto del fallback a planos: si la
                # consulta es fuera de tema, has_relevant ya es False y no se llega acá.
                relevant = [
                    c for c in candidates
                    if c.get("dense_similarity", 0.0) >= self.min_context_similarity_score
                    or "bm25_rank" in c or "visual_rank" in c
                ]
            else:
                relevant = [
                    c for c in candidates
                    if c.get("dense_similarity", 0.0) >= self.min_context_similarity_score
                ]

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

    # Facetas con las que Ingestion indexa UNA figura: el OCR del recorte, la
    # descripción estructurada y el chunk visual con la imagen. Comparten el id base
    # (chunk_3_ocr / chunk_3_structured / chunk_3_visual salen todas de chunk_3).
    _FIGURE_FACET_SUFFIXES = ("_ocr", "_structured", "_visual")

    @classmethod
    def _figure_base_id(cls, chunk_id: Any) -> Optional[str]:
        """Id base de la figura si `chunk_id` es una faceta; None si no lo es."""
        text = str(chunk_id or "")
        for suffix in cls._FIGURE_FACET_SUFFIXES:
            if text.endswith(suffix):
                return text[: -len(suffix)]
        return None

    @classmethod
    def _collapse_by_parent(cls, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Colapsa los candidatos que representan el MISMO contenido.

        Con Contextual Retrieval + preguntas sintéticas, un chunk aparece en el
        índice varias veces: una por su propio texto y una por cada pregunta que
        responde. Todas comparten `parent_chunk_id`. Se conserva el mejor de cada
        grupo (por rerank_score si existe, si no por score fusionado) y se
        acumulan las preguntas que matchearon, que son una señal útil de por qué
        se recuperó ese chunk.

        Las tres facetas de una figura se agrupan además entre sí, y ahí gana la que
        trae la imagen. Hace falta porque compiten por los mismos lugares del
        contexto describiendo lo mismo, y la que suele ganar por score es la del OCR
        —cuyo texto es ilegible ("Texto extraído: as a =Q ada 218")— porque sus
        preguntas sintéticas se generaron a partir de la descripción de la figura y
        rankean muy bien. Resultado antes de agrupar: la mitad del contexto eran
        chunks de OCR sin imagen, y el diagrama nunca llegaba a la UI.
        """
        best_by_parent: Dict[Any, Dict[str, Any]] = {}
        order: List[Any] = []

        def has_media(cand: Dict[str, Any]) -> bool:
            meta = cand.get("metadata") or {}
            return bool(meta.get("media_path") or meta.get("image_path"))

        def quality(cand: Dict[str, Any]) -> float:
            if "rerank_score" in cand:
                return float(cand["rerank_score"])
            return float(cand.get("fused_score", cand.get("dense_similarity", 0.0)))

        # Dentro de un grupo de facetas, la que tiene la imagen gana aunque puntúe
        # peor: su texto es la descripción (igual o mejor que el del OCR) y es la
        # única que puede mostrarle el diagrama al usuario.
        def rank(cand: Dict[str, Any]):
            return (has_media(cand), quality(cand))

        for cand in candidates:
            meta = cand.get("metadata") or {}
            # Clave de agrupación: el chunk padre dentro de su documento/página.
            # Si no hay parent_chunk_id (índice viejo), cae al doc_id, con lo que
            # el colapso es un no-op y el comportamiento no cambia.
            parent = meta.get("parent_chunk_id") or meta.get("chunk_id")
            figure_base = cls._figure_base_id(parent)
            if figure_base:
                # Se agrupa por figura, con un marcador para no mezclarla nunca con
                # un chunk de texto que casualmente se llame igual sin sufijo.
                key = (meta.get("file_name"), str(meta.get("page_num")), figure_base, "figura")
            elif parent:
                key = (meta.get("file_name"), str(meta.get("page_num")), parent)
            else:
                key = cand.get("doc_id")

            matched_question = (meta.get("question") or "").strip()

            existing = best_by_parent.get(key)
            if existing is None:
                cand["matched_questions"] = [matched_question] if matched_question else []
                best_by_parent[key] = cand
                order.append(key)
                continue

            if matched_question and matched_question not in existing["matched_questions"]:
                existing["matched_questions"].append(matched_question)

            if rank(cand) > rank(existing):
                # El nuevo es mejor: pasa a representar al grupo conservando las
                # preguntas ya acumuladas y el mejor dense_similarity visto (que
                # es lo que evalúa el gate de relevancia).
                cand["matched_questions"] = existing["matched_questions"]
                cand["dense_similarity"] = max(
                    float(cand.get("dense_similarity", 0.0)),
                    float(existing.get("dense_similarity", 0.0)),
                )
                best_by_parent[key] = cand
            else:
                existing["dense_similarity"] = max(
                    float(existing.get("dense_similarity", 0.0)),
                    float(cand.get("dense_similarity", 0.0)),
                )

        collapsed = [best_by_parent[k] for k in order]
        if len(collapsed) < len(candidates):
            logger.info(
                f"[Chroma] Multi-vector colapsado: {len(candidates)} -> {len(collapsed)} candidatos"
            )
        return collapsed

    # Núcleo del chunk dentro del texto ya expandido con vecinos (ver ContextExpander):
    # "[CONTEXTO PREVIO]\n...\n\n[CHUNK RELEVANTE]\n<núcleo>\n\n[CONTEXTO SIGUIENTE]\n...".
    # Comparar el texto completo haría que dos chunks vecinos nunca se vieran como
    # duplicados, porque cada uno lleva al otro adentro como contexto.
    _CORE_MARKER = "[CHUNK RELEVANTE]"

    @classmethod
    def _core_text(cls, candidate: Dict[str, Any]) -> str:
        text = candidate.get("text") or ""
        if cls._CORE_MARKER in text:
            text = text.split(cls._CORE_MARKER, 1)[1]
            for marker in ("[CONTEXTO SIGUIENTE]", "[CONTEXTO PREVIO]"):
                text = text.split(marker, 1)[0]
        return " ".join(text.lower().split())

    @classmethod
    def _drop_contained_duplicates(cls, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Elimina los candidatos cuyo núcleo de texto es subcadena del núcleo de otro
        candidato del mismo documento. No se pierde información: lo que se descarta
        está íntegro dentro de lo que se conserva.

        Se recorre de más largo a más corto para conservar siempre al contenedor (si
        se recorriera por score, un título corto bien puntuado podría "ganarle" al
        chunk completo que lo incluye y ahí sí se perdería texto). El
        `dense_similarity` del descartado se propaga al contenedor con max(): ese
        texto está literalmente dentro del contenedor, así que su relevancia es al
        menos esa — sin esto, descartar el candidato mejor puntuado podía tumbar el
        gate de relevancia y dejar la consulta sin contexto.

        Los candidatos CON media se evalúan primero y nunca se descartan: la tabla o
        la imagen es el aporte de esa fuente, aunque su texto sea redundante. El
        orden importa por un caso concreto de la ingesta: cada diagrama se indexa
        como par `diagram_description` + `diagram_visual` con el MISMO texto, y solo
        el segundo lleva `media_path`. Al mirar primero los que tienen media, el del
        par que sobrevive es el que trae la imagen — que es la única forma de que el
        diagrama llegue a la UI (el otro gana el ranking denso tan seguido que las
        imágenes casi nunca se mostraban).
        """
        if len(candidates) < 2:
            return candidates

        def has_media(cand: Dict[str, Any]) -> bool:
            meta = cand.get("metadata") or {}
            return bool(meta.get("media_path") or meta.get("image_path"))

        cores = {id(c): cls._core_text(c) for c in candidates}
        ordered = sorted(candidates, key=lambda c: (has_media(c), len(cores[id(c)])), reverse=True)

        kept: List[Dict[str, Any]] = []
        dropped = set()
        for cand in ordered:
            core = cores[id(cand)]
            meta = cand.get("metadata") or {}

            container = None
            if core and not has_media(cand):
                container = next(
                    (
                        k for k in kept
                        if (k.get("metadata") or {}).get("file_name") == meta.get("file_name")
                        and core in cores[id(k)]
                    ),
                    None,
                )

            if container is None:
                kept.append(cand)
                continue

            dropped.add(id(cand))
            container["dense_similarity"] = max(
                float(container.get("dense_similarity", 0.0)),
                float(cand.get("dense_similarity", 0.0)),
            )

        if not dropped:
            return candidates

        logger.info(
            f"[Chroma] Contenido solapado descartado: {len(candidates)} -> "
            f"{len(candidates) - len(dropped)} candidatos"
        )
        # Se devuelve en el orden de ranking original, no en el de recorrido.
        return [c for c in candidates if id(c) not in dropped]

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

    # sigmoid(raw) satura casi siempre en ~100%: para cross-encoder/ms-marco-MiniLM-L-6-v2
    # en este dominio, un match claramente relevante ya da un logit de +5/+6 (sigmoid(6)=99.75%)
    # y uno irrelevante ronda -9 (sigmoid(-9)=0.01%), así que casi todo lo "bueno" queda
    # indistinguible cerca de 100%. Medido empíricamente contra pares query/pasaje reales de
    # este corpus (ver conversación): raw ~6 = muy relevante, ~1 = algo relacionado, ~-2.5 =
    # poco relacionado, ~-9 = irrelevante. Con T=4, sigmoid(raw/T) reparte eso en ~82%/57%/
    # 35%/9%, mucho más útil para fijar un umbral. Es una calibración heurística, no una
    # probabilidad calibrada de verdad — puede necesitar reajuste si el patrón de preguntas
    # o el modelo de reranking cambian.
    RERANK_SCORE_TEMPERATURE = 4.0

    @classmethod
    def _normalize_display_score(cls, c: Dict[str, Any]) -> float:
        """
        Normaliza el score a mostrar al usuario a una escala 0-100 interpretable
        como "% de similitud":
        - rerank_score: logit del cross-encoder sin acotar (puede ser negativo o
          >10); se pasa por sigmoid con temperatura (ver RERANK_SCORE_TEMPERATURE)
          para evitar que sature en ~100% para cualquier match razonablemente bueno.
        - dense_similarity: ya es una similitud coseno en 0-1, alcanza con *100.
        - fused_score (RRF): no está pensado como score absoluto (con k=60 el
          máximo teórico es ~3/61), así que no hay forma no arbitraria de
          leerlo como "%"; se deja *100 igual para no perder la señal relativa
          de orden, sabiendo que en este fallback los números van a ser bajos.
        """
        if "rerank_score" in c:
            score = float(c["rerank_score"])
            # Los rerankers no coinciden en escala de salida:
            #  - bge-reranker-* vía sentence-transformers ya devuelve una
            #    probabilidad en [0,1] (medido: relevante 0.998, irrelevante 0.000).
            #    Pasarla otra vez por sigmoid la aplastaría a 50-73%, que como "%
            #    de similitud" es ilegible.
            #  - los cross-encoder de MS MARCO devuelven logits sin acotar
            #    (medido: -11 a +9), que sí necesitan sigmoid con temperatura.
            # Se detecta por el rango en vez de por nombre de modelo, así cambiar
            # de reranker no obliga a tocar esto. Un logit que caiga dentro de
            # [0,1] se muestra casi igual por ambos caminos, así que la heurística
            # es segura.
            if 0.0 <= score <= 1.0:
                return score * 100.0
            scaled = score / cls.RERANK_SCORE_TEMPERATURE
            return float(1.0 / (1.0 + np.exp(-scaled)) * 100.0)
        if "dense_similarity" in c:
            return float(c["dense_similarity"]) * 100.0
        return float(c.get("fused_score", 0.0)) * 100.0

    def _candidates_to_df(self, data: Dict[str, Any], n: int, candidates: List[Dict[str, Any]]) -> pd.DataFrame:
        qid = data.get("query_id", "no-qid")

        rows = []
        for i, c in enumerate(candidates):
            meta = c.get("metadata", {})
            # Score mostrado al usuario/log, normalizado a 0-100 (ver _normalize_display_score).
            display_score = self._normalize_display_score(c)
            # media_path: copia canónica en Ingestion/data/media/ (tablas e imágenes/diagramas).
            # image_path: crop original de la página (más específico para diagramas). Ambos
            # opcionales según el content_type del chunk (ver DualIndexer en Ingestion).
            rows.append([
                i, c["doc_id"], c["text"],
                meta.get("file_name"), meta.get("page_num"), meta.get("chunk_id"),
                display_score,
                # El score de fusión se conserva aparte del que se muestra: son escalas
                # distintas y el orden final puede decidirse por uno u otro.
                float(c.get("rerank_score", c.get("fused_score", 0.0))),
                meta.get("content_type"), meta.get("media_path"), meta.get("image_path"),
            ])

        base = pd.DataFrame(rows, columns=[
            "query_idx", "doc_id", "document", "file_name", "page_num", "chunk_id", "similarity",
            "fusion_score", "content_type", "media_path", "image_path",
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
            # Ordenar por el score de FUSIÓN (RRF) y no por la similitud densa. Antes
            # se ordenaba por "similarity", que sin reranker es `dense_similarity*100`:
            # eso descartaba el resultado de la fusión y dejaba que el denso decidiera
            # solo, otra razón por la que BM25 y CLIP no podían influir en nada.
            sort_column = "fusion_score" if self.fusion_decides_order else "similarity"
            base = base.sort_values(sort_column, ascending=False)
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
