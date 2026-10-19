"""
Etiqueta los chunks del índice con los componentes que mencionan
================================================================

El problema: el mismo variador es `VFD01` en el inventario, `INV1` en la tesis y
`PowerFlex 4M` en el manual del fabricante, que son 3.626 vectores y no dice
`VFD01` ni una vez. Nada en el índice los vincula, así que una consulta recupera
lo que se parece semánticamente, no lo que habla del mismo equipo.

Esto agrega a cada chunk dos metadatos:

    entidades       códigos que el chunk NOMBRA (por código o por alias específico)
    entidades_doc   códigos que el DOCUMENTO documenta por completo

Están separados a propósito. Una página del manual del variador que explica el
parámetro P112 no nombra el equipo, pero es documentación suya: afirmar las dos
cosas con la misma fuerza haría que "traeme lo de VFD01" devolviera 3.626
fragmentos con el mismo peso que el chunk que dice `VFD01` explícitamente.

No recalcula embeddings
-----------------------
Las etiquetas son metadatos: el texto del chunk no cambia, así que su vector
tampoco. Chroma permite actualizar metadatos en sitio. Una reindexación completa
cuesta USD 7,36 y unas horas; esto son minutos y cero llamadas a modelos. Si más
adelante se decide que el código tiene que estar DENTRO del texto embebido, eso
sí exige reindexar, y conviene decidirlo con esta medición hecha.

    python scripts/etiquetar_entidades.py --dry-run   # qué haría
    python scripts/etiquetar_entidades.py             # lo hace
"""

import argparse
import re
import os
import sys
from collections import Counter, defaultdict

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = os.path.abspath(os.path.join(RAIZ, "..", "API"))
sys.path.insert(0, API)

from configs.Configuration import Configuration as C  # noqa: E402
from configs.entidades import etiquetas, componentes_del_documento  # noqa: E402

LOTE = 500


def _clave(codigo: str) -> str:
    """`ent_VFD01` a partir de `VFD01`. Solo alfanumérico: un código del
    inventario trae puntos suspensivos (`TS01…`) y Chroma no los acepta."""
    return "ent_" + re.sub(r"\W", "", codigo)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="no escribe nada")
    ap.add_argument("--coleccion", default=C.AppSettings__ChromaIndex)
    args = ap.parse_args()

    import chromadb

    cliente = chromadb.PersistentClient(path=C.chroma_local_path)
    try:
        col = cliente.get_collection(args.coleccion)
    except Exception as e:
        print(f"No pude abrir la colección {args.coleccion!r}: {e}")
        return 1

    total = col.count()
    print(f"Colección {args.coleccion}: {total} vectores")

    leidos = cambiados = 0
    por_doc = defaultdict(lambda: {"total": 0, "con_mencion": 0, "con_doc": 0})
    frecuencia = Counter()
    desplazamiento = 0

    while desplazamiento < total:
        lote = col.get(limit=LOTE, offset=desplazamiento,
                       include=["documents", "metadatas"])
        ids = lote.get("ids") or []
        if not ids:
            break
        nuevos_ids, nuevas_metas = [], []
        for cid, texto, meta in zip(ids, lote["documents"], lote["metadatas"]):
            meta = dict(meta or {})
            leidos += 1
            fn = meta.get("file_name") or ""
            # La pregunta sintética también nombra componentes, y es el vector
            # que de hecho va a matchear la consulta.
            fuente = f"{texto or ''} {meta.get('question') or ''}"
            menciona = etiquetas(fuente)
            del_doc = [c for c in componentes_del_documento(fn) if c not in menciona]

            d = por_doc[fn]
            d["total"] += 1
            d["con_mencion"] += 1 if menciona else 0
            d["con_doc"] += 1 if del_doc else 0
            frecuencia.update(menciona)

            # Chroma no acepta listas en metadatos: se guardan como texto
            # delimitado, con separadores en los extremos para que un filtro
            # por "contiene |VFD01|" no matchee un código que lo tenga adentro.
            nuevo = {
                "entidades": "|" + "|".join(menciona) + "|" if menciona else "",
                "entidades_doc": "|" + "|".join(del_doc) + "|" if del_doc else "",
                "n_entidades": len(menciona),
            }
            # Chroma no sabe filtrar por subcadena en un metadato, así que para
            # poder preguntarle "dame lo de VFD01" hace falta una clave booleana
            # por componente. El texto delimitado de arriba queda igual: sirve
            # para leerlo de un vistazo y para los filtros que no son de Chroma.
            for codigo in menciona:
                nuevo[_clave(codigo)] = True
            if any(meta.get(k) != v for k, v in nuevo.items()):
                cambiados += 1
                meta.update(nuevo)
                nuevos_ids.append(cid)
                nuevas_metas.append(meta)

        if nuevos_ids and not args.dry_run:
            col.update(ids=nuevos_ids, metadatas=nuevas_metas)
        desplazamiento += len(ids)
        print(f"  {desplazamiento}/{total}...", end="\r", flush=True)

    print(" " * 40, end="\r")
    print(f"\nLeídos {leidos} | a actualizar {cambiados}"
          + (" (dry-run: no se escribió)" if args.dry_run else " (escritos)"))

    print(f"\n{'documento':<44} {'chunks':>7} {'nombran':>8} {'por doc':>8}")
    print("-" * 72)
    for fn in sorted(por_doc, key=lambda x: -por_doc[x]["total"]):
        d = por_doc[fn]
        print(f"  {fn[:42]:<42} {d['total']:>7} {d['con_mencion']:>8} {d['con_doc']:>8}")

    print(f"\ncomponentes más mencionados:")
    for cod, n in frecuencia.most_common(12):
        print(f"  {cod:<8} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
