"""
Grafo de componentes: consulta e inyección en el prompt
=======================================================

Qué resuelve
------------
El sistema contesta bien las relaciones de un salto, porque cada fila de la
tabla de conexiones es una arista y el retrieval la encuentra. Falla en la
agregación: a "listá los componentes que dependen de PS01" respondió con las
protecciones de aguas arriba —QD01, QF01…— confundiendo "aparece en el mismo
plano" con "depende de". La única arista documentada es PS01 → IO01.

Ese error no se arregla con mejor retrieval: el modelo tenía los fragmentos
correctos y dedujo mal la dirección. Lo que falta es afirmarle explícitamente
qué conexiones existen y cuáles no.

Cómo
----
Cuando la pregunta menciona un componente, se le pasa su vecindario: qué lo
alimenta, qué alimenta él, a qué sistema pertenece y qué discrepancias
abiertas lo tocan. Y se dice explícitamente que esa lista es completa, que es
lo que permite responder "solo IO01" en vez de inventar.
"""

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("app.Grafo")

RUTA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "grafo.json")

_cache: Optional[Dict[str, Any]] = None


def grafo() -> Dict[str, Any]:
    """El grafo, leído una sola vez. Vacío si no está: la respuesta sigue
    funcionando sin relaciones, que es peor pero no roto."""
    global _cache
    if _cache is None:
        try:
            with open(RUTA, encoding="utf-8") as fh:
                _cache = json.load(fh)
            logger.info(f"[Grafo] {len(_cache.get('nodos', {}))} nodos, "
                        f"{len(_cache.get('aristas', []))} aristas")
        except Exception as e:
            logger.warning(f"[Grafo] no se pudo cargar {RUTA}: {e}")
            _cache = {"nodos": {}, "aristas": [], "discrepancias": []}
    return _cache


def codigos_mencionados(texto: str) -> List[str]:
    """
    Los códigos del grafo que aparecen en un texto.

    Se cotejan contra los nodos reales en vez de usar solo un patrón: un patrón
    como [A-Z]{2,4}\\d{2} también atrapa "IP65", "M12" o "C15", que no son
    componentes. El grafo es la lista de lo que existe.
    """
    g = grafo()
    nodos = g.get("nodos", {})
    encontrados = []
    for codigo in nodos:
        if not codigo or not re.match(r"^[A-Z]", codigo):
            continue
        # Límite de palabra para que SSR01 no matchee dentro de SSR011.
        if re.search(rf"\b{re.escape(codigo)}\b", texto, re.IGNORECASE):
            encontrados.append(codigo)
    return sorted(set(encontrados))


def vecindario(codigo: str) -> Dict[str, Any]:
    """Las aristas que tocan a un componente, separadas por dirección."""
    g = grafo()
    entra, sale, bidir = [], [], []
    for a in g.get("aristas", []):
        if a["destino"] == codigo and not a.get("bidireccional"):
            entra.append(a)
        elif a["origen"] == codigo and not a.get("bidireccional"):
            sale.append(a)
        elif a.get("bidireccional") and codigo in (a["origen"], a["destino"]):
            bidir.append(a)
    return {"entra": entra, "sale": sale, "bidireccional": bidir}


def _describir(codigo: str) -> str:
    n = grafo().get("nodos", {}).get(codigo, {})
    return f"{codigo} ({n.get('nombre', '?')})" if n else codigo


def caminos(origen: str, destino: str, max_saltos: int = 4) -> List[List[str]]:
    """
    Los caminos documentados entre dos componentes, hasta `max_saltos`.

    Hace falta porque afirmar solo el vecindario inmediato dejó al modelo sin
    poder encadenar: a "¿cuál es el camino de TH01 al variador?" respondió que
    TH01 solo se conecta a IO01 y que no hay conexión documentada con el
    variador. Las dos cosas son ciertas por separado y la conclusión es falsa:
    el camino existe, TH01 → IO01 → VFD01. Un grafo que no se recorre no
    agrega nada sobre una tabla.
    """
    g = grafo()
    vecinos: Dict[str, List[str]] = {}
    for a in g.get("aristas", []):
        vecinos.setdefault(a["origen"], []).append(a["destino"])
        if a.get("bidireccional"):
            vecinos.setdefault(a["destino"], []).append(a["origen"])

    encontrados: List[List[str]] = []
    pila = [[origen]]
    while pila:
        camino = pila.pop()
        if len(camino) > max_saltos + 1:
            continue
        actual = camino[-1]
        if actual == destino and len(camino) > 1:
            encontrados.append(camino)
            continue
        for v in vecinos.get(actual, []):
            if v not in camino:  # sin ciclos
                pila.append(camino + [v])
    # Los más cortos primero: son los que describen la ruta real.
    return sorted(encontrados, key=len)[:3]


