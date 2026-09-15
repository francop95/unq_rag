"""
Ingesta SOLO TEXTO — línea base para comparar contra el pipeline multimodal
===========================================================================

Corre el mismo documento por un pipeline RAG convencional:

    PDF → capa de texto (PyMuPDF) + OCR → split por longitud → embeddings → Chroma

Solo modelo de TEXTO. Las imágenes entran por OCR (Tesseract), no por un modelo
de visión: de un diagrama se transcriben los rótulos que se puedan leer, sin
ninguna descripción de qué representa ni de cómo se conectan sus partes.

Sin tablas como estructura, sin índice visual CLIP, sin jerarquía. Escribe en un
índice y unas carpetas PROPIAS, así que no toca nada de la ingesta multimodal:
las dos pueden convivir y consultarse por separado.

Uso:
    cd Ingestion
    python src/main_text_baseline.py
    python src/main_text_baseline.py --force      # reingesta aunque no cambie el PDF

Configuración: lee el MISMO `.env` que la ingesta multimodal (una sola API key)
y encima aplica los valores de aislamiento, que se pueden sobrescribir en el
`.env` con estas claves:

    baseline_index_name        (default: baseline_documents)
    baseline_index_path        (default: ./data/chroma_index_baseline/)
    baseline_chunks_path       (default: ./data/chunks_data_baseline/)
    baseline_embeddings_path   (default: ./data/embeddings_data_baseline/)
    baseline_chunk_size        (default: 1000)
    baseline_chunk_overlap     (default: 200)
    baseline_use_ocr           (default: true)
    baseline_ocr_page_zoom     (default: 3.0)
    baseline_use_enrichment    (default: false)

Sobre `baseline_use_enrichment` — define QUÉ mide la comparación:

    false (default) → línea base clásica. La diferencia contra el índice
        multimodal incluye TODO lo que agrega el otro pipeline: visión,
        tablas como estructura, descripción de figuras, contextual retrieval,
        preguntas sintéticas y super-chunks.

    true → mantiene el enriquecimiento por LLM (que es texto puro) y deja
        fuera solo lo multimodal. La diferencia aísla el aporte del modelo de
        visión sobre el mismo contenido: las dos versiones leen los rótulos
        del diagrama por OCR, pero solo una entiende qué representa.

Correr las dos variantes responde preguntas distintas; ninguna es "la
correcta".
"""

import argparse
import hashlib
import json
import os
import re
import sys
import traceback
from datetime import datetime, timezone

from tasks.chunking_task_text import TextChunkingTask
from tasks.embeddings_task_multimodal import ChunksEmbeddings
from tasks.indexing_task_multimodal import AutomaticIndexer
from task import TaskReturnData
from config.config_reader import ConfigReader
from task_utils.llm_json import QuotaExhaustedError

# --- Raíz del proyecto (igual que el pipeline multimodal) ---
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
os.chdir(project_root)

reader = ConfigReader(".env")
config = reader.get_config()

# --- Ajustes propios de la línea base -------------------------------------
BASELINE = {
    "index_name": reader.get("baseline_index_name", "baseline_documents"),
    "index_path": reader.get("baseline_index_path", "./data/chroma_index_baseline/"),
    "chunks_data_path": reader.get("baseline_chunks_path", "./data/chunks_data_baseline/"),
    "embeddings_data_path": reader.get("baseline_embeddings_path", "./data/embeddings_data_baseline/"),
    "chunk_size": reader.get_int("baseline_chunk_size", 1000),
    "chunk_overlap": reader.get_int("baseline_chunk_overlap", 200),
    "baseline_use_ocr": reader.get_bool("baseline_use_ocr", True),
    "baseline_ocr_page_zoom": reader.get_float("baseline_ocr_page_zoom", 3.0),
}
USE_ENRICHMENT = reader.get_bool("baseline_use_enrichment", False)

