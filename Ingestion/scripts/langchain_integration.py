"""
Ejemplo de integración del sistema multimodal con LangChain.
Muestra cómo usar el índice para un sistema RAG completo.
"""

from langchain.chat_models import ChatOpenAI
from langchain.prompts import ChatPromptTemplate
from langchain.schema import Document
from langchain.schema.runnable import RunnablePassthrough
from langchain.schema.output_parser import StrOutputParser
from typing import List, Dict, Any
import chromadb
import json
import os


class MultimodalChromaRetriever:
    """
    Retriever compatible con LangChain que usa nuestro índice multimodal.
    """
    
    def __init__(
        self,
        chroma_path: str = "./data/chroma",
        multimodal_path: str = "./data/multimodal_chunks",
        collection_name: str = None,
        k: int = 3
    ):
        self.client = chromadb.PersistentClient(path=chroma_path)
        
        if collection_name is None:
            collections = self.client.list_collections()
            collection_name = collections[0].name if collections else "multimodal_documents"
        
        self.collection = self.client.get_collection(name=collection_name)
        self.multimodal_path = multimodal_path
        self.k = k
    
    def get_relevant_documents(self, query: str) -> List[Document]:
        """
        Método compatible con LangChain Retriever interface.
        
        Args:
            query: Consulta de búsqueda
            
        Returns:
            Lista de Documents de LangChain
        """
        # Búsqueda en Chroma
        results = self.collection.query(
            query_texts=[query],
            n_results=self.k,
            include=["documents", "metadatas", "distances"]
        )
        
        # Convertir a Documents de LangChain
        documents = []
        
        if results["ids"][0]:
            for i in range(len(results["ids"][0])):
                chunk_id = results["ids"][0][i]
                text = results["documents"][0][i]
                metadata = results["metadatas"][0][i]
                distance = results["distances"][0][i]
                
                # Enriquecer metadata con información multimodal
                enhanced_metadata = {
                    **metadata,
                    "chunk_id": chunk_id,
                    "similarity": 1 - distance,
                    "distance": distance
                }
                
                # Intentar cargar chunk multimodal completo
                chunk_path = os.path.join(
                    self.multimodal_path,
                    metadata['source_file'],
                    f"{chunk_id}.json"
                )
                
                if os.path.exists(chunk_path):
                    with open(chunk_path, 'r') as f:
                        multimodal_chunk = json.load(f)
                        
                        # Si tiene tablas, agregar su contenido al texto
                        if multimodal_chunk.get('media_references'):
                            table_texts = []
                            for ref in multimodal_chunk['media_references']:
                                if ref['media_type'] == 'table':
                                    table_path = os.path.join(
                                        self.multimodal_path,
                                        metadata['source_file'],
                                        ref['file_path']
                                    )
                                    if os.path.exists(table_path):
                                        with open(table_path, 'r') as tf:
                                            table_data = json.load(tf)
                                            if 'markdown' in table_data:
                                                table_texts.append(f"\n\nTabla:\n{table_data['markdown']}")
                            
                            if table_texts:
                                text += "".join(table_texts)
                                enhanced_metadata['has_tables'] = True
                
                # Crear Document de LangChain
                doc = Document(
                    page_content=text,
                    metadata=enhanced_metadata
                )
                documents.append(doc)
        
        return documents
    
    def invoke(self, query: str) -> List[Document]:
        """Alias para compatibilidad con Runnable."""
        return self.get_relevant_documents(query)


