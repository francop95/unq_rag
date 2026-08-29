"""
¿El texto recuperado CONTIENE la respuesta?

Por qué hace falta, además del recall por página: el eval medía si alguna de las páginas
correctas aparecía en el top-10, y un acierto de página que no trae la respuesta cuenta
igual que uno que sí. Medido sobre los 17 códigos de fallo del manual, la diferencia es
brutal: 16 de 17 "llegaban" por página, pero solo 8 de 17 traían la FILA que explica esa
falla. El resto devolvía otra parte de la misma tabla — para el técnico, un fallo completo.

La otra ventaja es de método: esta verificación **no depende de los límites de los chunks
ni del número de página**, así que sobrevive a un re-chunking. Es lo que permite comparar
dos ingestas, algo que el recall por página no puede hacer (medido: entre dos ingestas el
33% de los chunk_id del gold desaparecen y el 48% de los textos fuente cambian de lugar).

Estrategia, en orden:
  1. Si la `answer_key` tiene ANCLAS —un código como P101/F048/d012, o un valor con
     unidad como "216 mm" / "2 A" / "0.50 m/s"— alcanza con que una ancla aparezca.
  2. Si no tiene anclas pero es prosa, se pide solapamiento de palabras de contenido.
  3. Si es demasiado corta o ambigua para anclar nada ("0", "2", "50" sin unidad), se
     declara NO VERIFICABLE y se excluye de la métrica. Es preferible informar sobre
     cuántas se pudo verificar que inflar el número matcheando "0" contra cualquier texto.
"""
import re
import unicodedata
from typing import List, Optional, Tuple

# Códigos de parámetro/falla del dominio: P101, F048, d012, t201, C307, A450, X1, x2
_CODE_RE = re.compile(r"\b[A-Za-z]{1,2}\d{1,3}(?:\(\d+\))?\b")

# Valor + unidad. Sin unidad un número suelto no ancla nada: "50" aparece en cualquier lado.
#
# Dos regexes, y no una, por dos razones que salieron de los tests:
#
#  - Las unidades de UNA letra son ambiguas si se ignoran mayúsculas: la "A" de amperes
#    matchea la preposición "a" del español, y "0 a 300°C" se leía como "0 amperes".
#    Esas van sensibles a mayúsculas (A, V, W son mayúsculas en notación técnica).
#  - Las alternativas van de más larga a más corta: con "m" antes de "m/s", el patrón
#    cortaba "0.50 m/s" en "0.50 m" y perdía la unidad real.
_MULTI_UNIT = r"m/s|VCA|VCC|VAC|VDC|kWh|kW|kHz|mA|HP|Hz|rpm|mm|cm|kg|°C|N-m|Nm|seg(?:undos)?|minutos"
_SINGLE_UNIT = r"A|V|W|%|m|g|h|s"

_UNIT_RE_MULTI = re.compile(rf"\b\d+(?:[.,]\d+)?\s*(?:{_MULTI_UNIT})", re.IGNORECASE)
_UNIT_RE_SINGLE = re.compile(rf"\b\d+(?:[.,]\d+)?\s*(?:{_SINGLE_UNIT})\b")   # sin IGNORECASE

_STOPWORDS = {
    "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del", "al", "a", "en",
    "y", "o", "que", "se", "por", "para", "con", "sin", "su", "sus", "lo", "es", "son",
    "como", "más", "pero", "si", "no", "ya", "le", "les", "esta", "este", "esto", "esa",
    "ese", "eso", "hay", "ser", "está", "están", "the", "of", "and", "to", "in", "for",
}


def _normalizar(texto: str) -> str:
    """
    Minúsculas, sin tildes, espacios colapsados y coma decimal unificada a punto.

    Lo de la coma no es cosmético: una clave escrita "0,82 m/s" no matcheaba nunca una
    tabla que escribe "0.82", y contaba como fallo de retrieval cuando el dato estaba.
    """
    texto = unicodedata.normalize("NFKD", str(texto or "").lower())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"(?<=\d),(?=\d)", ".", texto)      # 0,82 -> 0.82
    return " ".join(texto.split())


def anclas(answer_key: str) -> List[str]:
    """
    Códigos y valores-con-unidad de la clave, normalizados y sin espacios internos, para
    que "2 A" matchee un texto que diga "2A" y al revés.

    El código se guarda SIN el marcador de nota al pie: de "P106(1)" queda "p106", que
    matchea igual un texto que escriba "P106" o "P106(1)".
    """
    bruto = str(answer_key or "")
    con_unidad = (
        [m.group(0) for m in _UNIT_RE_MULTI.finditer(bruto)]
        + [m.group(0) for m in _UNIT_RE_SINGLE.finditer(bruto)]
    )
    encontradas = _CODE_RE.findall(bruto) + con_unidad

    # El NÚMERO solo, además del par valor+unidad, cuando es específico (tiene decimales
    # o 3+ dígitos). En una tabla la unidad va en el ENCABEZADO y la celda trae solo el
    # número: "| Bandeja 3 | 0.79 |" con "v [m/s]" arriba. Exigir valor+unidad adyacentes
    # daba por no encontrada una respuesta que estaba textualmente en el chunk — medido,
    # 3 de 5 "falsos positivos" del eval eran en realidad este bug.
    #
    # Se pide que el número sea específico para no anclar en un "2" o un "50", que
    # aparecen en cualquier texto.
    for valor in con_unidad:
        numero = re.match(r"\d+(?:[.,]\d+)?", valor.strip())
        if not numero:
            continue
        crudo = _normalizar(numero.group(0))
        if "." in crudo or len(crudo.replace(".", "")) >= 3:
            encontradas.append(crudo)

    return sorted({_normalizar(a).replace(" ", "") for a in encontradas if a.strip()})


def palabras_contenido(texto: str) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9áéíóúñ]{3,}", _normalizar(texto))
            if w not in _STOPWORDS]


def es_verificable(answer_key: str, min_palabras: int = 2) -> bool:
    """
    False cuando la clave no puede discriminar: sin anclas y con muy pocas palabras de
    contenido. Ej. "0", "2", "50", "x2" solo → cualquier texto la "contiene".
    """
    if anclas(answer_key):
        return True
    return len(palabras_contenido(answer_key)) >= min_palabras


def respuesta_presente(
    answer_key: str,
    textos: List[str],
    solape_minimo: float = 0.6,
) -> Tuple[Optional[bool], str]:
    """
    ¿Aparece la respuesta en alguno de los textos recuperados?

    Returns:
        (True/False, motivo) o (None, "no verificable") si la clave no permite decidir.
    """
    if not es_verificable(answer_key):
        return None, "clave no verificable"

    unidos = _normalizar(" \n ".join(textos or []))
    sin_espacios = unidos.replace(" ", "")

    encontradas = anclas(answer_key)
    if encontradas:
        presentes = [a for a in encontradas if a in sin_espacios]
        if presentes:
            return True, f"ancla presente: {', '.join(presentes[:3])}"
        return False, f"ninguna ancla presente ({', '.join(encontradas[:3])})"

    palabras = palabras_contenido(answer_key)
    presentes = [w for w in palabras if w in unidos]
    ratio = len(presentes) / len(palabras)
    if ratio >= solape_minimo:
        return True, f"solape {ratio:.0%} de palabras de contenido"
    return False, f"solape {ratio:.0%} (< {solape_minimo:.0%})"
