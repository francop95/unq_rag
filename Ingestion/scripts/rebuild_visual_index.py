"""
Reconstruye el índice visual con un modelo multimodal, en vez de CLIP
=====================================================================

Por qué
-------
El índice visual se construye hoy con CLIP (`clip-ViT-B-32`, 512 dimensiones).
Ese espacio no es comparable con el textual, así que las imágenes tienen que
vivir en una colección aparte que se consulta por separado y cuyos puntajes no
se pueden fusionar con los del texto.

`cohere.embed-v4` embebe imagen y texto en el MISMO espacio de 1536
dimensiones. Medido sobre los cuatro planos del inventario, por el margen
entre el acierto y el primer error:

    "plano de conexionado del TBEN"           CLIP +0.020   embed-v4 +0.126
    "diagrama de distribución eléctrica..."   CLIP +0.025   embed-v4 +0.026
    "plano con los códigos QD01 QF01..."      CLIP erró     embed-v4 +0.140

CLIP acierta dos de tres con márgenes de ~0.02, que es ruido, y falla la
consulta que separa una copia rotulada de su original. embed-v4 pone el plano
rotulado primero y el mismo plano sin rotular último: está leyendo los códigos
dibujados encima, que es justamente para lo que existen esas hojas.

Por qué un script aparte y no un cambio en el indexador
-------------------------------------------------------
Reconstruir solo el índice visual no necesita volver a llamar al modelo de
chunking, que es lo caro: los recortes ya están en disco desde la ingesta. Un
cambio en `indexing_task_dual.py` obligaría a reingestar el corpus entero para
cambiar de modelo de imágenes.

Además escribe en una colección NUEVA y deja la de CLIP intacta, para poder
comparar las dos antes de elegir. Un reemplazo in situ no se puede deshacer sin
reingestar.

Uso
---
    python scripts/rebuild_visual_index.py                 # muestra qué haría
    python scripts/rebuild_visual_index.py --escribir      # construye
    python scripts/rebuild_visual_index.py --comparar "plano de conexionado del TBEN"
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import chromadb  # noqa: E402

from config.config_reader import load_config  # noqa: E402
from task_utils.embedding_provider import EmbeddingProvider  # noqa: E402

RAIZ = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def proveedor(cfg) -> EmbeddingProvider:
    p = EmbeddingProvider(
        provider=cfg.embedding.embedding_provider,
        model=cfg.embedding.embedding_model,
        region=cfg.embedding.embedding_region,
        output_dimension=cfg.embedding.embedding_output_dimension or None,
    )
    if not p.soporta_imagenes():
        raise SystemExit(
            f"El modelo configurado ({p.model}) no embebe imágenes.\n"
            f"Hace falta un modelo multimodal en el .env de la ingesta, por ejemplo:\n"
            f"  embedding_provider=bedrock\n  embedding_model=cohere.embed-v4:0"
        )
    return p


def leer_origen(col):
    """
    Los registros del índice visual de CLIP: id, documento, metadata e imagen.

    Se lee de la colección y no de los archivos de chunks para heredar
    exactamente la metadata que armó el indexador. Reconstruirla acá sería una
    segunda implementación de la misma regla, lista para divergir en silencio.
    """
    datos = col.get(include=["documents", "metadatas"])
    filas = []
    for i, doc_id in enumerate(datos["ids"]):
        meta = (datos["metadatas"] or [{}] * len(datos["ids"]))[i] or {}
        ruta = meta.get("image_path")
        filas.append({
            "id": doc_id,
            "documento": (datos["documents"] or [""] * len(datos["ids"]))[i] or "",
            "meta": meta,
            "ruta": ruta,
            "existe": bool(ruta) and os.path.exists(ruta),
        })
    return filas


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--origen", default=None, help="colección visual de CLIP (default: la del .env)")
    p.add_argument("--destino", default=None, help="colección nueva (default: <origen>_mm)")
    p.add_argument("--escribir", action="store_true", help="construye; sin esto solo informa")
    p.add_argument("--comparar", metavar="CONSULTA",
                   help="rankea las imágenes de las dos colecciones para esa consulta")
    p.add_argument("--lote", type=int, default=16)
    args = p.parse_args()

    cfg = load_config(os.path.join(RAIZ, ".env"))
    ruta_indice = os.path.join(RAIZ, cfg.paths.index_path.lstrip("./"))
    # El nombre ya viene completo del .env; agregarle un sufijo acá lo duplicaba
    # ("visual_docs_v2_v2") y el script no encontraba nada.
    origen = args.origen or cfg.dual.visual_index_name
    destino = args.destino or f"{origen}_mm"

    cli = chromadb.PersistentClient(path=ruta_indice)
    existentes = {c.name for c in cli.list_collections()}
    if origen not in existentes:
        raise SystemExit(f"No existe la colección {origen!r} en {ruta_indice}\n"
                         f"Disponibles: {', '.join(sorted(existentes)) or '(ninguna)'}")

    col_origen = cli.get_collection(origen)
    filas = leer_origen(col_origen)
    faltan = [f for f in filas if not f["existe"]]

    print(f"  índice:   {ruta_indice}")
    print(f"  origen:   {origen} ({len(filas)} imágenes)")
    print(f"  destino:  {destino}")
    if faltan:
        print(f"  ⚠️  {len(faltan)} sin archivo en disco: se omiten")
        for f in faltan[:3]:
            print(f"        {f['ruta']}")

    utilizables = [f for f in filas if f["existe"]]
    if not utilizables:
        raise SystemExit("Ninguna imagen disponible en disco; nada que reconstruir.")

    prov = proveedor(cfg)
    print(f"  modelo:   {prov.model} @ {prov.region}")

    if args.comparar:
        comparar(cli, existentes, origen, destino, prov, args.comparar)
        return 0

    if not args.escribir:
        print(f"\n  Reconstruiría {len(utilizables)} imágenes. Volvé a correr con --escribir.")
        return 0

    col_destino = cli.get_or_create_collection(name=destino, metadata={"hnsw:space": "cosine"})
    hechas = 0
    for i in range(0, len(utilizables), args.lote):
        trozo = utilizables[i:i + args.lote]
        try:
            vectores = prov.embed_images([f["ruta"] for f in trozo])
        except Exception as e:
            print(f"  ⚠️  lote {i}: {e}")
            continue
        col_destino.upsert(
            ids=[f["id"] for f in trozo],
            embeddings=[list(v) for v in vectores],
            documents=[f["documento"] for f in trozo],
            metadatas=[f["meta"] for f in trozo],
        )
        hechas += len(trozo)
        print(f"  {hechas}/{len(utilizables)}")

    print(f"\n✅ {destino}: {col_destino.count()} imágenes en {len(vectores[0])} dimensiones")
    print(f"   {origen} queda intacta, para poder comparar antes de elegir.")
    return 0


def comparar(cli, existentes, origen, destino, prov, consulta):
    """Rankea las imágenes de ambas colecciones para la misma consulta escrita."""
    print(f"\n  «{consulta}»\n")
    vec_mm = prov.embed([consulta], input_type="search_query")[0]

    if destino in existentes:
        col = cli.get_collection(destino)
        r = col.query(query_embeddings=[vec_mm], n_results=min(5, col.count()))
        print(f"  {destino} ({prov.model}):")
        for dist, meta in zip(r["distances"][0], r["metadatas"][0]):
            print(f"     {1 - dist:.3f}  {os.path.basename((meta or {}).get('image_path', '?'))}")
    else:
        print(f"  {destino}: todavía no existe (correr con --escribir)")

    print(f"\n  {origen} (CLIP) no se puede consultar con el mismo vector:")
    print(f"     su espacio tiene otra dimensión, que es precisamente el problema")
    print(f"     que el modelo multimodal resuelve.")


if __name__ == "__main__":
    sys.exit(main())
