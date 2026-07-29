"""
Adaptador para convertir chunks estándar a multimodales e integrar en pipeline.
Conecta la ingesta actual con el sistema multimodal.
"""

import os
import json
import uuid
from typing import List, Dict, Any, Tuple, Optional
from datetime import datetime
from PIL import Image
import io

from task_utils.multimodal_storage import MultimodalChunk, MediaReference, MultimodalStorage
from task_utils.multimodal_indexer import MultimodalIndexer, MultimodalRetriever


class ChunkToMultimodalAdapter:
    """
    Convierte chunks estándares (JSON del pipeline actual) a chunks multimodales.
    Extrae medias (imágenes, tablas) y crea referencias.
    """
    
    def __init__(
        self,
        storage: Optional[MultimodalStorage] = None,
        base_data_path: str = "./data"
    ):
        self.storage = storage or MultimodalStorage(base_data_path)
        self.base_data_path = base_data_path
    
    def convert_chunks_from_json(
        self,
        json_file_path: str,
        document_id: str,
        page_number: int,
        extract_media: bool = True
    ) -> Tuple[List[MultimodalChunk], Dict[str, Any]]:
        """
        Convierte chunks almacenados en JSON a chunks multimodales.
        Extrae y almacena medias.
        
        Args:
            json_file_path: Ruta al archivo JSON con chunks
            document_id: ID único del documento
            page_number: Número de página
            extract_media: Si True, extrae y guarda imágenes/tablas
            
        Returns:
            Tuple de (lista de MultimodalChunks, estadísticas)
        """
        multimodal_chunks = []
        stats = {
            "total_chunks": 0,
            "text_chunks": 0,
            "mixed_chunks": 0,
            "images_extracted": 0,
            "tables_extracted": 0,
        }
        
        if not os.path.exists(json_file_path):
            return multimodal_chunks, stats
        
        try:
            with open(json_file_path, 'r', encoding='utf-8') as f:
                chunks_data = json.load(f)
        except Exception as e:
            print(f"Error leyendo archivo: {e}")
            return multimodal_chunks, stats
        
        # Asegurar que es lista
        if not isinstance(chunks_data, list):
            chunks_data = [chunks_data]
        
        image_idx = 0
        table_idx = 0
        
        for chunk_data in chunks_data:
            chunk_id = str(uuid.uuid4())[:12]
            media_references = []
            content_type = "text"
            text_content = ""
            
            # Inferir página del chunk si está disponible (override del parámetro)
            chunk_page = page_number
            if isinstance(chunk_data, dict):
                # Intentar obtener página del chunk mismo
                if "page_num" in chunk_data:
                    try:
                        chunk_page = int(chunk_data["page_num"])
                    except:
                        pass
                elif "page" in chunk_data:
                    try:
                        chunk_page = int(chunk_data["page"])
                    except:
                        pass
            
            # Procesar contenido original
            if isinstance(chunk_data, dict):
                # Formato actual del sistema: content_type y original_chunk (JSON string)
                chunk_type = chunk_data.get("content_type", "text")
                original_chunk_str = chunk_data.get("original_chunk", "{}")
                
                # Parsear el JSON string si existe
                try:
                    if isinstance(original_chunk_str, str) and original_chunk_str.startswith("{"):
                        original_chunk = json.loads(original_chunk_str)
                    else:
                        original_chunk = {}
                except:
                    original_chunk = {}
                
                if chunk_type == "text":
                    # Contenido de texto directo o del original_chunk
                    text_content = chunk_data.get("text_content") or \
                                  original_chunk.get("text", "") or \
                                  chunk_data.get("content", "") or \
                                  chunk_data.get("original_chunk", "")
                    
                    # Si original_chunk no era JSON, usarlo como texto directo
                    if isinstance(original_chunk_str, str) and not original_chunk_str.startswith("{"):
                        text_content = original_chunk_str
                    
                    content_type = "text"
                    
                elif chunk_type == "image":
                    # Referencia a imagen
                    description = chunk_data.get("notes", "")
                    bbox = original_chunk.get("bbox", [])
                    image_path_original = original_chunk.get("image_path", "")
                    text_content = description or f"[Image from page {chunk_page}]"
                    
                    content_type = "image"
                    
                    # Guardar imagen en media/images si extract_media está habilitado
                    final_image_path = image_path_original
                    if extract_media and image_path_original and os.path.exists(image_path_original):
                        try:
                            # Leer imagen desde crops
                            with open(image_path_original, 'rb') as f:
                                image_bytes = f.read()
                            
                            # Guardar en media/images usando storage
                            final_image_path = self.storage.save_image(
                                image_bytes=image_bytes,
                                document_id=document_id,
                                page=chunk_page,
                                img_idx=image_idx
                            )
                        except Exception as e:
                            # Si falla, mantener path original
                            print(f"Warning: No se pudo copiar imagen {image_path_original}: {e}")
                            final_image_path = image_path_original
                    
                    media_ref = MediaReference(
                        media_id=f"img_{image_idx}",
                        media_type="image",
                        file_path=final_image_path,
                        description=description,
                        source_page=chunk_page,
                        bbox=bbox
                    )
                    media_references.append(media_ref)
                    image_idx += 1
                    stats["images_extracted"] += 1
                    
                elif chunk_type == "table":
                    # Tabla con contenido estructurado
                    bbox = original_chunk.get("bbox", [])
                    markdown = original_chunk.get("table_markdown", "")
                    table_json = original_chunk.get("table_json", {})
                    
                    # Usar markdown como texto visible
                    text_content = markdown or f"[Table from page {chunk_page}]"
                    
                    content_type = "table"
                    
                    # Guardar tabla si existe contenido
                    if extract_media and (markdown or table_json):
                        # Generar contenido searchable
                        searchable_text = self._generate_searchable_from_table({
                            "markdown": markdown,
                            "json": table_json if isinstance(table_json, dict) else {}
                        })
                        
                        # Agregar al tabla_content
                        enriched_table_content = {
                            "markdown": markdown,
                            "json": table_json,
                            "searchable_text": searchable_text,
                            "bbox": bbox
                        }
                        
                        table_path = self.storage.save_table(
                            table_data=enriched_table_content,
                            document_id=document_id,
                            page=chunk_page,
                            table_idx=table_idx
                        )
                        
                        media_ref = MediaReference(
                            media_id=f"tbl_{table_idx}",
                            media_type="table",
                            file_path=table_path,
                            description=f"Table on page {chunk_page}",
                            source_page=chunk_page,
                            bbox=bbox
                        )
                        media_references.append(media_ref)
                        table_idx += 1
                        stats["tables_extracted"] += 1
            else:
                # Si es string, usar como texto
                text_content = str(chunk_data)
                content_type = "text"
            
            # Validar que tenemos contenido
            if not text_content or len(text_content.strip()) < 5:
                continue
            
            # Crear chunk multimodal
            multimodal_chunk = MultimodalChunk(
                chunk_id=chunk_id,
                text_content=text_content,
                content_type=content_type,
                source_file=document_id,
                page_number=chunk_page,
                media_references=media_references,
                metadata={
                    "original_type": chunk_type if isinstance(chunk_data, dict) else "text",
                    "extraction_date": datetime.now().isoformat(),
                },
                created_at=datetime.now().isoformat(),
                confidence=1.0
            )
            
            multimodal_chunks.append(multimodal_chunk)
            stats["total_chunks"] += 1
            
            if content_type == "text":
                stats["text_chunks"] += 1
            else:
                stats["mixed_chunks"] += 1
        
        return multimodal_chunks, stats
    
    @staticmethod
    def _generate_searchable_from_table(table_content: Dict[str, Any]) -> str:
        """
        Genera texto searchable a partir de contenido de tabla.
        Útil para búsquedas full-text sin estructura.
        """
        searchable_parts = []
        
        # Del markdown
        if "markdown" in table_content:
            # Remover caracteres de formato, mantener contenido
            markdown = table_content["markdown"]
            # Remover pipes y dashes
            clean_text = markdown.replace("|", " ").replace("-", " ")
            searchable_parts.append(clean_text)
        
        # De las filas JSON
        if "json" in table_content and "rows" in table_content["json"]:
            rows = table_content["json"]["rows"]
            for row in rows:
                if isinstance(row, list):
                    row_text = " ".join(str(cell) for cell in row)
                    searchable_parts.append(row_text)
        
        # Unir y limpiar espacios múltiples
        searchable = " ".join(searchable_parts)
        searchable = " ".join(searchable.split())  # Normalizar espacios
        
        return searchable
    
    def convert_document(
        self,
        chunks_dir: str,
        document_id: str,
        output_dir: str
    ) -> Dict[str, Any]:
        """
        Convierte TODOS los chunks de un documento a multimodal.
        PRIORIDAD: Usa validated_chunks.json si existe (chunks validados + deduplicados).
        
        Args:
            chunks_dir: Directorio con chunks JSON del documento
            document_id: ID del documento
            output_dir: Directorio donde guardar chunks multimodales
            
        Returns:
            Estadísticas de conversión
        """
        os.makedirs(output_dir, exist_ok=True)
        
        total_stats = {
            "total_chunks": 0,
            "text_chunks": 0,
            "mixed_chunks": 0,
            "images_extracted": 0,
            "tables_extracted": 0,
            "pages_processed": 0,
        }
        
        if not os.path.exists(chunks_dir):
            return total_stats
        
        # PRIORIDAD 1: Buscar validated_chunks.json (contiene TODOS los chunks validados)
        validated_chunks_path = os.path.join(chunks_dir, "validated_chunks.json")
        if os.path.exists(validated_chunks_path):
            print(f"   ℹ️  Usando validated_chunks.json ({validated_chunks_path})")
            chunks, stats = self.convert_chunks_from_json(
                validated_chunks_path,
                document_id,
                page_number=0,  # Se inferirá de cada chunk
                extract_media=True
            )
            
            # Guardar chunks multimodales
            for chunk in chunks:
                self.storage.save_multimodal_chunk(chunk, output_dir)
            
            # Actualizar estadísticas
            for key in stats:
                total_stats[key] += stats[key]
            
            total_stats["pages_processed"] = 1  # Procesamos un único archivo consolidado
            return total_stats
        
        # PRIORIDAD 2: Si no existe validated_chunks.json, procesar archivos individuales
        print(f"   ℹ️  validated_chunks.json no encontrado, procesando archivos individuales")
        for filename in sorted(os.listdir(chunks_dir)):
            if not filename.endswith('.json'):
                continue
            
            # Skip archivos de reporte
            if filename in ['validation_report.json', 'quality_report.json', 'multimodal_report.json']:
                continue
            
            # Inferir número de página del nombre
            page_number = 0
            try:
                # Intenta extraer de nombre como page_XX.json
                parts = filename.split('_')
                for part in parts:
                    if part.isdigit() and len(part) <= 3:
                        page_number = int(part)
                        break
            except:
                pass
            
            json_path = os.path.join(chunks_dir, filename)
            chunks, stats = self.convert_chunks_from_json(
                json_path,
                document_id,
                page_number,
                extract_media=True
            )
            
            # Guardar chunks multimodales
            for chunk in chunks:
                self.storage.save_multimodal_chunk(chunk, output_dir)
            
            # Acumular estadísticas
            for key in stats:
                total_stats[key] += stats[key]
            
            total_stats["pages_processed"] += 1
        
        return total_stats


