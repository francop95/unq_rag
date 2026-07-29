#!/usr/bin/env python3
"""
Test simplificado de búsqueda en ChromaDB
"""
import chromadb

# Conectar a ChromaDB
client = chromadb.PersistentClient(path="./data/chroma_index")

# Obtener colección
collection = client.get_collection(name="multimodal_documents")

# Test queries
queries = [
    "Arduino ubicación plano",
    "conexiones módulo TBEN",
    "fuentes alimentación",
    "diagrama conexión eléctrico"
]

print(f"\n{'='*70}")
print(f"🔍 TEST DE BÚSQUEDA DIRECTA EN CHROMADB")
print(f"{'='*70}")
print(f"Total documentos indexados: {collection.count()}\n")

for query in queries:
    print(f"\n{'─'*70}")
    print(f"Query: {query}")
    print(f"{'─'*70}")
    
    results = collection.query(
        query_texts=[query],
        n_results=3,
        include=["documents", "metadatas", "distances"]
    )
    
    if results and results['documents'] and results['documents'][0]:
        for i, (doc, meta, dist) in enumerate(zip(
            results['documents'][0],
            results['metadatas'][0],
            results['distances'][0]
        ), 1):
            file_name = meta.get('file_name', 'N/A')
            content_type = meta.get('content_type', 'N/A')
            similarity = 1 - dist  # Convertir distancia a similaridad
            
            print(f"\n  [{i}] Similaridad: {similarity:.4f} | {file_name} ({content_type})")
            
            # Mostrar snippet del contenido
            snippet = str(doc)[:200].replace('\n', ' ')
            print(f"      {snippet}...")
    else:
        print("  ❌ No se encontraron resultados")

print(f"\n{'='*70}\n")
