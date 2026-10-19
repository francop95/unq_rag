"""
Registros consultables del inventario
=====================================

El inventario entra al índice como chunks de tabla en Markdown. Eso alcanza para
preguntas semánticas ("¿qué protege el motor?") y no alcanza para las exactas:
"¿cuál es la corriente nominal de QF01?" se termina respondiendo por similitud
contra un bloque de texto, cuando el dato es una celda.

Esto vuelca cada fila del inventario a un registro con clave, para que la API
pueda hacer una búsqueda exacta por código además de la semántica. El mismo dato
queda disponible por dos vías, que es la idea: el índice demuestra qué dice el
documento, el registro permite ir a buscarlo sin depender del embedding.

No se usa SQLite ni Postgres a propósito. Son 48 filas: un diccionario en
memoria da la misma propiedad —lectura exacta, sin similitud— sin agregar un
archivo que desplegar ni una dependencia. Si el inventario creciera a miles de
filas o hiciera falta filtrar por rangos, ahí sí conviene una base.

    python scripts/generar_inventario.py
"""

import json
import os
import re
import sys

import openpyxl

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXCEL = os.path.join(RAIZ, "data", "raw_data",
                     "Inventario_secadero_revision_Pablo_v2.xlsx")
SALIDA = os.path.abspath(os.path.join(RAIZ, "..", "API", "configs", "inventario.json"))

# Las dos hojas que importan y la columna por la que se unen.
HOJA_INVENTARIO = "Inventario"
HOJA_NOMENCLATURA = "Nomenclatura"


def _texto(valor) -> str:
    return re.sub(r"\s+", " ", str(valor)).strip() if valor is not None else ""


def _filas_con_encabezado(ws, encabezado_esperado: str):
    """Las filas de la hoja, ya como diccionarios, desde su fila de encabezado.

    La fila de encabezado no es la primera: estas hojas abren con un título y una
    nota. Se la busca por una columna que tiene que estar, en vez de asumir una
    posición fija, para que agregar una línea de nota arriba no rompa todo.
    """
    filas = [[_texto(c.value) for c in f] for f in ws.iter_rows()]
    pos = next((i for i, f in enumerate(filas)
                if any(c == encabezado_esperado for c in f)), None)
    if pos is None:
        raise SystemExit(f"No encontré el encabezado {encabezado_esperado!r} en {ws.title!r}")
    cols = filas[pos]
    for f in filas[pos + 1:]:
        if not any(f):
            continue
        yield {c: v for c, v in zip(cols, f) if c}


def construir() -> dict:
    wb = openpyxl.load_workbook(EXCEL, data_only=True)

    # ID de inventario -> código acordado. La nomenclatura es la que manda:
    # un registro sin código no tiene cómo ser nombrado en una respuesta.
    codigo_de = {}
    for fila in _filas_con_encabezado(wb[HOJA_NOMENCLATURA], "Código"):
        codigo, id_inv = fila.get("Código"), fila.get("ID de inventario")
        if not codigo:
            continue
        # Un código puede cubrir varios registros ("I009–I011") y un registro
        # puede caer en varios códigos. Se expanden los dos lados.
        for pieza in re.split(r"[,;/]| y ", id_inv or ""):
            pieza = pieza.strip()
            if not pieza:
                continue
            m = re.match(r"^I(\d+)\s*[–\-]\s*I?(\d+)$", pieza)
            if m:
                for n in range(int(m.group(1)), int(m.group(2)) + 1):
                    codigo_de.setdefault(f"I{n:03d}", []).append(codigo)
            else:
                codigo_de.setdefault(pieza, []).append(codigo)

    registros, sin_codigo = [], 0
    for fila in _filas_con_encabezado(wb[HOJA_INVENTARIO], "ID de registro"):
        id_reg = fila.get("ID de registro")
        if not id_reg or not re.match(r"^I\d+$", id_reg):
            continue
        codigos = sorted(set(codigo_de.get(id_reg, [])))
        sin_codigo += 0 if codigos else 1
        registros.append({
            "id_registro": id_reg,
            "codigos": codigos,
            "sistema": fila.get("Sistema", ""),
            "componente": fila.get("Componente / conjunto", ""),
            "cantidad": fila.get("Cantidad documental", ""),
            "referencia_original": fila.get("Referencia original", ""),
            # La columna que motiva todo esto: corrientes, tensiones, modelos.
            "datos": fila.get("Datos documentados", ""),
            "funcion": fila.get("Función", ""),
            "ubicacion": fila.get("Ubicación documentada", ""),
        })

    # Índice por código, que es como lo va a consultar la API.
    por_codigo = {}
    for r in registros:
        for c in r["codigos"]:
            por_codigo.setdefault(c, []).append(r["id_registro"])

    return {
        "registros": registros,
        "por_codigo": por_codigo,
        "_resumen": {
            "filas": len(registros),
            "con_codigo": len(registros) - sin_codigo,
            "sin_codigo": sin_codigo,
            "codigos": len(por_codigo),
            "con_datos": sum(1 for r in registros if r["datos"]),
        },
    }


if __name__ == "__main__":
    if not os.path.exists(EXCEL):
        sys.exit(f"No está el inventario: {EXCEL}")
    datos = construir()
    os.makedirs(os.path.dirname(SALIDA), exist_ok=True)
    with open(SALIDA, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)
    r = datos["_resumen"]
    print(f"{SALIDA}")
    print(f"  {r['filas']} registros | {r['con_codigo']} con código | "
          f"{r['sin_codigo']} sin código | {r['codigos']} códigos | "
          f"{r['con_datos']} con datos documentados")
