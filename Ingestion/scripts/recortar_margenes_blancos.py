"""
Quita el papel en blanco que sobra alrededor de una figura
==========================================================

El recorte del pipeline toma el bbox que devolvió el modelo de visión y lo
expande para no partir bloques de texto. Esa expansión es horizontal con
facilidad —la columna de texto es ancha— y el resultado son recortes que
incluyen media página en blanco a los costados.

Medido sobre las 313 imágenes del índice: **198 tienen más del 20% de su ancho
en blanco**, y una llega al 38% con la foto ocupando apenas el centro.

El efecto no es solo estético. En el visor la imagen se escala para entrar en la
pantalla, y si un tercio de ella es papel, la figura se ve un tercio más chica
de lo que podría. Peor: la foto rodeada de blanco parece *recortada de un lienzo
mayor*, que es exactamente la duda que reportaron los usuarios.

Lo que hace y lo que no
-----------------------
Recorta el blanco que rodea al contenido, dejando un margen pequeño. **No**
agranda el recorte ni recupera nada que el bbox haya dejado afuera: si la figura
venía cortada, sigue cortada. Para eso está `recortar_sin_cortar_texto.py`, que
rehace el recorte desde el bbox guardado.

Es una operación local sobre los PNG ya generados: no llama a ningún modelo, no
toca el índice y no cambia ninguna ruta, porque reescribe cada archivo en su
sitio.

    python scripts/recortar_margenes_blancos.py            # qué haría
    python scripts/recortar_margenes_blancos.py --escribir
"""

import argparse
import os
import sqlite3
import sys

from PIL import Image, ImageChops

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = os.path.abspath(os.path.join(RAIZ, "..", "API"))
sys.path.insert(0, API)

# Margen que se conserva alrededor del contenido, en píxeles. No cero: una figura
# pegada al borde del archivo es justo lo que da la impresión de estar cortada.
MARGEN = 12

# Por debajo de esto no se toca: el ahorro no compensa reescribir el archivo, y
# un recorte milimétrico sobre un borde difuso puede comerse una línea fina.
MINIMO_RECORTE = 0.08

# Un archivo casi vacío no se recorta: quedaría una miniatura de unos pocos
# píxeles, peor que el original. Son restos de figuras que el modelo marcó mal.
MINIMO_CONTENIDO = 0.02

# Tolerancia al blanco: un escaneo no da 255 puro, así que se trata como fondo
# todo lo que esté por encima de este valor en los tres canales.
UMBRAL_BLANCO = 244


def _caja_de_contenido(im: Image.Image):
    """El rectángulo que encierra lo que no es papel, o None si está todo blanco."""
    gris = im.convert("L").point(lambda v: 0 if v >= UMBRAL_BLANCO else 255)
    return gris.getbbox()


def rutas_del_indice():
    """Las imágenes que el índice referencia, que son las que el usuario ve."""
    from configs.Configuration import Configuration as C

    bd = os.path.join(C.chroma_local_path, "chroma.sqlite3")
    if not os.path.exists(bd):
        sys.exit(f"No está el índice: {bd}")
    con = sqlite3.connect(f"file:{bd}?mode=ro", uri=True)
    rutas = set()
    for _, v in con.execute(
        "SELECT id,string_value FROM embedding_metadata WHERE key='media_path'"
    ):
        if not v or not v.lower().endswith(".png"):
            continue
        p = v if os.path.isabs(v) else os.path.join(RAIZ, v)
        if not os.path.exists(p) and "media/" in v:
            p = os.path.join(RAIZ, "data", "media", v.split("media/", 1)[-1])
        if os.path.exists(p):
            rutas.add(p)
    return sorted(rutas)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--escribir", action="store_true", help="reescribe los PNG")
    args = ap.parse_args()

    rutas = rutas_del_indice()
    print(f"{len(rutas)} imágenes referenciadas por el índice\n")

    recortadas = saltadas = vacias = 0
    ahorro = []
    for p in rutas:
        try:
            im = Image.open(p).convert("RGB")
        except Exception as e:
            print(f"  no se pudo abrir {os.path.basename(p)}: {e}")
            continue
        w, h = im.size
        caja = _caja_de_contenido(im)
        if not caja:
            vacias += 1
            continue
        cw, ch = caja[2] - caja[0], caja[3] - caja[1]
        if (cw * ch) / (w * h) < MINIMO_CONTENIDO:
            vacias += 1
            continue

        nueva = (max(0, caja[0] - MARGEN), max(0, caja[1] - MARGEN),
                 min(w, caja[2] + MARGEN), min(h, caja[3] + MARGEN))
        nw, nh = nueva[2] - nueva[0], nueva[3] - nueva[1]
        if (w * h - nw * nh) / (w * h) < MINIMO_RECORTE:
            saltadas += 1
            continue

        recortadas += 1
        ahorro.append(1 - (nw * nh) / (w * h))
        if args.escribir:
            im.crop(nueva).save(p)

    import statistics

    print(f"  recortadas : {recortadas}" + ("" if args.escribir else "  (simulado)"))
    print(f"  ya ajustadas: {saltadas}")
    print(f"  casi vacías : {vacias}  (no se tocan)")
    if ahorro:
        print(f"  área de papel eliminada: mediana {statistics.median(ahorro):.0%}, "
              f"máximo {max(ahorro):.0%}")
    if not args.escribir:
        print("\nNada escrito. Volvé a correr con --escribir para aplicar.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
