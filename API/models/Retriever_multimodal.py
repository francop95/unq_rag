import re
import logging
import json
import pandas as pd
from typing import Dict, List, Any
from models.ModelCompletion_multimodal import ModelCompletion
from utils.utils import calculate_num_of_tokens

logger = logging.getLogger('app.Retriever')


class Retriever:
    def __init__(self, data: Dict[str, Any]):
        """
        Inicializa el Retriever con tolerancia a los esquemas de Chroma.
        """
        self.query_id = data["query_id"]
        logger.info(f"[{self.query_id}] [Retrieval] Initialization")
        self.model_type = str(data.get("retriever_model_type", "")).lower()

        # Tokens y prompts
        data["max_tokens"] = data["retriever_max_tokens"]
        self.buffer_tokens = int(data.get("retriever_buffer_tokens", 25))
        self.retriever_prompt = data["retriever_prompt"]
        self.retriever_sys_msg = data["retriever_sys_msg"]

        # Estados
        self.message_prompt: List[Dict[str, str]] = []
        self.default_retriever_output = None
        self.completion_failure = data["completion_failure"]
        self.completion_success = data["completion_success"]
        self.retry_count = 0
        self.max_retries = int(data.get("retriever_max_retries", 2))

        # Columnas esperadas (compatibles con ChromaConnection)
        self.text_column = data["text_column"]
        self.filename_column = data["filename_column"]
        self.page_number_column = data["page_number_column"]
        self.similarity_column = data["similarity_column"]

        # Selección de modelo según proveedor
        if self.model_type == "openai":
            self.model_selected = data.get("openai_model1") or data.get("openai_model")
            self.alt_model = data.get("openai_model2")
        elif self.model_type == "azure":
            self.model_selected = data.get("azure_oai_model1")
            self.alt_model = data.get("azure_oai_model2")
        elif self.model_type == "gemini":
            self.model_selected = data.get("gemini_model")
            self.alt_model = None
        else:
            raise ValueError(f"Invalid retriever_model_type: {self.model_type}")
        self._initial_model = self.model_selected

        self.chat_obj = None

        # Config opcional para construir metadata sintética
        # Lista de campos a incluir si existen; si no, se usa fallback
        self.metadata_fields: List[str] = data.get("retriever_metadata_fields") or []
        self.metadata_chars: int = int(data.get("retriever_metadata_chars", 280))  # longitud del snippet

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------
    def get_message_prompt(self, data: Dict[str, Any]) -> List[Dict[str, str]]:
        """
        Construye el prompt del retriever: {query} + lista de secciones con "section_id" y "page_metadata".
        """
        data = self.get_metadata(data)
        if not data.get("metadata_dict_list"):
            return []

        prompt = self.retriever_prompt.format(
            data["query"],
            data["metadata_dict_list"]
        )
        message_prompt = [
            {"role": "system", "content": str(self.retriever_sys_msg)},
            {"role": "user", "content": str(prompt)}
        ]
        logger.debug(f"[{self.query_id}] [Retriever] Message Prompt: {message_prompt}")
        return message_prompt

    def _build_section_metadata(self, row: Dict[str, Any]) -> str:
        """
        Construye un string de metadata a partir de las columnas disponibles.
        Preferencia: retriever_metadata_fields; si no existen, fallback a filename + page + snippet(del texto).
        """
        # Si el usuario configuró campos específicos y esos campos existen en la fila
        if self.metadata_fields:
            parts = []
            for f in self.metadata_fields:
                if f in row and row[f] not in (None, ""):
                    parts.append(f"{f}: {row[f]}")
            if parts:
                return " | ".join(parts)

        # Fallback con lo que siempre tenemos en ChromaConnection
        file_name = row.get(self.filename_column, "")
        page = row.get(self.page_number_column, "")
        text = row.get(self.text_column, "") or ""
        text = str(text).replace("\n", " ").strip()
        if self.metadata_chars > 0 and len(text) > self.metadata_chars:
            text = text[: self.metadata_chars].rstrip() + "…"
        base = f"{file_name} | page {page}" if file_name or page != "" else ""
        return (base + " | " if base else "") + text

    def get_metadata(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        A partir de data['data_df'] (DataFrame con resultados preliminares),
        arma metadata_dict_list = [{section_id, page_metadata}, ...]
        """
        data["metadata_dict_list"] = []
        df = data.get("data_df")
        if df is None or df.empty:
            logger.debug(f"[{self.query_id}] [Retriever] data_df vacío")
            return data

        recs = df.to_dict(orient="records")
        metadata_dict_list = []
        for i, row in enumerate(recs):
            section_dict = {
                "section_id": i + 1,  # 1-based
                "page_metadata": self._build_section_metadata(row),
            }
            metadata_dict_list.append(section_dict)
        data["metadata_dict_list"] = metadata_dict_list
        logger.debug(f"[{self.query_id}] [Retriever] metadata dict preparado con {len(metadata_dict_list)} secciones")
        return data

    # ------------------------------------------------------------------
    # Main flow
    # ------------------------------------------------------------------
    def get_relevant_sections(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Ejecuta el LLM para seleccionar secciones relevantes. Devuelve d["retriever_out_df"] con las filas elegidas.
        Espera que el modelo devuelva JSON como: {"sections": [<ids 1-based>]}
        """
        logger.info(f"[{self.query_id}] [Retriever] start")
        data["retriever_output"] = self.default_retriever_output
        data["retriever_out_df"] = pd.DataFrame()

        self.message_prompt = self.get_message_prompt(data)
        if not self.message_prompt:
            logger.info(f"[{self.query_id}] [Retriever] Sin metadata para prompt; retorno vacío")
            return data

        try:
            token_cnt = calculate_num_of_tokens(str(self.message_prompt), data["query_id"])
            retry_flag = True

            while retry_flag:
                dynamic_params = {
                    "model": self.model_selected,          # genérico
                    "openai_model": self.model_selected,   # específicos por proveedor (por compat)
                    "azure_model": self.model_selected,
                    "gemini_model": self.model_selected,
                    "max_tokens": token_cnt + self.buffer_tokens,
                }
                self.chat_obj = ModelCompletion(data, self.model_type, dynamic_params=dynamic_params)
                retriever_response, retriver_raw_response = self.chat_obj.get_response(self.message_prompt, data)
                data["retriver_raw_gpt_response"] = retriver_raw_response
                data["retriever_response"] = retriever_response

                data = self.parse_retriever_response(data)

                if (not data.get("retriever_output")) and (self.retry_count < self.max_retries):
                    # en el último intento, cambia de modelo si hay alternativo
                    if (self.retry_count == self.max_retries - 1) and self.alt_model:
                        self.model_selected = self.alt_model
                    logger.info(
                        f"[{self.query_id}] [Retriever] Reintento {self.retry_count+1} con modelo: {self.model_selected}"
                    )
                    self.retry_count += 1
                else:
                    retry_flag = False
                    self.retry_count = 0
                    self.model_selected = self._initial_model

        except Exception as e:
            logger.error(f"[{self.query_id}] [Retriever] Error en get_relevant_sections: {e}", exc_info=True)
            data["retriever_output"] = None
            data["retriver_raw_gpt_response"] = str(e)

        logger.info(f"[{self.query_id}] [Retriever] end")
        return data

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------
    def parse_retriever_response(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Espera respuesta como JSON:
            {"sections": [1, 2, 5]}
        (IDs 1-based que coinciden con el orden de data_df)
        """
        resp_list = data.get("retriever_response") or []
        data["retriever_output"] = self.default_retriever_output
        data["retriever_out_df"] = pd.DataFrame()

        if not resp_list:
            logger.error(f"[{self.query_id}] [Retriever] Respuesta vacía del modelo")
            return data

        first = resp_list[0]
        if first.get("res_status") == self.completion_failure:
            logger.error(f"[{self.query_id}] [Retriever] Failure en respuesta del modelo")
            return data

        # intentar JSON
        retriever_res = None
        try:
            retriever_res = json.loads(first.get("response", "") or "{}")
        except Exception as e:
            logger.error(
                f"[{self.query_id}] [Retriever] JSON Parsing Failure: {e}; usando regex fallback"
            )
            retriever_res = self.extract_response(first.get("response", ""))

        try:
            if retriever_res and isinstance(retriever_res.get("sections"), list):
                sections = []
                for sid in retriever_res["sections"]:
                    # tolera ints o strings-ints
                    if isinstance(sid, int) and sid > 0:
                        sections.append(sid)
                    elif isinstance(sid, str) and sid.isdigit():
                        sections.append(int(sid))
                sections = [s for s in sections if s > 0]

                if sections:
                    df = data["data_df"]
                    # mapea 1-based -> 0-based
                    idxs = [s - 1 for s in sections if 0 < s <= len(df)]
                    result_df = df.iloc[idxs].reset_index(drop=True)
                    data["retriever_output"] = sections
                    data["retriever_out_df"] = result_df
                    logger.info(f"[{self.query_id}] [Retriever] Output (sections): {sections}")
                else:
                    logger.info(f"[{self.query_id}] [Retriever] 'sections' vacío en respuesta")
            else:
                logger.info(f"[{self.query_id}] [Retriever] Respuesta sin 'sections'")
        except Exception as e:
            logger.error(f"[{self.query_id}] [Retriever] Parsing Failure: {e}", exc_info=True)

        return data

    # ------------------------------------------------------------------
    # Regex fallback
    # ------------------------------------------------------------------
    def extract_response(self, response: str):
        """
        Extrae {"sections": [...]} aunque 'sections' no esté entre comillas.
        Toma el primer objeto que parezca válido.
        """
        logger.info(f"[{self.query_id}][Retriever] Regex fallback")
        try:
            retriever_res = None
            # Busca inicio de objeto que contenga "sections" (con o sin comillas)
            m = re.search(r'{\s*"?sections"?\s*:\s*\[.*?\]}', response, flags=re.S)
            if m:
                req_text = m.group(0)
                # normaliza clave sections con comillas
                if not re.search(r'"\s*sections\s*"\s*:', req_text):
                    req_text = re.sub(r'(\{[\s]*)sections([\s]*:)', r'\1"sections"\2', req_text, count=1)
                retriever_res = json.loads(req_text)
            if retriever_res:
                logger.info(f"[{self.query_id}] [Retriever] Regex parse OK")
            else:
                logger.info(f"[{self.query_id}] [Retriever] No se pudo parsear con regex")
        except Exception as e:
            logger.error(f"[{self.query_id}] [Retriever] Regex parsing failure: {e}")
            retriever_res = None
        return retriever_res
