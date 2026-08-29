"""
Configuración centralizada para el pipeline RAG multimodal.
Todas las variables que pueden cambiar en el futuro están aquí.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class PathConfig:
    """Configuración de rutas de entrada/salida."""
    raw_data_path: str = "./data/raw_data/"
    chunks_data_path: str = "./data/chunks_data/"
    embeddings_data_path: str = "./data/embeddings_data/"
    index_path: str = "./data/chroma_index/"


@dataclass
class IndexConfig:
    """Configuración del índice Chroma."""
    index_name: str = "verion_index"
    
    # Parámetros HNSW (Hierarchical Navigable Small World)
    hnsw_space: str = "cosine"  # Métrica de distancia: "cosine", "l2", "ip"
    hnsw_search_ef: int = 100  # Búsqueda más exhaustiva (más lento, más preciso)
    
    # Tamaño de batch para indexación
    batch_size: int = 512


@dataclass
class ChunkingConfig:
    """Configuración del task de chunking (extracción de contenido del PDF)."""
    multimodal_model: str = "gpt-4o"  # Modelo LLM multimodal a usar
    temperature: float = 0.2  # Creatividad del modelo (0=determinista, 1=creativo)
    max_output_tokens: int = 4000  # Tokens máximos por respuesta
    pdf_zoom: float = 2.0  # Factor de zoom para rasterizar el PDF
    chunking_concurrency: int = 4  # Páginas LLM procesadas en paralelo (acotado por rate limits)


@dataclass
class EmbeddingConfig:
    """Configuración del task de embeddings (generación de vectores)."""
    embedding_model: str = "text-embedding-3-large"  # Modelo de embeddings OpenAI
    embedding_batch_size: int = 64  # Cuántos textos procesar en paralelo
    max_retries: int = 5  # Reintentos en caso de rate limit o error


@dataclass
class OpenAIConfig:
    """Configuración de credenciales y endpoints de OpenAI."""
    openai_key: str = ""  # Se lee del .env
    openai_url: Optional[str] = None  # None = usar endpoint por defecto de OpenAI


@dataclass
class HybridChunkingConfig:
    """Configuración para chunking híbrido y procesamiento avanzado."""
    # Chunking híbrido
    use_hybrid_chunking: bool = False
    complexity_threshold: float = 0.99
    chunk_size: int = 1000
    chunk_overlap: int = 200
    
    # Tablas
    max_table_rows: int = 10
    
    # Diagramas
    use_ocr_for_diagrams: bool = True
    ocr_confidence_threshold: float = 60.0
    
    # Validación
    min_chunk_length: int = 50
    max_chunk_length: int = 8000
    similarity_threshold: float = 0.85
    
    # Re-chunking semántico
    semantic_similarity_threshold: float = 0.85
    max_pages_gap: int = 2
    max_superchunk_size: int = 3000


@dataclass
class EnrichmentConfig:
    """
    Enriquecimiento por LLM durante la ingesta (más costo de ingesta, mejor retrieval).

    - Contextual Retrieval: prepende contexto generado por LLM a cada chunk antes
      de embeberlo, para que el vector no dependa de que el chunk se explique solo.
    - Preguntas sintéticas: indexa vectores extra con preguntas que el chunk
      responde, cerrando la brecha entre el lenguaje del usuario (síntomas) y el
      del manual (especificaciones).
    - Pasada dedicada de figuras/tablas: vuelve a llamar al modelo de visión con
      cada recorte aislado, en vez de confiar en la llamada de página que además
      está segmentando.
    """
    use_contextual_retrieval: bool = True
    use_synthetic_questions: bool = True
    max_synthetic_questions: int = 8
    enrichment_model: str = "gpt-4o-mini"
    enrichment_concurrency: int = 4

    use_dedicated_figure_pass: bool = True
    figure_pass_concurrency: int = 3


@dataclass
class DualIndexConfig:
    """Configuración para sistema dual de indexado y retrieval avanzado."""
    # Índices
    clip_model: str = "clip-ViT-B-32"
    visual_index_name: str = "visual_docs"
    media_path: str = "./data/media"
    
    # Advanced Retrieval ⭐
    # Reranking
    use_reranking: bool = True
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    rerank_top_k: int = 20
    
    # BM25 Sparse Retrieval
    use_bm25: bool = True
    bm25_weight: float = 0.3
    
    # Query Expansion
    use_query_expansion: bool = True
    max_expanded_terms: int = 3
    
    # Context Expansion
    use_context_expansion: bool = True
    context_window_size: int = 1


@dataclass
class Config:
    """Configuración completa del pipeline."""
    paths: PathConfig = None
    index: IndexConfig = None
    chunking: ChunkingConfig = None
    embedding: EmbeddingConfig = None
    openai: OpenAIConfig = None
    hybrid: HybridChunkingConfig = None
    dual: DualIndexConfig = None
    enrichment: EnrichmentConfig = None

    def __post_init__(self):
        """Inicializar subconfiguraciones si no se proporcionan."""
        if self.paths is None:
            self.paths = PathConfig()
        if self.index is None:
            self.index = IndexConfig()
        if self.chunking is None:
            self.chunking = ChunkingConfig()
        if self.embedding is None:
            self.embedding = EmbeddingConfig()
        if self.openai is None:
            self.openai = OpenAIConfig()
        if self.hybrid is None:
            self.hybrid = HybridChunkingConfig()
        if self.dual is None:
            self.dual = DualIndexConfig()
        if self.enrichment is None:
            self.enrichment = EnrichmentConfig()

    def to_task_settings_dict(self) -> dict:
        """
        Convierte la configuración a un diccionario compatible con los tasks.
        Útil para pasar a chunk_task._task_settings, emb_task._task_settings, etc.
        """
        return {
            # Paths
            "chunks_data_path": self.paths.chunks_data_path,
            "embeddings_data_path": self.paths.embeddings_data_path,
            
            # Index
            "index_name": self.index.index_name,
            "index_path": self.paths.index_path,
            
            # Chunking
            "multimodal_model": self.chunking.multimodal_model,
            "temperature": self.chunking.temperature,
            "max_output_tokens": self.chunking.max_output_tokens,
            "pdf_zoom": self.chunking.pdf_zoom,
            "chunking_concurrency": self.chunking.chunking_concurrency,
            
            # Embeddings
            "embedding_model": self.embedding.embedding_model,
            "embedding_batch_size": self.embedding.embedding_batch_size,
            "max_retries": self.embedding.max_retries,
            
            # OpenAI
            "open_ai_key": self.openai.openai_key,
            "open_ai_url": self.openai.openai_url,
            
            # Hybrid Chunking
            "use_hybrid_chunking": self.hybrid.use_hybrid_chunking,
            "complexity_threshold": self.hybrid.complexity_threshold,
            "chunk_size": self.hybrid.chunk_size,
            "chunk_overlap": self.hybrid.chunk_overlap,
            "max_table_rows": self.hybrid.max_table_rows,
            "use_ocr_for_diagrams": self.hybrid.use_ocr_for_diagrams,
            "ocr_confidence_threshold": self.hybrid.ocr_confidence_threshold,
            "min_chunk_length": self.hybrid.min_chunk_length,
            "max_chunk_length": self.hybrid.max_chunk_length,
            "similarity_threshold": self.hybrid.similarity_threshold,
            "semantic_similarity_threshold": self.hybrid.semantic_similarity_threshold,
            "max_pages_gap": self.hybrid.max_pages_gap,
            "max_superchunk_size": self.hybrid.max_superchunk_size,
            
            # Dual Index
            "clip_model": self.dual.clip_model,
            "visual_index_name": self.dual.visual_index_name,
            "media_path": self.dual.media_path,

            # Enriquecimiento por LLM (la pasada de figuras corre dentro del
            # ChunkingTask, así que necesita viajar en task_settings)
            "use_dedicated_figure_pass": self.enrichment.use_dedicated_figure_pass,
            "figure_pass_concurrency": self.enrichment.figure_pass_concurrency,
        }


# Instancia por defecto (se sobrescribe al leer del .env)
DEFAULT_CONFIG = Config()
