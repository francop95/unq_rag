"""
Re-indexación con Sistema Dual
===============================

Re-indexa documentos existentes usando el nuevo sistema dual:
1. Índice textual (OpenAI embeddings) - Para texto y tablas  
2. Índice visual (CLIP embeddings) - Para imágenes

No necesita volver a ejecutar GPT-4o, usa los embeddings ya generados
y añade embeddings visuales CLIP para las imágenes.

Uso:
    python reindex_dual.py
"""

import os
import sys
from pathlib import Path

# Añadir src al path (el script vive en scripts/, así que src está un nivel arriba)
project_root = Path(__file__).parent.parent
src_dir = project_root / "src"
sys.path.insert(0, str(src_dir))
# Las rutas de la config son relativas a la raíz del proyecto
os.chdir(project_root)

from tasks.indexing_task_dual import DualIndexer
from config.config_reader import load_config
from logger import Logger

logger = Logger.get_logger(__name__)


def main():
    """Re-indexa todos los documentos con sistema dual."""
    
    print(f"\n{'='*70}")
    print(f"🔄 RE-INDEXACIÓN CON SISTEMA DUAL")
    print(f"{'='*70}\n")
    
    # ══════════════════════════════════════════════════════════
    # 1. CARGAR CONFIGURACIÓN
    # ══════════════════════════════════════════════════════════
    
    print("📋 Cargando configuración...")
    config = load_config(".env")
    task_settings = config.to_task_settings_dict()
    
    # Añadir configuraciones específicas del sistema dual
    task_settings["clip_model"] = "clip-ViT-B-32"  # Modelo CLIP
    task_settings["visual_index_name"] = "visual_docs"  # Nombre colección visual
    
    print(f"   ✓ Índice textual: {task_settings.get('index_name', 'multimodal_documents')}")
    print(f"   ✓ Índice visual: {task_settings['visual_index_name']}")
    print(f"   ✓ Modelo CLIP: {task_settings['clip_model']}")
    
    # ══════════════════════════════════════════════════════════
    # 2. BUSCAR DOCUMENTOS PROCESADOS
    # ══════════════════════════════════════════════════════════
    
    embeddings_base_path = Path(task_settings["embeddings_data_path"])
    
    if not embeddings_base_path.exists():
        print(f"\n❌ Error: No existe la carpeta de embeddings: {embeddings_base_path}")
        return
    
    # Listar documentos
    documents = [d for d in embeddings_base_path.iterdir() if d.is_dir()]
    
    if not documents:
        print(f"\n⚠️  No se encontraron documentos en: {embeddings_base_path}")
        return
    
    print(f"\n📁 Documentos encontrados: {len(documents)}")
    for doc in documents:
        print(f"   • {doc.name}")
    
    # ══════════════════════════════════════════════════════════
    # 3. RE-INDEXAR CADA DOCUMENTO
    # ══════════════════════════════════════════════════════════
    
    total_text = 0
    total_visual = 0
    failed_docs = []
    
    print(f"\n{'─'*70}")
    print("🔄 Iniciando re-indexación dual...")
    print(f"{'─'*70}\n")
    
    for i, doc_path in enumerate(documents, 1):
        doc_name = doc_path.name
        print(f"[{i}/{len(documents)}] Procesando: {doc_name}")
        
        try:
            # Buscar última ejecución (carpeta más reciente)
            executions = sorted([x for x in doc_path.iterdir() if x.is_dir()])
            
            if not executions:
                print(f"   ⚠️  Sin ejecuciones para {doc_name}, saltando...")
                continue
            
            latest_execution = executions[-1]
            print(f"   → Usando ejecución: {latest_execution.name}")
            
            # Crear indexer dual
            indexer = DualIndexer()
            indexer._task_settings = task_settings
            indexer._input_data = {
                "file_name": f"{doc_name}.pdf",
                "embeddings": str(latest_execution)
            }
            
            # Ejecutar indexado
            result = indexer.execute()
            
            if result.error:
                print(f"   ❌ Error: {result.error}")
                failed_docs.append((doc_name, result.error))
            else:
                payload = result.payload
                doc_text_count = payload.get('total_text', 0)
                doc_visual_count = payload.get('total_visual', 0)
                
                total_text += doc_text_count
                total_visual += doc_visual_count
                
                print(f"   ✅ Indexado:")
                print(f"      • Textual: {doc_text_count} chunks")
                print(f"      • Visual: {doc_visual_count} imágenes")
        
        except Exception as e:
            print(f"   ❌ Error procesando {doc_name}: {e}")
            failed_docs.append((doc_name, str(e)))
            logger.exception(f"Error en {doc_name}")
        
        print()
    
    # ══════════════════════════════════════════════════════════
    # 4. REPORTE FINAL
    # ══════════════════════════════════════════════════════════
    
    print(f"\n{'='*70}")
    print(f"✅ RE-INDEXACIÓN DUAL COMPLETADA")
    print(f"{'='*70}")
    print(f"\n📊 ESTADÍSTICAS FINALES:")
    print(f"   • Documentos procesados: {len(documents) - len(failed_docs)}/{len(documents)}")
    print(f"   • Total chunks textuales: {total_text}")
    print(f"   • Total imágenes indexadas: {total_visual}")
    
    if failed_docs:
        print(f"\n⚠️  DOCUMENTOS CON ERRORES ({len(failed_docs)}):")
        for doc_name, error in failed_docs:
            print(f"   • {doc_name}: {error}")
    
    print(f"\n{'='*70}")
    print(f"🎉 Sistema dual listo para búsquedas híbridas!")
    print(f"{'='*70}")
    print(f"\nPara probar el sistema de búsqueda:")
    print(f"  python hybrid_multimodal_search.py \"tu consulta aquí\"")
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Re-indexación cancelada por el usuario")
    except Exception as e:
        print(f"\n❌ Error fatal: {e}")
        import traceback
        traceback.print_exc()
