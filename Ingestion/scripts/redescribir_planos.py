"""
Vuelve a describir los planos, leyéndolos a una resolución que se pueda leer
============================================================================

El problema concreto
--------------------
La descripción guardada del plano de distribución principal (Apéndice 14, pág.
87 de la tesis) dice:

    Inter. Ter. Int. 40A → Inter. Ter. Int. 30A → Inter. Ter. Int. 25A

En el plano no hay ningún interruptor de 30A. Hay un solo dispositivo rotulado

    Inter. dif.
    In: 40A
    30mA

—un diferencial tetrapolar de 40 A con 30 mA de sensibilidad—, y debajo un
`Inter. Ter. In: 25A`. El modelo partió la etiqueta en dos y convirtió los 30 mA
en un interruptor de 30 A que no existe. También inventó un 2,5 A (lo real es
`-F1 1,4-1,6A` sobre el motor) y describió en serie unas ramas que cuelgan en
paralelo del diferencial.

Por qué pasó
------------
No es que el modelo "alucine" sin motivo: no podía leer la etiqueta. La ingesta
rasteriza la página con zoom 2.0, o sea 144 dpi, y la API de visión además
reescala la imagen para que el lado corto no pase de 768 px. Un plano A4 denso
llega al modelo con los rótulos en unos pocos píxeles de alto. A esa altura
`30mA` y `30A` son el mismo manchón.

Encima la página 87 no tiene NADA de texto nativo —son 88 imágenes incrustadas,
cada rótulo un fragmento ráster suelto—, así que no hay capa de texto de la que
tirar: leer el plano es literalmente mirarlo.

Lo que hace este script
-----------------------
1. Rasteriza la página de origen a alta resolución (432 dpi por defecto).
2. Ubica el recorte guardado dentro de esa página por correlación, para
   re-renderizar la MISMA región con más detalle. No hace falta el bbox
   original, que no se guardó: los dos vienen del mismo rasterizador, así que
   basta con bajar ambos a una escala común y buscar la posición.
3. Endereza el dibujo. Los planos están rotados 90° respecto de la hoja y el
   modelo lee bastante peor de costado.
4. Lo corta en tiles solapados lo bastante chicos como para que la API no los
   reescale, y transcribe cada uno pidiendo SOLO lo legible, con lo ilegible
   marcado como tal.
5. Compone la descripción final a partir de las transcripciones, con la
   instrucción explícita de no agregar nada que no esté en ellas.

No toca el índice. Escribe un JSON para revisar antes de aplicar nada.

    python scripts/redescribir_planos.py --solo 87
    python scripts/redescribir_planos.py
"""

import argparse
import io
import json
import os
import re
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "src"))
sys.path.insert(0, os.path.abspath(os.path.join(RAIZ, "..", "API")))

import fitz  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

SALIDA = os.path.join(RAIZ, "data", "planos_redescritos.json")

# 6.0 = 432 dpi. A 144 dpi (lo que usa la ingesta) los rótulos del plano no se
# leen ni mirándolos de cerca; a 432 sí, y es lo que motivó todo esto.
ZOOM_ALTA = 6.0

# El zoom con el que la ingesta rasterizó la página para recortar las figuras.
# Se usa solo para localizar el recorte dentro de la página: hay que comparar
# contra un render de la MISMA escala o la razón de tamaños miente.
ZOOM_INGESTA = 2.0

# Lado máximo de cada tile. La API reescala por encima de ~2000 px, así que un
# tile más grande pierde justo el detalle que se fue a buscar.
LADO_TILE = 1400
SOLAPE = 0.15

# Los planos de ESTA instalación. El manual del fabricante queda afuera a
# propósito: son figuras de media página dentro de documentación de terceros, y
# el coste de equivocarse ahí es mucho menor que en el plano del secadero.
DOCUMENTOS_PLANO = {
    "0_Construcción y automatización de un secadero de pastas industrial CORREGIDO (2).pdf":
        {26, 53, 87, 88},
    "1_Plano distribucion electrica_v1.pdf": None,          # None = todas
    "2_Planos_secadero_codificados_propuesta.pdf": None,
    "conexionadoTben.pdf": None,
    "Poster_didactico_secadero_componentes_y_planos.pdf": None,
    "Inventario_secadero_revision_Pablo_v2.xlsx": None,
}

