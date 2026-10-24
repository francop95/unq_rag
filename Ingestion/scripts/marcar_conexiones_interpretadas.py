"""
Rotula como interpretadas las listas de conexiones ya indexadas
===============================================================

El texto de cada chunk se arma en la ingesta y se guarda tal cual en el índice,
así que cambiar el renderizador (`task_utils.chunk_text`) solo afecta a lo que
se ingiera de acá en adelante. Los chunks que ya están siguen diciendo
"Conexiones:" a secas. Esto los migra.

Qué cambia y por qué
--------------------
Solo el rótulo de esa lista. El contenido no se toca: no es una corrección de
los recorridos —para eso hay que volver a mirar la figura, que es lo que hace
`redescribir_figuras_con_valores.py`— sino la marca de que esa lista salió de
MIRAR el dibujo y no de transcribir un texto.

La distinción existe en el prompt de ingesta desde siempre ("textos_en_la_figura"
y "valores_nominales" se copian; "conexiones" y "dispositivos" se miran) pero se
perdía al renderizar: una cadena deducida quedaba con el mismo tono que una
etiqueta copiada, y el modelo de respuesta no tenía cómo saber la diferencia.

Las dos colecciones no se tratan igual
--------------------------------------
En el mismo Chroma viven el índice de texto (`multimodal_documents_v4_4o`,
3072 dimensiones, OpenAI) y el visual (`visual_docs_v4_4o`, 512, CLIP). Los dos
guardan la descripción de la figura como documento, pero embeben cosas
distintas: el primero embebe ESE TEXTO y el segundo LA IMAGEN.

Entonces el texto cambia en los dos, y el vector se rehace solo en el de texto,
donde dejó de representar al documento. En el visual el vector es de la imagen,
que no cambió: se reescribe el documento y se le devuelve su embedding tal cual,
explícitamente, para que Chroma no intente recalcularlo con su función por
defecto y meta un vector de otro modelo y otra dimensión.

El visual está apagado hoy (`USE_VISUAL_RETRIEVAL = False`), pero se migra
igual: dejarlo a medias significa que el día que se encienda vuelven los
rótulos viejos sin que nadie se entere.

Los metadatos no se tocan y las preguntas sintéticas tampoco: no contienen la
lista, y el rótulo no cambia qué responde el chunk.

    python scripts/marcar_conexiones_interpretadas.py            # qué haría
    python scripts/marcar_conexiones_interpretadas.py --escribir
"""

import argparse
import collections
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "src"))
sys.path.insert(0, os.path.abspath(os.path.join(RAIZ, "..", "API")))

# El rótulo viejo, con los dos puntos: así empieza la línea en el texto guardado.
VIEJO = "Conexiones: "

# De a cuántos se piden los embeddings. El mismo tamaño que usa el resto de los
# scripts de mantenimiento del índice.
LOTE = 64


def migrar_texto(texto: str, rotulo_nuevo: str) -> str:
    """Reescribe el rótulo de la lista de conexiones, si está y es el viejo.

    Trabaja línea por línea y solo sobre el comienzo de la línea: "Conexiones:"
    puede aparecer dentro de la descripción en prosa o de un texto transcripto
    de la figura, y ahí no es un rótulo nuestro sino contenido del documento.
    """
    salida, tocada = [], False
    for linea in texto.split("\n"):
        if not tocada and linea.startswith(VIEJO):
            salida.append(f"{rotulo_nuevo}: " + linea[len(VIEJO):])
            tocada = True
        else:
            salida.append(linea)
    return "\n".join(salida)


