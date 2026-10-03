"""
Construye el grafo de componentes del secadero
==============================================

Nodos: los componentes del inventario, con su código acordado, su sistema, su
función y su ubicación. Aristas: las conexiones documentadas.

Determinista, desde las tablas
------------------------------
No hay extracción con LLM en esta capa, y es a propósito. La hoja
`Conexiones TBEN` ya expresa las aristas de forma explícita —`I020 / PS01 →
I018 / IO01`— revisadas por una persona. Extraerlas de nuevo desde la prosa
introduciría errores en datos que ya están correctos, y una arista equivocada
en un tablero eléctrico es peor que una arista faltante: manda a revisar el
componente que no es.

La capa de extracción sobre los documentos vive aparte
(`extraer_relaciones.py`) y sus aristas quedan marcadas con su origen y su
confianza, para que nunca se confundan con las documentadas.

Por qué hace falta
------------------
El sistema responde bien las relaciones de un salto, porque cada fila de la
tabla es una arista y el retrieval la encuentra. Falla en la agregación: a
"listá los componentes que dependen de PS01" respondió con las protecciones de
aguas arriba, confundiendo "aparece en el mismo plano" con "depende de". Suena
plausible y está mal, que es la peor combinación.

    python scripts/construir_grafo.py
"""

import json
import os
import re
import sys

import openpyxl

RAIZ = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
EXCEL = os.path.join(RAIZ, "data", "raw_data", "Inventario_secadero_revision_Pablo_v2.xlsx")
SALIDA = os.path.abspath(os.path.join(RAIZ, "..", "API", "configs", "grafo.json"))

# "I009–I011 / SSR01–SSR03" nombra tres componentes, no uno. Los rangos se
# expanden: si no, una consulta por SSR02 no encuentra la arista que lo incluye.
RANGO = re.compile(r"^([A-Z]+)(\d+)\s*[–\-]\s*(?:[A-Z]+)?(\d+)$")


def expandir_rango(token: str):
    """'SSR01–SSR03' → ['SSR01','SSR02','SSR03']. Un token suelto se devuelve igual."""
    m = RANGO.match(token.strip())
    if not m:
        return [token.strip()]
    prefijo, desde, hasta = m.group(1), int(m.group(2)), int(m.group(3))
    if hasta < desde or hasta - desde > 50:
        return [token.strip()]
    ancho = len(m.group(2))
    return [f"{prefijo}{str(n).zfill(ancho)}" for n in range(desde, hasta + 1)]


def partir_nodo(texto: str):
    """
    'I020 / PS01' → (['I020'], ['PS01']). Devuelve (ids_inventario, codigos).

    Las anotaciones entre paréntesis —'I044 (previsto)'— se conservan aparte:
    un componente previsto pero no instalado no es lo mismo que uno existente, y
    perder esa distinción haría que el grafo afirme conexiones que no existen.
    """
    texto = (texto or "").strip()
    nota = ""
    m = re.search(r"\(([^)]*)\)", texto)
    if m:
        nota = m.group(1).strip()
        texto = texto[: m.start()].strip()

    partes = [p.strip() for p in texto.split("/") if p.strip()]
    ids, codigos = [], []
    for p in partes:
        for t in expandir_rango(p):
            if re.match(r"^I\d+$", t):
                ids.append(t)
            elif t:
                codigos.append(t)
    return ids, codigos, nota


def leer_hoja(wb, nombre):
    ws = wb[nombre]
    filas = [[("" if c is None else str(c).strip()) for c in r]
             for r in ws.iter_rows(values_only=True)]
    filas = [f for f in filas if any(f)]
    return filas[3:] if len(filas) > 3 else []


def main() -> int:
    if not os.path.exists(EXCEL):
        print(f"  no está el inventario: {EXCEL}")
        return 1
    wb = openpyxl.load_workbook(EXCEL, data_only=True)

    # ---------------- nodos: el inventario ----------------
    nodos = {}
    for f in leer_hoja(wb, "Inventario"):
        def col(i):
            return f[i].strip() if len(f) > i and f[i] else ""
        id_inv, sistema, nombre = col(0), col(1), col(2)
        if not id_inv:
            continue
        codigo = col(10)
        clave = codigo or id_inv
        nodos[clave] = {
            "codigo": codigo, "id_inventario": id_inv, "nombre": nombre,
            "sistema": sistema, "datos": col(5), "funcion": col(6),
            "ubicacion": col(7), "fuente": col(8),
        }

    # alias desde la nomenclatura
    for f in leer_hoja(wb, "Nomenclatura"):
        codigo = f[0].strip() if f and f[0] else ""
        if codigo in nodos:
            nodos[codigo]["alias"] = f[3].strip() if len(f) > 3 else ""
            nodos[codigo]["en_plano"] = f[4].strip() if len(f) > 4 else ""

    # ---------------- aristas: las conexiones ----------------
    aristas = []
    for f in leer_hoja(wb, "Conexiones TBEN"):
        puerto = f[0].strip() if f and f[0] else ""
        funcion = f[2].strip() if len(f) > 2 else ""
        registros = f[3].strip() if len(f) > 3 else ""
        fuente = f[4].strip() if len(f) > 4 else ""
        pendiente = f[6].strip() if len(f) > 6 else ""
        if not registros:
            continue

        bidireccional = "↔" in registros
        tramos = [t for t in re.split(r"→|↔", registros) if t.strip()]
        if len(tramos) < 2:
            continue  # un puerto sin destino no es una arista

        # Una cadena A → B → C son dos aristas, no una.
        for i in range(len(tramos) - 1):
            _, orig_cods, nota_o = partir_nodo(tramos[i])
            _, dest_cods, nota_d = partir_nodo(tramos[i + 1])
            for o in (orig_cods or ["?"]):
                for d in (dest_cods or ["?"]):
                    if o == "?" or d == "?":
                        continue
                    aristas.append({
                        "origen": o, "destino": d,
                        "tipo": "conecta_con" if bidireccional else "alimenta_o_controla",
                        "puerto": puerto, "funcion": funcion, "fuente": fuente,
                        "bidireccional": bidireccional,
                        "verificacion_pendiente": pendiente,
                        "nota": " ".join(x for x in (nota_o, nota_d) if x),
                        "origen_dato": "inventario",
                    })

    # ---------------- discrepancias: qué está en duda ----------------
    discrepancias = []
    for f in leer_hoja(wb, "Discrepancias"):
        def col(i):
            return f[i].strip() if len(f) > i and f[i] else ""
        if not col(0):
            continue
        codigos = []
        for t in re.split(r"[;,]", col(3)):
            _, cods, _ = partir_nodo(t)
            codigos.extend(cods)
        discrepancias.append({
            "id": col(0), "prioridad": col(1), "registros": col(2),
            "componentes": codigos, "tema": col(4),
            "pregunta": col(6), "estado": col(8),
        })

    grafo = {"nodos": nodos, "aristas": aristas, "discrepancias": discrepancias}
    os.makedirs(os.path.dirname(SALIDA), exist_ok=True)
    with open(SALIDA, "w", encoding="utf-8") as fh:
        json.dump(grafo, fh, ensure_ascii=False, indent=2)

    print(f"  {len(nodos)} nodos · {len(aristas)} aristas · "
          f"{len(discrepancias)} discrepancias → {SALIDA}\n")
    print("  aristas documentadas:")
    for a in aristas:
        flecha = "↔" if a["bidireccional"] else "→"
        print(f"    {a['origen']:>7} {flecha} {a['destino']:<9} [{a['puerto']}] {a['funcion'][:38]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
