"""
Procesador Avanzado para Tablas Grandes
=======================================

Estrategia de split inteligente para tablas:
- Divide tablas grandes en chunks manejables
- Repite encabezados en cada chunk
- Preserva contexto de la tabla completa
- Mantiene referencia a imagen de tabla completa
"""

import json
from typing import List, Dict, Any, Tuple, Optional
from logger import Logger

logger = Logger.get_logger(__name__)


class TableProcessor:
    """
    Procesa tablas grandes dividiéndolas en chunks manejables
    mientras preserva contexto y estructura.
    """
    
    def __init__(self,
                 max_rows_per_chunk: int = 10,
                 min_rows_per_chunk: int = 3,
                 max_tokens_per_chunk: int = 500):
        """
        Args:
            max_rows_per_chunk: Máximo de filas por chunk
            min_rows_per_chunk: Mínimo de filas (evitar chunks muy pequeños)
            max_tokens_per_chunk: Límite aproximado de tokens
        """
        self.max_rows_per_chunk = max_rows_per_chunk
        self.min_rows_per_chunk = min_rows_per_chunk
        self.max_tokens_per_chunk = max_tokens_per_chunk
    
    def needs_splitting(self, table_data: Dict[str, Any]) -> bool:
        """
        Determina si una tabla necesita ser dividida.
        
        Args:
            table_data: Chunk de tabla con table_json
            
        Returns:
            True si la tabla debe dividirse
        """
        try:
            # Intentar cargar tabla JSON
            if isinstance(table_data, str):
                table_json = json.loads(table_data)
            else:
                table_json = table_data
            
            # Obtener filas
            rows = table_json.get("table_json", {}).get("rows", [])
            
            if not rows:
                return False
            
            # Criterios para split:
            # 1. Más filas que el máximo
            if len(rows) > self.max_rows_per_chunk:
                return True
            
            # 2. Estimación de tokens (aprox 4 chars = 1 token)
            table_markdown = table_json.get("table_markdown", "")
            estimated_tokens = len(table_markdown) / 4
            
            if estimated_tokens > self.max_tokens_per_chunk:
                return True
            
            return False
        
        except Exception as e:
            logger.warning(f"Error evaluando split de tabla: {e}")
            return False
    
    def split_table(
        self,
        table_chunk: Dict[str, Any],
        context: str = ""
    ) -> List[Dict[str, Any]]:
        """
        Divide una tabla grande en múltiples chunks.
        
        Args:
            table_chunk: Chunk original de la tabla
            context: Contexto adicional (ej: "Tabla de especificaciones eléctricas")
            
        Returns:
            Lista de chunks de tabla, cada uno con subset de filas + headers
        """
        try:
            # Parsear datos de tabla
            original_chunk = table_chunk.get("original_chunk", "{}")
            if isinstance(original_chunk, str):
                table_data = json.loads(original_chunk)
            else:
                table_data = original_chunk
            
            table_json = table_data.get("table_json", {})
            rows = table_json.get("rows", [])
            
            if not rows or len(rows) <= self.max_rows_per_chunk:
                # No necesita split, devolver como está
                return [table_chunk]
            
            # Extraer header (primera fila generalmente)
            has_header = self._detect_header(rows)
            header_row = rows[0] if has_header else None
            data_rows = rows[1:] if has_header else rows
            
            # Dividir en chunks
            chunks = []
            for i in range(0, len(data_rows), self.max_rows_per_chunk):
                chunk_rows = data_rows[i:i + self.max_rows_per_chunk]
                
                # Construir subset de tabla
                if header_row:
                    subset_rows = [header_row] + chunk_rows
                else:
                    subset_rows = chunk_rows
                
                # Crear JSON de subset
                subset_json = {
                    "rows": subset_rows
                }
                
                # Crear Markdown de subset
                subset_markdown = self._rows_to_markdown(subset_rows, has_header)
                
                # Construir metadata del chunk
                chunk_num = (i // self.max_rows_per_chunk) + 1
                total_chunks = (len(data_rows) + self.max_rows_per_chunk - 1) // self.max_rows_per_chunk
                row_range = f"filas {i+1}-{min(i+self.max_rows_per_chunk, len(data_rows))}"
                
                # Construir texto searchable
                searchable_text = self._build_searchable_text(
                    subset_markdown,
                    context,
                    chunk_num,
                    total_chunks,
                    row_range,
                    table_chunk
                )
                
                # Crear nuevo chunk
                new_chunk = {
                    **table_chunk,
                    "chunk_id": f"{table_chunk.get('chunk_id')}_table_part{chunk_num}",
                    "original_chunk": json.dumps({
                        "table_markdown": subset_markdown,
                        "table_json": subset_json,
                        "bbox": table_data.get("bbox"),
                        "image_path": table_data.get("image_path"),
                        "is_partial": True,
                        "part": chunk_num,
                        "total_parts": total_chunks,
                        "row_range": row_range
                    }, ensure_ascii=False),
                    "searchable_text": searchable_text,
                    "table_part": chunk_num,
                    "table_total_parts": total_chunks,
                    "content_type": "table_partial"
                }
                
                chunks.append(new_chunk)
            
            logger.info(f"Tabla dividida en {len(chunks)} chunks (filas: {len(data_rows)})")
            return chunks
        
        except Exception as e:
            logger.error(f"Error dividiendo tabla: {e}")
            # En caso de error, devolver chunk original
            return [table_chunk]
    
    def _detect_header(self, rows: List[List[str]]) -> bool:
        """
        Detecta si la primera fila es un encabezado.
        
        Heurísticas:
        - Primera fila tiene palabras (no solo números)
        - Primera fila más corta que filas subsecuentes
        - Primera fila con texto descriptivo
        """
        if not rows or len(rows) < 2:
            return False
        
        first_row = rows[0]
        second_row = rows[1]
        
        # Verificar si primera fila tiene texto descriptivo
        first_row_text = " ".join([str(cell) for cell in first_row])
        has_words = any(c.isalpha() for c in first_row_text)
        
        # Verificar si segunda fila es más numérica
        second_row_text = " ".join([str(cell) for cell in second_row])
        second_is_numeric = sum(c.isdigit() for c in second_row_text) > sum(c.isalpha() for c in second_row_text)
        
        return has_words and (second_is_numeric or len(first_row) <= len(second_row))
    
    def _rows_to_markdown(self, rows: List[List[str]], has_header: bool = True) -> str:
        """Convierte filas a formato Markdown."""
        if not rows:
            return ""
        
        lines = []
        
        # Header
        if has_header and len(rows) > 0:
            header = rows[0]
            lines.append("| " + " | ".join(str(cell) for cell in header) + " |")
            lines.append("|" + "|".join(["---" for _ in header]) + "|")
            start_idx = 1
        else:
            start_idx = 0
        
        # Data rows
        for row in rows[start_idx:]:
            lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
        
        return "\n".join(lines)
    
    def _build_searchable_text(
        self,
        markdown: str,
        context: str,
        chunk_num: int,
        total_chunks: int,
        row_range: str,
        original_chunk: Dict[str, Any]
    ) -> str:
        """
        Construye texto optimizado para búsqueda.
        
        Incluye:
        - Contexto de la tabla
        - Indicación de parte (X de Y)
        - Markdown de la tabla
        - Metadata de página/documento
        """
        parts = []
        
        # Contexto
        if context:
            parts.append(context)
        else:
            parts.append(f"Tabla técnica")
        
        # Parte de tabla
        if total_chunks > 1:
            parts.append(f"(Parte {chunk_num} de {total_chunks} - {row_range})")
        
        # Metadata de documento
        page_num = original_chunk.get("page_num", "")
        file_name = original_chunk.get("file_name", "")
        if page_num:
            parts.append(f"Página {page_num}")
        if file_name:
            parts.append(f"Documento: {file_name}")
        
        # Tabla en Markdown
        parts.append("\n" + markdown)
        
        return "\n".join(parts)
    
    def extract_table_context(self, 
                              table_chunk: Dict[str, Any],
                              surrounding_chunks: List[Dict[str, Any]] = None) -> str:
        """
        Extrae contexto de la tabla desde chunks circundantes.
        
        Args:
            table_chunk: Chunk de la tabla
            surrounding_chunks: Chunks antes/después de la tabla
            
        Returns:
            Contexto descriptivo (ej: "Tabla de especificaciones eléctricas")
        """
        context_parts = []
        
        # Buscar en chunks anteriores (títulos, headers)
        if surrounding_chunks:
            for chunk in surrounding_chunks[-3:]:  # Últimos 3 chunks anteriores
                text = str(chunk.get("original_chunk", "")).strip()
                if text and len(text) < 200:  # Texto corto, probablemente título
                    # Verificar si parece un título
                    if any(word in text.lower() for word in ["tabla", "table", "especificaciones", "specifications"]):
                        context_parts.append(text)
                        break
        
        # Usar notes del chunk si existen
        notes = table_chunk.get("notes", "")
        if notes:
            context_parts.append(notes)
        
        # Default si no hay contexto
        if not context_parts:
            return "Tabla de datos técnicos"
        
        return " - ".join(context_parts)
