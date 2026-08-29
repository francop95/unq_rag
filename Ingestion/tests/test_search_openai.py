#!/usr/bin/env python3
"""
Test de búsqueda con OpenAI embeddings
"""
import chromadb
from openai import OpenAI
import os
from dotenv import load_dotenv

load_dotenv()

# Cliente OpenAI
openai_client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY") or os.getenv("OPEN_AI_KEY")  # alias legado
)

def get_embedding(text):
    """Generar embedding con text-embedding-3-large"""
    response = openai_client.embeddings.create(
        input=text,
        model="text-embedding-3-large"
    )
    return response.data[0].embedding

# Conectar a ChromaDB
client = chromadb.PersistentClient(path="./data/chroma_index")
collection = client.get_collection(name="multimodal_documents")

# Test queries
queries = [
    "Arduino ubicación plano",
    "conexiones módulo TBEN",
    "fuentes alimentación 220V",
    "diagrama conexión eléctrico"
]

print(f"\n{'='*70}")
print(f"🔍 TEST DE BÚSQUEDA CON OPENAI EMBEDDINGS")
print(f"{'='*70}")
print(f"Total documentos indexados: {collection.count()}\n")

for query in queries:
    print(f"\n{'─'*70}")
    print(f"Query: {query}")
    print(f"{'─'*70}")
    
    # Generar embedding de la query
    query_embedding = get_embedding(query)
    
    # Buscar documentos similares
    results = collection.query(
        query_embeddings=[query_embedding],
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
            
            print(f"\n  [{i}] Similaridad: {similarity:.4f}")
            print(f"      📄 Archivo: {file_name} ({content_type})")
            
            # Mostrar snippet del contenido
            snippet = str(doc)[:300].replace('\n', ' ')
            print(f"      💬 {snippet}...")
    else:
        print("  ❌ No se encontraron resultados")

print(f"\n{'='*70}\n")