class MultimodalRAGChain:
    """
    RAG Chain completo usando LangChain con nuestro retriever multimodal.
    """
    
    def __init__(
        self,
        retriever: MultimodalChromaRetriever,
        model_name: str = "gpt-4o",
        temperature: float = 0.3
    ):
        self.retriever = retriever
        self.llm = ChatOpenAI(model_name=model_name, temperature=temperature)
        self.chain = self._create_chain()
    
    def _create_chain(self):
        """Crea el chain RAG."""
        
        # Plantilla de prompt
        template = """Eres un asistente técnico experto. Responde la pregunta SOLO basándote en el siguiente contexto.

Si la respuesta no está en el contexto, di "No tengo información suficiente para responder esa pregunta."

Contexto:
{context}

Pregunta: {question}

Respuesta detallada:"""
        
        prompt = ChatPromptTemplate.from_template(template)
        
        # Chain con LCEL (LangChain Expression Language)
        chain = (
            {
                "context": self.retriever | self._format_docs,
                "question": RunnablePassthrough()
            }
            | prompt
            | self.llm
            | StrOutputParser()
        )
        
        return chain
    
    @staticmethod
    def _format_docs(docs: List[Document]) -> str:
        """Formatea los documentos recuperados para el prompt."""
        formatted = []
        
        for i, doc in enumerate(docs, 1):
            metadata = doc.metadata
            
            # Header con fuente
            header = f"[Fuente {i}: {metadata['source_file']}, Página {metadata['page_number']}]"
            
            # Tipo de contenido
            content_type = metadata.get('content_type', 'text')
            if content_type != 'text':
                header += f" (Tipo: {content_type})"
            
            formatted.append(f"{header}\n{doc.page_content}\n")
        
        return "\n\n---\n\n".join(formatted)
    
    def invoke(self, question: str) -> str:
        """
        Ejecuta el RAG chain.
        
        Args:
            question: Pregunta del usuario
            
        Returns:
            Respuesta generada
        """
        return self.chain.invoke(question)
    
    def invoke_with_sources(self, question: str) -> Dict[str, Any]:
        """
        Ejecuta el RAG chain y retorna respuesta con fuentes.
        
        Args:
            question: Pregunta del usuario
            
        Returns:
            Dict con 'answer' y 'sources'
        """
        # Obtener documentos relevantes
        docs = self.retriever.get_relevant_documents(question)
        
        # Generar respuesta
        answer = self.chain.invoke(question)
        
        # Formatear fuentes
        sources = [
            {
                "file": doc.metadata['source_file'],
                "page": doc.metadata['page_number'],
                "type": doc.metadata['content_type'],
                "similarity": f"{doc.metadata['similarity']:.2%}",
                "preview": doc.page_content[:200] + "..."
            }
            for doc in docs
        ]
        
        return {
            "answer": answer,
            "sources": sources
        }


# ============================================================================
# EJEMPLOS DE USO
# ============================================================================

def example_1_basic_rag():
    """Ejemplo 1: RAG básico con LangChain."""
    print("\n" + "="*70)
    print("📚 EJEMPLO 1: RAG Básico con LangChain")
    print("="*70 + "\n")
    
    # Setup
    retriever = MultimodalChromaRetriever(k=3)
    rag_chain = MultimodalRAGChain(retriever)
    
    # Query
    question = "¿Cuál es el voltaje de alimentación del TBEN-L4-8IOL?"
    print(f"❓ Pregunta: {question}\n")
    
    # Ejecutar
    answer = rag_chain.invoke(question)
    
    print(f"💬 Respuesta:\n{answer}\n")


def example_2_with_sources():
    """Ejemplo 2: RAG con fuentes detalladas."""
    print("\n" + "="*70)
    print("📚 EJEMPLO 2: RAG con Fuentes")
    print("="*70 + "\n")
    
    # Setup
    retriever = MultimodalChromaRetriever(k=3)
    rag_chain = MultimodalRAGChain(retriever)
    
    # Query
    question = "¿Qué muestran los LEDs cuando hay comunicación IO-Link?"
    print(f"❓ Pregunta: {question}\n")
    
    # Ejecutar con fuentes
    result = rag_chain.invoke_with_sources(question)
    
    print(f"💬 Respuesta:\n{result['answer']}\n")
    
    print("📖 Fuentes consultadas:")
    for i, source in enumerate(result['sources'], 1):
        print(f"\n  {i}. {source['file']} (Página {source['page']})")
        print(f"     Tipo: {source['type']} | Similaridad: {source['similarity']}")
        print(f"     Preview: {source['preview']}")


def example_3_table_search():
    """Ejemplo 3: Búsqueda específica en tablas."""
    print("\n" + "="*70)
    print("📚 EJEMPLO 3: Búsqueda en Tablas")
    print("="*70 + "\n")
    
    # Retriever que filtra solo tablas
    client = chromadb.PersistentClient(path="./data/chroma")
    collections = client.list_collections()
    collection = client.get_collection(name=collections[0].name)
    
    # Búsqueda manual filtrando por tipo
    query = "especificaciones eléctricas"
    results = collection.query(
        query_texts=[query],
        n_results=3,
        where={"content_type": "table"},
        include=["documents", "metadatas"]
    )
    
    print(f"❓ Query: '{query}' (solo tablas)\n")
    print(f"📋 Encontradas {len(results['ids'][0])} tablas:\n")
    
    for i in range(len(results['ids'][0])):
        metadata = results['metadatas'][0][i]
        text = results['documents'][0][i]
        
        print(f"  {i+1}. {metadata['source_file']} - Página {metadata['page_number']}")
        print(f"     {text[:150]}...\n")


