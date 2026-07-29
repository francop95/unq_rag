"""
Almacenamiento y gestión de contenido multimodal (texto, imágenes, tablas).
Estructura optimizada para índices multimodales.
"""

import os
import json
import hashlib
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict
from datetime import datetime
import base64


@dataclass
class MediaReference:
    """Referencia a un archivo multimedia (imagen o tabla)."""
    media_id: str  # Hash único del contenido
    media_type: str  # 'image' o 'table'
    file_path: str  # Ruta relativa al archivo
    description: str  # Descripción o notas
    source_page: int  # Página del documento original
    bbox: Optional[List[int]] = None  # Bounding box si aplica


@dataclass
class MultimodalChunk:
    """Chunk con contenido multimodal."""
    chunk_id: str
    text_content: str  # Texto principal
    content_type: str  # 'text', 'table', 'image', 'mixed'
    source_file: str
    page_number: int
    media_references: List[MediaReference]  # Medias relacionadas
    metadata: Dict[str, Any]  # Metadata adicional
    created_at: str
    confidence: float = 1.0


class MultimodalStorage:
    """Gestiona almacenamiento multimodal (imágenes, tablas, texto)."""
    
    def __init__(self, base_path: str = "./data"):
        self.base_path = base_path
        self.chunks_path = os.path.join(base_path, "chunks")
        self.images_path = os.path.join(base_path, "media/images")
        self.tables_path = os.path.join(base_path, "media/tables")
        
        # Crear directorios
        os.makedirs(self.chunks_path, exist_ok=True)
        os.makedirs(self.images_path, exist_ok=True)
        os.makedirs(self.tables_path, exist_ok=True)
    
    def save_image(self, image_bytes: bytes, document_id: str, page: int, img_idx: int) -> str:
        """
        Guarda una imagen y retorna el path relativo.
        
        Args:
            image_bytes: Bytes de la imagen PNG
            document_id: ID del documento
            page: Número de página
            img_idx: Índice de imagen en la página
            
        Returns:
            Path relativo de la imagen guardada
        """
        # Generar ID único basado en contenido
        media_id = hashlib.md5(image_bytes).hexdigest()[:12]
        
        # Crear estructura: media/images/document_id/page_XX/image_YY.png
        img_dir = os.path.join(self.images_path, document_id, f"page_{page:03d}")
        os.makedirs(img_dir, exist_ok=True)
        
        img_filename = f"image_{img_idx:02d}_{media_id}.png"
        img_path = os.path.join(img_dir, img_filename)
        
        # Guardar imagen
        with open(img_path, 'wb') as f:
            f.write(image_bytes)
        
        # Retornar path relativo
        return os.path.relpath(img_path, self.base_path)
    
    def save_table(self, table_data: Dict[str, Any], document_id: str, page: int, table_idx: int) -> str:
        """
        Guarda una tabla como JSON y retorna el path relativo.
        
        Args:
            table_data: Diccionario con datos de tabla (markdown, json, etc)
            document_id: ID del documento
            page: Número de página
            table_idx: Índice de tabla en la página
            
        Returns:
            Path relativo del archivo JSON guardado
        """
        # Generar ID único
        table_json = json.dumps(table_data, ensure_ascii=False)
        media_id = hashlib.md5(table_json.encode()).hexdigest()[:12]
        
        # Crear estructura: media/tables/document_id/page_XX/
        table_dir = os.path.join(self.tables_path, document_id, f"page_{page:03d}")
        os.makedirs(table_dir, exist_ok=True)
        
        table_filename = f"table_{table_idx:02d}_{media_id}.json"
        table_path = os.path.join(table_dir, table_filename)
        
        # Guardar tabla
        with open(table_path, 'w', encoding='utf-8') as f:
            json.dump(table_data, f, ensure_ascii=False, indent=2)
        
        return os.path.relpath(table_path, self.base_path)
    
    def load_image(self, rel_path: str) -> Optional[bytes]:
        """Carga imagen desde path relativo."""
        full_path = os.path.join(self.base_path, rel_path)
        if os.path.exists(full_path):
            with open(full_path, 'rb') as f:
                return f.read()
        return None
    
    def load_table(self, rel_path: str) -> Optional[Dict]:
        """Carga tabla desde path relativo."""
        full_path = os.path.join(self.base_path, rel_path)
        if os.path.exists(full_path):
            with open(full_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return None
    
    def save_multimodal_chunk(self, chunk: MultimodalChunk, output_dir: str):
        """
        Guarda un chunk multimodal como JSON con referencias a medias.
        
        Args:
            chunk: Chunk multimodal a guardar
            output_dir: Directorio de salida
        """
        os.makedirs(output_dir, exist_ok=True)
        
        chunk_dict = {
            "chunk_id": chunk.chunk_id,
            "text_content": chunk.text_content,
            "content_type": chunk.content_type,
            "source_file": chunk.source_file,
            "page_number": chunk.page_number,
            "media_references": [asdict(ref) for ref in chunk.media_references],
            "metadata": chunk.metadata,
            "created_at": chunk.created_at,
            "confidence": chunk.confidence,
        }
        
        chunk_filename = f"{chunk.chunk_id}.json"
        chunk_path = os.path.join(output_dir, chunk_filename)
        
        with open(chunk_path, 'w', encoding='utf-8') as f:
            json.dump(chunk_dict, f, ensure_ascii=False, indent=2)
    
    def load_multimodal_chunk(self, chunk_path: str) -> Optional[MultimodalChunk]:
        """Carga un chunk multimodal desde JSON."""
        if not os.path.exists(chunk_path):
            return None
        
        with open(chunk_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        media_refs = [
            MediaReference(**ref) for ref in data.get("media_references", [])
        ]
        
        return MultimodalChunk(
            chunk_id=data["chunk_id"],
            text_content=data["text_content"],
            content_type=data["content_type"],
            source_file=data["source_file"],
            page_number=data["page_number"],
            media_references=media_refs,
            metadata=data.get("metadata", {}),
            created_at=data["created_at"],
            confidence=data.get("confidence", 1.0),
        )
    
    def get_media_by_type(self, chunks: List[MultimodalChunk], media_type: str) -> List[MediaReference]:
        """
        Extrae todas las referencias de un tipo de media de múltiples chunks.
        
        Args:
            chunks: Lista de chunks
            media_type: 'image' o 'table'
            
        Returns:
            Lista de referencias al tipo especificado
        """
        media_refs = []
        seen_ids = set()
        
        for chunk in chunks:
            for ref in chunk.media_references:
                if ref.media_type == media_type and ref.media_id not in seen_ids:
                    media_refs.append(ref)
                    seen_ids.add(ref.media_id)
        
        return media_refs


class MultimodalContext:
    """Agrupa chunks relacionados con su contenido multimodal."""
    
    def __init__(self):
        self.chunks: List[MultimodalChunk] = []
        self.images: List[MediaReference] = []
        self.tables: List[MediaReference] = []
        self.source_file: str = ""
        self.pages: List[int] = []
    
    def add_chunk(self, chunk: MultimodalChunk):
        """Agrega un chunk al contexto."""
        self.chunks.append(chunk)
        if chunk.source_file:
            self.source_file = chunk.source_file
        if chunk.page_number not in self.pages:
            self.pages.append(chunk.page_number)
        
        # Extraer medias
        for ref in chunk.media_references:
            if ref.media_type == "image" and ref not in self.images:
                self.images.append(ref)
            elif ref.media_type == "table" and ref not in self.tables:
                self.tables.append(ref)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convierte contexto a diccionario para serialización."""
        return {
            "source_file": self.source_file,
            "pages": sorted(self.pages),
            "num_chunks": len(self.chunks),
            "num_images": len(self.images),
            "num_tables": len(self.tables),
            "text_content": "\n\n".join(c.text_content for c in self.chunks),
            "images": [asdict(img) for img in self.images],
            "tables": [asdict(tbl) for tbl in self.tables],
        }
    
    def summary(self) -> str:
        """Resumen legible del contexto."""
        return (
            f"📄 {self.source_file} (páginas {self.pages})\n"
            f"📝 {len(self.chunks)} chunks\n"
            f"🖼️  {len(self.images)} imágenes\n"
            f"📊 {len(self.tables)} tablas"
        )
