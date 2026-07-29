"""
Re-Chunker Semántico Cross-Page
===============================

Agrupa chunks relacionados que abarcan múltiples páginas:
- Procedimientos multi-página
- Especificaciones técnicas largas
- Descripciones de equipos
- Mantiene chunks originales + crea super-chunks
"""

import numpy as np
from typing import List, Dict, Any, Tuple, Optional
from sklearn.metrics.pairwise import cosine_similarity

from logger import Logger

logger = Logger.get_logger(__name__)


class SemanticRechunker:
    """
    Re-agrupa chunks usando similaridad semántica para crear
    super-chunks que abarcan múltiples páginas cuando tiene sentido.
    """
    
    def __init__(self,
                 similarity_threshold: float = 0.85,
                 max_pages_gap: int = 2,
                 max_superchunk_size: int = 3000,
                 min_chunks_for_merge: int = 2):
        """
        Args:
            similarity_threshold: Umbral de similaridad de embeddings (0-1)
            max_pages_gap: Máximo gap de páginas para fusionar
            max_superchunk_size: Tamaño máximo de super-chunk en caracteres
            min_chunks_for_merge: Mínimo de chunks para crear super-chunk
        """
        self.similarity_threshold = similarity_threshold
        self.max_pages_gap = max_pages_gap
        self.max_superchunk_size = max_superchunk_size
        self.min_chunks_for_merge = min_chunks_for_merge
    
    def find_related_chunks(self,
                           chunks: List[Dict[str, Any]],
                           embeddings: Optional[np.ndarray] = None) -> List[List[int]]:
        """
        Encuentra grupos de chunks relacionados.
        
        Args:
            chunks: Lista de chunks con metadata
            embeddings: Embeddings de chunks (opcional, si ya están calculados)
            
        Returns:
            Lista de grupos, cada grupo es lista de índices de chunks
            Ejemplo: [[0, 1, 2], [5, 6], [10, 11, 12]]
        """
        if not chunks:
            return []
        
        # Si no hay embeddings, no podemos hacer clustering semántico
        if embeddings is None:
            logger.warning("No hay embeddings, usando solo proximidad de páginas")
            return self._group_by_page_proximity(chunks)
        
        # Calcular matriz de similaridad
        similarity_matrix = cosine_similarity(embeddings)
        
        # Agrupar chunks similares y consecutivos
        groups = []
        visited = set()
        
        for i in range(len(chunks)):
            if i in visited:
                continue
            
            # Iniciar nuevo grupo
            group = [i]
            visited.add(i)
            
            # Buscar chunks similares consecutivos
            current_page = self._get_page_num(chunks[i])
            
            for j in range(i + 1, len(chunks)):
                if j in visited:
                    continue
                
                next_page = self._get_page_num(chunks[j])
                
                # Verificar proximidad de página
                if abs(next_page - current_page) > self.max_pages_gap:
                    break
                
                # Verificar similaridad semántica
                if similarity_matrix[i][j] >= self.similarity_threshold:
                    group.append(j)
                    visited.add(j)
                    current_page = next_page
            
            # Solo guardar grupos con suficientes chunks
            if len(group) >= self.min_chunks_for_merge:
                groups.append(group)
        
        logger.info(f"Encontrados {len(groups)} grupos de chunks relacionados")
        return groups
    
    def _is_mergeable(self, chunk: Dict[str, Any]) -> bool:
        """
        Solo los chunks de texto plano son candidatos a fusionarse en super-chunks.
        Tablas e imágenes deben preservar su content_type y su estructura (table_json,
        image_path, etc.); combinarlos como texto plano rompe esa estructura y hace
        que el chunk resultante quede mal etiquetado como "superchunk" en vez de
        "table"/"image".
        """
        return str(chunk.get("content_type", "text")).lower() == "text"

    def _group_by_page_proximity(self, chunks: List[Dict[str, Any]]) -> List[List[int]]:
        """
        Agrupa chunks solo por proximidad de páginas (fallback sin embeddings).
        """
        groups = []
        current_group = []
        last_page = None
        
        for i, chunk in enumerate(chunks):
            page = self._get_page_num(chunk)
            
            if last_page is None or page - last_page <= self.max_pages_gap:
                current_group.append(i)
            else:
                if len(current_group) >= self.min_chunks_for_merge:
                    groups.append(current_group)
                current_group = [i]
            
            last_page = page
        
        # Último grupo
        if len(current_group) >= self.min_chunks_for_merge:
            groups.append(current_group)
        
        return groups
    
    def create_superchunks(self,
                          chunks: List[Dict[str, Any]],
                          groups: List[List[int]]) -> List[Dict[str, Any]]:
        """
        Crea super-chunks a partir de grupos de chunks relacionados.
        
        Args:
            chunks: Lista de chunks originales
            groups: Grupos de índices de chunks relacionados
            
        Returns:
            Lista de super-chunks
        """
        superchunks = []
        
        for group_idx, group in enumerate(groups):
            if len(group) < self.min_chunks_for_merge:
                continue
            
            # Reunir texto de todos los chunks
            grouped_chunks = [chunks[i] for i in group]
            combined_text = self._combine_chunks_text(grouped_chunks)
            
            # Verificar tamaño
            if len(combined_text) > self.max_superchunk_size:
                # Dividir en sub-grupos más pequeños
                sub_superchunks = self._split_large_superchunk(
                    grouped_chunks,
                    combined_text
                )
                superchunks.extend(sub_superchunks)
            else:
                # Crear super-chunk
                superchunk = self._build_superchunk(
                    grouped_chunks,
                    combined_text,
                    group_idx
                )
                superchunks.append(superchunk)
        
        logger.info(f"Creados {len(superchunks)} super-chunks")
        return superchunks
    
    def _combine_chunks_text(self, chunks: List[Dict[str, Any]]) -> str:
        """Combina texto de múltiples chunks."""
        texts = []
        
        for chunk in chunks:
            text = str(chunk.get("original_chunk", "")).strip()
            if text:
                texts.append(text)
        
        return "\n\n".join(texts)
    
    def _build_superchunk(self,
                         chunks: List[Dict[str, Any]],
                         combined_text: str,
                         group_idx: int) -> Dict[str, Any]:
        """Construye un super-chunk."""
        # Metadata del primer chunk
        first_chunk = chunks[0]
        last_chunk = chunks[-1]
        
        page_start = self._get_page_num(first_chunk)
        page_end = self._get_page_num(last_chunk)
        
        superchunk = {
            "chunk_id": f"superchunk_{group_idx}",
            "original_chunk": combined_text,
            "content_type": "superchunk",
            "file_name": first_chunk.get("file_name"),
            "page_num": f"{page_start}-{page_end}",
            "page_metadata": f"pages {page_start}-{page_end}",
            "is_superchunk": True,
            "constituent_chunks": [c.get("chunk_id") for c in chunks],
            "num_merged_chunks": len(chunks),
            "page_range": {
                "start": page_start,
                "end": page_end
            }
        }
        
        # Heredar metadata jerárquica del primer chunk
        for field in ["document_section", "document_chapter", "section_number"]:
            if field in first_chunk:
                superchunk[field] = first_chunk[field]
        
        return superchunk
    
    def _split_large_superchunk(self,
                               chunks: List[Dict[str, Any]],
                               combined_text: str) -> List[Dict[str, Any]]:
        """Divide super-chunk muy grande en partes más pequeñas."""
        # Estrategia simple: dividir chunks en grupos más pequeños
        max_chunks_per_group = len(chunks) // 2
        
        sub_groups = [
            chunks[i:i + max_chunks_per_group]
            for i in range(0, len(chunks), max_chunks_per_group)
        ]
        
        superchunks = []
        for i, sub_group in enumerate(sub_groups):
            text = self._combine_chunks_text(sub_group)
            superchunk = self._build_superchunk(sub_group, text, i)
            superchunk["chunk_id"] = f"{superchunk['chunk_id']}_part{i+1}"
            superchunks.append(superchunk)
        
        return superchunks
    
    def _get_page_num(self, chunk: Dict[str, Any]) -> int:
        """Extrae número de página de un chunk."""
        page = chunk.get("page_num", "1")
        
        # Manejar rangos (ej: "1-3")
        if isinstance(page, str) and "-" in page:
            page = page.split("-")[0]
        
        try:
            return int(page)
        except (ValueError, TypeError):
            return 1
    
    def process(self,
                chunks: List[Dict[str, Any]],
                embeddings: Optional[np.ndarray] = None) -> Tuple[List[Dict], List[Dict]]:
        """
        Procesa chunks para crear super-chunks.
        
        Args:
            chunks: Chunks originales
            embeddings: Embeddings opcionales
            
        Returns:
            (chunks_originales, superchunks_creados)
        """
        # Tablas/imágenes nunca se fusionan: solo el texto narrativo es candidato
        # a formar super-chunks cross-page (ver _is_mergeable).
        mergeable_indices = [i for i, c in enumerate(chunks) if self._is_mergeable(c)]
        mergeable_chunks = [chunks[i] for i in mergeable_indices]
        mergeable_embeddings = (
            embeddings[mergeable_indices] if embeddings is not None else None
        )

        # Encontrar grupos relacionados
        groups = self.find_related_chunks(mergeable_chunks, mergeable_embeddings)

        # Crear super-chunks
        superchunks = self.create_superchunks(mergeable_chunks, groups)
        
        logger.info(
            f"Re-chunking semántico: {len(chunks)} chunks originales → "
            f"{len(superchunks)} super-chunks adicionales"
        )
        
        return chunks, superchunks


class ProcedureDetector:
    """
    Detector especializado para procedimientos multi-página.
    """
    
    @staticmethod
    def is_procedure_chunk(chunk: Dict[str, Any]) -> bool:
        """Detecta si un chunk es parte de un procedimiento."""
        text = str(chunk.get("original_chunk", "")).lower()
        
        procedure_keywords = [
            r'\bstep\s+\d+',
            r'\bpaso\s+\d+',
            r'procedure',
            r'procedimiento',
            r'installation',
            r'instalación',
            r'configure',
            r'configurar'
        ]
        
        import re
        return any(re.search(pattern, text) for pattern in procedure_keywords)
    
    @staticmethod
    def group_procedure_chunks(chunks: List[Dict[str, Any]]) -> List[List[int]]:
        """
        Agrupa chunks de procedimientos.
        
        Detecta secuencias de pasos numerados que abarcan múltiples páginas.
        """
        groups = []
        current_group = []
        
        for i, chunk in enumerate(chunks):
            if ProcedureDetector.is_procedure_chunk(chunk):
                current_group.append(i)
            else:
                if len(current_group) >= 2:
                    groups.append(current_group)
                current_group = []
        
        # Último grupo
        if len(current_group) >= 2:
            groups.append(current_group)
        
        return groups
