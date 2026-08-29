"""
Eval de consultas VISUALES, para juzgar el retrieval por CLIP.

El eval principal son consultas de texto, y con esas CLIP no aporta nada — pero eso no
lo condena: CLIP existe para buscar por semejanza visual ("el diagrama que tiene un
contactor y tres fusibles"), y ese tipo de consulta no estaba representado.

Cómo se generan: cada imagen indexada tiene una descripción escrita por el modelo de
visión. Se le pide al LLM que redacte una consulta que describa la FIGURA por su
apariencia —qué elementos se ven, cómo están dispuestos— sin nombrar el documento ni la
página, y sin usar el vocabulario técnico que ya está en el texto del manual. Así la
consulta se parece a lo que alguien escribiría buscando "ese dibujo que vi", que es el
caso de uso de CLIP.

Uso:
    python eval/generate_visual_eval.py [--out eval/eval_visual.jsonl] [--max 25]
"""
import argparse
import json
import os
import random

import chromadb
from dotenv import dotenv_values
from openai import OpenAI

MODEL = "gpt-4.1"

PROMPT = """Abajo está la descripción de una figura de un manual técnico, escrita por un
modelo de visión.

Escribí una consulta de búsqueda con la que alguien encontraría ESA figura describiendo
cómo se VE, no cómo se llama. Como cuando uno busca "ese dibujo que tenía dos motores y
un tablero en el medio".

Reglas:
- Describí elementos visibles y su disposición: cuántos hay, dónde están, qué forma
  tienen, si hay recuadros, flechas, tablas, fotos, cortes.
- NO menciones el documento, la página, ni el número de figura.
- NO uses el nombre técnico exacto del componente si podés describirlo ("una caja con
  bornes numerados" mejor que "bloque de terminales de E/S").
- Si la descripción es demasiado genérica para identificar una figura entre cientos
  (ej. "un diagrama eléctrico"), devolvé {{"skip": true}}.

Devolvé JSON estricto: {{"query": "..."}}  o  {{"skip": true}}

DESCRIPCIÓN DE LA FIGURA:
{description}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="eval/eval_visual.jsonl")
    ap.add_argument("--max", type=int, default=25)
    ap.add_argument("--chroma", default="data/chroma_index")
    args = ap.parse_args()

    cfg = dotenv_values(".env")
    client = OpenAI(api_key=cfg.get("openai_key"))

    col = chromadb.PersistentClient(path=args.chroma).get_collection("multimodal_documents")
    got = col.get(include=["metadatas", "documents"], limit=200000)

    figuras = [
        (m, d) for m, d in zip(got["metadatas"], got["documents"])
        if (m or {}).get("content_type") in ("diagram_visual", "image")
        and (m or {}).get("media_path")
        and len((d or "").strip()) > 80
    ]
    random.Random(5).shuffle(figuras)
    print(f"figuras con imagen y descripción: {len(figuras)}")

    entries, skipped = [], 0
    for meta, doc in figuras:
        if len(entries) >= args.max:
            break
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": PROMPT.format(description=doc[:1800])}],
                response_format={"type": "json_object"},
            )
            payload = json.loads(resp.choices[0].message.content)
        except Exception as e:
            print(f"  error: {e}")
            continue

        if payload.get("skip") or not payload.get("query"):
            skipped += 1
            continue

        entries.append({
            "question": payload["query"].strip(),
            "category": "visual",
            "gold_doc": meta.get("file_name"),
            "gold_pages": [str(meta.get("page_num"))],
            "gold_chunk_id": meta.get("chunk_id"),
            "gold_content_type": meta.get("content_type"),
            "gold_media_path": meta.get("media_path"),
            "answer_key": "la figura correcta debe aparecer entre las fuentes",
            "source_excerpt": " ".join((doc or "").split())[:300],
            "reviewed": False,
        })

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    print(f"\n{len(entries)} consultas visuales en {args.out} ({skipped} descartadas por genéricas)")
    print("\nComparar CLIP on/off:")
    print("  python eval/run_eval.py --set eval/eval_visual.jsonl \\")
    print("      --variant use_visual_retrieval=true,fusion_admits_sparse=true")


if __name__ == "__main__":
    main()
