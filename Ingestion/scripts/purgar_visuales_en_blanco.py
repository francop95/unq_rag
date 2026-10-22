"""
Saca del índice los chunks visuales cuya imagen está en blanco
==============================================================

El síntoma: preguntar "¿el motor está conectado en estrella o triángulo?"
devolvía cuatro imágenes en blanco. El síntoma visible es el menos grave.

Lo que pasó realmente
---------------------
El recorte de esas cuatro figuras salió en blanco —el bbox apuntaba a papel—, y
al modelo de visión se le pidió igual que describiera la figura. En vez de decir
que no había nada, **inventó** un diagrama de motores. Las cinco descripciones
fabricadas son variantes de la misma plantilla de libro de texto:

    Componentes: motor M1, contactor K1, relé térmico F1, botón de arranque S1
    Conexiones: L1 → K1 → motor M1, S1 → K1
    Valores nominales: {"voltage": "380V", "current": "10A"}

El ARGEE_Reference_Manual es el manual de un entorno de programación: las
páginas 10, 17, 20 y 92 hablan de la barra de menú, de Edit Code, de Trace y de
las State Variables. No hay un solo diagrama de motores en todo el documento.
Nada de eso existe.

Por qué envenena el retrieval
-----------------------------
De cada chunk fabricado salieron además preguntas sintéticas, que es lo que de
verdad hace daño: son 19, están redactadas como las escribiría un usuario, y una
de ellas es "¿Cómo se conecta el motor trifásico M1 en el circuito?". Contra una
consulta real sobre el motor, ese vector gana por parecido casi literal. En la
medición que motivó este script ganó con 74% de similitud, por encima de toda la
documentación auténtica, y la respuesta final citó el diagrama inexistente como
prueba de que no hay conmutación estrella-triángulo.

O sea: no es ruido que baja el recall, es evidencia falsa que el modelo cita.

Qué borra
---------
Todo vector cuyo `media_path` apunte a un PNG en blanco, y las preguntas
sintéticas que cuelgan de él (`parent_chunk_id`). El texto auténtico de esas
páginas no se toca: vive en los chunks de texto, que son otros.

La identidad de un chunk es la terna (file_name, page_num, chunk_id). Hace falta
entera: `chunk_id` se reinicia en cada página, así que `chunk_2_visual` existe 88
veces solo dentro del ARGEE. Emparejar por chunk_id —incluso acotado al mismo
documento— arrastraba 37 preguntas legítimas del manual del PowerFlex junto a la
que había que borrar. Con la terna, las 1358 claves del índice son únicas.

No reindexa nada: es un borrado de vectores. La causa de raíz se arregla en la
ingesta, que ya no manda al modelo de visión un recorte en blanco.

    python scripts/purgar_visuales_en_blanco.py             # qué haría
    python scripts/purgar_visuales_en_blanco.py --escribir
"""

import argparse
import json
import os
import sys
from collections import defaultdict

from PIL import Image

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = os.path.abspath(os.path.join(RAIZ, "..", "API"))
sys.path.insert(0, API)

LOTE = 500

# Un PNG con menos de este porcentaje de píxeles con tinta es papel en blanco.
# No es cero: un recorte puede traer una línea de borde suelta y sigue sin
# mostrar nada. Medido sobre las 313 imágenes del índice, la separación es
# nítida: las seis en blanco dan 0,00% y la siguiente más vacía da bastante más.
MINIMO_TINTA = 0.02

# Por encima de este valor en escala de grises se considera papel.
UMBRAL_BLANCO = 244


def _fraccion_con_tinta(ruta: str) -> float:
    """Porción de la imagen que no es papel, medida sobre una miniatura."""
    im = Image.open(ruta).convert("L").resize((80, 80))
    return sum(1 for p in im.getdata() if p < UMBRAL_BLANCO) / 6400.0


