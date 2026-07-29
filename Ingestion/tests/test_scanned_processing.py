#!/usr/bin/env python3
"""
Test de procesamiento de planos escaneados con fixes aplicados
"""
import sys
import os
sys.path.insert(0, "src")

from config.config import Config
from tasks.chunking_task_multimodal import ChunkingTask

# Configuración
config = Config()

# Solo procesar planos escaneados
test_files = [
    "data/raw_data/conexionadoTben.pdf",
    "data/raw_data/Plano distribucion electrica.pdf"
]

print(f"\n{'='*70}")
print(f"🧪 TEST DE PROCESAMIENTO - PLANOS ESCANEADOS CON FIXES")
print(f"{'='*70}\n")

for pdf_path in test_files:
    if not os.path.exists(pdf_path):
        print(f"❌ No encontrado: {pdf_path}")
        continue
    
    print(f"\n{'─'*70}")
    print(f"📄 Procesando: {os.path.basename(pdf_path)}")
    print(f"{'─'*70}")
    
    # Crear tarea de chunking
    chunker = ChunkingTask(
        file_name=os.path.basename(pdf_path),
        task_settings=config.as_task_settings()
    )
    
    # Ejecutar
    try:
        result = chunker.execute()
        
        print(f"\n✅ Procesamiento completado")
        print(f"   Timestamp: {result.timestamp}")
        print(f"   Output path: {result.data_output_path}")
        
        # Contar chunks generados
        chunks_dir = result.data_output_path
        if os.path.exists(chunks_dir):
            # Buscar carpetas de páginas
            page_dirs = [d for d in os.listdir(chunks_dir) 
                        if os.path.isdir(os.path.join(chunks_dir, d)) 
                        and not d.startswith('.')]
            
            total_chunks = 0
            for page_dir in page_dirs:
                page_path = os.path.join(chunks_dir, page_dir)
                chunk_files = [f for f in os.listdir(page_path) 
                              if f.endswith('.json')]
                total_chunks += len(chunk_files)
                
                if chunk_files:
                    print(f"\n   📦 {page_dir}: {len(chunk_files)} chunks")
                    for chunk_file in chunk_files:
                        print(f"      • {chunk_file}")
            
            print(f"\n   📊 Total chunks: {total_chunks}")
            
            # Si hay chunks, mostrar el primero
            if page_dirs and total_chunks > 0:
                import json
                first_page = page_dirs[0]
                first_page_path = os.path.join(chunks_dir, first_page)
                chunk_files = sorted([f for f in os.listdir(first_page_path) 
                                    if f.endswith('.json')])
                
                if chunk_files:
                    chunk_path = os.path.join(first_page_path, chunk_files[0])
                    with open(chunk_path, 'r') as f:
                        chunk_data = json.load(f)
                    
                    print(f"\n   🔍 Preview primer chunk:")
                    print(f"      • content_type: {chunk_data.get('content_type')}")
                    print(f"      • chunk_id: {chunk_data.get('chunk_id')}")
                    
                    # Mostrar notes si existen
                    original = chunk_data.get('original_chunk', {})
                    if isinstance(original, str):
                        import json
                        original = json.loads(original)
                    
                    notes = original.get('notes', {})
                    if isinstance(notes, dict):
                        print(f"      • diagram_type: {notes.get('diagram_type', notes.get('plan_type'))}")
                        if 'components' in notes:
                            print(f"      • components: {len(notes.get('components', []))} detectados")
                        if 'connections' in notes:
                            print(f"      • connections: {len(notes.get('connections', []))} detectadas")
                        if 'equipment_locations' in notes:
                            print(f"      • equipment_locations: {len(notes.get('equipment_locations', {}))} equipos")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

print(f"\n{'='*70}\n")
