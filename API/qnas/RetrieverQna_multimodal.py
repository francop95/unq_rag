import re
import sys
import os
import logging
from models.ModelCompletion_multimodal import ModelCompletion
import json
from typing import Dict, List, Any
from time import perf_counter
import numpy as np
from utils.utils import calculate_num_of_tokens

# Get the path of the top-level directory
top_level_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(top_level_dir)

logger = logging.getLogger('app.RetrieverQna')


class RetrieverQna:
    def __init__(self, data: Dict[str, Any]):
        """
        Initializes the RetrieverQna class.
        """
        self.query_id = data["query_id"]
        logger.info(f"[{self.query_id}] [RetrieverQna] Initialization")

        # LLM type (openai | azure | gemini)
        self.model_type = str(data.get("llm_type", "")).lower()

        # tokens / config
        data["max_tokens"] = data["retriever_qna_max_tokens"]
        self.buffer_tokens = 600
        self.completion_failure = data["completion_failure"]
        self.completion_success = data["completion_success"]

        # columnas configurables (coinciden con ChromaConnection)
        self.text_column = data["text_column"]
        self.filename_column = data["filename_column"]
        self.similarity_column = data["similarity_column"]
        self.page_number_column = data["page_number_column"]

        # respuesta por defecto
        self.default_response_dict = {
            "question": data["query"],
            "answer": "",
            "file_name": [""],
            "page": [0],
            "global": [False],
            "similarity_score": 0,
            "ans_type": data["gpt_ans_type"],
            "sources": [],
        }

        # reintentos
        self.retry_count = 0
        self.max_retries = data["retriever_qna_max_retries"]

        # elegir modelo inicial y alternativo según llm_type
        if self.model_type == "openai":
            self.model_selected = data.get("openai_model1") or data.get("openai_model")  # fallback
            self.alt_model = data.get("openai_model2")
        elif self.model_type == "azure":
            self.model_selected = data.get("azure_oai_model1")
            self.alt_model = data.get("azure_oai_model2")
        elif self.model_type == "gemini":
            # para gemini generalmente hay uno solo
            self.model_selected = data.get("gemini_model")
            self.alt_model = None
        else:
            raise ValueError(f"Invalid llm_type: {self.model_type}")

        self._model_selected_initial = self.model_selected  # para restaurar al finalizar

        # placeholders
        self.chat_obj = None
        self.context_df = None
        self.message_prompt = []

    # -------- Prompt ----------
    def get_message_prompt(self, data: Dict[str, Any]) -> List[Dict[str, str]]:
        """
        Construye el prompt con los contextos seleccionados (data['context_dataframe'])
        en el formato esperado por retriever_gpt_qna_prompt.

        Los planos (plano_distribucion_electrica.pdf, conexionadoTben.pdf) se adjuntan
        SIEMPRE al LLM (ver ModelCompletion._call_openai_multimodal), sin importar si
        hay contexto textual. Por eso acá NUNCA se corta el flujo por falta de contexto:
        si no hay chunks, o los que hay no superan el umbral de similitud
        (data['context_relevant']), se lo indica explícitamente en el prompt para que
        el modelo responda apoyándose únicamente en los planos.
        """
        self.context_df = None
        has_context = bool(data.get("context_found")) and data.get("context_dataframe") is not None \
            and (not data["context_dataframe"].empty)

        if has_context:
            self.context_df = data["context_dataframe"].copy()
            context_blocks = []
            for i, chunk in enumerate(self.context_df.to_dict(orient='records')):
                content = chunk.get(self.text_column, "")
                context_blocks.append(
                    f"""
                context_id: {i+1}
                content:
                {content}
                """.rstrip()
                )
            context = "\n\n".join(context_blocks).strip()

            if not data.get("context_relevant", True):
                context = (
                    "[AVISO: los contextos de abajo tienen baja similitud con la pregunta "
                    "(posiblemente no relacionados) — no los uses como fuente principal. "
                    "Priorizá los planos adjuntos y, si tampoco alcanzan, indicá que no "
                    "tenés información suficiente.]\n\n" + context
                )
        else:
            context = (
                "[No se encontró contexto textual para esta consulta. Respondé "
                "basándote ÚNICAMENTE en los planos adjuntos (plano_distribucion_electrica.pdf "
                "y conexionadoTben.pdf) si contienen información suficiente para diagnosticar "
                "la falla; si tampoco alcanza, indicalo explícitamente.]"
            )

        prompt = data["retriever_gpt_qna_prompt"].format(
            context,
            data["query"],
        )
        message_prompt = [
            {"role": "system", "content": data["gpt_sys_msg_content"]},
            {"role": "user", "content": prompt},
        ]
        logger.debug(f"[{self.query_id}] [RetrieverQna] Message Prompt preparado")
        return message_prompt

    # -------- Llamada al modelo ----------
    def get_response(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Ejecuta el QnA del retriever contra el LLM seleccionado y parsea el resultado.
        """
        logger.info(f"[{self.query_id}] [RetrieverQna] start")
        data["gpt_response"] = None
        data["gpt_ans_found"] = False
        start = perf_counter()

        # No se corta el flujo por falta de contexto: los planos van adjuntos
        # siempre (ModelCompletion._call_openai_multimodal), así que igual vale
        # la pena llamar al LLM aunque no haya contexto textual relevante.
        # get_message_prompt ya arma un aviso explícito para ese caso.
        self.message_prompt = self.get_message_prompt(data)
        if not self.message_prompt:
            data["gpt_response"] = [self.default_response_dict]
            data["gpt_ans_found"] = False
            return data

        try:
            token_cnt = calculate_num_of_tokens(str(self.message_prompt), data["query_id"])
            retry_flag = True

            while retry_flag:
                # parámetros dinámicos para ModelCompletion
                dynamic_params = {
                    "model": self.model_selected,              # genérico
                    "openai_model": self.model_selected,       # específicos por proveedor
                    "azure_model": self.model_selected,
                    "gemini_model": self.model_selected,
                    "max_tokens": token_cnt + self.buffer_tokens
                }

                self.chat_obj = ModelCompletion(data, self.model_type, dynamic_params=dynamic_params)
                retriever_gpt_response, retriver_qna_raw_response = self.chat_obj.get_response(
                    self.message_prompt, data
                )

                data["retriver_qna_raw_gpt_response"] = retriver_qna_raw_response
                data["gpt_response_raw"] = retriever_gpt_response
                data = self.parse_response(data)

                # si no encontró respuesta y hay modelo alternativo, reintenta
                if (not data.get("gpt_ans_found")) and (self.retry_count < self.max_retries):
                    # en el último intento, cambia a alt_model si existe
                    if (self.retry_count == self.max_retries - 1) and self.alt_model:
                        self.model_selected = self.alt_model
                    logger.info(
                        f"[{self.query_id}] [RetrieverQna] Sin respuesta. Reintento {self.retry_count+1} con modelo: {self.model_selected}"
                    )
                    self.retry_count += 1
                else:
                    retry_flag = False
                    self.retry_count = 0
                    self.model_selected = self._model_selected_initial

            logger.info(f"[{self.query_id}] [RetrieverQna] Total Time: {round(perf_counter() - start, 3)}s")

        except Exception as e:
            logger.error(f"[{self.query_id}] [RetrieverQna] Error en get_response: {e}", exc_info=True)
            data["retriver_qna_raw_gpt_response"] = str(e)
            # deja gpt_ans_found=False / respuesta vacía por consistencia
        return data

    # -------- Fuentes (texto/tablas/imágenes/planos) ----------
    @staticmethod
    def _is_present(val) -> bool:
        """True si `val` es un dato real (no None/NaN/""/[])."""
        if val is None:
            return False
        if isinstance(val, float) and np.isnan(val):
            return False
        if isinstance(val, (list, tuple, set)):
            return len(val) > 0
        if isinstance(val, str):
            return val.strip() != "" and val.strip().lower() != "nan"
        return bool(val)

    def _row_has_media(self, row: Dict[str, Any]) -> bool:
        if "media_path" in row or "image_path" in row:
            return self._is_present(row.get("media_path")) or self._is_present(row.get("image_path"))
        return self._is_present(row.get("media_paths")) or self._is_present(row.get("image_paths"))

    def _context_media_indices(self, exclude: set) -> List[int]:
        """
        Índices (0-based) de self.context_df cuyos chunks tienen media (imagen/
        diagrama/tabla) asociada, excluyendo los que ya están en `exclude`. Se usa
        para mostrar imágenes/planos aunque el modelo no los haya citado por texto
        en "referred_contexts" (algo habitual: el modelo interpreta imágenes/planos
        visualmente, no como texto citable, así que sin esto casi nunca llegarían al
        frontend pese a haber sido parte del contexto real usado para responder).
        """
        if self.context_df is None or self.context_df.empty:
            return []
        records = self.context_df.to_dict(orient="records")
        return [i for i, row in enumerate(records) if i not in exclude and self._row_has_media(row)]

    def _build_sources(self, subset) -> List[Dict[str, Any]]:
        """
        Arma la lista de fuentes (citadas por texto y/o con media asociada) con
        su media, para que el frontend pueda mostrar imágenes/tablas/planos junto
        con la respuesta. `subset` es un slice de self.context_df.
        Soporta tanto el modo "chunk" (columnas content_type/media_path/image_path)
        como el modo "page" (columnas *_types/*_paths, listas por página).
        """
        sources = []
        records = subset.to_dict(orient="records")
        for row in records:
            file_name = row.get(self.filename_column, "")
            page = row.get(self.page_number_column, 0)
            similarity = row.get(self.similarity_column, 0.0)

            if "media_path" in row or "image_path" in row:
                # modo chunk: un solo media por fila
                media_path = row.get("media_path")
                image_path = row.get("image_path")
                content_type = row.get("content_type")
                media = []
                if self._is_present(media_path) or self._is_present(image_path):
                    media.append({
                        "content_type": content_type,
                        "media_path": media_path if self._is_present(media_path) else None,
                        "image_path": image_path if self._is_present(image_path) else None,
                    })
            else:
                # modo page: listas ya agregadas por ChromaConnection
                media_paths = row.get("media_paths") or []
                image_paths = row.get("image_paths") or []
                content_types = row.get("content_types") or []
                media = [
                    {
                        "content_type": content_types[i] if i < len(content_types) else None,
                        "media_path": media_paths[i] if i < len(media_paths) else None,
                        "image_path": image_paths[i] if i < len(image_paths) else None,
                    }
                    for i in range(max(len(media_paths), len(image_paths)))
                ]

            sources.append({
                "file_name": file_name,
                "page": page,
                "similarity_score": similarity,
                "media": media,
            })
        return sources

    # -------- Parseo ----------
    def parse_response(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parsea la respuesta del modelo.
        Espera un JSON como:
        {
          "output": {
            "answer": "...",
            "referred_contexts": [1, 2, ...]   # IDs 1-based alineados con context_id del prompt
          }
        }
        """
        responses = data.get("gpt_response_raw") or []
        data["gpt_ans_found"] = False
        parsed_responses = []

        try:
            logger.debug(f"[{self.query_id}] [RetrieverQna] Raw responses: {responses}")
            for res in responses:
                # clonar estructura por defecto para cada alternativa
                res_dict = dict(self.default_response_dict)

                if res.get("res_status") == self.completion_success:
                    # intentar JSON puro
                    response_dict = None
                    try:
                        response_dict = json.loads(res["response"], strict=False).get("output")
                    except Exception as e:
                        logger.info(f"[{self.query_id}] [RetrieverQna] JSON parsing falló. Intentando regex. Error: {e}")
                        response_dict = self.extract_response(res.get("response", ""))

                    if not response_dict:
                        parsed_responses.append(res_dict)
                        continue

                    # answer
                    res_dict["answer"] = response_dict.get("answer", "").strip()

                    # referred_contexts (1-based en el prompt)
                    referred_context_ids = response_dict.get("referred_contexts") or []
                    file_names, pages, globals_col, sims = [], [], [], []

                    idxs: List[int] = []
                    if len(referred_context_ids) > 0 and self.context_df is not None:
                        # asegurar índices válidos: ids son [1..N] → df.index [0..N-1]
                        idxs = [int(i) - 1 for i in referred_context_ids if isinstance(i, (int, str)) and str(i).isdigit()]
                        idxs = [i for i in idxs if 0 <= i < len(self.context_df)]

                    if len(idxs) > 0:
                        subset = self.context_df.iloc[idxs].reset_index(drop=True)

                        # columnas tolerantes (si faltan, se completan)
                        file_names = list(subset.get(self.filename_column, [])) or [""]
                        pages = list(subset.get(self.page_number_column, [])) or [0]
                        sims_vals = list(subset.get(self.similarity_column, []))
                        if len(sims_vals) == 0:
                            similarity_score = 0.0
                        else:
                            try:
                                similarity_score = float(np.mean(sims_vals)) * 100.0
                            except Exception:
                                similarity_score = 0.0
                        # columna 'global' puede no existir en Chroma
                        if "global" in subset.columns:
                            globals_col = list(subset["global"])
                        else:
                            globals_col = [False] * len(subset)

                        res_dict["file_name"] = file_names
                        res_dict["page"] = pages
                        res_dict["similarity_score"] = round(similarity_score, 2)
                        res_dict["global"] = globals_col
                    else:
                        # sin contextos citados por texto (ninguno / ids inválidos) →
                        # defaults. Esto no afecta a las fuentes con media, que se
                        # agregan de todas formas más abajo.
                        res_dict["file_name"] = [""]
                        res_dict["page"] = [0]
                        res_dict["similarity_score"] = 0
                        res_dict["global"] = [False]

                    # Fuentes citadas explícitamente por el modelo (texto)
                    cited_sources = self._build_sources(self.context_df.iloc[idxs]) if idxs else []

                    # + fuentes con media (imágenes/diagramas/tablas) del contexto
                    # recuperado que el modelo no citó por texto (ver _context_media_indices).
                    already_seen = {
                        m.get("media_path") or m.get("image_path")
                        for s in cited_sources for m in s.get("media", [])
                        if m.get("media_path") or m.get("image_path")
                    }
                    media_idxs = self._context_media_indices(exclude=set(idxs))
                    media_sources = []
                    for s in (self._build_sources(self.context_df.iloc[media_idxs]) if media_idxs else []):
                        media_keys = {m.get("media_path") or m.get("image_path") for m in s.get("media", [])}
                        if media_keys & already_seen:
                            continue
                        already_seen |= media_keys
                        media_sources.append(s)

                    res_dict["sources"] = cited_sources + media_sources

                    parsed_responses.append(res_dict)
                    data["gpt_ans_found"] = True

        except Exception as e:
            logger.error(f"[{self.query_id}] [RetrieverQna] Error parseando: {e}", exc_info=True)

        data["gpt_response"] = parsed_responses
        logger.info(f"[{self.query_id}] [RetrieverQna] Parsed: {data['gpt_response']}")
        return data

    # -------- Parser por regex (fallback) ----------
    def extract_response(self, response: str) -> Dict[str, Any]:
        """
        Fallback para extraer {"answer": ..., "referred_contexts": ...} cuando no viene JSON limpio.
        Busca el primer bloque que comience con {"answer".
        """
        logger.info(f"[{self.query_id}][RetrieverQna] Regex fallback")
        try:
            retriever_res = None
            # Busca un bloque json que empiece por "answer"
            res = re.search(r'{\s*"answer"\s*:', response)
            if res:
                start = res.span()[0]
                # busca cierre del objeto (ingenuo pero útil)
                # mejora: balancear llaves si fuera necesario
                res2 = re.search(r'}\s*$', response[start:], flags=re.S)
                if res2:
                    end = start + res2.span()[1]
                    retriever_res = json.loads(response[start:end])
            if retriever_res:
                logger.info(f"[{self.query_id}] [RetrieverQna] Regex parse OK")
            else:
                logger.info(f"[{self.query_id}] [RetrieverQna] Regex parse falló")
        except Exception as e:
            logger.error(f"[{self.query_id}] [RetrieverQna] Regex parsing failure: {e}")
            retriever_res = None
        return retriever_res
