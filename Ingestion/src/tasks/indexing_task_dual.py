"""
Sistema Dual de Indexado Multimodal
====================================

Extensión del indexado tradicional que añade:
1. Índice TEXTUAL (OpenAI embeddings) - Para texto y tablas
2. Índice VISUAL (CLIP embeddings) - Para imágenes

Ventajas:
- Búsqueda semántica REAL de imágenes por contenido visual
- No depende solo de descripciones textuales
- Retrieval híbrido que combina ambos índices
"""

import os, json, math, re
from typing import List, Dict, Any, Tuple, Optional
from PIL import Image

import chromadb
from sentence_transformers import SentenceTransformer

from tasks.indexing_task_multimodal import AutomaticIndexer
from task import TaskReturnData
from logger import Logger
from task_utils.multimodal_storage import MultimodalStorage
from task_utils.multimodal_adapter import ChunkToMultimodalAdapter

logger = Logger.get_logger(__name__)
current_dir = os.path.dirname(os.path.abspath(__file__))

# Cache de modelos CLIP por nombre. Cargar un SentenceTransformer implica leer
# pesos del modelo (disco/red) y es costoso; sin este cache se recargaría por
# cada PDF procesado en el batch de main_multimodal.py, ya que DualIndexer()
# se instancia una vez por documento.
_CLIP_MODEL_CACHE: Dict[str, SentenceTransformer] = {}


def _get_clip_model(model_name: str) -> SentenceTransformer:
    if model_name not in _CLIP_MODEL_CACHE:
        _CLIP_MODEL_CACHE[model_name] = SentenceTransformer(model_name)
    return _CLIP_MODEL_CACHE[model_name]


