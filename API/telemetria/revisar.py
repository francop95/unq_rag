"""
Revisión del feedback recibido
==============================

Lee la base de telemetría y muestra qué respuestas no sirvieron y qué pasó en
esas ejecuciones. Existe para que mirar el feedback no requiera escribir SQL:
si revisarlo cuesta trabajo, no se revisa.

    python -m telemetria.revisar                  # resumen
    python -m telemetria.revisar --negativos      # solo lo que falló
    python -m telemetria.revisar --id <query_id>  # una ejecución en detalle
    python -m telemetria.revisar --csv salida.csv # para abrir en una planilla
"""

import argparse
import csv
import json
import sqlite3
import sys
from collections import Counter

from .store import MOTIVOS, _conectar, listar_feedback, ver_ejecucion

ETIQUETAS = {m["id"]: m["etiqueta"] for m in MOTIVOS}


def resumen() -> None:
    con = _conectar()
    try:
        con.row_factory = sqlite3.Row
        tot = con.execute("SELECT COUNT(*) c FROM ejecuciones").fetchone()["c"]
        fb = con.execute("SELECT COUNT(*) c, SUM(util) u FROM feedback").fetchone()
    finally:
        con.close()

    n, utiles = fb["c"] or 0, fb["u"] or 0
    print(f"\n  ejecuciones registradas : {tot}")
    print(f"  con feedback            : {n}" + (f"  ({n/tot*100:.0f}%)" if tot else ""))
    if n:
        print(f"  sirvieron               : {utiles}/{n}  ({utiles/n*100:.0f}%)")

    motivos = Counter()
    for f in listar_feedback(limite=10000, solo_negativos=True):
        motivos.update(f["motivos"])
    if motivos:
        print("\n  por qué no sirvieron:")
        for mid, c in motivos.most_common():
            print(f"    {c:>4}  {ETIQUETAS.get(mid, mid)}")
    print()


def negativos(limite: int) -> None:
    filas = listar_feedback(limite=limite, solo_negativos=True)
    if not filas:
        print("\n  Sin feedback negativo todavía.\n")
        return
    print(f"\n  {len(filas)} respuestas marcadas como no útiles:\n")
    for f in filas:
        print(f"  ── {f['creado_en'][:19]}  ·  query_id {f['query_id']}")
        print(f"     pregunta : {(f.get('pregunta') or '?')[:96]}")
        print(f"     motivos  : {', '.join(ETIQUETAS.get(m, m) for m in f['motivos']) or '—'}")
        if f.get("comentario"):
            print(f"     comentario: «{f['comentario'][:160]}»")
        print(f"     contexto : {f.get('n_contextos')} chunks · score máx "
              f"{f.get('score_maximo') or 0:.1f} · índice {f.get('indice')}")
        print(f"     detalle  : python -m telemetria.revisar --id {f['query_id']}\n")


def detalle(query_id: str) -> None:
    d = ver_ejecucion(query_id)
    if d is None:
        print(f"\n  No hay ninguna ejecución con id {query_id}\n")
        return
    print(f"\n  pregunta   : {d['pregunta']}")
    if d.get("pregunta_reformulada") and d["pregunta_reformulada"] != d["pregunta"]:
        print(f"  reformulada: {d['pregunta_reformulada']}")
    print(f"  cuándo     : {d['creada_en'][:19]}" + ("  [desde caché]" if d["desde_cache"] else ""))
    print(f"  índice     : {d['indice']} · respuesta con {d['modelo_respuesta']}")
    print(f"  embeddings : {d['proveedor_embeddings']}/{d['modelo_embeddings']}")
    print(f"  retrieval  : top_k {d['top_k']} · umbral {d['umbral_similitud']} · "
          f"gate cruzado {'sí' if d['gate_cruzado'] else 'no'}")
    print(f"  resultado  : {d['n_contextos']} contextos ({d['n_con_imagen']} con imagen) · "
          f"score máx {d['score_maximo'] or 0:.1f} · "
          f"{d['tiempos'].get('total_time', 0):.1f}s")
    print(f"\n  respuesta  : {(d['respuesta'] or '')[:400]}\n")

    print("  chunks recuperados (en orden de relevancia):")
    for c in d["contextos"]:
        marcas = ("  [imagen]" if c["media_path"] else "") + ("  [mostrado]" if c["mostrado"] else "")
        print(f"   {c['posicion']:>3}. {c['score'] or 0:>6.1f}  "
              f"{(c['content_type'] or '?'):<18} {(c['file_name'] or '')[:40]:<42} "
              f"p{c['page_num'] or '?'}{marcas}")
        if c["texto"]:
            print(f"        {c['texto'][:110].strip()}")
    print()
    for f in d["feedback"]:
        print(f"  feedback   : {'sirvió' if f['util'] else 'NO sirvió'} · "
              f"{', '.join(ETIQUETAS.get(m, m) for m in f['motivos']) or '—'}")
        if f["comentario"]:
            print(f"               «{f['comentario']}»")
    print()


def exportar_csv(ruta: str) -> None:
    filas = listar_feedback(limite=100000)
    if not filas:
        print("\n  Nada que exportar.\n")
        return
    with open(ruta, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["creado_en", "query_id", "util", "motivos", "comentario",
                    "pregunta", "n_contextos", "score_maximo", "indice"])
        for f in filas:
            w.writerow([f["creado_en"], f["query_id"], "sí" if f["util"] else "no",
                        "; ".join(ETIQUETAS.get(m, m) for m in f["motivos"]),
                        f.get("comentario") or "", f.get("pregunta") or "",
                        f.get("n_contextos"), f.get("score_maximo"), f.get("indice")])
    print(f"\n  {len(filas)} filas en {ruta}\n")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--negativos", action="store_true", help="solo las respuestas que no sirvieron")
    p.add_argument("--id", metavar="QUERY_ID", help="detalle completo de una ejecución")
    p.add_argument("--csv", metavar="ARCHIVO", help="exporta el feedback a CSV")
    p.add_argument("--limite", type=int, default=30)
    a = p.parse_args()

    if a.id:
        detalle(a.id)
    elif a.csv:
        exportar_csv(a.csv)
    elif a.negativos:
        negativos(a.limite)
    else:
        resumen()
        negativos(min(a.limite, 5))
    return 0


if __name__ == "__main__":
    sys.exit(main())
