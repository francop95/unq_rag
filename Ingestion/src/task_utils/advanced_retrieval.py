"""
Advanced Retrieval Module
==========================

Mejoras de retrieval implementadas:
1. Context Expansion (Parent-Child Chunking)
2. Reranking con Cross-Encoder
3. BM25 (Sparse Retrieval)
4. Query Expansion
5. Metadata Filtering
"""

from typing import List, Dict, Any, Optional, Tuple
import json
from pathlib import Path
from dataclasses import dataclass

try:
    from sentence_transformers import CrossEncoder
    CROSSENCODER_AVAILABLE = True
except ImportError:
    CROSSENCODER_AVAILABLE = False
    print("⚠️  cross-encoder no disponible. Instala: pip install sentence-transformers")

try:
    from rank_bm25 import BM25Okapi
    BM25_AVAILABLE = True
except ImportError:
    BM25_AVAILABLE = False
    print("⚠️  rank-bm25 no disponible. Instala: pip install rank-bm25")

try:
    from thefuzz import process as fuzz_process
    FUZZ_AVAILABLE = True
except ImportError:
    FUZZ_AVAILABLE = False
    print("⚠️  thefuzz no disponible. Instala: pip install thefuzz")


@dataclass
class ExpandedResult:
    """Resultado con contexto expandido."""
    main_chunk: str
    prev_context: str = ""
    next_context: str = ""
    combined_text: str = ""


class ContextExpander:
    """
    Expande chunks recuperados con chunks vecinos para mejor contexto.
    
    Mejora: +50% contexto para chunks fragmentados
    """
    
    def __init__(self, window_size: int = 1):
        """
        Args:
            window_size: Número de chunks vecinos a incluir (1 = prev + next)
        """
        self.window_size = window_size
        self._chunk_cache = {}
    
    @staticmethod
    def _composite_chunk_id(chunk: Dict[str, Any]) -> str:
        """
        ID compuesto file_name_pagenum_chunkid, igual al doc_id que usa
        DualIndexer/Chroma. chunk_id solo (ej. "chunk_1") no es único entre
        páginas/documentos, así que no sirve como clave de caché por sí solo.
        """
        return f"{chunk.get('file_name','')}_{chunk.get('page_num','')}_{chunk.get('chunk_id','')}"

    def load_chunks_from_directory(self, chunks_dir: str):
        """Carga todos los chunks de un directorio para búsqueda rápida."""
        chunks_dir = Path(chunks_dir)

        for page_folder in chunks_dir.iterdir():
            if not page_folder.is_dir():
                continue

            for json_file in page_folder.glob("*.json"):
                try:
                    with open(json_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)

                        chunks = data if isinstance(data, list) else [data]
                        for chunk in chunks:
                            if chunk.get('chunk_id'):
                                self._chunk_cache[self._composite_chunk_id(chunk)] = chunk
                except Exception as e:
                    print(f"   ⚠️  Error cargando {json_file}: {e}")
    
    def expand_context(
        self,
        chunk_id: str,
        metadata: Dict[str, Any]
    ) -> ExpandedResult:
        """
        Expande un chunk con sus vecinos.
        
        Args:
            chunk_id: ID del chunk principal
            metadata: Metadata del chunk (debe contener prev_chunk_id, next_chunk_id)
        
        Returns:
            ExpandedResult con contexto expandido
        """
        # doc_id de Chroma puede traer sufijo _desc/_visual (chunks de imagen);
        # el chunk_id compuesto en el cache no lo tiene, así que se prueba
        # el lookup directo y, si falla, se le saca el sufijo conocido.
        main_chunk = self._chunk_cache.get(chunk_id) or self._chunk_cache.get(
            chunk_id.removesuffix("_desc").removesuffix("_visual"), {}
        )
        main_text = str(main_chunk.get('original_chunk', ''))

        prev_text = ""
        next_text = ""

        # Obtener chunk previo
        prev_id = metadata.get('prev_chunk_id')
        if prev_id and prev_id in self._chunk_cache:
            prev_chunk = self._chunk_cache[prev_id]
            prev_text = str(prev_chunk.get('original_chunk', ''))

        # Obtener chunk siguiente
        next_id = metadata.get('next_chunk_id')
        if next_id and next_id in self._chunk_cache:
            next_chunk = self._chunk_cache[next_id]
            next_text = str(next_chunk.get('original_chunk', ''))
        
        # Combinar con separadores claros
        combined = ""
        if prev_text:
            combined += f"[CONTEXTO PREVIO]\n{prev_text}\n\n"
        
        combined += f"[CHUNK PRINCIPAL]\n{main_text}"
        
        if next_text:
            combined += f"\n\n[CONTEXTO SIGUIENTE]\n{next_text}"
        
        return ExpandedResult(
            main_chunk=main_text,
            prev_context=prev_text,
            next_context=next_text,
            combined_text=combined
        )


