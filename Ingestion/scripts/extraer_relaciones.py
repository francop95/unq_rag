"""
Extrae relaciones entre componentes que no están en el inventario
=================================================================

Complementa a `construir_grafo.py`, que arma el grafo determinista desde las
tablas. Esta capa busca con un LLM las relaciones que solo existen en prosa o
en código.

Por qué acotada
---------------
La tentación es correrla sobre todo el corpus. Medido antes de hacerlo:

- Solo 35 de 2113 chunks mencionan dos o más códigos, y 20 son del propio
  Excel. Los códigos (QD01, VFD01) son una convención reciente del inventario;
  la tesis y los manuales dicen "el variador" y "los relés".
- Buscando por nombre en cambio dan 1118 chunks, pero dominados por falsos
  positivos: "medición de humedad" coincide con TH01/TH02 porque el alias
  contiene "humedad".

Extraer sobre 1118 chunks costaría varios dólares y produciría aristas
espurias. Y una arista equivocada en un tablero eléctrico es peor que una
faltante: manda a revisar el componente que no es.

Dónde sí paga: los programas de control. Tienen relaciones precisas y
ausentes del Excel —`write_register(0x4400, variador)`, el puerto serie del
Arduino hacia los servos— que son exactamente lo que un técnico necesita y
ninguna tabla registra. Los planos codificados y el póster también, porque
fueron hechos con la nomenclatura.

Las aristas extraídas se marcan con `origen_dato: "extraido"`, su confianza y
el chunk del que salieron. Nunca se mezclan con las documentadas: el prompt
las presenta como lo que son.

    python scripts/extraer_relaciones.py            # estima e informa
    python scripts/extraer_relaciones.py --escribir # extrae y fusiona
"""

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from config.config_reader import load_config  # noqa: E402
from task_utils.chunk_text import readable_chunk_text  # noqa: E402
from task_utils.llm_json import LLMJsonClient, run_parallel  # noqa: E402

RAIZ = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
GRAFO = os.path.abspath(os.path.join(RAIZ, "..", "API", "configs", "grafo.json"))

# Documentos donde buscar. Los manuales del fabricante quedan afuera a
# propósito: describen un producto genérico, no esta instalación, y "el
# variador" en el manual del PowerFlex no es una referencia a VFD01.
DOCUMENTOS = (
    "control_secadero_251124",
    "osciloscopio_secadero_251023_1",
    "servosyrele300924",
    "2_Planos_secadero_codificados_propuesta",
    "Poster_didactico_secadero_componentes_y_planos",
    "1_Plano distribucion electrica_v1",
    "conexionadoTben",
)

PROMPT = """Sos un ingeniero de mantenimiento documentando las conexiones de un
secadero de pastas industrial.

COMPONENTES DEL INVENTARIO (son los únicos válidos):
{componentes}

FRAGMENTO ({documento}):
{texto}

Extraé SOLO las relaciones físicas o de control que el fragmento afirme de
forma explícita entre dos componentes de la lista. Devolvé JSON:

{{"relaciones": [
  {{"origen": "<código>", "destino": "<código>", "tipo": "alimenta|controla|mide|comunica",
    "evidencia": "<la frase o línea exacta del fragmento>",
    "confianza": "alta|media|baja"}}
]}}

Reglas, en orden de importancia:
1. Si el fragmento no afirma ninguna relación, devolvé {{"relaciones": []}}.
   Es el caso más común y es una respuesta correcta.
2. Que dos componentes aparezcan en el mismo fragmento NO es una relación.
3. Solo códigos de la lista. Nada inventado, nada inferido de conocimiento
   general sobre cómo suelen conectarse estos equipos.
4. `evidencia` tiene que ser texto literal del fragmento. Si no podés citarlo,
   la relación no está ahí.
5. `confianza` baja si hay que interpretar; alta solo si el fragmento lo dice
   con todas las letras.
"""


