"""
Parámetros que cada modelo acepta
=================================

Los modelos de razonamiento (familia gpt-5, serie o1/o3) rechazan `temperature`
con un 400: solo admiten su valor por defecto. El pipeline la pasa en los tres
lugares donde llama al modelo —0.2 en el chunking de página, 0.0 en la pasada
de figuras y en el enriquecimiento— así que cambiar el modelo por uno de esa
familia hace fallar TODAS las llamadas, no algunas.

Es el tipo de incompatibilidad que no se ve leyendo el código: la firma es la
misma, el parámetro existe en la API, y el error recién aparece en la primera
llamada real. Este módulo lo resuelve en un solo lugar en vez de repetir la
condición en cada punto de llamada.

Hay dos capas a propósito:

1. Una lista de prefijos conocidos, para no gastar una llamada fallida cuando
   ya se sabe que el modelo no la acepta.
2. `recordar_rechazo()`, que aprende del 400 en caliente. La lista de arriba
   envejece —salen modelos nuevos— y sin esto un modelo desconocido que
   rechace `temperature` rompería la corrida entera en lugar de reintentar
   una vez sin el parámetro.
"""

from typing import Any, Dict, Set

# Familias que solo aceptan el temperature por defecto.
SIN_TEMPERATURE = ("gpt-5", "o1", "o1-", "o3", "o3-", "o4-")

# Modelos que devolvieron un 400 por `temperature` durante esta corrida.
_rechazaron: Set[str] = set()


def acepta_temperature(model: str) -> bool:
    m = (model or "").strip().lower()
    if m in _rechazaron:
        return False
    return not any(m == p or m.startswith(p) for p in SIN_TEMPERATURE)


def recordar_rechazo(model: str) -> None:
    """Marca el modelo tras un 400 por `temperature`, para no repetir el error."""
    if model:
        _rechazaron.add(model.strip().lower())


def es_error_de_temperature(e: Exception) -> bool:
    texto = str(e).lower()
    return "temperature" in texto and ("unsupported" in texto or "does not support" in texto)


def chat_kwargs(model: str, temperature: float, **extra: Any) -> Dict[str, Any]:
    """
    Los kwargs de `chat.completions.create` para este modelo.

    Devuelve `temperature` solo si el modelo la acepta. El resto pasa igual.
    """
    kwargs: Dict[str, Any] = dict(extra)
    if acepta_temperature(model):
        kwargs["temperature"] = temperature
    return kwargs
