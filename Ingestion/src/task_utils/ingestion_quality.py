"""
CALIDAD DE INGESTA: Maximizar Calidad de Chunks Extraídos
==========================================================

Módulo focused en maximizar calidad durante la ingesta de documentos:
1. Validación exhaustiva de chunks extraídos
2. Métricas de calidad de ingesta
3. Reportes de ingesta
4. Detección de problemas estructurales
5. Estadísticas agregadas por documento
"""

import json
from typing import List, Dict, Any, Tuple
from dataclasses import dataclass
from enum import Enum
import numpy as np
from logger import Logger

logger = Logger.get_logger(__name__)


class ContentQualityLevel(Enum):
    """Niveles de calidad de contenido extraído."""
    EXCELLENT = 0.9  # Altamente estructurado, sin problemas
    GOOD = 0.7       # Bien extraído, algunos problemas menores
    FAIR = 0.5       # Extraído pero con problemas
    POOR = 0.3       # Problemas significativos
    UNUSABLE = 0.0   # No se puede usar


@dataclass
class IngestionMetrics:
    """Métricas de un PDF ingestionado."""
    file_name: str
    total_pages: int
    total_chunks: int
    valid_chunks: int
    invalid_chunks: int
    duplicate_chunks: int
    unique_chunks: int
    avg_chunk_length: float
    avg_confidence: float
    content_types: Dict[str, int]  # {"text": 45, "table": 5, "image": 2}
    
    # Cálculos derivados
    @property
    def validation_rate(self) -> float:
        """% de chunks que pasan validación."""
        return self.valid_chunks / self.total_chunks if self.total_chunks > 0 else 0.0
    
    @property
    def deduplication_savings(self) -> float:
        """% de chunks que fueron duplicados y eliminados."""
        unique_before_dedup = self.valid_chunks
        if unique_before_dedup == 0:
            return 0.0
        return (unique_before_dedup - self.unique_chunks) / unique_before_dedup
    
    @property
    def chunks_per_page(self) -> float:
        """Promedio de chunks por página."""
        return self.total_chunks / self.total_pages if self.total_pages > 0 else 0.0
    
    @property
    def overall_quality_score(self) -> float:
        """Score general de calidad (0-1)."""
        # Ponderado: validación (40%) + confidence (40%) + content diversity (20%)
        validation_score = self.validation_rate
        confidence_score = self.avg_confidence
        
        # Diversity: qué tan variados son los tipos de contenido
        content_variety = len(self.content_types)
        diversity_score = min(content_variety / 3, 1.0)  # Max 3 tipos
        
        score = (
            validation_score * 0.4 +
            confidence_score * 0.4 +
            diversity_score * 0.2
        )
        
        return min(max(score, 0.0), 1.0)