def _nfc(s: str) -> str:
    """Nombre de archivo comparable.

    El índice guarda los nombres tal como los dio macOS, en NFD: la "ó" de
    "Construcción" son dos code points. Un literal escrito en este archivo está
    en NFC y es un code point. Son cadenas distintas para Python aunque se vean
    idénticas, y sin esto el filtro por documento no matchea nada.
    """
    import unicodedata
    return unicodedata.normalize("NFC", s or "")


SISTEMA_ORIENTACION = """Mirás un plano técnico. Decime cuántos grados hay que
rotarlo EN SENTIDO ANTIHORARIO para que su texto quede derecho y legible.
Respondé solo JSON: {"grados": 0|90|180|270}"""

SISTEMA_TILE = """Transcribís un fragmento de un plano eléctrico. No lo
interpretás: lo transcribís.

Reglas, en orden de importancia:
1. Copiá los rótulos EXACTAMENTE como están escritos, con sus saltos de línea
   y unidades. Si dice "In: 40A" y debajo "30mA", eso es UN rótulo de UN
   dispositivo: "In: 40A / 30mA". No lo partas en dos dispositivos.
2. mA y A son unidades distintas. V y VA también. No las normalices.
3. Si algo no se lee con seguridad, ponelo en "ilegible". NO adivines. Un
   rótulo a medias es peor que un rótulo ausente.
4. Anotá solo las conexiones cuyo trazo podés seguir DENTRO de este fragmento.
   Si un cable sale del borde, decilo en "sale_del_fragmento".
5. No completes con lo que "suele haber" en un plano así.

JSON:
{"dispositivos": [{"rotulo": "texto exacto", "simbolo": "qué símbolo es"}],
 "textos": ["todo texto suelto legible"],
 "conexiones": ["A -> B, solo si se ve el trazo"],
 "sale_del_fragmento": ["cables que cruzan el borde"],
 "ilegible": ["qué había pero no se pudo leer"]}"""

SISTEMA_COMPOSICION = """Recibís las transcripciones de los fragmentos de un
plano eléctrico y tenés que componer UNA descripción del plano completo.

Regla única y absoluta: no podés afirmar nada que no esté en las
transcripciones. Si algo aparece como ilegible, o no aparece, NO va.

- Cada fragmento trae su caja [x0,y0,x1,y1] dentro del plano. Un mismo rótulo
  visto en dos fragmentos QUE SE SOLAPAN es UN solo dispositivo. El mismo
  rótulo en fragmentos que NO se solapan son dispositivos DISTINTOS, y van los
  dos. Un plano de distribución tiene varios interruptores con el mismo
  calibre: fusionarlos borra protecciones que existen.
- Por cada dispositivo indicá en qué fragmentos aparece, para poder revisarlo.
- Respetá los rótulos al pie de la letra. "In: 40A / 30mA" es un diferencial de
  40 A con 30 mA de sensibilidad: UN dispositivo, no dos.
- Si dos fragmentos se contradicen, decilo en "dudas" en vez de elegir uno.
- Las conexiones que ningún fragmento pudo trazar no se inventan.

Devolvé JSON:
{"resumen": "2-3 frases sobre qué es el plano y para qué sirve",
 "tipo": "wiring_diagram|schematic|connection_diagram|layout",
 "dispositivos": [{"rotulo": "texto exacto", "que_es": "...", "fragmentos": [1,2]}],
 "conexiones": ["las que se pudieron trazar"],
 "textos_en_la_figura": ["rótulos y textos"],
 "valores_nominales": {"clave": "valor con unidad"},
 "dudas": ["lo que quedó sin resolver o ilegible"]}"""