def bloque_para_prompt(pregunta: str, contexto: str = "") -> str:
    """
    Las relaciones documentadas de los componentes que menciona la consulta.

    Se mira la pregunta y también el contexto recuperado: quien pregunta "qué
    alimenta al TBEN" no escribe IO01, pero el contexto sí lo trae.
    """
    codigos = codigos_mencionados(f"{pregunta}\n{contexto}")
    if not codigos:
        return ""

    g = grafo()
    # Un tope: con muchos componentes en el contexto el bloque crece más que la
    # respuesta. Se priorizan los de la pregunta, que es lo que se preguntó.
    de_pregunta = set(codigos_mencionados(pregunta))
    codigos = sorted(codigos, key=lambda c: (c not in de_pregunta, c))[:8]

    lineas = []
    for c in codigos:
        n = g["nodos"].get(c, {})
        v = vecindario(c)
        partes = [f"- {_describir(c)}"]
        if n.get("sistema"):
            partes.append(f"  sistema: {n['sistema']}")
        if v["entra"]:
            partes.append("  lo alimenta/controla: "
                          + ", ".join(_describir(a["origen"]) for a in v["entra"]))
        if v["sale"]:
            partes.append("  alimenta/controla a: "
                          + ", ".join(_describir(a["destino"]) for a in v["sale"]))
        if v["bidireccional"]:
            otros = [a["destino"] if a["origen"] == c else a["origen"] for a in v["bidireccional"]]
            partes.append("  conectado con: " + ", ".join(_describir(o) for o in otros))
        if not (v["entra"] or v["sale"] or v["bidireccional"]):
            partes.append("  sin conexiones documentadas en el inventario")

        abiertas = [d for d in g.get("discrepancias", [])
                    if c in d.get("componentes", []) and d.get("estado", "").lower() != "resuelta"]
        if abiertas:
            partes.append("  discrepancia abierta: "
                          + "; ".join(f"{d['id']} {d['tema']}" for d in abiertas[:2]))
        lineas.append("\n".join(partes))

    # Caminos entre los componentes nombrados, para que el modelo pueda
    # encadenar sin inventar.
    rutas = []
    for i, a in enumerate(codigos):
        for b in codigos[i + 1:]:
            for c in caminos(a, b) + caminos(b, a):
                if len(c) > 2:  # los de un salto ya están en el vecindario
                    rutas.append(" → ".join(c))
    rutas = sorted(set(rutas), key=len)[:6]

    bloque_rutas = ""
    if rutas:
        bloque_rutas = ("\n\nCAMINOS DOCUMENTADOS entre esos componentes:\n"
                        + "\n".join(f"- {r}" for r in rutas))

    return (
        "\nCONEXIONES DOCUMENTADAS (del inventario revisado):\n"
        + "\n".join(lineas)
        + bloque_rutas
        + "\n\nEsta lista de conexiones es COMPLETA para los componentes nombrados: lo que\n"
          "no figura acá no está documentado. Si te preguntan qué depende de un\n"
          "componente, respondé con lo que figura y decí explícitamente que es lo\n"
          "único documentado. No deduzcas conexiones porque dos componentes aparezcan\n"
          "en el mismo plano o en el mismo fragmento: compartir una página no es\n"
          "estar conectados.\n"
          "Sí podés encadenar varias conexiones de la lista para describir un\n"
          "recorrido: si A alimenta a B y B alimenta a C, el camino de A a C existe.\n"
          "Lo que no se puede es agregar un tramo que no esté en la lista.\n"
    )
