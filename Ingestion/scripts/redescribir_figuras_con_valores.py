"""
Vuelve a leer las figuras que declaran magnitudes, transcribiéndolas
====================================================================

El problema
-----------
La chapa del motor del forzador (tesis, pág. 16) dice:

    MODELO: MT 802-4    V 220/380    A 3.51/2.03    Conexión Δ/Y

y el índice la tenía guardada como:

    MODELO: MT 160-4    V 230/380    A 3.5/2.03     Conexión A/Y

La Δ se volvió una A, y con eso el asistente respondió que "la placa indica
Conexión A/Y (estrella)", presentando como confirmación de la conexión real
algo que la chapa nunca dijo: Δ/Y significa que el motor ADMITE las dos.

Dos causas, no una
------------------
1. El pedido. `_FIGURE_SYSTEM_PROMPT` decía "describí la figura para que sea
   recuperable por búsqueda semántica", y describir invita a parafrasear. Un
   valor parafraseado es un valor inventado. Pidiéndole TRANSCRIBIR ese mismo
   recorte, sin tocarlo, el modelo devuelve `Δ/Y` y `220/380`, las dos bien.
   Esto es lo que arregla los errores graves.

2. El tamaño del texto que le llega al modelo. Con `detail: high` la API lleva
   el lado corto de la imagen a 768 px, SIEMPRE: ampliar la figura antes de
   mandarla no cambia nada, porque del otro lado se vuelve a achicar. La única
   forma de darle más píxeles por carácter es mandarle MENOS figura por imagen.
   Medido sobre esta misma chapa: la figura entera se lee `A 3.51/2`, y la zona
   de la chapa sola se lee `A 3.51/2.03`, que es lo que dice el original.

Qué hace
--------
1. TROCEADO. La figura se corta en mosaicos solapados y cada uno se amplía
   hasta el presupuesto de la API. Un mosaico cubre menos superficie con los
   mismos 768 px, así que cada carácter llega más grande.
2. TRANSCRIPCIÓN, dos veces por mosaico. Solo copiar lo escrito, sin
   interpretar.
3. COTEJO de las dos lecturas, acá en Python y no en el modelo. Una magnitud
   que las dos leyeron igual se guarda; una que leyeron distinto NO se guarda:
   pasa a "no legible". Es el único modo de distinguir un valor leído de uno
   adivinado, porque el modelo adivina con la misma seguridad con la que lee.
4. DESCRIPCIÓN, con la transcripción ya cotejada a la vista. Los textos y los
   valores salen de ahí tal cual; esta pasada solo agrega qué muestra la
   figura y cómo se conecta.

Pedir todo esto en una sola llamada es exactamente lo que produjo `A/Y`.

A qué figuras
-------------
A las que declaran magnitudes en su texto (tensiones, corrientes, potencias,
rpm, temperaturas): son las que alguien va a usar para decidir algo, y las
únicas donde una transcripción equivocada se vuelve un diagnóstico equivocado.
Los planos quedan afuera: ya los reescribió `redescribir_planos.py`, que además
los endereza, y volver a pasarlos por acá los empeora.

    python scripts/redescribir_figuras_con_valores.py --limite 3
    python scripts/redescribir_figuras_con_valores.py
    python scripts/aplicar_planos_redescritos.py --entrada data/figuras_redescritas.json
"""

import argparse
import base64
import io
import json
import os
import re
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "src"))
sys.path.insert(0, os.path.abspath(os.path.join(RAIZ, "..", "API")))

from PIL import Image  # noqa: E402

SALIDA = os.path.join(RAIZ, "data", "figuras_redescritas.json")

# Los planos ya los reescribió `redescribir_planos.py`, que los lee a 432 dpi y
# los endereza. Volver a pasarlos por acá los EMPEORA: se vio en la primera
# corrida, donde el plano de la pág. 87 perdía el `2 kW` y recuperaba el `2.5A`
# inventado que esa reescritura había sacado.
YA_REESCRITOS = os.path.join(RAIZ, "data", "planos_redescritos.json")

