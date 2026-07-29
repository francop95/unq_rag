"""
Procesador Especializado para Diagramas Eléctricos
==================================================

Estrategia multi-faceta para diagramas técnicos:
1. CLIP embedding (búsqueda visual)
2. OCR selectivo (componentes, valores, labels)
3. GPT-4o Vision (descripción estructurada)
4. Indexado triple para retrieval multi-faceta
"""

import os
import json
from typing import List, Dict, Any, Tuple, Optional
from PIL import Image
import io
import re

from logger import Logger

logger = Logger.get_logger(__name__)

try:
    import pytesseract
    _HAS_OCR = True
except ImportError:
    _HAS_OCR = False
    logger.warning("pytesseract no disponible. OCR deshabilitado para diagramas.")


class DiagramType:
    """Tipos de diagramas eléctricos comunes."""
    WIRING_DIAGRAM = "wiring_diagram"
    SCHEMATIC = "electrical_schematic"
    BLOCK_DIAGRAM = "block_diagram"
    CONNECTION_DIAGRAM = "connection_diagram"
    CIRCUIT_DIAGRAM = "circuit_diagram"
    LAYOUT = "electrical_layout"
    UNKNOWN = "diagram"


class ElectricalDiagramProcessor:
    """
    Procesador avanzado para diagramas eléctricos.
    
    Genera 3 chunks complementarios por diagrama:
    - Visual chunk (CLIP embedding de la imagen completa)
    - Text chunk (OCR de componentes, valores, labels)
    - Structured chunk (descripción estructurada GPT-4o)
    """
    
    def __init__(self, 
                 use_ocr: bool = True,
                 ocr_confidence_threshold: float = 60.0):
        """
        Args:
            use_ocr: Si True, usa OCR para extraer texto del diagrama
            ocr_confidence_threshold: Umbral de confianza OCR (0-100)
        """
        self.use_ocr = use_ocr and _HAS_OCR
        self.ocr_threshold = ocr_confidence_threshold
        
        # Patrones para detectar componentes eléctricos
        self.component_patterns = {
            "motor": r"\bmotor\b|\bM\d+\b",
            "contactor": r"\bcontactor\b|\bK\d+\b",
            "relay": r"\brelay\b|\bKA?\d+\b",
            "fuse": r"\bfuse\b|\bF\d+\b",
            "breaker": r"\bbreaker\b|\bQF?\d+\b",
            "transformer": r"\btransformer\b|\bT\d+\b",
            "terminal": r"\bterminal\b|\bX\d+\b",
            "sensor": r"\bsensor\b|\bB\d+\b",
            "switch": r"\bswitch\b|\bS\d+\b"
        }
        
        # Patrones para valores eléctricos
        self.value_patterns = {
            "voltage": r"\d+\.?\d*\s*[Vv](AC|DC)?",
            "current": r"\d+\.?\d*\s*[Aa]",
            "power": r"\d+\.?\d*\s*(kW|W|HP|hp)",
            "frequency": r"\d+\.?\d*\s*[Hh]z",
            "resistance": r"\d+\.?\d*\s*[Ωω]"
        }
    
    def is_diagram(self, chunk_data: Dict[str, Any]) -> bool:
        """
        Detecta si un chunk es un diagrama eléctrico.
        Soporta ambos formatos: notes directo o dentro de original_chunk.
        
        Args:
            chunk_data: Chunk con metadata
            
        Returns:
            True si es diagrama eléctrico
        """
        # Verificar si es imagen
        if chunk_data.get("content_type") != "image":
            return False
        
        # Intentar obtener notes (múltiples fuentes)
        notes = chunk_data.get("notes")
        
        # Si notes no existe directamente, buscar en original_chunk
        if not notes:
            try:
                original = chunk_data.get("original_chunk", {})
                if isinstance(original, str):
                    original = json.loads(original)
                notes = original.get("notes", {})
            except Exception:
                return False
        
        # Convertir a texto para búsqueda de keywords
        if isinstance(notes, dict):
            notes_text = json.dumps(notes, ensure_ascii=False).lower()
        else:
            notes_text = str(notes).lower()
        
        # Keywords ampliados para planos escaneados
        diagram_keywords = [
            "diagram", "schematic", "wiring", "circuit", "connection",
            "conexión", "esquema", "diagrama", "circuito", "conexionado",
            "electrical", "eléctrico", "electrico", "distribution",
            "plan", "plano", "layout"
        ]
        
        return any(keyword in notes_text for keyword in diagram_keywords)
    
    def preprocess_image_for_ocr(self, image_path: str) -> str:
        """
        Preprocesa imagen escaneada para mejorar calidad de OCR.
        Aplica: grayscale, deskew, binarización, denoise, resize.
        
        Args:
            image_path: Ruta a imagen original
            
        Returns:
            Ruta a imagen preprocesada
        """
        try:
            import cv2
            import numpy as np
            
            # Cargar imagen
            img = cv2.imread(image_path)
            if img is None:
                return image_path
            
            # 1. Convertir a escala de grises
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            
            # 2. Deskew (corregir rotación leve)
            coords = np.column_stack(np.where(gray > 0))
            if len(coords) > 0:
                angle = cv2.minAreaRect(coords)[-1]
                if angle < -45:
                    angle = 90 + angle
                (h, w) = gray.shape[:2]
                center = (w // 2, h // 2)
                M = cv2.getRotationMatrix2D(center, angle, 1.0)
                gray = cv2.warpAffine(gray, M, (w, h), 
                                     flags=cv2.INTER_CUBIC, 
                                     borderMode=cv2.BORDER_REPLICATE)
            
            # 3. Binarización adaptativa (mejor contraste)
            binary = cv2.adaptiveThreshold(gray, 255, 
                                           cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                           cv2.THRESH_BINARY, 11, 2)
            
            # 4. Denoise (eliminar ruido)
            denoised = cv2.fastNlMeansDenoising(binary)
            
            # 5. Escalar a ~300 DPI para Tesseract (optimal)
            # Asumir 72 DPI original, escalar ~4x
            scale_factor = 4.0
            new_w = int(gray.shape[1] * scale_factor)
            new_h = int(gray.shape[0] * scale_factor)
            resized = cv2.resize(denoised, (new_w, new_h), 
                                interpolation=cv2.INTER_CUBIC)
            
            # Guardar imagen procesada
            processed_path = image_path.replace(".png", "_preprocessed.png")
            cv2.imwrite(processed_path, resized)
            
            return processed_path
        except Exception as e:
            logger.warning(f"Image preprocessing failed: {e}, using original")
            return image_path
    
    def extract_ocr_text(self, image_path: str) -> Dict[str, Any]:
        """
        Extrae texto del diagrama usando OCR.
        
        Returns:
            {
                "raw_text": str,
                "components": List[str],
                "values": Dict[str, List[str]],
                "confidence": float
            }
        """
        if not self.use_ocr or not os.path.exists(image_path):
            return {
                "raw_text": "",
                "components": [],
                "values": {},
                "confidence": 0.0
            }
        
        try:
            # ⭐ Preprocesar imagen para mejor OCR
            processed_path = self.preprocess_image_for_ocr(image_path)
            
            # OCR con Tesseract (multi-idioma: español + inglés)
            img = Image.open(processed_path)
            
            # Tesseract con configuración optimizada para diagramas
            custom_config = r'--oem 3 --psm 11'
            ocr_data = pytesseract.image_to_data(
                img, 
                lang='spa+eng',  # ⭐ Multi-idioma
                output_type=pytesseract.Output.DICT,
                config=custom_config
            )
            
            # Filtrar texto por confianza
            filtered_text = []
            confidences = []
            
            for i, conf in enumerate(ocr_data['conf']):
                if conf > self.ocr_threshold:
                    text = ocr_data['text'][i].strip()
                    if text:
                        filtered_text.append(text)
                        confidences.append(float(conf))
            
            raw_text = " ".join(filtered_text)
            avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
            
            # Detectar componentes
            components = self._detect_components(raw_text)
            
            # Extraer valores eléctricos
            values = self._extract_electrical_values(raw_text)
            
            # Limpiar archivo temporal preprocesado
            if processed_path != image_path and os.path.exists(processed_path):
                try:
                    os.remove(processed_path)
                except Exception:
                    pass
            
            return {
                "raw_text": raw_text,
                "components": components,
                "values": values,
                "confidence": avg_confidence
            }
        
        except Exception as e:
            logger.warning(f"Error en OCR de diagrama: {e}")
            return {
                "raw_text": "",
                "components": [],
                "values": {},
                "confidence": 0.0
            }
    
    def _detect_components(self, text: str) -> List[str]:
        """Detecta componentes eléctricos en el texto."""
        components = []
        text_lower = text.lower()
        
        for comp_type, pattern in self.component_patterns.items():
            matches = re.findall(pattern, text_lower, re.IGNORECASE)
            if matches:
                components.extend([f"{comp_type}:{m}" for m in matches])
        
        return list(set(components))  # Eliminar duplicados
    
    def _extract_electrical_values(self, text: str) -> Dict[str, List[str]]:
        """Extrae valores eléctricos del texto."""
        values = {}
        
        for value_type, pattern in self.value_patterns.items():
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                values[value_type] = list(set(matches))
        
        return values
    
    def create_enhanced_diagram_chunks(
        self,
        diagram_chunk: Dict[str, Any],
        llm_description: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Crea múltiples chunks para un diagrama (multi-faceta).
        
        Args:
            diagram_chunk: Chunk original del diagrama
            llm_description: Descripción estructurada del LLM (opcional)
            
        Returns:
            Lista de chunks:
            1. Visual chunk (para indexado CLIP)
            2. OCR chunk (componentes + valores)
            3. Structured chunk (descripción del LLM)
        """
        chunks = []
        
        # Extraer info básica
        image_path = None
        try:
            original = json.loads(diagram_chunk.get("original_chunk", "{}"))
            image_path = original.get("image_path")
        except:
            pass
        
        base_metadata = {
            "file_name": diagram_chunk.get("file_name"),
            "page_num": diagram_chunk.get("page_num"),
            "page_metadata": diagram_chunk.get("page_metadata"),
            "is_diagram": True
        }
        
        # 1. CHUNK VISUAL (para CLIP)
        visual_chunk = {
            **diagram_chunk,
            "chunk_id": f"{diagram_chunk.get('chunk_id')}_visual",
            "content_type": "diagram_visual",
            "index_type": "visual",  # Marca para indexado CLIP
            **base_metadata
        }
        chunks.append(visual_chunk)
        
        # 2. CHUNK OCR (componentes + valores)
        if image_path and self.use_ocr:
            ocr_data = self.extract_ocr_text(image_path)
            
            if ocr_data["raw_text"]:
                # Construir texto searchable
                searchable_parts = [
                    f"Diagrama eléctrico - Página {base_metadata['page_num']}",
                    f"Componentes detectados: {', '.join(ocr_data['components'])}" if ocr_data['components'] else "",
                ]
                
                # Añadir valores eléctricos
                for value_type, values in ocr_data['values'].items():
                    if values:
                        searchable_parts.append(f"{value_type.title()}: {', '.join(values)}")
                
                searchable_parts.append(f"Texto extraído: {ocr_data['raw_text']}")
                
                searchable_text = "\n".join([p for p in searchable_parts if p])
                
                ocr_chunk = {
                    "chunk_id": f"{diagram_chunk.get('chunk_id')}_ocr",
                    "original_chunk": searchable_text,
                    "content_type": "diagram_text",
                    "index_type": "text",  # Para indexado textual
                    "ocr_data": ocr_data,
                    "image_path": image_path,
                    **base_metadata
                }
                chunks.append(ocr_chunk)
        
        # 3. CHUNK ESTRUCTURADO (descripción GPT-4o)
        if llm_description:
            structured_chunk = {
                "chunk_id": f"{diagram_chunk.get('chunk_id')}_structured",
                "original_chunk": llm_description,
                "content_type": "diagram_description",
                "index_type": "text",
                "image_path": image_path,
                **base_metadata
            }
            chunks.append(structured_chunk)
        
        logger.info(f"Diagrama procesado → {len(chunks)} chunks multi-faceta")
        return chunks
    
    def enhance_diagram_prompt(self, base_prompt: str) -> str:
        """
        Mejora el prompt del LLM para diagramas técnicos.
        
        Solicita descripción estructurada con:
        - Tipo de diagrama/plano
        - Componentes principales
        - Conexiones
        - Valores nominales
        - Planos de distribución
        """
        diagram_instructions = """

INSTRUCCIONES ESPECIALES PARA DIAGRAMAS Y PLANOS TÉCNICOS:

Si detectas un diagrama/esquema/plano técnico (eléctrico, mecánico, distribución, etc.), incluye en 'notes' un objeto JSON estructurado:

Para DIAGRAMAS ELÉCTRICOS/ESQUEMAS:
{
  "diagram_type": "wiring_diagram" | "schematic" | "block_diagram" | "connection_diagram" | "circuit_diagram",
  "components": ["motor M1", "contactor K1", "relay F1", "terminal X1", ...],
  "connections": ["L1 → K1 → motor M1", "PE → terminal X1", ...],
  "ratings": {
    "voltage": "480V AC",
    "current": "12A",
    "power": "5.5kW",
    "frequency": "60Hz"
  },
  "wire_colors": ["L1:black", "N:blue", "PE:green-yellow"],
  "terminal_numbers": ["X1:1-6", "X2:7-12"],
  "description": "Diagrama de conexionado del motor principal con protecciones"
}

Para PLANOS DE DISTRIBUCIÓN/LAYOUT:
{
  "plan_type": "electrical_distribution" | "panel_layout" | "installation_plan",
  "zones": ["Zona 1: Producción", "Zona 2: Oficinas", ...],
  "equipment_locations": {
    "Panel Principal": "coordX:123, coordY:456",
    "Motor M1": "Sector A",
    "Breaker QF1": "Panel 1"
  },
  "cable_routes": ["Bandeja principal desde X hasta Y", ...],
  "dimensions": {
    "total_area": "10m x 8m",
    "panel_size": "600x800mm"
  },
  "legends": ["symbols and their meanings"],
  "description": "Plano de distribución eléctrica general - Planta producción"
}

Para PLANOS MECÁNICOS/CONSTRUCTIVOS:
{
  "drawing_type": "assembly" | "detail" | "section",
  "parts": ["pieza 1", "tornillo M8", ...],
  "dimensions": ["largo: 500mm", "alto: 300mm"],
  "materials": ["acero inox", "aluminio"],
  "tolerances": ["±0.1mm en X"],
  "description": "Vista de montaje del conjunto X"
}

IMPORTANTE: Extrae TODOS los textos visibles (números, etiquetas, valores) y TODOS los componentes/elementos identificables.
"""
        
        return base_prompt + diagram_instructions