def chunks_candidatos():
    salida = []
    for doc in DOCUMENTOS:
        base = os.path.join(RAIZ, "data", "chunks_data", doc)
        if not os.path.isdir(base):
            continue
        corridas = sorted(d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d)))
        if not corridas:
            continue
        for f in glob.glob(os.path.join(base, corridas[-1], "*", "*.json")):
            try:
                c = json.load(open(f, encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(c, dict):
                continue
            texto = readable_chunk_text(c)
            if not texto or len(texto) < 60:
                continue
            salida.append({"documento": doc, "chunk_id": c.get("chunk_id"),
                           "page_num": c.get("page_num"), "texto": texto[:4000]})
    return salida


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--escribir", action="store_true", help="extrae y fusiona en el grafo")
    ap.add_argument("--concurrencia", type=int, default=4)
    a = ap.parse_args()

    if not os.path.exists(GRAFO):
        print("  falta el grafo. Corré primero: python scripts/construir_grafo.py")
        return 1
    g = json.load(open(GRAFO, encoding="utf-8"))
    nodos = g["nodos"]
    listado = "\n".join(f"{c} = {n.get('nombre','')}" for c, n in nodos.items() if c)

    candidatos = chunks_candidatos()
    print(f"  {len(candidatos)} chunks candidatos en {len(DOCUMENTOS)} documentos")
    if not a.escribir:
        porc = {}
        for c in candidatos:
            porc[c["documento"]] = porc.get(c["documento"], 0) + 1
        for d, n in sorted(porc.items(), key=lambda x: -x[1]):
            print(f"    {n:>4}  {d[:52]}")
        print("\n  Nada extraído. Volvé a correr con --escribir.")
        return 0

    cfg = load_config(os.path.join(RAIZ, ".env"))
    from openai import OpenAI
    cliente = OpenAI(api_key=cfg.openai.openai_key)
    llm = LLMJsonClient(client=cliente, model=cfg.enrichment.enrichment_model,
                        temperature=0.0, etapa="extracción de relaciones")

    def worker(c):
        return llm.complete_json(
            system_prompt="Extraés relaciones entre componentes. Respondés JSON.",
            user_content=PROMPT.format(componentes=listado, documento=c["documento"],
                                       texto=c["texto"]),
            label=f"rel/{c['documento'][:14]}",
        )

    resultados = run_parallel(candidatos, worker, a.concurrencia, label="relaciones")

    nuevas, descartadas = [], 0
    validos = set(nodos)
    documentadas = {(e["origen"], e["destino"]) for e in g["aristas"]}
    for c, r in zip(candidatos, resultados):
        if not isinstance(r, dict):
            continue
        for rel in (r.get("relaciones") or []):
            o, d = str(rel.get("origen", "")).strip(), str(rel.get("destino", "")).strip()
            if o not in validos or d not in validos or o == d:
                descartadas += 1
                continue
            if (o, d) in documentadas:
                continue  # ya está, y la documentada manda
            if not str(rel.get("evidencia", "")).strip():
                descartadas += 1
                continue
            nuevas.append({
                "origen": o, "destino": d,
                "tipo": rel.get("tipo", "relacionado"),
                "funcion": rel.get("tipo", ""),
                "evidencia": str(rel.get("evidencia", ""))[:300],
                "confianza": rel.get("confianza", "baja"),
                "fuente": f"{c['documento']} p{c['page_num']} {c['chunk_id']}",
                "bidireccional": False,
                "origen_dato": "extraido",
            })

    # Una misma relación hallada en varios fragmentos se queda con la de mayor
    # confianza: repetirla no la hace más cierta, pero verla afirmada con todas
    # las letras en algún lado sí.
    orden = {"alta": 0, "media": 1, "baja": 2}
    por_par = {}
    for e in nuevas:
        k = (e["origen"], e["destino"])
        if k not in por_par or orden.get(e["confianza"], 3) < orden.get(por_par[k]["confianza"], 3):
            por_par[k] = e
    nuevas = list(por_par.values())

    g["aristas"] = [e for e in g["aristas"] if e.get("origen_dato") != "extraido"] + nuevas
    with open(GRAFO, "w", encoding="utf-8") as fh:
        json.dump(g, fh, ensure_ascii=False, indent=2)

    print(f"\n  {len(nuevas)} relaciones nuevas · {descartadas} descartadas por inválidas")
    for e in sorted(nuevas, key=lambda x: orden.get(x["confianza"], 3))[:14]:
        print(f"    [{e['confianza']:<5}] {e['origen']:>7} → {e['destino']:<8} {e['tipo']:<9} "
              f"«{e['evidencia'][:54]}»")
    return 0


if __name__ == "__main__":
    sys.exit(main())
