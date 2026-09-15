"""
Chunking SOLO TEXTO + OCR (línea base para comparar contra el pipeline multimodal)
=================================================================================

Extrae contenido del PDF sin usar un modelo multimodal. Tres fuentes, todas
texto plano:

  1. Capa de texto del PDF (PyMuPDF `get_text()`).
  2. OCR de la página completa, cuando la página no tiene capa de texto
     (planos escaneados).
  3. OCR de cada imagen embebida, en páginas que sí tienen texto.

La diferencia contra el pipeline multimodal NO es el OCR —ese también lo
usa— sino el modelo de visión: acá ninguna imagen se le muestra a un LLM. Un
diagrama aporta lo que Tesseract pueda leer de sus rótulos, y nada más: no hay
descripción de qué representa, qué componentes conecta ni cómo se relacionan.

Qué NO hace, a propósito:

- No rasteriza la página para mandársela a un modelo de visión.
- No detecta tablas: si `get_text()` devuelve las celdas como texto corrido, se
  trocean por longitud y una fila puede quedar separada de su encabezado.
- No describe figuras: solo transcribe sus rótulos, si son legibles.
- No extrae jerarquía (TOC/capítulos).
- No indexa embeddings visuales (CLIP).

Emite el mismo esquema de chunk que el pipeline multimodal, así que las etapas
de embeddings e indexado se reutilizan sin tocarlas.
"""

import hashlib
import json
import os
from datetime import datetime

import fitz  # PyMuPDF

from task import Task, TaskReturnData
from logger import Logger
from task_utils.hybrid_chunking import SyntacticChunker
from task_utils.diagram_processor import ElectricalDiagramProcessor
from task_utils.validators.task_validators import DocumentExtensionValidator

logger = Logger.get_logger(__name__)
current_dir = os.path.dirname(os.path.abspath(__file__))

# Por debajo de esto se considera que la página no tiene capa de texto
# aprovechable y se intenta OCR. Una página escaneada suele devolver "" o un
# par de caracteres sueltos del encabezado.
MIN_PAGE_TEXT_CHARS = 20

# Imágenes muy chicas son logos, iconos y viñetas: el OCR no saca nada útil y
# cada una cuesta una pasada de Tesseract.
MIN_IMAGE_SIDE_PX = 120


