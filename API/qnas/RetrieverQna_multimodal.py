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
from configs.nomenclatura import bloque_para_prompt
from configs.grafo import bloque_para_prompt as relaciones_para_prompt

# Get the path of the top-level directory
top_level_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(top_level_dir)

logger = logging.getLogger('app.RetrieverQna')

# Cuántos contextos mostrar como fuente cuando el modelo respondió sin citar
# ninguno. Tres: suficientes para poder verificar la respuesta, pocos como para
# no sugerir una precisión de atribución que no tenemos.
UNCITED_FALLBACK_TOP_N = 3

# La planilla de la que se generan `nomenclatura.json` e `inventario.json`
# (ver Ingestion/scripts/generar_nomenclatura.py y generar_inventario.py).
# Es a dónde mandar al usuario cuando la respuesta se apoya en el glosario.
INVENTARIO_FUENTE = "Inventario_secadero_revision_Pablo_v2.xlsx"


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
        # context_id del bloque de inventario/nomenclatura, que no sale del
        # índice y por lo tanto no tiene fila en context_df (ver get_message_prompt).
        self.glosario_ctx_id = None
        self.glosario_texto = ""

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
                # Si este chunk tiene imagen/diagrama/tabla asociada, el usuario SÍ
                # va a poder verla (se muestra aparte, en el panel de fuentes), aunque
                # el modelo no pueda incluirla en el texto de la respuesta. Sin esta
                # marca el modelo asume que no hay fotos disponibles y se lo dice mal
                # al usuario, incluso citando este mismo context_id.
                #
                # La nota además le avisa que citar el context_id es lo que hace
                # aparecer la imagen: desde que la galería muestra solo lo citado,
                # no citar una figura que usó equivale a esconderla.
                media_note = (
                    "[IMAGEN DISPONIBLE: existe una foto/figura real de esto que el "
                    "usuario puede ver. Se le muestra SOLO si incluís este context_id "
                    "en referred_contexts. No digas que no hay fotos/imágenes de esto: "
                    "decí que está disponible para ver junto con la respuesta]\n"
                    if self._row_has_media(chunk) else ""
                )
                context_blocks.append(
                    f"""
                context_id: {i+1}
                {media_note}content:
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

        # Nomenclatura: que la respuesta diga "el variador (VFD01)" y no "el
        # variador". En un tablero con cuatro interruptores termomagnéticos,
        # "el interruptor" no alcanza para ir a buscarlo. Y las conexiones
        # documentadas de los componentes que aparecen en la pregunta: uno dice
        # qué es cada código, el otro con qué se conecta.
        #
        # Los dos salen del inventario, así que los dos van como UN contexto más,
        # numerado. Antes se pegaban al final del prompt ya formateado, sin
        # context_id, y eso los volvía una fuente real pero inatribuible: a la
        # pregunta por la conexión del motor el modelo respondía citando "el
        # inventario de componentes", correctamente —el glosario dice "conexión
        # estrella según tesis"—, y el usuario no encontraba ningún inventario
        # entre las fuentes porque no había forma de que apareciera.
        self.glosario_ctx_id = None
        extra = [b for b in (bloque_para_prompt(),
                             relaciones_para_prompt(data.get("query", ""), context)) if b]
        if extra:
            self.glosario_ctx_id = (len(self.context_df) if self.context_df is not None else 0) + 1
            self.glosario_texto = "\n".join(extra).strip()
            context = context + f"""

                context_id: {self.glosario_ctx_id}
                [Inventario y nomenclatura del secadero. Es documentación del equipo
                como cualquier otro contexto: si la usás, citá este context_id.]
                content:
                {self.glosario_texto}
                """.rstrip()

        # Los nombres EXACTOS de los planos adjuntos.
        #
        # El prompt de Configuration los llama "plano_distribucion_electrica.pdf"
        # y "conexionTben.pdf", y los archivos reales son "1_Plano distribucion
        # electrica_v1.pdf" y "conexionadoTben.pdf". Mientras el campo era un
        # booleano daba igual; desde que el modelo tiene que NOMBRAR el plano que
        # usó, el nombre inventado no coincide con ninguno y la declaración se
        # pierde: decía haber leído el diferencial de 40A/30mA en el plano y el
        # plano salía igual como no citado.
        adjuntos = [os.path.basename(p) for p in
                    (data.get("electric_diagram_path"), data.get("tben_diagram_path"))
                    if p and os.path.exists(p)] if data.get("attach_electric_diagrams", True) else []
        if adjuntos:
            context += ("\n\nPLANOS ADJUNTOS (nombres exactos; usá estas cadenas tal cual "
                        "en \"planos_usados\"):\n" + "\n".join(f"- {a}" for a in adjuntos))

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

    def _context_no_citado(self, exclude: set) -> List[int]:
        """
        Índices (0-based) de self.context_df que el modelo NO citó.

        Van todos, no solo los que tienen imagen. Antes esta función devolvía
        únicamente los chunks con media, porque su único propósito era que las
        figuras llegaran al frontend; con `citado` separando lo citado de lo
        recuperado, esa restricción quedó como un agujero: de los 10 fragmentos
        que se le entregaron al modelo para "¿el motor está en estrella o
        triángulo?" llegaban 5, y los 5 que faltaban eran de texto. El panel
        decía "ver todo el contexto recuperado" y mostraba la mitad.

        Que un fragmento sea de texto no lo hace menos revisable: si el modelo
        se apoyó en él sin citarlo, es justamente el que hay que poder leer.
        """
        if self.context_df is None or self.context_df.empty:
            return []
        return [i for i in range(len(self.context_df)) if i not in exclude]

    def _inventario_source(self) -> Dict[str, Any]:
        """
        El bloque de inventario/nomenclatura como fuente consultable.

        Va sin score porque no se recuperó por similitud: se incluye en todas
        las consultas. Apunta a la planilla de la que se generó, que es donde
        alguien puede ir a comprobar el dato o corregirlo.
        """
        return {
            "file_name": INVENTARIO_FUENTE,
            "page": 1,
            "similarity_score": 0.0,
            "media": [],
            "text": self.glosario_texto,
            "citado": True,
            # Lo distingue de un chunk recuperado de la MISMA planilla, que
            # también puede estar en la lista: dos filas con el mismo nombre de
            # archivo y la misma página se leen como un duplicado, cuando una es
            # un fragmento indexado y la otra el glosario completo.
            "is_glossary": True,
        }

    @staticmethod
    def _attached_plan_sources(data: Dict[str, Any], planos_usados) -> List[Dict[str, Any]]:
        """
        Los planos que se adjuntaron al LLM, como fuentes consultables.

        ModelCompletion los sube como `input_file` y el prompt le pide priorizarlos,
        así que buena parte de las respuestas eléctricas sale de ahí — pero los
        planos no están en el índice de Chroma, así que nunca aparecían en
        `sources` y el usuario recibía esas respuestas sin ninguna referencia ni
        forma de abrir el plano. Van sin score porque no se recuperaron por
        similitud: se adjuntan siempre (ver Configuration.ELECTRIC_DIAGRAM_RELATED_FILES).

        Qué plano queda marcado como citado sale de `planos_usados`, donde el
        modelo nombra el archivo Y dice qué leyó en él. Antes era un booleano
        `used_plans`, y un booleano sobre algo que viaja adjunto en todas las
        consultas no distingue "lo usé" de "estaba ahí": medido sobre la misma
        pregunta cuatro veces, `referred_contexts` dio [9] las cuatro y
        `used_plans` dio True, False, True, True. Tener que escribir qué leyó es
        lo que hace que la declaración cueste algo.

        Los que no nombró igual se devuelven, marcados `citado: False`: se le
        entregaron al modelo y el usuario puede querer abrirlos, pero no son
        respaldo de lo que respondió.
        """
        if not data.get("attach_electric_diagrams", True):
            return []

        declarados = []
        for p in (planos_usados or []):
            if isinstance(p, dict) and p.get("archivo"):
                declarados.append((str(p["archivo"]), str(p.get("dato") or "")))
            elif isinstance(p, str):
                declarados.append((p, ""))

        paths = [p for p in (data.get("electric_diagram_path"), data.get("tben_diagram_path"))
                 if p and os.path.exists(p)]
        salida = []
        for path in paths:
            nombre = os.path.basename(path)
            dato = RetrieverQna._dato_declarado(nombre, declarados)
            salida.append({
                "file_name": nombre,
                "page": 1,
                "similarity_score": 0.0,
                "media": [],
                # Qué dijo haber leído ahí. Es lo que permite verificar la cita
                # en vez de creerle, y lo que el panel muestra bajo el plano.
                "text": dato if dato is not None else "",
                "is_attached_plan": True,
                "citado": dato is not None,
            })
        return salida

    # Un nombre parecido alcanza: el modelo escribe el del prompt, no el del
    # disco, y "conexionTben.pdf" contra "conexionadoTben.pdf" no es un error
    # que deba costar la cita. Exacto primero, aproximado después.
    _PARECIDO_MINIMO = 0.7

    @staticmethod
    def _dato_declarado(nombre: str, declarados) -> Any:
        """Qué dijo el modelo haber leído en `nombre`, o None si no lo declaró."""
        from difflib import SequenceMatcher

        def norm(s: str) -> str:
            s = os.path.splitext(os.path.basename(str(s)))[0].lower()
            return "".join(c for c in s if c.isalnum())

        objetivo = norm(nombre)
        mejor, puntaje = None, 0.0
        for archivo, dato in declarados:
            n = norm(archivo)
            if not n:
                continue
            if n == objetivo:
                return dato
            r = SequenceMatcher(None, n, objetivo).ratio()
            if r > puntaje:
                mejor, puntaje = dato, r
        return mejor if puntaje >= RetrieverQna._PARECIDO_MINIMO else None

    def _build_sources(self, subset, citado: bool) -> List[Dict[str, Any]]:
        """
        Arma la lista de fuentes (citadas por texto y/o con media asociada) con
        su media, para que el frontend pueda mostrar imágenes/tablas/planos junto
        con la respuesta. `subset` es un slice de self.context_df.
        Soporta tanto el modo "chunk" (columnas content_type/media_path/image_path)
        como el modo "page" (columnas *_types/*_paths, listas por página).

        `citado` dice si el modelo declaró haber usado estas fuentes
        (referred_contexts) o si solo se le entregaron. Es la distinción que
        separa "de acá salió la respuesta" de "esto se recuperó", y el frontend
        la usa para decidir qué mostrar arriba y qué dejar plegado.
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
                "text": row.get(self.text_column, "") or "",
                "citado": citado,
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
            "referred_contexts": [1, 2, ...],  # IDs 1-based alineados con context_id del prompt
            "used_plans": true                 # si se apoyó en los planos PDF adjuntos
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
                    # El inventario es un context_id más, pero no tiene fila en
                    # context_df: se saca de la lista antes de usarla como índice,
                    # o el filtro de rango lo descartaría como si fuera inválido.
                    cito_inventario = self.glosario_ctx_id is not None and any(
                        str(i).isdigit() and int(i) == self.glosario_ctx_id
                        for i in referred_context_ids if isinstance(i, (int, str)))
                    file_names, pages, globals_col = [], [], []

                    idxs: List[int] = []
                    if len(referred_context_ids) > 0 and self.context_df is not None:
                        # asegurar índices válidos: ids son [1..N] → df.index [0..N-1]
                        idxs = [int(i) - 1 for i in referred_context_ids if isinstance(i, (int, str)) and str(i).isdigit()]
                        idxs = [i for i in idxs if 0 <= i < len(self.context_df)]

                    # Respaldo: el modelo respondió pero no citó ningún contexto.
                    #
                    # Pasa con las preguntas de diagnóstico —"el secadero no
                    # calienta"— donde el modelo sintetiza un procedimiento a partir
                    # de varios contextos en vez de responder desde uno solo. La
                    # respuesta queda bien fundada (usa códigos que solo existen en
                    # el corpus: SSR01-SSR03, R01-R03, TH01/TH02) pero se mostraba
                    # sin ninguna fuente, con "Context could not be found" y
                    # similitud 0. El usuario recibe una respuesta detallada y nada
                    # con qué verificarla, que es justo cuando más falta hace.
                    #
                    # Se muestran los contextos MÁS RELEVANTES de los que se le
                    # entregaron. No son los que declaró haber usado —eso no lo
                    # sabemos— sino los que tuvo a la vista: una afirmación más
                    # débil, pero cierta. Por eso viajan con `citado: False` y el
                    # frontend los rotula distinto.
                    cito_el_modelo = bool(idxs)
                    if not idxs and self.context_df is not None and len(self.context_df) > 0:
                        idxs = list(range(min(UNCITED_FALLBACK_TOP_N, len(self.context_df))))
                        logger.info(
                            f"[{self.query_id}] [RetrieverQna] el modelo no citó contextos; "
                            f"se muestran los {len(idxs)} más relevantes de los entregados"
                        )

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
                                # La columna ya viene en escala 0-100 (la normaliza
                                # ChromaConnection._normalize_display_score). El *100
                                # que había acá era de cuando traía la similitud
                                # coseno cruda en 0-1, y daba scores como 8406.47.
                                similarity_score = float(np.mean(sims_vals))
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
                    cited_sources = (
                        self._build_sources(self.context_df.iloc[idxs], citado=cito_el_modelo)
                        if idxs else []
                    )
                    if cito_inventario:
                        cited_sources.append(self._inventario_source())

                    # + el resto del contexto recuperado, que el modelo no citó
                    # (ver _context_no_citado). La deduplicación es solo por media:
                    # dos chunks pueden apuntar a la misma figura, y mostrarla dos
                    # veces hace parecer que hay dos evidencias donde hay una.
                    already_seen = {
                        m.get("media_path") or m.get("image_path")
                        for s in cited_sources for m in s.get("media", [])
                        if m.get("media_path") or m.get("image_path")
                    }
                    resto_idxs = self._context_no_citado(exclude=set(idxs))
                    media_sources = []
                    for s in (self._build_sources(self.context_df.iloc[resto_idxs], citado=False)
                              if resto_idxs else []):
                        media_keys = {m.get("media_path") or m.get("image_path")
                                      for m in s.get("media", [])
                                      if m.get("media_path") or m.get("image_path")}
                        if media_keys and media_keys & already_seen:
                            continue
                        already_seen |= media_keys
                        media_sources.append(s)

                    # Citadas primero, y dentro de cada grupo por relevancia real.
                    #
                    # El score ordena dentro del grupo porque sin eso el orden lo
                    # fija cómo el LLM enumeró los context_id, que no tiene nada que
                    # ver con qué tan cerca está cada fuente de la pregunta. Y el
                    # grupo va antes que el score porque un chunk no citado con 72%
                    # sigue siendo contexto recuperado, no respaldo de la respuesta:
                    # quien consuma `sources` sin mirar `citado` igual recibe
                    # primero lo que el modelo dijo haber usado. (Los planos se
                    # agregan después, aparte: no se recuperaron por similitud y el
                    # frontend los muestra en su propia sección.)
                    res_dict["sources"] = sorted(
                        cited_sources + media_sources,
                        key=lambda s: (not s.get("citado"), -float(s.get("similarity_score") or 0)),
                    )

                    # Si el modelo no devolvió "planos_usados" (p. ej. porque el
                    # JSON se recuperó por el fallback de regex), se asume que sí
                    # los usó cuando no hubo contexto textual: en ese caso los
                    # planos son la única fuente posible de la respuesta y no
                    # marcarlos dejaría a la respuesta sin ninguna referencia.
                    planos_usados = response_dict.get("planos_usados")
                    if planos_usados is None:
                        # `used_plans` es el campo viejo; se sigue leyendo para no
                        # romper una respuesta cacheada ni el parseo por regex.
                        viejo = response_dict.get("used_plans")
                        sin_contexto = self.context_df is None or self.context_df.empty
                        if viejo or (viejo is None and sin_contexto):
                            planos_usados = [
                                {"archivo": os.path.basename(p), "dato": ""}
                                for p in (data.get("electric_diagram_path"),
                                          data.get("tben_diagram_path")) if p
                            ]
                    res_dict["sources"] += self._attached_plan_sources(data, planos_usados)

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