class DualIndexer(AutomaticIndexer):
    """
    Indexador dual que crea dos colecciones Chroma:
    1. text_docs: Embeddings textuales (OpenAI) para texto y tablas
    2. visual_docs: Embeddings visuales (CLIP) para imágenes
    """

    name = "DualIndexerCreation"

    def __init__(self):
        super().__init__()
        self.clip_model = None
        self.visual_collection = None
        self.storage = MultimodalStorage(
            base_path=os.path.join(current_dir, "..", "..", "data")
        )

    def execute(self):
        try:
            collections = self.execute_local()
            text_stats = collections["stats"]["text"]
            visual_stats = collections["stats"]["visual"]
            return TaskReturnData(payload={
                "text_indexer": collections["text"].name,
                "visual_indexer": collections["visual"].name,
                "total_text": collections["text"].count(),
                "total_visual": collections["visual"].count(),
                "text_chunks": text_stats["text"],
                "tables_indexed": text_stats["table"],
                "image_descriptions_indexed": text_stats["image_desc"],
                "images_indexed": visual_stats["images"],
            })
        except Exception as ex:
            logger.exception("DualIndexer failed")
            return TaskReturnData(error=str(ex))

    def execute_local(self) -> Dict[str, Any]:
        """
        Ejecuta indexado dual en dos colecciones separadas.
        """
        # Ruta base donde están los JSON de embeddings
        embeddings_base = os.path.join(
            current_dir, "..", "..",
            self._task_settings["embeddings_data_path"],
            (self._input_data["file_name"]).split(".")[0]
        )

        if os.path.isabs(self._input_data["embeddings"]) or os.path.exists(self._input_data["embeddings"]):
            embeddings_base = self._input_data["embeddings"]

        # ═══════════════════════════════════════════════════════════
        # 1. INICIALIZAR MODELO CLIP (para embeddings visuales)
        # ═══════════════════════════════════════════════════════════
        
        clip_model_name = self._task_settings.get("clip_model", "clip-ViT-B-32")
        cached = clip_model_name in _CLIP_MODEL_CACHE
        print(f"\n🔧 {'Reutilizando' if cached else 'Inicializando'} modelo CLIP para embeddings visuales...")
        self.clip_model = _get_clip_model(clip_model_name)
        print(f"✅ Modelo CLIP listo: {clip_model_name}")

        # ═══════════════════════════════════════════════════════════
        # 2. CREAR COLECCIONES (texto y visual)
        # ═══════════════════════════════════════════════════════════
        
        print("\n📦 Creando colecciones Chroma...")
        
        # Colección textual (usa el método heredado)
        text_collection = self._create_or_load_collection(
            self._task_settings.get("index_name", "text_docs"),
            self._task_settings["index_path"]
        )
        
        # Colección visual (nueva)
        visual_collection = self._create_or_load_collection(
            self._task_settings.get("visual_index_name", "visual_docs"),
            self._task_settings["index_path"]
        )
        
        self.visual_collection = visual_collection
        print(f"✅ Colección textual: {text_collection.name}")
        print(f"✅ Colección visual: {visual_collection.name}")

        # ═══════════════════════════════════════════════════════════
        # 3. INDEXADO DUAL
        # ═══════════════════════════════════════════════════════════
        
        print(f"\n🔄 Procesando documentos desde: {embeddings_base}\n")
        
        text_stats, visual_stats = self._index_documents_dual(
            text_collection,
            visual_collection,
            embeddings_base
        )
        
        # ═══════════════════════════════════════════════════════════
        # 4. REPORTE FINAL
        # ═══════════════════════════════════════════════════════════
        
        print(f"\n{'='*70}")
        print(f"✅ INDEXADO DUAL COMPLETADO")
        print(f"{'='*70}")
        print(f"📝 Índice Textual ({text_collection.name}):")
        print(f"   • Total documentos: {text_collection.count()}")
        print(f"   • Textos: {text_stats['text']}")
        print(f"   • Tablas: {text_stats['table']}")
        print(f"   • Descripciones de imágenes: {text_stats['image_desc']}")
        print(f"   • Super-chunks: {text_stats['superchunk']}")
        print(f"   • Preguntas sintéticas: {text_stats['synthetic_question']}")
        print(f"\n🖼️  Índice Visual ({visual_collection.name}):")
        print(f"   • Total documentos: {visual_collection.count()}")
        print(f"   • Imágenes indexadas: {visual_stats['images']}")
        print(f"{'='*70}\n")

        return {
            "text": text_collection,
            "visual": visual_collection,
            "stats": {
                "text": text_stats,
                "visual": visual_stats
            }
        }

    def _index_documents_dual(
        self,
        text_collection,
        visual_collection,
        base_path: str,
        batch_size: int = 512
    ) -> Tuple[Dict[str, int], Dict[str, int]]:
        """
        Indexa documentos en ambas colecciones según tipo de contenido.
        
        Returns:
            (text_stats, visual_stats)
        """
        # Contadores
        text_stats = {"text": 0, "table": 0, "image_desc": 0,
                      "synthetic_question": 0, "superchunk": 0}
        visual_stats = {"images": 0}
        table_idx = 0
        image_idx = 0
        document_id = None
        
        # Buffers para batching
        text_ids: List[str] = []
        text_embeddings: List[List[float]] = []
        text_documents: List[str] = []
        text_metadatas: List[Dict[str, Any]] = []
        
        visual_ids: List[str] = []
        visual_embeddings: List[List[float]] = []
        visual_documents: List[str] = []
        visual_metadatas: List[Dict[str, Any]] = []

        if not os.path.exists(base_path):
            raise FileNotFoundError(f"No existe la carpeta de embeddings: {base_path}")

        # Determinar ruta raíz del proyecto para rutas de imágenes
        project_root = os.path.join(current_dir, "..", "..")

        # Recorrer archivos de embeddings
        for sub in sorted(os.listdir(base_path), key=self._extract_number):
            sub_path = os.path.join(base_path, sub)
            if not os.path.isdir(sub_path):
                continue
            
            for f in sorted(os.listdir(sub_path), key=self._extract_chunk_number):
                if not f.endswith(".json"):
                    continue
                
                fp = os.path.join(sub_path, f)
                try:
                    with open(fp, "r", encoding="utf-8") as h:
                        data = json.load(h)

                    content_type = (data.get("content_type") or "").lower()
                    doc_id = f"{data.get('file_name','')}_{data.get('page_num','')}_{data.get('chunk_id','')}"
                    if document_id is None:
                        document_id = str(data.get("file_name", "")).split(".")[0] or "document"

                    chunk_id_value = self._meta_str(data.get("chunk_id"))
                    meta = {
                        "file_name": self._meta_str(data.get("file_name")),
                        "page_num": self._meta_str(data.get("page_num")),
                        "chunk_id": chunk_id_value,
                        "page_metadata": self._meta_str(data.get("page_metadata")),
                        "content_type": content_type,
                        # Metadata jerárquica ⭐ (ChromaDB solo acepta str, int, float, bool)
                        "document_section": self._meta_str(data.get("document_section")),
                        "chapter": self._meta_str(data.get("document_chapter")),
                        "hierarchy_path": self._meta_str(data.get("hierarchy_path")),
                        "section_number": self._meta_str(data.get("section_number")),
                        # Metadata para contexto expandido ⭐
                        "prev_chunk_id": self._meta_str(data.get("prev_chunk_id")),
                        "next_chunk_id": self._meta_str(data.get("next_chunk_id")),
                        # Multi-vector: los chunks-pregunta sintéticos apuntan al
                        # chunk padre; un chunk de contenido es su propio padre. El
                        # lado de consulta usa parent_chunk_id para colapsar varios
                        # vectores del mismo contenido en un único resultado.
                        "parent_chunk_id": self._meta_str(
                            data.get("parent_chunk_id"), default=chunk_id_value
                        ),
                        # Contexto generado por LLM (Contextual Retrieval)
                        "context_summary": self._meta_str(data.get("context_summary"))[:1000],
                        # La pregunta que originó este vector (vacío si no aplica)
                        "question": self._meta_str(data.get("question"))[:500],
                    }

                    # ═══════════════════════════════════════════════════
                    # TEXTO, TABLAS, SUPER-CHUNKS Y FACETAS DE DIAGRAMA
                    # (diagram_text/diagram_description: chunks generados por
                    # ElectricalDiagramProcessor, ya son texto plano) → ÍNDICE TEXTUAL
                    # ═══════════════════════════════════════════════════

                    if content_type in ["text", "table", "superchunk", "diagram_text",
                                        "diagram_description", "synthetic_question", ""]:
                        vec = self._extract_vector(data)
                        if vec is None:
                            continue

                        text = self._document_text_from_chunk(data)

                        if content_type == "table":
                            page_num = self._page_num_for_storage(data)
                            searchable_text, media_path = self._store_table_media(
                                data, document_id, page_num, table_idx
                            )
                            table_idx += 1
                            if media_path:
                                meta["media_path"] = media_path
                            if searchable_text:
                                meta["searchable_text"] = searchable_text[:2000]

                        text_ids.append(doc_id)
                        text_embeddings.append(vec)
                        text_documents.append(text)
                        text_metadatas.append(meta)

                        # Contadores separados: mezclar las preguntas sintéticas con
                        # el texto real hacía que el reporte dijera "Textos: 2366"
                        # cuando solo 207 eran texto del documento.
                        if content_type == "table":
                            text_stats["table"] += 1
                        elif content_type == "synthetic_question":
                            text_stats["synthetic_question"] += 1
                        elif content_type == "superchunk":
                            text_stats["superchunk"] += 1
                        else:
                            text_stats["text"] += 1

                        # Batch upsert (idempotente: reprocesar el mismo doc actualiza en vez de fallar/duplicar)
                        if len(text_ids) >= batch_size:
                            text_collection.upsert(
                                ids=text_ids,
                                embeddings=text_embeddings,
                                documents=text_documents,
                                metadatas=text_metadatas
                            )
                            text_ids, text_embeddings, text_documents, text_metadatas = [], [], [], []

                    # ═══════════════════════════════════════════════════
                    # IMÁGENES → DOBLE INDEXADO (textual + visual)
                    # ═══════════════════════════════════════════════════
                    
                    elif content_type in ("image", "diagram_visual"):
                        # diagram_visual: faceta visual generada por ElectricalDiagramProcessor,
                        # copia el mismo original_chunk (bbox/image_path/notes) que un "image" normal.
                        # Extraer path de la imagen del chunk original (usado por A y B)
                        image_path = self._extract_image_path(data)
                        media_path = None
                        if image_path and os.path.exists(image_path):
                            page_num = self._page_num_for_storage(data)
                            media_path = self._store_image_media(
                                image_path, document_id, page_num, image_idx
                            )
                            image_idx += 1

                        # ───────────────────────────────────────────────
                        # A) ÍNDICE TEXTUAL (descripción con OpenAI embedding)
                        # ───────────────────────────────────────────────

                        vec = self._extract_vector(data)
                        if vec is not None:
                            text = self._document_text_from_chunk(data)

                            image_meta = {**meta, "subtype": "image_description"}
                            if media_path:
                                image_meta["media_path"] = media_path

                            text_ids.append(doc_id + "_desc")
                            text_embeddings.append(vec)
                            text_documents.append(text)
                            text_metadatas.append(image_meta)

                            text_stats["image_desc"] += 1

                            if len(text_ids) >= batch_size:
                                text_collection.upsert(
                                    ids=text_ids,
                                    embeddings=text_embeddings,
                                    documents=text_documents,
                                    metadatas=text_metadatas
                                )
                                text_ids, text_embeddings, text_documents, text_metadatas = [], [], [], []

                        # ───────────────────────────────────────────────
                        # B) ÍNDICE VISUAL (embedding CLIP de la imagen)
                        # ───────────────────────────────────────────────

                        if image_path and os.path.exists(image_path):
                            try:
                                # Cargar imagen
                                img = Image.open(image_path).convert("RGB")

                                # Generar embedding visual con CLIP
                                visual_emb = self.clip_model.encode(
                                    img,
                                    convert_to_tensor=False,
                                    show_progress_bar=False
                                )

                                # Añadir a buffer visual
                                visual_meta = {**meta, "image_path": image_path}
                                if media_path:
                                    visual_meta["media_path"] = media_path

                                visual_ids.append(doc_id + "_visual")
                                visual_embeddings.append(visual_emb.tolist())
                                visual_documents.append(self._document_text_from_chunk(data))
                                visual_metadatas.append(visual_meta)

                                visual_stats["images"] += 1

                                # Batch upsert
                                if len(visual_ids) >= batch_size:
                                    visual_collection.upsert(
                                        ids=visual_ids,
                                        embeddings=visual_embeddings,
                                        documents=visual_documents,
                                        metadatas=visual_metadatas
                                    )
                                    visual_ids, visual_embeddings, visual_documents, visual_metadatas = [], [], [], []

                            except Exception as e:
                                logger.warning(f"Error procesando imagen {image_path}: {e}")
                        else:
                            if image_path:
                                logger.warning(f"Imagen no encontrada: {image_path}")

                except Exception as e:
                    logger.warning(f"Error procesando {fp}: {e}")

        # Flush final de buffers
        if text_ids:
            text_collection.upsert(
                ids=text_ids,
                embeddings=text_embeddings,
                documents=text_documents,
                metadatas=text_metadatas
            )

        if visual_ids:
            visual_collection.upsert(
                ids=visual_ids,
                embeddings=visual_embeddings,
                documents=visual_documents,
                metadatas=visual_metadatas
            )

        return text_stats, visual_stats

    @staticmethod
    def _meta_str(value: Any, default: str = "") -> str:
        """
        Normaliza un valor a string apto para metadata de Chroma.

        Trata como ausentes None, NaN y la cadena "nan": los chunks vienen de un
        DataFrame de pandas, que rellena con NaN los campos que ese chunk no
        tiene, y `NaN` es truthy (así que un `valor or default` se quedaba con el
        NaN y guardaba la cadena "nan" en el índice).
        """
        if value is None:
            return default
        if isinstance(value, float) and math.isnan(value):
            return default
        text = str(value).strip()
        if text.lower() == "nan" or not text:
            return default
        return text

    def _page_num_for_storage(self, data: Dict[str, Any]) -> int:
        """Convierte page_num (puede ser '1' o rango '1-3' de super-chunks) a int para MultimodalStorage."""
        page = str(data.get("page_num", "1"))
        if "-" in page:
            page = page.split("-")[0]
        try:
            return int(page)
        except (ValueError, TypeError):
            return 1

    def _store_table_media(
        self, data: Dict[str, Any], document_id: str, page_num: int, table_idx: int
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Guarda una copia canónica de la tabla (markdown + json + searchable_text) en
        el storage multimodal compartido y devuelve (searchable_text, media_path).
        """
        original = data.get("original_chunk", "")
        try:
            j = json.loads(original) if isinstance(original, str) else original
        except Exception:
            j = None

        if not isinstance(j, dict):
            return None, None

        markdown = j.get("table_markdown") or ""
        table_json = j.get("table_json") or {}
        if not markdown and not table_json:
            return None, None

        searchable_text = ChunkToMultimodalAdapter._generate_searchable_from_table({
            "markdown": markdown,
            "json": table_json if isinstance(table_json, dict) else {}
        })

        try:
            media_path = self.storage.save_table(
                table_data={
                    "markdown": markdown,
                    "json": table_json,
                    "searchable_text": searchable_text,
                    "bbox": j.get("bbox"),
                },
                document_id=document_id,
                page=page_num,
                table_idx=table_idx
            )
        except Exception as e:
            logger.warning(f"No se pudo guardar tabla en storage multimodal: {e}")
            media_path = None

        return searchable_text, media_path

    def _store_image_media(
        self, image_path: str, document_id: str, page_num: int, image_idx: int
    ) -> Optional[str]:
        """Guarda una copia canónica de la imagen en el storage multimodal compartido."""
        try:
            with open(image_path, "rb") as f:
                image_bytes = f.read()
            return self.storage.save_image(
                image_bytes=image_bytes,
                document_id=document_id,
                page=page_num,
                img_idx=image_idx
            )
        except Exception as e:
            logger.warning(f"No se pudo guardar imagen en storage multimodal: {e}")
            return None

    def _extract_image_path(self, data: Dict[str, Any]) -> Optional[str]:
        """
        Extrae la ruta de la imagen del chunk.
        Busca en original_chunk JSON.
        """
        original = data.get("original_chunk", "")
        
        try:
            if isinstance(original, str):
                j = json.loads(original)
            else:
                j = original
            
            if isinstance(j, dict):
                # Buscar image_path en el JSON
                image_path = j.get("image_path")
                
                if image_path:
                    # Si es ruta relativa, convertir a absoluta
                    if not os.path.isabs(image_path):
                        project_root = os.path.join(current_dir, "..", "..")
                        image_path = os.path.join(project_root, image_path)
                    
                    return image_path
        except Exception as e:
            logger.debug(f"No se pudo extraer image_path: {e}")
        
        return None