def example_4_conversation():
    """Ejemplo 4: Conversación multi-turn con memoria."""
    print("\n" + "="*70)
    print("📚 EJEMPLO 4: Conversación con Memoria")
    print("="*70 + "\n")
    
    from langchain.memory import ConversationBufferMemory
    from langchain.chains import ConversationalRetrievalChain
    
    # Setup
    retriever = MultimodalChromaRetriever(k=3)
    llm = ChatOpenAI(model_name="gpt-4o", temperature=0.3)
    memory = ConversationBufferMemory(
        memory_key="chat_history",
        return_messages=True,
        output_key="answer"
    )
    
    # Chain conversacional
    qa_chain = ConversationalRetrievalChain.from_llm(
        llm=llm,
        retriever=retriever,
        memory=memory,
        return_source_documents=True
    )
    
    # Conversación
    questions = [
        "¿Qué es el TBEN-L4-8IOL?",
        "¿Cuál es su voltaje de operación?",
        "¿Qué protecciones tiene?"
    ]
    
    for i, question in enumerate(questions, 1):
        print(f"👤 Usuario ({i}): {question}")
        result = qa_chain({"question": question})
        print(f"🤖 Asistente: {result['answer']}\n")


def example_5_custom_prompt():
    """Ejemplo 5: Prompt personalizado para respuestas técnicas."""
    print("\n" + "="*70)
    print("📚 EJEMPLO 5: Prompt Personalizado")
    print("="*70 + "\n")
    
    retriever = MultimodalChromaRetriever(k=3)
    llm = ChatOpenAI(model_name="gpt-4o", temperature=0.2)
    
    # Prompt técnico especializado
    template = """Eres un ingeniero especialista en automatización industrial. 
Tu trabajo es proveer respuestas técnicas precisas basadas en la documentación.

REGLAS:
1. Cita específicamente la fuente (documento y página)
2. Si hay valores numéricos o especificaciones, repítelos exactamente
3. Si hay tablas relevantes, preséntelas en formato claro
4. Si no tienes información, dilo claramente

CONTEXTO TÉCNICO:
{context}

PREGUNTA TÉCNICA: {question}

RESPUESTA TÉCNICA DETALLADA:"""
    
    prompt = ChatPromptTemplate.from_template(template)
    
    chain = (
        {
            "context": retriever | MultimodalRAGChain._format_docs,
            "question": RunnablePassthrough()
        }
        | prompt
        | llm
        | StrOutputParser()
    )
    
    question = "Dame las especificaciones completas de alimentación del TBEN-L4-8IOL"
    print(f"❓ Pregunta: {question}\n")
    
    answer = chain.invoke(question)
    print(f"💬 Respuesta técnica:\n{answer}\n")


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    import sys
    
    examples = {
        "1": example_1_basic_rag,
        "2": example_2_with_sources,
        "3": example_3_table_search,
        "4": example_4_conversation,
        "5": example_5_custom_prompt,
    }
    
    if len(sys.argv) > 1 and sys.argv[1] in examples:
        examples[sys.argv[1]]()
    else:
        print("\n" + "="*70)
        print("🎯 EJEMPLOS DE INTEGRACIÓN CON LANGCHAIN")
        print("="*70 + "\n")
        
        print("Ejecuta un ejemplo específico:")
        print("  python langchain_integration.py 1  # RAG básico")
        print("  python langchain_integration.py 2  # Con fuentes")
        print("  python langchain_integration.py 3  # Búsqueda en tablas")
        print("  python langchain_integration.py 4  # Conversación")
        print("  python langchain_integration.py 5  # Prompt personalizado")
        print("\nO ejecuta todos:")
        
        for key, func in examples.items():
            try:
                func()
            except Exception as e:
                print(f"\n❌ Error en ejemplo {key}: {e}\n")
