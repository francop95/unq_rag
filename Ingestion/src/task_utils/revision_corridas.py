"""
Lectura de lo que el troceo dejó en disco
=========================================

El troceo escribe `chunks_data/<documento>/<corrida>/`: un JSON por chunk
dentro de una carpeta por página, los recortes de las figuras bajo `crops/` y
cuatro informes de validación. Este módulo lo lee. No decide nada: los
veredictos viven en `revision_store` y la compuerta en `main_multimodal`.

Está separado de las pantallas que lo usan porque ya son dos —la de consola
(`scripts/revisar_ingesta.py`) y la del navegador (`src/revision_web/`)— y
las dos tienen que ver exactamente lo mismo. Si una leyera distinto de la
otra, aprobar en una pantalla dejaría de significar lo mismo que aprobar en la
otra, que es justamente lo único que la compuerta promete.
"""

import json
import os
from typing import Any, Dict, List, Optional, Tuple

from task_utils.chunk_text import readable_chunk_text

# Figuras y tablas primero en toda vista: son ~300 de 1569 chunks y ahí
# vivieron todos los defectos encontrados hasta hoy.
VISUALES = ("diagram_visual", "image", "table", "diagram_description")

_RAIZ = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
)


def raiz_datos() -> str:
    """`Ingestion/data`, la carpeta que la API publica bajo `/media`."""
    return os.path.abspath(os.getenv("INGESTION_DATA_DIR", os.path.join(_RAIZ, "data")))


def raiz_chunks() -> str:
    return os.path.join(raiz_datos(), "chunks_data")


def clave(chunk: Dict[str, Any]) -> Tuple[str, str]:
    """La identidad de un chunk, igual que en el índice: `(página, chunk_id)`.

    Como str las dos, porque en disco `page_num` es int y en el índice es str,
    y comparar un 87 con un "87" devuelve False sin avisar.
    """
    return (str(chunk.get("page_num")), str(chunk.get("chunk_id")))


def documentos() -> List[str]:
    """Documentos con al menos una corrida de troceo en disco."""
    base = raiz_chunks()
    if not os.path.isdir(base):
        return []
    return sorted(d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d)))


def documentos_que_coinciden(texto: str) -> List[str]:
    return [d for d in documentos() if texto.lower() in d.lower()]


def corridas_de(documento: str) -> List[str]:
    """Las corridas de un documento, de más nueva a más vieja."""
    base = os.path.join(raiz_chunks(), documento)
    if not os.path.isdir(base):
        return []
    return sorted((d for d in os.listdir(base)
                   if os.path.isdir(os.path.join(base, d))), reverse=True)


def leer_chunks(documento: str, corrida: str) -> List[Dict[str, Any]]:
    """Los chunks de una corrida, en orden de página."""
    base = os.path.join(raiz_chunks(), documento, corrida)
    if not os.path.isdir(base):
        return []
    salida: List[Dict[str, Any]] = []
    for carpeta in sorted(os.listdir(base)):
        ruta = os.path.join(base, carpeta)
        if not os.path.isdir(ruta) or carpeta == "crops":
            continue
        for nombre in sorted(os.listdir(ruta)):
            if not nombre.endswith(".json"):
                continue
            try:
                with open(os.path.join(ruta, nombre), encoding="utf-8") as f:
                    datos = json.load(f)
            except (OSError, ValueError):
                continue
            salida.extend(datos if isinstance(datos, list) else [datos])

    def orden(c):
        try:
            return (int(c.get("page_num", 0)), str(c.get("chunk_id", "")))
        except (TypeError, ValueError):
            return (0, str(c.get("chunk_id", "")))

    return sorted(salida, key=orden)


def recorte_de(chunk: Dict[str, Any]) -> Optional[str]:
    """La ruta del recorte de una figura, si tiene."""
    original = chunk.get("original_chunk")
    if isinstance(original, str):
        try:
            original = json.loads(original)
        except ValueError:
            return None
    if not isinstance(original, dict):
        return None
    return original.get("image_path") or original.get("media_path")


