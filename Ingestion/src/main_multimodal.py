import os
import re
import hashlib
from datetime import datetime, timezone
from tasks.chunking_task_multimodal import ChunkingTask
from tasks.embeddings_task_multimodal import ChunksEmbeddings
from tasks.indexing_task_dual import DualIndexer
from task import TaskReturnData
from config.config_reader import load_config
from task_utils.chunk_quality import ChunkQualityPipeline
from task_utils.ingestion_quality import IngestionQualityAnalyzer
from task_utils.technical_validators import TechnicalDocumentValidator
from task_utils.semantic_rechunker import SemanticRechunker
from task_utils.contextual_enricher import (
    ContextualEnricher, build_question_chunks, build_document_outline,
)
from task_utils.llm_json import QuotaExhaustedError
import json
import traceback

# --- Calcular ruta raíz del proyecto ---
current_dir = os.path.dirname(os.path.abspath(__file__))  # /path/to/src
project_root = os.path.dirname(current_dir)  # /path/to/Ingestion
os.chdir(project_root)  # Cambiar a directorio raíz

# --- Cargar configuración desde .env ---
config = load_config(".env")

# Preparar task_settings para pasar a los tasks
# Ahora todas las configuraciones vienen del .env
task_settings = config.to_task_settings_dict()

# --- Idempotencia a nivel de documento ---
# Manifest que registra el hash del último PDF procesado exitosamente por
# file_stem. Evita re-chunkear con LLM y re-embeber un documento que no
# cambió desde la última corrida exitosa del batch.
MANIFEST_PATH = os.path.join(project_root, "data", "ingestion_manifest.json")


def _compute_file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def build_openai_client(cfg):
    """Cliente OpenAI para las etapas de enriquecimiento (misma config que los tasks)."""
    from openai import OpenAI

    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(var, None)

    if cfg.openai.openai_url:
        return OpenAI(api_key=cfg.openai.openai_key, base_url=cfg.openai.openai_url)
    return OpenAI(api_key=cfg.openai.openai_key)


