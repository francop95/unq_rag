"""
Audita si la descripción de cada figura coincide con la figura
==============================================================

Por qué existe
--------------
El filtro de recortes en blanco (`purgar_visuales_en_blanco.py`) resuelve el
caso en que el modelo describe papel vacío. Pero ese caso apareció de casualidad,
por una pregunta suelta sobre el motor, y no hay ninguna razón para suponer que
la invención se limite a los recortes vacíos: un recorte CON contenido también
puede recibir una descripción equivocada, y ahí el filtro de blancos no llega.

Esto lo mide en vez de suponerlo. Por cada figura indexada manda al modelo la
imagen junto con la descripción que está guardada y le pregunta si lo que la
descripción afirma se ve en la imagen.

Qué cuenta como invención
-------------------------
Solo lo verificable: componentes, etiquetas, valores y conexiones que la
descripción nombra y la imagen no muestra. NO se penaliza que la descripción sea
breve, que omita cosas, ni que agregue contexto del documento —un pie de figura,
el número de capítulo— que no esté dibujado. El objetivo es encontrar evidencia
falsa, no redacciones imperfectas.

Se audita con la imagen a la vista, que es justamente lo que no tuvo el modelo
que escribió la descripción cuando el recorte salió mal.

El punto ciego de este modo, y el modo --valores
------------------------------------------------
"¿La descripción corresponde a esta figura?" es una pregunta sobre el tema, no
sobre los números, y un auditor que mira la misma imagen con los mismos ojos
avala los mismos errores. La chapa del motor de la tesis (pág. 16) pasó por acá
con veredicto `coincide` y el comentario "especificaciones técnicas claramente
legibles", teniendo guardado `Conexión A/Y` donde el original dice `Conexión
Δ/Y`, y `V 230/380` donde dice `V 220/380`. El tema estaba bien; los valores no,
y con ellos el asistente respondió que la placa confirmaba una conexión que la
placa nunca afirmó.

`--valores` audita otra cosa: toma las magnitudes que la descripción declara y
las coteja contra una TRANSCRIPCIÓN estricta de la misma figura, troceada para
que el texto le llegue al modelo con suficientes píxeles. No pregunta si la
descripción viene al caso: pregunta si cada número está realmente escrito ahí.

Coste
-----
Una llamada con imagen por figura. Sobre las ~307 figuras del índice son unos
pocos dólares. `--limite` sirve para probar con unas pocas antes de largar todo.

    python scripts/auditar_descripciones_visuales.py --limite 10
    python scripts/auditar_descripciones_visuales.py
    python scripts/auditar_descripciones_visuales.py --valores
"""

import argparse
import json
import os
import sys
from collections import Counter

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "src"))
sys.path.insert(0, os.path.abspath(os.path.join(RAIZ, "..", "API")))

from PIL import Image  # noqa: E402

# El troceado y el prompt de transcripción se comparten con el script que
# reescribe: si el auditor leyera distinto del que corrige, sus hallazgos no
# serían comprobables con la herramienta que los tiene que arreglar.
from redescribir_figuras_con_valores import (  # noqa: E402
    NUMERO, SISTEMA_TRANSCRIPCION, _parte_imagen, _normal, magnitudes, mosaicos,
)

SALIDA = os.path.join(RAIZ, "data", "auditoria_descripciones.json")
SALIDA_VALORES = os.path.join(RAIZ, "data", "auditoria_valores.json")

UMBRAL_PAPEL = 244
MINIMO_TINTA = 0.02

SISTEMA = """Sos un auditor técnico. Recibís UNA imagen extraída de un documento
de ingeniería y la DESCRIPCIÓN que un sistema automático guardó sobre ella.

Tu única tarea es decidir si lo que la descripción AFIRMA se ve en la imagen.

Criterios:
- Juzgá solo afirmaciones verificables mirando la imagen: componentes, etiquetas,
  códigos, valores numéricos, unidades y conexiones concretas.
- NO penalices que la descripción sea breve, que omita detalles, ni que agregue
  contexto del documento que no esté dibujado (título, capítulo, pie de figura).
- Una afirmación es inventada si nombra algo concreto que la imagen no muestra.
  Ejemplo típico: la descripción dice "motor M1, contactor K1, relé térmico F1,
  380V" y la imagen es una captura de pantalla de un software.

Respondé SOLO JSON:
{"veredicto": "coincide" | "parcial" | "no_coincide",
 "inventado": ["afirmación concreta que no se ve", ...],
 "que_es": "en pocas palabras, qué muestra realmente la imagen"}

"parcial" = lo esencial coincide pero hay algún detalle inventado.
"no_coincide" = la descripción habla de otra cosa."""


