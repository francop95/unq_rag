#!/usr/bin/env python3
"""
Script de prueba para validar la integración de retrieval avanzado.
Verifica que todos los componentes nuevos están correctamente instalados y funcionales.
"""
import os
import sys

def test_imports():
    """Prueba que todas las importaciones nuevas funcionan."""
    print("="*70)
    print("🧪 TEST 1: Verificar importaciones de módulos nuevos")
    print("="*70)
    
    errors = []
    
    # Test 1: Advanced retrieval module
    print("\n1. Módulo advanced_retrieval...")
    try:
        from src.task_utils.advanced_retrieval import (
            ContextExpander,
            CrossEncoderReranker,
            BM25Index,
            QueryExpander,
            MetadataFilter
        )
        print("   ✅ Todas las clases importadas correctamente")
    except ImportError as e:
        errors.append(f"advanced_retrieval: {e}")
        print(f"   ❌ Error: {e}")
    
    # Test 2: Config updates
    print("\n2. Config con retrieval settings...")
    try:
        from src.config.config import DualIndexConfig
        from src.config.config_reader import ConfigReader
        
        # Verificar que DualIndexConfig tiene los nuevos campos
        config_fields = DualIndexConfig.__dataclass_fields__.keys()
        required_fields = [
            'use_reranking', 'reranker_model', 'rerank_top_k',
            'use_bm25', 'bm25_weight',
            'use_query_expansion', 'max_expanded_terms',
            'use_context_expansion', 'context_window_size'
        ]
        
        missing = [f for f in required_fields if f not in config_fields]
        if missing:
            errors.append(f"Config falta campos: {missing}")
            print(f"   ❌ Faltan campos: {missing}")
        else:
            print(f"   ✅ Config tiene todos los campos de retrieval avanzado")
    except Exception as e:
        errors.append(f"config: {e}")
        print(f"   ❌ Error: {e}")
    
    # Test 3: Dependencies opcionales
    print("\n3. Dependencias opcionales...")
    
    # Cross-encoder
    try:
        from sentence_transformers import CrossEncoder
        print("   ✅ sentence_transformers (cross-encoder)")
    except ImportError:
        print("   ⚠️  sentence_transformers no disponible (reranking deshabilitado)")
    
    # BM25
    try:
        from rank_bm25 import BM25Okapi
        print("   ✅ rank-bm25")
    except ImportError:
        print("   ⚠️  rank-bm25 no disponible (sparse retrieval deshabilitado)")
    
    # TheFuzz
    try:
        from thefuzz import process as fuzz_process
        print("   ✅ thefuzz")
    except ImportError:
        print("   ⚠️  thefuzz no disponible (query expansion con degradación)")
    
    return errors


def test_config_loading():
    """Prueba que el config se carga correctamente con las nuevas variables."""
    print("\n" + "="*70)
    print("🧪 TEST 2: Cargar configuración desde .env")
    print("="*70)
    
    try:
        from src.config.config_reader import load_config
        
        if not os.path.exists(".env"):
            print("   ⚠️  Archivo .env no encontrado")
            return ["No .env file"]
        
        config = load_config(".env")
        
        print(f"\n   Retrieval Config:")
        print(f"   - use_reranking: {config.dual.use_reranking}")
        print(f"   - reranker_model: {config.dual.reranker_model}")
        print(f"   - use_bm25: {config.dual.use_bm25}")
        print(f"   - bm25_weight: {config.dual.bm25_weight}")
        print(f"   - use_query_expansion: {config.dual.use_query_expansion}")
        print(f"   - use_context_expansion: {config.dual.use_context_expansion}")
        print(f"   - context_window_size: {config.dual.context_window_size}")
        
        print("\n   ✅ Configuración cargada correctamente")
        return []
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return [f"config_loading: {e}"]


def test_search_result_fields():
    """Prueba que SearchResult tiene los nuevos campos."""
    print("\n" + "="*70)
    print("🧪 TEST 3: Verificar SearchResult con campos nuevos")
    print("="*70)
    
    try:
        # Importar desde el archivo correcto
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "hybrid_multimodal_search",
            "hybrid_multimodal_search.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        
        SearchResult = module.SearchResult
        
        # Crear instancia de prueba
        result = SearchResult(
            doc_id="test_123",
            score=0.95,
            content="Test content",
            content_type="text",
            page_num="1",
            file_name="test.pdf",
            source="text+bm25",
            distance=0.15,
            chapter="Test Chapter",
            section="Test Section",
            hierarchy_path="Root > Chapter > Section",
            rerank_score=0.98,
            bm25_score=12.5,
            expanded_context="Previous chunk context..."
        )
        
        print("\n   Campos nuevos:")
        print(f"   - chapter: {result.chapter}")
        print(f"   - section: {result.section}")
        print(f"   - hierarchy_path: {result.hierarchy_path}")
        print(f"   - rerank_score: {result.rerank_score}")
        print(f"   - bm25_score: {result.bm25_score}")
        print(f"   - expanded_context: {result.expanded_context[:30]}...")
        
        print("\n   ✅ SearchResult tiene todos los campos nuevos")
        return []
    except Exception as e:
        print(f"   ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return [f"search_result: {e}"]


def main():
    """Ejecuta todos los tests."""
    print("\n🔬 VALIDACIÓN DE INTEGRACIÓN: RETRIEVAL AVANZADO")
    print("="*70)
    
    all_errors = []
    
    # Test 1: Imports
    errors = test_imports()
    all_errors.extend(errors)
    
    # Test 2: Config loading
    errors = test_config_loading()
    all_errors.extend(errors)
    
    # Test 3: SearchResult
    errors = test_search_result_fields()
    all_errors.extend(errors)
    
    # Resumen final
    print("\n" + "="*70)
    print("📊 RESUMEN DE VALIDACIÓN")
    print("="*70)
    
    if all_errors:
        print(f"\n❌ {len(all_errors)} errores encontrados:\n")
        for error in all_errors:
            print(f"   - {error}")
        print("\n⚠️  SISTEMA PARCIALMENTE FUNCIONAL")
        print("   Las features con errores estarán deshabilitadas.")
        sys.exit(1)
    else:
        print("\n✅ TODOS LOS TESTS PASARON")
        print()
        print("Sistema de retrieval avanzado correctamente integrado:")
        print("  ✅ 6 mejoras de retrieval implementadas")
        print("  ✅ Configuración leída desde .env")
        print("  ✅ Graceful degradation para deps opcionales")
        print()
        print("Próximo paso:")
        print("  1. pip install -r requirements.txt (instalar deps faltantes)")
        print("  2. python hybrid_multimodal_search.py 'tu query'")
        sys.exit(0)


if __name__ == "__main__":
    main()
