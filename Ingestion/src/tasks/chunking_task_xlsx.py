"""
Chunking de libros de Excel (.xlsx)
===================================

El inventario del secadero no es un PDF: es una planilla con dos formas
distintas de contenido conviviendo en el mismo archivo, y tratarlas igual
produce basura.

  1. Hojas tabulares (Inventario, Discrepancias, Conexiones TBEN, ...): un
     par de filas de título, una fila de encabezados y filas de datos donde
     CADA FILA es un registro autocontenido ("I001 | Protecciones |
     Interruptor diferencial general | 40 A; 30 mA | ..."). Acá la fuente ya
     es estructurada: leerla con openpyxl da la tabla exacta, sin el paso de
     visión que el pipeline de PDF necesita para reconstruirla. Pedirle a un
     modelo que "lea" lo que ya tenemos perfecto solo agrega costo y errores.

  2. Hojas de planos (Planos de referencia, Planos rotulados): un título, un
     pie y una IMAGEN embebida. Son cuatro: los dos planos originales y sus
     dos copias rotuladas con los códigos que define la hoja Nomenclatura.
     Esas copias rotuladas son el puente entre la geometría del plano y el
     inventario, y un volcado de celdas las perdería enteras —openpyxl no las
     ve como filas—. Se extraen a archivo y se emiten como chunks de imagen,
     con lo que reciben el mismo tratamiento que un plano de un PDF: pasada
     dedicada del modelo de visión y facetas del procesador de diagramas.

Emite el MISMO esquema de chunk que el pipeline multimodal, así que las
etapas de enriquecimiento, embeddings e indexado se reutilizan sin tocarlas
(igual que chunking_task_text.py).
"""

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import openpyxl

from task import Task, TaskReturnData
from logger import Logger
from task_utils.diagram_processor import ElectricalDiagramProcessor
from task_utils.llm_json import QuotaExhaustedError
from task_utils.validators.task_validators import DocumentExtensionValidator

logger = Logger.get_logger(__name__)
current_dir = os.path.dirname(os.path.abspath(__file__))

# Una fila cuenta como candidata a encabezado si tiene al menos esta cantidad
# de celdas con contenido. Las filas de título de cada hoja tienen una sola
# celda (a veces dos, por culpa de las celdas combinadas), así que dos es el
# corte que las deja afuera sin perder la hoja "Guia de revision", que es
# legítimamente de dos columnas.
MIN_CELDAS_ENCABEZADO = 2

# Presupuesto de caracteres por chunk de tabla. La alternativa —un número fijo
# de filas— agrupa 10 registros del inventario (13 columnas, celdas largas) en
# un bloque de 8000 caracteres donde la consulta "interruptor diferencial"
# arrastra nueve componentes que no tienen nada que ver. Con presupuesto de
# caracteres los chunks quedan parejos sin importar el ancho de la hoja.
PRESUPUESTO_CHUNK_TABLA = 1800

# Filas vacías que pueden separar dos líneas que son una sola idea (el título
# de un plano y su pie). Las celdas combinadas del libro dejan una fila en
# blanco entre ambas, así que uno solo no alcanza.
GAP_BLOQUE_TEXTO = 4


def _texto(celda: Any) -> str:
    """Celda a texto plano, listo para una tabla markdown."""
    if celda is None:
        return ""
    if isinstance(celda, datetime):
        return celda.strftime("%d/%m/%Y")
    # El salto de línea rompe la fila de una tabla markdown y no aporta nada al
    # texto plano. El pipe se escapa recién al armar la tabla: hacerlo acá se
    # filtraba a los chunks de texto y a los pies de los planos.
    return str(celda).strip().replace("\n", " · ").replace("\r", "")


def _fila_a_markdown(celdas: List[str]) -> str:
    return "| " + " | ".join(c.replace("|", "\\|") for c in celdas) + " |"


