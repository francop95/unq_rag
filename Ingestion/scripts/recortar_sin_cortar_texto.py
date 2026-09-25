"""
Rehace los recortes de figura que cortan texto por la mitad
===========================================================

El problema
-----------
El modelo de visión devuelve el bbox de cada figura y el pipeline recorta ahí,
con un margen de seguridad del 3%. En páginas con maquetación densa —un póster
A1 a varias columnas, por ejemplo— ese bbox no se alinea con los bloques
visuales y el recorte parte una foto al medio, corta el título de la sección de
abajo y deja una franja de la columna vecina.

Visto en la demo: consultar "el secadero no calienta" devolvía un recorte del
póster que contenía la respuesta correcta (la cadena SSR01–SSR03 → R01–R03)
pero cortada arriba y abajo, y la reacción natural de quien lo ve es dudar de si
la imagen tiene algo que ver.

El arreglo
----------
El bbox queda guardado en el chunk, así que el recorte se puede rehacer sin
volver a llamar al modelo —que es lo caro—. Para cada recorte se leen los
bloques de texto de la página con PyMuPDF y se EXPANDE el bbox hasta contener
enteros todos los bloques que intersecta. Un bloque queda dentro o queda fuera,
nunca partido.

La expansión se limita: si obligara a cubrir más del `--tope` de la página
(80% por defecto), se usa la página completa. Un recorte que cubre casi todo no
aporta nada sobre la página entera y sí puede confundir.

Uso
---
    python scripts/recortar_sin_cortar_texto.py                 # informa
    python scripts/recortar_sin_cortar_texto.py --escribir      # rehace
    python scripts/recortar_sin_cortar_texto.py --solo Poster   # un documento
"""

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import fitz  # noqa: E402
from PIL import Image  # noqa: E402

from config.config_reader import load_config  # noqa: E402

RAIZ = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

# Margen mínimo alrededor del bbox, en fracción de página. Es el mismo criterio
# que el pipeline ya aplica al recortar; acá se conserva para no achicar
# recortes que hoy están bien.
MARGEN = 0.03


def bloques_de_texto(page):
    """Bloques de texto de la página, en coordenadas normalizadas."""
    w, h = page.rect.width, page.rect.height
    salida = []
    for x0, y0, x1, y1, texto, *_ in page.get_text("blocks"):
        if not (texto or "").strip():
            continue
        salida.append((x0 / w, y0 / h, x1 / w, y1 / h))
    return salida


def expandir(bbox, bloques, tope):
    """
    Expande el bbox hasta contener enteros los bloques que intersecta.

    Devuelve (bbox_nuevo, cuantos_bloques_partia). Si la expansión supera el
    tope de área, devuelve la página completa.
    """
    x0, y0, x1, y1 = bbox
    x0, y0 = max(0.0, x0 - MARGEN), max(0.0, y0 - MARGEN)
    x1, y1 = min(1.0, x1 + MARGEN), min(1.0, y1 + MARGEN)

    partidos = 0
    for bx0, by0, bx1, by1 in bloques:
        # ¿se solapan?
        if bx1 <= x0 or bx0 >= x1 or by1 <= y0 or by0 >= y1:
            continue
        # ¿está contenido entero?
        if bx0 >= x0 and bx1 <= x1 and by0 >= y0 and by1 <= y1:
            continue
        partidos += 1
        x0, y0 = min(x0, bx0), min(y0, by0)
        x1, y1 = max(x1, bx1), max(y1, by1)

    if (x1 - x0) * (y1 - y0) > tope:
        return (0.0, 0.0, 1.0, 1.0), partidos
    return (max(0.0, x0), max(0.0, y0), min(1.0, x1), min(1.0, y1)), partidos


def pdf_de(file_name, raw_root):
    """La ruta del PDF a partir del file_name que guarda el chunk."""
    for ruta in glob.glob(os.path.join(raw_root, "**", "*.pdf"), recursive=True):
        if os.sep + "old" + os.sep in ruta:
            continue
        if os.path.basename(ruta) == file_name:
            return ruta
    return None


def recortes_del_indice(cfg):
    """
    Los recortes que el índice sirve hoy: (file_name, page_num, chunk_id, media_path).

    Se lee del índice y no de los chunks porque lo que hay que arreglar es lo
    que el usuario ve, y eso es el archivo en data/media/ al que apunta la
    metadata.
    """
    import chromadb

    ruta = os.path.join(RAIZ, cfg.paths.index_path.lstrip("./"))
    col = chromadb.PersistentClient(path=ruta).get_collection(cfg.index.index_name)
    salida = []
    for tipo in ("image", "diagram_visual", "table"):
        r = col.get(where={"content_type": tipo}, include=["metadatas"])
        for m in r["metadatas"] or []:
            ruta = str(m.get("media_path") or "")
            # Los chunks de tabla guardan su media como JSON (markdown + filas),
            # no como imagen. No hay recorte que rehacer ahí.
            if ruta.lower().endswith((".png", ".jpg", ".jpeg")):
                salida.append(m)
    return salida