# Se parte de los settings del pipeline multimodal (traen la API key, el modelo
# de embeddings, batch sizes) y se pisan las rutas y el índice, que es lo que
# mantiene las dos ingestas separadas.
task_settings = config.to_task_settings_dict()
task_settings.update(BASELINE)

MANIFEST_PATH = os.path.join(project_root, "data", "baseline_manifest.json")


def _compute_file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def chunk_sort_key(chunk: dict):
    """Orden documental: (página, orden natural del chunk_id)."""
    page_raw = str(chunk.get("page_num", "0")).split("-")[0].strip()
    try:
        page = int(page_raw)
    except (TypeError, ValueError):
        page = 0

    chunk_id = str(chunk.get("chunk_id", ""))
    natural = tuple(
        (1, int(tok)) if tok.isdigit() else (0, tok)
        for tok in re.split(r"(\d+)", chunk_id) if tok
    )
    return (page, natural)


def build_openai_client(cfg):
    from openai import OpenAI

    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(var, None)

    if cfg.openai.openai_url:
        return OpenAI(api_key=cfg.openai.openai_key, base_url=cfg.openai.openai_url)
    return OpenAI(api_key=cfg.openai.openai_key)


def _load_manifest() -> dict:
    if os.path.exists(MANIFEST_PATH):
        try:
            with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_manifest(manifest: dict) -> None:
    os.makedirs(os.path.dirname(MANIFEST_PATH), exist_ok=True)
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def load_chunks_from_dir(chunks_dir: str) -> list:
    """Lee los JSON que dejó el chunker, en orden documental."""
    chunks = []
    for page_folder in os.listdir(chunks_dir):
        page_folder_path = os.path.join(chunks_dir, page_folder)
        if not os.path.isdir(page_folder_path):
            continue
        for json_file in os.listdir(page_folder_path):
            if not json_file.endswith(".json"):
                continue
            try:
                with open(os.path.join(page_folder_path, json_file), "r", encoding="utf-8") as f:
                    data = json.load(f)
                chunks.extend(data if isinstance(data, list) else [data])
            except Exception as e:
                print(f"   ⚠️  Error cargando {json_file}: {e}")

    chunks.sort(key=chunk_sort_key)
    return chunks


