"""
Validadores Técnicos de Dominio
================================

Validadores especializados para documentación técnica:
- Validación de tablas de especificaciones
- Validación de procedimientos
- Validación de valores con unidades
- Detección de OCR corrupto en datos críticos
"""

import re
from typing import List, Dict, Any, Tuple, Optional
import json

from logger import Logger

logger = Logger.get_logger(__name__)


class TechnicalValidator:
    """Clase base para validadores técnicos."""
    
    def validate(self, chunk: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """
        Valida un chunk.
        
        Returns:
            (is_valid, warnings)
        """
        raise NotImplementedError


class SpecificationTableValidator(TechnicalValidator):
    """
    Valida tablas de especificaciones técnicas.
    
    Verifica:
    - Presencia de unidades en valores numéricos
    - Formato consistente de datos
    - No hay valores faltantes en celdas críticas
    """
    
    def __init__(self):
        # Patrones de valores con unidades
        self.unit_patterns = {
            "voltage": r"\d+\.?\d*\s*[Vv]",
            "current": r"\d+\.?\d*\s*[Aa]",
            "power": r"\d+\.?\d*\s*(kW|W|HP|hp)",
            "frequency": r"\d+\.?\d*\s*[Hh]z",
            "temperature": r"\d+\.?\d*\s*[°◦]?[CcFf]",
            "speed": r"\d+\.?\d*\s*(rpm|RPM|m/s)",
            "pressure": r"\d+\.?\d*\s*(bar|psi|PSI|Pa)",
            "distance": r"\d+\.?\d*\s*(mm|cm|m|in|ft)"
        }
    
    def validate(self, chunk: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """Valida tabla de especificaciones."""
        warnings = []
        
        if chunk.get("content_type") != "table":
            return True, []
        
        try:
            # Obtener datos de tabla
            original = chunk.get("original_chunk", "{}")
            if isinstance(original, str):
                table_data = json.loads(original)
            else:
                table_data = original
            
            table_json = table_data.get("table_json", {})
            rows = table_json. get("rows", [])
            
            if not rows:
                warnings.append("Tabla vacía")
                return False, warnings
            
            # Verificar valores numéricos sin unidades
            numeric_without_units = self._find_numeric_without_units(rows)
            if numeric_without_units > len(rows) * 0.5:
                warnings.append(
                    f"Tabla contiene muchos valores numéricos sin unidades "
                    f"({numeric_without_units} celdas)"
                )
            
            # Verificar celdas vacías
            empty_cells = self._count_empty_cells(rows)
            total_cells = sum(len(row) for row in rows)
            
            if empty_cells > total_cells * 0.3:
                warnings.append(
                    f"Tabla tiene muchas celdas vacías "
                    f"({empty_cells}/{total_cells})"
                )
            
            # Si hay advertencias críticas, marcar como inválido
            is_valid = len(warnings) == 0
            
            return is_valid, warnings
        
        except Exception as e:
            logger.warning(f"Error validando tabla: {e}")
            return True, [f"Error en validación: {str(e)}"]
    
    def _find_numeric_without_units(self, rows: List[List[str]]) -> int:
        """Cuenta celdas con números pero sin unidades."""
        count = 0
        
        for row in rows:
            for cell in row:
                cell_str = str(cell).strip()
                
                # Verificar si contiene número
                has_number = bool(re.search(r'\d+\.?\d*', cell_str))
                
                if has_number:
                    # Verificar si tiene unidad
                    has_unit = any(
                        re.search(pattern, cell_str)
                        for pattern in self.unit_patterns.values()
                    )
                    
                    if not has_unit:
                        # Verificar si es solo número (sin texto descriptivo)
                        if re.match(r'^\d+\.?\d*$', cell_str):
                            count += 1
        
        return count
    
    def _count_empty_cells(self, rows: List[List[str]]) -> int:
        """Cuenta celdas vacías."""
        count = 0
        
        for row in rows:
            for cell in row:
                if not str(cell).strip() or str(cell).strip() in ["-", "N/A", "n/a"]:
                    count += 1
        
        return count


class ProcedureValidator(TechnicalValidator):
    """
    Valida procedimientos/instrucciones.
    
    Verifica:
    - Numeración secuencial
    - Presencia de pasos claros
    - No hay saltos en numeración
    """
    
    def validate(self, chunk: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """Valida procedimiento."""
        warnings = []
        
        if chunk.get("content_type") != "text":
            return True, []
        
        text = str(chunk.get("original_chunk", "")).lower()
        
        # Detectar si parece ser un procedimiento
        procedure_keywords = [
            "step", "paso", "procedure", "procedimiento",
            "instrucción", "instruction"
        ]
        
        is_procedure = any(keyword in text for keyword in procedure_keywords)
        
        if not is_procedure:
            return True, []
        
        # Verificar numeración secuencial
        numbers = re.findall(r'(?:step|paso)\s+(\d+)', text, re.IGNORECASE)
        
        if numbers:
            numbers = [int(n) for n in numbers]
            
            # Verificar secuencia
            expected = list(range(1, max(numbers) + 1))
            missing = set(expected) - set(numbers)
            
            if missing:
                warnings.append(
                    f"Procedimiento con numeración no secuencial. "
                    f"Faltan pasos: {sorted(missing)}"
                )
        
        # Verificar longitud mínima (procedimiento muy corto es sospechoso)
        if len(text) < 100:
            warnings.append("Procedimiento muy corto (posible fragmentación)")
        
        is_valid = len(warnings) == 0
        
        return is_valid, warnings


class OCRCorruptionDetector(TechnicalValidator):
    """
    Detecta corrupción de OCR en datos críticos.
    
    Busca:
    - Caracteres especiales mezclados con texto
    - Palabras sin vocales
    - Secuencias de caracteres aleatorios
    """
    
    def __init__(self):
        # Patrones de corrupción común en OCR
        self.corruption_patterns = [
            r'[bcdfghjklmnpqrstvwxyzBCDFGHJKLMNPQRSTVWXYZ]{15,}',  # Palabras MUY largas sin vocales (15+ consonantes)
            r'[^\w\s]{5,}',   # 5+ caracteres especiales seguidos (más específico)
            r'(?<![A-Za-z])\d[A-Za-z]\d[A-Za-z]\d(?![A-Za-z0-9])',  # Alternancia número-letra aislada (no códigos de producto)
        ]
    
    def validate(self, chunk: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """Valida texto para detectar OCR corrupto."""
        warnings = []
        
        text = str(chunk.get("original_chunk", ""))
        
        if len(text) < 20:
            return True, []
        
        # Buscar patrones de corrupción
        for pattern in self.corruption_patterns:
            matches = re.findall(pattern, text)
            # Umbral más alto para reducir falsos positivos en docs técnicas
            if len(matches) > 10:
                warnings.append(
                    f"Posible OCR corrupto: patrón '{pattern}' encontrado "
                    f"{len(matches)} veces"
                )
        
        # Verificar ratio de caracteres especiales
        special_chars = len(re.findall(r'[^\w\s]', text))
        total_chars = len(text)
        
        # Permitir más caracteres especiales en docs técnicas (fórmulas, diagramas)
        if total_chars > 0 and (special_chars / total_chars) > 0.4:
            warnings.append(
                f"Alto ratio de caracteres especiales ({special_chars}/{total_chars})"
            )
        
        # Verificar palabras sin vocales (común en OCR malo)
        words = re.findall(r'\b[A-Za-z]{4,}\b', text)
        words_without_vowels = [
            w for w in words
            if not any(v in w.lower() for v in 'aeiou')
        ]
        
        # Permitir más siglas técnicas (PLC, SCADA, etc.)
        if len(words) > 0 and len(words_without_vowels) / len(words) > 0.4:
            warnings.append(
                f"Muchas palabras sin vocales ({len(words_without_vowels)}/{len(words)}). "
                f"Ejemplos: {words_without_vowels[:3]}"
            )
        
        is_valid = len(warnings) == 0
        
        return is_valid, warnings


class DiagramLabelValidator(TechnicalValidator):
    """
    Valida que diagramas tengan labels/texto legible.
    
    Para imágenes marcadas como diagramas, verifica:
    - Tiene descripción o notas
    - OCR encontró texto legible
    """
    
    def validate(self, chunk: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """Valida diagrama."""
        warnings = []
        
        if chunk.get("content_type") != "image":
            return True, []
        
        # Verificar si tiene notas/descripción
        try:
            original = chunk.get("original_chunk", "{}")
            if isinstance(original, str):
                image_data = json.loads(original)
            else:
                image_data = original
            
            notes = image_data.get("notes", "")
            
            # notes puede ser string o dict
            has_adequate_description = False
            if isinstance(notes, str):
                has_adequate_description = len(notes) >= 10
            elif isinstance(notes, dict):
                # Verificar si tiene description, diagram_type, components, etc.
                description = notes.get("description", "")
                has_adequate_description = (
                    len(description) >= 10 or 
                    "diagram_type" in notes or 
                    ("components" in notes and len(notes.get("components", [])) > 0) or
                    ("connections" in notes and len(notes.get("connections", [])) > 0)
                )
            
            if not has_adequate_description:
                warnings.append(
                    "Diagrama sin descripción adecuada "
                    "(dificulta retrieval textual)"
                )
            
            # Si tiene datos OCR, verificar calidad
            if "ocr_data" in chunk:
                ocr_data = chunk["ocr_data"]
                confidence = ocr_data.get("confidence", 0)
                
                if confidence < 50:
                    warnings.append(
                        f"OCR de baja confianza en diagrama ({confidence:.1f}%)"
                    )
        
        except Exception as e:
            logger.debug(f"Error validando diagrama: {e}")
        
        is_valid = len(warnings) == 0
        
        return is_valid, warnings


class TechnicalDocumentValidator:
    """
    Validador compuesto para documentación técnica.
    
    Aplica todos los validadores específicos según tipo de chunk.
    """
    
    def __init__(self):
        self.validators = [
            SpecificationTableValidator(),
            ProcedureValidator(),
            OCRCorruptionDetector(),
            DiagramLabelValidator()
        ]
    
    def validate_chunk(self, chunk: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """
        Valida chunk con todos los validadores aplicables.
        
        Returns:
            (is_valid, all_warnings)
        """
        all_warnings = []
        is_valid = True
        
        for validator in self.validators:
            valid, warnings = validator.validate(chunk)
            
            if not valid:
                is_valid = False
            
            if warnings:
                all_warnings.extend(warnings)
        
        return is_valid, all_warnings
    
    def validate_chunks(self, 
                       chunks: List[Dict[str, Any]]) -> Tuple[List[Dict], Dict[str, Any]]:
        """
        Valida lista de chunks.
        
        Returns:
            (valid_chunks, validation_report)
        """
        valid_chunks = []
        invalid_chunks = []
        warnings_by_type = {}
        
        for chunk in chunks:
            is_valid, warnings = self.validate_chunk(chunk)
            
            # Añadir warnings al chunk
            if warnings:
                chunk["validation_warnings"] = warnings
                
                # Registrar por tipo
                for warning in warnings:
                    warning_type = warning.split(":")[0]
                    warnings_by_type[warning_type] = warnings_by_type.get(warning_type, 0) + 1
            
            if is_valid:
                valid_chunks.append(chunk)
            else:
                invalid_chunks.append(chunk)
                logger.warning(
                    f"Chunk inválido (página {chunk.get('page_num')}): {warnings}"
                )
        
        report = {
            "total": len(chunks),
            "valid": len(valid_chunks),
            "invalid": len(invalid_chunks),
            "warnings_by_type": warnings_by_type
        }
        
        logger.info(
            f"Validación técnica: {report['valid']}/{report['total']} chunks válidos"
        )
        
        return valid_chunks, report
