#!/usr/bin/env python3
"""
Script de Validación Rápida - Mejoras RAG Multimodal
====================================================

Verifica que todos los módulos nuevos se puedan importar correctamente.
"""

import sys
import os

# Añadir src al path
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.join(current_dir, "src")
sys.path.insert(0, src_dir)

def test_imports():
    """Prueba imports de todos los módulos nuevos."""
    print("🔍 Verificando imports de módulos mejorados...\n")
    
    tests = []
    
    # Test 1: Hybrid Chunking
    try:
        from task_utils.hybrid_chunking import (
            ContentAnalyzer, SyntacticChunker, 
            HybridChunkingStrategy, ChunkingStrategy
        )
        tests.append(("✅", "Hybrid Chunking"))
    except Exception as e:
        tests.append(("❌", f"Hybrid Chunking - {e}"))
    
    # Test 2: Diagram Processor
    try:
        from task_utils.diagram_processor import ElectricalDiagramProcessor
        tests.append(("✅", "Diagram Processor"))
    except Exception as e:
        tests.append(("❌", f"Diagram Processor - {e}"))
    
    # Test 3: Table Processor
    try:
        from task_utils.table_processor import TableProcessor
        tests.append(("✅", "Table Processor"))
    except Exception as e:
        tests.append(("❌", f"Table Processor - {e}"))
    
    # Test 4: Hierarchy Extractor
    try:
        from task_utils.hierarchy_extractor import DocumentHierarchyExtractor
        tests.append(("✅", "Hierarchy Extractor"))
    except Exception as e:
        tests.append(("❌", f"Hierarchy Extractor - {e}"))
    
    # Test 5: Technical Validators
    try:
        from task_utils.technical_validators import (
            TechnicalDocumentValidator,
            SpecificationTableValidator,
            ProcedureValidator,
            OCRCorruptionDetector,
            DiagramLabelValidator
        )
        tests.append(("✅", "Technical Validators"))
    except Exception as e:
        tests.append(("❌", f"Technical Validators - {e}"))
    
    # Test 6: Semantic Rechunker
    try:
        from task_utils.semantic_rechunker import SemanticRechunker, ProcedureDetector
        tests.append(("✅", "Semantic Rechunker"))
    except Exception as e:
        tests.append(("❌", f"Semantic Rechunker - {e}"))
    
    # Test 7: Modified Chunking Task
    try:
        from tasks.chunking_task_multimodal import ChunkingTask
        tests.append(("✅", "Chunking Task (modificado)"))
    except Exception as e:
        tests.append(("❌", f"Chunking Task - {e}"))
    
    # Test 8: Main Multimodal
    try:
        # Solo verificar que se puede importar (no ejecutar)
        with open(os.path.join(src_dir, "main_multimodal.py"), 'r') as f:
            content = f.read()
            if "TechnicalDocumentValidator" in content and "SemanticRechunker" in content:
                tests.append(("✅", "Main Multimodal (integrado)"))
            else:
                tests.append(("⚠️", "Main Multimodal (falta integración)"))
    except Exception as e:
        tests.append(("❌", f"Main Multimodal - {e}"))
    
    # Mostrar resultados
    print("="*60)
    print("RESULTADOS DE VALIDACIÓN")
    print("="*60)
    for status, module in tests:
        print(f"{status} {module}")
    
    # Resumen
    passed = sum(1 for status, _ in tests if status == "✅")
    failed = sum(1 for status, _ in tests if status == "❌")
    warnings = sum(1 for status, _ in tests if status == "⚠️")
    
    print("\n" + "="*60)
    print(f"Total: {len(tests)} | Exitosos: {passed} | Fallidos: {failed} | Advertencias: {warnings}")
    print("="*60)
    
    if failed > 0:
        print("\n❌ Algunas verificaciones fallaron. Revisar errores arriba.")
        return False
    elif warnings > 0:
        print("\n⚠️  Verificaciones pasaron con advertencias.")
        return True
    else:
        print("\n✅ Todas las verificaciones pasaron correctamente!")
        return True


