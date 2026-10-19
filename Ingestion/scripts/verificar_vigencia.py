"""
Verifica que lo que la API cita exista y esté vigente
=====================================================

Hay dos formas de que el sistema responda con documentación vieja, y el
manifiesto solo cubre una.

La que cubre: alguien agrega `plano_v2.pdf` y el `_v1` queda indexado. Eso se
detecta por el nombre y se marca `vigente: false`.

La que NO cubre, y es la que de verdad pasó: la configuración de la API nombra
un archivo por su nombre exacto, el corpus se renombra, y ese nombre deja de
coincidir. No falla ruidosamente — simplemente nunca coincide, los planos dejan
de adjuntarse y nadie se entera. En paralelo, la copia vieja seguía en el
servidor, así que la ruta del plano resolvía contra una revisión anterior.

Esto compara las tres listas que tienen que coincidir y que nada más compara:

  1. lo que el índice contiene          (file_name de los vectores)
  2. lo que el manifiesto declara vigente
  3. lo que la configuración de la API nombra

    python scripts/verificar_vigencia.py        # 0 si está todo bien, 1 si no
"""

import json
import os
import sqlite3
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = os.path.abspath(os.path.join(RAIZ, "..", "API"))
MANIFIESTO = os.path.join(RAIZ, "data", "ingestion_manifest.json")


def _indice():
    """Los file_name de la colección que sirve la API."""
    sys.path.insert(0, API)
    from configs.Configuration import Configuration as C
    ruta = os.path.join(C.chroma_local_path, "chroma.sqlite3")
    if not os.path.exists(ruta):
        sys.exit(f"No está el índice: {ruta}")
    con = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True)
    coleccion = C.AppSettings__ChromaIndex
    fila = con.execute("SELECT id FROM collections WHERE name=?", (coleccion,)).fetchone()
    if not fila:
        sys.exit(f"La colección {coleccion!r} no está en el índice")
    segmentos = [r[0] for r in con.execute("SELECT id FROM segments WHERE collection=?", (fila[0],))]
    marcadores = ",".join("?" * len(segmentos))
    ids = {r[0] for r in con.execute(
        f"SELECT id FROM embeddings WHERE segment_id IN ({marcadores})", segmentos)}
    nombres = {v for i, v in con.execute(
        "SELECT id,string_value FROM embedding_metadata WHERE key='file_name'") if i in ids}
    return C, nombres


def main() -> int:
    C, en_indice = _indice()
    manifiesto = json.load(open(MANIFIESTO, encoding="utf-8")) if os.path.exists(MANIFIESTO) else {}
    # El manifiesto guarda el nombre sin extensión; el índice, con ella.
    superadas = {n for n, e in manifiesto.items() if not e.get("vigente", True)}

    problemas = []

    # 1) nada superado debería seguir en el índice
    for nombre in sorted(en_indice):
        base = os.path.splitext(nombre)[0]
        if base in superadas:
            quien = manifiesto[base].get("superada_por", "?")
            problemas.append(f"indexado pero superado por {quien}: {nombre}")

    # 2) todo nombre que la configuración cita tiene que estar en el índice
    citados = list(getattr(C, "ELECTRIC_DIAGRAM_RELATED_FILES", []) or [])
    for nombre in citados:
        if nombre not in en_indice:
            problemas.append(f"citado en ELECTRIC_DIAGRAM_RELATED_FILES y ausente del índice: {nombre}")

    # 3) los PDF que se adjuntan enteros tienen que existir en disco
    for atributo in ("electric_diagram_path", "tben_diagram_path"):
        ruta = getattr(C, atributo, None)
        if ruta and not os.path.exists(ruta):
            problemas.append(f"{atributo} apunta a un archivo que no existe: {ruta}")

    print(f"índice: {len(en_indice)} documentos | manifiesto: {len(manifiesto)} | "
          f"superados: {len(superadas)} | citados por la API: {len(citados)}")
    if problemas:
        print(f"\n{len(problemas)} problema(s):")
        for p in problemas:
            print(f"  - {p}")
        return 1
    print("\nTodo coincide: lo que la API cita existe, está indexado y es la revisión vigente.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
