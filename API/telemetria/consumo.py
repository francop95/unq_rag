"""
Medidor de consumo de la API, por consulta
==========================================

La ingesta ya tenía su medidor (`Ingestion/src/task_utils/usage_meter.py`) y
por eso se sabe al centavo lo que cuesta construir el índice. La API no tenía
ninguno: cada respuesta de OpenAI trae su campo `usage` y los tres puntos de
llamada lo tiraban, así que la pregunta "¿cuánto sale responder una consulta?"
no tenía respuesta, ni siquiera aproximada.

Esto acumula los tokens por `query_id` —una consulta hace al menos dos
llamadas, la de intención y la de respuesta— y los convierte a dólares. El
resultado viaja en `extra_json` de la tabla `ejecuciones`, así que el costo
queda junto al resto de lo que pasó en esa consulta y se puede promediar con
una consulta SQL.

Sobre los precios: los tokens son medidos y exactos, los precios son de
referencia y cambian sin aviso. Se pisan con la variable de entorno
`OPENAI_PRECIOS`, en formato `modelo:entrada_por_1M:salida_por_1M` separados
por coma. Un modelo sin tarifa cuenta igual sus tokens y se reporta aparte, que
es mejor que asumir cero y dar un total que miente.
"""

import logging
import os
import threading
from collections import defaultdict
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("app.consumo")

# USD por 1M de tokens: (entrada, salida).
PRECIOS_USD: Dict[str, Tuple[float, float]] = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4-0613": (30.00, 60.00),
    "text-embedding-3-large": (0.13, 0.0),
    "text-embedding-3-small": (0.02, 0.0),
}

_lock = threading.Lock()
# query_id -> modelo -> {"entrada": n, "salida": n, "llamadas": n}
_por_consulta: Dict[str, Dict[str, Dict[str, int]]] = defaultdict(
    lambda: defaultdict(lambda: {"entrada": 0, "salida": 0, "llamadas": 0})
)


def _precios() -> Dict[str, Tuple[float, float]]:
    tabla = dict(PRECIOS_USD)
    crudo = os.getenv("OPENAI_PRECIOS", "").strip()
    for item in filter(None, (p.strip() for p in crudo.split(","))):
        try:
            modelo, entrada, salida = item.split(":")
            tabla[modelo.strip()] = (float(entrada), float(salida))
        except ValueError:
            logger.warning(f"[Consumo] OPENAI_PRECIOS mal formado, se ignora: {item!r}")
    return tabla


def _tarifa(modelo: str, tabla: Dict[str, Tuple[float, float]]):
    """Coincidencia por prefijo más largo: `gpt-4.1-2025-04-14` cobra como `gpt-4.1`."""
    candidatos = [k for k in tabla if modelo.startswith(k)]
    return tabla[max(candidatos, key=len)] if candidatos else None


def anotar(query_id: Optional[str], modelo: str, usage: Any) -> None:
    """Suma el `usage` de una respuesta. Nunca lanza: medir no puede romper responder."""
    if not query_id or usage is None:
        return
    try:
        # Chat Completions dice prompt/completion; Responses dice input/output.
        entrada = getattr(usage, "prompt_tokens", None)
        if entrada is None:
            entrada = getattr(usage, "input_tokens", 0)
        salida = getattr(usage, "completion_tokens", None)
        if salida is None:
            salida = getattr(usage, "output_tokens", 0)
        with _lock:
            acc = _por_consulta[query_id][modelo or "desconocido"]
            acc["entrada"] += int(entrada or 0)
            acc["salida"] += int(salida or 0)
            acc["llamadas"] += 1
    except Exception as e:  # pragma: no cover - defensivo a propósito
        logger.debug(f"[Consumo] no se pudo anotar el uso: {e}")


def cerrar(query_id: Optional[str]) -> Dict[str, Any]:
    """Devuelve el resumen de la consulta y la saca de memoria.

    Se llama una sola vez, al registrar la ejecución. Si se llamara dos veces la
    segunda daría cero, que es correcto: lo ya contado no se cuenta de nuevo.
    """
    if not query_id:
        return {}
    with _lock:
        por_modelo = _por_consulta.pop(query_id, None)
    if not por_modelo:
        return {}

    tabla = _precios()
    usd = 0.0
    sin_tarifa, detalle = [], {}
    for modelo, acc in por_modelo.items():
        tarifa = _tarifa(modelo, tabla)
        if tarifa:
            usd += acc["entrada"] / 1e6 * tarifa[0] + acc["salida"] / 1e6 * tarifa[1]
        else:
            sin_tarifa.append(modelo)
        detalle[modelo] = dict(acc)

    resumen: Dict[str, Any] = {
        "tokens_entrada": sum(a["entrada"] for a in por_modelo.values()),
        "tokens_salida": sum(a["salida"] for a in por_modelo.values()),
        "llamadas": sum(a["llamadas"] for a in por_modelo.values()),
        "usd": round(usd, 6),
        "por_modelo": detalle,
    }
    if sin_tarifa:
        # Un total que omite un modelo sin precio no es un total: se dice.
        resumen["sin_tarifa"] = sorted(sin_tarifa)
    return resumen


def olvidar(query_id: Optional[str]) -> None:
    """Descarta lo acumulado sin reportarlo, para una consulta que no se registra."""
    if query_id:
        with _lock:
            _por_consulta.pop(query_id, None)
