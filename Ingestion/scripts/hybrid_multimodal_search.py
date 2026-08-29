"""
Sistema de Búsqueda Híbrida Multimodal AVANZADO
================================================

MEJORAS 2026 ⭐⭐⭐:
1. ✅ Metadata jerárquica (filtros por sección/capítulo)
2. ✅ Contexto expandido (parent-child chunking)
3. ✅ Reranking con cross-encoder (+20-30% precisión)
4. ✅ BM25 sparse retrieval (mejor keywords exactos)
5. ✅ Filtros avanzados de metadata

ÍNDICES:
1. TEXTUAL (OpenAI embeddings) - Texto y tablas
2. VISUAL (CLIP embeddings) - Imágenes  
3. BM25 (sparse) - Keywords exactos

FUSIÓN: Reciprocal Rank Fusion (RRF) + Cross-Encoder Reranking

Uso:
    python scripts/hybrid_multimodal_search.py "diagrama de conexiones del motor"
    python scripts/hybrid_multimodal_search.py "procedimiento de instalación" --filter-chapter 3
"""

import os
import sys
from typing import List, Dict, Any, Tuple, Optional
from dataclasses import dataclass, field
from pathlib import Path

import chromadb
from openai import OpenAI
from sentence_transformers import SentenceTransformer

# Añadir src al path para imports
current_dir = Path(__file__).parent.parent  # Subir un nivel porque estamos en scripts/
src_dir = current_dir / "src"
sys.path.insert(0, str(src_dir))

from config.config_reader import load_config
from task_utils.advanced_retrieval import (
    ContextExpander,
    CrossEncoderReranker,
    BM25Index,
    MetadataFilter
)

# Cargar configuración del proyecto
config = load_config(".env")


@dataclass
class SearchResult:
    """Resultado de búsqueda con metadata enriquecida."""
    doc_id: str
    score: float
    content: str
    content_type: str
    page_num: str
    file_name: str
    source: str  # 'text', 'visual', 'both', 'bm25'
    distance: float
    image_path: str = None
    media_path: str = None  # Copia canónica de tabla/imagen (data/media/...) guardada por DualIndexer
    # Metadata jerárquica ⭐
    chapter: str = ""
    section: str = ""
    hierarchy_path: str = ""
    prev_chunk_id: str = ""
    next_chunk_id: str = ""
    # Scores de reranking ⭐
    rerank_score: float = 0.0
    bm25_score: float = 0.0
    # Contexto expandido ⭐
    expanded_context: str = ""


