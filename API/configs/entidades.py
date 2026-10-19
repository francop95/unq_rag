"""
Resolución de entidades: qué componentes menciona un texto
==========================================================

El problema que resuelve: el mismo variador se llama `VFD01` en el inventario,
`INV1` en la tesis y `PowerFlex 4M` en el manual del fabricante, que son 3.626
vectores y no menciona `VFD01` ni una vez. Nada en el índice dice que son el
mismo equipo, así que una consulta trae lo que se parece semánticamente, no lo
que habla del mismo aparato.

Esto mapea texto → códigos de componente, en tres niveles de especificidad, y
cada acierto dice por qué nivel entró. El nivel importa: un chunk que dice
`VFD01` habla del variador sin lugar a dudas; uno que dice `F1` puede estar
hablando de un guardamotor, de un fusible o de una tecla de función.

Niveles
-------
`codigo`    el código acordado: VFD01, SSR01. Inequívoco.
`especifico` un alias que solo puede ser ese componente en este corpus:
            "PowerFlex 4M", "DP1213", "Arduino uno", "TBEN". Hay uno solo de
            cada uno, así que nombrarlo identifica el equipo.
`debil`     tokens cortos o genéricos: "PC", "X1", "F1", "C4", "válvula". Se
            detectan y se devuelven aparte, pero NO alcanzan solos para afirmar
            que el texto habla de ese componente.

Sobre el campo `alias` del inventario
-------------------------------------
Mezcla alias reales con notas editoriales. El de los SSR dice "KM1 no equivale
individualmente a este SSR", que es exactamente lo contrario de un alias:
tomarlo como tal etiquetaría como relé cada mención de KM1. Esas entradas se
descartan por su redacción, y la lista de lo descartado se puede inspeccionar
con `alias_descartados()` en vez de quedar escondida.
"""

import json
import logging
import os
import re
import unicodedata
from functools import lru_cache
from typing import Dict, List, Set, Tuple

logger = logging.getLogger("app.entidades")

_RUTA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nomenclatura.json")

# Una entrada de `alias` redactada como nota, no como nombre alternativo.
_ES_NOTA = re.compile(
    r"no equivale|distintas variantes|extremo externo|pendiente|sin confirmar|"
    r"no confirmad|según|segun",
    re.IGNORECASE,
)

# Alias que existen pero son demasiado cortos o comunes para identificar solos.
# Se listan a mano y no por longitud: "TBEN" tiene 4 letras y es inequívoco en
# este corpus, mientras que "PC" tiene 2 y aparece en cualquier lado.
_DEBILES = {
    "pc", "x1", "x2", "x3", "f1", "m1", "c4", "c5",
    "valvula", "valvula extractora", "forzador", "servo 1", "servo 2",
}


# Documentos que SON la documentación de un componente. Una página del manual
# del variador que habla del parámetro P112 no dice "PowerFlex" en ninguna
# parte, pero es documentación de VFD01: sin esto, el 85% de ese manual queda
# sin vincular al equipo que describe.
#
# Es una afirmación sobre el corpus, no una inferencia: cada entrada dice de qué
# equipo es manual cada archivo. Se mantiene a mano porque son pocos y porque
# equivocarse acá etiqueta un documento entero.
DOCUMENTO_DE = {
    "manual powerflex 4m.pdf": ["VFD01"],
    "tben-L4-8ioL.pdf": ["IO01"],
    "Secadero v01 On TBEN-L4-8IOL.pdf": ["IO01"],
    # ARGEE es el entorno de programación que corre EN el TBEN, así que su
    # manual documenta cómo se programa ese módulo.
    "ARGEE_Reference_Manual.pdf": ["IO01"],
}


# Nombres comunes que en ESTA instalación identifican un componente porque hay
# uno solo. No son alias del fabricante: son una afirmación sobre el secadero.
#
# Hace falta porque el programa de control escribe `Variador * 160` a secas, sin
# el código ni la marca, y esa línea es justamente la que calcula la salida del
# equipo. Sin esto, el chunk más específico sobre el variador no queda ligado a
# él.
#
# Si algún día hubiera un segundo variador, esto pasa de correcto a erróneo. Por
# eso vive acá, separado y con el riesgo escrito, y no mezclado con los alias
# del inventario.
UNICO_EN_LA_INSTALACION = {
    "variador": "VFD01",
    "guardamotor": "QM01",
    "arduino": "MCU01",
}


