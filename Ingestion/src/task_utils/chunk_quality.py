"""
MEJORAS IMPLEMENTADAS: Validación, Deduplicación y Enriquecimiento de Chunks
============================================================================

Módulo auxiliar que agrega validación de calidad al pipeline existente.
Se integra después del chunking y antes de embeddings.
"""

import hashlib
import json
from typing import List, Dict, Any, Tuple
from difflib import SequenceMatcher
from logger import Logger

logger = Logger.get_logger(__name__)


class ChunkValidator:
    """
    Valida y filtra chunks según reglas de calidad.
    """
    
    def __init__(self, 
                 min_length: int = 50,
                 max_length: int = 8000,
                 confidence_threshold: float = 0.0):
        """
        Args:
            min_length: Mínimo de caracteres válido
            max_length: Máximo de caracteres válido
            confidence_threshold: Confianza mínima del LLM (0-1)
        """
        self.min_length = min_length
        self.max_length = max_length
        self.confidence_threshold = confidence_threshold
    
    def validate_chunk(self, chunk: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Valida un solo chunk.
        
        Returns:
            (is_valid, reason)
        """
        # Verificar campo obligatorio
        if "original_chunk" not in chunk:
            return False, "missing_original_chunk"
        
        text = str(chunk.get("original_chunk", "")).strip()
        
        # Verificar longitud
        if len(text) < self.min_length:
            return False, f"too_short ({len(text)} < {self.min_length})"
        
        if len(text) > self.max_length:
            return False, f"too_long ({len(text)} > {self.max_length})"
        
        # Verificar confianza (si el chunk tiene confidence score)
        confidence = chunk.get("confidence", 1.0)
        if isinstance(confidence, str):
            try:
                confidence = float(confidence)
            except ValueError:
                confidence = 1.0
        
        if confidence < self.confidence_threshold:
            return False, f"low_confidence ({confidence:.2f} < {self.confidence_threshold})"
        
        # Verificar que no sea basura (solo espacios/caracteres especiales)
        if not any(c.isalnum() for c in text):
            return False, "no_alphanumeric"
        
        return True, "valid"
    
    def validate_chunks(self, chunks: List[Dict]) -> Tuple[List[Dict], Dict[str, int]]:
        """
        Valida una lista de chunks.
        
        Returns:
            (valid_chunks, stats)
        """
        valid = []
        stats = {
            "total": len(chunks),
            "valid": 0,
            "invalid": 0,
            "reasons": {}
        }
        
        for chunk in chunks:
            is_valid, reason = self.validate_chunk(chunk)
            stats["reasons"][reason] = stats["reasons"].get(reason, 0) + 1
            
            if is_valid:
                valid.append(chunk)
                stats["valid"] += 1
            else:
                stats["invalid"] += 1
        
        logger.info(f"Validación: {stats['valid']}/{stats['total']} chunks válidos")
        if stats["reasons"]:
            logger.debug(f"Razones de rechazo: {stats['reasons']}")
        
        return valid, stats


class ChunkDeduplicator:
    """
    Detecta y elimina chunks duplicados o muy similares.
    """
    
    def __init__(self, similarity_threshold: float = 0.85):
        """
        Args:
            similarity_threshold: Umbral de similitud (0-1) para considerar duplicado
        """
        self.similarity_threshold = similarity_threshold
    
    @staticmethod
    def _compute_hash(text: str) -> str:
        """Computa hash MD5 del texto."""
        return hashlib.md5(text.encode()).hexdigest()
    
    @staticmethod
    def _text_similarity(text1: str, text2: str) -> float:
        """Calcula similitud entre dos textos usando SequenceMatcher."""
        # Normalizar
        t1 = text1.lower().strip()
        t2 = text2.lower().strip()
        
        if t1 == t2:
            return 1.0
        
        ratio = SequenceMatcher(None, t1, t2).ratio()
        return ratio
    
    def deduplicate(self, chunks: List[Dict]) -> Tuple[List[Dict], Dict[str, Any]]:
        """
        Elimina duplicados y muy similares.
        
        Returns:
            (deduplicated_chunks, stats)
        """
        if not chunks:
            return [], {"total": 0, "unique": 0, "duplicates_removed": 0}
        
        seen_hashes = {}
        unique = []
        stats = {
            "total": len(chunks),
            "unique": 0,
            "duplicates_removed": 0,
            "similarity_removed": 0
        }
        
        for chunk in chunks:
            text = str(chunk.get("original_chunk", "")).strip()
            text_hash = self._compute_hash(text)

            # Verificar hash exacto
            if text_hash in seen_hashes:
                stats["duplicates_removed"] += 1
                continue

            # La similitud por texto SOLO aplica a texto/tablas. Para chunks de
            # imagen, "original_chunk" es {"bbox","image_path","notes"}: dos
            # fotos distintas de la misma figura (o dos fotos bajo el mismo pie
            # "Figura N: ...") tienen 'notes' idéntico o casi idéntico y el
            # JSON entero da sim≈1.0 aunque 'image_path' sea distinto — eso
            # borraba imágenes reales y distintas por "duplicadas". Una imagen
            # sólo es duplicado exacto si coincide el hash completo (ya
            # verificado arriba); nunca por similitud de su descripción.
            content_type = str(chunk.get("content_type", "")).lower()
            skip_similarity_check = content_type in ("image", "table", "diagram_visual")

            # Verificar similitud con chunks anteriores
            is_duplicate = False
            if not skip_similarity_check:
                for unique_chunk in unique:
                    unique_text = str(unique_chunk.get("original_chunk", "")).strip()
                    sim = self._text_similarity(text, unique_text)

                    if sim >= self.similarity_threshold:
                        logger.debug(f"Chunk similar removido (sim={sim:.2f}): {text[:50]}...")
                        is_duplicate = True
                        stats["similarity_removed"] += 1
                        break

            if not is_duplicate:
                seen_hashes[text_hash] = True
                unique.append(chunk)
                stats["unique"] += 1
        
        logger.info(f"Deduplicación: {stats['unique']}/{stats['total']} chunks únicos")
        return unique, stats


class ChunkEnricher:
    """
    Enriquece chunks con metadatos adicionales.
    """
    
    @staticmethod
    def add_metadata(chunks: List[Dict], 
                     source_file: str = "",
                     document_id: str = "",
                     embedding_model: str = "text-embedding-3-large",
                     model_version: str = "gpt-4o-2024-08") -> List[Dict]:
        """
        Agrega metadatos a chunks.
        
        Args:
            chunks: Lista de chunks
            source_file: Nombre del archivo fuente
            document_id: ID del documento
            embedding_model: Modelo usado para embeddings
            model_version: Versión del modelo de LLM
        
        Returns:
            Chunks enriquecidos
        """
        from datetime import datetime
        
        timestamp = datetime.utcnow().isoformat()
        
        enriched = []
        for i, chunk in enumerate(chunks):
            # Copiar chunk original
            enriched_chunk = chunk.copy()
            
            # Agregar metadatos si no existen
            if "source_file" not in enriched_chunk:
                enriched_chunk["source_file"] = source_file
            
            if "document_id" not in enriched_chunk:
                enriched_chunk["document_id"] = document_id
            
            if "chunk_index" not in enriched_chunk:
                enriched_chunk["chunk_index"] = i
            
            if "created_at" not in enriched_chunk:
                enriched_chunk["created_at"] = timestamp
            
            if "embedding_model" not in enriched_chunk:
                enriched_chunk["embedding_model"] = embedding_model
            
            if "model_version" not in enriched_chunk:
                enriched_chunk["model_version"] = model_version
            
            # Calcular hash para tracking
            if "content_hash" not in enriched_chunk:
                text = str(enriched_chunk.get("original_chunk", ""))
                enriched_chunk["content_hash"] = ChunkDeduplicator._compute_hash(text)
            
            enriched.append(enriched_chunk)
        
        logger.info(f"Metadatos agregados a {len(enriched)} chunks")
        return enriched


class ChunkQualityPipeline:
    """
    Pipeline completo: Validar → Deduplicar → Enriquecer
    """
    
    def __init__(self,
                 min_chunk_length: int = 50,
                 max_chunk_length: int = 8000,
                 confidence_threshold: float = 0.0,
                 similarity_threshold: float = 0.85):
        self.validator = ChunkValidator(
            min_length=min_chunk_length,
            max_length=max_chunk_length,
            confidence_threshold=confidence_threshold
        )
        self.deduplicator = ChunkDeduplicator(
            similarity_threshold=similarity_threshold
        )
    
    def process(self, chunks: List[Dict], 
                source_file: str = "",
                document_id: str = "") -> Tuple[List[Dict], Dict[str, Any]]:
        """
        Procesa chunks a través del pipeline completo.
        
        Args:
            chunks: Lista de chunks del chunking task
            source_file: Nombre del archivo fuente
            document_id: ID del documento
        
        Returns:
            (processed_chunks, quality_report)
        """
        report = {}
        
        # Paso 1: Validar
        logger.info("Iniciando validación de chunks...")
        valid_chunks, validation_stats = self.validator.validate_chunks(chunks)
        report["validation"] = validation_stats
        
        # Paso 2: Deduplicar
        logger.info("Iniciando deduplicación...")
        unique_chunks, dedup_stats = self.deduplicator.deduplicate(valid_chunks)
        report["deduplication"] = dedup_stats
        
        # Paso 3: Enriquecer
        logger.info("Enriqueciendo chunks con metadatos...")
        enriched_chunks = ChunkEnricher.add_metadata(
            unique_chunks,
            source_file=source_file,
            document_id=document_id
        )
        
        # Resumen
        report["summary"] = {
            "original_count": len(chunks),
            "valid_count": len(valid_chunks),
            "unique_count": len(unique_chunks),
            "final_count": len(enriched_chunks),
            "quality_retention": f"{len(enriched_chunks) / len(chunks) * 100:.1f}%" if chunks else "0%"
        }
        
        logger.info(f"Pipeline completado: {report['summary']}")
        
        return enriched_chunks, report


# ============================================================
# EJEMPLO DE USO
# ============================================================
if __name__ == "__main__":
    # Simular chunks del LLM
    sample_chunks = [
        {
            "chunk_id": "chunk_1",
            "original_chunk": "Este es un chunk de prueba con contenido válido.",
            "content_type": "text",
            "page_num": 1,
            "confidence": 0.95
        },
        {
            "chunk_id": "chunk_2",
            "original_chunk": "Este es un chunk de prueba con contenido válido.",  # Duplicado
            "content_type": "text",
            "page_num": 1,
            "confidence": 0.92
        },
        {
            "chunk_id": "chunk_3",
            "original_chunk": "x",  # Demasiado corto
            "content_type": "text",
            "page_num": 2,
            "confidence": 0.5
        },
        {
            "chunk_id": "chunk_4",
            "original_chunk": "Este es un chunk muy similar con contenido válido y parecido.",
            "content_type": "text",
            "page_num": 2,
            "confidence": 0.88
        }
    ]
    
    # Procesar
    pipeline = ChunkQualityPipeline()
    processed, report = pipeline.process(
        sample_chunks,
        source_file="example.pdf",
        document_id="doc_001"
    )
    
    print(json.dumps(report, indent=2))
    print(f"\nChunks procesados: {len(processed)}")
    for chunk in processed:
        print(f"  - {chunk['chunk_id']}: {chunk['original_chunk'][:50]}...")
