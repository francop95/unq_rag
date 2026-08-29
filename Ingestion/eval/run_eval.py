"""
Corre el eval set contra el pipeline REAL de retrieval de la API.

Importa `ChromaConnection` de API/ en vez de reimplementar la búsqueda: un benchmark que
reimplementa el pipeline mide algo que no es producción, que es justo el error que nos
hizo recomendar mal el reranker antes de rehacer la medición.

Métricas:
  - recall@k de la página correcta (k = 1, 3, 5, 10). recall@10 es la que decide si la
    respuesta llega al LLM, porque CHROMA_TOP_N corta ahí.
  - MRR sobre la primera aparición de la página correcta.
  - cobertura de media: chunks con imagen/tabla en el top-10.
  - gate de relevancia: en las consultas fuera de tema, ¿devuelve contexto vacío?

Uso:
    python eval/run_eval.py [--set eval/eval_set.jsonl] [--only-reviewed]
                            [--variant nombre=valor,...]

`--variant` permite comparar configuraciones sin editar Configuration.py, p. ej.:
    --variant use_visual_retrieval=false
    --variant min_context_similarity_score=0.35
"""
import argparse
import json
import os
import statistics
import sys

API_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "API"))
sys.path.insert(0, API_DIR)

EVAL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, EVAL_DIR)
from answer_check import respuesta_presente