# Magnitudes con unidad: criterio de selección de figuras y unidad de cotejo
# entre las dos lecturas.
#
# Hacen falta las dos formas porque el orden no es estable. Una chapa escribe
# `V 220/380` y `A 3.51/2.03`, con la unidad ADELANTE; un catálogo escribe
# `40A`, con la unidad atrás. Buscando solo "número seguido de unidad", la
# chapa del motor no producía ninguna magnitud: el cotejo entre las dos
# lecturas comparaba dos conjuntos vacíos y daba todo por bueno, que es
# exactamente el error que este script existe para no cometer.
#
# Los pares con barra se parten en valores sueltos: `V 220/380` da {220V, 380V},
# para que dos lecturas se puedan diferenciar en UNO de los dos.
_UNIDADES = r"kV|kW|mA|Vca|Vcc|Vac|Vdc|RPM|rpm|Nm|bar|HP|Hz|°C|Ω|V|A|W"
_GRUPO = r"\d+(?:[.,]\d+)?(?:\s*/\s*\d+(?:[.,]\d+)?)*"
# Un solo barrido con dos alternativas, no dos barridos: con dos, el mismo
# texto se cuenta de las dos formas y `V 220/380 Hz 50 A 3.51` produce además
# `220Hz`, `380Hz` y `50A`, pares cruzados que no existen en la figura. Las
# alternativas de un finditer no se solapan, así que cada número queda con la
# unidad que de verdad lo acompaña.
MAGNITUD = re.compile(
    rf"(?P<up>\b(?:{_UNIDADES}))\s*(?P<np>{_GRUPO})"
    rf"|(?P<nn>{_GRUPO})\s*(?P<un>(?:{_UNIDADES})\b)")

# Lado del mosaico EN PÍXELES DE LA FIGURA ORIGINAL. Es el parámetro que
# gobierna cuántos píxeles por carácter recibe el modelo: la API va a llevar
# cada mosaico a 768 px de lado corto, así que cuanto menos superficie cubra el
# mosaico, más grande llega su texto. 420 px deja ~1.8x de ampliación efectiva
# en los recortes chicos del corpus, que es el orden en el que la chapa del
# motor pasó de leerse `3.51/2` a leerse `3.51/2.03`.
LADO_MOSAICO = 420
SOLAPE = 0.15
MAX_MOSAICOS = 12
# A lo que se amplía cada mosaico antes de mandarlo. Por encima del lado corto
# que la API acepta no hay ganancia, pero tampoco pérdida, y evita depender de
# que el reescalado de la API interpole mejor que LANCZOS.
LADO_ENVIO = 1000


def mosaicos(im: Image.Image, desfase: float = 0.0):
    """
    Corta la figura en mosaicos solapados, cada uno ampliado para el envío.

    `desfase` corre la grilla medio paso. Las dos lecturas que se cotejan usan
    desfases distintos a propósito: con la MISMA grilla el modelo ve exactamente
    los mismos píxeles dos veces y, a temperatura 0, repite su error con la
    misma seguridad —así `3.51/2.03` se leyó `3.51/2.09` las dos veces y el
    cotejo lo dio por bueno—. Dos encuadres distintos son dos observaciones;
    dos llamadas sobre el mismo encuadre son una sola, hecha dos veces.
    """
    paso = max(1, int(LADO_MOSAICO * (1 - SOLAPE)))
    inicio = -int(paso * desfase)

    def cortes(largo):
        """
        Tramos de LADO_MOSAICO, el último pegado al borde en vez de recortado.

        Sin eso, el tramo final puede quedar de 100 px contra uno de 420 y, al
        ampliar por el lado corto, ese mosaico se estira 8x: se manda una tira
        de 1000x3400 que la API vuelve a achicar, o sea más llamadas por menos
        resolución efectiva.
        """
        if largo <= LADO_MOSAICO:
            return [(0, largo)]
        pos, p = [], inicio
        while True:
            a = max(0, min(p, largo - LADO_MOSAICO))
            tramo = (a, a + LADO_MOSAICO)
            if tramo not in pos:
                pos.append(tramo)
            if tramo[1] >= largo:
                break
            p += paso
        return pos

    cajas, vistas = [], set()
    for a, b in cortes(im.height):
        for c, d in cortes(im.width):
            if (c, a, d, b) not in vistas:
                vistas.add((c, a, d, b))
                cajas.append((c, a, d, b))

    salida = []
    for caja in cajas[:MAX_MOSAICOS]:
        trozo = im.crop(caja)
        f = max(1.0, LADO_ENVIO / max(1, min(trozo.size)))
        if f > 1.0:
            trozo = trozo.resize((int(trozo.width * f), int(trozo.height * f)), Image.LANCZOS)
        salida.append(trozo)
    return salida