class IngestionQualityAnalyzer:
    """
    Analiza calidad de chunks extraídos durante ingesta.
    """
    
    def __init__(self):
        self.problems_detected: List[str] = []
        self.warnings: List[str] = []
    
    def analyze_chunks(self, chunks: List[Dict[str, Any]],
                      file_name: str = "") -> IngestionMetrics:
        """
        Analiza batch de chunks y retorna métricas de calidad.
        
        Args:
            chunks: Chunks extraídos (antes de validación/dedup)
            file_name: Nombre del archivo procesado
        
        Returns:
            IngestionMetrics con análisis completo
        """
        self.problems_detected = []
        self.warnings = []
        
        total_chunks = len(chunks)
        valid_chunks = 0
        unique_chunks = 0
        content_types = {}
        confidence_scores = []
        chunk_lengths = []
        
        # Tracking de duplicados
        seen_hashes = {}
        
        for chunk in chunks:
            # 1. Validación básica
            if self._is_valid_chunk(chunk):
                valid_chunks += 1
            else:
                self.warnings.append(
                    f"Chunk inválido: {chunk.get('chunk_id')} - "
                    f"{self._get_validation_reason(chunk)}"
                )
            
            # 2. Deduplicación
            text = str(chunk.get("original_chunk", "")).strip()
            text_hash = hash(text)
            
            if text_hash not in seen_hashes:
                seen_hashes[text_hash] = True
                unique_chunks += 1
            
            # 3. Contabilizar tipos de contenido
            content_type = chunk.get("content_type", "text")
            content_types[content_type] = content_types.get(content_type, 0) + 1
            
            # 4. Recolectar métricas
            confidence_scores.append(float(chunk.get("confidence", 0.8)))
            chunk_lengths.append(len(text))
        
        # Calcular promedios
        avg_confidence = np.mean(confidence_scores) if confidence_scores else 0.0
        avg_length = np.mean(chunk_lengths) if chunk_lengths else 0.0
        
        # Detectar problemas
        self._detect_problems(
            total_chunks=total_chunks,
            valid_chunks=valid_chunks,
            avg_confidence=avg_confidence,
            avg_length=avg_length,
            content_types=content_types
        )
        
        return IngestionMetrics(
            file_name=file_name,
            total_pages=1,  # Se asigna después
            total_chunks=total_chunks,
            valid_chunks=valid_chunks,
            invalid_chunks=total_chunks - valid_chunks,
            duplicate_chunks=total_chunks - unique_chunks,
            unique_chunks=unique_chunks,
            avg_chunk_length=avg_length,
            avg_confidence=avg_confidence,
            content_types=content_types
        )
    
    @staticmethod
    def _is_valid_chunk(chunk: Dict[str, Any]) -> bool:
        """Valida estructura básica de chunk."""
        # Debe tener contenido
        text = str(chunk.get("original_chunk", "")).strip()
        if len(text) < 50:
            return False
        
        # Debe tener metadata mínima
        if "chunk_id" not in chunk:
            return False
        
        # Confidence debe ser razonable
        confidence = float(chunk.get("confidence", 1.0))
        if confidence < 0.3:
            return False
        
        return True
    
    @staticmethod
    def _get_validation_reason(chunk: Dict[str, Any]) -> str:
        """Explica por qué un chunk es inválido."""
        text = str(chunk.get("original_chunk", "")).strip()
        
        if len(text) < 50:
            return f"Muy corto ({len(text)} caracteres)"
        
        if "chunk_id" not in chunk:
            return "Sin chunk_id"
        
        confidence = float(chunk.get("confidence", 1.0))
        if confidence < 0.3:
            return f"Confianza muy baja ({confidence:.2f})"
        
        return "Motivo desconocido"
    
    def _detect_problems(self, 
                        total_chunks: int,
                        valid_chunks: int,
                        avg_confidence: float,
                        avg_length: float,
                        content_types: Dict[str, int]):
        """Detecta problemas potenciales en la ingesta."""
        
        # Problema 1: Tasa de validación baja
        validation_rate = valid_chunks / total_chunks if total_chunks > 0 else 0
        if validation_rate < 0.8:
            self.problems_detected.append(
                f"⚠️ Tasa de validación baja ({validation_rate:.1%}). "
                f"Considera revisar el modelo o los parámetros de chunking."
            )
        
        # Problema 2: Confianza baja
        if avg_confidence < 0.6:
            self.problems_detected.append(
                f"⚠️ Confianza promedio baja ({avg_confidence:.2f}). "
                f"El modelo de extracción tiene dudas. Revisa OCR si es necesario."
            )
        
        # Problema 3: Chunks muy cortos
        if avg_length < 100:
            self.problems_detected.append(
                f"⚠️ Chunks muy cortos en promedio ({avg_length:.0f} chars). "
                f"Considera aumentar chunk_size en configuración."
            )
        
        # Problema 4: Chunks muy largos
        if avg_length > 3000:
            self.problems_detected.append(
                f"⚠️ Chunks muy largos ({avg_length:.0f} chars). "
                f"Pueden ser difíciles de procesar. Reduce chunk_size."
            )
        
        # Problema 5: Sin variedad de contenido
        if len(content_types) == 1:
            content_type = list(content_types.keys())[0]
            if content_type != "text":
                self.problems_detected.append(
                    f"⚠️ Solo se detectó contenido tipo '{content_type}'. "
                    f"Verifica si el PDF tiene tablas/imágenes que NO se extraen."
                )
        
        # Problema 6: Muchas imágenes sin descripción
        image_count = content_types.get("image", 0)
        if image_count > total_chunks * 0.3:
            self.problems_detected.append(
                f"⚠️ Muchas imágenes ({image_count}) sin descripción textual. "
                f"Considera usar OCR o descripción manual."
            )
    
    def get_report(self, metrics: IngestionMetrics) -> Dict[str, Any]:
        """Genera reporte completo de ingesta."""
        quality_level = self._assess_quality_level(metrics)
        
        return {
            "file": metrics.file_name,
            "summary": {
                "total_chunks": metrics.total_chunks,
                "valid_chunks": metrics.valid_chunks,
                "unique_chunks": metrics.unique_chunks,
                "chunks_per_page": f"{metrics.chunks_per_page:.1f}",
            },
            "quality": {
                "overall_score": f"{metrics.overall_quality_score:.2f}",
                "quality_level": quality_level.name,
                "validation_rate": f"{metrics.validation_rate:.1%}",
                "deduplication_savings": f"{metrics.deduplication_savings:.1%}",
                "avg_confidence": f"{metrics.avg_confidence:.2f}",
                "avg_chunk_length": f"{metrics.avg_chunk_length:.0f} chars",
            },
            "content_composition": metrics.content_types,
            "issues": {
                "invalid_chunks": metrics.invalid_chunks,
                "duplicate_chunks": metrics.duplicate_chunks,
                "problems": self.problems_detected,
                "warnings": self.warnings[:5],  # Top 5 warnings
            },
            "recommendations": self._generate_recommendations(metrics),
        }
    
    @staticmethod
    def _assess_quality_level(metrics: IngestionMetrics) -> ContentQualityLevel:
        """Asigna nivel de calidad basado en métricas."""
        score = metrics.overall_quality_score
        
        if score >= ContentQualityLevel.EXCELLENT.value:
            return ContentQualityLevel.EXCELLENT
        elif score >= ContentQualityLevel.GOOD.value:
            return ContentQualityLevel.GOOD
        elif score >= ContentQualityLevel.FAIR.value:
            return ContentQualityLevel.FAIR
        elif score >= ContentQualityLevel.POOR.value:
            return ContentQualityLevel.POOR
        else:
            return ContentQualityLevel.UNUSABLE
    
    @staticmethod
    def _generate_recommendations(metrics: IngestionMetrics) -> List[str]:
        """Genera recomendaciones para mejorar ingesta."""
        recommendations = []
        
        if metrics.validation_rate < 0.9:
            recommendations.append("📌 Aumentar strictness en validación de chunks")
        
        if metrics.avg_confidence < 0.75:
            recommendations.append("🔍 Revisar OCR o usar modelo de visión más preciso")
        
        if metrics.deduplication_savings > 0.15:
            recommendations.append("🔄 Alta tasa de duplicados - considera overlapping en chunks")
        
        if metrics.chunks_per_page < 2:
            recommendations.append("✂️ Muy pocos chunks por página - reduce chunk size")
        
        if metrics.chunks_per_page > 10:
            recommendations.append("📄 Muchos chunks por página - aumenta chunk size")
        
        if metrics.avg_chunk_length < 200:
            recommendations.append("➕ Chunks muy cortos - aumenta chunk_size en config")
        
        # Good news
        if metrics.overall_quality_score >= 0.85:
            recommendations.append("✅ Excelente calidad de ingesta")
        
        return recommendations