class XlsxChunkingTask(Task):
    """
    Libro de Excel → chunks (tablas + imágenes embebidas).

    Settings que usa:
        chunks_data_path (str):   carpeta base de salida
        max_table_rows (int):     tope de filas por chunk de tabla
        open_ai_key / open_ai_url:  para la pasada dedicada de figuras
        multimodal_model (str):   modelo de visión de esa pasada
        use_dedicated_figure_pass (bool)
        use_ocr_for_diagrams (bool), ocr_confidence_threshold (float)
    """

    name = "XlsxChunking"
    _INPUT_VALIDATORS = [DocumentExtensionValidator("xlsx_path", ".xlsx")]

    def __init__(self):
        super().__init__()
        self.stats: Dict[str, Any] = {}

    def validate(self):
        errores = [v.validate(self._input_data) for v in self._INPUT_VALIDATORS]
        return [e for e in errores if e is not None]

    def execute(self) -> TaskReturnData:
        try:
            chunks_dir = self.execute_local()
            return TaskReturnData(payload={"chunks": chunks_dir, "stats": self.stats})
        except QuotaExhaustedError:
            # Se propaga: un error de crédito es de la corrida, no de este
            # documento. Envuelto en TaskReturnData, el lote sigue al siguiente
            # y falla ahí también, uno por uno. Ver chunking_task_multimodal.
            logger.error("Sin crédito en la cuenta de OpenAI: se aborta el lote")
            raise
        except Exception as ex:
            logger.exception("XlsxChunkingTask falló")
            return TaskReturnData(error=str(ex))

    @staticmethod
    def _get_exec_timestamp() -> str:
        return datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    # ------------------------------------------------------------ estructura
    @staticmethod
    def _filas_utiles(ws) -> List[Dict[str, Any]]:
        """
        Filas no vacías de la hoja, con su índice crudo.

        El índice crudo hace falta para ubicar las imágenes: vienen ancladas a
        una fila, y el pie que las describe está en la fila no vacía anterior.
        """
        salida = []
        for indice, fila in enumerate(ws.iter_rows(values_only=True)):
            celdas = [_texto(c) for c in fila]
            # Recorta columnas vacías del final (Excel guarda muchas de más)
            while celdas and not celdas[-1]:
                celdas.pop()
            if any(celdas):
                salida.append({"fila": indice, "celdas": celdas})
        return salida

    @staticmethod
    def _detectar_encabezado(filas: List[Dict[str, Any]]) -> Optional[int]:
        """
        Posición (dentro de `filas`) de la fila de encabezados, o None si la
        hoja no es tabular.

        No alcanza con "la primera fila con varias celdas llenas": en las hojas
        de planos, las celdas combinadas hacen que el título aparezca con dos
        celdas y se confundiría con un encabezado. Un encabezado de verdad
        viene seguido inmediatamente de filas de datos igual de anchas, así que
        eso es lo que se exige.
        """
        for i, fila in enumerate(filas[:-2]):
            llenas = sum(1 for c in fila["celdas"] if c)
            if llenas < MIN_CELDAS_ENCABEZADO:
                continue
            siguientes = filas[i + 1:i + 3]
            if len(siguientes) < 2:
                return None
            if all(sum(1 for c in f["celdas"] if c) >= MIN_CELDAS_ENCABEZADO
                   for f in siguientes):
                return i
        return None

    # --------------------------------------------------------------- chunks
    def _chunks_de_hoja_tabular(self, filas, pos_encabezado, base) -> List[Dict[str, Any]]:
        chunks: List[Dict[str, Any]] = []
        contador = 0

        encabezados = filas[pos_encabezado]["celdas"]
        ancho = len(encabezados)

        # Las filas por encima del encabezado son el título y el subtítulo de la
        # hoja: dicen para qué sirve y bajo qué criterio se armó. Van como chunk
        # de texto propio, porque es el contexto que ninguna fila de datos trae.
        preface = [" — ".join(c for c in f["celdas"] if c)
                   for f in filas[:pos_encabezado]]
        preface = [p for p in preface if p]
        if preface:
            contador += 1
            chunks.append(self._chunk(base, contador, "text", "\n".join(preface)))

        separador = _fila_a_markdown(["---"] * ancho)
        cabecera_md = _fila_a_markdown(encabezados) + "\n" + separador
        tope_filas = max(1, int(self._task_settings.get("max_table_rows", 10)))

        lote: List[List[str]] = []
        largo = 0

        def volcar():
            nonlocal lote, largo, contador
            if not lote:
                return
            contador += 1
            cuerpo = "\n".join(_fila_a_markdown(f) for f in lote)
            payload = {
                "table_markdown": cabecera_md + "\n" + cuerpo,
                "table_json": {"rows": [encabezados] + lote},
                "bbox": None,
                "image_path": None,
            }
            chunks.append(self._chunk(base, contador, "table",
                                      json.dumps(payload, ensure_ascii=False)))
            lote, largo = [], 0

        for fila in filas[pos_encabezado + 1:]:
            celdas = (fila["celdas"] + [""] * ancho)[:ancho]
            md = _fila_a_markdown(celdas)
            if lote and (largo + len(md) > PRESUPUESTO_CHUNK_TABLA or len(lote) >= tope_filas):
                volcar()
            lote.append(celdas)
            largo += len(md)
        volcar()

        return chunks

    def _chunks_de_hoja_de_planos(self, ws, filas, base, crops_dir) -> List[Dict[str, Any]]:
        """
        Hoja sin tabla: bloques de texto sueltos e imágenes embebidas.

        Cada imagen se asocia al último bloque de texto que quedó por encima
        —el título y su pie— porque es la única descripción que el libro trae
        de ella, y sin eso el modelo de visión recibe un plano sin saber si
        está mirando el original o la copia rotulada.
        """
        chunks: List[Dict[str, Any]] = []
        contador = 0

        # El título de un plano y su pie viven en filas separadas pero son una
        # sola idea ("Distribución eléctrica | Códigos de referencia" +
        # "Copia rotulada de F01..."). Emitidos por separado, el título queda en
        # 40 caracteres y el filtro de calidad lo descarta por corto; juntos
        # forman el bloque que efectivamente describe la imagen que sigue.
        bloques: List[Dict[str, Any]] = []
        for f in filas:
            texto = " — ".join(c for c in f["celdas"] if c)
            if not texto:
                continue
            if bloques and f["fila"] - bloques[-1]["fila_fin"] <= GAP_BLOQUE_TEXTO:
                bloques[-1]["texto"] += " — " + texto
                bloques[-1]["fila_fin"] = f["fila"]
            else:
                bloques.append({"fila": f["fila"], "fila_fin": f["fila"], "texto": texto})

        for b in bloques:
            contador += 1
            chunks.append(self._chunk(base, contador, "text", b["texto"]))

        for i, imagen in enumerate(getattr(ws, "_images", []) or []):
            try:
                fila_ancla = imagen.anchor._from.row
            except AttributeError:
                fila_ancla = 10 ** 9

            previos = [b["texto"] for b in bloques if b["fila"] <= fila_ancla]
            leyenda = previos[-1] if previos else ""

            formato = (getattr(imagen, "format", None) or "png").lower()
            destino = os.path.join(
                crops_dir, f"{base['stem']}_{base['page_num']}_img{i + 1}.{formato}"
            )
            try:
                with open(destino, "wb") as fh:
                    fh.write(imagen._data())
            except Exception as e:
                logger.warning(f"No se pudo extraer la imagen {i + 1} de «{base['sheet']}»: {e}")
                continue

            contador += 1
            self.stats["images_extracted"] = self.stats.get("images_extracted", 0) + 1
            payload = {
                "bbox": None,
                "image_path": destino,
                # `notes` es de donde el resto del pipeline lee la
                # representación textual de una imagen. La pasada dedicada de
                # figuras lo reemplaza por su descripción estructurada; esto es
                # lo que queda si esa pasada está apagada o falla.
                "notes": leyenda,
            }
            chunks.append(self._chunk(base, contador, "image",
                                      json.dumps(payload, ensure_ascii=False)))

        return chunks

    @staticmethod
    def _chunk(base: Dict[str, Any], numero: int, content_type: str,
               original_chunk: str) -> Dict[str, Any]:
        return {
            "file_name": base["file_name"],
            "page_num": base["page_num"],
            "chunk_id": f"chunk_{numero}",
            "page_metadata": f"hoja «{base['sheet']}»",
            "original_chunk": original_chunk,
            "content_type": content_type,
            # La hoja hace de sección: es lo que da sentido a una fila suelta
            # ("D01 | Alta | ..." no significa nada sin "Discrepancias").
            "document_section": base["sheet"],
            "hierarchy_path": f"{base['stem']} > {base['sheet']}",
        }

    # ------------------------------------------------------------- pipeline
    def execute_local(self) -> str:
        xlsx_path = self._input_data["xlsx_path"]
        file_name = os.path.basename(xlsx_path)
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

        stats = {
            "total_sheets": 0,
            "sheets_tabular": 0,
            "sheets_non_tabular": 0,
            "sheets_empty": 0,
            "sheets_empty_list": [],
            "images_extracted": 0,
            "chunks_table": 0,
            "chunks_text": 0,
            "chunks_image": 0,
            "total_chunks": 0,
        }
        self.stats = stats

        # data_only=True devuelve el valor calculado de las fórmulas en vez de
        # la fórmula. Si el libro se guardó sin cachear resultados quedarían en
        # None; se avisa más abajo si una hoja entera sale vacía.
        wb = openpyxl.load_workbook(xlsx_path, data_only=True)

        # Clave: número de hoja (hace de "página"). Es lo que espera la pasada
        # dedicada de figuras, que se reutiliza tal cual del pipeline de PDF.
        chunks_por_hoja: Dict[int, List[Dict[str, Any]]] = {}

        for indice, ws in enumerate(wb.worksheets, start=1):
            stats["total_sheets"] += 1
            base = {
                "file_name": file_name,
                "stem": file_stem,
                "sheet": ws.title,
                "page_num": str(indice),
            }

            filas = self._filas_utiles(ws)
            imagenes = len(getattr(ws, "_images", []) or [])
            if not filas and not imagenes:
                stats["sheets_empty"] += 1
                stats["sheets_empty_list"].append(ws.title)
                continue

            pos = self._detectar_encabezado(filas)
            if pos is not None:
                stats["sheets_tabular"] += 1
                chunks = self._chunks_de_hoja_tabular(filas, pos, base)
            else:
                stats["sheets_non_tabular"] += 1
                chunks = self._chunks_de_hoja_de_planos(ws, filas, base, crops_dir)

            chunks_por_hoja[indice] = chunks
            logger.info(
                f"Hoja «{ws.title}» ({'tabular' if pos is not None else 'planos'}): "
                f"{len(chunks)} chunks, {imagenes} imágenes"
            )

        # ---- pasada dedicada del modelo de visión sobre los planos ----
        # Se reutiliza el método del pipeline multimodal en vez de duplicarlo:
        # es el mismo trabajo (recorte aislado + prompt enfocado) y mantener dos
        # copias del prompt garantiza que se desincronicen.
        if self._task_settings.get("use_dedicated_figure_pass", True):
            imagenes_totales = sum(
                1 for cs in chunks_por_hoja.values() for c in cs
                if c["content_type"] == "image"
            )
            if imagenes_totales:
                from tasks.chunking_task_multimodal import ChunkingTask

                ayudante = ChunkingTask()
                ayudante._task_settings = self._task_settings
                ayudante._enrich_figures_and_tables(
                    page_chunks=chunks_por_hoja,
                    client=ayudante._get_openai_client(),
                    model=self._task_settings.get("multimodal_model", "gpt-4o"),
                    stats=stats,
                )

        # ---- facetas de diagrama (OCR + estructura) sobre los planos ----
        procesador = ElectricalDiagramProcessor(
            use_ocr=bool(self._task_settings.get("use_ocr_for_diagrams", True)),
            ocr_confidence_threshold=float(
                self._task_settings.get("ocr_confidence_threshold", 60.0)
            ),
        )
        for indice, chunks in list(chunks_por_hoja.items()):
            expandidos: List[Dict[str, Any]] = []
            for chunk in chunks:
                if procesador.is_diagram(chunk):
                    facetas = procesador.create_enhanced_diagram_chunks(chunk)
                    expandidos.extend(facetas)
                    logger.info(
                        f"  ✨ Plano mejorado → {len(facetas)} chunks "
                        f"({', '.join(c.get('content_type', '?') for c in facetas)})"
                    )
                else:
                    expandidos.append(chunk)
            chunks_por_hoja[indice] = expandidos

        # ---- enlace prev/next entre chunks consecutivos ----
        # Habilita la expansión de contexto en retrieval: una fila del
        # inventario recupera también las de al lado, que es donde suele estar
        # el resto del subsistema.
        ordenados: List[Dict[str, Any]] = []
        for indice in sorted(chunks_por_hoja):
            ordenados.extend(chunks_por_hoja[indice])

        def _id_compuesto(c: Dict[str, Any]) -> str:
            return f"{c.get('file_name','')}_{c.get('page_num','')}_{c.get('chunk_id','')}"

        def _grupo_figura(c: Dict[str, Any]) -> Optional[str]:
            """Las facetas de un mismo plano no son vecinas entre sí."""
            chunk_id = str(c.get("chunk_id", ""))
            for sufijo in ("_ocr", "_structured", "_visual"):
                if chunk_id.endswith(sufijo):
                    return f"{c.get('file_name','')}_{c.get('page_num','')}_{chunk_id[:-len(sufijo)]}"
            return None

        for i, chunk in enumerate(ordenados):
            grupo = _grupo_figura(chunk)
            anterior = i - 1
            while anterior >= 0 and grupo is not None and _grupo_figura(ordenados[anterior]) == grupo:
                anterior -= 1
            siguiente = i + 1
            while (siguiente < len(ordenados) and grupo is not None
                   and _grupo_figura(ordenados[siguiente]) == grupo):
                siguiente += 1
            chunk["prev_chunk_id"] = _id_compuesto(ordenados[anterior]) if anterior >= 0 else ""
            chunk["next_chunk_id"] = (_id_compuesto(ordenados[siguiente])
                                      if siguiente < len(ordenados) else "")

        # ---- persistir ----
        for chunk in ordenados:
            page_dir = os.path.join(base_output_dir, f"{file_stem}_{chunk['page_num']}")
            os.makedirs(page_dir, exist_ok=True)
            if not str(chunk.get("original_chunk", "")).strip():
                continue
            destino = os.path.join(
                page_dir, f"{file_stem}_{chunk['page_num']}_{chunk['chunk_id']}.json"
            )
            with open(destino, "w", encoding="utf-8") as fh:
                json.dump(chunk, fh, ensure_ascii=False, indent=4)

            stats["total_chunks"] += 1
            tipo = str(chunk.get("content_type", ""))
            if tipo == "table":
                stats["chunks_table"] += 1
            elif tipo == "image" or tipo.startswith("diagram_"):
                # El procesador de diagramas reemplaza 'image' por sus facetas
                # ('diagram_visual', 'diagram_ocr'), así que contar solo 'image'
                # daba cero con los cuatro planos ya escritos en disco.
                stats["chunks_image"] += 1
            else:
                stats["chunks_text"] += 1

        self.stats = stats

        logger.info("=" * 60)
        logger.info("CHUNKING DE EXCEL")
        logger.info("=" * 60)
        logger.info(f"Hojas:                {stats['total_sheets']} "
                    f"({stats['sheets_tabular']} tabulares, "
                    f"{stats['sheets_non_tabular']} de planos)")
        logger.info(f"Chunks de tabla:      {stats['chunks_table']}")
        logger.info(f"Chunks de imagen:     {stats['chunks_image']}")
        logger.info(f"Chunks de texto:      {stats['chunks_text']}")
        logger.info(f"Chunks totales:       {stats['total_chunks']}")
        if stats["sheets_empty_list"]:
            logger.warning(f"Hojas sin contenido: {', '.join(stats['sheets_empty_list'])}")

        with open(os.path.join(base_output_dir, "chunking_stats.json"), "w", encoding="utf-8") as fh:
            json.dump(stats, fh, ensure_ascii=False, indent=2)

        return base_output_dir
