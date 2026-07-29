"""
CHUNKING HÍBRIDO: Sintáctico + LLM
==================================

Estrategia inteligente para reducir costo y tiempo de chunking:
- Primero intenta chunking sintáctico (rápido, gratis)
- Si hay contenido visual (tablas, diagramas), usa LLM
- Fallback: GPT-4o mini si el modelo principal falla

Ahorra ~80% costo y 60% tiempo vs. chunking puro LLM.
"""

import os
import json
import fitz  # PyMuPDF
from typing import List, Dict, Any, Tuple
from enum import Enum
import fitz  # PyMuPDF
from langchain.text_splitter import RecursiveCharacterTextSplitter
from logger import Logger

logger = Logger.get_logger(__name__)


class ChunkingStrategy(Enum):
    """Estrategias de chunking disponibles."""
    SYNTACTIC = "syntactic"  # RecursiveCharacterTextSplitter
    LLM = "llm"  # GPT-4o with vision
    LLM_MINI = "llm_mini"  # GPT-4o mini (fallback, más barato)
    HYBRID = "hybrid"  # Inteligente (default)


class ContentAnalyzer:
    """
    Analiza contenido del PDF para determinar mejor estrategia de chunking.
    """
    
    @staticmethod
    def has_tables(page) -> bool:
        """
        Detecta si la página contiene tablas.
        Usa el detector nativo de PyMuPDF (find_tables, basado en líneas/rects
        reales de la página) y cae al heurístico de espaciado solo si falla.
        """
        try:
            tables = page.find_tables()
            if len(tables.tables) > 0:
                return True
            return False
        except Exception as e:
            logger.debug(f"find_tables falló, usando heurístico de espaciado: {e}")

        try:
            # Heurística simple: buscar alineación vertical de espacios
            text = page.get_text()
            lines = text.split('\n')
            aligned_cols = sum(1 for line in lines if '  ' in line)
            # Si hay muchas líneas con espacios alineados, probablemente hay tabla
            return (aligned_cols / len(lines)) > 0.3 if lines else False
        except Exception as e:
            logger.warning(f"Error detectando tablas: {e}")
            return False

    # Cantidad mínima de trazos vectoriales (líneas/curvas/rects) para considerar
    # que una página tiene un diagrama dibujado con vectores (no imagen rasterizada).
    MIN_DRAWINGS_FOR_COMPLEXITY = 15

    @staticmethod
    def has_vector_graphics(page) -> bool:
        """
        Detecta diagramas dibujados con primitivas vectoriales (líneas, curvas,
        rectángulos). get_images() NO los ve porque no son imágenes rasterizadas
        embebidas — es el caso típico de diagramas de cableado/planos exportados
        desde CAD, que de otro modo quedan con complejidad visual subestimada.
        """
        try:
            drawings = page.get_drawings()
            return len(drawings) >= ContentAnalyzer.MIN_DRAWINGS_FOR_COMPLEXITY
        except Exception as e:
            logger.debug(f"Error detectando gráficos vectoriales: {e}")
            return False
    
    @staticmethod
    def has_images(page) -> bool:
        """Detecta si la página contiene imágenes (incluyendo PDFs escaneados completos)."""
        try:
            # Método 1: Imágenes embebidas estándar
            image_list = page.get_images()
            if len(image_list) > 0:
                return True
            
            # Método 2: PDFs escaneados (toda la página es una imagen de fondo)
            # Renderizar página a pixmap y verificar si tiene contenido visual significativo
            text = (page.get_text() or "").strip()
            if len(text) < 50:  # Muy poco texto detectado
                try:
                    # Renderizar a baja resolución para análisis rápido
                    pix = page.get_pixmap(matrix=fitz.Matrix(0.2, 0.2))  # 20% del tamaño
                    
                    # Verificar si hay píxeles no blancos
                    samples = pix.samples
                    if samples:
                        # Contar píxeles no blancos (aproximación simple)
                        # Para RGB: blanco puro es (255, 255, 255)
                        non_white_pixels = sum(1 for i in range(0, len(samples), 3) 
                                             if not (samples[i] > 240 and samples[i+1] > 240 and samples[i+2] > 240))
                        
                        # Si >10% de píxeles no son blancos, hay contenido visual
                        total_pixels = pix.width * pix.height
                        if non_white_pixels > total_pixels * 0.1:
                            return True
                except Exception:
                    pass
            
            return False
        except Exception:
            return False
    
    @staticmethod
    def estimate_visual_complexity(page) -> float:
        """
        Estima complejidad visual de la página (0-1).

        Basado en:
        - Número de imágenes rasterizadas embebidas
        - Presencia de tablas
        - Gráficos vectoriales (diagramas dibujados con líneas/curvas, no rasterizados)

        Returns:
            float: 0 = simple (solo texto), 1 = muy complejo (muchas imágenes/tablas)
        """
        try:
            has_images = ContentAnalyzer.has_images(page)
            has_tables = ContentAnalyzer.has_tables(page)
            has_vectors = ContentAnalyzer.has_vector_graphics(page)

            complexity = 0.0

            if has_images:
                image_count = len(page.get_images())
                complexity += min(0.5, image_count * 0.1)  # Max 0.5

            if has_tables:
                complexity += 0.3

            if has_vectors:
                # Diagrama vectorial (cableado/planos) no capturado por has_images
                drawing_count = len(page.get_drawings())
                complexity += min(0.4, 0.2 + drawing_count * 0.005)  # Max 0.4

            return min(complexity, 1.0)
        except Exception as e:
            logger.warning(f"Error estimando complejidad: {e}")
            return 0.5  # Default: asumir complejidad media

    @staticmethod
    def analyze_page(page) -> Dict[str, Any]:
        """
        Análisis completo de una página.

        Returns:
            {
                "has_text": bool,
                "has_tables": bool,
                "has_images": bool,
                "has_vectors": bool,
                "visual_complexity": float,
                "recommended_strategy": ChunkingStrategy
            }
        """
        text = (page.get_text() or "").strip()
        has_text = len(text) > 100
        has_tables = ContentAnalyzer.has_tables(page)
        has_images = ContentAnalyzer.has_images(page)
        has_vectors = ContentAnalyzer.has_vector_graphics(page)
        complexity = ContentAnalyzer.estimate_visual_complexity(page)

        # Decidir estrategia
        if not has_text and not has_images and not has_vectors:
            strategy = ChunkingStrategy.SYNTACTIC  # Página vacía
        else:
            strategy = ChunkingStrategy.LLM

        return {
            "has_text": has_text,
            "has_tables": has_tables,
            "has_images": has_images,
            "has_vectors": has_vectors,
            "visual_complexity": complexity,
            "recommended_strategy": strategy
        }