class HybridMultimodalSearch:
    """
    Motor de búsqueda híbrida que combina:
    - Embeddings textuales (OpenAI)
    - Embeddings visuales (CLIP)
    """
    
    @staticmethod
    def _get_document_name(metadata):
        """Extrae el nombre del documento de metadata con múltiples fallbacks."""
        # Intentar file_name primero
        file_name = metadata.get('file_name')
        if file_name and file_name not in ['', 'nan', 'None', None]:
            return file_name.strip()
        
        # Fallback 1: source_file
        source = metadata.get('source_file') or metadata.get('source')
        if source and source not in ['', 'nan', 'None', None]:
            return source.strip()
        
        # Fallback 2: Construir desde hierarchy_path + page_num
        hierarchy = metadata.get('hierarchy_path') or metadata.get('document_section')
        if hierarchy and hierarchy not in ['', 'nan', 'None', None]:
            page = metadata.get('page_num', '')
            if page and page not in ['', 'nan', 'None', None]:
                return f"{hierarchy.strip()} (pág. {page})"
            return hierarchy.strip()
        
        # Fallback 3: Solo chapter
        chapter = metadata.get('chapter')
        if chapter and chapter not in ['', 'nan', 'None', None]:
            return f"{chapter.strip()}"
        
        return None  # Realmente desconocido
    
    @staticmethod
    def _clean_metadata(value, default='Desconocido'):
        """Limpia valores de metadata que pueden ser None, '', o 'nan'."""
        if value is None or value == '' or str(value).lower() == 'nan' or str(value).lower() == 'none':
            return default
        return str(value).strip()

    def _load_context_expander_chunks(self, chunks_base_path: str):
        """
        Carga en el cache del ContextExpander los chunks de la última corrida
        de cada documento (data/chunks_data/{doc}/{timestamp}/{doc}_{page}/*.json).
        Sin esto, expand_context() nunca encuentra nada y Context Expansion
        no tiene ningún efecto aunque prev_chunk_id/next_chunk_id existan.
        """
        if not os.path.isdir(chunks_base_path):
            return

        for doc_name in sorted(os.listdir(chunks_base_path)):
            doc_path = os.path.join(chunks_base_path, doc_name)
            if not os.path.isdir(doc_path):
                continue

            timestamps = sorted(
                d for d in os.listdir(doc_path) if os.path.isdir(os.path.join(doc_path, d))
            )
            if not timestamps:
                continue

            # Solo la corrida más reciente por documento
            latest_dir = os.path.join(doc_path, timestamps[-1])
            self.context_expander.load_chunks_from_directory(latest_dir)

    def __init__(
        self,
        chroma_path: str = "./data/chroma",
        text_collection_name: str = "text_docs",
        visual_collection_name: str = "visual_docs",
        openai_model: str = "text-embedding-3-large",
        clip_model: str = "clip-ViT-B-32"
    ):
        """
        Inicializa el motor de búsqueda híbrida.
        
        Args:
            chroma_path: Ruta a la base de datos Chroma
            text_collection_name: Nombre de la colección textual
            visual_collection_name: Nombre de la colección visual
            openai_model: Modelo de embeddings OpenAI
            clip_model: Modelo CLIP para embeddings visuales
        """
        print(f"\n{'='*70}")
        print(f"🔧 INICIALIZANDO MOTOR DE BÚSQUEDA HÍBRIDA MULTIMODAL")
        print(f"{'='*70}\n")
        
        # ══════════════════════════════════════════════════════════
        # 1. INICIALIZAR MODELOS
        # ══════════════════════════════════════════════════════════
        
        print("📝 Cargando modelo de embeddings textuales (OpenAI)...")
        # Leer API key desde configuración del proyecto
        api_key = config.openai.openai_key if hasattr(config, 'openai') else None
        if not api_key:
            # Fallback a variable de entorno
            api_key = os.getenv("OPENAI_API_KEY")
        
        if not api_key:
            raise ValueError("No se encontró openai_key en .env ni OPENAI_API_KEY en variables de entorno")
        
        self.openai_client = OpenAI(api_key=api_key)
        self.openai_model = openai_model
        print(f"   ✅ {openai_model}")
        
        print("\n🖼️  Cargando modelo de embeddings visuales (CLIP)...")
        self.clip_model = SentenceTransformer(clip_model)
        print(f"   ✅ {clip_model}")
        
        # ══════════════════════════════════════════════════════════
        # 2. CONECTAR A COLECCIONES CHROMA
        # ══════════════════════════════════════════════════════════
        
        print(f"\n📦 Conectando a Chroma DB: {chroma_path}")
        chroma_client = chromadb.PersistentClient(path=chroma_path)
        
        try:
            self.text_collection = chroma_client.get_collection(text_collection_name)
            print(f"   ✅ Colección textual: {text_collection_name} ({self.text_collection.count()} docs)")
        except Exception as e:
            print(f"   ⚠️  Colección textual no encontrada: {e}")
            self.text_collection = None
        
        try:
            self.visual_collection = chroma_client.get_collection(visual_collection_name)
            print(f"   ✅ Colección visual: {visual_collection_name} ({self.visual_collection.count()} docs)")
        except Exception as e:
            print(f"   ⚠️  Colección visual no encontrada: {e}")
            self.visual_collection = None
        
        # ══════════════════════════════════════════════════════════
        # 3. INICIALIZAR MÓDULOS DE RETRIEVAL AVANZADO ⭐
        # ══════════════════════════════════════════════════════════
        
        print(f"\n⚡ Inicializando componentes de retrieval avanzado...")
        
        # Context expansion
        self.context_expander = None
        if config.dual.use_context_expansion:
            try:
                self.context_expander = ContextExpander(
                    window_size=config.dual.context_window_size
                )
                chunks_base_path = str(current_dir / config.paths.chunks_data_path)
                self._load_context_expander_chunks(chunks_base_path)
                print(
                    f"   ✅ Context Expansion (window={config.dual.context_window_size}, "
                    f"{len(self.context_expander._chunk_cache)} chunks cacheados)"
                )
            except Exception as e:
                print(f"   ⚠️  Context Expansion no disponible: {e}")
        
        # Cross-encoder reranking
        self.reranker = None
        self.rerank_top_k = config.dual.rerank_top_k
        if config.dual.use_reranking:
            try:
                self.reranker = CrossEncoderReranker(
                    model_name=config.dual.reranker_model
                )
                print(f"   ✅ Cross-Encoder Reranking (top_k={config.dual.rerank_top_k})")
            except Exception as e:
                print(f"   ⚠️  Reranking no disponible: {e}")
        
        # BM25 sparse retrieval
        self.bm25_index = None
        if config.dual.use_bm25 and self.text_collection:
            try:
                print("   🔄 Construyendo índice BM25...")
                self.bm25_index = BM25Index()
                # Cargar corpus desde colección textual
                results = self.text_collection.get(include=['documents', 'metadatas'])
                if results and results['documents']:
                    self.bm25_index.add_documents(results['documents'], results['metadatas'])
                    print(f"   ✅ BM25 Index ({len(results['documents'])} documentos)")
                else:
                    self.bm25_index = None
            except Exception as e:
                print(f"   ⚠️  BM25 no disponible: {e}")
        
        # Metadata filter builder
        self.metadata_filter = MetadataFilter()
        print(f"   ✅ Metadata Filter")
        
        print(f"\n{'='*70}")
        print(f"✅ SISTEMA LISTO PARA BÚSQUEDAS")
        print(f"{'='*70}\n")

    def search(
        self,
        query: str,
        top_k: int = 5,
        text_weight: float = 1.0,
        visual_weight: float = 1.2,
        filter_type: str = None,
        filter_metadata: dict = None
    ) -> List[SearchResult]:
        """
        Realiza búsqueda híbrida avanzada con retrieval optimizado.
        
        Args:
            query: Consulta del usuario
            top_k: Número de resultados a retornar
            text_weight: Peso para resultados textuales (1.0 = normal)
            visual_weight: Peso para resultados visuales (>1.0 = priorizar imágenes)
            filter_type: Filtrar por tipo ('text', 'table', 'image', None=todos)
            filter_metadata: Filtros adicionales (e.g., {'chapter': 'Introducción'})
        
        Returns:
            Lista de SearchResult ordenados por relevancia
        """
        print(f"\n🔍 BÚSQUEDA: \"{query}\"")
        print(f"{'─'*70}\n")
        
        # ══════════════════════════════════════════════════════════
        # 1. CONSTRUIR FILTROS DE METADATA ⭐
        # ══════════════════════════════════════════════════════════
        
        chroma_filter = None
        if filter_type or filter_metadata:
            print("→ Construyendo filtros de metadata...")
            filters = []
            
            if filter_type:
                filters.append({"content_type": filter_type})
            
            if filter_metadata:
                filters.append(filter_metadata)
            
            # Combinar filtros
            combined_filter = {}
            for f in filters:
                combined_filter.update(f)
            
            chroma_filter = self.metadata_filter.build_chroma_filter(combined_filter)
            print(f"   ✓ Filtros aplicados: {chroma_filter}")
        
        # ══════════════════════════════════════════════════════════
        # 3. BUSCAR EN ÍNDICE TEXTUAL (Dense Retrieval)
        # ══════════════════════════════════════════════════════════
        
        text_results = {}
        if self.text_collection:
            print("→ Buscando en índice textual (dense embeddings)...")
            
            # Generar embedding de la query con OpenAI
            query_emb = self.openai_client.embeddings.create(
                model=self.openai_model,
                input=query
            ).data[0].embedding
            
            # Buscar en Chroma con filtros
            results = self.text_collection.query(
                query_embeddings=[query_emb],
                n_results=min(20, top_k * 4),
                include=["documents", "metadatas", "distances"],
                where=chroma_filter
            )
            
            # Procesar resultados
            for rank, (doc_id, distance, metadata, text) in enumerate(
                zip(
                    results['ids'][0],
                    results['distances'][0],
                    results['metadatas'][0],
                    results['documents'][0]
                ),
                start=1
            ):
                text_results[doc_id] = SearchResult(
                    doc_id=doc_id,
                    score=self._rrf_score(rank) * text_weight,
                    content=text,
                    content_type=metadata.get('content_type', 'text'),
                    page_num=self._clean_metadata(metadata.get('page_num'), '?'),
                    file_name=self._get_document_name(metadata) or 'Desconocido',
                    source='text',
                    distance=distance,
                    media_path=metadata.get('media_path'),
                    chapter=self._clean_metadata(metadata.get('chapter'), ''),
                    section=self._clean_metadata(metadata.get('document_section'), ''),
                    hierarchy_path=self._clean_metadata(metadata.get('hierarchy_path'), ''),
                    prev_chunk_id=metadata.get('prev_chunk_id', ''),
                    next_chunk_id=metadata.get('next_chunk_id', '')
                )

            print(f"   ✓ {len(text_results)} resultados textuales")
        
        # ══════════════════════════════════════════════════════════
        # 4. BM25 SPARSE RETRIEVAL ⭐
        # ══════════════════════════════════════════════════════════
        
        bm25_results = {}
        if self.bm25_index and config.dual.use_bm25:
            print("→ Buscando con BM25 (sparse retrieval)...")
            sparse_results = self.bm25_index.search(query, top_k=top_k * 2)
            
            for rank, result in enumerate(sparse_results, start=1):
                metadata = result['metadata']
                score = result['bm25_score']
                text = result['text']
                
                # El metadata debe incluir el doc_id (debe estar en metadata o generarlo)
                doc_id = metadata.get('id', f"bm25_{rank}")
                
                # Aplicar filtros manualmente (BM25 no soporta filtros nativos)
                if filter_type and metadata.get('content_type') != filter_type:
                    continue
                
                bm25_results[doc_id] = SearchResult(
                    doc_id=doc_id,
                    score=self._rrf_score(rank) * config.dual.bm25_weight,
                    content=text,
                    content_type=metadata.get('content_type', 'text'),
                    page_num=self._clean_metadata(metadata.get('page_num'), '?'),
                    file_name=self._get_document_name(metadata) or 'Desconocido',
                    source='bm25',
                    distance=0.0,  # BM25 no usa distancia
                    bm25_score=score,
                    media_path=metadata.get('media_path'),
                    chapter=self._clean_metadata(metadata.get('chapter'), ''),
                    section=self._clean_metadata(metadata.get('document_section'), ''),
                    hierarchy_path=self._clean_metadata(metadata.get('hierarchy_path'), ''),
                    prev_chunk_id=metadata.get('prev_chunk_id', ''),
                    next_chunk_id=metadata.get('next_chunk_id', '')
                )

            print(f"   ✓ {len(bm25_results)} resultados BM25")
        
        # ══════════════════════════════════════════════════════════
        # 5. BUSCAR EN ÍNDICE VISUAL
        # ══════════════════════════════════════════════════════════
        
        visual_results = {}
        if self.visual_collection:
            print("→ Buscando en índice visual...")
            
            # Generar embedding de la query con CLIP
            query_visual_emb = self.clip_model.encode(
                query,
                convert_to_tensor=False,
                show_progress_bar=False
            )
            
            # Buscar en Chroma
            results = self.visual_collection.query(
                query_embeddings=[query_visual_emb.tolist()],
                n_results=min(10, top_k * 2),
                include=["documents", "metadatas", "distances"],
                where=chroma_filter
            )
            
            # Procesar resultados
            for rank, (doc_id, distance, metadata, text) in enumerate(
                zip(
                    results['ids'][0],
                    results['distances'][0],
                    results['metadatas'][0],
                    results['documents'][0]
                ),
                start=1
            ):
                visual_results[doc_id] = SearchResult(
                    doc_id=doc_id,
                    score=self._rrf_score(rank) * visual_weight,
                    content=text,
                    content_type=metadata.get('content_type', 'image'),
                    page_num=self._clean_metadata(metadata.get('page_num'), '?'),
                    file_name=self._get_document_name(metadata) or 'Desconocido',
                    source='visual',
                    distance=distance,
                    image_path=metadata.get('image_path'),
                    media_path=metadata.get('media_path'),
                    prev_chunk_id=metadata.get('prev_chunk_id', ''),
                    next_chunk_id=metadata.get('next_chunk_id', '')
                )
            
            print(f"   ✓ {len(visual_results)} resultados visuales")
        
        # ══════════════════════════════════════════════════════════
        # 6. FUSIÓN DE RESULTADOS (RRF) ⭐
        # ══════════════════════════════════════════════════════════
        
        print("→ Fusionando resultados (text + BM25 + visual)...")
        
        # Combinar scores
        all_results = {}
        
        # Añadir resultados textuales
        for doc_id, result in text_results.items():
            all_results[doc_id] = result
        
        # Añadir/combinar resultados BM25
        for doc_id, result in bm25_results.items():
            if doc_id in all_results:
                # Fusionar scores
                all_results[doc_id].score += result.score
                all_results[doc_id].bm25_score = result.bm25_score
                all_results[doc_id].source = 'text+bm25'
            else:
                all_results[doc_id] = result
        
        # Añadir/combinar resultados visuales
        for doc_id, result in visual_results.items():
            # Quitar sufijo _visual para matching
            base_doc_id = doc_id.replace("_visual", "").replace("_desc", "")
            
            # Buscar versión textual del mismo documento
            matching_text = None
            for text_id in all_results:
                if base_doc_id in text_id:
                    matching_text = text_id
                    break
            
            if matching_text and matching_text in all_results:
                # Mismo documento en ambos índices: combinar scores
                all_results[matching_text].score += result.score
                all_results[matching_text].source = 'both'
                all_results[matching_text].image_path = result.image_path
                if result.media_path:
                    all_results[matching_text].media_path = result.media_path
            else:
                # Solo en índice visual
                all_results[doc_id] = result
        
        # Ordenar por score combinado
        ranked_results = sorted(
            all_results.values(),
            key=lambda x: x.score,
            reverse=True
        )
        
        # Deduplicar resultados por contenido similar ⭐
        deduplicated = []
        seen_content = set()
        
        for result in ranked_results:
            # Hash de los primeros 150 caracteres para deduplicación
            content_hash = result.content[:150].strip() if result.content else ""
            
            if content_hash and content_hash not in seen_content:
                deduplicated.append(result)
                seen_content.add(content_hash)
            elif not content_hash:
                # Incluir igualmente si no tiene contenido
                deduplicated.append(result)
        
        ranked_results = deduplicated
        
        # Deduplicar resultados por contenido similar ⭐
        deduplicated = []
        seen_content = set()
        
        for result in ranked_results:
            # Hash de los primeros 150 caracteres para deduplicación
            content_hash = result.content[:150].strip()
            
            if content_hash not in seen_content:
                deduplicated.append(result)
                seen_content.add(content_hash)
        
        ranked_results = deduplicated
        
        # ══════════════════════════════════════════════════════════
        # 7. CROSS-ENCODER RERANKING ⭐
        # ══════════════════════════════════════════════════════════
        
        if self.reranker and len(ranked_results) > 1:
            print(f"→ Reranking con cross-encoder (top {len(ranked_results)})...")
            reranked = self.reranker.rerank(query, ranked_results, top_k=self.rerank_top_k)
            final_results = reranked[:top_k]
        else:
            final_results = ranked_results[:top_k]
        
        # ══════════════════════════════════════════════════════════
        # 8. CONTEXT EXPANSION ⭐
        # ══════════════════════════════════════════════════════════
        
        if self.context_expander:
            print("→ Expandiendo resultados con contexto...")
            expanded_results = []
            for result in final_results:
                metadata = {
                    'prev_chunk_id': result.prev_chunk_id,
                    'next_chunk_id': result.next_chunk_id
                }
                try:
                    expanded = self.context_expander.expand_context(result.doc_id, metadata)
                    result.expanded_context = expanded.combined_text
                except Exception as e:
                    # Si falla, usar contenido original
                    result.expanded_context = result.content
                expanded_results.append(result)
            final_results = expanded_results
        
        print(f"   ✓ {len(final_results)} resultados finales (top {top_k})")
        print()
        
        return final_results

    @staticmethod
    def _rrf_score(rank: int, k: int = 60) -> float:
        """
        Calcula score usando Reciprocal Rank Fusion.
        
        Args:
            rank: Posición del resultado (1, 2, 3, ...)
            k: Constante de suavizado (típicamente 60)
        
        Returns:
            Score RRF
        """
        return 1.0 / (k + rank)

    def print_results(self, results: List[SearchResult]):
        """Imprime resultados formateados con metadata enriquecida."""
        print(f"\n{'='*70}")
        print(f"📊 RESULTADOS ({len(results)} encontrados)")
        print(f"{'='*70}\n")
        
        for i, result in enumerate(results, 1):
            # Emoji según tipo
            emoji = {
                'text': '📝',
                'table': '📊',
                'image': '🖼️',
                'superchunk': '🔗',
                'diagram_visual': '🖼️',
                'diagram_text': '🔍',
                'diagram_description': '📐'
            }.get(result.content_type, '📄')
            
            # Indicador de fuente
            source_badge = {
                'text': '[TEXT]',
                'visual': '[VISUAL]',
                'both': '[TEXT+VISUAL]',
                'bm25': '[BM25]',
                'text+bm25': '[TEXT+BM25]'
            }.get(result.source, '[?]')
            
            print(f"{i}. {emoji} Score: {result.score:.3f} | {source_badge}")
            print(f"   Tipo: {result.content_type}")
            
            # Mostrar documento solo si no es "Desconocido"
            if result.file_name != "Desconocido":
                print(f"   📄 Documento: {result.file_name}")
            
            print(f"   📊 Página: {result.page_num}")
            
            # Metadata jerárquica ⭐ (solo mostrar partes no vacías)
            hierarchy_parts = []
            if result.chapter and result.chapter not in ['None', 'Desconocido', '']:
                hierarchy_parts.append(result.chapter)
            if result.section and result.section not in ['None', 'Desconocido', '']:
                hierarchy_parts.append(result.section)
            
            if hierarchy_parts:
                hierarchy = ' > '.join(hierarchy_parts)
                print(f"   📍 Jerarquía: {hierarchy}")
            
            # Scores adicionales ⭐
            if result.rerank_score > 0:
                print(f"   🎯 Rerank Score: {result.rerank_score:.3f}")
            if result.bm25_score > 0:
                print(f"   🔍 BM25 Score: {result.bm25_score:.3f}")
            
            print(f"   Distance: {result.distance:.3f}")
            
            if result.image_path:
                print(f"   🖼️  Imagen: {result.image_path}")

            if result.media_path:
                print(f"   💾 Media (tabla/imagen canónica): {result.media_path}")

            # Mostrar preview del contenido (truncado inteligente)
            if result.content:
                # Normalizar saltos de línea y espacios múltiples
                content_clean = ' '.join(result.content.split())
                
                # Truncar en espacio o punto más cercano para no cortar palabras
                content_preview = content_clean[:400]
                if len(content_clean) > 400:
                    # Buscar último espacio o punto
                    last_space = content_preview.rfind(' ')
                    last_period = content_preview.rfind('.')
                    cut_point = max(last_space, last_period)
                    if cut_point > 300:  # Solo si está en rango razonable
                        content_preview = content_preview[:cut_point]
                    content_preview += "..."
                
                # Mostrar en múltiples líneas si es muy largo
                if len(content_preview) > 150:
                    # Dividir en líneas de ~100 caracteres
                    lines = []
                    words = content_preview.split()
                    current_line = ""
                    for word in words:
                        if len(current_line) + len(word) + 1 <= 100:
                            current_line += (" " if current_line else "") + word
                        else:
                            if current_line:
                                lines.append(current_line)
                            current_line = word
                    if current_line:
                        lines.append(current_line)
                    
                    print(f"   📝 Contenido:")
                    for line in lines[:4]:  # Máximo 4 líneas
                        print(f"      {line}")
                else:
                    print(f"   📝 Contenido: {content_preview}")
            else:
                print(f"   📝 Contenido: [Sin contenido]")
            
            # Contexto expandido ⭐ (solo si es diferente del principal)
            if result.expanded_context and result.expanded_context != result.content:
                # Verificar si realmente tiene contexto adicional
                if "[CONTEXTO PREVIO]" in result.expanded_context or "[CONTEXTO SIGUIENTE]" in result.expanded_context:
                    context_lines = result.expanded_context.split('\n')
                    context_summary = ' '.join(context_lines[:3])[:200]
                    print(f"   📖 Contexto expandido: {context_summary}...")
            
            print()


