"""
Chunking de programas Python (.py)
==================================

Los tres programas de control del secadero no son documentación: son la
fuente más precisa que hay sobre cómo se habla realmente con el equipo. Los
registros Modbus que se leen, el puerto serie del Arduino, los comandos que
mueven los servos, las constantes del control y las fórmulas psicrométricas
están en el código y en ningún manual.

Cómo se corta
-------------
El autor ya segmentó los archivos con celdas de Spyder (`#%%`), y cada marca
trae un comentario que dice qué hace el bloque ("#%% Cálculo de humedad
absoluta", "#%% Genero vectores de Referencias y Control-Relax"). Esa división
es mejor que cualquier heurística que se pueda inventar acá, así que es la
división primaria; el comentario de la marca queda como encabezado de sección.

Dentro de una celda demasiado larga se corta por los límites de las funciones
de nivel superior, usando los números de línea del AST. Una función que por sí
sola excede el presupuesto se deja entera igual: partir un cuerpo por la mitad
produce dos chunks que no responden nada, mientras que uno grande al menos
responde su pregunta.

Emite el MISMO esquema de chunk que el pipeline multimodal (`content_type`
"text", con el código en un bloque markdown), así que las etapas de
enriquecimiento, embeddings e indexado se reutilizan sin tocarlas.
"""

import ast
import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from task import Task, TaskReturnData
from logger import Logger
from task_utils.validators.task_validators import DocumentExtensionValidator

logger = Logger.get_logger(__name__)
current_dir = os.path.dirname(os.path.abspath(__file__))

# Marca de celda de Spyder: `#%%`, `# %%` y variantes con texto detrás.
MARCA_CELDA = re.compile(r"^\s*#\s*%%+\s*(?P<titulo>.*)$")

# Presupuesto de caracteres por chunk de código. Más chico que el de prosa: en
# código la densidad de información por carácter es mayor y un bloque grande
# diluye el embedding entre media docena de temas sin relación.
PRESUPUESTO_CHUNK = 2200


