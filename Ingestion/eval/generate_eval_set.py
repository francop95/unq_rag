"""
Genera un set de evaluación CANDIDATO a partir del contenido ya indexado.

Por qué no se reutilizan las preguntas sintéticas del índice: están indexadas como
vectores, así que consultar con una de ellas la recupera con similitud ~1.0 y el recall
sale artificialmente perfecto (ya nos pasó midiendo el reranker). Acá se generan
preguntas NUEVAS, en el lenguaje de un técnico y sin copiar la redacción del manual,
ancladas a un chunk concreto que hace de ground truth.

El resultado es un CANDIDATO para revisión humana, no un eval final: el modelo puede
escribir una pregunta que otro chunk responda igual de bien, o inventar un detalle. Cada
entrada trae el texto de la fuente al lado para que revisarla sea rápido.

Uso:
    python eval/generate_eval_set.py [--per-doc 12] [--out eval/eval_set.jsonl]
"""
import argparse
import json
import os
import random
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import chromadb
from dotenv import dotenv_values
from openai import OpenAI

MODEL = "gpt-4.1"          # el generador vale la pena que sea el bueno: escribe el ground truth
MAX_SOURCE_CHARS = 2600

SYSTEM = """Sos un técnico de mantenimiento industrial con 20 años de experiencia en
secaderos de pastas, variadores de frecuencia y tableros eléctricos. Escribís las
preguntas como se las harías a un colega, no como las escribiría un manual."""

PROMPT = """Abajo hay un fragmento de documentación técnica. Escribí UNA pregunta que un
técnico de mantenimiento haría de verdad y cuya respuesta esté ESPECÍFICAMENTE en este
fragmento.

Reglas:
1. La pregunta tiene que anclarse en un dato concreto del fragmento (un número de
   parámetro, un valor, un borne, un paso de procedimiento, un síntoma). Si el fragmento
   es genérico o es un encabezado/índice/portada sin contenido propio, devolvé
   {{"skip": true}} y nada más.
2. NO copies la redacción del fragmento. Usá las palabras que usaría alguien en el
   taller, en español rioplatense. Puede ser coloquial ("no arranca", "se me dispara").
3. NO menciones el número de página ni "el manual" ni "el documento".
4. La pregunta tiene que poder responderse sin ver el fragmento delante, o sea, tiene que
   ser autocontenida (mal: "¿qué dice la tabla?"; bien: "¿qué parámetro define de dónde
   toma la orden de arranque el variador?").
5. Devolvé también `answer_key`: el dato concreto que responde la pregunta, en pocas
   palabras, para poder verificar después si la respuesta del sistema es correcta.

Formato JSON estricto:
{{"question": "...", "answer_key": "...", "category": "falla|parametro|procedimiento|plano|proceso|especificacion"}}
o bien:
{{"skip": true}}

FRAGMENTO ({content_type}, {file_name}, página {page_num}):
{source}
"""

# Consultas fuera de tema: verifican el gate de relevancia, no el recall. No tienen
# página correcta — la respuesta correcta es "no tengo información de esto".
OFF_TOPIC = [
    "¿Cuál es la receta de la pizza napolitana?",
    "¿Cómo configuro un router wifi en casa?",
    "¿Quién ganó el mundial de 1986?",
    "¿Cuánto cuesta un pasaje a Bariloche?",
    "¿Cómo se hace un asado a la parrilla?",
]


def _expandir_paginas(page_num) -> list:
    """'55-56' -> ['55','56'];  '70-70' -> ['70'];  '46' -> ['46']"""
    texto = str(page_num).strip()
    if "-" not in texto:
        return [texto]
    partes = texto.split("-")
    try:
        a, b = int(partes[0]), int(partes[-1])
    except ValueError:
        return [texto]
    return [str(n) for n in range(min(a, b), max(a, b) + 1)]


