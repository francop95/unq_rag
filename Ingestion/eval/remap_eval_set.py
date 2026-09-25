"""
Reapunta el set de evaluación a un corpus nuevo
===============================================

Cada pregunta del set declara dónde está su respuesta: `gold_doc` y
`gold_pages`. `run_eval.py` puntúa comparando eso contra lo que devuelve el
retrieval, así que esos dos campos son la vara de medir.

Cuando el corpus se reorganiza, la vara deja de servir sin aviso. Tres
manuales fueron reemplazados por versiones nuevas con otro nombre y otra
cantidad de páginas, y las 83 preguntas siguen apuntando a los archivos
viejos. Contra el índice nuevo eso da un recall cercano a cero, que se ve
exactamente igual que "el reindexado rompió el retrieval" — el peor modo de
falla posible, porque manda a depurar un problema que no existe.

Cómo reapunta
-------------
Cada pregunta trae `source_excerpt`: el texto del que salió la respuesta. Ese
texto sigue estando en el corpus nuevo, aunque haya cambiado de archivo, de
página y de chunk. El script lo busca en los chunks recién generados y propone
el `(documento, páginas, chunk)` donde apareció.

La medida es CONTENCIÓN de n-gramas de caracteres, no similitud: el excerpt
salió de un chunk de la corrida vieja y el chunking nuevo puede haberlo
partido distinto, así que lo que importa es qué fracción del excerpt aparece
en el chunk candidato, no cuánto se parecen los dos textos en conjunto.

No escribe nada por su cuenta
----------------------------
Por defecto propone y muestra. Reescribir la vara de medir sin mirar es la
forma de convertir un error de medición en un error permanente, así que
`--escribir` es explícito y siempre deja el archivo anterior con sufijo .bak.
"""

import argparse
import json
import os
import re
import sys
from collections import Counter
from typing import Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from task_utils.chunk_text import readable_chunk_text  # noqa: E402

RAIZ = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

# Tamaño del n-grama de caracteres. 5 es suficientemente corto para sobrevivir
# a diferencias de espaciado y puntuación entre una corrida y otra, y
# suficientemente largo para que la coincidencia no sea casual.
N = 5

# Por debajo de esto la propuesta no se acepta sola: el excerpt puede haber
# quedado en un documento que ya no está, o el chunking nuevo puede haberlo
# partido en dos y ninguna mitad contiene lo suficiente.
UMBRAL_ACEPTACION = 0.60

# Para la segunda estrategia (tokens de la clave). Más bajo porque su puntaje ya
# viene escalado a la mitad, y de todos modos sus propuestas se marcan aparte
# para que se revisen antes de aplicarlas.
UMBRAL_CLAVE = 0.40