def parse_variant(text: str) -> dict:
    """'a=false,b=0.35' -> {'a': False, 'b': 0.35} (bool/int/float/str)."""
    out = {}
    for pair in (text or "").split(","):
        if not pair.strip():
            continue
        key, _, raw = pair.partition("=")
        key, raw = key.strip(), raw.strip()
        if raw.lower() in ("true", "false"):
            value = raw.lower() == "true"
        else:
            try:
                value = int(raw)
            except ValueError:
                try:
                    value = float(raw)
                except ValueError:
                    value = raw
        out[key] = value
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default=os.path.join(EVAL_DIR, "eval_set.jsonl"))
    ap.add_argument("--only-reviewed", action="store_true",
                    help="usar solo las entradas con reviewed=true")
    ap.add_argument("--variant", default="", help="overrides de config: clave=valor,...")
    ap.add_argument("--verbose", action="store_true", help="mostrar cada consulta")
    args = ap.parse_args()

    # El path del set se resuelve ANTES del chdir: si no, un `--set eval/x.jsonl`
    # relativo se busca dentro de API/ y falla.
    set_path = os.path.abspath(args.set)

    # cwd en API/ para que las rutas relativas de Configuration resuelvan igual que en producción
    os.chdir(API_DIR)
    from configs.ReadConfig import ReadConfig
    from contexts.ChromaConnector import ChromaConnection
    from openai import OpenAI

    with open(set_path, encoding="utf-8") as f:
        entries = [json.loads(line) for line in f if line.strip()]
    if args.only_reviewed:
        entries = [e for e in entries if e.get("reviewed")]

    on_topic = [e for e in entries if e.get("gold_pages")]
    off_topic = [e for e in entries if not e.get("gold_pages")]
    if not entries:
        print("El set está vacío (¿usaste --only-reviewed sin haber revisado nada?)")
        return

    data = ReadConfig().getConfigSettings()
    overrides = parse_variant(args.variant)
    data.update(overrides)
    data["query_id"] = "eval"
    top_k = data["chroma_top_n_contexts"]

    keys = data["openai_keys"]
    oai = OpenAI(api_key=keys if isinstance(keys, str) else keys[0])
    conn = ChromaConnection(data)
    conn.connect()

    if overrides:
        print(f"variante: {overrides}")
    print(f"set: {len(on_topic)} en tema + {len(off_topic)} fuera de tema | top_k={top_k}\n")

    ranks, media_counts = [], []
    # Presencia de la respuesta en el texto recuperado: True / False / None (clave no
    # verificable). Es la métrica que NO depende del chunking ni de la paginación, así que
    # es la única con la que se pueden comparar dos ingestas distintas.
    respuestas = []
    por_categoria = {}
    for i, entry in enumerate(on_topic, 1):
        d = dict(data)
        d["query"] = entry["question"]
        vector = oai.embeddings.create(
            model=data["openai_emb_model"], input=entry["question"]
        ).data[0].embedding
        df = conn.search_vectors(d, vector, top_k=top_k)

        if df.empty:
            rank = None
            media = 0
            presente, _motivo = respuesta_presente(entry.get("answer_key"), [])
        else:
            pages = [str(p) for p in df[data["page_number_column"]]]
            docs = [str(x) for x in df[data["filename_column"]]]
            gold_pages = {str(p) for p in entry["gold_pages"]}
            rank = None
            for pos, (doc, page) in enumerate(zip(docs, pages), start=1):
                # La página del super-chunk puede venir como rango "82-84"
                page_parts = {page, page.split("-")[0], page.split("-")[-1]}
                if doc == entry["gold_doc"] and page_parts & gold_pages:
                    rank = pos
                    break
            media = int(sum(1 for _, r in df.iterrows()
                            if r.get("media_path") or r.get("image_path")))
            # ¿El texto que va a ver el LLM contiene la respuesta?
            presente, _motivo = respuesta_presente(
                entry.get("answer_key"),
                [str(x) for x in df[data["text_column"]]],
            )

        ranks.append(rank)
        respuestas.append(presente)
        media_counts.append(media)
        por_categoria.setdefault(entry.get("category", "otro"), []).append(rank)

        if args.verbose:
            estado = f"rank {rank}" if rank else "NO ESTÁ"
            resp = {True: "resp ✓", False: "resp ✗", None: "resp ?"}[presente]
            print(f"  [{i}/{len(on_topic)}] {estado:<9} {resp}  {entry['question'][:62]}")

    # Gate de relevancia sobre las consultas fuera de tema
    gate_ok = 0
    for entry in off_topic:
        d = dict(data)
        d["query"] = entry["question"]
        vector = oai.embeddings.create(
            model=data["openai_emb_model"], input=entry["question"]
        ).data[0].embedding
        df = conn.search_vectors(d, vector, top_k=top_k)
        if df.empty:
            gate_ok += 1
        elif args.verbose:
            print(f"  [gate] PASÓ contexto para: {entry['question'][:60]}")

    n = len(on_topic)
    print(f"\n{'='*62}\nRESULTADOS ({n} consultas en tema)\n{'='*62}")
    for k in (1, 3, 5, 10):
        if k > top_k:
            continue
        hits = sum(1 for r in ranks if r and r <= k)
        print(f"  recall@{k:<3} {hits}/{n}  = {hits/n*100:5.1f}%")
    mrr = sum(1.0 / r for r in ranks if r) / n
    print(f"  MRR      {mrr:.3f}")
    print(f"  no llega nunca:            {sum(1 for r in ranks if r is None)}/{n}")
    print(f"  chunks con media (mediana): {statistics.median(media_counts):.0f} de {top_k}")

    # La métrica que importa: el LLM recibió la respuesta, no solo la página correcta
    verificables = [r for r in respuestas if r is not None]
    if verificables:
        con_resp = sum(1 for r in verificables if r)
        print(f"\n  RESPUESTA presente en el texto recuperado: "
              f"{con_resp}/{len(verificables)} = {con_resp/len(verificables)*100:5.1f}%"
              f"   ({len(respuestas)-len(verificables)} claves no verificables, excluidas)")
        # Cuántas aciertan la página pero NO traen la respuesta: el falso positivo que
        # el recall por página no ve.
        falsos = sum(1 for r, p in zip(ranks, respuestas) if r and p is False)
        print(f"  de las que llegan al top-10, SIN la respuesta: {falsos}")

    print(f"\n  gate fuera de tema: {gate_ok}/{len(off_topic)} rechazadas correctamente")

    print(f"\n  por categoría:")
    for cat, rs in sorted(por_categoria.items()):
        hits = sum(1 for r in rs if r and r <= 10)
        print(f"    {cat:<16} recall@10 {hits}/{len(rs)}")


if __name__ == "__main__":
    main()
