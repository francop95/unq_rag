"""
Indexador multimodal para Chroma.
Mantiene índices de contenido heterogéneo con capacidad de retrieval de contexto completo.
"""

import os
import json
from typing import List, Dict, Any, Optional, Tuple
import chromadb

from task_utils.multimodal_storage import MultimodalChunk, MultimodalStorage, MultimodalContext


class MultimodalIndexer:
    """
    Indexa contenido multimodal en Chroma.
    - Indexa solo texto (para búsqueda vectorial)
    - Mantiene referencias a medias (imágenes, tablas)
    - Permite retrieval de contexto completo
    """
    
    def __init__(
        self, 
        index_name: str = "multimodal_index",
        index_path: str = "./data/chroma",
        storage: Optional[MultimodalStorage] = None
    ):
        self.index_name = index_name
        self.index_path = index_path
        self.storage = storage or MultimodalStorage()
        
        # Inicializar cliente Chroma persistente
        self.client = chromadb.PersistentClient(path=index_path)
        self.collection = self.client.get_or_create_collection(
            name=index_name,
            metadata={"hnsw:space": "cosine", "hnsw:search_ef": 100}
        )
    
    def add_multimodal_chunks(
        self,
        chunks: List[MultimodalChunk],
        document_id: str,
        collection_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Indexa chunks multimodales en Chroma.
        
        Args:
            chunks: Lista de chunks multimodales a indexar
            document_id: ID del documento fuente
            collection_name: Nombre de colección (si None, usa self.collection)
            
        Returns:
            Estadísticas de indexación
        """        # Sanitizar nombre de colección (Chroma solo permite 3-512 chars, [a-zA-Z0-9._-])
        if collection_name is None:
            # Reemplazar espacios y caracteres especiales
            collection_name = document_id.replace(" ", "_").replace("-", "_")
            collection_name = "doc_" + collection_name[:100]  # Limitar longitud        if collection_name:
            collection = self.client.get_or_create_collection(
                name=collection_name,
                metadata={"hnsw:space": "cosine"}
            )
        else:
            collection = self.collection
        
        ids = []
        embeddings_text = []
        metadatas = []
        
        for chunk in chunks:
            chunk_id = chunk.chunk_id
            ids.append(chunk_id)
            embeddings_text.append(chunk.text_content)
            
            # Metadata para búsqueda
            metadata = {
                "source_file": chunk.source_file,
                "page_number": chunk.page_number,
                "content_type": chunk.content_type,
                "confidence": chunk.confidence,
                "num_images": len([r for r in chunk.media_references if r.media_type == "image"]),
                "num_tables": len([r for r in chunk.media_references if r.media_type == "table"]),
            }
            metadatas.append(metadata)
        
        # Agregar a colección
        if len(ids) > 0:
            collection.add(
                ids=ids,
                metadatas=metadatas,
                documents=embeddings_text,  # Chroma genera embeddings automáticamente
            )
        
        return {
            "indexed_chunks": len(chunks),
            "document_id": document_id,
            "collection": collection.name,
        }
    
    def search_multimodal(
        self,
        query: str,
        n_results: int = 5,
        collection_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Busca chunks por similitud textual.
        
        Args:
            query: Consulta de búsqueda
            n_results: Número de resultados
            collection_name: Colección a buscar
            
        Returns:
            Lista de resultados con metadata
        """
        if collection_name:
            collection = self.client.get_collection(name=collection_name)
        else:
            collection = self.collection
        
        results = collection.query(
            query_texts=[query],
            n_results=n_results,
            include=["documents", "metadatas", "distances"]
        )
        
        # Procesar resultados
        processed_results = []
        if results["ids"][0]:
            for i, chunk_id in enumerate(results["ids"][0]):
                processed_results.append({
                    "chunk_id": chunk_id,
                    "text": results["documents"][0][i],
                    "metadata": results["metadatas"][0][i],
                    "distance": results["distances"][0][i],
                    "similarity": 1 - results["distances"][0][i],  # Convert distance to similarity
                })
        
        return processed_results
    
    def get_multimodal_context(
        self,
        chunk_ids: List[str],
        chunks_dir: str,
        cluster_by_page: bool = True
    ) -> Optional[MultimodalContext]:
        """
        Agrupa chunks en contexto multimodal completo.
        
        Args:
            chunk_ids: IDs de chunks a agrupar
            chunks_dir: Directorio donde están guardados los chunks
            cluster_by_page: Si True, agrupa por página del documento
            
        Returns:
            MultimodalContext con todos los chunks y medias relacionadas
        """
        context = MultimodalContext()
        
        for chunk_id in chunk_ids:
            chunk_path = os.path.join(chunks_dir, f"{chunk_id}.json")
            chunk = self.storage.load_multimodal_chunk(chunk_path)
            
            if chunk:
                context.add_chunk(chunk)
        
        return context if len(context.chunks) > 0 else None
    
    def search_with_context(
        self,
        query: str,
        chunks_dir: str,
        n_results: int = 3,
        n_context_chunks: int = 10,  # Chunks adicionales del mismo documento/página
        collection_name: Optional[str] = None,
    ) -> List[MultimodalContext]:
        """
        Busca y retorna contexto multimodal completo.
        
        Args:
            query: Consulta de búsqueda
            chunks_dir: Directorio de chunks
            n_results: Resultados principales
            n_context_chunks: Chunks adicionales a incluir en contexto
            collection_name: Colección a buscar
            
        Returns:
            Lista de contextos multimodales
        """
        # Búsqueda inicial
        search_results = self.search_multimodal(
            query=query,
            n_results=n_results,
            collection_name=collection_name
        )
        
        if not search_results:
            return []
        
        # Agrupar resultados por documento/página
        contexts = []
        processed_files = set()
        
        for result in search_results:
            chunk_id = result["chunk_id"]
            metadata = result["metadata"]
            
            # Clave única para evitar duplicados
            file_key = f"{metadata['source_file']}_{metadata['page_number']}"
            
            if file_key not in processed_files:
                processed_files.add(file_key)
                
                # Obtener chunks relacionados del mismo documento/página
                related_chunks = self._get_related_chunks(
                    metadata["source_file"],
                    metadata["page_number"],
                    chunks_dir,
                    max_chunks=n_context_chunks
                )
                
                # Crear contexto
                context = MultimodalContext()
                for related_chunk_id in related_chunks:
                    chunk_path = os.path.join(chunks_dir, f"{related_chunk_id}.json")
                    chunk = self.storage.load_multimodal_chunk(chunk_path)
                    if chunk:
                        context.add_chunk(chunk)
                
                if len(context.chunks) > 0:
                    contexts.append(context)
        
        return contexts
    
    @staticmethod
    def _get_related_chunks(
        source_file: str,
        page_number: int,
        chunks_dir: str,
        max_chunks: int = 10
    ) -> List[str]:
        """
        Obtiene chunks del mismo documento y página.
        
        Args:
            source_file: Archivo fuente
            page_number: Número de página
            chunks_dir: Directorio de chunks
            max_chunks: Máximo de chunks a retornar
            
        Returns:
            Lista de IDs de chunks relacionados
        """
        related = []
        
        if not os.path.exists(chunks_dir):
            return related
        
        for filename in os.listdir(chunks_dir):
            if not filename.endswith('.json'):
                continue
            
            chunk_path = os.path.join(chunks_dir, filename)
            try:
                with open(chunk_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                if (data.get("source_file") == source_file and 
                    data.get("page_number") == page_number):
                    related.append(data["chunk_id"])
                    
                    if len(related) >= max_chunks:
                        break
            except Exception:
                pass
        
        return related
    
    def delete_collection(self, collection_name: str):
        """Elimina una colección."""
        try:
            self.client.delete_collection(name=collection_name)
        except Exception as e:
            print(f"Error eliminando colección: {e}")
    
    def list_collections(self) -> List[str]:
        """Lista todas las colecciones."""
        collections = self.client.list_collections()
        return [c.name for c in collections]


class MultimodalRetriever:
    """
    Recupera contexto multimodal (texto + imágenes + tablas).
    Interfaz de alto nivel para búsqueda y recuperación.
    """
    
    def __init__(
        self,
        indexer: MultimodalIndexer,
        storage: MultimodalStorage,
        chunks_dir: str
    ):
        self.indexer = indexer
        self.storage = storage
        self.chunks_dir = chunks_dir
    
    def retrieve(
        self,
        query: str,
        n_results: int = 3,
        load_media: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Recupera contexto multimodal completo para una consulta.
        
        Args:
            query: Consulta de búsqueda
            n_results: Número de contextos a retornar
            load_media: Si True, carga las imágenes/tablas en memoria
            
        Returns:
            Lista de contextos con contenido multimodal completo
        """
        contexts = self.indexer.search_with_context(
            query=query,
            chunks_dir=self.chunks_dir,
            n_results=n_results
        )
        
        results = []
        for context in contexts:
            result = context.to_dict()
            
            if load_media:
                # Cargar imágenes
                result["images_binary"] = {}
                for img_ref in context.images:
                    img_bytes = self.storage.load_image(img_ref.file_path)
                    if img_bytes:
                        result["images_binary"][img_ref.media_id] = {
                            "data": img_bytes.hex()[:100] + "...",  # Preview
                            "path": img_ref.file_path,
                            "description": img_ref.description,
                        }
                
                # Cargar tablas
                result["tables_data"] = {}
                for table_ref in context.tables:
                    table_data = self.storage.load_table(table_ref.file_path)
                    if table_data:
                        result["tables_data"][table_ref.media_id] = {
                            "data": table_data,
                            "path": table_ref.file_path,
                            "description": table_ref.description,
                        }
            
            results.append(result)
        
        return results
    
    def retrieve_summary(self, query: str, n_results: int = 3) -> str:
        """Retorna resumen legible de resultados multimodales."""
        contexts = self.indexer.search_with_context(
            query=query,
            chunks_dir=self.chunks_dir,
            n_results=n_results
        )
        
        summary = f"📚 **Resultados para: '{query}'**\n\n"
        
        for i, context in enumerate(contexts, 1):
            summary += f"**Resultado {i}:** {context.summary()}\n"
            summary += f"Contenido: {context.chunks[0].text_content[:200]}...\n\n"
        
        return summary