SISTEMA_REVISION = """Recibís una descripción compuesta de un plano y las
transcripciones crudas de las que salió. Tu trabajo es podarla, no mejorarla.

Quitá o corregí, en este orden:

1. DUPLICADOS POR SOLAPE. Dos entradas que son el mismo dispositivo visto en
   tiles contiguos, una con el rótulo cortado por el borde: "Inter." e
   "Inter. dif." con los mismos valores son UNO. Quedate con el rótulo más
   completo. OJO: el mismo rótulo en tiles que NO se tocan son dispositivos
   distintos y los dos se quedan.
2. ENTRADAS QUE NO SON DISPOSITIVOS. "ilegible", una letra suelta de la grilla
   del cajetín (A, B, C, D, E), un número de columna. Van a "dudas" o se
   descartan.
3. VALORES DERIVADOS. Si el plano dice "3 x 2kW", el valor es "3 x 2kW". Un
   "6kW" calculado por vos no está escrito en ninguna parte: fuera.
4. CUALQUIER AFIRMACIÓN que no puedas encontrar en las transcripciones.

No agregues nada. No completes nada. Si dudás, va a "dudas".

Devolvé el MISMO formato JSON que recibiste, podado."""


def _a_gris_pequeno(im, lado=260):
    """Miniatura en escala de grises como array, para comparar posiciones."""
    g = im.convert("L")
    esc = lado / max(g.size)
    g = g.resize((max(1, int(g.width * esc)), max(1, int(g.height * esc))), Image.LANCZOS)
    a = np.asarray(g, dtype=np.float32)
    return (a - a.mean()) / (a.std() + 1e-6)


def ubicar_recorte(pagina: Image.Image, recorte: Image.Image):
    """Dónde cae `recorte` dentro de `pagina`, en coordenadas normalizadas.

    `pagina` tiene que estar renderizada a la MISMA escala que usó la ingesta
    para producir el recorte (zoom 2.0). Comparar contra el render de alta
    resolución da una razón de tamaños de 0,333 en vez de 1,0, la guarda de
    "el recorte es la página entera" no se dispara, y la búsqueda termina
    encontrando una ventana de un tercio de página en cualquier lado: es el
    error que hizo que los planos codificados se describieran desde el
    rincón equivocado de la hoja.

    Devuelve (x0, y0, x1, y1) en 0..1, o None si no hay coincidencia clara.
    """
    rel = recorte.width / pagina.width, recorte.height / pagina.height
    if rel[0] > 0.97 and rel[1] > 0.97:
        return (0.0, 0.0, 1.0, 1.0)          # el recorte ES la página

    P = _a_gris_pequeno(pagina, 300)
    # El recorte vino del MISMO render a otro zoom, así que su tamaño relativo
    # a la página es el mismo en cualquier escala: se lleva a la escala de P.
    rw = max(2, int(rel[0] * P.shape[1]))
    rh = max(2, int(rel[1] * P.shape[0]))
    if rw >= P.shape[1] or rh >= P.shape[0]:
        return (0.0, 0.0, 1.0, 1.0)
    R = _a_gris_pequeno(recorte.resize((rw, rh), Image.LANCZOS), max(rw, rh))
    if R.shape != (rh, rw):
        R = R[:rh, :rw]

    mejor, mejor_pos = -1e9, None
    for y in range(0, P.shape[0] - rh + 1, 2):
        for x in range(0, P.shape[1] - rw + 1, 2):
            v = float((P[y:y + rh, x:x + rw] * R).sum())
            if v > mejor:
                mejor, mejor_pos = v, (x, y)
    if mejor_pos is None:
        return None
    # Normalizado contra el máximo teórico (correlación consigo mismo)
    norma = float((R * R).sum())
    if norma <= 0 or mejor / norma < 0.35:
        return None
    x, y = mejor_pos
    return (x / P.shape[1], y / P.shape[0],
            (x + rw) / P.shape[1], (y + rh) / P.shape[0])


def tiles(im: Image.Image):
    """Trocea en tiles solapados que la API no vaya a reescalar."""
    if max(im.size) <= LADO_TILE:
        return [((0, 0, im.width, im.height), im)]
    paso = int(LADO_TILE * (1 - SOLAPE))
    salida = []
    for y in range(0, im.height, paso):
        for x in range(0, im.width, paso):
            caja = (x, y, min(x + LADO_TILE, im.width), min(y + LADO_TILE, im.height))
            if caja[2] - caja[0] < 80 or caja[3] - caja[1] < 80:
                continue
            salida.append((caja, im.crop(caja)))
            if caja[2] >= im.width:
                break
        if y + LADO_TILE >= im.height:
            break
    return salida