def main():
    ap = argparse.ArgumentParser(description="Ingesta solo texto (línea base)")
    ap.add_argument("--force", action="store_true",
                    help="reingesta los PDF aunque no hayan cambiado")
    args = ap.parse_args()

    pdf_folder = os.path.abspath(config.paths.raw_data_path)

    print(f"\n{'='*70}")
    print("INGESTA SOLO TEXTO (línea base, sin multimodal)")
    print(f"{'='*70}")
    print(f"  PDFs:            {pdf_folder}")
    print(f"  Índice:          {BASELINE['index_name']}")
    print(f"  Ruta del índice: {BASELINE['index_path']}")
    print(f"  Chunk:           {BASELINE['chunk_size']} caracteres "
          f"(solapamiento {BASELINE['chunk_overlap']})")
    print(f"  OCR:             {'sí' if BASELINE['baseline_use_ocr'] else 'no'}")
    print(f"  Enriquecimiento: {'sí' if USE_ENRICHMENT else 'no'}")
    print(f"{'='*70}")

    if not os.path.isdir(pdf_folder):
        print(f"\n❌ No existe la carpeta de PDFs: {pdf_folder}")
        return 1

    processed_docs = 0
    skipped_docs = []
    failed_docs = []
    totals = {"pages": 0, "pages_ocr": 0, "pages_empty": 0,
              "images_ocr": 0, "chunks": 0}
    manifest = _load_manifest()

    for filename in sorted(os.listdir(pdf_folder)):
        pdf_path = os.path.join(pdf_folder, filename)
        if not (os.path.isfile(pdf_path) and filename.lower().endswith(".pdf")):
            continue

        file_stem = os.path.splitext(filename)[0]
        pdf_hash = _compute_file_hash(pdf_path)
        prev = manifest.get(file_stem)
        if not args.force and prev and prev.get("sha256") == pdf_hash and prev.get("status") == "success":
            print(f"⏭️  {file_stem}: sin cambios desde la última ingesta, se omite.")
            skipped_docs.append(file_stem)
            continue

        print(f"\n{'='*60}")
        print(f"🔧 Procesando: {file_stem}")
        print(f"{'='*60}")

        try:
            # ---------- 1) CHUNKING SOLO TEXTO ----------
            chunk_task = TextChunkingTask()
            chunk_task._task_settings = task_settings
            chunk_task._input_data = {"pdf_path": pdf_path}

            chunk_result: TaskReturnData = chunk_task.execute()
            if chunk_result.error:
                print("❌ Chunking error:", chunk_result.error)
                failed_docs.append((file_stem, f"Chunking: {chunk_result.error}"))
                continue

            chunks_dir = chunk_result.payload["chunks"]
            stats = chunk_result.payload["stats"]
            totals["pages"] += stats["total_pages"]
            totals["pages_ocr"] += stats["pages_ocr"]
            totals["pages_empty"] += stats["pages_empty"]
            totals["images_ocr"] += stats["images_ocr_legible"]
            totals["chunks"] += stats["total_chunks"]

            print(f"✅ Chunking OK: {stats['total_chunks']} chunks")
            print(f"   • capa de texto:      {stats['pages_text_layer']}/{stats['total_pages']} páginas")
            if stats["pages_ocr"]:
                print(f"   • recuperadas por OCR: {stats['pages_ocr']} páginas")
            if stats["images_ocr_legible"]:
                print(f"   • imágenes con OCR legible: {stats['images_ocr_legible']}")
            if stats["pages_empty"]:
                paginas = ", ".join(str(p) for p in stats["pages_empty_list"][:12])
                extra = "..." if stats["pages_empty"] > 12 else ""
                print(f"   ⚠️  {stats['pages_empty']} páginas sin contenido utilizable "
                      f"(ni texto ni OCR legible): {paginas}{extra}")

            chunks = load_chunks_from_dir(chunks_dir)
            if not chunks:
                print("   ⚠️  El documento no dejó ningún chunk de texto: se omite.")
                failed_docs.append((file_stem, "sin texto extraíble"))
                continue

            # ---------- 2) ENRIQUECIMIENTO (opcional) ----------
            question_chunks = []
            if USE_ENRICHMENT:
                print("\n🧠 Contextual Retrieval + preguntas sintéticas...")
                try:
                    from task_utils.contextual_enricher import (
                        ContextualEnricher, build_question_chunks, build_document_outline,
                    )
                    enricher = ContextualEnricher(
                        client=build_openai_client(config),
                        model=config.enrichment.enrichment_model,
                        concurrency=config.enrichment.enrichment_concurrency,
                        max_questions=config.enrichment.max_synthetic_questions,
                    )
                    outline = build_document_outline(chunks)
                    ctx_stats = enricher.enrich(chunks, document_outline=outline)
                    print(f"   ✅ {ctx_stats['enriched']} chunks contextualizados, "
                          f"{ctx_stats['questions']} preguntas generadas")
                    if config.enrichment.use_synthetic_questions:
                        question_chunks = build_question_chunks(chunks, set())
                        print(f"   ✅ Vectores extra por preguntas: {len(question_chunks)}")
                except Exception as e:
                    print(f"   ⚠️  Error en enriquecimiento: {e}")
                    traceback.print_exc()

            chunks_for_embedding = chunks + question_chunks
            chunks_file = os.path.join(chunks_dir, "chunks_for_embedding.json")
            with open(chunks_file, "w", encoding="utf-8") as f:
                json.dump(chunks_for_embedding, f, ensure_ascii=False, indent=2)

            # ---------- 3) EMBEDDINGS ----------
            print(f"\n🔢 Embeddings ({len(chunks_for_embedding)} chunks)...")
            emb_task = ChunksEmbeddings()
            emb_task._task_settings = task_settings
            emb_task._input_data = {
                "file_name": f"{file_stem}.pdf",
                "chunks": chunks_file,
            }
            emb_result: TaskReturnData = emb_task.execute()
            if emb_result.error:
                print("❌ Embeddings error:", emb_result.error)
                failed_docs.append((file_stem, f"Embeddings: {emb_result.error}"))
                continue

            embeddings_dir = emb_result.payload["output_path"]
            print(f"   ✅ Vectores en: {embeddings_dir}")

            # ---------- 4) INDEXADO (solo textual, sin CLIP) ----------
            print("\n🔄 Indexando (colección de texto únicamente)...")
            idx_task = AutomaticIndexer()
            idx_task._task_settings = task_settings
            idx_task._input_data = {
                "file_name": f"{file_stem}.pdf",
                "embeddings": embeddings_dir,
            }
            idx_result: TaskReturnData = idx_task.execute()
            if idx_result.error:
                print("❌ Indexer error:", idx_result.error)
                failed_docs.append((file_stem, f"Indexing: {idx_result.error}"))
                continue

            print(f"✅ Indexado en la colección: {idx_result.payload['indexer']}")

            report = {
                "document_id": file_stem,
                "index_name": BASELINE["index_name"],
                "chunking": stats,
                "chunks_indexed": len(chunks),
                "question_chunks_indexed": len(question_chunks),
                "enrichment": USE_ENRICHMENT,
            }
            with open(os.path.join(chunks_dir, "baseline_report.json"), "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)

            processed_docs += 1
            print(f"\n✅ DOCUMENTO COMPLETADO: {file_stem}")

            manifest[file_stem] = {
                "sha256": pdf_hash,
                "last_processed_at": datetime.now(timezone.utc).isoformat(),
                "status": "success",
            }
            _save_manifest(manifest)

        except QuotaExhaustedError as e:
            print(f"\n{'='*70}")
            print(f"⛔ {e}")
            print(f"{'='*70}")
            print("Se aborta el lote: sin crédito no tiene sentido seguir.")
            failed_docs.append((file_stem, str(e)))
            break

        except Exception as e:
            print(f"❌ Error procesando {file_stem}: {e}")
            failed_docs.append((file_stem, str(e)))
            traceback.print_exc()

    # ---------- RESUMEN ----------
    print(f"\n\n{'='*70}")
    print("RESUMEN — INGESTA SOLO TEXTO")
    print(f"{'='*70}")
    print(f"✅ Documentos procesados: {processed_docs}")
    if skipped_docs:
        print(f"⏭️  Omitidos (sin cambios): {len(skipped_docs)}")
        for name in skipped_docs:
            print(f"   • {name}")
    if failed_docs:
        print(f"❌ Con error: {len(failed_docs)}")
        for name, err in failed_docs:
            print(f"   • {name}: {err}")

    if processed_docs:
        print(f"\n📄 Páginas totales:         {totals['pages']}")
        print(f"🔍 Recuperadas por OCR:     {totals['pages_ocr']}")
        print(f"🖼️  Imágenes con OCR legible: {totals['images_ocr']}")
        print(f"🚫 Sin contenido utilizable: {totals['pages_empty']}")
        print(f"🧩 Chunks indexados:        {totals['chunks']}")
        print(f"\n📦 Índice: {BASELINE['index_name']} en {BASELINE['index_path']}")
        print("\nPara comparar las respuestas en el frontend:")
        print("  1) cd API && python app.py                   # :5000 multimodal")
        print("  2) cd API && python app_baseline.py          # :5001 esta versión")
        print("  3) cd Frontend && npm run dev                # :5173 -> :5000")
        print("  4) cd Frontend && VITE_API_BASE_URL=http://localhost:5001 \\")
        print("       npm run dev -- --port 5174              # :5174 -> :5001")
        print("\nPara comparar por métricas:")
        print("  python eval/compare_baseline.py")

    return 1 if failed_docs and not processed_docs else 0


if __name__ == "__main__":
    sys.exit(main())
