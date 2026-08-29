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
import re
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
    
    @staticmethod
    def _unwrap_table_payload(table_data: Any) -> Dict[str, Any]:
        """
        Normaliza la entrada a la carga útil de la tabla ({table_markdown, table_json, ...}).

        Acepta tanto el payload directo como el chunk completo del pipeline, donde
        el payload viene serializado dentro de `original_chunk`. Antes esto no se
        desenvolvía y `needs_splitting` recibía el chunk: buscaba `chunk["table_json"]`
        (que no existe en ese nivel), obtenía [] filas y devolvía SIEMPRE False, por
        lo que el split de tablas nunca se ejecutaba.
        """
        if isinstance(table_data, str):
            try:
                table_data = json.loads(table_data)
            except (ValueError, TypeError):
                return {}

        if not isinstance(table_data, dict):
            return {}

        # ¿Es el chunk completo? Entonces la carga útil está en original_chunk.
        if "table_json" not in table_data and "table_markdown" not in table_data:
            original = table_data.get("original_chunk")
            if original is not None:
                if isinstance(original, str):
                    try:
                        original = json.loads(original)
                    except (ValueError, TypeError):
                        return {}
                if isinstance(original, dict):
                    return original
            return {}

        return table_data

    # Detección de "tabla índice de parámetros": una grilla cuyas celdas son pares
    # (nombre, código) repetidos horizontalmente, tipo
    #   | Programa básico | Volt placa motor | P101 | Modo de Paro     | P107 |
    #   |                 | Hz placa motor   | P102 | Referencia Veloc | P108 |
    #
    # Estas tablas son el techo medido del retrieval: para "¿con qué parámetro seteás el
    # voltaje de placa del motor?" (respuesta P101) el chunk correcto contenía 19
    # parámetros en 605 caracteres, así que su embedding es el promedio de 19 cosas sin
    # relación y no matchea fuerte con ninguna. Partir por FILAS no ayuda: la densidad
    # está DENTRO de la fila (2 parámetros por fila), así que 10 filas siguen siendo 20
    # parámetros.
    # Tolera el marcador de nota al pie que traen los códigos en las tablas reales:
    # "P106(1)" aparece tal cual en la p28 del manual, y con el patrón estricto se
    # descartaba.
    _PARAM_CODE_RE = re.compile(r"^[A-Za-z]\d{3}(\(\d+\))?$")
    _MIN_CODES_FOR_INDEX_TABLE = 6
    _PARAMS_PER_CHUNK = 4

    @classmethod
    def _param_pairs(cls, rows: List[List[Any]]) -> List[Tuple[str, str]]:
        """
        Pares (nombre, código) de una grilla índice, leyendo cada fila de izquierda a
        derecha: un código toma como nombre la celda no vacía inmediatamente anterior.
        """
        pares: List[Tuple[str, str]] = []
        for row in rows:
            celdas = [("" if c is None else str(c)).strip() for c in row]
            for i, celda in enumerate(celdas):
                if not cls._PARAM_CODE_RE.match(celda):
                    continue
                nombre = ""
                for anterior in reversed(celdas[:i]):
                    if anterior and not cls._PARAM_CODE_RE.match(anterior):
                        nombre = anterior
                        break
                if nombre:
                    pares.append((nombre, celda))
        return pares

    @classmethod
    def is_parameter_index_table(cls, rows: List[List[Any]]) -> bool:
        return len(cls._param_pairs(rows)) >= cls._MIN_CODES_FOR_INDEX_TABLE

    def _split_parameter_index(
        self,
        table_chunk: Dict[str, Any],
        table_data: Dict[str, Any],
        rows: List[List[Any]],
        context: str,
    ) -> List[Dict[str, Any]]:
        """
        Parte una tabla índice por PARÁMETRO en vez de por fila, para que cada nombre
        quede en un chunk chico y su embedding sea discriminante.

        Se conserva el grupo (la primera celda no vacía de la fila, ej. "Programa
        básico") como encabezado de cada parte, porque es lo que le da sentido al código.
        """
        pares = self._param_pairs(rows)

        # El nombre del grupo ("Programa básico", "Grupo de pantalla") viene en la primera
        # columna como celda combinada: aparece una vez y el resto de las filas la tienen
        # vacía. Se busca ahí, entre las filas de DATOS.
        #
        # Se descartaron dos reglas más ingenuas: tomar la primera celda no vacía de la
        # tabla agarraba el encabezado de columna y salía "Grupo: Grupo"; y exigir que no
        # hubiera códigos a la derecha lo descartaba, porque la celda del grupo comparte
        # fila con los primeros parámetros.
        #
        # El guard de "mayormente vacía" evita confundir el grupo con una columna de
        # nombres de parámetro, que estaría llena en todas las filas. Puede no haber
        # grupo (queda en otra parte de la tabla partida): en ese caso no se prefija nada.
        data_rows = rows[1:] if self._detect_header(rows) else rows
        grupo = ""
        if data_rows:
            primera_col = [("" if r[0] is None else str(r[0])).strip() if r else "" for r in data_rows]
            vacias = sum(1 for v in primera_col if not v)
            if vacias >= len(primera_col) / 2:
                grupo = next((v for v in primera_col if v and not self._PARAM_CODE_RE.match(v)), "")

        total = (len(pares) + self._PARAMS_PER_CHUNK - 1) // self._PARAMS_PER_CHUNK
        chunks = []
        for indice, inicio in enumerate(range(0, len(pares), self._PARAMS_PER_CHUNK), start=1):
            grupo_pares = pares[inicio:inicio + self._PARAMS_PER_CHUNK]
            filas = [["Parámetro", "Código"]] + [[n, c] for n, c in grupo_pares]
            markdown = self._rows_to_markdown(filas, has_header=True)
            if grupo:
                markdown = f"Grupo: {grupo}\n\n{markdown}"

            # El texto para BM25 lleva los pares planos, sin sintaxis de markdown
            plano = " ".join(f"{n} {c}" for n, c in grupo_pares)

            chunks.append({
                **table_chunk,
                "chunk_id": f"{table_chunk.get('chunk_id')}_param{indice}",
                "original_chunk": json.dumps({
                    "table_markdown": markdown,
                    "table_json": {"rows": filas},
                    "bbox": table_data.get("bbox"),
                    "image_path": table_data.get("image_path"),
                    "is_partial": True,
                    "part": indice,
                    "total_parts": total,
                    "row_range": f"parámetros {inicio+1}-{inicio+len(grupo_pares)}",
                }, ensure_ascii=False),
                "searchable_text": f"{context} {grupo} {plano}".strip(),
                "table_part": indice,
                "table_total_parts": total,
                "content_type": "table",
            })

        logger.info(
            f"Tabla índice de parámetros dividida en {len(chunks)} chunks "
            f"({len(pares)} parámetros, {self._PARAMS_PER_CHUNK} por chunk)"
        )
        return chunks

    # ── Tablas de REFERENCIA POR CÓDIGO ────────────────────────────────────────────
    #
    # Distinta de la grilla índice: acá cada FILA está indexada por un código y es
    # autocontenida. La tabla de fallos del variador es el caso canónico:
    #
    #   | N.º  | Fallo          | Descripción                  | Acción            |
    #   | F12  | Sobrecorr. HW  | La corriente de salida...    | Revise la prog... |
    #   | F13  | Fallo tierra   | Se ha detectado...           | Revise el motor.. |
    #
    # Es el contenido MÁS valioso de este corpus —"me tira F048, qué hago" es la consulta
    # canónica de un técnico— y con el split por filas normal quedaba inutilizable.
    # Medido sobre los 17 códigos de fallo del manual:
    #
    #   chunk con 3 códigos  -> los 3 se recuperan CON su fila
    #   chunk con 10 códigos -> 9 de 10 devuelven la parte equivocada de la tabla
    #   chunk con 4 códigos  -> los 3 críticos no llegan nunca
    #
    # O sea: 14 de 17 "llegaban" contando por página, pero solo 6 de 17 traían la fila
    # que explica esa falla. El embedding de un chunk con 10 fallos distintos es el
    # promedio de 10 cosas sin relación y no discrimina cuál de las partes tiene F039.
    #
    # Estas tablas se parten en chunks de pocas filas, repitiendo el encabezado.
    _CODE_CELL_RE = re.compile(r"^[A-Za-z]{1,2}\d{2,3}(\(\d+\))?$")
    _ROWS_PER_CODE_CHUNK = 3
    _MIN_CODED_ROWS = 4

    @classmethod
    def _coded_row_ratio(cls, rows: List[List[Any]]) -> float:
        """Fracción de filas de datos cuya primera celda no vacía es un código."""
        data_rows = rows[1:] if len(rows) > 1 else rows
        if not data_rows:
            return 0.0
        con_codigo = 0
        for row in data_rows:
            for celda in row:
                texto = ("" if celda is None else str(celda)).strip()
                if not texto:
                    continue
                if cls._CODE_CELL_RE.match(texto):
                    con_codigo += 1
                break                      # solo la PRIMERA celda no vacía
        return con_codigo / len(data_rows)

    @classmethod
    def is_code_reference_table(cls, rows: List[List[Any]]) -> bool:
        """
        True si es una tabla de referencia por código: filas indexadas por un
        identificador, cada una autocontenida. Se exige mayoría de filas con código para
        no confundirla con una tabla que solo menciona algún código al pasar.
        """
        data_rows = rows[1:] if len(rows) > 1 else rows
        return len(data_rows) >= cls._MIN_CODED_ROWS and cls._coded_row_ratio(rows) >= 0.6

    def needs_splitting(self, table_data: Dict[str, Any]) -> bool:
        """
        Determina si una tabla necesita ser dividida.

        Args:
            table_data: Chunk de tabla del pipeline, o su payload de tabla

        Returns:
            True si la tabla debe dividirse
        """
        try:
            table_json = self._unwrap_table_payload(table_data)

            # Obtener filas
            rows = (table_json.get("table_json") or {}).get("rows", [])

            if not rows:
                return False

            # Criterios para split:
            # 0. Tabla índice de parámetros: se parte por parámetro, no por fila, así que
            #    puede necesitar split incluso con pocas filas (la densidad está dentro
            #    de la fila). Ver _split_parameter_index.
            if self.is_parameter_index_table(rows):
                return True

            # Tabla de referencia por código: se parte fino aunque tenga pocas filas
            if self.is_code_reference_table(rows) and len(rows) - 1 > self._ROWS_PER_CODE_CHUNK:
                return True

            # 1. Más filas que el máximo
            if len(rows) > self.max_rows_per_chunk:
                return True
            
            # 2. Estimación de tokens (aprox 4 chars = 1 token)
            table_markdown = table_json.get("table_markdown") or ""
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
            table_data = self._unwrap_table_payload(table_chunk)

            table_json = table_data.get("table_json") or {}
            rows = table_json.get("rows", [])

            if not rows:
                return [table_chunk]

            if self.is_parameter_index_table(rows):
                return self._split_parameter_index(table_chunk, table_data, rows, context)

            # Filas indexadas por código: se usa un tamaño de chunk mucho menor, porque
            # cada fila se consulta por separado ("qué significa F048").
            filas_por_chunk = (
                self._ROWS_PER_CODE_CHUNK
                if self.is_code_reference_table(rows)
                else self.max_rows_per_chunk
            )

            if len(rows) <= filas_por_chunk:
                # No necesita split, devolver como está
                return [table_chunk]
            
            # Extraer header (primera fila generalmente)
            has_header = self._detect_header(rows)
            header_row = rows[0] if has_header else None
            data_rows = rows[1:] if has_header else rows
            
            # Dividir en chunks
            chunks = []
            for i in range(0, len(data_rows), filas_por_chunk):
                chunk_rows = data_rows[i:i + filas_por_chunk]
                
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
                chunk_num = (i // filas_por_chunk) + 1
                total_chunks = (len(data_rows) + filas_por_chunk - 1) // filas_por_chunk
                row_range = f"filas {i+1}-{min(i+filas_por_chunk, len(data_rows))}"
                
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
                    # Se mantiene "table" a propósito: las partes SIGUEN siendo tablas y
                    # así todo el pipeline posterior (embeddings, DualIndexer, media
                    # storage, validadores) las trata igual. Un content_type propio
                    # ("table_partial") no estaba contemplado en ninguna de esas etapas
                    # y hacía que las partes se descartaran silenciosamente al indexar.
                    # La condición de parcialidad viaja en is_partial/part/total_parts.
                    "content_type": "table",
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