class SyntacticChunker:
    """
    Chunking sintáctico: basado en estructura del texto.
    Rápido, gratis, pero no entiende contexto.
    """
    
    def __init__(self,
                 chunk_size: int = 1000,
                 chunk_overlap: int = 200,
                 separators: List[str] = None):
        """
        Args:
            chunk_size: Tamaño aproximado de chunk
            chunk_overlap: Superposición entre chunks
            separators: Separadores a usar
        """
        if separators is None:
            separators = [
                "\n\n",  # Párrafos
                "\n",    # Líneas
                ". ",    # Oraciones
                " ",     # Palabras
                ""       # Caracteres
            ]
        
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=separators,
            length_function=len,
        )
    
    def chunk(self, text: str, page_num: int = 1, file_name: str = "") -> List[Dict]:
        """
        Divide texto en chunks.
        
        Args:
            text: Texto a dividir
            page_num: Número de página (para metadata)
            file_name: Nombre del archivo (para metadata)
        
        Returns:
            Lista de chunks
        """
        chunks_text = self.splitter.split_text(text)
        
        chunks = []
        for i, chunk_text in enumerate(chunks_text):
            chunk = {
                "chunk_id": f"chunk_{i}",
                "original_chunk": chunk_text,
                "content_type": "text",
                "file_name": file_name,
                "page_num": str(page_num),
                "page_metadata": f"page {page_num}",
                "extraction_method": "syntactic_chunking",
                "confidence": 1.0  # Syntactic es determinístico
            }
            chunks.append(chunk)
        
        logger.debug(f"Syntactic chunking: {len(chunks_text)} chunks de página {page_num}")
        return chunks


