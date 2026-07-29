import os
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
import json

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
                
                print(f"   📊 Chunks cargados: {len(raw_chunks)}")
                
                # 1.5.1) Validación técnica de dominio
                print("\n🔬 Validación técnica de chunks...")
                technical_validator = TechnicalDocumentValidator()
                tech_validated_chunks, tech_report = technical_validator.validate_chunks(raw_chunks)
                
                if tech_report['warnings_by_type']:
                    print(f"   ⚠️  Advertencias técnicas encontradas:")
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
                
                print(f"   📊 Chunks totales: {len(raw_chunks)}")
                print(f"   ✅ Chunks técnicamente válidos: {tech_report['valid']}")
                print(f"   ✅ Chunks finales válidos: {validation_report['validation']['valid']}")
                print(f"   ❌ Chunks rechazados: {validation_report['validation']['invalid']}")
                print(f"   🔄 Duplicados removidos: {validation_report['deduplication']['duplicates_removed']}")
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
                # 2) RE-CHUNKING SEMÁNTICO CROSS-PAGE
                # --------------------
                print("\n🔗 Analizando chunks relacionados (re-chunking semántico)...")

                # Este paso crea super-chunks que agrupan contenido relacionado
                # que abarca múltiples páginas (ej: procedimientos largos).
                # Corre ANTES de embeddings para que los super-chunks generados
                # también se embeban e indexen (antes se guardaban sueltos y
                # ningún paso posterior los volvía a leer).
                superchunks = []
                try:
                    rechunker = SemanticRechunker(
                        similarity_threshold=config.hybrid.semantic_similarity_threshold,
                        max_pages_gap=config.hybrid.max_pages_gap,
                        max_superchunk_size=config.hybrid.max_superchunk_size
                    )

                    # Por ahora usamos solo proximidad de páginas (sin embeddings calculados aún)
                    # En una versión futura, se podrían cargar embeddings ya calculados
                    _, superchunks = rechunker.process(
                        validated_chunks,
                        embeddings=None  # Usar solo proximidad
                    )

                    if superchunks:
                        print(f"   ✅ Super-chunks creados: {len(superchunks)}")
                        superchunks_file = os.path.join(chunks_dir, "superchunks.json")
                        with open(superchunks_file, 'w', encoding='utf-8') as f:
                            json.dump(superchunks, f, ensure_ascii=False, indent=2)
                    else:
                        print(f"   ℹ️  No se crearon super-chunks (contenido muy fragmentado)")

                except Exception as e:
                    print(f"   ⚠️  Error en re-chunking semántico: {e}")
                    # Continuar sin super-chunks

                # Consolidado que se envía a embeddings: chunks validados/deduplicados
                # + super-chunks (en vez de los chunks crudos sin validar).
                chunks_for_embedding = validated_chunks + superchunks
                chunks_for_embedding_file = os.path.join(chunks_dir, "chunks_for_embedding.json")
                with open(chunks_for_embedding_file, 'w', encoding='utf-8') as f:
                    json.dump(chunks_for_embedding, f, ensure_ascii=False, indent=2)

                # --------------------
                # 3) EMBEDDINGS
                # --------------------
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
                metrics = analyzer.analyze_chunks(validated_chunks, file_name=os.path.basename(PDF_PATH))
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