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
from functools import lru_cache
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


@lru_cache(maxsize=1)
def _datos_inventario() -> dict:
    """Código -> los datos documentados de su fila del inventario.

    Viene de `Ingestion/scripts/generar_inventario.py`, que vuelca el Excel a
    registros con clave. El dato que importa es la columna "Datos documentados":
    corrientes, tensiones y modelos que hoy solo existían dentro de un chunk de
    tabla en Markdown, recuperables por similitud y no por búsqueda exacta.

    Si el archivo no está, el glosario sigue funcionando sin esta parte: es una
    mejora del bloque, no un requisito.
    """
    ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "inventario.json")
    try:
        with open(ruta, encoding="utf-8") as f:
            inv = json.load(f)
    except (OSError, ValueError):
        return {}
    por_id = {r["id_registro"]: r for r in inv.get("registros", [])}
    salida = {}
    for codigo, ids in (inv.get("por_codigo") or {}).items():
        datos = [por_id[i]["datos"] for i in ids if por_id.get(i, {}).get("datos")]
        funcion = next((por_id[i]["funcion"] for i in ids if por_id.get(i, {}).get("funcion")), "")
        if datos or funcion:
            # Varias filas por código pasa con los grupos (SSR01-SSR03): se
            # conserva la primera, que es la que describe la unidad.
            salida[codigo] = {"datos": datos[0] if datos else "", "funcion": funcion}
    return salida


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
        extras = []
        if c.get("alias"):
            extras.append(f"en los planos: {c['alias']}")
        if c.get("ubicacion"):
            extras.append(f"ubicación: {c['ubicacion']}")
        if c.get("en_plano"):
            extras.append(f"plano rotulado: {c['en_plano']}")
        # Los datos de placa van en el glosario y no en un bloque aparte: un
        # segundo bloque repetiría código, nombre y ubicación de los 32
        # componentes para agregar una columna.
        inv = _datos_inventario().get(c["codigo"]) or {}
        if inv.get("datos"):
            extras.append(f"documentado: {inv['datos']}")
        if extras:
            linea += " [" + " · ".join(extras) + "]"
        lineas.append(linea)

    return (
        "\nNOMENCLATURA DEL EQUIPO (código = componente [dónde encontrarlo · qué dice la documentación]):\n"
        + "\n".join(lineas)
        + "\n\nCuando menciones un actuador, sensor, protección o equipo de esta lista,\n"
          "identificalo la primera vez que aparezca:\n"
          '  "los relés de estado sólido (SSR01-SSR03)"\n'
          '  "el variador (VFD01)"\n'
          "Y si la pregunta es de diagnóstico, inspección o de dónde está algo,\n"
          "agregá también dónde encontrarlo:\n"
          '  "el interruptor de calefacción (QF02, en el tablero; plano P1, ramal\n'
          '   calefacción)"\n'
          "Reglas:\n"
          "- Solo para componentes de la lista. Si no está, no inventes un código.\n"
          "- Una vez por respuesta y por componente, no en cada mención.\n"
          "- El código acompaña al nombre, no lo reemplaza: quien pregunta puede no\n"
          "  conocer la nomenclatura todavía.\n"
          "- La ubicación solo cuando aporte. En una pregunta conceptual sobre cómo\n"
          "  funciona algo, repetir dónde está montado estorba más de lo que ayuda.\n"
    )
