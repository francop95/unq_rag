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
import unicodedata
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

# A qué figuras aplicarlo. Dos filtros, y cada uno resuelve algo distinto.
#
# 1. EL DOCUMENTO. Los manuales de fabricante quedan afuera a propósito: son
#    figuras de media página dentro de documentación de terceros, y el coste
#    de equivocarse ahí es mucho menor que en el plano del secadero. Esta
#    pasada es cara —rasteriza a 432 dpi y manda varios mosaicos por figura—
#    y sobre el corpus actual incluirlos agregaría 15 figuras de manuales.
#
#    Es una lista, sí, pero de DOCUMENTOS y no de páginas. La que había antes
#    enumeraba páginas concretas de la tesis ({26, 53, 87, 88}) y estaba mal
#    en las dos direcciones: se perdía la 23, que es un wiring_diagram de la
#    instalación, y había que mantenerla a mano cada vez que algo se
#    re-troceaba y cambiaba de página.
#
# 2. EL TIPO, que lo asignó el troceo. Dentro de los documentos propios casi
#    no filtra —de 15 figuras, 11 son wiring_diagram, 3 connection_diagram y
#    1 layout— pero es lo que evita gastar la pasada en una foto si alguno de
#    esos documentos llega a tener una.
#
# `layout` entra: la página 53 de la tesis es la disposición del tablero, y
# tiene los mismos rótulos finos que el resto.
DOCUMENTOS_PROPIOS = (
    "0_Construcción", "1_Plano", "2_Planos", "conexionadoTben",
    "Poster_didactico", "Inventario_secadero", "Secadero v01",
)

TIPOS_PLANO = ("wiring_diagram", "connection_diagram", "schematic",
               "circuit_diagram", "layout", "panel_layout")


def _es_de_la_instalacion(file_name: str) -> bool:
    """¿El documento es nuestro, o es un manual de un tercero?

    Los dos lados se normalizan a NFC antes de comparar. El índice guarda los
    nombres tal como los dio macOS, en NFD —la "ó" de "Construcción" son dos
    code points— y un literal escrito en este archivo está en NFC. Sin
    normalizar, "0_Construcción" no matchea su propio nombre y las cuatro
    páginas de planos de la tesis quedan afuera en silencio. Comparar por
    fragmento no alcanza: sigue siendo comparar code points.
    """
    nombre = unicodedata.normalize("NFC", file_name or "")
    return any(unicodedata.normalize("NFC", f) in nombre for f in DOCUMENTOS_PROPIOS)