def pendientes_de(col, rotulo: str, con_embedding: bool):
    """Los chunks de la colección a los que les falta el rótulo nuevo."""
    incluir = ["documents", "metadatas"] + (["embeddings"] if con_embedding else [])
    salida, ya = [], 0
    total, off = col.count(), 0
    while off < total:
        b = col.get(limit=500, offset=off, include=incluir)
        embs = b.get("embeddings") if con_embedding else None
        for i, (cid, doc) in enumerate(zip(b["ids"], b["documents"])):
            doc = doc or ""
            if f"{rotulo}: " in doc:
                ya += 1
                continue
            nuevo = migrar_texto(doc, rotulo)
            if nuevo == doc:
                continue
            meta = (b["metadatas"] or [{}] * len(b["ids"]))[i] or {}
            salida.append({"id": cid, "texto": nuevo,
                           "archivo": meta.get("file_name") or "?",
                           "embedding": embs[i] if embs is not None else None})
        off += len(b["ids"])
    return salida, ya


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--escribir", action="store_true")
    args = ap.parse_args()

    from configs.Configuration import Configuration as C
    from openai import OpenAI
    from task_utils.chunk_text import ROTULO_CONEXIONES
    from task_utils.embedding_provider import EmbeddingProvider
    import chromadb

    cliente = chromadb.PersistentClient(path=C.chroma_local_path)
    existentes = {c.name for c in cliente.list_collections()}

    # (nombre, re-embeber). El visual guarda el vector de la imagen: el texto
    # cambia pero el vector no, así que se conserva en vez de recalcularse.
    objetivo = [(C.AppSettings__ChromaIndex, True)]
    visual = getattr(C, "VISUAL_INDEX_NAME", None)
    if visual and visual != C.AppSettings__ChromaIndex:
        objetivo.append((visual, False))

    trabajo = []
    for nombre, reembeber in objetivo:
        if nombre not in existentes:
            print(f"{nombre}: no está en este Chroma, se saltea\n")
            continue
        col = cliente.get_collection(nombre)
        pend, ya = pendientes_de(col, ROTULO_CONEXIONES, con_embedding=not reembeber)
        print(f"{nombre}: {col.count()} vectores | ya rotulados: {ya} | "
              f"a migrar: {len(pend)} | "
              f"{'re-embebe el texto' if reembeber else 'conserva el vector de la imagen'}")
        for archivo, n in collections.Counter(p["archivo"] for p in pend).most_common():
            print(f"    {n:4d}  {archivo}")
        print()
        if pend:
            trabajo.append((col, pend, reembeber))

    if not trabajo:
        print("Nada que hacer.")
        return 0
    if not args.escribir:
        print("Nada escrito. Volvé a correr con --escribir para aplicar.")
        return 0

    emb = None
    for col, pend, reembeber in trabajo:
        textos = [p["texto"] for p in pend]
        if reembeber:
            if emb is None:
                emb = EmbeddingProvider(
                    provider="openai",
                    model=getattr(C, "EMBEDDING_MODEL", "text-embedding-3-large"),
                    openai_client=OpenAI(),
                )
            print(f"{col.name}: embebiendo {len(textos)} textos con {emb.model}...")
            vectores = []
            for i in range(0, len(textos), LOTE):
                vectores.extend(emb.embed(textos[i:i + LOTE], input_type="search_document"))
        else:
            vectores = [p["embedding"] for p in pend]
            if any(v is None for v in vectores):
                print(f"{col.name}: faltan embeddings; no se escribe nada.")
                return 1
            vectores = [list(v) for v in vectores]

        if len(vectores) != len(pend):
            print(f"{col.name}: los embeddings no coinciden con los textos; "
                  "no se escribe nada.")
            return 1

        # update y no delete+add: los metadatos y las preguntas sintéticas que
        # cuelgan de estos chunks quedan intactos, que es justamente lo que se
        # quiere acá. El embedding va explícito siempre —aun cuando no cambie—
        # para que Chroma no lo recalcule con su función por defecto.
        col.update(ids=[p["id"] for p in pend], documents=textos, embeddings=vectores)
        print(f"{col.name}: {len(pend)} chunks rotulados; sigue en {col.count()} vectores.\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