def load_content_chunks(chroma_path: str, collection: str):
    col = chromadb.PersistentClient(path=chroma_path).get_collection(collection)
    got = col.get(include=["metadatas", "documents"], limit=200000)
    chunks = []
    for meta, doc in zip(got["metadatas"], got["documents"]):
        if (meta or {}).get("content_type") == "synthetic_question":
            continue
        text = " ".join((doc or "").split())
        if len(text) < 120:          # muy corto para anclar una pregunta
            continue
        chunks.append({
            "file_name": meta.get("file_name"),
            "page_num": str(meta.get("page_num")),
            "chunk_id": meta.get("chunk_id"),
            "content_type": meta.get("content_type"),
            "text": text,
        })
    return chunks


def stratified_sample(chunks, per_doc: int, seed: int = 11):
    """Muestrea por documento y por tipo de contenido, para no evaluar solo texto."""
    rng = random.Random(seed)
    by_doc_type = defaultdict(list)
    for c in chunks:
        by_doc_type[(c["file_name"], c["content_type"])].append(c)

    by_doc = defaultdict(list)
    for (doc, _ctype), items in by_doc_type.items():
        by_doc[doc].append(items)

    sample = []
    for doc, groups in by_doc.items():
        groups.sort(key=len, reverse=True)
        # Reparte el cupo del documento entre sus tipos de contenido
        per_group = max(1, per_doc // max(1, len(groups)))
        for items in groups:
            rng.shuffle(items)
            sample.extend(items[:per_group])
    rng.shuffle(sample)
    return sample


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-doc", type=int, default=12, help="preguntas objetivo por documento")
    ap.add_argument("--out", default="eval/eval_set.jsonl")
    ap.add_argument("--chroma", default="data/chroma_index")
    ap.add_argument("--collection", default="multimodal_documents")
    args = ap.parse_args()

    cfg = dotenv_values(".env")
    client = OpenAI(api_key=cfg.get("openai_key"))

    chunks = load_content_chunks(args.chroma, args.collection)
    sample = stratified_sample(chunks, args.per_doc)
    print(f"chunks de contenido: {len(chunks)} | muestreados: {len(sample)}")

    entries, skipped = [], 0
    for i, chunk in enumerate(sample, 1):
        prompt = PROMPT.format(
            content_type=chunk["content_type"],
            file_name=chunk["file_name"],
            page_num=chunk["page_num"],
            source=chunk["text"][:MAX_SOURCE_CHARS],
        )
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "system", "content": SYSTEM},
                          {"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
            )
            data = json.loads(resp.choices[0].message.content)
        except Exception as e:
            print(f"  [{i}/{len(sample)}] error: {e}")
            continue

        if data.get("skip") or not data.get("question"):
            skipped += 1
            continue

        entries.append({
            "question": data["question"].strip(),
            "answer_key": (data.get("answer_key") or "").strip(),
            "category": data.get("category", "otro"),
            "gold_doc": chunk["file_name"],
            # Un super-chunk trae page_num como rango ("55-56"). Se expande a páginas
            # reales del PDF: los límites de super-chunk cambian entre ingestas (se
            # agrupan por similitud sobre un chunking no determinista), así que un gold
            # con el rango literal no vuelve a matchear nunca y el eval deja de servir
            # para comparar dos ingestas.
            "gold_pages": _expandir_paginas(chunk["page_num"]),
            "gold_chunk_id": chunk["chunk_id"],
            "gold_content_type": chunk["content_type"],
            # Para que revisar sea rápido: la fuente al lado de la pregunta
            "source_excerpt": chunk["text"][:400],
            "reviewed": False,
        })
        if i % 10 == 0:
            print(f"  [{i}/{len(sample)}] {len(entries)} preguntas, {skipped} descartadas")

    for q in OFF_TOPIC:
        entries.append({
            "question": q,
            "answer_key": "sin información en el corpus",
            "category": "fuera_de_tema",
            "gold_doc": None,
            "gold_pages": [],
            "gold_chunk_id": None,
            "gold_content_type": None,
            "source_excerpt": "",
            "reviewed": True,
        })

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    print(f"\n{len(entries)} entradas escritas en {args.out}")
    print(f"  {skipped} fragmentos descartados por el modelo (genéricos/encabezados)")
    print(f"  {len(OFF_TOPIC)} consultas fuera de tema para el gate de relevancia")
    print("\nSiguiente paso: revisar el archivo (campo `reviewed`) y correr:")
    print("  python eval/run_eval.py")


if __name__ == "__main__":
    main()