class PythonChunkingTask(Task):
    """
    Programa Python → chunks por celda de Spyder y por función.

    Settings que usa:
        chunks_data_path (str):  carpeta base de salida
        max_chunk_length (int):  tope duro de caracteres por chunk
    """

    name = "PythonChunking"
    _INPUT_VALIDATORS = [DocumentExtensionValidator("py_path", ".py")]

    def __init__(self):
        super().__init__()
        self.stats: Dict[str, Any] = {}

    def validate(self):
        errores = [v.validate(self._input_data) for v in self._INPUT_VALIDATORS]
        return [e for e in errores if e is not None]

    def execute(self) -> TaskReturnData:
        try:
            chunks_dir = self.execute_local()
            return TaskReturnData(payload={"chunks": chunks_dir, "stats": self.stats})
        except Exception as ex:
            logger.exception("PythonChunkingTask falló")
            return TaskReturnData(error=str(ex))

    @staticmethod
    def _get_exec_timestamp() -> str:
        return datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    # ------------------------------------------------------------ lectura
    @staticmethod
    def _leer(path: str) -> str:
        """
        El contenido del archivo, tolerando codificaciones.

        Los tres programas declaran `# -*- coding: utf-8 -*-`, pero vienen de
        Windows y basta un acento guardado en cp1252 para que falle la lectura
        entera. Un carácter mal decodificado es mucho mejor que perder el
        archivo completo.
        """
        for codificacion in ("utf-8", "cp1252", "latin-1"):
            try:
                with open(path, "r", encoding=codificacion) as fh:
                    return fh.read()
            except UnicodeDecodeError:
                continue
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()

    # --------------------------------------------------------- estructura
    @staticmethod
    def _celdas(lineas: List[str]) -> List[Dict[str, Any]]:
        """
        Divide el archivo en celdas `#%%`.

        Devuelve dicts con `titulo`, `inicio` y `fin` (1-based, `fin`
        inclusive). El código anterior a la primera marca es una celda propia:
        ahí viven los imports y la configuración del puerto serie.
        """
        cortes: List[Tuple[int, str]] = []
        for numero, linea in enumerate(lineas, start=1):
            m = MARCA_CELDA.match(linea)
            if m:
                cortes.append((numero, (m.group("titulo") or "").strip()))

        if not cortes:
            return [{"titulo": "", "inicio": 1, "fin": len(lineas)}]

        celdas: List[Dict[str, Any]] = []
        if cortes[0][0] > 1:
            celdas.append({"titulo": "", "inicio": 1, "fin": cortes[0][0] - 1})
        for i, (linea_marca, titulo) in enumerate(cortes):
            fin = cortes[i + 1][0] - 1 if i + 1 < len(cortes) else len(lineas)
            celdas.append({"titulo": titulo, "inicio": linea_marca, "fin": fin})
        return celdas

    @staticmethod
    def _definiciones(fuente: str) -> List[Dict[str, Any]]:
        """
        Funciones y clases de nivel superior, con su rango de líneas.

        Si el archivo no parsea se devuelve una lista vacía y el corte queda en
        manos de las celdas: un `.py` con un error de sintaxis sigue siendo
        documentación válida de cómo se opera el equipo, así que no se descarta.
        """
        try:
            arbol = ast.parse(fuente)
        except SyntaxError as e:
            logger.warning(f"El archivo no parsea ({e}); se corta solo por celdas.")
            return []

        definiciones = []
        for nodo in arbol.body:
            if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                # El decorador es parte de la definición
                inicio = min([nodo.lineno] + [d.lineno for d in nodo.decorator_list])
                definiciones.append({
                    "nombre": nodo.name,
                    "tipo": "clase" if isinstance(nodo, ast.ClassDef) else "función",
                    "inicio": inicio,
                    "fin": getattr(nodo, "end_lineno", nodo.lineno),
                    "doc": (ast.get_docstring(nodo) or "").strip(),
                })
        return definiciones

    @classmethod
    def _resumen_modulo(cls, fuente: str, definiciones: List[Dict[str, Any]]) -> str:
        """
        Chunk de cabecera: de qué trata el programa y qué define.

        Sin esto, una consulta como "qué hace el programa de control del
        secadero" no tiene ningún chunk al que parecerse: todos los demás son
        fragmentos de código sin visión de conjunto.
        """
        partes: List[str] = []
        try:
            doc = (ast.get_docstring(ast.parse(fuente)) or "").strip()
        except SyntaxError:
            doc = ""
        if doc:
            partes.append(doc)

        try:
            arbol = ast.parse(fuente)
            modulos = set()
            for nodo in ast.walk(arbol):
                if isinstance(nodo, ast.Import):
                    modulos.update(a.name.split(".")[0] for a in nodo.names)
                elif isinstance(nodo, ast.ImportFrom) and nodo.module:
                    modulos.add(nodo.module.split(".")[0])
            if modulos:
                partes.append("Librerías que usa: " + ", ".join(sorted(modulos)) + ".")
        except SyntaxError:
            pass

        if definiciones:
            listado = "\n".join(
                f"- {d['tipo']} `{d['nombre']}` (líneas {d['inicio']}–{d['fin']})"
                + (f": {d['doc'].splitlines()[0]}" if d["doc"] else "")
                for d in definiciones
            )
            partes.append(f"Define {len(definiciones)} elementos:\n{listado}")

        return "\n\n".join(partes)

    # ------------------------------------------------------------- cortes
    def _trozos_de_celda(self, celda: Dict[str, Any],
                         definiciones: List[Dict[str, Any]],
                         lineas: List[str]) -> List[Dict[str, Any]]:
        """
        Una celda en uno o más trozos, sin partir nunca una función.

        Devuelve dicts con `inicio`, `fin` y `etiqueta` (el nombre de la función
        cuando el trozo es exactamente una).
        """
        def texto(a: int, b: int) -> str:
            return "\n".join(lineas[a - 1:b])

        tope = int(self._task_settings.get("max_chunk_length", 8000))
        entero = texto(celda["inicio"], celda["fin"])
        if len(entero) <= PRESUPUESTO_CHUNK:
            return [{"inicio": celda["inicio"], "fin": celda["fin"], "etiqueta": ""}]

        # Funciones que caen dentro de la celda, en orden
        dentro = sorted(
            (d for d in definiciones
             if d["inicio"] >= celda["inicio"] and d["fin"] <= celda["fin"]),
            key=lambda d: d["inicio"],
        )

        trozos: List[Dict[str, Any]] = []
        cursor = celda["inicio"]
        for d in dentro:
            if d["inicio"] > cursor:
                # Código suelto antes de la definición (constantes, llamadas)
                trozos.append({"inicio": cursor, "fin": d["inicio"] - 1, "etiqueta": ""})
            trozos.append({"inicio": d["inicio"], "fin": d["fin"], "etiqueta": d["nombre"]})
            cursor = d["fin"] + 1
        if cursor <= celda["fin"]:
            trozos.append({"inicio": cursor, "fin": celda["fin"], "etiqueta": ""})

        # Los trozos sin función y todavía largos se parten por líneas. Esto
        # solo alcanza a código de nivel de módulo (listas de inicialización,
        # bucles sueltos), donde cortar es aceptable; una función nunca llega
        # acá porque su trozo se emite completo.
        salida: List[Dict[str, Any]] = []
        for t in trozos:
            bloque = texto(t["inicio"], t["fin"])
            if not bloque.strip():
                continue
            if t["etiqueta"] or len(bloque) <= max(PRESUPUESTO_CHUNK, tope // 4):
                salida.append(t)
                continue
            inicio = t["inicio"]
            largo = 0
            for numero in range(t["inicio"], t["fin"] + 1):
                largo += len(lineas[numero - 1]) + 1
                if largo >= PRESUPUESTO_CHUNK and numero < t["fin"]:
                    salida.append({"inicio": inicio, "fin": numero, "etiqueta": ""})
                    inicio, largo = numero + 1, 0
            if inicio <= t["fin"]:
                salida.append({"inicio": inicio, "fin": t["fin"], "etiqueta": ""})

        return salida or [{"inicio": celda["inicio"], "fin": celda["fin"], "etiqueta": ""}]

    # ------------------------------------------------------------- pipeline
    def execute_local(self) -> str:
        py_path = self._input_data["py_path"]
        file_name = os.path.basename(py_path)
        file_stem = os.path.splitext(file_name)[0]

        base_output_dir = os.path.join(
            current_dir, "..", "..",
            self._task_settings["chunks_data_path"],
            file_stem,
            self._get_exec_timestamp(),
        )
        os.makedirs(base_output_dir, exist_ok=True)

        fuente = self._leer(py_path)
        lineas = fuente.splitlines()
        definiciones = self._definiciones(fuente)
        celdas = self._celdas(lineas)

        stats = {
            "total_lines": len(lineas),
            "cells": len(celdas),
            "definitions": len(definiciones),
            "parsed": bool(definiciones) or "def " not in fuente,
            "chunks_code": 0,
            "total_chunks": 0,
        }

        chunks: List[Dict[str, Any]] = []
        contador = 0

        def nuevo(numero: int, texto: str, seccion: str, metadata: str) -> Dict[str, Any]:
            return {
                "file_name": file_name,
                # No hay páginas: la "página" es el número de celda, que es la
                # unidad de lectura que eligió el autor del programa.
                "page_num": str(numero),
                "chunk_id": f"chunk_{contador}",
                "page_metadata": metadata,
                "original_chunk": texto,
                "content_type": "text",
                "document_section": seccion,
                "hierarchy_path": f"{file_stem} > {seccion}" if seccion else file_stem,
            }

        # Chunk de cabecera (celda 0): de qué trata el programa
        resumen = self._resumen_modulo(fuente, definiciones)
        if resumen:
            contador += 1
            chunks.append(nuevo(
                0,
                f"Programa `{file_name}` ({len(lineas)} líneas).\n\n{resumen}",
                "Resumen del programa",
                f"{file_name}, resumen",
            ))

        for indice, celda in enumerate(celdas, start=1):
            seccion = celda["titulo"] or f"Bloque {indice}"
            for trozo in self._trozos_de_celda(celda, definiciones, lineas):
                codigo = "\n".join(lineas[trozo["inicio"] - 1:trozo["fin"]]).strip("\n")
                if not codigo.strip():
                    continue

                encabezado = f"Programa `{file_name}`, sección «{seccion}»"
                if trozo["etiqueta"]:
                    encabezado += f", función `{trozo['etiqueta']}`"
                encabezado += f" (líneas {trozo['inicio']}–{trozo['fin']}):"

                contador += 1
                chunks.append(nuevo(
                    indice,
                    f"{encabezado}\n\n```python\n{codigo}\n```",
                    seccion,
                    f"líneas {trozo['inicio']}–{trozo['fin']}",
                ))
                stats["chunks_code"] += 1

        # Enlace prev/next: la expansión de contexto recupera el bloque de al
        # lado, que en código es casi siempre donde está la otra mitad de la
        # respuesta (la constante que usa la función, o quién la llama).
        def _id_compuesto(c: Dict[str, Any]) -> str:
            return f"{c.get('file_name','')}_{c.get('page_num','')}_{c.get('chunk_id','')}"

        for i, chunk in enumerate(chunks):
            chunk["prev_chunk_id"] = _id_compuesto(chunks[i - 1]) if i > 0 else ""
            chunk["next_chunk_id"] = _id_compuesto(chunks[i + 1]) if i + 1 < len(chunks) else ""

        for chunk in chunks:
            page_dir = os.path.join(base_output_dir, f"{file_stem}_{chunk['page_num']}")
            os.makedirs(page_dir, exist_ok=True)
            destino = os.path.join(
                page_dir, f"{file_stem}_{chunk['page_num']}_{chunk['chunk_id']}.json"
            )
            with open(destino, "w", encoding="utf-8") as fh:
                json.dump(chunk, fh, ensure_ascii=False, indent=4)
            stats["total_chunks"] += 1

        self.stats = stats

        logger.info("=" * 60)
        logger.info(f"CHUNKING DE PYTHON — {file_name}")
        logger.info("=" * 60)
        logger.info(f"Líneas:      {stats['total_lines']}")
        logger.info(f"Celdas:      {stats['cells']}")
        logger.info(f"Definiciones:{stats['definitions']:>4}")
        logger.info(f"Chunks:      {stats['total_chunks']}")

        with open(os.path.join(base_output_dir, "chunking_stats.json"), "w", encoding="utf-8") as fh:
            json.dump(stats, fh, ensure_ascii=False, indent=2)

        return base_output_dir