class CrossEncoderReranker:
    """
    Reranking de resultados con Cross-Encoder.
    
    Mejora: +20-30% precisión en top-5
    """
    
    def __init__(self, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"):
        """
        Args:
            model_name: Modelo de cross-encoder a usar
        """
        if not CROSSENCODER_AVAILABLE:
            raise ImportError("sentence-transformers no disponible para cross-encoder")
        
        print(f"🔄 Cargando cross-encoder: {model_name}...")
        self.model = CrossEncoder(model_name)
        print(f"   ✅ Cross-encoder listo")
    
    def rerank(
        self,
        query: str,
        results: List[Dict[str, Any]],
        top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Reordena resultados usando cross-encoder.
        
        Args:
            query: Query del usuario
            results: Lista de resultados a reordenar
            top_k: Número de resultados finales
        
        Returns:
            Lista reordenada de resultados
        """
        if not results:
            return []
        
        # Preparar pares (query, documento)
        pairs = []
        for result in results:
            # Soportar tanto dicts como dataclasses
            if hasattr(result, 'content'):
                # Es un objeto (dataclass)
                text = result.content or getattr(result, 'text', '')
            else:
                # Es un dict
                text = result.get('content', '') or result.get('text', '')
            pairs.append((query, text))
        
        # Calcular scores del cross-encoder
        scores = self.model.predict(pairs)
        
        # Añadir scores a resultados
        for result, score in zip(results, scores):
            if hasattr(result, 'rerank_score'):
                # Es un objeto (dataclass) - usar setattr
                result.rerank_score = float(score)
            else:
                # Es un dict
                result['rerank_score'] = float(score)
        
        # Reordenar por score del cross-encoder
        reranked = sorted(
            results,
            key=lambda x: x.rerank_score if hasattr(x, 'rerank_score') else x.get('rerank_score', 0),
            reverse=True
        )
        
        return reranked[:top_k]


class BM25Index:
    """
    Índice BM25 para sparse retrieval.
    
    Mejora: Mejor para queries con keywords exactos
    """
    
    def __init__(self):
        """Inicializa índice BM25 vacío."""
        if not BM25_AVAILABLE:
            raise ImportError("rank-bm25 no disponible")
        
        self.corpus = []
        self.corpus_metadata = []
        self.bm25 = None
    
    def add_documents(self, documents: List[str], metadatas: List[Dict[str, Any]]):
        """
        Añade documentos al índice BM25.
        
        Args:
            documents: Lista de textos
            metadatas: Lista de metadata asociada
        """
        self.corpus = documents
        self.corpus_metadata = metadatas
        
        # Tokenizar corpus (simple split por palabras)
        tokenized_corpus = [doc.lower().split() for doc in documents]
        
        # Crear índice BM25
        self.bm25 = BM25Okapi(tokenized_corpus)
        
        print(f"   ✅ Índice BM25 creado: {len(documents)} documentos")
    
    def search(self, query: str, top_k: int = 10) -> List[Tuple[float, Dict[str, Any]]]:
        """
        Busca en el índice BM25.
        
        Args:
            query: Query del usuario
            top_k: Número de resultados
        
        Returns:
            Lista de (score, metadata) ordenada por relevancia
        """
        if self.bm25 is None:
            return []
        
        # Tokenizar query
        tokenized_query = query.lower().split()
        
        # Obtener scores BM25
        scores = self.bm25.get_scores(tokenized_query)
        
        # Combinar con metadata
        results = [
            (score, metadata, text)
            for score, metadata, text in zip(scores, self.corpus_metadata, self.corpus)
        ]
        
        # Ordenar y retornar top-k
        results.sort(key=lambda x: x[0], reverse=True)
        
        return [
            {
                'bm25_score': score,
                'metadata': metadata,
                'text': text
            }
            for score, metadata, text in results[:top_k]
        ]


class QueryExpander:
    """
    Expansión de queries con sinónimos y términos relacionados.
    
    Mejora: Mejor recall
    """
    
    # Diccionario de expansiones técnicas comunes
    TECHNICAL_EXPANSIONS = {
        "motor": ["motor", "motor eléctrico", "motor trifásico", "máquina eléctrica"],
        "diagrama": ["diagrama", "esquema", "plano", "circuit diagram"],
        "conexión": ["conexión", "conexionado", "cableado", "wiring"],
        "voltaje": ["voltaje", "tensión", "voltage", "V"],
        "corriente": ["corriente", "amperaje", "current", "A"],
        "potencia": ["potencia", "power", "kW", "watts"],
        "especificación": ["especificación", "spec", "parámetro", "característica"],
        "procedimiento": ["procedimiento", "proceso", "pasos", "instrucciones"],
        "instalación": ["instalación", "montaje", "installation", "setup"],
        "mantenimiento": ["mantenimiento", "service", "maintenance", "revisión"],
    }
    
    def __init__(self, max_expansions: int = 3):
        """
        Args:
            max_expansions: Máximo número de términos expandidos por palabra
        """
        self.max_expansions = max_expansions
    
    def expand_query(self, query: str) -> List[str]:
        """
        Expande una query con términos relacionados.
        
        Args:
            query: Query original
        
        Returns:
            Lista de queries expandidas (incluye original)
        """
        expanded_queries = [query]  # Siempre incluir original
        
        # Buscar términos expandibles
        query_lower = query.lower()
        
        for term, expansions in self.TECHNICAL_EXPANSIONS.items():
            if term in query_lower:
                # Generar variaciones con expansiones
                for expansion in expansions[:self.max_expansions]:
                    if expansion != term:
                        expanded_query = query_lower.replace(term, expansion)
                        if expanded_query not in expanded_queries:
                            expanded_queries.append(expanded_query)
        
        return expanded_queries[:self.max_expansions + 1]


class MetadataFilter:
    """
    Filtrado avanzado por metadata.
    
    Mejora: Búsquedas más precisas
    """
    
    @staticmethod
    def build_chroma_filter(filter_dict: Dict[str, Any]) -> Dict[str, Any]:
        """
        Construye un filtro compatible con ChromaDB.
        
        Args:
            filter_dict: Diccionario de filtros {campo: valor}
        
        Returns:
            Filtro en formato ChromaDB
        
        Ejemplos:
            {"chapter": "3"} → buscar solo en capítulo 3
            {"content_type": "diagram", "chapter": "2"} → diagramas del cap 2
        """
        if not filter_dict:
            return {}
        
        # Formato ChromaDB: {"$and": [{"campo": valor}, ...]}
        conditions = []
        
        for field, value in filter_dict.items():
            if isinstance(value, list):
                # OR condition
                conditions.append({
                    "$or": [{field: v} for v in value]
                })
            else:
                # Simple equality
                conditions.append({field: value})
        
        if len(conditions) == 1:
            return conditions[0]
        
        return {"$and": conditions}
    
    @staticmethod
    def filter_results(
        results: List[Dict[str, Any]],
        filter_dict: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Filtra resultados por metadata (post-retrieval).
        
        Args:
            results: Lista de resultados
            filter_dict: Filtros a aplicar
        
        Returns:
            Resultados filtrados
        """
        if not filter_dict:
            return results
        
        filtered = []
        
        for result in results:
            metadata = result.get('metadata', {})
            
            # Verificar que cumple todos los filtros
            matches = True
            for field, value in filter_dict.items():
                meta_value = metadata.get(field)
                
                if isinstance(value, list):
                    # OR: al menos uno debe coincidir
                    if meta_value not in value:
                        matches = False
                        break
                else:
                    # Equality
                    if meta_value != value:
                        matches = False
                        break
            
            if matches:
                filtered.append(result)
        
        return filtered
