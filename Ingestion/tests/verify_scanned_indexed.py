#!/usr/bin/env python3
"""
Verificar que los planos escaneados estén indexados correctamente
"""
import chromadb

# Conectar a ChromaDB
client = chromadb.PersistentClient(path="./data/chroma_index")
collection = client.get_collection(name="multimodal_documents")

# Obtener todos los documentos (peek)
print(f"\n{'='*70}")
print(f"📊 VERIFICACIÓN DE INDEXACIÓN - PLANOS ESCANEADOS")
print(f"{'='*70}")
print(f"\nTotal documentos indexados: {collection.count()}\n")

# Buscar específicamente los planos
try:
    results = collection.get(
        where={"file_name": "conexionadoTben.pdf"},
        include=["documents", "metadatas"]
    )
    
    print(f"{'─'*70}")
    print(f"📄 conexionadoTben.pdf")
    print(f"{'─'*70}")
    if results['documents']:
        for i, (doc, meta) in enumerate(zip(results['documents'], results['metadatas']), 1):
            content_type = meta.get('content_type', 'N/A')
            print(f"\n  Chunk {i} ({content_type}):")
            snippet = str(doc)[:400].replace('\n', ' ')
            print(f"  {snippet}...")
    else:
        print("  ❌ No se encontró indexado")
except Exception as e:
    print(f"  ❌ Error: {e}")

try:
    results = collection.get(
        where={"file_name": "Plano distribucion electrica.pdf"},
        include=["documents", "metadatas"]
    )
    
    print(f"\n{'─'*70}")
    print(f"📄 Plano distribucion electrica.pdf")
    print(f"{'─'*70}")
    if results['documents']:
        for i, (doc, meta) in enumerate(zip(results['documents'], results['metadatas']), 1):
            content_type = meta.get('content_type', 'N/A')
            print(f"\n  Chunk {i} ({content_type}):")
            snippet = str(doc)[:400].replace('\n', ' ')
            print(f"  {snippet}...")
    else:
        print("  ❌ No se encontró indexado")
except Exception as e:
    print(f"  ❌ Error: {e}")

print(f"\n{'='*70}\n")