def _resolver(media_path: str):
    """La ruta en disco de un `media_path` del índice, o None si no está."""
    if not media_path or not media_path.lower().endswith(".png"):
        return None
    if os.path.isabs(media_path) and os.path.exists(media_path):
        return media_path
    directo = os.path.join(RAIZ, media_path)
    if os.path.exists(directo):
        return directo
    if "media/" in media_path:
        p = os.path.join(RAIZ, "data", "media", media_path.split("media/", 1)[-1])
        if os.path.exists(p):
            return p
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--escribir", action="store_true", help="borra de verdad")
    ap.add_argument("--coleccion", default=None)
    # Además de los recortes vacíos, se pueden purgar las figuras que la
    # auditoría marcó como descritas con algo que no está en la imagen. Van por
    # el mismo camino porque el daño es el mismo —evidencia falsa indexada— y
    # porque el arrastre de preguntas sintéticas hay que hacerlo igual.
    ap.add_argument("--desde-auditoria", metavar="RUTA", default=None,
                    help="JSON de auditar_descripciones_visuales.py; purga los no_coincide")
    ap.add_argument("--conservar", nargs="*", default=[], metavar="RUTA_MEDIA",
                    help="media_path a NO purgar aunque la auditoría los marque")
    args = ap.parse_args()

    from configs.Configuration import Configuration as C
    import chromadb

    coleccion = args.coleccion or C.AppSettings__ChromaIndex
    cliente = chromadb.PersistentClient(path=C.chroma_local_path)
    try:
        col = cliente.get_collection(coleccion)
    except Exception as e:
        print(f"No pude abrir la colección {coleccion!r}: {e}")
        return 1

    total = col.count()
    print(f"Colección {coleccion}: {total} vectores\n")

    # Rutas que la auditoría marcó como descritas con algo que no está en la
    # imagen. Se comparan por el final de la ruta porque el índice guarda
    # `media/images/...` y la auditoría la ruta absoluta.
    marcadas_auditoria = set()
    if args.desde_auditoria:
        with open(args.desde_auditoria, encoding="utf-8") as f:
            for r in json.load(f):
                if r.get("veredicto") == "no_coincide":
                    marcadas_auditoria.add(os.path.normpath(r["ruta"]))
        conservar = {os.path.normpath(c) for c in args.conservar}
        marcadas_auditoria = {
            r for r in marcadas_auditoria
            if not any(r.endswith(c) or c.endswith(r) for c in conservar)
        }
        print(f"Auditoría: {len(marcadas_auditoria)} figuras con descripción "
              f"que no coincide" + (f" ({len(conservar)} conservadas a mano)"
                                    if conservar else "") + "\n")

    # Primera pasada: qué chunks tienen la imagen en blanco.
    en_blanco = {}          # (file_name, page_num, chunk_id) -> (id, media_path)
    hijos = defaultdict(list)   # misma terna del padre -> [(id, question)]
    sin_archivo = []
    desplazamiento = 0
    while desplazamiento < total:
        lote = col.get(limit=LOTE, offset=desplazamiento, include=["metadatas"])
        ids = lote.get("ids") or []
        if not ids:
            break
        for cid, meta in zip(ids, lote["metadatas"]):
            meta = meta or {}
            fn = meta.get("file_name") or ""
            pag = meta.get("page_num")
            padre = meta.get("parent_chunk_id")
            if meta.get("question") and padre:
                hijos[(fn, pag, padre)].append((cid, meta.get("question")))
                continue
            ruta = _resolver(meta.get("media_path") or "")
            if meta.get("media_path") and not ruta:
                sin_archivo.append(meta["media_path"])
                continue
            if not ruta:
                continue
            motivo = None
            if _fraccion_con_tinta(ruta) < MINIMO_TINTA:
                motivo = "recorte en blanco"
            elif os.path.normpath(ruta) in marcadas_auditoria:
                motivo = "descripción no coincide con la imagen"
            if motivo:
                en_blanco[(fn, pag, meta.get("chunk_id"))] = (cid, meta["media_path"], motivo)
        desplazamiento += len(ids)
        print(f"  revisados {desplazamiento}/{total}...", end="\r", flush=True)
    print(" " * 46, end="\r")

    if not en_blanco:
        print("Nada que purgar.")
        return 0

    a_borrar, detalle = [], []
    for clave, (cid, mp, motivo) in sorted(en_blanco.items(), key=lambda x: str(x[0])):
        fn, pag, chunk_id = clave
        preguntas = hijos.get(clave, [])
        a_borrar.append(cid)
        a_borrar.extend(c for c, _ in preguntas)
        detalle.append((fn, pag, chunk_id, mp, motivo, preguntas))

    print(f"Chunks a purgar: {len(en_blanco)}")
    print(f"Preguntas sintéticas colgando de ellos: {len(a_borrar) - len(en_blanco)}")
    print(f"Total de vectores a borrar: {len(a_borrar)}\n")

    for fn, pag, chunk_id, mp, motivo, preguntas in detalle:
        print(f"  {fn}  pág.{pag}  [{chunk_id}]  — {motivo}")
        print(f"      {mp}")
        for _, q in preguntas:
            print(f"      · {q}")
    if sin_archivo:
        print(f"\n  ({len(sin_archivo)} media_path sin archivo en disco, no se tocan)")

    if not args.escribir:
        print("\nNada borrado. Volvé a correr con --escribir para aplicar.")
        return 0

    col.delete(ids=a_borrar)
    print(f"\nBorrados {len(a_borrar)} vectores. La colección queda en {col.count()}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
