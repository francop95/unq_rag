"""
Script de Limpieza Completa del Sistema
========================================

Borra TODOS los datos procesados para empezar desde cero:
- Chunks generados (data/chunks_data/)
- Embeddings (data/embeddings_data/)
- Índices Chroma (data/chroma/ y data/chroma_index/)
- Chunks multimodales (data/multimodal_chunks/)
- Media (data/media/)

ADVERTENCIA: Esta acción NO se puede deshacer.

Uso:
    python scripts/clean_all.py [--confirm]
"""

import os
import shutil
import sys
from pathlib import Path


def get_size_mb(path: Path) -> float:
    """Calcula el tamaño total de un directorio en MB."""
    if not path.exists():
        return 0.0
    
    total_size = 0
    try:
        if path.is_file():
            total_size = path.stat().st_size
        else:
            for item in path.rglob('*'):
                if item.is_file():
                    total_size += item.stat().st_size
    except Exception:
        pass
    
    return total_size / (1024 * 1024)  # Convertir a MB


def count_files(path: Path) -> int:
    """Cuenta archivos recursivamente."""
    if not path.exists():
        return 0
    
    try:
        return sum(1 for _ in path.rglob('*') if _.is_file())
    except Exception:
        return 0


def main():
    """Función principal de limpieza."""
    
    print(f"\n{'='*70}")
    print(f"🧹 LIMPIEZA COMPLETA DEL SISTEMA")
    print(f"{'='*70}\n")
    
    # Directorios a borrar
    data_dir = Path("./data")
    
    directories_to_clean = {
        "chunks_data": data_dir / "chunks_data",
        "embeddings_data": data_dir / "embeddings_data",
        "chroma": data_dir / "chroma",
        "chroma_index": data_dir / "chroma_index",
        "multimodal_chunks": data_dir / "multimodal_chunks",
        "media": data_dir / "media"
    }
    
    # Verificar qué existe
    print("📊 ESTADO ACTUAL:\n")
    
    total_size = 0.0
    total_files = 0
    existing_dirs = []
    
    for name, path in directories_to_clean.items():
        if path.exists():
            size_mb = get_size_mb(path)
            files = count_files(path)
            total_size += size_mb
            total_files += files
            existing_dirs.append(name)
            
            print(f"   📁 {name}:")
            print(f"      • Tamaño: {size_mb:.2f} MB")
            print(f"      • Archivos: {files:,}")
        else:
            print(f"   ⚪ {name}: (no existe)")
    
    print(f"\n{'─'*70}")
    print(f"   💾 TOTAL: {total_size:.2f} MB en {total_files:,} archivos")
    print(f"{'─'*70}\n")
    
    if not existing_dirs:
        print("✅ No hay datos para limpiar. Sistema ya está limpio.")
        return
    
    # Confirmar con usuario
    auto_confirm = "--confirm" in sys.argv or "-y" in sys.argv
    
    if not auto_confirm:
        print("⚠️  ADVERTENCIA: Esta acción borrará TODOS los datos procesados.")
        print("   Los PDFs originales en data/raw_data/ NO serán afectados.")
        print()
        response = input("¿Estás seguro de continuar? (escribe 'SI' para confirmar): ")
        
        if response.strip().upper() != "SI":
            print("\n❌ Limpieza cancelada por el usuario.")
            return
    
    # Proceder con la limpieza
    print(f"\n{'─'*70}")
    print("🧹 INICIANDO LIMPIEZA...")
    print(f"{'─'*70}\n")
    
    deleted_dirs = []
    failed_dirs = []
    
    for name, path in directories_to_clean.items():
        if path.exists():
            try:
                print(f"   🗑️  Borrando {name}... ", end="", flush=True)
                
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                
                print("✅")
                deleted_dirs.append(name)
            except Exception as e:
                print(f"❌ Error: {e}")
                failed_dirs.append((name, str(e)))
    
    # Reporte final
    print(f"\n{'='*70}")
    print(f"📊 REPORTE FINAL")
    print(f"{'='*70}\n")
    
    if deleted_dirs:
        print(f"✅ Directorios borrados ({len(deleted_dirs)}):")
        for name in deleted_dirs:
            print(f"   • {name}")
    
    if failed_dirs:
        print(f"\n❌ Errores ({len(failed_dirs)}):")
        for name, error in failed_dirs:
            print(f"   • {name}: {error}")
    
    print(f"\n💾 Espacio liberado: {total_size:.2f} MB")
    
    # Recrear estructura básica
    print(f"\n{'─'*70}")
    print("📁 Recreando estructura de directorios...")
    print(f"{'─'*70}\n")
    
    dirs_to_create = [
        data_dir / "chunks_data",
        data_dir / "embeddings_data",
        data_dir / "chroma_index",
        data_dir / "multimodal_chunks",
        data_dir / "media" / "images",
        data_dir / "media" / "tables"
    ]
    
    for dir_path in dirs_to_create:
        dir_path.mkdir(parents=True, exist_ok=True)
        print(f"   ✓ {dir_path}")
    
    print(f"\n{'='*70}")
    print(f"✅ LIMPIEZA COMPLETADA")
    print(f"{'='*70}\n")
    
    print("🚀 Sistema listo para re-procesamiento completo.")
    print("\nPara re-procesar todos los documentos:")
    print("   python src/main_multimodal.py")
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Limpieza cancelada por el usuario (Ctrl+C)")
    except Exception as e:
        print(f"\n❌ Error fatal: {e}")
        import traceback
        traceback.print_exc()