def _es_plano(texto: str) -> bool:
    """¿El troceo clasificó esta figura como un plano?

    Se lee del texto del chunk y no de la metadata porque es ahí donde el
    renderizador deja el tipo: la línea "Tipo: wiring_diagram".
    """
    linea = next((l for l in (texto or "").split("\n") if l.startswith("Tipo: ")), "")
    return linea[len("Tipo: "):].strip() in TIPOS_PLANO


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
    ap.add_argument("--desde-chunks", default=None, metavar="CARPETA",
                    dest="desde_chunks",
                    help="tomar los planos de una corrida de troceo en vez del "
                         "índice, y escribir la corrección en su JSON. Es el "
                         "modo que usa la ingesta: durante el pipeline los "
                         "chunks todavía no están indexados")
    ap.add_argument("--todos-los-documentos", action="store_true",
                    dest="todos_los_documentos",
                    help="incluir también los manuales de fabricante, que por "
                         "defecto quedan afuera por costo")
    ap.add_argument("--modelo", default="gpt-4.1")
    ap.add_argument("--zoom", type=float, default=ZOOM_ALTA)
    # Rehacer los tiles es lo caro; podar el resultado no. Con esto se repasa
    # el JSON ya producido sin volver a mirar ninguna imagen.
    ap.add_argument("--revisar", action="store_true",
                    help="solo poda el JSON existente contra sus transcripciones")
    args = ap.parse_args()

    from configs.Configuration import Configuration as C
    from task_utils.llm_json import LLMJsonClient, cliente_openai, text_content_part
    import chromadb

    if args.revisar:
        llm = LLMJsonClient(client=cliente_openai(), model=args.modelo, temperature=0.0,
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

    if args.desde_chunks:
        objetivos = objetivos_desde_chunks(args.desde_chunks)
        if args.solo:
            objetivos = [o for o in objetivos if o["pagina"] == args.solo]
        print(f"Planos a redescribir: {len(objetivos)}\n")
        if not objetivos:
            return 0
        return _procesar(objetivos, args, desde_chunks=True)

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
            if not _es_plano(doc):
                continue
            if not args.todos_los_documentos and not _es_de_la_instalacion(fn):
                continue
            try:
                pg = int(m.get("page_num"))
            except (TypeError, ValueError):
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
    return _procesar(objetivos, args, desde_chunks=False)


def _procesar(objetivos, args, desde_chunks: bool) -> int:
    """La pasada en sí. La comparten el modo índice y el modo chunks.

    Lo único que cambia entre los dos es de dónde salieron los objetivos y
    adónde va el resultado; el procedimiento —rasterizar a alta, localizar el
    recorte, enderezar, cortar en mosaicos, transcribir y componer— es el
    mismo, y tiene que seguir siéndolo: si divergieran, una figura corregida
    durante la ingesta diría algo distinto que la misma figura reparada
    después.
    """
    from task_utils.llm_json import LLMJsonClient, cliente_openai, text_content_part

    llm = LLMJsonClient(client=cliente_openai(), model=args.modelo, temperature=0.0,
                        max_output_tokens=2500, etapa="redescripcion_planos")

    resultados = []
    for o in objetivos:
        print(f"── {o['archivo'][:46]}  pág.{o['pagina']}")
        # En modo chunks el recorte vive en la propia corrida y la ruta ya
        # es absoluta; en modo índice hay que reconstruirla bajo data/media.
        ruta_crop = (o["media_path"] if os.path.exists(o["media_path"])
                     else os.path.join(RAIZ, "data", "media",
                                       o["media_path"].split("media/", 1)[-1]))
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

    if desde_chunks:
        escritos = aplicar_a_chunks(resultados)
        # La lectura cruda —las transcripciones de cada mosaico, cuántos
        # salieron, la descripción antes de podarla— queda al lado de los
        # chunks y no en `data/`: pertenece a ESTA corrida, y es con lo que
        # después se puede responder "¿de dónde salió esto?" sin volver a
        # pagar las llamadas. Se descartaba, y por eso no se podía rehacer
        # el pegado sin releer el plano entero.
        evidencia = os.path.join(args.desde_chunks, "planos_relectura.json")
        with open(evidencia, "w", encoding="utf-8") as f:
            json.dump(resultados, f, ensure_ascii=False, indent=2)
        print(f"\n   ✅ {escritos} figura(s) corregidas en el JSON del troceo")
        print(f"   📄 lectura cruda: {os.path.basename(evidencia)}")
        return 0

    with open(SALIDA, "w", encoding="utf-8") as f:
        json.dump(resultados, f, ensure_ascii=False, indent=2)
    print(f"\nEscrito: {SALIDA}")
    print("Revisalo antes de aplicarlo al índice.")
    return 0



# ─────────────────────── la misma pasada, pero sobre el troceo en disco

def objetivos_desde_chunks(chunks_dir: str):
    """Los planos de una corrida recién troceada, leídos del disco.

    El modo de siempre recorre el índice, y eso solo sirve para reparar algo
    ya indexado. Durante la ingesta los chunks todavía no están en Chroma: si
    la corrección no puede correr acá, queda como un script suelto que alguien
    tiene que acordarse de ejecutar — y si se olvida, el índice se queda con
    la lectura de baja resolución para siempre.
    """
    import glob

    objetivos = []
    for ruta in sorted(glob.glob(os.path.join(chunks_dir, "*", "*.json"))):
        try:
            with open(ruta, encoding="utf-8") as f:
                datos = json.load(f)
        except (OSError, ValueError):
            continue
        for i, chunk in enumerate(datos if isinstance(datos, list) else [datos]):
            if not isinstance(chunk, dict):
                continue
            if (chunk.get("content_type") or "") not in ("diagram_visual", "image"):
                continue
            original = chunk.get("original_chunk")
            if isinstance(original, str):
                try:
                    original = json.loads(original)
                except ValueError:
                    continue
            if not isinstance(original, dict):
                continue
            notas = original.get("notes")
            if not isinstance(notas, dict):
                continue
            if (notas.get("diagram_type") or "") not in TIPOS_PLANO:
                continue
            media = original.get("image_path") or original.get("media_path") or ""
            if not str(media).lower().endswith(".png") or not os.path.exists(media):
                continue
            objetivos.append({
                "ruta_json": ruta, "indice_en_json": i,
                "archivo": chunk.get("file_name") or "",
                "pagina": int(chunk.get("page_num") or 0),
                "media_path": media,
                "descripcion_vieja": "",
            })
    return objetivos


def aplicar_a_chunks(resultados) -> int:
    """Escribe las descripciones nuevas en el JSON del troceo.

    En el JSON y no en el índice: así el artefacto de disco es la verdad y el
    índice vuelve a ser un render de él. Mientras la corrección viva solo en
    Chroma, reindexar la destruye y la pantalla de revisión muestra una
    versión que no es la que responde.
    """
    from task_utils.chunk_text import notas_desde_relectura

    por_archivo = {}
    for r in resultados:
        if r.get("descripcion_nueva") and r.get("ruta_json"):
            por_archivo.setdefault(r["ruta_json"], []).append(r)

    escritos = 0
    for ruta, rs in por_archivo.items():
        with open(ruta, encoding="utf-8") as f:
            datos = json.load(f)
        lista = datos if isinstance(datos, list) else [datos]
        for r in rs:
            chunk = lista[r["indice_en_json"]]
            original = chunk.get("original_chunk")
            if isinstance(original, str):
                original = json.loads(original)
            viejas = original.get("notes") or {}
            # La lectura cruda anterior se conserva: es la evidencia de qué
            # había que corregir, y sin ella no se puede medir si la pasada
            # de alta resolución mejora o empeora.
            original.setdefault("notes_troceo", original.get("notes"))

            nuevas = notas_desde_relectura(r["descripcion_nueva"])
            # Los recorridos los sigue poniendo el troceo, no la relectura.
            # Cada pasada ve una cosa distinta: el troceo mira la hoja entera
            # a 144 dpi —borrosa, pero puede seguir una línea de punta a
            # punta— y la relectura mira mosaicos a 6x, donde el texto se lee
            # pero los conductores se cortan en el borde de cada mosaico. La
            # propia relectura lo dice: "no se puede asegurar la continuidad
            # de todas las conexiones entre fragmentos".
            #
            # Medido en el plano codificado: el troceo da 16 aristas con
            # código (QD01 → QF01), la relectura 22 pero nombradas por el
            # rótulo impreso ("Inter. dif. In: 40A 30mA"). La tabla de
            # conexiones se siembra de acá y se cruza contra los códigos del
            # inventario: con los rótulos no engancha ninguno, y quien revisa
            # pasa de corregir detalles a escribir la tabla de cero, que es
            # justo lo que esta pantalla existe para evitar.
            #
            # Las de la relectura no se tiran: son aristas que el troceo no
            # vio (QF04 → PS01, Arduino → Servo) y quedan como evidencia.
            if viejas.get("connections"):
                nuevas["connections_relectura"] = nuevas.get("connections")
                nuevas["connections"] = viejas["connections"]
            original["notes"] = nuevas
            chunk["original_chunk"] = json.dumps(original, ensure_ascii=False)
            escritos += 1
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, indent=2)
    return escritos


if __name__ == "__main__":
    sys.exit(main())
