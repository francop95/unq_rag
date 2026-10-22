"""
Mete en el índice las descripciones de planos ya revisadas
==========================================================

`redescribir_planos.py` produce las descripciones nuevas y las deja en un JSON
para mirarlas. Esto es el paso que las aplica.

No alcanza con cambiar el texto
-------------------------------
Hay que rehacer tres cosas, y omitir cualquiera deja el índice incoherente:

1. El **vector del chunk**. El texto cambió, así que su embedding ya no lo
   representa. Hay que volver a embeber con el MISMO modelo que construyó el
   índice: si no coinciden, el vector cae en otro espacio y dejan de
   recuperarse, sin ningún error visible.

2. Las **preguntas sintéticas**. Son vectores propios que apuntan al chunk, y
   se generaron a partir de la descripción vieja: arrastran exactamente los
   errores que se vinieron a corregir. Una de las del plano principal pregunta
   por un interruptor de 30A que no existe. Se borran y se generan de nuevo
   desde la descripción corregida.

3. Las **entidades**. El etiquetado sale del texto del chunk, así que cambia
   con él. El plano codificado, por ejemplo, ahora nombra QF02, SSR01 o VFD01,
   que antes no aparecían y por lo tanto no estaban etiquetados.

El orden importa: primero se borra el chunk viejo con sus preguntas, después se
inserta el nuevo. Al revés quedarían las dos versiones compitiendo.

    python scripts/aplicar_planos_redescritos.py            # qué haría
    python scripts/aplicar_planos_redescritos.py --escribir
"""

import argparse
import json
import os
import sys
from collections import defaultdict

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "src"))
sys.path.insert(0, os.path.abspath(os.path.join(RAIZ, "..", "API")))

ENTRADA = os.path.join(RAIZ, "data", "planos_redescritos.json")

# Las mismas que usa la ingesta hoy (config: max_synthetic_questions).
N_PREGUNTAS = 4

SISTEMA_PREGUNTAS = """Generás preguntas que un técnico de mantenimiento haría
y que ESTA descripción de plano responde.

- Solo sobre lo que la descripción afirma. Si no menciona un valor, no preguntes
  por ese valor.
- Nada de preguntas cuya respuesta sea "no se sabe" o esté en "dudas".
- Concretas y en español rioplatense, como las escribiría alguien frente al
  tablero.

Devolvé {"preguntas": ["...", "..."]} con exactamente %d."""


