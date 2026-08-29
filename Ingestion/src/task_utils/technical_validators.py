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
    """
    Clase base para validadores técnicos.

    IMPORTANTE sobre la semántica del retorno: `is_fatal=True` significa que el
    chunk es INUTILIZABLE y debe descartarse. Las advertencias de calidad
    (unidades faltantes, diagrama sin descripción, OCR sospechoso, etc.) NO son
    fatales: se adjuntan como metadata para diagnóstico, pero el chunk se
    conserva. Descartar por advertencia hacía perder contenido legítimo
    (diagramas sin descripción, tablas de especificaciones puramente numéricas).
    """

    def validate(self, chunk: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """
        Valida un chunk.

        Returns:
            (is_fatal, warnings)
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
        """Valida tabla de especificaciones. Devuelve (is_fatal, warnings)."""
        warnings = []

        if chunk.get("content_type") != "table":
            return False, []

        try:
            # Obtener datos de tabla
            original = chunk.get("original_chunk", "{}")
            if isinstance(original, str):
                table_data = json.loads(original)
            else:
                table_data = original

            table_json = table_data.get("table_json") or {}
            rows = table_json.get("rows", [])

            if not rows:
                # Sin filas estructuradas todavía puede haber markdown útil (o el
                # recorte de imagen de la tabla). Solo es fatal si no hay NADA.
                has_markdown = bool(str(table_data.get("table_markdown") or "").strip())
                has_image = bool(table_data.get("image_path"))
                if has_markdown or has_image:
                    warnings.append("Tabla sin filas estructuradas (se usa markdown/imagen)")
                    return False, warnings
                warnings.append("Tabla vacía")
                return True, warnings

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

            # Advertencias de calidad: no descartan la tabla
            return False, warnings

        except Exception as e:
            logger.warning(f"Error validando tabla: {e}")
            return False, [f"Error en validación: {str(e)}"]
    
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
        """Valida procedimiento. Devuelve (is_fatal, warnings) — nunca es fatal."""
        warnings = []

        if chunk.get("content_type") != "text":
            return False, []

        text = str(chunk.get("original_chunk", "")).lower()
        
        # Detectar si parece ser un procedimiento
        procedure_keywords = [
            "step", "paso", "procedure", "procedimiento",
            "instrucción", "instruction"
        ]
        
        is_procedure = any(keyword in text for keyword in procedure_keywords)

        if not is_procedure:
            return False, []

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

        return False, warnings


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
        """Valida texto para detectar OCR corrupto. Devuelve (is_fatal, warnings) — nunca es fatal."""
        warnings = []

        # Solo aplica a contenido textual: el original_chunk de tablas/imágenes es
        # un JSON (lleno de {, ", [, :) que disparaba el heurístico de "alto ratio
        # de caracteres especiales" y hacía descartar tablas/diagramas legítimos.
        ctype = (chunk.get("content_type") or "").lower()
        if ctype not in ("", "text", "superchunk", "diagram_text"):
            return False, []

        text = str(chunk.get("original_chunk", ""))

        if len(text) < 20:
            return False, []

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

        return False, warnings


class DiagramLabelValidator(TechnicalValidator):
    """
    Valida que diagramas tengan labels/texto legible.

    Para imágenes marcadas como diagramas, verifica:
    - Tiene descripción o notas
    - OCR encontró texto legible
    - La descripción no declara que la imagen no tiene contenido
    """

    # El modelo de visión avisa cuando el recorte salió vacío ("La imagen está en
    # blanco y no contiene información visible"). Ese chunk no solo es inútil: el
    # enriquecedor le genera preguntas ENCIMA, y salen plausibles — medido en la
    # ingesta anterior, 4 imágenes en blanco de la página 9 arrastraron 16 preguntas,
    # entre ellas "¿Cómo puedo identificar los componentes y conexiones del variador
    # PowerFlex 4M?". Esa pregunta matchea una consulta real y devuelve una imagen
    # vacía, con lo cual es peor que no tener nada.
    _EMPTY_IMAGE_PATTERNS = re.compile(
        r"imagen (está|esta) en blanco"
        r"|no contiene informaci[óo]n visible"
        r"|imagen (en blanco|vac[íi]a)"
        r"|sin contenido visible"
        r"|no se (pueden|puede) identificar (componentes|elementos)"
        r"|no hay (informaci[óo]n|contenido) (visible|alguna|alguno)",
        re.IGNORECASE,
    )

    def validate(self, chunk: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """
        Valida diagrama. Devuelve (is_fatal, warnings).

        Es fatal solo si la descripción declara explícitamente que la imagen no tiene
        contenido: en ese caso tampoco sirve para CLIP (no hay nada que ver). Una
        imagen sin descripción, en cambio, NO se descarta: sigue siendo indexable en
        el índice visual, y descartarla perdía su única representación.
        """
        warnings = []

        if (chunk.get("content_type") or "").lower() not in ("image", "diagram_visual"):
            return False, []

        from task_utils.chunk_text import readable_chunk_text
        description = readable_chunk_text(chunk)
        if description and self._EMPTY_IMAGE_PATTERNS.search(description):
            return True, [
                f"Imagen sin contenido según la descripción del modelo: "
                f"{' '.join(description.split())[:70]}"
            ]

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

        return False, warnings


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
            (is_usable, all_warnings) — is_usable=False SOLO si algún validador
            marcó el chunk como fatal (contenido inutilizable). Las advertencias
            de calidad no descartan el chunk.
        """
        all_warnings = []
        is_fatal = False

        for validator in self.validators:
            fatal, warnings = validator.validate(chunk)

            if fatal:
                is_fatal = True

            if warnings:
                all_warnings.extend(warnings)

        return (not is_fatal), all_warnings
    
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
                    f"Chunk descartado por contenido inutilizable "
                    f"(página {chunk.get('page_num')}, id {chunk.get('chunk_id')}): {warnings}"
                )

        chunks_with_warnings = sum(1 for c in chunks if c.get("validation_warnings"))
        report = {
            "total": len(chunks),
            "valid": len(valid_chunks),
            "invalid": len(invalid_chunks),
            "with_warnings": chunks_with_warnings,
            "warnings_by_type": warnings_by_type
        }

        logger.info(
            f"Validación técnica: {report['valid']}/{report['total']} chunks conservados "
            f"({report['invalid']} descartados por contenido inutilizable, "
            f"{chunks_with_warnings} con advertencias no bloqueantes)"
        )

        return valid_chunks, report