def main():
    """Función principal para demo de búsqueda."""
    
    # ══════════════════════════════════════════════════════════
    # CONFIGURACIÓN
    # ══════════════════════════════════════════════════════════
    
    CHROMA_PATH = config.paths.index_path if hasattr(config, 'paths') else "./data/chroma"
    TEXT_COLLECTION = config.index.index_name if hasattr(config, 'index') else "multimodal_documents"
    VISUAL_COLLECTION = config.dual.visual_index_name if hasattr(config, 'dual') else "visual_docs"
    
    # ══════════════════════════════════════════════════════════
    # INICIALIZAR MOTOR DE BÚSQUEDA
    # ══════════════════════════════════════════════════════════
    
    search_engine = HybridMultimodalSearch(
        chroma_path=CHROMA_PATH,
        text_collection_name=TEXT_COLLECTION,
        visual_collection_name=VISUAL_COLLECTION
    )
    
    # ══════════════════════════════════════════════════════════
    # BÚSQUEDA INTERACTIVA
    # ══════════════════════════════════════════════════════════
    
    # Query desde argumentos o interactivo
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        queries = [query]
    else:
        # Ejemplos de queries
        queries = [
            "diagrama de conexiones del motor",
            "especificaciones técnicas del variador",
            "tabla de parámetros eléctricos",
            "instalación del equipo",
        ]
    
    for query in queries:
        # Realizar búsqueda
        results = search_engine.search(
            query=query,
            top_k=5,
            text_weight=1.0,      # Peso normal para texto
            visual_weight=1.2,    # Priorizar ligeramente las imágenes
            filter_type=None      # None = todos, 'image' = solo imágenes, etc.
        )
        
        # Mostrar resultados
        search_engine.print_results(results)
        
        if len(queries) > 1:
            print("\n" + "="*70 + "\n")
            input("Presiona Enter para continuar con la siguiente búsqueda...")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Búsqueda cancelada por el usuario")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
