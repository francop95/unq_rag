"""
Glosario de nomenclatura del secadero
=====================================

El código acordado de cada componente (QD01, SSR01-SSR03, VFD01…), su nombre
técnico y los alias con que aparece en los planos. Lo genera
`Ingestion/scripts/generar_nomenclatura.py` desde la hoja `Nomenclatura` del
inventario, y se versiona como JSON para poder revisarlo en un diff cuando el
inventario cambie.

Sirve para que una respuesta diga "el variador (VFD01)" en lugar de "el
variador": en un tablero con cuatro interruptores termomagnéticos, decir
"el interruptor" no alcanza para ir a buscarlo.
"""

import json
import logging
import os
from typing import Any, Dict, List

logger = logging.getLogger("app.Nomenclatura")

RUTA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nomenclatura.json")

_cache: List[Dict[str, Any]] | None = None


def componentes() -> List[Dict[str, Any]]:
    """El glosario, leído una sola vez. Lista vacía si no está: la respuesta
    sigue funcionando sin códigos, que es peor pero no roto."""
    global _cache
    if _cache is None:
        try:
            with open(RUTA, encoding="utf-8") as fh:
                _cache = json.load(fh).get("componentes", [])
            logger.info(f"[Nomenclatura] {len(_cache)} componentes cargados")
        except Exception as e:
            logger.warning(f"[Nomenclatura] no se pudo cargar {RUTA}: {e}")
            _cache = []
    return _cache


def bloque_para_prompt() -> str:
    """
    El glosario como texto para el prompt, o cadena vacía si no hay datos.

    Se pasa entero —son 32 componentes, unos 600 tokens— en vez de filtrarlo por
    la pregunta. Filtrar exigiría saber de antemano qué componentes va a
    mencionar la respuesta, que es justamente lo que no se sabe hasta tenerla.
    """
    comps = componentes()
    if not comps:
        return ""

    lineas = []
    for c in comps:
        linea = f"{c['codigo']} = {c['nombre']}"
        if c.get("alias"):
            linea += f" (en los planos: {c['alias']})"
        lineas.append(linea)

    return (
        "\nNOMENCLATURA DEL EQUIPO (código acordado = componente):\n"
        + "\n".join(lineas)
        + "\n\nCuando menciones un actuador, sensor, protección o equipo que esté en\n"
          "esta lista, agregá su código entre paréntesis la primera vez que aparezca:\n"
          '"los relés de estado sólido (SSR01-SSR03)", "el variador (VFD01)".\n'
          "Reglas:\n"
          "- Solo para componentes de la lista. Si no está, no inventes un código.\n"
          "- Una vez por respuesta y por componente, no en cada mención.\n"
          "- El código acompaña al nombre, no lo reemplaza: quien pregunta puede no\n"
          "  conocer la nomenclatura todavía.\n"
    )