def texto_del_chunk(n: dict) -> str:
    """Arma el texto en el mismo formato que el resto de las figuras del índice.

    Se respeta el formato existente a propósito: los chunks visuales del índice
    siguen todos este molde (`Tipo:`, `Componentes:`, `Conexiones:`), el prompt
    de respuesta lo da por sentado, y cambiarlo solo para estos 19 los volvería
    distintos de los otros 288 sin ninguna ventaja.
    """
    def rot(x):
        if isinstance(x, dict):
            r = (x.get("rotulo") or "").replace("\n", " ")
            q = x.get("que_es") or ""
            return f"{r} ({q})" if q else r
        return str(x)

    partes = [n.get("resumen", "").strip()]
    if n.get("tipo"):
        partes.append(f"Tipo: {n['tipo']}")
    disp = [rot(x) for x in (n.get("dispositivos") or []) if rot(x).strip()]
    if disp:
        partes.append("Componentes: " + ", ".join(disp))
    if n.get("conexiones"):
        partes.append("Conexiones: " + ", ".join(str(c) for c in n["conexiones"]))
    if n.get("textos_en_la_figura"):
        partes.append("Textos en la figura: "
                      + ", ".join(str(t) for t in n["textos_en_la_figura"]))
    if n.get("valores_nominales"):
        partes.append("Valores nominales: "
                      + json.dumps(n["valores_nominales"], ensure_ascii=False))
    # Las dudas van dentro del texto a propósito: que el modelo de respuesta
    # sepa qué NO se pudo leer es justamente lo que evita que lo complete solo.
    if n.get("dudas"):
        partes.append("No legible en el plano: " + "; ".join(str(d) for d in n["dudas"]))
    return "\n".join(p for p in partes if p)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--escribir", action="store_true")
    ap.add_argument("--modelo-preguntas", default="gpt-4.1")
    args = ap.parse_args()

    from configs.Configuration import Configuration as C
    from configs.entidades import etiquetas, componentes_del_documento
    from openai import OpenAI
    from task_utils.llm_json import LLMJsonClient, text_content_part
    from task_utils.embedding_provider import EmbeddingProvider
    import chromadb

    with open(ENTRADA, encoding="utf-8") as f:
        planos = [p for p in json.load(f) if p.get("descripcion_nueva")]
    print(f"Planos con descripción nueva: {len(planos)}\n")

    cliente = chromadb.PersistentClient(path=C.chroma_local_path)
    col = cliente.get_collection(C.AppSettings__ChromaIndex)

    # Metadatos y preguntas actuales de cada chunk afectado
    por_id, hijos = {}, defaultdict(list)
    objetivo = {p["id"] for p in planos}
    claves = {}
    total, off = col.count(), 0
    while off < total:
        b = col.get(limit=500, offset=off, include=["metadatas"])
        for cid, m in zip(b["ids"], b["metadatas"]):
            m = m or {}
            if cid in objetivo:
                por_id[cid] = m
                claves[(m.get("file_name"), m.get("page_num"), m.get("chunk_id"))] = cid
        off += len(b["ids"])
    off = 0
    while off < total:
        b = col.get(limit=500, offset=off, include=["metadatas"])
        for cid, m in zip(b["ids"], b["metadatas"]):
            m = m or {}
            if not m.get("question"):
                continue
            k = (m.get("file_name"), m.get("page_num"), m.get("parent_chunk_id"))
            if k in claves:
                hijos[claves[k]].append(cid)
        off += len(b["ids"])

    llm = LLMJsonClient(client=OpenAI(), model=args.modelo_preguntas,
                        temperature=0.0, max_output_tokens=800,
                        etapa="preguntas_planos")
    emb = EmbeddingProvider(provider="openai", model=C.EMBEDDING_MODEL
                            if hasattr(C, "EMBEDDING_MODEL") else "text-embedding-3-large",
                            openai_client=OpenAI())

    a_borrar, nuevos = [], []
    for p in planos:
        cid = p["id"]
        meta = por_id.get(cid)
        if meta is None:
            print(f"  ! {p['archivo']} pág.{p['pagina']}: ya no está en el índice, se saltea")
            continue
        texto = texto_del_chunk(p["descripcion_nueva"])
        preguntas_viejas = hijos.get(cid, [])

        r = llm.complete_json(SISTEMA_PREGUNTAS % N_PREGUNTAS,
                              [text_content_part(texto)], label="preguntas")
        preguntas = [q for q in ((r or {}).get("preguntas") or []) if isinstance(q, str)][:N_PREGUNTAS]

        print(f"  {p['archivo'][:40]:<42} pág.{p['pagina']:<4} "
              f"texto {len(p['descripcion_vieja'])}→{len(texto)} car  "
              f"preguntas {len(preguntas_viejas)}→{len(preguntas)}")

        a_borrar.append(cid)
        a_borrar.extend(preguntas_viejas)

        base = dict(meta)
        base.pop("question", None)
        ents = etiquetas(texto) or []
        del_doc = [c for c in componentes_del_documento(meta.get("file_name") or "")
                   if c not in ents]
        for d in (base, ):
            d["entidades"] = "|" + "|".join(ents) + "|" if ents else ""
            d["entidades_doc"] = "|" + "|".join(del_doc) + "|" if del_doc else ""
            d["n_entidades"] = len(ents)
        for c in ents:
            base["ent_" + "".join(ch for ch in c if ch.isalnum())] = True

        nuevos.append({"id": cid, "texto": texto, "meta": base})
        for i, q in enumerate(preguntas, 1):
            mq = dict(base)
            mq["question"] = q
            mq["parent_chunk_id"] = meta.get("chunk_id")
            mq["chunk_id"] = f"{meta.get('chunk_id')}_q{i}"
            mq.pop("media_path", None)   # la imagen vive en el chunk padre
            nuevos.append({"id": f"{cid}_q{i}", "texto": q, "meta": mq})

    print(f"\n  vectores a borrar  : {len(a_borrar)}")
    print(f"  vectores a insertar: {len(nuevos)}")
    if not args.escribir:
        print("\nNada escrito. Volvé a correr con --escribir para aplicar.")
        return 0

    textos = [n["texto"] for n in nuevos]
    print(f"\nEmbebiendo {len(textos)} textos con {emb.model}...")
    vectores = []
    for i in range(0, len(textos), 64):
        vectores.extend(emb.embed(textos[i:i + 64], input_type="search_document"))
    if len(vectores) != len(nuevos):
        print("Los embeddings no coinciden con los textos; no se escribe nada.")
        return 1

    col.delete(ids=a_borrar)
    col.add(ids=[n["id"] for n in nuevos], documents=textos,
            metadatas=[n["meta"] for n in nuevos], embeddings=vectores)
    print(f"Listo. La colección queda en {col.count()} vectores.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
