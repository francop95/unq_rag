from datetime import datetime
import json
import os
import io
import base64
import hashlib
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Tuple, Optional

import fitz  # PyMuPDF (rasteriza y ayuda con texto nativo)
from PIL import Image

# Si usas OpenAI oficial:
# pip install openai>=1.40
try:
    from openai import OpenAI, RateLimitError
    _HAS_OPENAI = True
except Exception:
    _HAS_OPENAI = False

from langchain.text_splitter import RecursiveCharacterTextSplitter

from task import (Task, TaskReturnData)
from logger import Logger
from task_utils.validators.task_validators import DocumentExtensionValidator
from task_utils.hybrid_chunking import (
    ContentAnalyzer, SyntacticChunker, HybridChunkingStrategy, ChunkingStrategy
)
from task_utils.diagram_processor import ElectricalDiagramProcessor
from task_utils.table_processor import TableProcessor
from task_utils.hierarchy_extractor import DocumentHierarchyExtractor
from task_utils.technical_validators import TechnicalDocumentValidator
from task_utils.llm_json import QuotaExhaustedError, raise_if_quota_exhausted

current_dir = os.path.dirname(os.path.abspath(__file__))
logger = Logger.get_logger(__name__)


class ChunkingTask(Task):
    """
    Chunking multimodal inteligente con estrategia híbrida:
      - Análisis de complejidad de página (decide sintáctico vs LLM)
      - LLM multimodal para páginas complejas (tablas, diagramas)
      - Chunking sintáctico para páginas simples (ahorro de costo)
      - Procesamiento especializado de diagramas eléctricos
      - Split inteligente de tablas grandes
      - Extracción de jerarquía documental
      - Validación técnica de chunks
    """

    name = "DataChunking"

    _INPUT_VALIDATORS = [
        DocumentExtensionValidator("pdf_path", ".pdf"),
    ]
    
    def __init__(self):
        super().__init__()
        # Inicializar procesadores especializados
        self.hybrid_strategy = None
        self.syntactic_chunker = None
        self.diagram_processor = None
        self.table_processor = None
        self.hierarchy_extractor = None
        self.technical_validator = None

    def validate(self):
        validation_errors = [validator.validate(self._input_data)
                             for validator in self._INPUT_VALIDATORS]
        return [e for e in validation_errors if e is not None]

    def execute(self):
        try:
            output_dir = self.execute_local()
            return TaskReturnData(payload={"chunks": output_dir})
        except Exception as e:
            logger.exception("ChunkingTask failed")
            return TaskReturnData(error=str(e))

    # ===============================
    # Ejecuta local: página a página
    # ===============================
    def execute_local(self) -> str:
        pdf_path = self._input_data["pdf_path"]
        model = self._task_settings.get("multimodal_model", "gpt-5")  # p.ej. "gpt-5"
        temperature = float(self._task_settings.get("temperature", 0.2))
        max_output_tokens = int(self._task_settings.get("max_output_tokens", 4000))
        
        # Configurar estrategia híbrida
        use_hybrid = self._task_settings.get("use_hybrid_chunking", True)
        complexity_threshold = float(self._task_settings.get("complexity_threshold", 0.5))

        file_name = os.path.basename(pdf_path)
        timestamp = self._get_exec_timestamp()

        # Carpeta base de salida
        base_output_dir = os.path.join(
            current_dir, "..", "..",
            self._task_settings["chunks_data_path"],
            os.path.splitext(file_name)[0],
            timestamp
        )
        os.makedirs(base_output_dir, exist_ok=True)

        # Subcarpeta de imágenes recortadas (image/table crops)
        crops_dir = os.path.join(base_output_dir, "crops")
        os.makedirs(crops_dir, exist_ok=True)
        
        # Inicializar procesadores especializados
        self.hybrid_strategy = HybridChunkingStrategy(
            llm_model=model,
            complexity_threshold=complexity_threshold
        )
        self.syntactic_chunker = SyntacticChunker(
            chunk_size=int(self._task_settings.get("chunk_size", 1000)),
            chunk_overlap=int(self._task_settings.get("chunk_overlap", 200))
        )
        self.diagram_processor = ElectricalDiagramProcessor(
            use_ocr=self._task_settings.get("use_ocr_for_diagrams", True)
        )
        self.table_processor = TableProcessor(
            max_rows_per_chunk=int(self._task_settings.get("max_table_rows", 10))
        )
        self.hierarchy_extractor = DocumentHierarchyExtractor()
        self.technical_validator = TechnicalDocumentValidator()

        # Cliente LLM
        client = self._get_openai_client()  # usa OpenAI oficial o un stub
        
        # Extraer jerarquía del documento completo (una sola vez)
        logger.info("Extrayendo jerarquía del documento...")
        hierarchy_data = self.hierarchy_extractor.extract_from_pdf(pdf_path)
        logger.info(f"Jerarquía extraída: {len(hierarchy_data['toc'])} entradas TOC")
        
        # Estadísticas de procesamiento
        stats = {
            "pages_syntactic": 0,
            "pages_llm": 0,
            "diagrams_processed": 0,
            "tables_split": 0,
            "total_chunks": 0,
            "native_images_extracted": 0
        }

        # Abrimos el PDF
        with fitz.open(pdf_path) as doc:
            total_pages = len(doc)

            # Imágenes ya vistas en el documento (hash de píxeles), para no
            # repetir logos/encabezados que se repiten en muchas páginas
            # SYNTACTIC como si fueran figuras nuevas cada vez.
            seen_native_image_hashes: set = set()

            # ═══════════════════════════════════════════════════════════
            # FASE 1: análisis + preparación (secuencial, toca fitz/PyMuPDF,
            # que no es seguro para acceso concurrente desde varios threads)
            # ═══════════════════════════════════════════════════════════
            page_chunks: Dict[int, List[Dict[str, Any]]] = {}
            llm_jobs: List[Dict[str, Any]] = []

            for idx in range(total_pages):
                page_no = idx + 1
                page = doc[idx]
                logger.info(f"Analizando página {page_no}/{total_pages} ...")

                # 1) ANÁLISIS DE COMPLEJIDAD
                # El análisis se hace SIEMPRE: además de decidir la estrategia,
                # alimenta el OCR previo de planos escaneados y la variante del
                # prompt para diagramas. Cuando use_hybrid=false solo se ignora
                # para la decisión (todo va a LLM), pero se sigue necesitando.
                page_analysis = ContentAnalyzer.analyze_page(page)
                if use_hybrid:
                    strategy = self.hybrid_strategy.decide_strategy(page, file_name)
                    complexity = page_analysis["visual_complexity"]
                    logger.info(f"  Complejidad: {complexity:.2f} | Estrategia: {strategy.value}")
                else:
                    strategy = ChunkingStrategy.LLM
                    logger.info(f"  Estrategia: llm (chunking híbrido desactivado)")

                # 2) PROCESAMIENTO SEGÚN ESTRATEGIA
                if strategy == ChunkingStrategy.SYNTACTIC:
                    # Chunking sintáctico (rápido, gratis) -> resuelto ya en esta fase
                    native_text = (page.get_text("text") or "").strip()
                    normalized_chunks = self.syntactic_chunker.chunk(
                        text=native_text,
                        page_num=page_no,
                        file_name=file_name
                    )

                    # Las páginas SYNTACTIC no pasan por el pipeline LLM, que hasta
                    # ahora era el único que extraía imágenes (vía bbox sobre el
                    # render de la página). Eso dejaba cualquier foto embebida en
                    # una página de baja complejidad visual (ej. 1-2 fotos sueltas,
                    # sin tablas ni diagramas vectoriales) completamente fuera del
                    # sistema multimodal: nunca se guardaba ni se indexaba. Se
                    # extraen acá directo con PyMuPDF, sin costo de LLM.
                    native_image_chunks = self._extract_native_images(
                        doc=doc,
                        page=page,
                        page_no=page_no,
                        native_text=native_text,
                        crops_dir=crops_dir,
                        base_name=os.path.splitext(file_name)[0],
                        seen_hashes=seen_native_image_hashes,
                    )
                    if native_image_chunks:
                        stats["native_images_extracted"] += len(native_image_chunks)
                    normalized_chunks = normalized_chunks + native_image_chunks

                    stats["pages_syntactic"] += 1
                    page_chunks[page_no] = normalized_chunks

                else:
                    # Chunking con LLM multimodal (para páginas complejas):
                    # se prepara el payload acá (necesita fitz) pero la llamada
                    # de red se difiere a la Fase 2, en paralelo.
                    stats["pages_llm"] += 1

                    # Rasterizar página -> PNG base64
                    png_bytes, (img_w, img_h) = self._render_page_png(page, zoom=2.0)
                    page_b64 = base64.b64encode(png_bytes).decode("utf-8")
                    data_url = f"data:image/png;base64,{page_b64}"

                    # Texto nativo (si lo hay) – da contexto extra al modelo
                    native_text = (page.get_text("text") or "").strip()

                    # ⭐ PLANOS ESCANEADOS: Si no hay texto pero sí imágenes, hacer OCR previo
                    ocr_text = ""
                    if page_analysis and page_analysis.get("has_images") and not page_analysis.get("has_text"):
                        logger.info("  📄 Plano escaneado detectado → Aplicando OCR previo...")
                        # Guardar imagen temporal para OCR
                        temp_img_path = os.path.join(crops_dir, f"temp_page_{page_no}.png")
                        with open(temp_img_path, "wb") as tmp:
                            tmp.write(png_bytes)

                        # Ejecutar OCR
                        ocr_data = self.diagram_processor.extract_ocr_text(temp_img_path)
                        ocr_text = ocr_data.get("raw_text", "")

                        if ocr_text:
                            logger.info(f"  ✓ OCR extrajo {len(ocr_text)} caracteres (confianza: {ocr_data.get('confidence', 0):.1f}%)")
                            # Añadir OCR como contexto adicional
                            native_text = f"[OCR del plano]\n{ocr_text}\n\n{native_text}".strip()

                        # Limpiar temporal
                        try:
                            os.remove(temp_img_path)
                        except:
                            pass

                    # Prompt de instrucción (mejorado para diagramas/planos)
                    system_prompt = self._system_prompt_for_chunking()
                    if page_analysis and (page_analysis.get("has_images") or page_analysis.get("has_vectors")):
                        system_prompt = self.diagram_processor.enhance_diagram_prompt(system_prompt)

                    user_payload = self._build_user_multimodal_payload(
                        file_name=file_name,
                        page_num=page_no,
                        page_image_data_url=data_url,
                        page_text=native_text,
                        image_width=img_w,
                        image_height=img_h
                    )

                    llm_jobs.append({
                        "page_no": page_no,
                        "system_prompt": system_prompt,
                        "user_payload": user_payload,
                        "png_bytes": png_bytes,
                    })

            # ═══════════════════════════════════════════════════════════
            # FASE 2: llamadas al LLM en paralelo (I/O de red, acotado por
            # chunking_concurrency para no pasarse de los rate limits del
            # proveedor). El cliente OpenAI es seguro para uso concurrente.
            # ═══════════════════════════════════════════════════════════
            if llm_jobs:
                max_workers = max(1, int(self._task_settings.get("chunking_concurrency", 4)))
                logger.info(
                    f"Lanzando {len(llm_jobs)} llamadas LLM con concurrencia={max_workers}..."
                )
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    future_to_job = {
                        executor.submit(
                            self._call_llm_multimodal,
                            client, model, job["system_prompt"], job["user_payload"],
                            temperature, max_output_tokens
                        ): job
                        for job in llm_jobs
                    }
                    for future in as_completed(future_to_job):
                        job = future_to_job[future]
                        try:
                            job["llm_json"] = future.result()
                        except QuotaExhaustedError:
                            # Sin crédito no hay página que vaya a funcionar: cortar acá
                            # en vez de loguear el mismo error 86 veces y terminar con un
                            # documento vacío.
                            logger.error("Sin crédito en la cuenta de OpenAI: se aborta el chunking")
                            raise
                        except Exception as e:
                            logger.error(f"Error en LLM para página {job['page_no']}: {e}")
                            job["llm_json"] = {"chunks": []}

                # Post-procesar (recortar bboxes y normalizar) en orden de página
                for job in llm_jobs:
                    page_chunks[job["page_no"]] = self._postprocess_page_response(
                        llm_json=job["llm_json"],
                        crops_dir=crops_dir,
                        base_name=os.path.splitext(file_name)[0],
                        page_no=job["page_no"],
                        image_bytes=job["png_bytes"]
                    )

            # ═══════════════════════════════════════════════════════════
            # FASE 2.5: pasada DEDICADA por figura/tabla
            # La llamada de página está segmentando y transcribiendo a la vez, así
            # que la descripción de cada figura y la transcripción de cada tabla
            # salen diluidas. Acá se vuelve a llamar al modelo con el recorte
            # AISLADO y un prompt enfocado, que da descripciones de diagramas
            # mucho más ricas (componentes, conexiones, valores) y transcripciones
            # de tabla fieles (celdas combinadas incluidas).
            # ═══════════════════════════════════════════════════════════
            if self._task_settings.get("use_dedicated_figure_pass", True):
                self._enrich_figures_and_tables(
                    page_chunks=page_chunks,
                    client=client,
                    model=model,
                    stats=stats,
                )

            # ═══════════════════════════════════════════════════════════
            # FASE 3: procesamiento especializado, en orden de página
            # (determinista, igual que antes de paralelizar). La persistencia
            # se difiere: el enlace prev/next (Fase 3.1) necesita ver TODOS
            # los chunks del documento para poder enlazar cruzando páginas.
            # ═══════════════════════════════════════════════════════════
            all_chunks_ordered: List[Dict[str, Any]] = []

            for page_no in range(1, total_pages + 1):
                normalized_chunks = page_chunks.get(page_no, [])

                # 3) PROCESAMIENTO ESPECIALIZADO POR TIPO DE CHUNK
                processed_chunks = []
                for chunk in normalized_chunks:
                    # 3.1) Procesar diagramas eléctricos
                    if self.diagram_processor.is_diagram(chunk):
                        enhanced_chunks = self.diagram_processor.create_enhanced_diagram_chunks(chunk)
                        processed_chunks.extend(enhanced_chunks)
                        stats["diagrams_processed"] += 1
                        facetas = ", ".join(c.get("content_type", "?") for c in enhanced_chunks)
                        logger.info(f"  ✨ Diagrama mejorado → {len(enhanced_chunks)} chunks ({facetas})")

                    # 3.2) Procesar tablas grandes
                    elif chunk.get("content_type") == "table":
                        if self.table_processor.needs_splitting(chunk):
                            # Dividir tabla en chunks más pequeños
                            table_chunks = self.table_processor.split_table(chunk)
                            processed_chunks.extend(table_chunks)
                            stats["tables_split"] += 1
                            logger.info(f"  Tabla dividida → {len(table_chunks)} partes")
                        else:
                            processed_chunks.append(chunk)

                    else:
                        processed_chunks.append(chunk)

                # 4) ENRIQUECER CON METADATA JERÁRQUICA
                for chunk in processed_chunks:
                    chunk = self.hierarchy_extractor.enrich_chunk_with_hierarchy(chunk)

                # Reemplazar con chunks procesados
                normalized_chunks = processed_chunks
                stats["total_chunks"] += len(normalized_chunks)
                all_chunks_ordered.extend(normalized_chunks)

                logger.info(f"Página {page_no} -> {len(normalized_chunks)} chunks.")

            # ═══════════════════════════════════════════════════════════
            # FASE 3.1: enlazar prev_chunk_id/next_chunk_id secuencialmente
            # sobre TODOS los chunks del documento (cruza páginas). Habilita
            # Context Expansion en retrieval: cuando un chunk queda corto o
            # aislado (ej: título de síntoma separado de su acción
            # correctiva en una tabla de troubleshooting), se puede
            # recuperar el contenido del chunk vecino.
            # ═══════════════════════════════════════════════════════════
            def _composite_chunk_id(c: Dict[str, Any]) -> str:
                return f"{c.get('file_name','')}_{c.get('page_num','')}_{c.get('chunk_id','')}"

            def _figure_group(c: Dict[str, Any]) -> Optional[str]:
                """
                Id de la figura a la que pertenece un chunk, si es una de sus facetas.
                Las facetas de una figura (chunk_3_visual, chunk_3_ocr) son consecutivas
                en la lista, así que una cadena secuencial plana las volvía vecinas
                entre sí: medido, 361 de 1544 enlaces apuntaban a una hermana, y el
                context expander terminaba inyectándole a un diagrama su propio OCR
                ilegible como "[CONTEXTO SIGUIENTE]". Una figura es UNA unidad de
                lectura: sus vecinos son el contenido de alrededor, no sus facetas.
                """
                chunk_id = str(c.get("chunk_id", ""))
                for suffix in ("_ocr", "_structured", "_visual"):
                    if chunk_id.endswith(suffix):
                        return f"{c.get('file_name','')}_{c.get('page_num','')}_{chunk_id[:-len(suffix)]}"
                return None

            for i, chunk in enumerate(all_chunks_ordered):
                group = _figure_group(chunk)

                # Retroceder/avanzar hasta salir del grupo de facetas propio
                prev_idx = i - 1
                while prev_idx >= 0 and group is not None and _figure_group(all_chunks_ordered[prev_idx]) == group:
                    prev_idx -= 1
                next_idx = i + 1
                while (next_idx < len(all_chunks_ordered) and group is not None
                       and _figure_group(all_chunks_ordered[next_idx]) == group):
                    next_idx += 1

                chunk["prev_chunk_id"] = _composite_chunk_id(all_chunks_ordered[prev_idx]) if prev_idx >= 0 else ""
                chunk["next_chunk_id"] = (
                    _composite_chunk_id(all_chunks_ordered[next_idx])
                    if next_idx < len(all_chunks_ordered) else ""
                )

            # ═══════════════════════════════════════════════════════════
            # FASE 3.2: persistir chunks en archivos individuales por página
            # ═══════════════════════════════════════════════════════════
            for chunk in all_chunks_ordered:
                page_output_dir = os.path.join(
                    base_output_dir, f"{os.path.splitext(file_name)[0]}_{chunk.get('page_num')}"
                )
                os.makedirs(page_output_dir, exist_ok=True)

                out_path = os.path.join(
                    page_output_dir,
                    f"{os.path.splitext(file_name)[0]}_{chunk.get('page_num')}_{chunk['chunk_id']}.json"
                )
                with open(out_path, "w", encoding="utf-8") as f:
                    json.dump(chunk, f, ensure_ascii=False, indent=4)

        # Reporte final de estadísticas (total_pages puede ser 0 en un PDF vacío
        # o ilegible: se evita la división por cero)
        pages_divisor = total_pages or 1
        logger.info(f"\n{'='*60}")
        logger.info(f"ESTADÍSTICAS DE CHUNKING")
        logger.info(f"{'='*60}")
        logger.info(f"Total páginas:           {total_pages}")
        logger.info(f"Páginas sintácticas:     {stats['pages_syntactic']} ({stats['pages_syntactic']/pages_divisor*100:.1f}%)")
        logger.info(f"Páginas con LLM:         {stats['pages_llm']} ({stats['pages_llm']/pages_divisor*100:.1f}%)")
        logger.info(f"Diagramas procesados:    {stats['diagrams_processed']}")
        logger.info(f"Tablas divididas:        {stats['tables_split']}")
        logger.info(f"Imágenes nativas (sin LLM): {stats['native_images_extracted']}")
        logger.info(f"Figuras re-descritas:     {stats.get('figures_enriched', 0)}")
        logger.info(f"Tablas re-transcritas:    {stats.get('tables_enriched', 0)}")
        logger.info(f"Total chunks:            {stats['total_chunks']}")

        # Estimar ahorro
        if use_hybrid and stats['pages_syntactic'] > 0:
            estimated_savings = stats['pages_syntactic'] / pages_divisor * 100
            logger.info(f"Ahorro estimado:         {estimated_savings:.1f}% (~${estimated_savings * 0.03:.2f})")
        logger.info(f"{'='*60}\n")
        
        return base_output_dir

    # ===============================
    # Pasada dedicada por figura / tabla
    # ===============================
    _FIGURE_SYSTEM_PROMPT = (
        "Eres un especialista en documentación técnica industrial. Recibirás UNA "
        "figura recortada de un manual (diagrama eléctrico, esquema, plano o foto de "
        "equipo) y debes describirla para que sea recuperable por búsqueda semántica.\n\n"
        "Devuelve SOLO un objeto JSON con estas claves:\n"
        '- "diagram_type": tipo ("wiring_diagram", "schematic", "block_diagram", '
        '"connection_diagram", "circuit_diagram", "panel_layout", "photo", "chart", "other")\n'
        '- "description": 2-4 frases describiendo qué muestra y para qué sirve\n'
        '- "components": lista de componentes identificables con su etiqueta ("motor M1", "contactor K1", "borne X1:3")\n'
        '- "connections": lista de conexiones o recorridos visibles ("L1 → K1 → motor M1")\n'
        '- "ratings": objeto con valores nominales visibles ({"voltage": "480V AC", "current": "12A"})\n'
        '- "labels": lista de TODOS los textos/números legibles en la figura\n\n'
        "Usa listas vacías u objetos vacíos si algo no aplica. No inventes: describe "
        "únicamente lo que se ve. Responde en el idioma de la figura (español si está en español)."
    )

    _TABLE_SYSTEM_PROMPT = (
        "Eres un especialista en transcripción de tablas técnicas. Recibirás UNA tabla "
        "recortada de un manual y debes transcribirla con exactitud.\n\n"
        "Devuelve SOLO un objeto JSON con estas claves:\n"
        '- "table_markdown": la tabla completa en Markdown, con encabezados\n'
        '- "rows": array de arrays con TODAS las filas (la primera es el encabezado)\n'
        '- "caption": título o pie de la tabla si es visible, si no ""\n'
        '- "notes": aclaraciones de la tabla (llamadas, notas al pie) si las hay\n\n'
        "Reglas: respetá las unidades tal como aparecen; si una celda está combinada, "
        "repetí su valor en cada columna que abarca; si una celda está vacía usá \"\". "
        "No resumas ni omitas filas. Transcribí exactamente lo que se ve."
    )

    def _enrich_figures_and_tables(
        self,
        page_chunks: Dict[int, List[Dict[str, Any]]],
        client,
        model: str,
        stats: Dict[str, int],
    ) -> None:
        """
        Reemplaza en sitio la descripción/transcripción de cada figura y tabla con
        el resultado de una llamada dedicada al modelo de visión sobre su recorte.
        """
        from task_utils.llm_json import (
            LLMJsonClient, image_content_part, text_content_part, run_parallel,
        )

        # Recolectar los chunks que tienen un recorte propio
        targets: List[Dict[str, Any]] = []
        for page_no, chunks in page_chunks.items():
            for chunk in chunks:
                ctype = chunk.get("content_type")
                if ctype not in ("image", "table"):
                    continue
                try:
                    payload = json.loads(chunk["original_chunk"]) if isinstance(
                        chunk.get("original_chunk"), str
                    ) else chunk.get("original_chunk")
                except (ValueError, TypeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                image_path = payload.get("image_path")
                if image_path and os.path.isfile(image_path):
                    targets.append(
                        {"chunk": chunk, "payload": payload, "image_path": image_path,
                         "ctype": ctype, "page_no": page_no}
                    )

        if not targets:
            logger.info("Pasada dedicada de figuras: no hay recortes que enriquecer")
            return

        concurrency = max(1, int(self._task_settings.get("figure_pass_concurrency", 3)))
        logger.info(
            f"Pasada dedicada de figuras/tablas: {len(targets)} recortes "
            f"con concurrencia={concurrency}"
        )

        llm = LLMJsonClient(client=client, model=model, temperature=0.0)

        def worker(target: Dict[str, Any]):
            image_part = image_content_part(target["image_path"])
            if image_part is None:
                return None

            is_table = target["ctype"] == "table"
            system = self._TABLE_SYSTEM_PROMPT if is_table else self._FIGURE_SYSTEM_PROMPT
            hint = (
                f"Tabla recortada de la página {target['page_no']}."
                if is_table else
                f"Figura recortada de la página {target['page_no']}."
            )
            return llm.complete_json(
                system_prompt=system,
                user_content=[text_content_part(hint), image_part],
                label=f"figure_pass_p{target['page_no']}",
            )

        results = run_parallel(targets, worker, concurrency, label="figure_pass")

        enriched_figures = 0
        enriched_tables = 0
        for target, result in zip(targets, results):
            if not isinstance(result, dict):
                continue

            chunk = target["chunk"]
            payload = target["payload"]

            if target["ctype"] == "table":
                markdown = result.get("table_markdown")
                rows = result.get("rows")
                if not markdown and not rows:
                    continue
                if markdown:
                    payload["table_markdown"] = markdown
                if isinstance(rows, list) and rows:
                    payload["table_json"] = {"rows": rows}
                for key in ("caption", "notes"):
                    if result.get(key):
                        payload[key] = result[key]
                enriched_tables += 1
            else:
                # La descripción estructurada va en `notes`, que es de donde el
                # resto del pipeline (diagram_processor, embeddings, indexer) ya
                # lee la representación textual de una imagen.
                if not result.get("description") and not result.get("labels"):
                    continue
                payload["notes"] = result
                enriched_figures += 1

            chunk["original_chunk"] = json.dumps(payload, ensure_ascii=False)

        stats["figures_enriched"] = enriched_figures
        stats["tables_enriched"] = enriched_tables
        logger.info(
            f"Pasada dedicada completada: {enriched_figures} figuras y "
            f"{enriched_tables} tablas re-descritas"
        )

    # ===============================
    # Prompts / payload para el LLM
    # ===============================
    @staticmethod
    def _system_prompt_for_chunking() -> str:
        """
        Instrucciones concisas para el LLM multimodal.
        Pide devolver una ESTRUCTURA JSON con chunks ya listos.
        """
        return (
            "Eres un sistema de segmentación y extracción de documentos. "
            "Recibirás una página como imagen y su texto nativo (si existe). "
            "Devuelve una lista JSON de 'chunks' ORDENADOS de arriba a abajo. "
            "Cada chunk debe ser UN OBJETO con:\n"
            "- type: 'text' | 'table' | 'image'\n"
            "- bbox: [x0,y0,x1,y1] NORMALIZADO como fracciones de 0.0 a 1.0 del ancho/alto de la imagen "
            "(x0,x1 relativos al ancho; y0,y1 relativos al alto; NO uses píxeles absolutos) "
            "(obligatorio para 'table' e 'image'; opcional para 'text')\n"
            "- content: para 'text', el texto limpio (sin encabezados/pies repetidos si detectas patrones). "
            "Para 'table', incluye 'markdown' con la tabla en Markdown con encabezados si aplican, "
            "y 'json' con {'rows': [[celdas...], ...]}.\n"
            "- notes: opcional (p.ej. título de figura, pie de tabla, etc.)\n\n"
            "Reglas:\n"
            "1) Identifica como 'image' gráficos/dibujos/diagramas. Si una imagen contiene texto denso legible, puedes convertirla a 'text' o 'table'.\n"
            "2) Para tablas, prioriza extraerlas a Markdown y JSON estructurado; añade bbox.\n"
            "3) No devuelvas nada fuera de JSON. Respuesta = array de objetos.\n"
            "4) IMPORTANTE sobre bbox de 'image'/'table': el bbox debe cubrir la figura COMPLETA "
            "(todas sus partes conectadas: líneas, símbolos, leyendas y etiquetas que forman parte de "
            "ella), sin cortar ningún borde. Excluí el texto de párrafo que la precede o sigue (no forma "
            "parte de la figura). Ante la duda entre un bbox más ajustado o uno un poco más amplio que "
            "cubra todo, preferí el más amplio: es mucho peor cortar la figura que incluir unos pocos "
            "píxeles de margen de más.\n"
        )

    @staticmethod
    def _build_user_multimodal_payload(
        file_name: str,
        page_num: int,
        page_image_data_url: str,
        page_text: str,
        image_width: int,
        image_height: int
    ) -> List[Dict[str, Any]]:
        """
        Mensaje 'user' multimodal (texto + imagen data URL).
        """
        header = (
            f"Documento: {file_name} | Página: {page_num}\n"
            f"Dimensiones imagen (px, solo de referencia): width={image_width}, height={image_height}\n"
            "Recordá: bbox va normalizado (0.0-1.0), NO en estas unidades de píxeles.\n"
            "Devuelve SOLO JSON (array de objetos chunk)."
        )
        text_hint = (
            "Texto nativo de la página (si existe), útil para OCR-free:\n"
            f"---\n{page_text[:8000]}\n---" if page_text else
            "No hay texto nativo extraído; usa la imagen."
        )

        return [
            {
                "type": "input_text",
                "text": header + "\n\n" + text_hint
            },
            {
                "type": "input_image",
                "image_url": page_image_data_url
            }
        ]

    # ===============================
    # Llamada al LLM multimodal
    # ===============================
    def _get_openai_client(self):
        api_key = self._task_settings["open_ai_key"]
        base_url = self._task_settings.get("open_ai_url")

        # opcional: limpiar proxies del entorno si te dieron problemas
        import os
        for k in ("HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","http_proxy","https_proxy","all_proxy"):
            os.environ.pop(k, None)

        if base_url:
            return OpenAI(api_key=api_key, base_url=base_url)
        return OpenAI(api_key=api_key)

    @staticmethod
    def _retry_delay_from_error(e: Exception, attempt: int, base_delay: float, max_delay: float) -> float:
        """
        Ante un RateLimitError, usa el tiempo de espera que el propio OpenAI indica
        (header Retry-After, o el texto "Please try again in Xs" del mensaje) en vez
        de un backoff ciego — que puede esperar de más o de menos respecto al momento
        real en que se libera el cupo de tokens/min de la cuenta.
        """
        import re as _re

        try:
            response = getattr(e, "response", None)
            if response is not None:
                retry_after = response.headers.get("retry-after")
                if retry_after:
                    return min(float(retry_after) + 0.5, max_delay)
        except Exception:
            pass

        try:
            match = _re.search(r"try again in ([\d.]+)s", str(e))
            if match:
                return min(float(match.group(1)) + 0.5, max_delay)
        except Exception:
            pass

        return min(base_delay * (2 ** attempt), max_delay)


    def _call_llm_multimodal(
        self,
        client,
        model: str,
        system_prompt: str,
        user_payload,  # [{"type":"input_text","text":...},{"type":"input_image","image_url":...}]
        temperature: float,
        max_output_tokens: int
    ):
        """
        OpenAI SDK >= 1.x only.
        1) Intenta Chat Completions (multimodal).
        2) Si existe client.responses, intenta Responses (sin response_format).
        Devuelve dict {"chunks":[...]}.
        """
        import json
        import re

        def _ensure_chunks_json(text: str):
            t = (text or "").strip()
            if t.startswith("```"):
                t = t.strip("`")
                if t.lower().startswith("json"):
                    t = t[4:].lstrip()
            
            # Guardar JSON original para debugging
            original_text = t
            
            # Intentar parsear
            try:
                data = json.loads(t)
            except json.JSONDecodeError as e:
                logger.warning(f"JSON malformado en chunking, intentando reparar...")
                logger.debug(f"JSON completo (primeros 500 chars): {t[:500]}")
                logger.debug(f"JSON completo (últimos 500 chars): {t[-500:]}")
                
                # Guardar JSON problemático para inspección
                try:
                    import os
                    debug_dir = os.path.join(os.path.dirname(__file__), "..", "..", "data", "debug_json")
                    os.makedirs(debug_dir, exist_ok=True)
                    debug_file = os.path.join(debug_dir, f"malformed_{hash(t) % 10000}.json")
                    with open(debug_file, 'w', encoding='utf-8') as f:
                        f.write(original_text)
                    logger.info(f"JSON problemático guardado en: {debug_file}")
                except Exception as debug_err:
                    logger.debug(f"No se pudo guardar debug JSON: {debug_err}")
                
                error_msg = str(e)
                strategies_tried = []
                
                # ESTRATEGIA 1: Buscar chunks válidos parciales usando regex
                # Intentar extraer objetos chunk individuales que estén completos
                strategies_tried.append("extract_valid_chunks")
                chunk_pattern = r'\{\s*"chunk_id"\s*:\s*\d+\s*,\s*"text"\s*:\s*"[^"]*"\s*,\s*"metadata"\s*:\s*\{[^}]*\}\s*\}'
                found_chunks = re.findall(chunk_pattern, t, re.DOTALL)
                if found_chunks:
                    logger.info(f"Encontrados {len(found_chunks)} chunks válidos mediante regex")
                    try:
                        # Intentar parsear cada chunk encontrado
                        valid_chunks = []
                        for chunk_str in found_chunks:
                            try:
                                chunk = json.loads(chunk_str)
                                valid_chunks.append(chunk)
                            except:
                                pass
                        if valid_chunks:
                            logger.info(f"Se recuperaron {len(valid_chunks)} chunks válidos de JSON corrupto")
                            return {"chunks": valid_chunks}
                    except Exception as regex_err:
                        logger.debug(f"Error procesando chunks extraídos: {regex_err}")
                
                # ESTRATEGIA 2: Truncar en el último chunk completo
                strategies_tried.append("truncate_at_last_complete_chunk")
                # Buscar el último } antes del error
                last_brace = t.rfind('}')
                if last_brace > 0:
                    # Truncar después del último }
                    t_truncated = t[:last_brace+1]
                    
                    # Si no termina en ], agregarlo
                    if not t_truncated.rstrip().endswith(']'):
                        t_truncated = t_truncated.rstrip().rstrip(',') + ']'
                    
                    # Si comienza con {, envolver en {"chunks":...}
                    if t_truncated.lstrip().startswith('['):
                        t_attempt = t_truncated
                    else:
                        # Buscar si hay "chunks": al inicio
                        if '"chunks"' in t_truncated[:100]:
                            # Ya tiene chunks, solo asegurar cierre
                            if not t_truncated.rstrip().endswith('}'):
                                t_attempt = t_truncated.rstrip() + '}'
                            else:
                                t_attempt = t_truncated
                        else:
                            t_attempt = '{"chunks":' + t_truncated + '}'
                    
                    try:
                        data = json.loads(t_attempt)
                        logger.info(f"JSON reparado truncando en último chunk completo")
                        if isinstance(data, list):
                            return {"chunks": data}
                        if isinstance(data, dict) and "chunks" in data:
                            return data
                        if isinstance(data, dict) and "items" in data and isinstance(data["items"], list):
                            return {"chunks": data["items"]}
                        return data
                    except Exception as trunc_err:
                        logger.debug(f"Error en truncamiento: {trunc_err}")
                
                # ESTRATEGIA 3: Encontrar último ] o } válido (método original mejorado)
                strategies_tried.append("find_last_bracket")
                if t and (t[0] == '[' or t[0] == '{'):
                    if t[0] == '[':
                        last_valid = t.rfind(']')
                    else:
                        last_valid = t.rfind('}')
                    
                    if last_valid > 0:
                        t_attempt = t[:last_valid+1]
                        try:
                            data = json.loads(t_attempt)
                            logger.info("JSON reparado mediante truncamiento en bracket")
                            if isinstance(data, list):
                                return {"chunks": data}
                            if isinstance(data, dict) and "chunks" in data:
                                return data
                            if isinstance(data, dict) and "items" in data and isinstance(data["items"], list):
                                return {"chunks": data["items"]}
                            return data
                        except:
                            pass
                
                # ESTRATEGIA 4: Cerrar strings sin terminar
                if "Unterminated string" in error_msg:
                    strategies_tried.append("close_unterminated_string")
                    # Buscar el último chunk que parece estar incompleto
                    # y cerrarlo apropiadamente
                    last_brace = t.rfind('}')
                    if last_brace > 0:
                        t_before_error = t[:last_brace+1]
                        # Cerrar con ]
                        t_attempt = t_before_error.rstrip(',').rstrip() + ']'
                        # Envolver si es necesario
                        if not t_attempt.startswith('['):
                            if '"chunks"' not in t_attempt[:100]:
                                t_attempt = '{"chunks":[' + t_attempt + ']}'
                        
                        try:
                            data = json.loads(t_attempt)
                            logger.info("JSON reparado cerrando string incompleto")
                            if isinstance(data, list):
                                return {"chunks": data}
                            if isinstance(data, dict) and "chunks" in data:
                                return data
                        except:
                            pass
                
                # ÚLTIMA ESTRATEGIA: Saltar este documento
                logger.error(f"No se pudo reparar JSON. Estrategias intentadas: {strategies_tried}. Error: {e}")
                logger.error(f"Primeros 200 chars: {t[:200]}")
                logger.error(f"Últimos 200 chars: {t[-200:]}")
                raise ValueError(f"JSON no reparable después de {len(strategies_tried)} estrategias: {str(e)}")
            
            # Procesar JSON válido
            if isinstance(data, list):
                return {"chunks": data}
            if isinstance(data, dict) and "chunks" in data:
                return data
            if isinstance(data, dict) and "items" in data and isinstance(data["items"], list):
                return {"chunks": data["items"]}
            raise ValueError("La respuesta no contiene 'chunks' ni es un array de chunks.")

        # ---- Construye mensajes para Chat Completions multimodal ----
        # Forzamos JSON por prompt (no usamos response_format aquí, por compatibilidad amplia).
        chat_user_content = []
        for part in user_payload:
            if part.get("type") == "input_text":
                txt = part["text"] + "\n\nDEVUELVE EXCLUSIVAMENTE JSON VÁLIDO (array o {\"chunks\":[]})."
                chat_user_content.append({"type": "text", "text": txt})
            elif part.get("type") == "input_image":
                chat_user_content.append({"type": "image_url", "image_url": {"url": part["image_url"]}})

        chat_messages = [
            {"role": "system", "content": system_prompt + "\n\nResponde SOLO en JSON válido."},
            {"role": "user", "content": chat_user_content},
        ]

        # -------- 1) Chat Completions (SDK 1.x) con retry para rate limits --------
        if hasattr(client, "chat") and hasattr(client.chat, "completions"):
            max_retries = 8
            base_delay = 2  # segundos, solo si no hay hint del servidor
            max_delay = 60

            for attempt in range(max_retries):
                try:
                    resp = client.chat.completions.create(
                        model=model,
                        messages=chat_messages,
                        temperature=temperature,
                        # Nota: en algunas versiones se puede pasar max_tokens; en otras, no aplica igual a multimodal.
                        # Si tu SDK lo soporta, descomenta:
                        # max_tokens=max_output_tokens,
                    )
                    text = resp.choices[0].message.content
                    return _ensure_chunks_json(text)

                except RateLimitError as e:
                    # Falta de crédito también es 429: cortar en vez de reintentar
                    raise_if_quota_exhausted(e, "chunking multimodal")
                    if attempt < max_retries - 1:
                        delay = self._retry_delay_from_error(e, attempt, base_delay, max_delay)
                        logger.warning(f"Rate limit alcanzado. Reintentando en {delay:.1f}s... (intento {attempt + 1}/{max_retries})")
                        time.sleep(delay)
                    else:
                        logger.error(f"Rate limit persistente después de {max_retries} intentos")
                        raise

        # -------- 2) Responses API (sin response_format) --------
        if hasattr(client, "responses"):
            resp = client.responses.create(
                model=model,
                temperature=temperature,
                # Algunas versiones soportan max_output_tokens; si da error, comenta esta línea:
                max_output_tokens=max_output_tokens,
                input=[
                    {"role": "system", "content": [{"type": "text", "text": system_prompt + "\n\nResponde SOLO en JSON válido."}]},
                    {"role": "user",   "content": user_payload},
                ],
            )
            # Extracción compatible con distintas versiones
            if hasattr(resp, "output_json") and resp.output_json:
                return resp.output_json
            if hasattr(resp, "output_text") and resp.output_text:
                return _ensure_chunks_json(resp.output_text)
            try:
                outputs = getattr(resp, "output", None) or getattr(resp, "outputs", None)
                if outputs:
                    first = outputs[0]
                    content = getattr(first, "content", None) or getattr(first, "contents", None)
                    if content and getattr(content[0], "type", "") == "output_text":
                        txt = getattr(content[0], "text", None) or getattr(content[0], "value", "")
                        return _ensure_chunks_json(txt)
            except Exception:
                pass

        raise RuntimeError("Tu SDK openai>=1.x no expone ni chat.completions ni responses utilizable para esta llamada.")



    # Lado mínimo (px) para no capturar íconos/viñetas/adornos como si fueran
    # figuras reales al extraer imágenes nativas de páginas SYNTACTIC.
    MIN_NATIVE_IMAGE_SIDE = 120

    def _extract_native_images(
        self,
        doc: "fitz.Document",
        page: "fitz.Page",
        page_no: int,
        native_text: str,
        crops_dir: str,
        base_name: str,
        seen_hashes: set,
    ) -> List[Dict[str, Any]]:
        """
        Extrae directamente (sin LLM) las imágenes rasterizadas embebidas en una
        página SYNTACTIC, con el mismo formato de chunk que produce el pipeline
        LLM para 'image' (original_chunk con bbox/image_path/notes), para que
        fluyan sin cambios por el resto del pipeline (jerarquía, embeddings,
        indexado dual, storage multimodal).

        La descripción ('notes') es best-effort: si el texto nativo trae un pie
        de figura ("Figura N: ..."), se usa eso; si no, un fragmento del texto
        de la página. Sin esto la imagen igual se guarda y se indexa vía CLIP,
        pero sin 'notes' no tiene embedding de texto propio (ver
        embeddings_task_multimodal.py) y sólo es recuperable por similitud
        visual, que no participa del gate de relevancia textual en
        ChromaConnector.search_vectors.
        """
        import re

        chunks_out: List[Dict[str, Any]] = []
        try:
            image_list = page.get_images(full=True)
        except Exception as e:
            logger.debug(f"No se pudieron listar imágenes nativas de la página {page_no}: {e}")
            return chunks_out

        if not image_list:
            return chunks_out

        caption_match = re.search(
            r"(?:figura|fig\.?|imagen|foto)\s*\d+[.:]?[^\n]*", native_text, flags=re.IGNORECASE
        )
        caption = caption_match.group(0).strip() if caption_match else ""

        seen_xrefs_this_page = set()
        counter = 0
        for img in image_list:
            xref = img[0]
            if xref in seen_xrefs_this_page:
                continue
            seen_xrefs_this_page.add(xref)

            pix = None
            try:
                pix = fitz.Pixmap(doc, xref)
                if pix.n - pix.alpha >= 4:  # CMYK u otro espacio no-RGB -> convertir
                    pix = fitz.Pixmap(fitz.csRGB, pix)

                if pix.width < self.MIN_NATIVE_IMAGE_SIDE or pix.height < self.MIN_NATIVE_IMAGE_SIDE:
                    continue

                img_hash = hashlib.md5(pix.samples).hexdigest()
                if img_hash in seen_hashes:
                    # Logo/encabezado repetido en varias páginas del mismo doc
                    continue
                seen_hashes.add(img_hash)

                counter += 1
                crop_path = os.path.join(
                    crops_dir, f"{base_name}_p{page_no}_nativeimg_{counter}.png"
                )
                pix.save(crop_path)
            except Exception as e:
                logger.debug(f"No se pudo extraer imagen nativa xref={xref} en página {page_no}: {e}")
                continue
            finally:
                pix = None  # liberar el Pixmap explícitamente (fitz)

            notes = caption or (
                native_text[:300].strip() if native_text
                else f"Imagen de la página {page_no} de {base_name}."
            )

            original_chunk = json.dumps(
                {"bbox": None, "image_path": crop_path, "notes": notes},
                ensure_ascii=False,
            )
            chunks_out.append({
                "file_name": f"{base_name}.pdf",
                "page_num": f"{page_no}",
                "chunk_id": f"chunk_nativeimg_{counter}",
                "page_metadata": f"page {page_no}",
                "original_chunk": original_chunk,
                "content_type": "image",
                "extraction_method": "native_pymupdf",
            })

        return chunks_out

    # ===============================
    # Post-procesado de la respuesta
    # ===============================
    def _postprocess_page_response(
        self,
        llm_json: Dict[str, Any],
        crops_dir: str,
        base_name: str,
        page_no: int,
        image_bytes: bytes
    ) -> List[Dict[str, Any]]:
        """
        Normaliza los chunks devueltos por el LLM:
          - Para 'image' y 'table' con bbox, recorta y guarda PNG
          - Ensambla objeto compatible con el siguiente paso
        """
        chunks_out: List[Dict[str, Any]] = []
        page_img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")

        counter = 0
        for item in llm_json.get("chunks", []):
            ctype = item.get("type")
            bbox = item.get("bbox")  # [x0,y0,x1,y1] en px
            content = item.get("content", None)
            notes = item.get("notes", None)

            if ctype not in ("text", "table", "image"):
                continue

            counter += 1
            chunk_id = f"chunk_{counter}"
            original_chunk: Any

            if ctype == "text":
                # content debe ser string
                text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
                original_chunk = text

            elif ctype in ("table", "image"):
                # recorte si hay bbox válida
                crop_path = None
                if self._is_valid_bbox(bbox):
                    crop_img = self._crop_bbox(page_img, bbox)
                    crop_path = os.path.join(
                        crops_dir,
                        f"{base_name}_p{page_no}_{ctype}_{counter}.png"
                    )
                    crop_img.save(crop_path)

                if ctype == "table":
                    # content: {markdown: str, json: {"rows":[...]}} idealmente
                    payload = {
                        "table_markdown": None,
                        "table_json": None,
                        "bbox": bbox,
                        "image_path": crop_path
                    }
                    if isinstance(content, dict):
                        if "markdown" in content:
                            payload["table_markdown"] = content["markdown"]
                        if "json" in content:
                            payload["table_json"] = content["json"]
                    if payload["table_markdown"] and payload["table_json"]:
                        payload["table_json"] = self._reconcile_table_json_header(
                            payload["table_markdown"], payload["table_json"]
                        )
                    original_chunk = json.dumps(payload, ensure_ascii=False)
                else:
                    # ctype == "image"
                    payload = {
                        "bbox": bbox,
                        "image_path": crop_path,
                        "notes": notes
                    }
                    original_chunk = json.dumps(payload, ensure_ascii=False)
            else:
                continue

            chunks_out.append({
                "file_name": f"{base_name}.pdf",
                "page_num": f"{page_no}",
                "chunk_id": chunk_id,
                "page_metadata": f"page {page_no}",
                "original_chunk": original_chunk,
                "content_type": ctype
            })

        return chunks_out

    # Margen de seguridad agregado a cada bbox antes de recortar. Los modelos
    # de visión tienden a subestimar los bordes de una figura (bbox demasiado
    # ajustado corta parte del diagrama/tabla) — es preferible incluir un poco
    # de texto/margen de más que perder contenido de la figura.
    BBOX_SAFETY_MARGIN = 0.03  # 3% del ancho/alto de la página, por lado

    @staticmethod
    def _extract_markdown_table_header(markdown: Optional[str]) -> Optional[List[str]]:
        """Extrae la fila de encabezados (primera línea '| ... |') de una tabla markdown."""
        if not markdown:
            return None
        for line in markdown.splitlines():
            line = line.strip()
            if line.startswith("|") and line.endswith("|"):
                return [cell.strip() for cell in line.strip("|").split("|")]
        return None

    @classmethod
    def _reconcile_table_json_header(cls, table_markdown: Optional[str], table_json: Any) -> Any:
        """
        El LLM a veces devuelve 'table_markdown' con encabezado correcto pero
        'table_json.rows' sin la fila de encabezado (rows[0] termina siendo ya
        la primera fila de datos). Como el resto del pipeline y el frontend
        asumen rows[0] == encabezado, esto hace que se muestren datos en vez de
        títulos de columna. Si detectamos esa inconsistencia, reinsertamos el
        encabezado extraído del markdown como primera fila de 'rows'.
        """
        if not isinstance(table_json, dict):
            return table_json

        rows = table_json.get("rows")
        if not isinstance(rows, list) or not rows:
            return table_json

        header = cls._extract_markdown_table_header(table_markdown)
        if not header:
            return table_json

        first_row = rows[0]
        if isinstance(first_row, list):
            first_row_norm = [str(cell).strip().lower() for cell in first_row]
            header_norm = [cell.strip().lower() for cell in header]
            if first_row_norm == header_norm:
                return table_json  # ya tiene el encabezado, nada que hacer

        table_json["rows"] = [header] + rows
        return table_json

    @staticmethod
    def _is_valid_bbox(bbox) -> bool:
        """bbox esperado normalizado (0.0-1.0). Tolera un pequeño overshoot del
        modelo, pero rechaza valores muy fuera de rango (indicio de que devolvió
        píxeles absolutos por error en vez de fracciones normalizadas)."""
        try:
            if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                return False
            x0, y0, x1, y1 = [float(v) for v in bbox]
            if not all(-0.1 <= v <= 1.1 for v in (x0, y0, x1, y1)):
                return False
            return x1 > x0 and y1 > y0
        except Exception:
            return False

    @classmethod
    def _crop_bbox(cls, img: Image.Image, bbox: List[float]) -> Image.Image:
        """bbox normalizado (0.0-1.0) relativo al tamaño de `img`. Se expande con
        BBOX_SAFETY_MARGIN antes de convertir a píxeles y recortar."""
        x0, y0, x1, y1 = bbox
        margin = cls.BBOX_SAFETY_MARGIN
        x0 = max(0.0, x0 - margin)
        y0 = max(0.0, y0 - margin)
        x1 = min(1.0, x1 + margin)
        y1 = min(1.0, y1 + margin)

        w, h = img.size
        px0 = max(0, min(w, x0 * w))
        px1 = max(0, min(w, x1 * w))
        py0 = max(0, min(h, y0 * h))
        py1 = max(0, min(h, y1 * h))
        return img.crop((px0, py0, px1, py1))

    # ===============================
    # Utilidades
    # ===============================
    @staticmethod
    def _render_page_png(page: "fitz.Page", zoom: float = 2.0) -> Tuple[bytes, Tuple[int, int]]:
        """
        Renderiza la página a PNG bytes. zoom=2.0 da buena calidad para OCR/visión.
        """
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue(), (img.width, img.height)

    def _get_exec_timestamp(self):
        return datetime.utcnow().strftime("%Y%m%d_%H%M%S")