class MultimodalPipelineAdapter:
    """
    Integra sistema multimodal en el pipeline de ingesta.
    Reemplaza indexación estándar con multimodal.
    """
    
    def __init__(
        self,
        chunks_base_path: str = "./data/chunks_data",
        chroma_path: str = "./data/chroma",
        index_name: str = "multimodal_documents"
    ):
        self.chunks_base_path = chunks_base_path
        self.storage = MultimodalStorage()
        self.indexer = MultimodalIndexer(
            index_name=index_name,
            index_path=chroma_path,
            storage=self.storage
        )
        self.retriever = MultimodalRetriever(
            indexer=self.indexer,
            storage=self.storage,
            chunks_dir=chunks_base_path
        )
        self.adapter = ChunkToMultimodalAdapter(
            storage=self.storage,
            base_data_path=os.path.dirname(chunks_base_path)
        )
    
    def ingest_document(
        self,
        chunks_dir: str,
        document_id: str,
        multimodal_output_dir: str
    ) -> Dict[str, Any]:
        """
        Ingesta un documento completo en el índice multimodal.
        
        Args:
            chunks_dir: Directorio con chunks JSON del documento
            document_id: ID único del documento
            multimodal_output_dir: Directorio de salida para chunks multimodales
            
        Returns:
            Reporte de ingesta
        """
        # Convertir a multimodal
        conversion_stats = self.adapter.convert_document(
            chunks_dir=chunks_dir,
            document_id=document_id,
            output_dir=multimodal_output_dir
        )
        
        # Cargar chunks multimodales
        multimodal_chunks = []
        for filename in os.listdir(multimodal_output_dir):
            if filename.endswith('.json'):
                chunk_path = os.path.join(multimodal_output_dir, filename)
                chunk = self.storage.load_multimodal_chunk(chunk_path)
                if chunk:
                    multimodal_chunks.append(chunk)
        
        # Indexar en Chroma
        indexing_stats = self.indexer.add_multimodal_chunks(
            chunks=multimodal_chunks,
            document_id=document_id,
            collection_name=f"doc_{document_id}"
        )
        
        return {
            "document_id": document_id,
            "conversion": conversion_stats,
            "indexing": indexing_stats,
            "multimodal_chunks_count": len(multimodal_chunks),
        }
    
    def search(self, query: str, n_results: int = 3) -> List[Dict[str, Any]]:
        """Busca multimodal con contexto completo."""
        return self.retriever.retrieve(
            query=query,
            n_results=n_results,
            load_media=True
        )
    
    def search_summary(self, query: str, n_results: int = 3) -> str:
        """Resumen de búsqueda multimodal."""
        return self.retriever.retrieve_summary(
            query=query,
            n_results=n_results
        )
