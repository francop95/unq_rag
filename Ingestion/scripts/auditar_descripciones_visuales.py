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

Coste
-----
Una llamada con imagen por figura. Sobre las ~307 figuras del índice son unos
pocos dólares. `--limite` sirve para probar con unas pocas antes de largar todo.

    python scripts/auditar_descripciones_visuales.py --limite 10
    python scripts/auditar_descripciones_visuales.py
"""

import argparse
import json
import os
import sys
from collections import Counter

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "src"))
sys.path.insert(0, os.path.abspath(os.path.join(RAIZ, "..", "API")))

from PIL import Image  # noqa: E402

SALIDA = os.path.join(RAIZ, "data", "auditoria_descripciones.json")

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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limite", type=int, default=0, help="auditar solo N figuras")
    ap.add_argument("--hilos", type=int, default=8)
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
                        max_output_tokens=600, etapa="auditoria_figuras")
    print(f"Modelo: {modelo}\n")

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
