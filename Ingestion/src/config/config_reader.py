"""
ConfigReader: Lee variables de entorno (.env) y proporciona configuración segura.
"""
import os
from typing import Optional
from dotenv import dotenv_values
from .config import (
    Config, PathConfig, IndexConfig, ChunkingConfig, 
    EmbeddingConfig, OpenAIConfig, HybridChunkingConfig, DualIndexConfig
)


class ConfigReader:
    """
    Lee variables de configuración del archivo .env y las proporciona
    a través de una interfaz segura y con valores por defecto.
    
    Ejemplo de uso:
        config_reader = ConfigReader()
        config = config_reader.get_config()
        print(config.openai.openai_key)
        
        # O acceso directo:
        key = config_reader.get("openai_key")
    """

    def __init__(self, env_file: str = ".env"):
        """
        Inicializa el lector desde un archivo .env.
        
        Args:
            env_file: Ruta al archivo .env (por defecto ".env" en la carpeta raíz)
        """
        self.env_file = env_file
        self.env_vars = dotenv_values(env_file)
        
    def get(self, key: str, default: Optional[str] = None) -> str:
        """
        Obtiene una variable de entorno individual.
        
        Args:
            key: Nombre de la variable (sin comillas ni espacios)
            default: Valor por defecto si no existe
            
        Returns:
            Valor de la variable o default
        """
        return self.env_vars.get(key, default or "")
    
    def get_int(self, key: str, default: int = 0) -> int:
        """Obtiene una variable como entero."""
        try:
            return int(self.env_vars.get(key, default))
        except (ValueError, TypeError):
            return default
    
    def get_float(self, key: str, default: float = 0.0) -> float:
        """Obtiene una variable como flotante."""
        try:
            return float(self.env_vars.get(key, default))
        except (ValueError, TypeError):
            return default
    
    def get_bool(self, key: str, default: bool = False) -> bool:
        """Obtiene una variable como booleano."""
        val = self.env_vars.get(key, "").lower()
        if val in ("true", "1", "yes", "on"):
            return True
        elif val in ("false", "0", "no", "off"):
            return False
        return default

    def get_config(self) -> Config:
        """
        Construye un objeto Config desde las variables del .env.
        Cualquier variable no encontrada usará el valor por defecto definido en config.py.
        
        Returns:
            Config: Objeto de configuración completo
        """
        # Rutas
        paths = PathConfig(
            raw_data_path=self.get("raw_data_path", "./data/raw_data/"),
            chunks_data_path=self.get("chunks_data_path", "./data/chunks_data/"),
            embeddings_data_path=self.get("embeddings_data_path", "./data/embeddings_data/"),
            index_path=self.get("index_path", "./data/chroma_index/"),
        )
        
        # Índice
        index = IndexConfig(
            index_name=self.get("index_name", "verion_index"),
            hnsw_space=self.get("hnsw_space", "cosine"),
            hnsw_search_ef=self.get_int("hnsw_search_ef", 100),
            batch_size=self.get_int("index_batch_size", 512),
        )
        
        # Chunking
        chunking = ChunkingConfig(
            multimodal_model=self.get("multimodal_model", "gpt-4o"),
            temperature=self.get_float("chunking_temperature", 0.2),
            max_output_tokens=self.get_int("chunking_max_output_tokens", 4000),
            pdf_zoom=self.get_float("pdf_zoom", 2.0),
            chunking_concurrency=self.get_int("chunking_concurrency", 4),
        )
        
        # Embeddings
        embedding = EmbeddingConfig(
            embedding_model=self.get("embedding_model", "text-embedding-3-large"),
            embedding_batch_size=self.get_int("embedding_batch_size", 64),
            max_retries=self.get_int("embedding_max_retries", 5),
        )
        
        # OpenAI
        openai = OpenAIConfig(
            openai_key=self.get("openai_key", ""),
            openai_url=self.get("openai_url") or None,  # None si está vacío
        )
        
        # Hybrid Chunking
        hybrid = HybridChunkingConfig(
            use_hybrid_chunking=self.get_bool("use_hybrid_chunking", True),
            complexity_threshold=self.get_float("complexity_threshold", 0.5),
            chunk_size=self.get_int("chunk_size", 1000),
            chunk_overlap=self.get_int("chunk_overlap", 200),
            max_table_rows=self.get_int("max_table_rows", 10),
            use_ocr_for_diagrams=self.get_bool("use_ocr_for_diagrams", True),
            ocr_confidence_threshold=self.get_float("ocr_confidence_threshold", 60.0),
            min_chunk_length=self.get_int("min_chunk_length", 50),
            max_chunk_length=self.get_int("max_chunk_length", 8000),
            similarity_threshold=self.get_float("similarity_threshold", 0.85),
            semantic_similarity_threshold=self.get_float("semantic_similarity_threshold", 0.85),
            max_pages_gap=self.get_int("max_pages_gap", 2),
            max_superchunk_size=self.get_int("max_superchunk_size", 3000),
        )
        
        # Dual Index + Advanced Retrieval
        dual = DualIndexConfig(
            clip_model=self.get("clip_model", "clip-ViT-B-32"),
            visual_index_name=self.get("visual_index_name", "visual_docs"),
            media_path=self.get("media_path", "./data/media"),
            # Advanced Retrieval ⭐
            use_reranking=self.get_bool("use_reranking", True),
            reranker_model=self.get("reranker_model", "cross-encoder/ms-marco-MiniLM-L-6-v2"),
            rerank_top_k=self.get_int("rerank_top_k", 20),
            use_bm25=self.get_bool("use_bm25", True),
            bm25_weight=self.get_float("bm25_weight", 0.3),
            use_query_expansion=self.get_bool("use_query_expansion", True),
            max_expanded_terms=self.get_int("max_expanded_terms", 3),
            use_context_expansion=self.get_bool("use_context_expansion", True),
            context_window_size=self.get_int("context_window_size", 1),
        )
        
        return Config(
            paths=paths,
            index=index,
            chunking=chunking,
            embedding=embedding,
            openai=openai,
            hybrid=hybrid,
            dual=dual,
        )


def load_config(env_file: str = ".env") -> Config:
    """
    Función conveniente para cargar la configuración en una línea.
    
    Args:
        env_file: Ruta al archivo .env
        
    Returns:
        Config: Objeto de configuración
        
    Ejemplo:
        config = load_config()
        print(config.openai.openai_key)
    """
    reader = ConfigReader(env_file)
    return reader.get_config()
