#!/usr/bin/env python3
"""
Test búsqueda en planos escaneados procesados con GPT-4o Vision
"""
import sys
sys.path.insert(0, "src")

from config.config import Config
from hybrid_multimodal_search import HybridMultimodalSearch

config = Config()
searcher = HybridMultimodalSearch(config)

# Test queries relacionadas con planos escaneados
queries = [
    "¿Dónde está ubicado el Arduino en el plano?",
    "conexiones del módulo TBEN",
    "ubicación de las fuentes de alimentación",
    "diagrama de conexionado eléctrico"
]

print(f"\n{'='*70}")
print(f"🔍 TEST DE BÚSQUEDA - PLANOS ESCANEADOS")
print(f"{'='*70}\n")

for query in queries:
    print(f"\n{'─'*70}")
    print(f"Query: {query}")
    print(f"{'─'*70}")
    
    results = searcher.search(
        query=query,
        top_k=3,
        use_reranking=True,
        use_bm25=True,
        use_query_expansion=True
    )
    
    if results:
        for i, result in enumerate(results[:3], 1):
            doc_id = result.get("doc_id", "N/A")
            score = result.get("score", 0)
            file_name = result.get("metadata", {}).get("file_name", "N/A")
            content_type = result.get("metadata", {}).get("content_type", "N/A")
            
            print(f"\n  [{i}] Score: {score:.4f} | {file_name} ({content_type})")
            
            # Mostrar snippet del contenido
            content = result.get("content", "")
            if isinstance(content, dict):
                # Chunk multimodal procesado como dict
                if "plan_type" in content:
                    print(f"      📋 Tipo: {content.get('plan_type')}")
                if "equipment_locations" in content and content["equipment_locations"]:
                    print(f"      🔧 Equipos detectados: {list(content['equipment_locations'].keys())[:5]}")
                if "description" in content:
                    print(f"      📝 {content.get('description')}")
                if "diagram_type" in content:
                    print(f"      📊 Tipo: {content.get('diagram_type')}")
                if "components" in content and content["components"]:
                    print(f"      ⚙️  Componentes: {', '.join(content['components'][:5])}")
            else:
                # Chunk de texto
                snippet = str(content)[:200].replace('\n', ' ')
                print(f"      {snippet}...")
    else:
        print("  ❌ No se encontraron resultados")

print(f"\n{'='*70}\n")