def _en_blanco(ruta: str) -> bool:
    im = Image.open(ruta).convert("L").resize((80, 80))
    return sum(1 for p in im.getdata() if p < UMBRAL_PAPEL) / 6400.0 < MINIMO_TINTA


def _resolver(media_path: str):
    if not media_path or not media_path.lower().endswith(".png"):
        return None
    for cand in (media_path, os.path.join(RAIZ, media_path)):
        if os.path.isabs(cand) and os.path.exists(cand):
            return cand
        if os.path.exists(cand):
            return cand
    if "media/" in media_path:
        p = os.path.join(RAIZ, "data", "media", media_path.split("media/", 1)[-1])
        if os.path.exists(p):
            return p
    return None


def figuras_del_indice():
    """Un registro por figura indexada: (id, archivo, página, ruta, descripción)."""
    from configs.Configuration import Configuration as C
    import chromadb

    cliente = chromadb.PersistentClient(path=C.chroma_local_path)
    col = cliente.get_collection(C.AppSettings__ChromaIndex)
    total, off = col.count(), 0
    salida, vacias = [], 0
    while off < total:
        lote = col.get(limit=500, offset=off, include=["documents", "metadatas"])
        ids = lote.get("ids") or []
        if not ids:
            break
        for cid, doc, meta in zip(ids, lote["documents"], lote["metadatas"]):
            meta = meta or {}
            if meta.get("question"):
                continue            # las preguntas heredan la descripción del padre
            ruta = _resolver(meta.get("media_path") or "")
            if not ruta:
                continue
            if _en_blanco(ruta):
                vacias += 1          # las cubre purgar_visuales_en_blanco.py
                continue
            salida.append({
                "id": cid,
                "archivo": meta.get("file_name"),
                "pagina": meta.get("page_num"),
                "ruta": ruta,
                "descripcion": (doc or "").strip(),
            })
        off += len(ids)
    return salida, vacias


def declaradas(descripcion: str) -> set:
    """Magnitudes que la descripción guardada afirma leer en la figura."""
    lineas = [l for l in (descripcion or "").split("\n")
              if l.startswith(("Textos en la figura:", "Valores nominales:"))]
    return magnitudes(" ".join(lineas))


