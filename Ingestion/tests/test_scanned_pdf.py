#!/usr/bin/env python3
"""
Script de diagnóstico para verificar la detección de imágenes en PDFs escaneados.
"""
import fitz  # PyMuPDF

def has_images_enhanced(page) -> bool:
    """Versión mejorada de has_images que detecta PDFs escaneados."""
    try:
        # Método 1: Imágenes embebidas estándar
        image_list = page.get_images()
        if len(image_list) > 0:
            return True
        
        # Método 2: PDFs escaneados (toda la página es una imagen de fondo)
        text = (page.get_text() or "").strip()
        if len(text) < 50:  # Muy poco texto detectado
            try:
                # Renderizar a baja resolución para análisis rápido
                pix = page.get_pixmap(matrix=fitz.Matrix(0.2, 0.2))  # 20% del tamaño
                
                # Verificar si hay píxeles no blancos
                samples = pix.samples
                if samples:
                    # Contar píxeles no blancos
                    non_white_pixels = sum(1 for i in range(0, len(samples), 3) 
                                         if not (samples[i] > 240 and samples[i+1] > 240 and samples[i+2] > 240))
                    
                    # Si >10% de píxeles no son blancos, hay contenido visual
                    total_pixels = pix.width * pix.height
                    if non_white_pixels > total_pixels * 0.1:
                        return True
            except Exception as e:
                print(f"      Error en pixmap analysis: {e}")
        
        return False
    except Exception as e:
        print(f"      Error en has_images: {e}")
        return False

pdf_paths = [
    "data/raw_data/conexionadoTben.pdf",
    "data/raw_data/Plano distribucion electrica.pdf",
]

for pdf_path in pdf_paths:
    print(f"\n{'='*60}")
    print(f"📄 Analizando: {pdf_path}")
    print(f"{'='*60}")
    
    try:
        doc = fitz.open(pdf_path)
        print(f"Total páginas: {len(doc)}")
        
        for page_num in range(len(doc)):
            page = doc[page_num]
            text = page.get_text().strip()
            images = page.get_images()
            
            print(f"\nPágina {page_num + 1}:")
            print(f"  - Texto extraído: {len(text)} caracteres")
            if text:
                preview = text[:100].replace('\n', ' ')
                print(f"    Vista previa: {preview}...")
            print(f"  - Imágenes embebidas: {len(images)}")
            
            # Test detección mejorada
            print(f"\n  🔍 Test detección mejorada:")
            has_images_result = has_images_enhanced(page)
            print(f"    - has_images_enhanced(): {has_images_result}")
            
            # Verificar análisis de contenido
            has_text = len(text) > 100
            has_images_bool = has_images_result
            print(f"\n  📊 Análisis final:")
            print(f"    - has_text (>100 chars): {has_text}")
            print(f"    - has_images: {has_images_bool}")
            
            if has_images_bool and not has_text:
                print(f"  ✅ PLANO ESCANEADO DETECTADO → Debería usar GPT-4o Vision + OCR")
            elif has_text and not has_images_bool:
                print(f"  ℹ️  Documento de texto puro → Chunking sintáctico")
            elif has_images_bool and has_text:
                print(f"  ℹ️  Documento mixto (texto + imágenes)")
            else:
                print(f"  ⚠️  Página vacía (sin texto ni imágenes)")
        
        doc.close()
    except Exception as e:
        print(f"❌ Error: {e}")

