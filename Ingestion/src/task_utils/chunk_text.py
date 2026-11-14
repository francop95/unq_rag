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
import re
from typing import Any, Dict, List, Optional

# Cuántos elementos de cada lista de la descripción estructurada se incluyen.
#
# Era 30, y cortaba en silencio. Medido sobre el corpus: 34 de 427 chunks
# visuales perdían contenido, 358 elementos en total. En el plano codificado
# se iban 14 de 44 "Textos en la figura", y no cualquiera: TBEN-L4-8IOL,
# IO01, PS01, PS02, MCU01, SV01, SV02 y las notas de las compuertas. Toda la
# sección de control del plano, que el modelo SÍ había leído. Buscar "PS02"
# no encontraba el plano donde está dibujada.
#
# El tope existe para que una lista desbocada no se coma el chunk, y eso
# sigue valiendo; lo que no vale es cortar sin decirlo. Ahora son 200 —el
# peor caso del corpus son 65— y si igual corta, lo dice.
_MAX_LIST_ITEMS = 200
_MAX_TABLE_ROWS = 400

# Los recorridos de un diagrama no se transcriben: salen de mirar el dibujo y
# decidir qué línea llega a dónde. Es la única parte de la figura que el modelo
# de visión deduce y que, sin embargo, se guardaba con el mismo tono que una
# etiqueta copiada. Lo que eso produce no es teórico: la descripción del plano
# codificado afirma "QF03 → IF01 → VFD01", metiendo IF01 —que el inventario
# define como la interfaz de control entre el TBEN y el variador— en la línea
# de potencia del motor, porque su línea de llamada se parece a un conductor.
# La del plano original hace llegar el motor por KM1 y saltea el variador.
#
# El rótulo viaja dentro del texto del chunk, no en los metadatos, porque lo
# que tiene que cambiar de comportamiento es el modelo de respuesta y lo único
# que él ve es este texto. Qué hacer con la marca está en
# RETRIEVER_GPT_QNA_PROMPT, sección 4.
ROTULO_CONEXIONES = "Conexiones (interpretación del dibujo, no transcripción)"


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
        ("connections", ROTULO_CONEXIONES),
        ("labels", "Textos en la figura"),
        ("zones", "Zonas"),
        ("parts", "Piezas"),
        # Lo que la figura tiene escrito pero no se pudo leer. Va al texto a
        # propósito: que el modelo de respuesta sepa que ahí hay un dato
        # ilegible es lo que evita que lo complete solo.
        ("ilegible", "No legible en la figura"),
    ):
        value = notes.get(key)
        if isinstance(value, list) and value:
            visibles = [str(v) for v in value[:_MAX_LIST_ITEMS]]
            # Decir que la lista está cortada: así el modelo de respuesta
            # sabe que no es completa, en vez de creer que vio todo.
            if len(value) > _MAX_LIST_ITEMS:
                visibles.append(f"… y {len(value) - _MAX_LIST_ITEMS} más")
            parts.append(f"{label}: " + ", ".join(visibles))

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

    `texto_revisado` gana sobre todo lo demás. Lo pone la compuerta de revisión
    cuando una persona corrigió la figura —a mano o releyéndola— y es la única
    forma de que esa corrección llegue a los cuatro consumidores de esta
    función (enriquecedor, embeddings, indexer, validadores) sin tener que
    acordarse de aplicarla en cada uno: ese olvido es exactamente el que hizo
    que `diagram_visual` se perdiera en silencio en tres lugares.
    """
    revisado = chunk.get("texto_revisado")
    if isinstance(revisado, str) and revisado.strip():
        return revisado

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


def texto_del_chunk(n: Dict[str, Any]) -> str:
    """Arma el texto de una figura a partir de una descripción recién leída.

    Es el camino inverso de `readable_chunk_text`: ahí se renderiza lo que el
    troceo dejó en `notes`, acá lo que devolvió una relectura
    (`redescribir_figuras_con_valores.py`, o el botón de releer de la pantalla
    de revisión). Las dos tienen que producir el MISMO molde —`Tipo:`,
    `Componentes:`, el rótulo de conexiones—, porque los 300 chunks visuales
    del índice lo siguen y el prompt de respuesta lo da por sentado. Por eso
    viven en el mismo archivo: son dos renderizadores de un formato y tienen
    que moverse juntos.
    """
    def rot(x):
        if isinstance(x, dict):
            r = (x.get("rotulo") or "").replace("\n", " ")
            q = x.get("que_es") or ""
            return f"{r} ({q})" if q else r
        return str(x)

    partes = [str(n.get("resumen", "")).strip()]
    if n.get("tipo"):
        partes.append(f"Tipo: {n['tipo']}")
    disp = [rot(x) for x in (n.get("dispositivos") or []) if rot(x).strip()]
    if disp:
        partes.append("Componentes: " + ", ".join(disp))
    if n.get("conexiones"):
        partes.append(f"{ROTULO_CONEXIONES}: "
                      + ", ".join(str(c) for c in n["conexiones"]))
    if n.get("textos_en_la_figura"):
        partes.append("Textos en la figura: "
                      + ", ".join(str(t) for t in n["textos_en_la_figura"]))
    if n.get("valores_nominales"):
        partes.append("Valores nominales: "
                      + json.dumps(n["valores_nominales"], ensure_ascii=False))
    # Las dudas van dentro del texto a propósito: que el modelo de respuesta
    # sepa qué NO se pudo leer es justamente lo que evita que lo complete solo.
    if n.get("dudas"):
        partes.append("No legible en la figura: " + "; ".join(str(d) for d in n["dudas"]))
    return "\n".join(p for p in partes if p)


# Cómo se llama en `notes` cada campo que devuelve una relectura. Las dos
# pasadas de corrección —redescribir_planos y redescribir_figuras_con_valores—
# responden en español; `notes` es lo que escribió el troceo, en inglés.
#
# Sin este mapeo la corrección no se puede guardar en el JSON del troceo y hay
# que meterla directo en el índice, que es exactamente lo que dejó 72 figuras
# con un texto en disco y otro distinto en Chroma.
_CAMPOS_DE_RELECTURA = (
    ("resumen", "description"),
    ("tipo", "diagram_type"),
    ("dispositivos", "components"),
    ("conexiones", "connections"),
    ("textos_en_la_figura", "labels"),
    ("valores_nominales", "ratings"),
    ("dudas", "ilegible"),
)


def _una_linea(x: Any) -> str:
    """Aplasta a un renglón lo que en el dibujo venía en varios.

    Los rótulos de un plano están partidos como entran en la chapa ("Inter.
    dif.\\nIn: 40A\\n30mA"), y la relectura por mosaicos los devuelve tal cual.
    Esas listas se renderizan unidas por comas: un salto de línea en el medio
    corta el renglón al mostrarlo y, en la tabla de conexiones, parte el
    nombre del componente en tres.
    """
    return re.sub(r"\s+", " ", str(x)).strip()


def notas_desde_relectura(nueva: Dict[str, Any]) -> Dict[str, Any]:
    """Lleva una descripción recién leída a la forma que guarda el troceo.

    Los dispositivos vienen como dicts `{rotulo, que_es}` y se aplanan igual
    que en `texto_del_chunk`, para que las dos formas de renderizar la misma
    figura digan lo mismo.
    """
    def rot(x):
        if isinstance(x, dict):
            r = x.get("rotulo") or ""
            q = x.get("que_es") or ""
            return _una_linea(f"{r} ({q})" if q else r)
        return _una_linea(x)

    notas: Dict[str, Any] = {}
    for origen, destino in _CAMPOS_DE_RELECTURA:
        valor = nueva.get(origen)
        if not valor:
            continue
        if origen == "dispositivos":
            valor = [rot(x) for x in valor if rot(x).strip()]
        elif isinstance(valor, list):
            valor = [_una_linea(x) for x in valor]
        notas[destino] = valor
    return notas
