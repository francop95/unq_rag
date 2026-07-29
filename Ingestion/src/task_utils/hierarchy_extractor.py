"""
Extractor de Jerarquía Documental
=================================

Extrae estructura jerárquica del documento:
- Table of Contents (TOC) del PDF
- Inferencia de secciones/capítulos desde contenido
- Tracking de headings por página
- Enriquece chunks con metadata de estructura
"""

import os
import re
from typing import List, Dict, Any, Tuple, Optional
import fitz  # PyMuPDF

from logger import Logger

logger = Logger.get_logger(__name__)


class DocumentHierarchyExtractor:
    """
    Extrae y mantiene jerarquía del documento para enriquecer chunks.
    """
    
    def __init__(self):
        self.toc = []
        self.heading_by_page = {}
        self.document_structure = {}
    
    def extract_from_pdf(self, pdf_path: str) -> Dict[str, Any]:
        """
        Extrae jerarquía completa del PDF.
        
        Args:
            pdf_path: Ruta al PDF
            
        Returns:
            {
                "toc": [...],  # Table of contents
                "heading_by_page": {page: heading},
                "structure": {...}  # Estructura jerárquica
            }
        """
        try:
            with fitz.open(pdf_path) as doc:
                # 1. Extraer TOC del PDF (si existe)
                self.toc = self._extract_toc(doc)
                
                # 2. Analizar páginas para detectar headings
                self.heading_by_page = self._analyze_pages_for_headings(doc)
                
                # 3. Construir estructura jerárquica
                self.document_structure = self._build_structure(self.toc, self.heading_by_page)
                
                logger.info(f"Jerarquía extraída: {len(self.toc)} entradas TOC, "
                          f"{len(self.heading_by_page)} headings por página")
                
                return {
                    "toc": self.toc,
                    "heading_by_page": self.heading_by_page,
                    "structure": self.document_structure
                }
        
        except Exception as e:
            logger.error(f"Error extrayendo jerarquía: {e}")
            return {
                "toc": [],
                "heading_by_page": {},
                "structure": {}
            }
    
    def _extract_toc(self, doc) -> List[Dict[str, Any]]:
        """
        Extrae Table of Contents del PDF.
        
        Returns:
            Lista de entradas:
            [
                {
                    "level": 1,
                    "title": "Chapter 1: Introduction",
                    "page": 5
                },
                ...
            ]
        """
        toc = []
        
        try:
            # PyMuPDF puede extraer TOC
            toc_data = doc.get_toc()
            
            for entry in toc_data:
                level, title, page = entry
                toc.append({
                    "level": level,
                    "title": title.strip(),
                    "page": page
                })
        
        except Exception as e:
            logger.warning(f"No se pudo extraer TOC del PDF: {e}")
        
        return toc
    
    def _analyze_pages_for_headings(self, doc) -> Dict[int, Dict[str, Any]]:
        """
        Analiza cada página para detectar headings/títulos.
        
        Heurísticas:
        - Texto grande/bold al inicio de página
        - Patrones como "Chapter X", "Section X.Y"
        - Numeración jerárquica
        
        Returns:
            {
                page_num: {
                    "main_heading": str,
                    "sub_headings": [str],
                    "section_number": str  # ej: "3.2.1"
                }
            }
        """
        heading_by_page = {}
        
        for page_num in range(len(doc)):
            page = doc[page_num]
            
            # Extraer bloques de texto con formato
            try:
                blocks = page.get_text("dict")["blocks"]
                
                headings = self._detect_headings_in_blocks(blocks)
                
                if headings:
                    heading_by_page[page_num + 1] = headings
            
            except Exception as e:
                logger.debug(f"Error analizando página {page_num + 1}: {e}")
        
        return heading_by_page
    
    def _detect_headings_in_blocks(self, blocks: List[Dict]) -> Optional[Dict[str, Any]]:
        """
        Detecta headings en bloques de texto.
        
        Busca:
        - Texto con font size mayor que el normal
        - Texto bold/negrita
        - Patrones de numeración (1., 1.1, etc.)
        """
        main_heading = None
        sub_headings = []
        section_number = None
        
        # Font size normal típico en documentos técnicos: 10-12pt
        normal_font_size = 11.0
        
        for block in blocks:
            if block.get("type") == 0:  # Text block
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        text = span.get("text", "").strip()
                        font_size = span.get("size", normal_font_size)
                        font_flags = span.get("flags", 0)
                        
                        # Verificar si es bold (flag 16 en PyMuPDF)
                        is_bold = bool(font_flags & 16)
                        
                        # Verificar si es heading (font grande o bold)
                        is_heading = (font_size > normal_font_size * 1.2) or is_bold
                        
                        if is_heading and text and len(text) > 3:
                            # Detectar número de sección
                            section_match = re.match(r'^(\d+(?:\.\d+)*)\s+(.+)', text)
                            
                            if section_match:
                                section_number = section_match.group(1)
                                heading_text = section_match.group(2)
                            else:
                                heading_text = text
                            
                            # Determinar si es main o sub heading
                            if not main_heading:
                                main_heading = heading_text
                            else:
                                sub_headings.append(heading_text)
        
        if main_heading:
            return {
                "main_heading": main_heading,
                "sub_headings": sub_headings[:3],  # Max 3 sub-headings
                "section_number": section_number
            }
        
        return None
    
    def _build_structure(self, 
                        toc: List[Dict], 
                        heading_by_page: Dict[int, Dict]) -> Dict[str, Any]:
        """
        Construye estructura jerárquica del documento.
        
        Returns:
            {
                "chapters": [...],
                "sections_by_page": {page: section_path}
            }
        """
        structure = {
            "chapters": [],
            "sections_by_page": {}
        }
        
        # Si hay TOC, usarlo como base
        if toc:
            current_chapter = None
            current_section = None
            
            for entry in toc:
                level = entry["level"]
                title = entry["title"]
                page = entry["page"]
                
                if level == 1:
                    # Nuevo capítulo
                    current_chapter = {
                        "title": title,
                        "page": page,
                        "sections": []
                    }
                    structure["chapters"].append(current_chapter)
                    structure["sections_by_page"][page] = title
                
                elif level == 2 and current_chapter:
                    # Nueva sección
                    current_section = {
                        "title": title,
                        "page": page
                    }
                    current_chapter["sections"].append(current_section)
                    section_path = f"{current_chapter['title']} > {title}"
                    structure["sections_by_page"][page] = section_path
        
        # Si no hay TOC, usar headings detectados
        else:
            for page_num, headings in sorted(heading_by_page.items()):
                main = headings.get("main_heading")
                section_num = headings.get("section_number", "")
                
                if main:
                    structure["sections_by_page"][page_num] = f"{section_num} {main}".strip()
        
        return structure
    
    def get_metadata_for_page(self, page_num: int) -> Dict[str, Any]:
        """
        Obtiene metadata jerárquica para una página.
        
        Args:
            page_num: Número de página (1-indexed)
            
        Returns:
            {
                "section": str,
                "chapter": str,
                "heading": str,
                "sub_heading": str,
                "section_number": str,
                "hierarchy": [str]  # Path jerárquico
            }
        """
        metadata = {
            "section": None,
            "chapter": None,
            "heading": None,
            "sub_heading": None,
            "section_number": None,
            "hierarchy": []
        }
        
        # Buscar en estructura
        section_path = self.document_structure.get("sections_by_page", {}).get(page_num)
        
        if section_path:
            metadata["section"] = section_path
            
            # Parsear jerarquía
            if " > " in section_path:
                parts = section_path.split(" > ")
                metadata["chapter"] = parts[0]
                metadata["section"] = parts[-1]
                metadata["hierarchy"] = parts
            else:
                metadata["hierarchy"] = [section_path]
        
        # Buscar headings detectados en página
        page_headings = self.heading_by_page.get(page_num)
        
        if page_headings:
            metadata["heading"] = page_headings.get("main_heading")
            metadata["section_number"] = page_headings.get("section_number")
            
            sub_headings = page_headings.get("sub_headings", [])
            if sub_headings:
                metadata["sub_heading"] = sub_headings[0]
        
        # Si no hay nada, buscar la sección más cercana anterior
        if not metadata["section"]:
            metadata["section"] = self._find_nearest_section(page_num)
        
        return metadata
    
    def _find_nearest_section(self, page_num: int) -> Optional[str]:
        """
        Encuentra la sección más cercana anterior a la página.
        """
        sections_by_page = self.document_structure.get("sections_by_page", {})
        
        # Buscar la página más cercana anterior que tenga sección
        for p in range(page_num, 0, -1):
            if p in sections_by_page:
                return sections_by_page[p]
        
        return None
    
    def enrich_chunk_with_hierarchy(self, 
                                    chunk: Dict[str, Any]) -> Dict[str, Any]:
        """
        Enriquece un chunk con metadata jerárquica.
        
        Args:
            chunk: Chunk a enriquecer
            
        Returns:
            Chunk enriquecido con campos de jerarquía
        """
        page_num = chunk.get("page_num")
        
        if page_num:
            try:
                page_num = int(page_num)
                metadata = self.get_metadata_for_page(page_num)
                
                # Añadir campos al chunk (ChromaDB solo acepta str, int, float, bool)
                chunk["document_section"] = metadata["section"]
                chunk["document_chapter"] = metadata["chapter"]
                chunk["heading"] = metadata["heading"]
                chunk["sub_heading"] = metadata["sub_heading"]
                chunk["section_number"] = metadata["section_number"]
                # Convertir lista a string ⭐ FIX
                hierarchy_list = metadata["hierarchy"]
                if hierarchy_list:
                    chunk["hierarchy_path"] = " > ".join(str(h) for h in hierarchy_list)
                else:
                    chunk["hierarchy_path"] = ""
            
            except Exception as e:
                logger.debug(f"Error enriqueciendo chunk con jerarquía: {e}")
        
        return chunk