def _parte_imagen(im: Image.Image):
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return {"type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "high"}}


def _texto(t: str):
    return {"type": "text", "text": t}


SISTEMA_TRANSCRIPCION = """Transcribís texto de figuras técnicas. No describís
nada: solo copiás lo que está escrito.

Reglas:
- Carácter por carácter. No redondees, no normalices, no completes, no traduzcas.
  220/380 no es 230/380. 3.51 no es 3.5. MT 802-4 no es MT 160-4.
- Los símbolos no son letras. Δ (triángulo) NO es una A. Y, Ω, °, ±, µ, ~ van
  como están. "Conexión Δ/Y" se escribe Δ/Y.
- Las unidades cambian el significado: mA no es A, kW no es W, Vca no es Vcc.
- Si un carácter o un valor no se lee con seguridad, NO lo adivines: describí
  dónde está en "ilegible" y omitilo de "lineas".
- Recibís un FRAGMENTO de la figura. Si no hay texto, devolvé listas vacías;
  no completes lo que supongas que había en el resto.

Un valor equivocado es peor que un valor ausente: el primero se usa para
diagnosticar, el segundo solo falta.

Devolvé JSON {"lineas": ["...", ...], "ilegible": ["...", ...]}."""


SISTEMA_DESCRIPCION = """Describís figuras de documentación técnica industrial
para que se puedan recuperar por búsqueda semántica.

Recibís la figura y FRAGMENTOS TRANSCRIPTOS de ella.

Dos campos se COPIAN y el resto se MIRA:
- "textos_en_la_figura" y "valores_nominales" salen de la transcripción, literal.
- "resumen", "dispositivos", "conexiones" y "tipo" los sacás de MIRAR la figura.
  Las flechas, los recorridos y la forma de los bloques no están en ninguna
  transcripción —el texto no las lleva— y son la mitad de lo que hace útil a un
  diagrama: si la figura muestra que el diferencial alimenta al termomagnético
  general y ese a tres ramas, eso va en "conexiones" aunque ninguna línea
  transcripta lo diga. Describir de menos acá no es ser prudente, es perder el
  dibujo.

Los fragmentos vienen de recortes solapados leídos DOS VECES con encuadres
distintos, así que el mismo texto aparece repetido, a veces cortado y a veces
leído distinto. Tu trabajo con ellos es reconciliarlos:

- Una línea repetida se escribe UNA vez.
- Un fragmento cortado se completa con el que lo contiene; no inventes el resto.
- Si dos lecturas del MISMO dato no coinciden (0.76 y 0.78, 40°C y 45°C,
  3.51/2.03 y 3.51/2.09), ese dato NO se pudo leer: va a "dudas" y no va ni a
  "textos_en_la_figura" ni a "valores_nominales" ni al "resumen". No elijas
  una de las dos.
- En esos dos campos no agregues texto que no esté en ningún fragmento, y no
  corrijas lo que dicen: copiás, no interpretás.

Una chapa de características, una placa de datos o una tabla de selección dicen
lo que el equipo ADMITE, no cómo está instalado: "Conexión Δ/Y" significa que
acepta estrella y triángulo. En "resumen" no afirmes que el equipo ESTÁ
conectado, ajustado o configurado de alguna manera si lo único que ves es la
especificación.

Devolvé JSON:
{"resumen": "2-4 frases: qué muestra y para qué sirve",
 "tipo": "photo|wiring_diagram|schematic|connection_diagram|panel_layout|chart|other",
 "dispositivos": ["motor eléctrico trifásico", "ventilador axial"],
 "conexiones": ["L1 → K1 → motor M1"],
 "textos_en_la_figura": ["las líneas reconciliadas, una sola vez cada una"],
 "valores_nominales": {"voltage": "220/380 V", "current": "3.51/2.03 A"},
 "dudas": ["lo que la figura tiene escrito y no se pudo leer con seguridad"]}"""


def _resolver(media_path: str):
    if not media_path or not media_path.lower().endswith(".png"):
        return None
    for cand in (media_path, os.path.join(RAIZ, media_path)):
        if os.path.exists(cand):
            return cand
    if "media/" in media_path:
        p = os.path.join(RAIZ, "data", "media", media_path.split("media/", 1)[-1])
        if os.path.exists(p):
            return p
    return None


def _ids_de_planos() -> set:
    try:
        with open(YA_REESCRITOS, encoding="utf-8") as f:
            return {p.get("id") for p in json.load(f) if p.get("descripcion_nueva")}
    except (OSError, ValueError):
        return set()


def figuras_con_magnitudes(filtro_archivo: str = ""):
    """Figuras indexadas cuyo texto declara magnitudes con unidad."""
    from configs.Configuration import Configuration as C
    import chromadb

    planos = _ids_de_planos()
    col = chromadb.PersistentClient(path=C.chroma_local_path).get_collection(
        C.AppSettings__ChromaIndex)
    total, off, salida, saltados = col.count(), 0, [], 0
    while off < total:
        lote = col.get(limit=500, offset=off, include=["documents", "metadatas"])
        ids = lote.get("ids") or []
        if not ids:
            break
        for cid, doc, meta in zip(ids, lote["documents"], lote["metadatas"]):
            meta, doc = meta or {}, (doc or "").strip()
            if meta.get("question") or meta.get("content_type") != "diagram_visual":
                continue
            if cid in planos:
                saltados += 1
                continue
            interes = "\n".join(l for l in doc.split("\n")
                                if l.startswith(("Textos en la figura:", "Valores nominales:")))
            if not magnitudes(interes):
                continue
            if filtro_archivo and filtro_archivo.lower() not in str(meta.get("file_name")).lower():
                continue
            ruta = _resolver(meta.get("media_path") or "")
            if not ruta:
                continue
            salida.append({"id": cid, "archivo": meta.get("file_name"),
                           "pagina": meta.get("page_num"), "ruta": ruta,
                           "descripcion_vieja": doc})
        off += len(ids)
    return salida, saltados


def magnitudes(texto: str) -> set:
    """{'220V', '380V', '3.51A', '2.03A'} — cada valor con su unidad, ya suelto."""
    salida = set()
    for m in MAGNITUD.finditer(texto or ""):
        unidad = m.group("up") or m.group("un")
        numeros = m.group("np") or m.group("nn") or ""
        for n in numeros.split("/"):
            n = n.strip().replace(",", ".")
            if n:
                salida.add(f"{n}{unidad}")
    return salida


def _normal(s: str) -> str:
    """Para comparar: sin espacios, en minúsculas. `V 220/380` ≡ `v220/380`."""
    return "".join(str(s).split()).lower()


NUMERO = re.compile(r"\d+(?:[.,]\d+)?")


def no_transcripto(valor: str, crudo_normalizado: str) -> bool:
    """
    True si algún número de `valor` no aparece en ninguna transcripción.

    Es la garantía dura del script, y va en Python y no en el prompt: la pasada
    de descripción reconcilia fragmentos, y reconciliar se parece lo suficiente
    a redactar como para que se le escape un valor "arreglado". Lo que no se
    transcribió no entra, por más razonable que suene.

    Se cotejan los NÚMEROS y no el par número+unidad porque el orden de los dos
    no es estable: esta chapa escribe `V 220/380` y `A 3.51/2.03`, con la unidad
    adelante, así que buscar `380V` en la transcripción da negativo y tiraba
    valores que estaban bien leídos.
    """
    return any(n.replace(",", ".") not in crudo_normalizado
               for n in NUMERO.findall(str(valor)))


def valores_en_disputa(lectura_a: list, lectura_b: list) -> list:
    """
    Magnitudes que una lectura vio y la otra no.

    Las dos lecturas usan encuadres distintos (ver mosaicos()), así que son dos
    observaciones y no dos muestras de la misma. Una magnitud que solo aparece
    en una es un valor que el modelo completó, no que leyó: `3.1/1.8` y
    `3.51/2` son los dos plausibles y los dos están mal.
    """
    return sorted(magnitudes(" ".join(lectura_a)) ^ magnitudes(" ".join(lectura_b)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limite", type=int, default=0)
    ap.add_argument("--archivo", default="", help="filtrar por nombre de archivo")
    ap.add_argument("--hilos", type=int, default=6)
    ap.add_argument("--modelo", default=None)
    args = ap.parse_args()

    figuras, saltados = figuras_con_magnitudes(args.archivo)
    if args.limite:
        figuras = figuras[: args.limite]
    print(f"Figuras que declaran magnitudes: {len(figuras)}"
          + (f"   ({saltados} son planos ya reescritos, se saltean)" if saltados else ""))
    if not figuras:
        return 0

    from openai import OpenAI
    from task_utils.llm_json import LLMJsonClient, run_parallel
    from config.config_reader import ConfigReader

    modelo = args.modelo or ConfigReader().get("multimodal_model", "gpt-4o")
    llm = LLMJsonClient(client=OpenAI(), model=modelo, temperature=0.0,
                        max_output_tokens=4000, etapa="redescripcion_figuras")
    print(f"Modelo: {modelo}\n")

    def leer(partes, etiqueta):
        """Una pasada de transcripción sobre todos los mosaicos de una figura."""
        lineas, ilegible = [], []
        for i, parte in enumerate(partes, 1):
            t = llm.complete_json(
                SISTEMA_TRANSCRIPCION,
                [_texto(f"Fragmento {i} de {len(partes)} de la figura. "
                        "Transcribí el texto que se ve en ESTE fragmento."), parte],
                label=f"{etiqueta} frag.{i}")
            lineas += [str(x) for x in ((t or {}).get("lineas") or [])]
            ilegible += [str(x) for x in ((t or {}).get("ilegible") or [])]
        return lineas, ilegible

    def redescribir(fig):
        im = Image.open(fig["ruta"]).convert("RGB")
        # Dos encuadres distintos, no dos llamadas sobre el mismo (ver mosaicos()).
        trozos_a = mosaicos(im, desfase=0.0)
        trozos_b = mosaicos(im, desfase=0.5)
        et = f"{fig['archivo'][:26]} p.{fig['pagina']}"

        l1, i1 = leer([_parte_imagen(t) for t in trozos_a], f"leer A {et}")
        l2, i2 = leer([_parte_imagen(t) for t in trozos_b], f"leer B {et}")
        if not (l1 or l2):
            return {**fig, "descripcion_nueva": None, "motivo": "transcripción vacía"}

        disputa = valores_en_disputa(l1, l2)
        ilegible = sorted({x.strip() for x in (i1 + i2) if len(x.strip()) > 3})
        crudo = _normal(" ".join(l1 + l2))
        aviso = ""
        if disputa:
            aviso = ("\n\nVALORES QUE LAS DOS LECTURAS LEYERON DISTINTO, así que no se "
                     "pudieron leer con seguridad: " + ", ".join(disputa)
                     + "\nNo los pongas en textos_en_la_figura, ni en valores_nominales, "
                       "ni en el resumen. Listalos en dudas.")

        d = llm.complete_json(
            SISTEMA_DESCRIPCION,
            [_texto("FRAGMENTOS TRANSCRIPTOS (lectura A):\n" + "\n".join(l1)
                    + "\n\nFRAGMENTOS TRANSCRIPTOS (lectura B, otro encuadre):\n"
                    + "\n".join(l2)
                    + ("\n\nNO LEGIBLE:\n" + "\n".join(ilegible) if ilegible else "")
                    + aviso + "\n\nReconciliá y describí la figura."), _parte_imagen(im)],
            label=f"describir {et}")
        if not isinstance(d, dict) or not d.get("resumen"):
            return {**fig, "descripcion_nueva": None, "motivo": "descripción vacía"}

        # Dos filtros sobre lo que la reconciliación propuso. El primero saca lo
        # que las lecturas no acordaron; el segundo, lo que ninguna transcribió.
        descartado = []

        def admitido(v) -> bool:
            if magnitudes(str(v)) & set(disputa):
                descartado.append(f"en disputa: {v}")
                return False
            if no_transcripto(v, crudo):
                descartado.append(f"no transcripto: {v}")
                return False
            return True

        d["textos_en_la_figura"] = [t for t in (d.get("textos_en_la_figura") or [])
                                    if admitido(t)]
        d["valores_nominales"] = {k: v for k, v in (d.get("valores_nominales") or {}).items()
                                  if admitido(v)}

        # Una duda que repite algo que sí se leyó no es una duda: el texto del
        # chunk diría "No legible en la figura: Conexión Δ/Y" justo debajo de
        # "Textos en la figura: ... Conexión Δ/Y", y de las dos frases la que el
        # modelo de respuesta va a creer es la peor.
        leido = _normal(" ".join(d["textos_en_la_figura"]))
        d["dudas"] = sorted({x for x in
                             [str(y).strip() for y in (d.get("dudas") or [])] + ilegible
                             + [f"valor ilegible: {v}" for v in disputa]
                             if len(x) > 3 and _normal(x) not in leido})
        return {**fig, "descripcion_nueva": d, "mosaicos": len(trozos_a),
                "en_disputa": disputa, "descartado": descartado}

    resultados = [r for r in run_parallel(figuras, redescribir, max_workers=args.hilos,
                                          label="redescripción de figuras") if r]

    print("\n" + "=" * 78)
    cambiadas = 0
    for r in resultados:
        if not r.get("descripcion_nueva"):
            print(f"  ! {r['archivo'][:40]} p.{r['pagina']}: {r.get('motivo')}")
            continue
        n = r["descripcion_nueva"]
        vieja = [l[len("Valores nominales:"):] for l in r["descripcion_vieja"].split("\n")
                 if l.startswith("Valores nominales:")]
        nueva = json.dumps(n.get("valores_nominales") or {}, ensure_ascii=False)
        if (vieja[0].strip() if vieja else "{}") != nueva or r.get("en_disputa"):
            cambiadas += 1
            print(f"\n  {r['archivo'][:46]:<48} p.{r['pagina']}  ({r['mosaicos']} mosaicos)")
            print(f"      antes   : {(vieja[0].strip() if vieja else '(sin valores)')[:110]}")
            print(f"      ahora   : {nueva[:110]}")
            if r.get("en_disputa"):
                print(f"      ilegible: {', '.join(r['en_disputa'])}")
            for x in (r.get("descartado") or [])[:4]:
                print(f"      descartado {x[:90]}")

    print(f"\n  figuras releídas            : {len(resultados)}")
    print(f"  con valores distintos       : {cambiadas}")
    with open(SALIDA, "w", encoding="utf-8") as f:
        json.dump(resultados, f, ensure_ascii=False, indent=2)
    print(f"\nEscrito: {SALIDA}")
    print("Nada tocó el índice todavía. Para aplicarlo:")
    print(f"  python scripts/aplicar_planos_redescritos.py --entrada {SALIDA}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