class TextChunkingTask(Task):
    """
    PDF → chunks de texto plano (capa de texto + OCR), sin modelo de visión.

    Settings que usa:
        chunks_data_path (str):  carpeta base de salida
        chunk_size (int):        tamaño objetivo del chunk, en caracteres
        chunk_overlap (int):     solapamiento entre chunks consecutivos
        baseline_use_ocr (bool): activa el OCR (default True)
        baseline_ocr_page_zoom (float):  zoom al rasterizar para OCR de página
        ocr_confidence_threshold (float): umbral que reporta el procesador
    """

    name = "TextOnlyChunking"
    _INPUT_VALIDATORS = [DocumentExtensionValidator("pdf_path", ".pdf")]

    def __init__(self):
        super().__init__()
        self.stats = {}
        self.ocr = None
        self.chunker = None

    def validate(self):
        errors = [v.validate(self._input_data) for v in self._INPUT_VALIDATORS]
        return [e for e in errors if e is not None]

    def execute(self) -> TaskReturnData:
        try:
            chunks_dir = self.execute_local()
            return TaskReturnData(payload={"chunks": chunks_dir, "stats": self.stats})
        except Exception as ex:
            logger.exception("TextChunkingTask failed")
            return TaskReturnData(error=str(ex))

    @staticmethod
    def _get_exec_timestamp() -> str:
        return datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    # ------------------------------------------------------------------ OCR
    def _ocr_image_file(self, image_path: str) -> dict:
        """
        OCR sobre un archivo de imagen, con el mismo gate de legibilidad que usa
        el pipeline multimodal.

        Devuelve {"text", "confidence", "legible"}. El gate importa: sobre planos
        de línea escaneados Tesseract devuelve tokens sueltos con confianza alta,
        y ese ruido indexado hace que la portada del manual matchee cualquier
        consulta eléctrica.
        """
        vacio = {"text": "", "confidence": 0.0, "legible": False}
        if self.ocr is None:
            return vacio
        try:
            res = self.ocr.extract_ocr_text(image_path)
        except Exception as e:
            logger.warning(f"OCR falló en {os.path.basename(image_path)}: {e}")
            return vacio

        texto = (res.get("raw_text") or "").strip()
        if not texto:
            return vacio

        return {
            "text": texto,
            "confidence": float(res.get("confidence") or 0.0),
            "legible": ElectricalDiagramProcessor._ocr_is_legible(texto),
        }

    def _ocr_full_page(self, page, page_num: int, crops_dir: str, base_name: str) -> dict:
        """Rasteriza la página entera y le pasa OCR. Para páginas sin capa de texto."""
        zoom = float(self._task_settings.get("baseline_ocr_page_zoom", 3.0))
        img_path = os.path.join(crops_dir, f"{base_name}_p{page_num}_full.png")
        try:
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
            pix.save(img_path)
        except Exception as e:
            logger.warning(f"No se pudo rasterizar la página {page_num}: {e}")
            return {"text": "", "confidence": 0.0, "legible": False}
        return self._ocr_image_file(img_path)

    def _ocr_embedded_images(self, doc, page, page_num: int, crops_dir: str,
                             base_name: str, seen_hashes: set) -> list:
        """
        OCR de cada imagen embebida de la página.

        Devuelve una lista de dicts con `text`, `confidence`, `index` y
        `legible`. Se devuelven TAMBIÉN las ilegibles para poder contarlas: sin
        eso, un cero en `images_ocr_legible` no distingue "la página no tenía
        imágenes" de "tenía quince y el OCR no sacó nada de ninguna", que son
        diagnósticos opuestos.
        """
        salida = []
        try:
            image_list = page.get_images(full=True)
        except Exception as e:
            logger.debug(f"No se listaron imágenes de la página {page_num}: {e}")
            return salida

        for idx, info in enumerate(image_list):
            xref = info[0]
            try:
                base = doc.extract_image(xref)
            except Exception:
                continue

            data = base.get("image")
            if not data:
                continue

            # Una misma imagen repetida en varias páginas (logos, encabezados)
            # se OCRea una sola vez.
            h = hashlib.sha256(data).hexdigest()
            if h in seen_hashes:
                continue
            seen_hashes.add(h)

            if base.get("width", 0) < MIN_IMAGE_SIDE_PX or base.get("height", 0) < MIN_IMAGE_SIDE_PX:
                continue

            ext = base.get("ext", "png")
            img_path = os.path.join(crops_dir, f"{base_name}_p{page_num}_img{idx}.{ext}")
            try:
                with open(img_path, "wb") as f:
                    f.write(data)
            except Exception:
                continue

            res = self._ocr_image_file(img_path)
            salida.append({
                "text": res["text"],
                "confidence": res["confidence"],
                "index": idx,
                "legible": res["legible"],
            })

        return salida

    # -------------------------------------------------------------- pipeline
    def execute_local(self) -> str:
        pdf_path = self._input_data["pdf_path"]
        file_name = os.path.basename(pdf_path)
        file_stem = os.path.splitext(file_name)[0]

        base_output_dir = os.path.join(
            current_dir, "..", "..",
            self._task_settings["chunks_data_path"],
            file_stem,
            self._get_exec_timestamp(),
        )
        os.makedirs(base_output_dir, exist_ok=True)

        crops_dir = os.path.join(base_output_dir, "crops")
        os.makedirs(crops_dir, exist_ok=True)

        self.chunker = SyntacticChunker(
            chunk_size=int(self._task_settings.get("chunk_size", 1000)),
            chunk_overlap=int(self._task_settings.get("chunk_overlap", 200)),
        )

        use_ocr = bool(self._task_settings.get("baseline_use_ocr", True))
        self.ocr = ElectricalDiagramProcessor(
            use_ocr=use_ocr,
            ocr_confidence_threshold=float(
                self._task_settings.get("ocr_confidence_threshold", 60.0)
            ),
        ) if use_ocr else None

        if use_ocr and self.ocr is not None and not self.ocr.use_ocr:
            logger.warning("OCR pedido pero pytesseract no está disponible: se ingesta solo la capa de texto.")

        stats = {
            "total_pages": 0,
            "pages_text_layer": 0,
            "pages_ocr": 0,
            "pages_empty": 0,
            "pages_empty_list": [],
            "images_ocr_total": 0,
            "images_ocr_legible": 0,
            "images_ocr_discarded": 0,
            "pages_ocr_illegible": 0,
            "chunks_from_text": 0,
            "chunks_from_ocr_page": 0,
            "chunks_from_ocr_image": 0,
            "total_chunks": 0,
            "ocr_enabled": bool(use_ocr and self.ocr is not None and self.ocr.use_ocr),
        }

        seen_hashes: set = set()

        with fitz.open(pdf_path) as doc:
            stats["total_pages"] = len(doc)

            for page_index in range(len(doc)):
                page_num = page_index + 1
                page = doc[page_index]
                page_chunks = []

                native = (page.get_text("text") or "").strip()
                tiene_texto = len(native) >= MIN_PAGE_TEXT_CHARS

                if tiene_texto:
                    stats["pages_text_layer"] += 1
                    for c in self.chunker.chunk(native, page_num=page_num, file_name=file_name):
                        c["extraction_method"] = "text_layer"
                        page_chunks.append(c)
                    stats["chunks_from_text"] += len(page_chunks)

                    # "OCR para las imágenes": rótulos de figuras y diagramas que
                    # la capa de texto no incluye.
                    if stats["ocr_enabled"]:
                        for img in self._ocr_embedded_images(
                            doc, page, page_num, crops_dir, file_stem, seen_hashes
                        ):
                            stats["images_ocr_total"] += 1
                            if not img["legible"]:
                                stats["images_ocr_discarded"] += 1
                                continue
                            stats["images_ocr_legible"] += 1
                            for j, c in enumerate(
                                self.chunker.chunk(img["text"], page_num=page_num,
                                                   file_name=file_name)
                            ):
                                c["chunk_id"] = f"chunk_ocrimg_{img['index']}_{j}"
                                c["extraction_method"] = "ocr_image"
                                c["confidence"] = round(img["confidence"] / 100.0, 3)
                                page_chunks.append(c)
                                stats["chunks_from_ocr_image"] += 1

                else:
                    # Página sin capa de texto: única vía es el OCR.
                    res = (self._ocr_full_page(page, page_num, crops_dir, file_stem)
                           if stats["ocr_enabled"] else
                           {"text": "", "confidence": 0.0, "legible": False})

                    if res["legible"]:
                        stats["pages_ocr"] += 1
                        for c in self.chunker.chunk(res["text"], page_num=page_num, file_name=file_name):
                            c["chunk_id"] = f"chunk_ocr_{c['chunk_id'].split('_')[-1]}"
                            c["extraction_method"] = "ocr_page"
                            c["confidence"] = round(res["confidence"] / 100.0, 3)
                            page_chunks.append(c)
                            stats["chunks_from_ocr_page"] += 1
                    else:
                        # Ni texto ni OCR legible: la página no entra al índice.
                        stats["pages_empty"] += 1
                        stats["pages_empty_list"].append(page_num)
                        if res["text"]:
                            # El OCR devolvió algo, pero era ruido sin frases.
                            stats["pages_ocr_illegible"] += 1

                # ---- persistir ----
                if not page_chunks:
                    continue

                page_output_dir = os.path.join(base_output_dir, f"{file_stem}_{page_num}")
                os.makedirs(page_output_dir, exist_ok=True)

                for chunk in page_chunks:
                    if not str(chunk.get("original_chunk", "")).strip():
                        continue
                    out_path = os.path.join(
                        page_output_dir,
                        f"{file_stem}_{page_num}_{chunk['chunk_id']}.json",
                    )
                    with open(out_path, "w", encoding="utf-8") as f:
                        json.dump(chunk, f, ensure_ascii=False, indent=4)
                    stats["total_chunks"] += 1

        self.stats = stats

        logger.info("=" * 60)
        logger.info("CHUNKING SOLO TEXTO + OCR")
        logger.info("=" * 60)
        logger.info(f"Páginas totales:            {stats['total_pages']}")
        logger.info(f"  con capa de texto:        {stats['pages_text_layer']}")
        logger.info(f"  recuperadas por OCR:      {stats['pages_ocr']}")
        logger.info(f"  sin contenido utilizable: {stats['pages_empty']}")
        logger.info(f"Imágenes OCR:               {stats['images_ocr_legible']} legibles de {stats['images_ocr_total']}")
        logger.info(f"Chunks totales:             {stats['total_chunks']}")

        with open(os.path.join(base_output_dir, "chunking_stats.json"), "w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)

        return base_output_dir
