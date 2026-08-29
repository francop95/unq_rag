from datetime import datetime
import os, sys, json, time, re
from typing import List, Dict, Any, Tuple, Optional

import pandas as pd

from task import (Task, TaskReturnData)
from logger import Logger
from task_utils.chunk_text import readable_chunk_text
from task_utils.validators.task_validators import TaskSettingPresenceValidator

# OpenAI SDK >=1.x
from openai import OpenAI
from httpx import ReadTimeout, ConnectTimeout, HTTPStatusError

logger = Logger.get_logger(__name__)
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, "../../../"))
sys.path.append(project_root)


class ChunksEmbeddings(Task):
    """
    Genera embeddings con OpenAI (SDK >=1.x) para los chunks generados previamente.
    - Lee carpeta de chunks locales
    - Para cada chunk:
        text        -> original_chunk
        table       -> table_markdown (o filas aplanadas) desde original_chunk JSON
        image       -> notes/alt si existe; si no, se omite
    - Batching + reintentos con backoff
    - Guarda JSON por página/chunk con campo 'text_vector'
    """

    name = "ChunksEmbeddings"

    _INPUT_VALIDATORS = [TaskSettingPresenceValidator("chunks")]

    # ---------------- Public API ----------------
    def validate(self):
        validation_errors = [validator.validate(self._input_data)
                             for validator in self._INPUT_VALIDATORS]
        return [e for e in validation_errors if e is not None]

    def execute(self):
        try:
            timestamp, output_path = self.execute_local()
            return TaskReturnData(payload={
                "embeddings": timestamp,
                "chunks": self._input_data.get("chunks"),
                "output_path": output_path
            })
        except Exception as e:
            logger.exception("ChunksEmbeddings failed")
            return TaskReturnData(error=str(e))

    def execute_local(self) -> Tuple[str, str]:
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        file_stem = (self._input_data["file_name"]).split(".")[0]
        output_path = os.path.join(
            current_dir, "..", "..",
            self._task_settings["embeddings_data_path"],
            file_stem, timestamp
        )
        os.makedirs(output_path, exist_ok=True)

        # 1) Cargar chunks
        chunks_df_list = self._get_chunks_data_from_local_folder()
        if not chunks_df_list:
            raise RuntimeError("No se encontraron chunks en la carpeta indicada.")

        processed_df = pd.concat(chunks_df_list, ignore_index=True)

        # 2) Preparar textos a embeber
        #    devolvemos (idx, text) para poder re-asignar luego
        items_to_embed: List[Tuple[int, str]] = []
        for idx, row in processed_df.iterrows():
            text = self._build_text_to_embed(row)
            if text and text.strip():
                items_to_embed.append((idx, text.strip()))

        if not items_to_embed:
            logger.warning("No hay texto válido para generar embeddings (todos eran imágenes sin notas).")
            # Aun así guardamos sin vectors
            self._save_embeddings_to_local_folder(processed_df, [], output_path)
            return timestamp, output_path

        # 3) Generar embeddings con OpenAI (batching + backoff)
        vectors = self._generate_embeddings_openai(items_to_embed)

        # 4) Escribir resultados
        self._save_embeddings_to_local_folder(processed_df, vectors, output_path)
        logger.info(f"Embeddings generados y guardados en: {output_path}")
        return timestamp, output_path

    # ---------------- Data loading ----------------
    def extract_number(self, name: str) -> int:
        m = re.search(r"(\d+)$", name)
        return int(m.group(1)) if m else 0

    def extract_chunk_number(self, name: str) -> int:
        m = re.search(r"_chunk_(\d+)", name)
        return int(m.group(1)) if m else 0

    def _get_chunks_data_from_local_folder(self) -> List[pd.DataFrame]:
        base_dir = self._input_data["chunks"]
        if not os.path.exists(base_dir):
            raise FileNotFoundError(f"No se encontró la carpeta o archivo: {base_dir}")

        # Archivo JSON consolidado (validated_chunks + superchunks). Se prioriza sobre
        # la carpeta cruda para no generar embeddings de chunks ya descartados por
        # validación técnica/calidad o marcados como duplicados.
        if os.path.isfile(base_dir) and base_dir.endswith(".json"):
            with open(base_dir, "r", encoding="utf-8") as h:
                data = json.load(h)
            records = data if isinstance(data, list) else [data]
            return [pd.DataFrame([record]) for record in records]

        document_chunks_list: List[pd.DataFrame] = []
        for subfolder in sorted(os.listdir(base_dir), key=self.extract_number):
            subfolder_path = os.path.join(base_dir, subfolder)
            if os.path.isdir(subfolder_path):
                for f in sorted(os.listdir(subfolder_path), key=self.extract_chunk_number):
                    if f.endswith(".json"):
                        fp = os.path.join(subfolder_path, f)
                        try:
                            with open(fp, "r", encoding="utf-8") as h:
                                data = json.load(h)
                            df = pd.DataFrame([data]) if isinstance(data, dict) else pd.DataFrame(data)
                            document_chunks_list.append(df)
                        except Exception as e:
                            logger.warning(f"Error al leer {fp}: {e}")
        return document_chunks_list

    # ---------------- Text building per chunk ----------------
    def _build_text_to_embed(self, row: pd.Series) -> Optional[str]:
        """
        Extrae una representación textual útil según el tipo de chunk:
          - text: usa original_chunk
          - table: intenta JSON -> markdown; si no, plana filas; si tampoco, usa original_chunk como string
          - image: usa notes/alt si existen; si no, None (omitimos)
        """
        original = row.get("original_chunk", "")
        ctype = (row.get("content_type", "") or "").lower()

        # Si original ya es string no-JSON y hay heading/subheading/page, podemos enriquecer
        heading = str(row.get("heading", "") or "").strip()
        sub_heading = str(row.get("sub_heading", "") or "").strip()
        page_meta = str(row.get("page_metadata", "") or "").strip()

        # Contexto generado por LLM (Contextual Retrieval): se prepende al texto
        # embebido para que el vector no dependa de que el chunk se explique solo.
        context_summary = row.get("context_summary", "")
        if not isinstance(context_summary, str):
            context_summary = ""
        context_summary = context_summary.strip()

        def add_context(txt: str) -> str:
            parts = [p for p in [context_summary, page_meta, heading, sub_heading, txt] if p]
            return "\n".join(parts)

        # `embed_text` gana sobre todo: lo usan los chunks-pregunta sintéticos,
        # cuyo vector debe representar la PREGUNTA mientras el documento
        # almacenado sigue siendo el contenido real del chunk padre.
        embed_override = row.get("embed_text", "")
        if isinstance(embed_override, str) and embed_override.strip():
            return embed_override.strip()

        # Texto legible del chunk según su tipo (fuente única: task_utils.chunk_text)
        readable = readable_chunk_text(dict(row)).strip()

        # Una imagen sin ninguna descripción no aporta nada al índice textual: se
        # omite del embedding (igual queda indexada por CLIP en el índice visual).
        if not readable:
            return None

        return add_context(readable)

    # ---------------- OpenAI Embeddings (batching + retries) ----------------
    def _get_openai_client(self) -> OpenAI:
        api_key = self._task_settings["open_ai_key"]
        base_url = self._task_settings.get("open_ai_url")

        # opcional: evitar proxies que puedan romper httpx
        import os as _os
        for k in ("HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","http_proxy","https_proxy","all_proxy"):
            _os.environ.pop(k, None)

        if base_url:
            return OpenAI(api_key=api_key, base_url=base_url)
        return OpenAI(api_key=api_key)

    def _generate_embeddings_openai(self, items_to_embed: List[Tuple[int, str]]) -> List[Tuple[int, List[float]]]:
        """
        items_to_embed: lista de (row_index, text)
        return: lista de (row_index, embedding_vector)
        """
        client = self._get_openai_client()
        model = self._task_settings.get("embedding_model", "text-embedding-3-large")
        batch_size = int(self._task_settings.get("embedding_batch_size", 64))
        max_retries = int(self._task_settings.get("max_retries", 5))

        results: List[Tuple[int, List[float]]] = []

        for start in range(0, len(items_to_embed), batch_size):
            batch = items_to_embed[start:start + batch_size]
            idxs = [i for (i, _) in batch]
            texts = [t for (_, t) in batch]

            # Reintentos con backoff exponencial
            attempt = 0
            while True:
                try:
                    resp = client.embeddings.create(model=model, input=texts)
                    emb_list = resp.data  # orden corresponde al input
                    if len(emb_list) != len(texts):
                        raise RuntimeError("El número de embeddings no coincide con el tamaño del batch.")
                    for i, item in enumerate(emb_list):
                        vec = item.embedding
                        results.append((idxs[i], list(vec)))
                    break  # batch OK, salir del while
                except (HTTPStatusError, ReadTimeout, ConnectTimeout) as e:
                    attempt += 1
                    if attempt > max_retries:
                        raise RuntimeError(f"Fallo tras {max_retries} reintentos al pedir embeddings: {e}")
                    sleep_s = min(2 ** attempt, 30)  # 2,4,8,16,30
                    logger.warning(f"Embeddings batch error (intento {attempt}/{max_retries}): {e}. Reintentando en {sleep_s}s ...")
                    time.sleep(sleep_s)
                except Exception as e:
                    # Errores no transientes -> relanzar
                    raise

        return results

    # ---------------- Persistencia ----------------
    def _save_embeddings_to_local_folder(self, processed_chunks_df: pd.DataFrame,
                                         vectors: List[Tuple[int, List[float]]],
                                         base_folder: str):
        """
        Asigna vectors (row_idx -> embedding) y persiste cada fila como JSON en:
        <base_folder>/<file_stem>_<page_num>/<file_stem>_<page_num>_<chunk_id>.json
        """
        # Mapear por índice de fila
        mapping: Dict[int, List[float]] = {i: vec for (i, vec) in vectors}

        for idx, df_record in processed_chunks_df.iterrows():
            if idx in mapping:
                df_record = df_record.copy()
                df_record["text_vector"] = mapping[idx]
            # else: puede no tener texto (imagen sin notas) => no añadimos vector

            file_stem = (str(df_record["file_name"]).split(".")[0]) if "file_name" in df_record else "document"
            page = str(df_record.get("page_num", "0"))
            chunk_id = str(df_record.get("chunk_id", "chunk_0"))

            folder_name_embedding = os.path.join(base_folder, f"{file_stem}_{page}")
            os.makedirs(folder_name_embedding, exist_ok=True)

            file_name = f"{file_stem}_{page}_{chunk_id}.json"
            output_path = os.path.join(folder_name_embedding, file_name)

            with open(output_path, "w", encoding="utf-8") as out_f:
                out_f.write(json.dumps(
                    self._clean_record(df_record), ensure_ascii=False, indent=4
                ))

    @staticmethod
    def _clean_record(df_record: pd.Series) -> Dict[str, Any]:
        """
        Convierte una fila del DataFrame a dict apto para JSON, quitando los NaN.

        Al concatenar chunks heterogéneos, pandas rellena con NaN los campos que
        un chunk no tiene (ej. `parent_chunk_id` en un chunk de contenido, o
        `question` en cualquier chunk que no sea una pregunta sintética). Ese NaN
        se serializaba como el literal `NaN` y aguas abajo era *truthy*, por lo
        que terminaba en la metadata de Chroma como la cadena "nan" y rompía las
        comprobaciones tipo `data.get(campo) or <default>`.
        """
        record = {}
        for key, value in dict(df_record).items():
            if isinstance(value, float) and pd.isna(value):
                continue  # se omite: el consumidor aplica su propio default
            record[key] = value
        return record