def normalizar(texto: str) -> str:
    texto = (texto or "").lower()
    texto = re.sub(r"[^\w\s.,;:%°/|-]", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def ngramas(texto: str) -> set:
    t = normalizar(texto)
    return {t[i:i + N] for i in range(len(t) - N + 1)} if len(t) >= N else set()


def contencion(excerpt: set, chunk: set) -> float:
    """Fracción de los n-gramas del excerpt presentes en el chunk."""
    return len(excerpt & chunk) / len(excerpt) if excerpt else 0.0


def ultima_corrida(doc_dir: str) -> Optional[str]:
    """La subcarpeta con timestamp más reciente de un documento."""
    if not os.path.isdir(doc_dir):
        return None
    subs = sorted(d for d in os.listdir(doc_dir)
                  if os.path.isdir(os.path.join(doc_dir, d)))
    return os.path.join(doc_dir, subs[-1]) if subs else None


def corpus_vigente(raw_root: str) -> set:
    """
    Los file_stem que hoy se ingestan, para no reapuntar a un documento muerto.

    data/chunks_data/ conserva las corridas de TODOS los corpus anteriores, así
    que los manuales viejos siguen ahí con sus chunks intactos. Sin este filtro
    el excerpt coincide perfecto con el documento del que salió —que es
    justamente el que se reemplazó— y el set queda reapuntado a archivos que no
    están en el índice. El resultado se vería sano y no mediría nada, que es
    peor que no haber reapuntado.
    """
    from main_multimodal import iter_documentos  # misma regla de descubrimiento

    return {os.path.splitext(os.path.basename(p))[0] for p in iter_documentos(raw_root)}


def cargar_chunks(chunks_root: str, vigentes: Optional[set] = None) -> List[Dict]:
    """
    Todos los chunks de la última corrida de cada documento del corpus vigente.

    Se lee de data/chunks_data/ y no del índice porque acá interesa el texto
    crudo del chunk, sin el resumen contextual que el enricher le antepone: ese
    prefijo es texto generado que no estaba en el documento y ensuciaría la
    coincidencia.
    """
    salida = []
    if not os.path.isdir(chunks_root):
        return salida
    for doc in sorted(os.listdir(chunks_root)):
        if vigentes is not None and doc not in vigentes:
            continue
        corrida = ultima_corrida(os.path.join(chunks_root, doc))
        if not corrida:
            continue
        for carpeta in sorted(os.listdir(corrida)):
            ruta = os.path.join(corrida, carpeta)
            if not os.path.isdir(ruta):
                continue
            for archivo in sorted(os.listdir(ruta)):
                if not archivo.endswith(".json"):
                    continue
                try:
                    with open(os.path.join(ruta, archivo), "r", encoding="utf-8") as fh:
                        c = json.load(fh)
                except Exception:
                    continue
                if not isinstance(c, dict):
                    continue
                texto = readable_chunk_text(c)
                if not texto:
                    continue
                salida.append({
                    "file_name": c.get("file_name", ""),
                    "page_num": str(c.get("page_num", "")),
                    "chunk_id": str(c.get("chunk_id", "")),
                    "content_type": c.get("content_type", ""),
                    "ngramas": ngramas(texto),
                    "texto": texto,
                })
    return salida


# Tokens de una clave de respuesta que sobreviven a una traducción o a que el
# texto lo haya reescrito otro modelo: números con unidad ("9 A", "-40…+70 °C")
# y códigos en mayúsculas ("VAUX2", "QD01"). Las palabras sueltas no sirven.
TOKEN_CLAVE = re.compile(r"[0-9]+(?:[.,][0-9]+)?\s*[A-Za-zÁ-ú%°]*|[A-Z]{2,}[0-9]*")


def tokens_de_clave(answer_key: str) -> List[str]:
    return [t.strip() for t in TOKEN_CLAVE.findall(answer_key or "") if len(t.strip()) > 1]


def coincidencia_por_clave(answer_key: str, chunks: List[Dict]) -> Tuple[Optional[Dict], float]:
    """
    Segunda estrategia, para cuando el excerpt ya no existe como texto.

    Pasa en dos casos reales de este corpus. Uno: el catálogo del TBEN fue
    reemplazado por su traducción al español, y una coincidencia por n-gramas
    de caracteres no cruza un cambio de idioma. Dos: varios excerpts no son
    texto del PDF sino descripciones que generó el modelo de visión, y esas se
    reescriben distinto en cada corrida aunque la figura sea la misma.

    Los números con unidad y los códigos en mayúsculas sobreviven a las dos
    cosas. La contrapartida es que una clave como "2" u "8" no identifica nada,
    así que se exige al menos dos tokens y se devuelve una confianza baja a
    propósito: estas propuestas son para revisar, no para aplicar a ciegas.
    """
    tokens = tokens_de_clave(answer_key)
    if len(tokens) < 2:
        return None, 0.0

    mejor, puntaje = None, 0.0
    for c in chunks:
        texto = c.get("texto") or ""
        if not texto:
            continue
        hallados = sum(1 for t in tokens if re.search(re.escape(t), texto, re.I))
        p = hallados / len(tokens)
        if p > puntaje:
            mejor, puntaje = c, p
    # Se escala para que nunca compita con una coincidencia por excerpt, que es
    # evidencia mucho más fuerte.
    return (mejor, puntaje * 0.5) if mejor else (None, 0.0)


def mejor_coincidencia(excerpt: str, chunks: List[Dict]) -> Tuple[Optional[Dict], float]:
    e = ngramas(excerpt)
    if not e:
        return None, 0.0
    mejor, puntaje = None, 0.0
    for c in chunks:
        p = contencion(e, c["ngramas"])
        if p > puntaje:
            mejor, puntaje = c, p
    return mejor, puntaje


# Contención mínima para aceptar que una página CONTIGUA también contiene parte
# del excerpt. Es baja a propósito: si un salto de página partió el excerpt, cada
# mitad contiene solo una fracción.
CONTENCION_VECINA = 0.20


def _pagina_int(page_num: str) -> Optional[int]:
    """Número de página como entero. Un super-chunk viene como rango '8-11'."""
    try:
        return int(str(page_num).split("-")[0].strip())
    except (TypeError, ValueError):
        return None


def paginas_vecinas(excerpt: str, chunks: List[Dict], doc: str,
                    pagina_mejor: str) -> List[str]:
    """
    La página del mejor chunk, más las CONTIGUAS que también contengan parte
    del excerpt.

    Un excerpt puede cruzar un salto de página: `gold_pages` en el set viejo ya
    era una lista por esa razón. Quedarse solo con la página del mejor chunk
    daría por incorrecta una recuperación que cayó en la otra mitad.

    La contigüidad no es un detalle: aceptar cualquier página por encima de un
    umbral laxo devolvía cosas como ['72', '73', '83'] y ['22', '32', '58'],
    donde las páginas lejanas apenas comparten frases de relleno del mismo
    manual. Con eso `run_eval.py` daría por acertada una recuperación que cayó
    cincuenta páginas lejos del contenido, y el recall medido sería más alto
    que el real — el error más caro que puede tener una vara de medir.
    """
    e = ngramas(excerpt)
    base = _pagina_int(pagina_mejor)
    paginas = {str(pagina_mejor)}
    if base is not None:
        for c in chunks:
            if c["file_name"] != doc:
                continue
            n = _pagina_int(c["page_num"])
            if n is None or abs(n - base) != 1:
                continue
            if contencion(e, c["ngramas"]) >= CONTENCION_VECINA:
                paginas.add(str(c["page_num"]))
    return sorted(paginas, key=lambda p: (_pagina_int(p) if _pagina_int(p) is not None else 0, p))


def procesar(ruta_set: str, chunks: List[Dict], umbral: float) -> Tuple[List[Dict], Dict[str, int]]:
    entradas = [json.loads(l) for l in open(ruta_set, encoding="utf-8") if l.strip()]
    resultados, cuenta = [], Counter()

    for entrada in entradas:
        salida = dict(entrada)
        excerpt = entrada.get("source_excerpt") or ""
        doc_viejo = entrada.get("gold_doc")

        if not doc_viejo:
            # Preguntas fuera de tema: no tienen documento por diseño, miden que
            # el sistema conteste que no sabe. No hay nada que reapuntar.
            cuenta["fuera de tema"] += 1
            resultados.append({"entrada": salida, "estado": "fuera de tema",
                               "puntaje": None, "propuesta": None})
            continue

        if not excerpt:
            cuenta["sin excerpt"] += 1
            resultados.append({"entrada": salida, "estado": "sin excerpt",
                               "puntaje": None, "propuesta": None})
            continue

        mejor, puntaje = mejor_coincidencia(excerpt, chunks)
        via = "excerpt"
        if mejor is None or puntaje < umbral:
            alt, p_alt = coincidencia_por_clave(entrada.get("answer_key", ""), chunks)
            if alt is not None and p_alt >= UMBRAL_CLAVE:
                mejor, puntaje, via = alt, p_alt, "clave"
            else:
                cuenta["sin coincidencia"] += 1
                resultados.append({"entrada": salida, "estado": "sin coincidencia",
                                   "puntaje": max(puntaje, p_alt), "propuesta": mejor})
                continue

        propuesta = {
            "gold_doc": mejor["file_name"],
            "gold_pages": paginas_vecinas(excerpt, chunks, mejor["file_name"], mejor["page_num"]),
            "gold_chunk_id": mejor["chunk_id"],
            "gold_content_type": mejor["content_type"],
        }
        estado = "sin cambio" if (propuesta["gold_doc"] == doc_viejo and
                                  set(propuesta["gold_pages"]) == set(map(str, entrada.get("gold_pages") or []))) \
            else "reapuntada"
        if via == "clave" and estado == "reapuntada":
            estado = "reapuntada (por clave, revisar)"
        cuenta[estado] += 1
        resultados.append({"entrada": salida, "estado": estado, "puntaje": puntaje,
                           "propuesta": propuesta, "mejor_pagina": mejor["page_num"],
                           "via": via})

    return resultados, cuenta


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--set", default="eval_set.jsonl", help="archivo .jsonl a reapuntar")
    p.add_argument("--chunks", default=os.path.join(RAIZ, "data", "chunks_data"),
                   help="carpeta de chunks de la corrida nueva")
    p.add_argument("--umbral", type=float, default=UMBRAL_ACEPTACION)
    p.add_argument("--escribir", action="store_true",
                   help="aplica las propuestas (deja el original como .bak)")
    p.add_argument("--verbose", action="store_true", help="muestra cada pregunta")
    args = p.parse_args()

    ruta_set = args.set if os.path.isabs(args.set) else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), args.set)

    vigentes = corpus_vigente(os.path.join(RAIZ, "data", "raw_data"))
    print(f"Corpus vigente: {len(vigentes)} documentos")

    print(f"Leyendo chunks de {args.chunks} ...")
    chunks = cargar_chunks(args.chunks, vigentes)
    docs = sorted({c["file_name"] for c in chunks})
    faltan = vigentes - {os.path.splitext(d)[0] for d in docs}
    if faltan:
        print(f"  ⚠️  {len(faltan)} documentos del corpus todavía sin chunks "
              f"(¿la ingesta sigue corriendo?): {', '.join(sorted(faltan)[:4])}"
              + (" ..." if len(faltan) > 4 else ""))
    print(f"  {len(chunks)} chunks de {len(docs)} documentos:")
    for d in docs:
        print(f"    {sum(1 for c in chunks if c['file_name'] == d):>5}  {d}")

    print(f"\nReapuntando {os.path.basename(ruta_set)} (umbral {args.umbral}) ...\n")
    resultados, cuenta = procesar(ruta_set, chunks, args.umbral)

    for r in resultados:
        if r["estado"] in ("sin cambio",) and not args.verbose:
            continue
        e, prop = r["entrada"], r["propuesta"]
        marca = {"reapuntada": "→", "reapuntada (por clave, revisar)": "≈",
                 "sin cambio": "=", "sin coincidencia": "!",
                 "sin excerpt": "?", "fuera de tema": "·"}[r["estado"]]
        puntaje = f"{r['puntaje']:.2f}" if r["puntaje"] is not None else "   -"
        print(f"  {marca} [{puntaje}] {e['question'][:66]}")
        if r["estado"].startswith("reapuntada"):
            print(f"      {e['gold_doc']} p{e.get('gold_pages')}")
            print(f"   →  {prop['gold_doc']} p{prop['gold_pages']} "
                  f"(mejor: p{r['mejor_pagina']} {prop['gold_chunk_id']})")
        elif r["estado"] == "sin coincidencia" and r["propuesta"]:
            print(f"      mejor candidato: {r['propuesta']['file_name']} "
                  f"p{r['propuesta']['page_num']} — por debajo del umbral")

    print("\n" + "=" * 70)
    for k, v in cuenta.most_common():
        print(f"  {v:>3}  {k}")
    print("=" * 70)

    if not args.escribir:
        print("\nNada escrito. Revisar y volver a correr con --escribir para aplicar.")
        return 0

    pendientes = [r for r in resultados if r["estado"] == "sin coincidencia"]
    if pendientes:
        print(f"\n⚠️  {len(pendientes)} preguntas sin coincidencia: se dejan como estaban.")

    respaldo = ruta_set + ".bak"
    os.replace(ruta_set, respaldo)
    with open(ruta_set, "w", encoding="utf-8") as fh:
        for r in resultados:
            e = dict(r["entrada"])
            if r["estado"].startswith("reapuntada"):
                e.update(r["propuesta"])
                e["remap_via"] = r.get("via", "excerpt")
                e["remapped_from"] = {"gold_doc": r["entrada"]["gold_doc"],
                                      "gold_pages": r["entrada"].get("gold_pages"),
                                      "containment": round(r["puntaje"], 3)}
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    print(f"\n✅ Escrito {ruta_set}\n   original en {respaldo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