def componentes_del_documento(file_name: str) -> List[str]:
    """Los componentes que un documento documenta por completo, si los hay."""
    return list(DOCUMENTO_DE.get(file_name or "", []))


def _normalizar(texto: str) -> str:
    """Sin tildes y en minúsculas, para comparar sin depender de la acentuación."""
    t = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


@lru_cache(maxsize=1)
def _tabla() -> Tuple[Dict[str, List[Tuple[str, str]]], List[Tuple[str, str]]]:
    """(patrón compilado -> [(código, nivel)], alias descartados).

    Se agrupa por patrón y no por código porque un alias puede pertenecer a más
    de un componente: "Grupo 3 x 2 kW" nombra a R01, R02 y R03 por igual, y
    quedarse con uno solo perdería los otros dos.
    """
    try:
        with open(_RUTA, encoding="utf-8") as f:
            comps = json.load(f).get("componentes", [])
    except (OSError, ValueError) as e:
        logger.warning(f"[Entidades] no se pudo leer la nomenclatura: {e}")
        return {}, []

    por_termino: Dict[str, List[Tuple[str, str]]] = {}
    descartados: List[Tuple[str, str]] = []

    def agregar(termino: str, codigo: str, nivel: str) -> None:
        clave = _normalizar(termino).strip()
        if len(clave) < 2:
            return
        por_termino.setdefault(clave, [])
        if (codigo, nivel) not in por_termino[clave]:
            por_termino[clave].append((codigo, nivel))

    for c in comps:
        codigo = (c.get("codigo") or "").strip()
        if not codigo:
            continue
        agregar(codigo, codigo, "codigo")
        # El nombre técnico completo también identifica, y es lo que usa la prosa.
        if c.get("nombre"):
            agregar(c["nombre"], codigo, "especifico")
        for comun, duenio in UNICO_EN_LA_INSTALACION.items():
            if duenio == codigo:
                agregar(comun, codigo, "especifico")
        for pieza in re.split(r"[;,]", c.get("alias") or ""):
            pieza = pieza.strip()
            if not pieza:
                continue
            if _ES_NOTA.search(pieza):
                descartados.append((codigo, pieza))
                continue
            nivel = "debil" if _normalizar(pieza) in _DEBILES else "especifico"
            agregar(pieza, codigo, nivel)

    return por_termino, descartados


def alias_descartados() -> List[Tuple[str, str]]:
    """Las entradas de `alias` que son notas y no nombres. Para poder revisarlas."""
    return _tabla()[1]


@lru_cache(maxsize=4096)
def _patron(termino: str) -> "re.Pattern":
    # Límite de palabra alfanumérico, SIN excluir el guion: "M1" no debe entrar
    # dentro de "M12" —el 2 es alfanumérico y lo bloquea— pero "TBEN" sí tiene
    # que entrar dentro de "TBEN-L4-8IOL", que es como el manual del módulo se
    # nombra a sí mismo. Excluir el guion dejaba ese manual entero sin etiquetar.
    return re.compile(rf"(?<!\w){re.escape(termino)}(?!\w)", re.IGNORECASE)


def resolver(texto: str) -> Dict[str, Set[str]]:
    """Los componentes que menciona un texto, agrupados por nivel.

    Devuelve {"codigo": {...}, "especifico": {...}, "debil": {...}}. Quien lo use
    decide qué niveles acepta: para etiquetar el índice conviene `codigo` y
    `especifico`; `debil` sirve como señal de apoyo, nunca sola.
    """
    salida: Dict[str, Set[str]] = {"codigo": set(), "especifico": set(), "debil": set()}
    if not texto:
        return salida
    plano = _normalizar(texto)
    por_termino, _ = _tabla()
    for termino, duenos in por_termino.items():
        if termino not in plano:          # descarte barato antes del regex
            continue
        if not _patron(termino).search(plano):
            continue
        for codigo, nivel in duenos:
            salida[nivel].add(codigo)
    # Un componente nombrado por su código no necesita figurar además como débil.
    salida["debil"] -= salida["codigo"] | salida["especifico"]
    salida["especifico"] -= salida["codigo"]
    return salida


def etiquetas(texto: str) -> List[str]:
    """Los códigos que se pueden afirmar de un texto: nivel `codigo` + `especifico`."""
    r = resolver(texto)
    return sorted(r["codigo"] | r["especifico"])