def ruta_relativa_a_datos(ruta: str) -> Optional[str]:
    """La ruta de un archivo relativa a `Ingestion/data`, si cae adentro.

    Devuelve None para cualquier cosa de afuera: es lo que impide que un
    `image_path` torcido en un JSON termine sirviendo `/etc/passwd`.
    """
    if not ruta:
        return None
    datos = raiz_datos()
    entero = os.path.abspath(ruta)
    if not entero.startswith(datos + os.sep):
        return None
    return os.path.relpath(entero, datos).replace(os.sep, "/")


def texto_de(chunk: Dict[str, Any]) -> str:
    return readable_chunk_text(chunk).strip()


def comparar(documento: str, corrida: str, previa: str) -> Dict[str, Any]:
    """Qué cambió entre dos corridas del mismo documento.

    Es la vista que más defectos por minuto encuentra: revisar 1569 chunks de
    cero no se hace dos veces, y los que cambiaron son pocos. Así se vio que un
    barrido de valores había degradado la página 87, que perdió su "2 kW" y
    recuperó un "2.5A" inventado que una corrección anterior había sacado.
    """
    def por_clave(chunks):
        return {clave(c): texto_de(c) for c in chunks}

    antes = por_clave(leer_chunks(documento, previa))
    ahora = por_clave(leer_chunks(documento, corrida))
    return {
        "previa": previa,
        "corrida": corrida,
        "nuevos": sorted(set(ahora) - set(antes)),
        "perdidos": sorted(set(antes) - set(ahora)),
        "cambiados": sorted(k for k in set(antes) & set(ahora)
                            if antes[k] != ahora[k]),
        "antes": antes,
        "ahora": ahora,
    }


# Las extensiones que el pipeline sabe trocear, y las carpetas que saltea.
# Está duplicado de `CHUNKERS` / `CARPETAS_IGNORADAS` de `main_multimodal` a
# propósito: importarlo de ahí arrastra torch, chromadb y los tres tasks de
# troceo adentro de un servidor web que solo quiere listar archivos, y de paso
# ejecuta su `os.chdir` al importarse. Una prueba lee las dos definiciones con
# `ast` y falla si se separan.
EXTENSIONES_INGESTABLES = (".pdf", ".xlsx", ".py")
CARPETAS_IGNORADAS = {"old", "descartados", "__pycache__"}


def raiz_corpus() -> str:
    """`Ingestion/data/raw_data`, de donde el pipeline toma los documentos."""
    return os.path.join(raiz_datos(), "raw_data")


def documentos_ingestables() -> List[Dict[str, Any]]:
    """Los documentos del corpus, estén troceados o no.

    El nombre es el basename sin extensión, igual que en el manifiesto y en la
    metadata `file_name` del índice, para que un documento se llame igual en
    todos lados. `filtro` es lo que hay que pasarle a `--solo`: lleva la
    extensión porque sin ella "Plano distribucion electrica" también agarra
    "1_Plano distribucion electrica_v1.pdf", y procesar dos documentos cuando
    se pidió uno cuesta dinero.
    """
    raiz = raiz_corpus()
    salida = []
    for dirpath, dirnames, filenames in os.walk(raiz):
        dirnames[:] = sorted(d for d in dirnames
                             if d.lower() not in CARPETAS_IGNORADAS)
        for filename in sorted(filenames):
            if os.path.splitext(filename)[1].lower() not in EXTENSIONES_INGESTABLES:
                continue
            ruta = os.path.join(dirpath, filename)
            salida.append({
                "nombre": os.path.splitext(filename)[0],
                "archivo": filename,
                "filtro": filename,
                "relativa": os.path.relpath(ruta, raiz).replace(os.sep, "/"),
                "bytes": os.path.getsize(ruta),
            })
    return sorted(salida, key=lambda d: d["relativa"].lower())
