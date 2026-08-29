"""
Representación textual canónica de un chunk
===========================================

Única fuente de verdad para convertir un chunk del pipeline en el texto legible
que se embebe y se almacena en el índice.

Existe porque esta lógica estaba duplicada en tres lugares (el task de
embeddings, el indexer y el enriquecedor contextual) y cada vez que aparecía un
nuevo `content_type` había que acordarse de actualizar los tres: los chunks
`diagram_visual` / `diagram_text` / `diagram_description` y las partes de tabla
se perdieron en silencio justamente por eso.
"""

import json
from typing import Any, Dict, List, Optional

# Cuántos elementos de cada lista de la descripción estructurada se incluyen
_MAX_LIST_ITEMS = 30
_MAX_TABLE_ROWS = 400


def _parse_payload(original: Any) -> Optional[Dict[str, Any]]:
    """Intenta obtener el dict de `original_chunk` (viene serializado en JSON)."""
    if isinstance(original, dict):
        return original
    if isinstance(original, str):
        try:
            parsed = json.loads(original)
        except (ValueError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _rows_to_text(rows: List[Any]) -> str:
    lines = []
    for row in rows[:_MAX_TABLE_ROWS]:
        if isinstance(row, (list, tuple)):
            lines.append("\t".join("" if c is None else str(c) for c in row))
        else:
            lines.append(str(row))
    return "\n".join(lines)


def _describe_figure(notes: Any) -> str:
    """
    Texto buscable de una figura a partir de sus `notes`.

    Con la pasada dedicada de figuras, `notes` es un dict rico
    (description/components/connections/ratings/labels). Se aplanan TODOS esos
    campos, no solo la descripción: los nombres de componentes y las etiquetas
    legibles del plano son justamente lo que un técnico busca ("borne X1:3",
    "contactor K1", "480V").
    """
    if not isinstance(notes, dict):
        return str(notes or "")

    parts: List[str] = []

    if notes.get("description"):
        parts.append(str(notes["description"]))

    if notes.get("diagram_type"):
        parts.append(f"Tipo: {notes['diagram_type']}")

    for key, label in (
        ("components", "Componentes"),
        ("connections", "Conexiones"),
        ("labels", "Textos en la figura"),
        ("zones", "Zonas"),
        ("parts", "Piezas"),
    ):
        value = notes.get(key)
        if isinstance(value, list) and value:
            parts.append(f"{label}: " + ", ".join(str(v) for v in value[:_MAX_LIST_ITEMS]))

    for key, label in (("ratings", "Valores nominales"), ("dimensions", "Dimensiones"),
                       ("equipment_locations", "Ubicaciones")):
        value = notes.get(key)
        if isinstance(value, dict) and value:
            parts.append(f"{label}: " + json.dumps(value, ensure_ascii=False))

    if parts:
        return "\n".join(parts)

    # Sin campos conocidos: mejor el JSON que nada
    return json.dumps(notes, ensure_ascii=False)


def readable_chunk_text(chunk: Dict[str, Any]) -> str:
    """
    Devuelve el texto legible de un chunk, según su `content_type`.

    Nunca lanza: ante contenido inesperado devuelve la mejor aproximación
    disponible (o cadena vacía).
    """
    original = chunk.get("original_chunk", "")
    ctype = (chunk.get("content_type") or "").lower()
    payload = _parse_payload(original)

    # Tablas (incluidas sus partes, que conservan content_type "table")
    if ctype == "table" and payload is not None:
        if payload.get("table_markdown"):
            return str(payload["table_markdown"])
        rows = (payload.get("table_json") or {}).get("rows")
        if isinstance(rows, list) and rows:
            return _rows_to_text(rows)
        return json.dumps(payload, ensure_ascii=False)

    # Figuras: imagen suelta o faceta visual de un diagrama
    if ctype in ("image", "diagram_visual") and payload is not None:
        return _describe_figure(payload.get("notes") or payload.get("alt") or "")

    # Descripción estructurada producida por ElectricalDiagramProcessor
    if ctype == "diagram_description":
        if payload is not None:
            return _describe_figure(payload)
        return str(original or "")

    # Texto plano, super-chunks, OCR de diagramas y preguntas sintéticas
    # (en estas últimas `original_chunk` ya es el texto del chunk padre)
    if isinstance(original, str):
        return original
    return json.dumps(original, ensure_ascii=False) if original else ""
