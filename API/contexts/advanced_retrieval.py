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
    """
    Índice BM25 en memoria sobre el corpus textual ya indexado en Chroma.

    Distingue el texto de PUNTUACIÓN del texto de PRESENTACIÓN: al corpus léxico
    se le pueden agregar campos auxiliares (celdas de tabla aplanadas, preguntas
    que el chunk responde) para mejorar el match exacto, pero lo que se devuelve
    como contenido del resultado tiene que seguir siendo el documento original —
    de lo contrario esos campos auxiliares terminarían mostrados al usuario y
    enviados al LLM como si fueran parte del chunk.
    """

    def __init__(self):
        if not BM25_AVAILABLE:
            raise ImportError("rank-bm25 no disponible")
        self.doc_ids: List[str] = []
        self.corpus: List[str] = []          # texto de presentación
        self.metadatas: List[Dict[str, Any]] = []
        self.bm25: Optional["BM25Okapi"] = None

    def build(
        self,
        doc_ids: List[str],
        documents: List[str],
        metadatas: List[Dict[str, Any]],
        scoring_texts: Optional[List[str]] = None,
    ):
        """
        Args:
            documents: texto que se devuelve como contenido del resultado
            scoring_texts: texto sobre el que se calcula BM25 (por defecto,
                `documents`). Permite puntuar sobre una versión enriquecida sin
                contaminar lo que se muestra.
        """
        self.doc_ids = doc_ids
        self.corpus = documents
        self.metadatas = metadatas
        texts_for_scoring = scoring_texts if scoring_texts is not None else documents
        tokenized = [(text or "").lower().split() for text in texts_for_scoring]
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
        # Ids cuyo texto es boilerplate de página (ver _index_boilerplate)
        self._boilerplate_ids: set = set()

    @staticmethod
    def _composite_chunk_id(chunk: Dict[str, Any]) -> str:
        return f"{chunk.get('file_name','')}_{chunk.get('page_num','')}_{chunk.get('chunk_id','')}"

    @staticmethod
    def _readable_chunk_text(chunk: Dict[str, Any]) -> str:
        """
        Convierte 'original_chunk' a texto legible, con la misma lógica que usa
        Ingestion al indexar (ver _document_text_from_chunk en
        indexing_task_multimodal.py). Sin esto, un chunk vecino de tipo tabla/
        imagen inyecta su JSON crudo (ej. {"table_markdown": ...}) como contexto
        previo/siguiente en vez del texto legible.
        """
        original = chunk.get("original_chunk", "")
        ctype = (chunk.get("content_type") or "").lower()

        if isinstance(original, str):
            try:
                parsed = json.loads(original)
            except (ValueError, TypeError):
                parsed = None
        else:
            parsed = original

        if ctype == "table" and isinstance(parsed, dict):
            if parsed.get("table_markdown"):
                return str(parsed["table_markdown"])
            rows = (parsed.get("table_json") or {}).get("rows")
            if isinstance(rows, list) and rows:
                lines = ["\t".join("" if c is None else str(c) for c in r) for r in rows]
                return "\n".join(lines[:400])
            return json.dumps(parsed, ensure_ascii=False)

        if ctype in ("image", "diagram_visual") and isinstance(parsed, dict):
            notes = parsed.get("notes") or parsed.get("alt") or ""
            if isinstance(notes, dict):
                return notes.get("description") or json.dumps(notes, ensure_ascii=False)
            return str(notes)

        if ctype == "diagram_description" and isinstance(parsed, dict):
            return parsed.get("description") or json.dumps(parsed, ensure_ascii=False)

        return original if isinstance(original, str) else str(original or "")

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
        self._index_boilerplate()

    def _index_boilerplate(self):
        """
        Marca como boilerplate los chunks cuyo texto se repite dentro de su documento.

        El caché tiene MÁS chunks que el índice (708 vs 664): incluye los que la
        validación rechazó, porque se cargan de los JSON de chunking y no de Chroma. Eso
        abría una puerta lateral — medido, 128 enlaces prev/next inyectaban al prompt un
        chunk que el retrieval había descartado, y 31 de ellos eran encabezados y pies de
        página ("PowerFlex 4M Variador de frecuencia ajustable FRN 1.xx – 2.xx Manual del
        usuario Publicación 22F-UM001C-ES-E" aparece 13 veces).

        La repetición dentro del documento separa bien las dos cosas: un encabezado de
        página se repite, un epígrafe como "Figura 3.6: Conexionado placa TBEN" no.
        Medido sobre el corpus: 40 de 708 chunks tienen texto repetido y los 40 son
        boilerplate — encabezados, pies, bloques de contacto del proveedor, "Notas:".

        Esto solo suprime la INYECCIÓN COMO CONTEXTO. Ningún chunk deja de ser
        recuperable por mérito propio, así que el peor caso de un falso positivo es
        perder una línea de contexto, no perder contenido.

        Queda un residuo a propósito: los encabezados que llevan el nombre de la sección
        adelante ("Resolución de problemas PowerFlex 4M Variador de frecuencia…") son
        únicos como texto completo y no se detectan. Se probó extender la regla a
        n-gramas repetidos (6-gramas con ≥3 apariciones, cobertura ≥60%) y se descartó:
        marcaba 62 chunks pero con falsos positivos reales — código Python de la tesis y
        tablas de especificaciones partidas, que comparten la fila de encabezado entre
        sus partes. Cambiar 0 falsos positivos por unos encabezados menos no vale: el
        costo de suprimir el contexto de una tabla real es mayor que el de dejar pasar
        una línea de pie de página.
        """
        by_document: Dict[Any, Dict[str, int]] = {}
        for chunk in self._chunk_cache.values():
            text = " ".join(self._readable_chunk_text(chunk).split())
            if not text:
                continue
            counts = by_document.setdefault(chunk.get("file_name"), {})
            counts[text] = counts.get(text, 0) + 1

        self._boilerplate_ids = set()
        for composite_id, chunk in self._chunk_cache.items():
            text = " ".join(self._readable_chunk_text(chunk).split())
            if text and by_document.get(chunk.get("file_name"), {}).get(text, 0) >= 2:
                self._boilerplate_ids.add(composite_id)

        if self._boilerplate_ids:
            logger.info(
                f"[AdvancedRetrieval] Boilerplate detectado: {len(self._boilerplate_ids)} "
                f"chunks no se inyectarán como contexto (texto repetido en su documento)"
            )

    # Facetas con las que Ingestion indexa una misma figura (ver
    # ElectricalDiagramProcessor.create_multifaceted_chunks).
    _FIGURE_FACET_SUFFIXES = ("_ocr", "_structured", "_visual")

    @classmethod
    def _same_figure(cls, id_a: str, id_b: str) -> bool:
        """True si los dos ids son facetas de la misma figura (chunk_3_ocr / chunk_3_visual)."""
        def base(composite: str) -> Optional[str]:
            for suffix in cls._FIGURE_FACET_SUFFIXES:
                if composite.endswith(suffix):
                    return composite[: -len(suffix)]
            return None

        base_a, base_b = base(id_a or ""), base(id_b or "")
        return bool(base_a) and base_a == base_b

    def expand(
        self,
        file_name: str,
        page_num: Any,
        chunk_id: str,
        skip_ids: Optional[set] = None,
    ) -> Tuple[str, str]:
        """
        Devuelve (prev_text, next_text) para el chunk indicado, o ("","") si no hay vecinos.

        Args:
            skip_ids: ids compuestos de chunks que NO hay que inyectar como contexto
                porque ya están presentes como resultado por mérito propio. Sin esto
                el mismo contenido llega dos veces al LLM (como resultado y como
                contexto de su vecino).

        Tampoco se inyecta un vecino que sea una faceta hermana de la MISMA figura.
        Ingestion encadena prev/next de forma secuencial sobre la lista de chunks, y las
        tres facetas de una figura son consecutivas, así que terminan siendo vecinas
        entre sí: medido, 349 de 561 enlaces de figuras apuntan a una hermana. El efecto
        es que a un diagrama se le inyecta como "contexto" o el OCR ilegible de su
        propio recorte ("Diagrama eléctrico - Página 7 Texto extraído: 5 Ñ AN Th…") o
        una copia textual de su propia descripción.
        """
        composite_id = f"{file_name}_{page_num}_{chunk_id}"
        chunk = self._chunk_cache.get(composite_id)
        if not chunk:
            return "", ""

        skip_ids = skip_ids or set()
        prev_text, next_text = "", ""

        def usable(neighbor_id: Optional[str]) -> bool:
            return bool(
                neighbor_id
                and neighbor_id in self._chunk_cache
                and neighbor_id not in skip_ids
                and neighbor_id not in self._boilerplate_ids
                and not self._same_figure(composite_id, neighbor_id)
            )

        prev_id = chunk.get("prev_chunk_id")
        if usable(prev_id):
            prev_text = self._readable_chunk_text(self._chunk_cache[prev_id])

        next_id = chunk.get("next_chunk_id")
        if usable(next_id):
            next_text = self._readable_chunk_text(self._chunk_cache[next_id])

        return prev_text, next_text


def rrf_score(rank: int, k: int = 60) -> float:
    """Reciprocal Rank Fusion: score de un resultado según su posición (1-based) en una lista."""
    return 1.0 / (k + rank)