def test_dependencies():
    """Verifica dependencias opcionales."""
    print("\n🔍 Verificando dependencias opcionales...\n")
    
    deps = []
    
    # PyMuPDF
    try:
        import fitz
        deps.append(("✅", "PyMuPDF (fitz)", fitz.__version__))
    except ImportError:
        deps.append(("❌", "PyMuPDF (fitz)", "No instalado - REQUERIDO"))
    
    # OpenAI
    try:
        import openai
        deps.append(("✅", "OpenAI SDK", openai.__version__))
    except ImportError:
        deps.append(("❌", "OpenAI SDK", "No instalado - REQUERIDO"))
    
    # LangChain
    try:
        import langchain
        deps.append(("✅", "LangChain", langchain.__version__))
    except ImportError:
        deps.append(("❌", "LangChain", "No instalado - REQUERIDO"))
    
    # ChromaDB
    try:
        import chromadb
        deps.append(("✅", "ChromaDB", chromadb.__version__))
    except ImportError:
        deps.append(("❌", "ChromaDB", "No instalado - REQUERIDO"))
    
    # Sentence Transformers (CLIP)
    try:
        import sentence_transformers
        deps.append(("✅", "Sentence Transformers", sentence_transformers.__version__))
    except ImportError:
        deps.append(("❌", "Sentence Transformers", "No instalado - REQUERIDO"))
    
    # Tesseract (opcional, para OCR de diagramas)
    try:
        import pytesseract
        deps.append(("✅", "Pytesseract", "Instalado"))
    except ImportError:
        deps.append(("⚠️", "Pytesseract", "No instalado - OPCIONAL (diagrams OCR)"))
    
    # scikit-learn (para semantic rechunker)
    try:
        import sklearn
        deps.append(("✅", "scikit-learn", sklearn.__version__))
    except ImportError:
        deps.append(("❌", "scikit-learn", "No instalado - REQUERIDO"))
    
    # NumPy
    try:
        import numpy
        deps.append(("✅", "NumPy", numpy.__version__))
    except ImportError:
        deps.append(("❌", "NumPy", "No instalado - REQUERIDO"))
    
    print("="*70)
    print("DEPENDENCIAS")
    print("="*70)
    for status, name, version in deps:
        print(f"{status} {name:<30} {version}")
    print("="*70)
    
    missing_required = sum(1 for status, _, v in deps if status == "❌" and "REQUERIDO" in v)
    
    if missing_required > 0:
        print(f"\n❌ Faltan {missing_required} dependencias requeridas.")
        print("Instalar con: pip install -r requirements.txt")
        return False
    else:
        print("\n✅ Todas las dependencias requeridas están instaladas.")
        return True


def test_config():
    """Verifica configuración."""
    print("\n🔍 Verificando configuración...\n")
    
    env_file = os.path.join(current_dir, ".env")
    env_example = os.path.join(current_dir, ".env.example")
    
    if os.path.exists(env_file):
        print("✅ Archivo .env encontrado")
        
        # Verificar nuevas variables
        with open(env_file, 'r') as f:
            content = f.read()
        
        new_vars = [
            "USE_HYBRID_CHUNKING",
            "COMPLEXITY_THRESHOLD",
            "MAX_TABLE_ROWS",
            "USE_OCR_FOR_DIAGRAMS"
        ]
        
        missing = [var for var in new_vars if var not in content]
        
        if missing:
            print(f"⚠️  Faltan variables nuevas en .env:")
            for var in missing:
                print(f"   - {var}")
            print(f"\nReferencia: {env_example}")
        else:
            print("✅ Todas las variables de configuración nuevas están presentes")
    else:
        print(f"⚠️  No existe .env, usando .env.example como referencia")
        if os.path.exists(env_example):
            print(f"ℹ️  Copiar: cp {env_example} {env_file}")


if __name__ == "__main__":
    print("\n" + "="*70)
    print("VALIDACIÓN DE MEJORAS RAG MULTIMODAL")
    print("="*70 + "\n")
    
    # Tests
    deps_ok = test_dependencies()
    imports_ok = test_imports()
    test_config()
    
    # Resultado final
    print("\n" + "="*70)
    if deps_ok and imports_ok:
        print("🎉 SISTEMA LISTO PARA USAR")
        print("="*70)
        print("\nPara ejecutar pipeline mejorado:")
        print("  python src/main_multimodal.py")
        print("\nPara más información:")
        print("  cat IMPROVEMENTS_README.md")
        sys.exit(0)
    else:
        print("❌ SISTEMA NO ESTÁ LISTO")
        print("="*70)
        print("\nResolver errores arriba antes de continuar.")
        sys.exit(1)
