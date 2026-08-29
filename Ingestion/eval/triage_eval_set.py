"""
Pre-clasifica el eval set para que la revisión humana sea corta.

El problema: 54 preguntas generadas por un LLM, y revisarlas todas a ciegas es caro. Pero
no todas necesitan la misma atención. Este script marca las sospechosas por señales
objetivas y deja el resto como "probablemente bien", así se revisa lo que importa.

Señales que se chequean:
  AMBIGUA   otro chunk del corpus responde la pregunta igual o mejor que el marcado como
            correcto. Es el falso negativo más caro: el sistema acierta y el eval lo
            cuenta como fallo.
  SIN_ANCLA la `answer_key` no aparece en el texto de la fuente, así que o la pregunta no
            se responde ahí o el modelo se la inventó.
  FALLA     el retrieval no la encuentra. No es necesariamente un problema del eval, pero
            son las que hay que mirar primero.

OJO con lo que este script NO hace: no marca `reviewed`. Ese campo significa "un humano
verificó que la pregunta es justa", y dos de las tres señales de acá (FALLA, AMBIGUA) se
derivan del resultado del retrieval. Usarlas para marcar entradas como buenas y después
medir solo sobre esas es circular: se estaría filtrando por "las que acertamos" y el
recall sale 100% por construcción. Se probó y daba exactamente eso.

Lo que sí hace es escribir `triage_flags` en cada entrada y ordenar el informe, para que
la revisión humana empiece por donde más rinde.

Uso:
    python eval/triage_eval_set.py                 # informe
    python eval/triage_eval_set.py --anotar        # guarda triage_flags en el archivo
"""
import argparse
import json
import os
import re
import sys

API_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "API"))
sys.path.insert(0, API_DIR)
EVAL_DIR = os.path.dirname(os.path.abspath(__file__))


def normalizar(texto):
    return " ".join(str(texto or "").lower().split())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default=os.path.join(EVAL_DIR, "eval_set.jsonl"))
    ap.add_argument("--anotar", action="store_true",
                    help="guardar triage_flags en el archivo (no toca `reviewed`)")
    args = ap.parse_args()
    set_path = os.path.abspath(args.set)

    os.chdir(API_DIR)
    from configs.ReadConfig import ReadConfig
    from contexts.ChromaConnector import ChromaConnection
    from openai import OpenAI

    with open(set_path, encoding="utf-8") as f:
        entries = [json.loads(l) for l in f if l.strip()]

    data = ReadConfig().getConfigSettings()
    data["query_id"] = "triage"
    keys = data["openai_keys"]
    oai = OpenAI(api_key=keys if isinstance(keys, str) else keys[0])
    conn = ChromaConnection(data)
    conn.connect()
    top_k = data["chroma_top_n_contexts"]

    informe = []
    for entry in entries:
        if not entry.get("gold_pages"):
            continue                                  # las de fuera de tema ya están revisadas

        alertas = []
        pregunta = entry["question"]
        clave = normalizar(entry.get("answer_key"))
        fuente = normalizar(entry.get("source_excerpt"))

        # SIN_ANCLA: la respuesta no está en la fuente marcada
        if clave and len(clave) < 60:
            tokens = [t for t in re.split(r"[\s,;()]+", clave) if len(t) > 2]
            if tokens and not any(t in fuente for t in tokens):
                alertas.append("SIN_ANCLA")

        # AMBIGUA / FALLA: mirar qué recupera el retrieval
        d = dict(data)
        d["query"] = pregunta
        vec = oai.embeddings.create(model=data["openai_emb_model"], input=pregunta).data[0].embedding
        df = conn.search_vectors(d, vec, top_k=top_k)

        rank, otros_arriba = None, []
        if not df.empty:
            gold = {str(p) for p in entry["gold_pages"]}
            for pos, row in enumerate(df.to_dict("records"), 1):
                pagina = str(row[data["page_number_column"]])
                partes = {pagina, pagina.split("-")[0], pagina.split("-")[-1]}
                if str(row[data["filename_column"]]) == entry["gold_doc"] and (partes & gold):
                    rank = pos
                    break
                otros_arriba.append(f"p{pagina}")

        if rank is None:
            alertas.append("FALLA")
        elif rank > 5:
            # Hay 5+ chunks que el retrieval consideró mejores. Puede ser que alguno
            # responda igual de bien y el ground truth de una sola página sea injusto.
            #
            # Se probó marcar desde rank>3 y también una señal "GENERICA" por ausencia
            # de términos técnicos: entre las dos marcaban 47 de 54, o sea que no
            # servían para priorizar. La GENERICA además era un proxy malo — marcaba
            # "¿cuánto marca el anemómetro en la bandeja 7 al 50%?", que es específica.
            alertas.append(f"AMBIGUA(rank {rank}, arriba: {', '.join(otros_arriba[:3])})")

        informe.append((entry, alertas))

    sospechosas = [(e, a) for e, a in informe if a]
    limpias = [(e, a) for e, a in informe if not a]

    print(f"\n{'='*100}")
    print(f"TRIAGE: {len(informe)} preguntas en tema")
    print(f"  probablemente bien (sin alertas): {len(limpias)}")
    print(f"  a revisar:                        {len(sospechosas)}")
    print(f"{'='*100}\n")

    for entry, alertas in sorted(sospechosas, key=lambda x: x[1]):
        print(f"[{' | '.join(alertas)}]")
        print(f"  P: {entry['question']}")
        print(f"  clave: {entry.get('answer_key','')[:80]}")
        print(f"  gold:  p{entry['gold_pages'][0]} ({entry.get('gold_content_type')}) — {entry.get('source_excerpt','')[:110]}")
        print()

    if args.anotar:
        flags_por_pregunta = {e["question"]: a for e, a in informe}
        for entry in entries:
            if entry["question"] in flags_por_pregunta:
                entry["triage_flags"] = flags_por_pregunta[entry["question"]]
        with open(set_path, "w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(f"Anotadas {len(informe)} entradas con triage_flags.")
        print("`reviewed` sigue en false: eso lo pone un humano después de leer la pregunta")
        print("y su source_excerpt, y decidir si el ground truth es justo.")


if __name__ == "__main__":
    main()
