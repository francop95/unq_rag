import os, json, re
from typing import List, Dict, Any, Tuple, Optional

import numpy as np
import chromadb

from task import (Task, TaskReturnData)
from logger import Logger
from task_utils.validators.task_validators import TaskSettingPresenceValidator

logger = Logger.get_logger(__name__)
current_dir = os.path.dirname(os.path.abspath(__file__))


class AutomaticIndexer(Task):
    """
    Indexa los JSON de embeddings en una colección Chroma persistente.
    - Espera archivos con campos: file_name, page_num, chunk_id, original_chunk, text_vector
    - text_vector: lista de floats (formato actual). También soporta legacy anidado.
    - Convierte original_chunk JSON (tabla/imagen) a texto legible.
    """

    name = "IndexerCreation"
    _INPUT_VALIDATORS = [TaskSettingPresenceValidator("embeddings")]

    def validate(self):
        validation_errors = [v.validate(self._input_data) for v in self._INPUT_VALIDATORS]
        return [e for e in validation_errors if e is not None]

    def execute(self):
        try:
            collection = self.execute_local()
            return TaskReturnData(payload={"indexer": collection.name})
        except Exception as ex:
            logger.exception("AutomaticIndexer failed")
            return TaskReturnData(error=str(ex))

    def execute_local(self):
        # Ruta base donde están los JSON de embeddings (la carpeta de timestamp)
        embeddings_base = os.path.join(
            current_dir, "..", "..",
            self._task_settings["embeddings_data_path"],
            (self._input_data["file_name"]).split(".")[0]
        )

        # Si el usuario te pasa directamente el path final (timestamp) en _input_data['embeddings'],
        # úsalo y no reconstruyas:
        if os.path.isabs(self._input_data["embeddings"]) or os.path.exists(self._input_data["embeddings"]):
            embeddings_base = self._input_data["embeddings"]

        collection = self._create_or_load_collection(
            self._task_settings["index_name"], self._task_settings["index_path"]
        )

        total = self._index_documents_in_chroma(collection, embeddings_base)
        logger.info(f"Indexer updated. Docs añadidos: {total}")
        return collection

    # ----------------- Chroma utils -----------------
    def _create_or_load_collection(self, collection_name: str, persist_directory: str):
        """
        Crea o carga una colección Chroma persistente en `persist_directory`.
        """
        output_path = os.path.join(current_dir, "..", "..", persist_directory)
        os.makedirs(output_path, exist_ok=True)

        client = chromadb.PersistentClient(path=output_path)
        # Usa espacio coseno (recomendado para embeddings de OpenAI)
        coll = client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine", "hnsw:search_ef": 100}
        )
        return coll

    # ----------------- Helpers de ordenación -----------------
    def _extract_number(self, name: str) -> int:
        m = re.search(r"(\d+)$", name)
        return int(m.group(1)) if m else 0

    def _extract_chunk_number(self, name: str) -> int:
        m = re.search(r"_chunk_(\d+)", name)
        return int(m.group(1)) if m else 0

    # ----------------- Indexado -----------------
    def _index_documents_in_chroma(self, collection, base_path: str, batch_size: int = 512) -> int:
        """
        Recorre `base_path` y añade documentos a la colección Chroma en batches.
        Estructura esperada por archivo JSON:
          {
            "file_name": "...",
            "page_num": "1",
            "chunk_id": "chunk_3",
            "page_metadata": "page 1",
            "original_chunk": "... o JSON de tabla/imagen ...",
            "text_vector": [floats]   # (formato actual)
          }
        También soporta legacy: text_vector[1]["data"][0]["embedding"].
        """
        ids: List[str] = []
        embeddings: List[List[float]] = []
        documents: List[str] = []
        metadatas: List[Dict[str, Any]] = []

        if not os.path.exists(base_path):
            raise FileNotFoundError(f"No existe la carpeta de embeddings: {base_path}")

        # Recorre por carpetas (file_stem_page)
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

                    vec = self._extract_vector(data)
                    if vec is None:
                        # salta archivos sin vector (p.ej. imagen sin notas)
                        continue

                    doc_id = f"{data.get('file_name','')}_{data.get('page_num','')}_{data.get('chunk_id','')}"
                    text = self._document_text_from_chunk(data)

                    meta = {
                        "file_name": data.get("file_name", ""),
                        "page_num": data.get("page_num", ""),
                        "chunk_id": data.get("chunk_id", ""),
                        "page_metadata": data.get("page_metadata", ""),
                        "content_type": (data.get("content_type") or "")
                    }

                    ids.append(doc_id)
                    embeddings.append(vec)
                    documents.append(text)
                    metadatas.append(meta)

                    # Batch upsert (idempotente ante reprocesamiento del mismo documento)
                    if len(ids) >= batch_size:
                        collection.upsert(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)
                        ids, embeddings, documents, metadatas = [], [], [], []

                except Exception as e:
                    logger.warning(f"Error procesando {fp}: {e}")

        # flush final
        if ids:
            collection.upsert(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)

        total = collection.count()
        print(f"Se indexaron/almacenaron {total} documentos en la colección '{collection.name}'.")
        return total

    # ----------------- Parsing helpers -----------------
    def _extract_vector(self, data: Dict[str, Any]) -> Optional[List[float]]:
        """
        Devuelve la lista de floats del vector de embedding.
        - Formato actual: data['text_vector'] -> list[float]
        - Legacy: data['text_vector'][1]['data'][0]['embedding']
        """
        vec = data.get("text_vector")
        if vec is None:
            return None

        # formato actual
        if isinstance(vec, list) and vec and isinstance(vec[0], (float, int)):
            return [float(x) for x in vec]

        # legacy anidado
        try:
            legacy = vec[1]["data"][0]["embedding"]
            if isinstance(legacy, list):
                return [float(x) for x in legacy]
        except Exception:
            pass

        return None

    def _document_text_from_chunk(self, data: Dict[str, Any]) -> str:
        """
        Convierte original_chunk a texto legible:
          - si es string -> tal cual
          - si es JSON y tiene 'table_markdown' -> usa eso
          - si es tabla con rows -> aplana filas
          - si es imagen -> usa notes/alt si existen; si no, cadena vacía
        """
        original = data.get("original_chunk", "")
        ctype = (data.get("content_type") or "").lower()

        if isinstance(original, str):
            # ¿parece JSON?
            try:
                j = json.loads(original)
            except Exception:
                j = None
        else:
            j = original

        # Tablas
        if ctype == "table" and isinstance(j, dict):
            if j.get("table_markdown"):
                return str(j["table_markdown"])
            rows = None
            tj = j.get("table_json")
            if isinstance(tj, dict):
                rows = tj.get("rows")
            if rows and isinstance(rows, list):
                lines = ["\t".join([("" if c is None else str(c)) for c in r]) for r in rows]
                return "\n".join(lines[:400])
            return json.dumps(j, ensure_ascii=False)

        # Imágenes (diagram_visual: faceta visual de ElectricalDiagramProcessor,
        # copia el mismo original_chunk que una imagen normal)
        if ctype in ("image", "diagram_visual") and isinstance(j, dict):
            notes = j.get("notes") or j.get("alt") or ""
            if isinstance(notes, dict):
                return notes.get("description") or json.dumps(notes, ensure_ascii=False)
            return str(notes)

        # Descripción estructurada de diagrama (dict directo, no serializado a string)
        if ctype == "diagram_description" and isinstance(j, dict):
            return j.get("description") or json.dumps(j, ensure_ascii=False)

        # Default
        return original if isinstance(original, str) else json.dumps(original, ensure_ascii=False)