def auditar_valores(figuras, llm, hilos: int) -> int:
    """
    Coteja cada magnitud declarada contra una transcripción estricta.

    No pregunta si la descripción corresponde a la figura: eso es el otro modo,
    y es justamente lo que no alcanzó. Pregunta si el número está escrito ahí.
    """
    from task_utils.llm_json import run_parallel

    candidatas = [f for f in figuras if declaradas(f["descripcion"])]
    print(f"Figuras que declaran magnitudes: {len(candidatas)} de {len(figuras)}\n")
    if not candidatas:
        return 0

    def revisar(fig):
        im = Image.open(fig["ruta"]).convert("RGB")
        leido = []
        trozos = mosaicos(im)
        for i, trozo in enumerate(trozos, 1):
            t = llm.complete_json(
                SISTEMA_TRANSCRIPCION,
                [{"type": "text", "text": f"Fragmento {i} de {len(trozos)} de la figura. "
                  "Transcribí el texto que se ve en ESTE fragmento."},
                 _parte_imagen(trozo)],
                label=f"transcribir {fig['archivo'][:26]} p.{fig['pagina']} frag.{i}")
            leido += [str(x) for x in ((t or {}).get("lineas") or [])]
        crudo = _normal(" ".join(leido))

        # Se coteja número por número y no el par número+unidad: el orden de los
        # dos no es estable (`V 220/380` escribe la unidad adelante) y buscar
        # "380V" daba falsos positivos sobre valores bien leídos.
        sin_respaldo = sorted(
            m for m in declaradas(fig["descripcion"])
            if any(n.replace(",", ".") not in crudo for n in NUMERO.findall(m)))
        return {**fig, "veredicto": "valores_sospechosos" if sin_respaldo else "valores_ok",
                "sin_respaldo": sin_respaldo, "transcripcion": leido}

    resultados = [r for r in run_parallel(candidatas, revisar, max_workers=hilos,
                                          label="auditoría de valores") if r]
    malas = [r for r in resultados if r["sin_respaldo"]]

    print("\n" + "=" * 70)
    print(f"  valores_ok           {len(resultados) - len(malas):>4}")
    print(f"  valores_sospechosos  {len(malas):>4}")
    for r in sorted(malas, key=lambda r: -len(r["sin_respaldo"])):
        print(f"\n  {str(r['archivo'])[:46]:<48} p.{r['pagina']}")
        print(f"      declara y no se transcribe: {', '.join(r['sin_respaldo'][:8])}")

    with open(SALIDA_VALORES, "w", encoding="utf-8") as f:
        json.dump(resultados, f, ensure_ascii=False, indent=2)
    print(f"\nInforme completo: {SALIDA_VALORES}")
    if malas:
        print("Para corregirlas:  python scripts/redescribir_figuras_con_valores.py")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limite", type=int, default=0, help="auditar solo N figuras")
    ap.add_argument("--hilos", type=int, default=8)
    ap.add_argument("--valores", action="store_true",
                    help="cotejar las magnitudes declaradas contra una transcripción estricta")
    # Conviene auditar con un modelo DISTINTO del que escribió las descripciones:
    # pedirle al mismo que revise su propio trabajo tiende a confirmarlo.
    ap.add_argument("--modelo", default=None, help="por defecto, el multimodal_model de la config")
    args = ap.parse_args()

    figuras, vacias = figuras_del_indice()
    if args.limite:
        figuras = figuras[: args.limite]
    print(f"Figuras a auditar: {len(figuras)}"
          + (f"   ({vacias} en blanco, las cubre el otro script)" if vacias else ""))
    if not figuras:
        return 0

    from openai import OpenAI
    from task_utils.llm_json import (
        LLMJsonClient, image_content_part, text_content_part, run_parallel,
    )
    from config.config_reader import ConfigReader

    cfg = ConfigReader()
    modelo = args.modelo or cfg.get("multimodal_model", "gpt-4o")
    llm = LLMJsonClient(client=OpenAI(), model=modelo, temperature=0.0,
                        max_output_tokens=4000 if args.valores else 600,
                        etapa="auditoria_valores" if args.valores else "auditoria_figuras")
    print(f"Modelo: {modelo}\n")

    if args.valores:
        return auditar_valores(figuras, llm, args.hilos)

    def auditar(fig):
        parte = image_content_part(fig["ruta"])
        if not parte:
            return {**fig, "veredicto": "sin_imagen"}
        contenido = [
            text_content_part(
                "DESCRIPCIÓN GUARDADA:\n" + fig["descripcion"][:4000]
                + "\n\n¿Lo que afirma se ve en la imagen?"),
            parte,
        ]
        r = llm.complete_json(SISTEMA, contenido, label=f"audit {fig['archivo']}")
        if not isinstance(r, dict):
            return {**fig, "veredicto": "error"}
        return {**fig, "veredicto": r.get("veredicto", "error"),
                "inventado": r.get("inventado") or [], "que_es": r.get("que_es", "")}

    resultados = run_parallel(figuras, auditar, max_workers=args.hilos,
                              label="auditoría de figuras")
    resultados = [r for r in resultados if r]

    conteo = Counter(r["veredicto"] for r in resultados)
    print("\n" + "=" * 70)
    for v in ("coincide", "parcial", "no_coincide", "sin_imagen", "error"):
        if conteo.get(v):
            print(f"  {v:<12} {conteo[v]:>4}")

    sospechosas = [r for r in resultados if r["veredicto"] in ("no_coincide", "parcial")]
    sospechosas.sort(key=lambda r: (r["veredicto"] != "no_coincide", str(r["archivo"])))
    for r in sospechosas:
        print(f"\n  [{r['veredicto']}] {r['archivo']} pág.{r['pagina']}")
        print(f"      imagen muestra: {r.get('que_es','')}")
        for inv in (r.get("inventado") or [])[:6]:
            print(f"      inventado: {inv}")

    with open(SALIDA, "w", encoding="utf-8") as f:
        json.dump(resultados, f, ensure_ascii=False, indent=2)
    print(f"\nInforme completo: {SALIDA}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