def bbox_del_chunk(file_stem, page_num, chunk_id, chunks_root):
    """Busca el bbox guardado del chunk en la última corrida del documento."""
    base = os.path.join(chunks_root, file_stem)
    if not os.path.isdir(base):
        return None
    corridas = sorted(d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d)))
    raiz_chunk = chunk_id.replace("_visual", "").replace("_ocr", "").replace("_structured", "")
    for corrida in reversed(corridas):
        for f in glob.glob(os.path.join(base, corrida, "*", "*.json")):
            try:
                c = json.load(open(f, encoding="utf-8"))
            except Exception:
                continue
            if str(c.get("page_num")) != str(page_num):
                continue
            cid = str(c.get("chunk_id", ""))
            if cid != chunk_id and cid != raiz_chunk:
                continue
            try:
                p = json.loads(c["original_chunk"])
            except Exception:
                continue
            if isinstance(p, dict) and p.get("bbox"):
                return p["bbox"]
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--escribir", action="store_true", help="rehace los recortes")
    ap.add_argument("--solo", default=None, help="filtra por nombre de documento")
    ap.add_argument("--tope", type=float, default=0.80,
                    help="si la expansión supera esta fracción de página, usa la página entera")
    ap.add_argument("--zoom", type=float, default=2.0)
    args = ap.parse_args()

    cfg = load_config(os.path.join(RAIZ, ".env"))
    raw_root = os.path.join(RAIZ, cfg.paths.raw_data_path.lstrip("./"))
    chunks_root = os.path.join(RAIZ, cfg.paths.chunks_data_path.lstrip("./"))
    media_root = os.path.join(RAIZ, "data")

    metas = recortes_del_indice(cfg)
    if args.solo:
        metas = [m for m in metas if args.solo.lower() in str(m.get("file_name", "")).lower()]
    print(f"  {len(metas)} recortes en el índice" + (f" (filtrado por «{args.solo}»)" if args.solo else ""))

    docs = {}
    arreglados = sin_bbox = sin_pdf = ok = pagina_entera = 0

    for m in metas:
        file_name = m.get("file_name", "")
        if not file_name.lower().endswith(".pdf"):
            continue  # el Excel no tiene página de la que recortar
        stem = os.path.splitext(file_name)[0]
        page_num = str(m.get("page_num", "")).split("-")[0]
        bbox = bbox_del_chunk(stem, page_num, str(m.get("chunk_id", "")), chunks_root)
        if not bbox:
            sin_bbox += 1
            continue

        if file_name not in docs:
            ruta = pdf_de(file_name, raw_root)
            if not ruta:
                sin_pdf += 1
                continue
            docs[file_name] = fitz.open(ruta)
        doc = docs[file_name]

        try:
            page = doc[int(page_num) - 1]
        except Exception:
            continue

        nuevo, partidos = expandir(tuple(bbox), bloques_de_texto(page), args.tope)
        if partidos == 0:
            ok += 1
            continue
        if nuevo == (0.0, 0.0, 1.0, 1.0):
            pagina_entera += 1

        destino = os.path.join(media_root, m["media_path"])
        antes = Image.open(destino).size if os.path.exists(destino) else None

        if args.escribir:
            w, h = page.rect.width, page.rect.height
            rect = fitz.Rect(nuevo[0] * w, nuevo[1] * h, nuevo[2] * w, nuevo[3] * h)
            pix = page.get_pixmap(matrix=fitz.Matrix(args.zoom, args.zoom), clip=rect)
            os.makedirs(os.path.dirname(destino), exist_ok=True)
            pix.save(destino)
            despues = Image.open(destino).size
        else:
            despues = (int((nuevo[2] - nuevo[0]) * page.rect.width * args.zoom),
                       int((nuevo[3] - nuevo[1]) * page.rect.height * args.zoom))

        arreglados += 1
        if arreglados <= 8:
            print(f"    {stem[:38]:<40} p{page_num:<4} cortaba {partidos} bloques  "
                  f"{antes} → {despues}" + ("  [página entera]" if nuevo == (0.0,0.0,1.0,1.0) else ""))

    for d in docs.values():
        d.close()

    print(f"\n  ya estaban bien:        {ok}")
    print(f"  rehechos:               {arreglados}" + ("" if args.escribir else "  (simulado)"))
    print(f"    de esos, página entera: {pagina_entera}")
    print(f"  sin bbox guardado:      {sin_bbox}")
    print(f"  sin PDF de origen:      {sin_pdf}")
    if not args.escribir:
        print("\n  Nada escrito. Volvé a correr con --escribir para aplicar.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