def load_chunk_vectors(chunks: list, embeddings_dir: str):
    """
    Matriz de embeddings alineada con `chunks`, leída de la salida del task de
    embeddings. Se usa para que el re-chunking semántico agrupe por significado.

    Devuelve None si falta el vector de algún chunk de TEXTO (los únicos que se
    fusionan en super-chunks): en ese caso el rechunker cae a proximidad de
    páginas, que es correcto aunque más pobre. Las imágenes sin descripción no
    tienen vector y no invalidan la matriz.
    """
    import numpy as np

    if not os.path.isdir(embeddings_dir):
        return None

    vectors_by_key = {}
    dim = 0
    for page_folder in os.listdir(embeddings_dir):
        page_path = os.path.join(embeddings_dir, page_folder)
        if not os.path.isdir(page_path):
            continue
        for file_name in os.listdir(page_path):
            if not file_name.endswith(".json"):
                continue
            try:
                with open(os.path.join(page_path, file_name), "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
            vector = data.get("text_vector")
            if not isinstance(vector, list) or not vector:
                continue
            key = (str(data.get("page_num", "")), str(data.get("chunk_id", "")))
            vectors_by_key[key] = vector
            dim = max(dim, len(vector))

    if not dim:
        return None

    matrix = []
    for chunk in chunks:
        key = (str(chunk.get("page_num", "")), str(chunk.get("chunk_id", "")))
        vector = vectors_by_key.get(key)
        if vector is None:
            # Solo bloquea si es un chunk de texto (candidato a fusionarse)
            if str(chunk.get("content_type", "text")).lower() == "text":
                return None
            vector = [0.0] * dim
        matrix.append(vector)

    return np.array(matrix, dtype=float)


def chunk_sort_key(chunk: dict):
    """
    Clave de orden documental de un chunk: (página, orden natural del chunk_id).

    page_num puede venir como "8" o como rango "8-11" (super-chunks): se usa el
    inicio. chunk_id se ordena de forma natural (chunk_2 antes de chunk_10) y
    tolera sufijos (chunk_3_visual, chunk_3_table_part2, chunk_nativeimg_1).
    """
    page_raw = str(chunk.get("page_num", "0")).split("-")[0].strip()
    try:
        page = int(page_raw)
    except (TypeError, ValueError):
        page = 0

    chunk_id = str(chunk.get("chunk_id", ""))
    # Trocea en partes numéricas y no numéricas para comparar sin mezclar tipos
    natural = tuple(
        (1, int(tok)) if tok.isdigit() else (0, tok)
        for tok in re.split(r"(\d+)", chunk_id) if tok
    )
    return (page, natural)


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


# --- Ejecutar tarea ---
if __name__ == "__main__":
    PDF_FOLDER = os.path.abspath(config.paths.raw_data_path)
    processed_docs = 0
    skipped_docs = []
    failed_docs = []
    manifest = _load_manifest()

    for filename in os.listdir(PDF_FOLDER):
        full_path = os.path.join(PDF_FOLDER, filename)
        if os.path.isfile(full_path) and filename.lower().endswith(".pdf"):
            PDF_PATH=full_path
            file_stem = os.path.splitext(os.path.basename(PDF_PATH))[0]

            pdf_hash = _compute_file_hash(PDF_PATH)
            prev_entry = manifest.get(file_stem)
            if prev_entry and prev_entry.get("sha256") == pdf_hash and prev_entry.get("status") == "success":
                print(f"⏭️  {file_stem}: sin cambios desde la última ingesta exitosa, se omite.")
                skipped_docs.append(file_stem)
                continue

            print(f"\n{'='*60}")
            print(f"🔧 Procesando: {file_stem}")
            print(f"{'='*60}")

            try:
                input_data = {
                            "pdf_path": PDF_PATH
                        }
                # --------------------
                # 1) CHUNKING (PDF → chunks)
                # --------------------
                chunk_task = ChunkingTask()
                chunk_task._task_settings = task_settings
                chunk_task._input_data = {"pdf_path": PDF_PATH}

                chunk_result: TaskReturnData = chunk_task.execute()
                if chunk_result.error:
                    print("❌ Chunking error:", chunk_result.error)
                    failed_docs.append((file_stem, f"Chunking: {chunk_result.error}"))
                    continue

                
                chunks_dir = chunk_result.payload["chunks"]
                print(f"✅ Chunking OK. Carpeta: {chunks_dir}")

                # --------------------
                # 1.5) VALIDACIÓN Y ENRIQUECIMIENTO
                # --------------------
                print("\n🔍 Validando y enriqueciendo chunks...")
                
                # Cargar chunks crudos desde JSON (recursivamente en subcarpetas)
                raw_chunks = []
                for page_folder in os.listdir(chunks_dir):
                    page_folder_path = os.path.join(chunks_dir, page_folder)
                    if os.path.isdir(page_folder_path):
                        for json_file in os.listdir(page_folder_path):
                            if json_file.endswith('.json'):
                                try:
                                    with open(os.path.join(page_folder_path, json_file), 'r', encoding='utf-8') as f:
                                        chunk_data = json.load(f)
                                        if isinstance(chunk_data, list):
                                            raw_chunks.extend(chunk_data)
                                        else:
                                            raw_chunks.append(chunk_data)
                                except Exception as e:
                                    print(f"   ⚠️  Error cargando {json_file}: {e}")

                # Restaurar el orden documental: os.listdir devuelve un orden
                # arbitrario del filesystem, y varios pasos posteriores dependen
                # del orden real de lectura del documento (el re-chunking semántico
                # agrupa por proximidad de páginas, y el enricher numera
                # chunk_index). Sin este sort se generaban super-chunks que unían
                # páginas inconexas (ej. página 106 con la 1).
                raw_chunks.sort(key=chunk_sort_key)

                print(f"   📊 Chunks cargados: {len(raw_chunks)}")
                
                # 1.5.1) Validación técnica de dominio
                print("\n🔬 Validación técnica de chunks...")
                technical_validator = TechnicalDocumentValidator()
                tech_validated_chunks, tech_report = technical_validator.validate_chunks(raw_chunks)
                
                print(
                    f"   ✅ Conservados: {tech_report['valid']}/{tech_report['total']} "
                    f"({tech_report['invalid']} descartados por contenido inutilizable, "
                    f"{tech_report.get('with_warnings', 0)} con advertencias no bloqueantes)"
                )
                if tech_report['warnings_by_type']:
                    print(f"   ⚠️  Advertencias técnicas (no descartan el chunk):")
                    for warning_type, count in tech_report['warnings_by_type'].items():
                        print(f"      • {warning_type}: {count}")
                
                # 1.5.2) Validación de calidad general
                quality_pipeline = ChunkQualityPipeline(
                    min_chunk_length=config.hybrid.min_chunk_length,
                    max_chunk_length=config.hybrid.max_chunk_length,
                    similarity_threshold=config.hybrid.similarity_threshold
                )
                
                validated_chunks, validation_report = quality_pipeline.process(
                    tech_validated_chunks,
                    source_file=os.path.basename(PDF_PATH),
                    document_id=file_stem
                )
                
                dedup_report = validation_report['deduplication']
                print(f"   📊 Chunks totales: {len(raw_chunks)}")
                print(f"   ✅ Chunks técnicamente válidos: {tech_report['valid']}")
                print(f"   ✅ Chunks finales válidos: {validation_report['validation']['valid']}")
                print(f"   ❌ Chunks rechazados (longitud/confianza): {validation_report['validation']['invalid']}")
                print(
                    f"   🔄 Duplicados removidos: "
                    f"{dedup_report['duplicates_removed']} exactos + "
                    f"{dedup_report.get('similarity_removed', 0)} por similitud"
                )
                print(f"   📈 Retención de calidad: {validation_report['summary']['quality_retention']}")
                
                validated_chunks_file = os.path.join(chunks_dir, "validated_chunks.json")
                with open(validated_chunks_file, 'w', encoding='utf-8') as f:
                    json.dump(validated_chunks, f, ensure_ascii=False, indent=2)
                
                validation_report_file = os.path.join(chunks_dir, "validation_report.json")
                with open(validation_report_file, 'w', encoding='utf-8') as f:
                    json.dump(validation_report, f, ensure_ascii=False, indent=2)
                
                # Guardar reporte de validación técnica
                technical_report_file = os.path.join(chunks_dir, "technical_validation_report.json")
                with open(technical_report_file, 'w', encoding='utf-8') as f:
                    json.dump(tech_report, f, ensure_ascii=False, indent=2)

                # --------------------
                # 2) CONTEXTUAL RETRIEVAL + PREGUNTAS SINTÉTICAS
                # --------------------
                # Una llamada LLM por chunk que devuelve (a) 1-2 frases que sitúan
                # el chunk en su documento, que se prependen antes de embeber, y
                # (b) preguntas que ese chunk responde, que se indexan como
                # vectores adicionales apuntando al mismo contenido.
                question_chunks = []
                # Compartido entre la pasada de chunks y la de super-chunks: dos
                # preguntas con el mismo texto producen el mismo embedding y el
                # desempate entre ellas queda al azar.
                seen_questions: set = set()
                if config.enrichment.use_contextual_retrieval:
                    print("\n🧠 Contextual Retrieval + preguntas sintéticas...")
                    try:
                        enricher = ContextualEnricher(
                            client=build_openai_client(config),
                            model=config.enrichment.enrichment_model,
                            concurrency=config.enrichment.enrichment_concurrency,
                            max_questions=config.enrichment.max_synthetic_questions,
                        )
                        outline = build_document_outline(validated_chunks)
                        ctx_stats = enricher.enrich(validated_chunks, document_outline=outline)
                        print(
                            f"   ✅ {ctx_stats['enriched']} chunks contextualizados, "
                            f"{ctx_stats['questions']} preguntas generadas"
                            + (f", {ctx_stats['failed']} fallos" if ctx_stats['failed'] else "")
                        )
                        if config.enrichment.use_synthetic_questions:
                            question_chunks = build_question_chunks(validated_chunks, seen_questions)
                            print(f"   ✅ Vectores extra por preguntas: {len(question_chunks)}")
                    except Exception as e:
                        print(f"   ⚠️  Error en enriquecimiento contextual: {e}")
                        traceback.print_exc()
                        enricher = None
                else:
                    enricher = None

                # --------------------
                # 3) EMBEDDINGS (pasada 1: base para el clustering semántico)
                # --------------------
                # El re-chunking semántico necesita vectores para agrupar por
                # significado. Antes se le pasaba embeddings=None y quedaba
                # degradado a "páginas contiguas", dejando muerto el camino de
                # clustering por similitud.
                base_chunks_file = os.path.join(chunks_dir, "chunks_base.json")
                with open(base_chunks_file, 'w', encoding='utf-8') as f:
                    json.dump(validated_chunks, f, ensure_ascii=False, indent=2)

                print("\n🔢 Embeddings (pasada 1: base para clustering)...")
                emb1_task = ChunksEmbeddings()
                emb1_task._task_settings = task_settings
                emb1_task._input_data = {
                        "file_name": f"{file_stem}.pdf",
                        "chunks": base_chunks_file,
                        }
                emb1_result: TaskReturnData = emb1_task.execute()
                if emb1_result.error:
                    print("❌ Embeddings error:", emb1_result.error)
                    failed_docs.append((file_stem, f"Embeddings: {emb1_result.error}"))
                    continue
                base_embeddings_dir = emb1_result.payload["output_path"]
                print(f"   ✅ Vectores base en: {base_embeddings_dir}")

                # --------------------
                # 4) RE-CHUNKING SEMÁNTICO CROSS-PAGE (con vectores reales)
                # --------------------
                print("\n🔗 Analizando chunks relacionados (re-chunking semántico)...")
                superchunks = []
                try:
                    rechunker = SemanticRechunker(
                        similarity_threshold=config.hybrid.semantic_similarity_threshold,
                        max_pages_gap=config.hybrid.max_pages_gap,
                        max_superchunk_size=config.hybrid.max_superchunk_size
                    )

                    base_vectors = load_chunk_vectors(validated_chunks, base_embeddings_dir)
                    if base_vectors is not None:
                        print("   🧭 Agrupando por similitud semántica real")
                    else:
                        print("   ℹ️  Sin vectores completos: se agrupa por proximidad de páginas")

                    _, superchunks = rechunker.process(validated_chunks, embeddings=base_vectors)

                    if superchunks:
                        print(f"   ✅ Super-chunks creados: {len(superchunks)}")
                        superchunks_file = os.path.join(chunks_dir, "superchunks.json")
                        with open(superchunks_file, 'w', encoding='utf-8') as f:
                            json.dump(superchunks, f, ensure_ascii=False, indent=2)
                    else:
                        print(f"   ℹ️  No se crearon super-chunks (contenido muy fragmentado)")

                except Exception as e:
                    print(f"   ⚠️  Error en re-chunking semántico: {e}")
                    traceback.print_exc()

                # --------------------
                # 4.1) ENRIQUECER LOS SUPER-CHUNKS
                # --------------------
                # Los super-chunks nacen DESPUÉS del enriquecimiento, así que quedaban
                # afuera de las dos técnicas que traen la mayor parte del recall: sin
                # prefijo de contexto y sin un solo vector de pregunta (medido: 0 de 30
                # tenían context_summary y 0 tenían preguntas). Como ~70% de lo que
                # llega al top-10 entra por una pregunta sintética, competían con una
                # mano atada. Son pocos (30), así que la pasada extra es barata.
                if superchunks and enricher is not None:
                    print("\n🧠 Contextual Retrieval sobre los super-chunks...")
                    try:
                        sc_stats = enricher.enrich(superchunks, document_outline=outline)
                        print(
                            f"   ✅ {sc_stats['enriched']} super-chunks contextualizados, "
                            f"{sc_stats['questions']} preguntas generadas"
                            + (f", {sc_stats['failed']} fallos" if sc_stats['failed'] else "")
                        )
                        if config.enrichment.use_synthetic_questions:
                            sc_questions = build_question_chunks(superchunks, seen_questions)
                            question_chunks.extend(sc_questions)
                            print(f"   ✅ Vectores extra por preguntas: {len(sc_questions)}")
                    except Exception as e:
                        print(f"   ⚠️  Error enriqueciendo super-chunks: {e}")
                        traceback.print_exc()

                # Consolidado final que se indexa: chunks validados + super-chunks
                # + un vector por pregunta sintética.
                chunks_for_embedding = validated_chunks + superchunks + question_chunks
                chunks_for_embedding_file = os.path.join(chunks_dir, "chunks_for_embedding.json")
                with open(chunks_for_embedding_file, 'w', encoding='utf-8') as f:
                    json.dump(chunks_for_embedding, f, ensure_ascii=False, indent=2)

                # --------------------
                # 5) EMBEDDINGS (pasada 2: set final)
                # --------------------
                # Se re-embeben los chunks base junto a los nuevos para que el
                # indexer lea una sola carpeta coherente. Re-embeber la base es
                # barato (text-embedding-3-large) y evita fusionar dos carpetas.
                print("\n🔢 Embeddings (pasada 2: set final)...")
                emb_task = ChunksEmbeddings()
                emb_task._task_settings = task_settings
                emb_task._input_data = {
                        "file_name": f"{file_stem}.pdf",
                        "chunks": chunks_for_embedding_file,
                        }

                emb_result: TaskReturnData = emb_task.execute()
                if emb_result.error:
                    print("❌ Embeddings error:", emb_result.error)
                    failed_docs.append((file_stem, f"Embeddings: {emb_result.error}"))
                    continue

                print("✅ Embeddings OK.")
                print("   Carpeta de salida:", emb_result.payload["output_path"])
                embeddings_dir = emb_result.payload["output_path"]

                # --------------------
                # 4) INDEXING DUAL (Textual + Visual)
                # --------------------
                print("\n🔄 Indexando con sistema dual (texto + visual)...")
                idx_task = DualIndexer()
                idx_task._task_settings = task_settings
                idx_task._input_data = {
                                        "file_name": f"{file_stem}.pdf",
                                        "embeddings": embeddings_dir
                                    }

                res: TaskReturnData = idx_task.execute()
                if res.error:
                    print("❌ Indexer error:", res.error)
                    failed_docs.append((file_stem, f"Indexing: {res.error}"))
                    continue
                
                payload = res.payload
                print(f"✅ Indexado dual completado:")
                print(f"   • Índice textual: {payload.get('text_indexer', 'N/A')} ({payload.get('total_text', 0)} docs)")
                print(f"   • Índice visual: {payload.get('visual_indexer', 'N/A')} ({payload.get('total_visual', 0)} docs)")
                print(f"   • Textos indexados: {payload.get('text_chunks', 0)}")
                print(f"   • Tablas indexadas (con searchable_text): {payload.get('tables_indexed', 0)}")
                print(f"   • Descripciones de imágenes indexadas: {payload.get('image_descriptions_indexed', 0)}")
                print(f"   • Imágenes indexadas (CLIP): {payload.get('images_indexed', 0)}")

                multimodal_report = {
                    "document_id": file_stem,
                    "text_indexer": payload.get("text_indexer"),
                    "visual_indexer": payload.get("visual_indexer"),
                    "text_chunks_indexed": payload.get("text_chunks", 0),
                    "tables_indexed": payload.get("tables_indexed", 0),
                    "image_descriptions_indexed": payload.get("image_descriptions_indexed", 0),
                    "images_indexed": payload.get("images_indexed", 0),
                    "superchunks_indexed": len(superchunks),
                }
                multimodal_report_file = os.path.join(chunks_dir, "multimodal_report.json")
                with open(multimodal_report_file, 'w', encoding='utf-8') as f:
                    json.dump(multimodal_report, f, ensure_ascii=False, indent=2)

                # --------------------
                # 5) ANÁLISIS DE CALIDAD POST-INGESTA
                # --------------------
                print("\n📊 Generando reporte de calidad...")
                
                analyzer = IngestionQualityAnalyzer()
                dedup_stats = validation_report.get("deduplication", {})
                metrics = analyzer.analyze_chunks(
                    validated_chunks,
                    file_name=os.path.basename(PDF_PATH),
                    duplicates_removed=(
                        dedup_stats.get("duplicates_removed", 0)
                        + dedup_stats.get("similarity_removed", 0)
                    ),
                )
                quality_report = analyzer.get_report(metrics)
                
                print(f"\n{'='*70}")
                print(f"📈 REPORTE DE CALIDAD: {file_stem}")
                print(f"{'='*70}")
                print(f"Score General:              {quality_report['quality']['overall_score']} ⭐")
                print(f"Tasa de Validación:         {quality_report['quality']['validation_rate']}")
                print(f"Tasa de Deduplicación:      {quality_report['quality']['deduplication_savings']}")
                print(f"Confianza Promedio:         {quality_report['quality']['avg_confidence']}")
                print(f"Variedad de Contenido:      {quality_report['content_composition']}")
                
                if quality_report['issues']['problems']:
                    print(f"\n⚠️  PROBLEMAS DETECTADOS:")
                    for problem in quality_report['issues']['problems']:
                        print(f"   • {problem}")
                
                if quality_report['recommendations']:
                    print(f"\n💡 RECOMENDACIONES:")
                    for rec in quality_report['recommendations']:
                        print(f"   • {rec}")
                
                print(f"{'='*70}\n")
                
                quality_report_file = os.path.join(chunks_dir, "quality_report.json")
                with open(quality_report_file, 'w', encoding='utf-8') as f:
                    json.dump(quality_report, f, ensure_ascii=False, indent=2)

                processed_docs += 1
                print(f"\n✅ DOCUMENTO COMPLETADO: {file_stem}\n")

                manifest[file_stem] = {
                    "sha256": pdf_hash,
                    "last_processed_at": datetime.now(timezone.utc).isoformat(),
                    "status": "success",
                }
                _save_manifest(manifest)

            except QuotaExhaustedError as e:
                # Un error de crédito no se arregla pasando al documento siguiente: se
                # corta el lote. Sin esto la corrida seguía moliendo los 5 documentos
                # durante más de una hora para terminar con 0 procesados y —si se había
                # limpiado antes— el índice vacío.
                print(f"\n{'='*70}")
                print(f"⛔ {e}")
                print(f"{'='*70}")
                print("Se aborta el lote. El índice y los chunks quedan como estaban")
                print("(salvo que hayas corrido scripts/clean_all.py antes: en ese caso")
                print("restaurá el backup o volvé a ingestar cuando haya crédito).")
                failed_docs.append((file_stem, str(e)))
                break

            except Exception as e:
                print(f"❌ Error procesando {file_stem}: {e}")
                failed_docs.append((file_stem, str(e)))
                import traceback
                traceback.print_exc()

    # ========== RESUMEN FINAL ==========
    print(f"\n\n{'='*70}")
    print(f"📊 RESUMEN DE EJECUCIÓN")
    print(f"{'='*70}")
    print(f"✅ Documentos procesados: {processed_docs}")
    if skipped_docs:
        print(f"⏭️  Documentos omitidos (sin cambios): {len(skipped_docs)}")
        for doc_name in skipped_docs:
            print(f"   • {doc_name}")
    if failed_docs:
        print(f"❌ Documentos con error: {len(failed_docs)}")
        for doc_name, error_msg in failed_docs:
            print(f"   • {doc_name}: {error_msg}")
    print(f"{'='*70}")