def _parte_imagen(im: Image.Image, detalle="high"):
    import base64
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return {"type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}", "detail": detalle}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--solo", type=int, default=None, help="una sola página")
    ap.add_argument("--documento", default=None, help="filtrar por nombre de archivo")
    ap.add_argument("--modelo", default="gpt-4.1")
    ap.add_argument("--zoom", type=float, default=ZOOM_ALTA)
    # Rehacer los tiles es lo caro; podar el resultado no. Con esto se repasa
    # el JSON ya producido sin volver a mirar ninguna imagen.
    ap.add_argument("--revisar", action="store_true",
                    help="solo poda el JSON existente contra sus transcripciones")
    args = ap.parse_args()

    from configs.Configuration import Configuration as C
    from openai import OpenAI
    from task_utils.llm_json import LLMJsonClient, text_content_part
    import chromadb

    if args.revisar:
        llm = LLMJsonClient(client=OpenAI(), model=args.modelo, temperature=0.0,
                            max_output_tokens=2500, etapa="revision_planos")
        with open(SALIDA, encoding="utf-8") as f:
            previos = json.load(f)
        print(f"Revisando {len(previos)} descripciones ya compuestas\n")
        for r in previos:
            antes = r.get("descripcion_nueva") or {}
            if not antes:
                continue
            podada = llm.complete_json(
                SISTEMA_REVISION,
                [text_content_part(
                    "DESCRIPCIÓN COMPUESTA:\n" + json.dumps(antes, ensure_ascii=False)
                    + "\n\nTRANSCRIPCIONES CRUDAS:\n"
                    + json.dumps(r.get("transcripciones") or [], ensure_ascii=False)[:60000])],
                label="revision")
            if isinstance(podada, dict):
                r["descripcion_sin_podar"] = antes
                r["descripcion_nueva"] = podada
                d0 = len(antes.get("dispositivos") or [])
                d1 = len(podada.get("dispositivos") or [])
                print(f"  {r['archivo'][:40]:<42} pág.{r['pagina']:<4} "
                      f"dispositivos {d0} → {d1}")
        with open(SALIDA, "w", encoding="utf-8") as f:
            json.dump(previos, f, ensure_ascii=False, indent=2)
        print(f"\nEscrito: {SALIDA}")
        return 0

    cli = chromadb.PersistentClient(path=C.chroma_local_path)
    col = cli.get_collection(C.AppSettings__ChromaIndex)

    # Qué figuras tocar
    objetivos, tot, off = [], col.count(), 0
    while off < tot:
        b = col.get(limit=500, offset=off, include=["documents", "metadatas"])
        for cid, doc, m in zip(b["ids"], b["documents"], b["metadatas"]):
            m = m or {}
            if m.get("question"):
                continue
            mp = m.get("media_path") or ""
            if not mp.lower().endswith(".png"):
                continue
            fn = m.get("file_name") or ""
            clave = next((k for k in DOCUMENTOS_PLANO if _nfc(k) == _nfc(fn)), None)
            if clave is None:
                continue
            paginas = DOCUMENTOS_PLANO[clave]
            try:
                pg = int(m.get("page_num"))
            except (TypeError, ValueError):
                continue
            if paginas is not None and pg not in paginas:
                continue
            if args.solo and pg != args.solo:
                continue
            if args.documento and args.documento.lower() not in fn.lower():
                continue
            objetivos.append({"id": cid, "archivo": fn, "pagina": pg,
                              "media_path": mp, "descripcion_vieja": (doc or "").strip()})
        off += len(b["ids"])

    print(f"Planos a redescribir: {len(objetivos)}\n")
    if not objetivos:
        return 0

    llm = LLMJsonClient(client=OpenAI(), model=args.modelo, temperature=0.0,
                        max_output_tokens=2500, etapa="redescripcion_planos")

    resultados = []
    for o in objetivos:
        print(f"── {o['archivo'][:46]}  pág.{o['pagina']}")
        ruta_crop = os.path.join(RAIZ, "data", "media",
                                 o["media_path"].split("media/", 1)[-1])
        pdf = os.path.join(RAIZ, "data", "raw_data", o["archivo"])
        if not os.path.exists(pdf):
            for sub in ("hardware", "old"):
                alt = os.path.join(RAIZ, "data", "raw_data", sub, o["archivo"])
                if os.path.exists(alt):
                    pdf = alt
                    break

        if pdf.lower().endswith(".pdf") and os.path.exists(pdf):
            doc = fitz.open(pdf)
            pag = doc[o["pagina"] - 1]
            pix = pag.get_pixmap(matrix=fitz.Matrix(args.zoom, args.zoom))
            pagina_alta = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
            region = None
            if os.path.exists(ruta_crop):
                # La localización se hace contra un render a la escala de la
                # ingesta; la región resultante es normalizada, así que se
                # aplica igual sobre el render de alta.
                pb = pag.get_pixmap(matrix=fitz.Matrix(ZOOM_INGESTA, ZOOM_INGESTA))
                pagina_baja = Image.open(io.BytesIO(pb.tobytes("png"))).convert("RGB")
                region = ubicar_recorte(pagina_baja, Image.open(ruta_crop).convert("RGB"))
            if region and region != (0.0, 0.0, 1.0, 1.0):
                w, h = pagina_alta.size
                img = pagina_alta.crop((int(region[0] * w), int(region[1] * h),
                                        int(region[2] * w), int(region[3] * h)))
                print(f"   región localizada en la página: {tuple(round(v,3) for v in region)}")
            else:
                img = pagina_alta
                print("   se usa la página completa")
            origen = f"render {args.zoom}x del PDF"
        else:
            # Sin PDF de origen (p. ej. xlsx): lo único disponible es el recorte.
            if not os.path.exists(ruta_crop):
                print("   sin imagen disponible, se salta")
                continue
            img = Image.open(ruta_crop).convert("RGB")
            img = img.resize((img.width * 3, img.height * 3), Image.LANCZOS)
            origen = "recorte guardado ampliado x3 (no hay PDF de origen)"
        print(f"   {origen}  →  {img.size}")

        # Enderezar
        vista = img.copy()
        vista.thumbnail((1100, 1100), Image.LANCZOS)
        r = llm.complete_json(SISTEMA_ORIENTACION, [_parte_imagen(vista, "low")],
                              label="orientacion")
        grados = int((r or {}).get("grados", 0)) % 360
        if grados:
            img = img.rotate(grados, expand=True)
            print(f"   rotado {grados}° para enderezar el texto")

        partes = tiles(img)
        print(f"   {len(partes)} tiles de hasta {LADO_TILE}px")
        transcripciones = []
        for i, (caja, t) in enumerate(partes, 1):
            tr = llm.complete_json(
                SISTEMA_TILE,
                [text_content_part(f"Fragmento {i} de {len(partes)}. "
                                   f"Posición en el plano: {caja}."),
                 _parte_imagen(t, "high")],
                label=f"tile {i}")
            if tr:
                transcripciones.append({"fragmento": i, "caja": caja, **tr})
        print(f"   transcritos {len(transcripciones)}/{len(partes)}")

        solape = (
            "Dos fragmentos se solapan si sus cajas se tocan. El paso entre "
            f"fragmentos es de {int(LADO_TILE * (1 - SOLAPE))}px y cada uno mide "
            f"hasta {LADO_TILE}px, así que solo se solapan los contiguos."
        )
        final = llm.complete_json(
            SISTEMA_COMPOSICION,
            [text_content_part(solape + "\n\nTRANSCRIPCIONES DE LOS FRAGMENTOS:\n"
                               + json.dumps(transcripciones, ensure_ascii=False)[:60000])],
            label="composicion")
        resultados.append({**o, "origen": origen, "rotacion": grados,
                           "n_tiles": len(partes),
                           "transcripciones": transcripciones,
                           "descripcion_nueva": final})
        if final:
            print(f"   dispositivos: {len(final.get('dispositivos') or [])}"
                  f"   conexiones: {len(final.get('conexiones') or [])}"
                  f"   dudas: {len(final.get('dudas') or [])}")

    with open(SALIDA, "w", encoding="utf-8") as f:
        json.dump(resultados, f, ensure_ascii=False, indent=2)
    print(f"\nEscrito: {SALIDA}")
    print("Revisalo antes de aplicarlo al índice.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