class HybridChunkingStrategy:
    """
    Estrategia híbrida: decide inteligentemente cuándo usar sintáctico vs LLM.
    """
    
    def __init__(self,
                 llm_model: str = "gpt-4o",
                 llm_model_mini: str = "gpt-4o-mini",
                 complexity_threshold: float = 0.5):
        """
        Args:
            llm_model: Modelo principal (costoso, preciso)
            llm_model_mini: Modelo fallback (barato, rápido)
            complexity_threshold: Umbral de complejidad para usar LLM
        """
        self.llm_model = llm_model
        self.llm_model_mini = llm_model_mini
        self.complexity_threshold = complexity_threshold
        self.syntactic_chunker = SyntacticChunker()
    
    def decide_strategy(self, page, file_name: str = "") -> ChunkingStrategy:
        """
        Decide qué estrategia usar para esta página.
        
        Returns:
            ChunkingStrategy a usar
        """
        analysis = ContentAnalyzer.analyze_page(page)
        complexity = analysis["visual_complexity"]
        
        if complexity > self.complexity_threshold:
            logger.info(
                f"Página compleja (complexity={complexity:.2f}) → "
                f"Usando {self.llm_model}"
            )
            return ChunkingStrategy.LLM
        else:
            logger.info(
                f"Página simple (complexity={complexity:.2f}) → "
                f"Usando chunking sintáctico"
            )
            return ChunkingStrategy.SYNTACTIC
    
    def estimate_cost_and_time(self, 
                               num_pages: int,
                               simple_ratio: float = 0.8) -> Dict[str, Any]:
        """
        Estima costo y tiempo aproximado.
        
        Args:
            num_pages: Número de páginas del PDF
            simple_ratio: Ratio de páginas simples estimadas (0-1)
        
        Returns:
            {
                "estimated_cost_usd": float,
                "estimated_time_minutes": float,
                "breakdown": {...}
            }
        """
        complex_pages = int(num_pages * (1 - simple_ratio))
        simple_pages = int(num_pages * simple_ratio)
        
        # Costos (aproximados, según OpenAI pricing)
        # GPT-4o: $5 per 1M input tokens, $15 per 1M output tokens
        # GPT-4o mini: $0.15 per 1M input tokens, $0.6 per 1M output tokens
        cost_per_page_gpt4o = 0.03  # Aproximado
        cost_per_page_mini = 0.003  # 10x más barato
        
        # Tiempos (segundos por página)
        time_per_page_llm = 5  # Segundos
        time_per_page_syntactic = 0.5  # Muy rápido
        
        total_cost = (
            (complex_pages * cost_per_page_gpt4o) +
            (simple_pages * 0)  # Syntactic es gratis
        )
        
        total_time = (
            (complex_pages * time_per_page_llm) +
            (simple_pages * time_per_page_syntactic)
        ) / 60  # Convertir a minutos
        
        return {
            "estimated_cost_usd": total_cost,
            "estimated_time_minutes": total_time,
            "breakdown": {
                "simple_pages": simple_pages,
                "complex_pages": complex_pages,
                "cost_per_complex_page": cost_per_page_gpt4o,
                "cost_per_simple_page": 0
            },
            "comparison": {
                "pure_llm_cost": num_pages * cost_per_page_gpt4o,
                "savings": f"{(1 - total_cost / (num_pages * cost_per_page_gpt4o)) * 100:.1f}%"
            }
        }


# ============================================================
# EJEMPLO DE USO
# ============================================================
if __name__ == "__main__":
    import sys
    
    pdf_path = "/Users/francopiorno/Desktop/Proyectos/unq_rag/Ingestion/data/raw_data/example.pdf"
    
    if os.path.exists(pdf_path):
        # Analizar PDF
        strategy = HybridChunkingStrategy()
        
        with fitz.open(pdf_path) as doc:
            total_pages = len(doc)
            print(f"\n📄 Analizando {pdf_path} ({total_pages} páginas)\n")
            
            simple_count = 0
            complex_count = 0
            
            for idx in range(min(total_pages, 10)):  # Primeras 10 páginas
                page = doc[idx]
                analysis = ContentAnalyzer.analyze_page(page)
                chosen_strategy = strategy.decide_strategy(page)
                
                if analysis["visual_complexity"] < strategy.complexity_threshold:
                    simple_count += 1
                else:
                    complex_count += 1
                
                print(f"Página {idx + 1}:")
                print(f"  - Complejidad: {analysis['visual_complexity']:.2f}")
                print(f"  - Tiene tablas: {analysis['has_tables']}")
                print(f"  - Tiene imágenes: {analysis['has_images']}")
                print(f"  - Estrategia: {chosen_strategy.value}")
                print()
        
        # Estimación de costo
        simple_ratio = simple_count / min(total_pages, 10)
        estimate = strategy.estimate_cost_and_time(total_pages, simple_ratio)
        
        print(f"\n💰 ESTIMACIÓN PARA {total_pages} PÁGINAS")
        print(f"={' '*40}")
        print(f"Costo estimado:        ${estimate['estimated_cost_usd']:.2f}")
        print(f"Tiempo estimado:       {estimate['estimated_time_minutes']:.1f} minutos")
        print(f"\nComparación:")
        print(f"  Pure LLM cost:       ${estimate['comparison']['pure_llm_cost']:.2f}")
        print(f"  Hybrid savings:      {estimate['comparison']['savings']}")
    else:
        print(f"❌ Archivo no encontrado: {pdf_path}")
        print("Por favor, coloca un PDF en data/raw_data/")
