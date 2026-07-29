"""
Módulo de configuración para el pipeline RAG multimodal.
Exporta las clases principales para fácil importación.
"""

from .config import (
    Config,
    PathConfig,
    IndexConfig,
    ChunkingConfig,
    EmbeddingConfig,
    OpenAIConfig,
    DEFAULT_CONFIG,
)

from .config_reader import (
    ConfigReader,
    load_config,
)

__all__ = [
    "Config",
    "PathConfig",
    "IndexConfig",
    "ChunkingConfig",
    "EmbeddingConfig",
    "OpenAIConfig",
    "DEFAULT_CONFIG",
    "ConfigReader",
    "load_config",
]
