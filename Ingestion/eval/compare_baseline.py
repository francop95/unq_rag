"""
Compara el índice multimodal contra el índice solo texto (línea base).

Corre `run_eval.py` DOS veces sobre el mismo set de preguntas —una por índice—
y pone los números uno al lado del otro. No reimplementa nada del retrieval:
lanza el mismo evaluador que ya se usa, que a su vez consulta el pipeline real
de la API, y lo único que cambia entre las dos corridas es a qué colección de
Chroma apunta.

Uso:
    cd Ingestion
    python eval/compare_baseline.py
    python eval/compare_baseline.py --only-reviewed
    python eval/compare_baseline.py --set eval/eval_visual.jsonl

Qué mirar:

    "respuesta presente" es la métrica que vale para esta comparación. El
    recall@k se mide por (documento, página), y las dos ingestas trocean y
    paginan distinto, así que un mismo acierto puede contar diferente. Que el
    texto recuperado CONTENGA la respuesta no depende del troceado.
"""

import argparse
import os
import re
import subprocess
import sys

EVAL_DIR = os.path.dirname(os.path.abspath(__file__))
INGESTION_DIR = os.path.dirname(EVAL_DIR)

# Índice solo texto: mismos defaults que src/main_text_baseline.py
BASELINE_INDEX = os.environ.get("BASELINE_INDEX_NAME", "baseline_documents")
BASELINE_PATH = os.environ.get(
    "BASELINE_INDEX_PATH",
    os.path.join(INGESTION_DIR, "data", "chroma_index_baseline"),
)

PATRONES = {
    "recall@1": r"recall@1\s+\d+/\d+\s+=\s+([\d.]+)%",
    "recall@3": r"recall@3\s+\d+/\d+\s+=\s+([\d.]+)%",
    "recall@5": r"recall@5\s+\d+/\d+\s+=\s+([\d.]+)%",
    "recall@10": r"recall@10\s+\d+/\d+\s+=\s+([\d.]+)%",
    "MRR": r"MRR\s+([\d.]+)",
    "no llega nunca": r"no llega nunca:\s+(\d+)/\d+",
    "respuesta presente": r"RESPUESTA presente en el texto recuperado:\s+\d+/\d+\s+=\s+([\d.]+)%",
    "gate fuera de tema": r"gate fuera de tema:\s+(\d+)/\d+",
}


def correr(titulo: str, variant: str, args) -> tuple:
    """Lanza run_eval y devuelve (salida, métricas)."""
    cmd = [sys.executable, os.path.join(EVAL_DIR, "run_eval.py"), "--set", args.set]
    if args.only_reviewed:
        cmd.append("--only-reviewed")
    if variant:
        cmd += ["--variant", variant]

    print(f"\n{'='*66}")
    print(f"  {titulo}")
    print(f"{'='*66}")
    if variant:
        print(f"  variante: {variant}")
    print("  corriendo...", flush=True)

    proc = subprocess.run(cmd, cwd=INGESTION_DIR, capture_output=True, text=True)
    salida = proc.stdout + proc.stderr

    if proc.returncode != 0:
        print(f"  ❌ run_eval devolvió {proc.returncode}")
        print("\n".join("     " + l for l in salida.strip().splitlines()[-15:]))
        return salida, None

    metricas = {}
    for nombre, patron in PATRONES.items():
        m = re.search(patron, salida)
        metricas[nombre] = m.group(1) if m else None

    for linea in salida.splitlines():
        if linea.strip().startswith(("recall@", "MRR", "no llega", "RESPUESTA",
                                     "gate fuera", "chunks con media")):
            print("  " + linea.strip())

    return salida, metricas


def tabla(multi: dict, base: dict) -> None:
    print(f"\n\n{'='*66}")
    print("  COMPARACIÓN")
    print(f"{'='*66}")
    print(f"  {'métrica':<24} {'multimodal':>12} {'solo texto':>12} {'dif':>10}")
    print(f"  {'-'*24} {'-'*12} {'-'*12} {'-'*10}")

    for nombre in PATRONES:
        a, b = multi.get(nombre), base.get(nombre)
        if a is None and b is None:
            continue
        sa = "—" if a is None else a
        sb = "—" if b is None else b

        dif = ""
        if a is not None and b is not None:
            try:
                d = float(a) - float(b)
                # En "no llega nunca" y el gate, más alto no es mejor / son conteos
                dif = f"{d:+.1f}"
            except ValueError:
                dif = ""

        sufijo = "" if nombre in ("MRR", "no llega nunca", "gate fuera de tema") else "%"
        marca = "  ←" if nombre == "respuesta presente" else ""
        print(f"  {nombre:<24} {sa+sufijo:>12} {sb+sufijo:>12} {dif:>10}{marca}")

    print(f"\n  ← la métrica comparable entre dos ingestas distintas.")
    print("    El recall@k se mide por página y cada pipeline pagina distinto.")


def main():
    ap = argparse.ArgumentParser(description="Compara índice multimodal vs solo texto")
    ap.add_argument("--set", default=os.path.join(EVAL_DIR, "eval_set.jsonl"))
    ap.add_argument("--only-reviewed", action="store_true")
    ap.add_argument("--save", metavar="ARCHIVO",
                    help="guarda la salida completa de las dos corridas")
    args = ap.parse_args()

    if not os.path.isdir(BASELINE_PATH):
        print(f"❌ No existe el índice solo texto en:\n   {BASELINE_PATH}\n")
        print("   Construilo primero:")
        print("     cd Ingestion && python src/main_text_baseline.py")
        return 1

    out_multi, multi = correr("MULTIMODAL (índice actual)", "", args)
    out_base, base = correr(
        "SOLO TEXTO (línea base)",
        f"chroma_index_name={BASELINE_INDEX},chroma_path={BASELINE_PATH}",
        args,
    )

    if multi and base:
        tabla(multi, base)

    if args.save:
        with open(args.save, "w", encoding="utf-8") as f:
            f.write("===== MULTIMODAL =====\n" + out_multi)
            f.write("\n\n===== SOLO TEXTO =====\n" + out_base)
        print(f"\n  salida completa en: {args.save}")

    return 0 if (multi and base) else 1


if __name__ == "__main__":
    sys.exit(main())