class IngestionStatistics:
    """
    Estadísticas agregadas de múltiples ingestiones.
    """
    
    def __init__(self):
        self.metrics_history: List[IngestionMetrics] = []
    
    def add_metrics(self, metrics: IngestionMetrics):
        """Agregar métricas de una ingesta."""
        self.metrics_history.append(metrics)
    
    def get_aggregate_report(self) -> Dict[str, Any]:
        """Reporte agregado de todas las ingestiones."""
        if not self.metrics_history:
            return {"message": "Sin ingestiones registradas"}
        
        scores = [m.overall_quality_score for m in self.metrics_history]
        validation_rates = [m.validation_rate for m in self.metrics_history]
        confidences = [m.avg_confidence for m in self.metrics_history]
        
        return {
            "total_ingestions": len(self.metrics_history),
            "total_chunks": sum(m.total_chunks for m in self.metrics_history),
            "total_unique_chunks": sum(m.unique_chunks for m in self.metrics_history),
            "quality": {
                "avg_quality_score": f"{np.mean(scores):.2f}",
                "min_quality_score": f"{np.min(scores):.2f}",
                "max_quality_score": f"{np.max(scores):.2f}",
                "avg_validation_rate": f"{np.mean(validation_rates):.1%}",
                "avg_confidence": f"{np.mean(confidences):.2f}",
            },
            "files": [
                {
                    "name": m.file_name,
                    "chunks": m.total_chunks,
                    "quality_score": f"{m.overall_quality_score:.2f}"
                }
                for m in self.metrics_history
            ]
        }


# ============================================================
# EJEMPLO DE USO
# ============================================================
if __name__ == "__main__":
    # Simulación con chunks
    sample_chunks = [
        {
            "chunk_id": "chunk_1",
            "original_chunk": "Este es el contenido del primer chunk con suficiente longitud.",
            "content_type": "text",
            "confidence": 0.95,
        },
        {
            "chunk_id": "chunk_2",
            "original_chunk": "x" * 100,  # Demasiado corto
            "content_type": "text",
            "confidence": 0.85,
        },
        {
            "chunk_id": "chunk_3",
            "original_chunk": "Segundo chunk con contenido relevante útil para el análisis.",
            "content_type": "text",
            "confidence": 0.90,
        },
        {
            "chunk_id": "table_1",
            "original_chunk": "| Columna 1 | Columna 2 | Fila 1 | Valor 1 |",
            "content_type": "table",
            "confidence": 0.88,
        },
    ]
    
    # Analizar
    analyzer = IngestionQualityAnalyzer()
    metrics = analyzer.analyze_chunks(sample_chunks, file_name="documento.pdf")
    
    # Generar reporte
    report = analyzer.get_report(metrics)
    
    print("\n📊 REPORTE DE CALIDAD DE INGESTA")
    print("=" * 50)
    print(json.dumps(report, indent=2, ensure_ascii=False))
