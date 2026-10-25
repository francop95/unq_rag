"""
Propone las conexiones de un plano para que una persona las corrija
===================================================================

Qué produce
-----------
Un Excel con una fila por conexión propuesta, en el MISMO formato de columnas
que la hoja `Conexiones TBEN` del inventario, para pegarlo ahí una vez
revisado. La columna "Conexión confirmada" queda vacía a propósito: la llena
quien revisa, y `construir_grafo.py` solo toma las filas confirmadas.

    python scripts/proponer_conexiones.py --plano 2_Planos --pagina 1
    python scripts/proponer_conexiones.py --plano 2_Planos --pagina 1 --limite 5

Por qué un borrador y no una respuesta
--------------------------------------
Esto NO sabe leer un plano. Se midió sobre el plano codificado, preguntando
en las dos direcciones y quedándose solo con lo que ambas confirmaban:

    de 7 aristas "confirmadas por las dos", 2 estaban mal
    —entre ellas QF03 → IF01, que mete la interfaz de control en la línea de
    potencia del motor— y 14 aristas CORRECTAS quedaron fuera.

O sea que el acuerdo entre direcciones resultó anticorrelacionado con la
verdad en el caso que importaba. Por eso acá el acuerdo **no filtra nada**:
las dos lecturas se muestran como evidencia y todas las filas llegan sin
confirmar. El valor de esta herramienta es que nadie escriba trece filas
desde cero, no que acierte.

Dos errores que comete siempre, y conviene buscar primero al revisar:

1. **Líneas de llamada leídas como conductores.** El rótulo de color unido a
   su símbolo por una línea punteada se lee como un cable. Así nace
   `QF03 → IF01`.
2. **Barras colectoras no seguidas.** Usa adyacencia física en vez de
   conexión: dice que QF03 cuelga de QF02 porque está al lado, cuando los dos
   cuelgan de la barra de QF01.

Qué sí hace bien
----------------
Nombrar los componentes y no inventar códigos: el catálogo sale de la hoja
`Nomenclatura`, así que lo que el modelo diga se normaliza contra códigos que
existen. Lo que no se pueda normalizar se reporta aparte en vez de colarse.

Y coteja contra lo ya declarado: una propuesta que contradice una conexión
revisada por una persona sale marcada como conflicto, no como novedad.
"""

import argparse
import base64
import io
import json
import os
import re
import sys
from collections import defaultdict

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "src"))

import openpyxl  # noqa: E402

EXCEL = os.path.join(RAIZ, "data", "raw_data",
                     "Inventario_secadero_revision_Pablo_v2.xlsx")
SALIDA = os.path.join(RAIZ, "data", "conexiones_propuestas.xlsx")

# Las mismas diez columnas que `Conexiones TBEN`, para poder pegar las filas
# ahí sin traducir nada. `construir_grafo.py` lee la 4ª (índice 3).
COLUMNAS = [
    "Puerto", "Origen → destino", "Función documentada", "Registros relacionados",
    "Fuente", "Detalle documental", "Verificación pendiente", "Discrepancias",
    "Conexión confirmada", "Observaciones",
]


# ------------------------------------------------------- el catálogo de códigos

def leer_hoja(wb, nombre):
    """Las filas con datos de una hoja. Las tres primeras son títulos."""
    filas = [[("" if c is None else str(c).strip()) for c in f]
             for f in wb[nombre].iter_rows(values_only=True)]
    filas = [f for f in filas if any(f)]
    return filas[3:] if len(filas) > 3 else []


def cargar_catalogo(wb):
    """`{código: {id, nombre, alias}}` desde la hoja Nomenclatura."""
    catalogo = {}
    for f in leer_hoja(wb, "Nomenclatura"):
        codigo = (f[0] if f else "").strip().rstrip("…").strip()
        if not codigo:
            continue
        catalogo[codigo] = {
            "id": (f[1] if len(f) > 1 else "").strip(),
            "nombre": (f[2] if len(f) > 2 else "").strip(),
            "alias": (f[3] if len(f) > 3 else "").strip(),
        }
    return catalogo


def _claves(texto):
    """Palabras significativas de un texto, para emparejar por alias.

    Los números cortos se conservan aunque midan un carácter: en "Servo 1" /
    "Servomotor 1" el `1` es lo ÚNICO que distingue SV01 de SV02.
    """
    palabras = re.findall(r"[a-záéíóúñ0-9]+", texto.lower())
    return {p for p in palabras if len(p) > 3 or any(c.isdigit() for c in p)}


def _coinciden(a, b):
    """Dos palabras que son la misma raíz. "servo" y "servomotor" lo son."""
    if a == b:
        return True
    corta, larga = (a, b) if len(a) <= len(b) else (b, a)
    return len(corta) >= 4 and larga.startswith(corta)


def _comunes(claves, palabras):
    return {c for c in claves if any(_coinciden(c, p) for p in palabras)}


def _frecuencia_de(clave, frecuencia):
    """En cuántos componentes aparece esa raíz."""
    return sum(n for p, n in frecuencia.items() if _coinciden(clave, p)) or 99


def normalizar(texto, catalogo):
    """Lleva lo que dijo el modelo a un código del catálogo, o None.

    Hace falta porque el modelo nombra el mismo componente de dos formas
    distintas según desde dónde se le pregunte —"PS01" en una dirección y
    "Fuente de alimentación 220 VCA a 24 VCC/10A" en la otra— y sin
    normalizar quedan como dos aristas que no se encuentran entre sí.

    Devuelve None en vez de adivinar: lo que no se reconoce se reporta.
    """
    t = (texto or "").strip()
    if not t:
        return None

    # 1. El código, tal cual o adentro del texto.
    for codigo in sorted(catalogo, key=len, reverse=True):
        if re.search(rf"\b{re.escape(codigo)}\b", t, re.IGNORECASE):
            return codigo

    # 2. Por nombre técnico o alias. No alcanza con una palabra en común:
    #    "interruptor" la comparten los cinco interruptores. Pero exigir dos
    #    deja afuera los nombres cortos —"Arduino Uno", "TBEN-L4-8IOL"— que
    #    el modelo usa todo el tiempo. Así que una sola palabra vale si es
    #    DISTINTIVA: si no la comparten varios componentes del catálogo.
    claves_t = _claves(t)
    frecuencia = {}
    por_codigo = {}
    for codigo, d in catalogo.items():
        palabras = _claves(d["nombre"]) | _claves(d["alias"])
        por_codigo[codigo] = palabras
        for palabra in palabras:
            frecuencia[palabra] = frecuencia.get(palabra, 0) + 1

    # El nombre técnico pesa más que el alias. Si no, "Resistencia
    # calefactora 2" empata con R01, cuyo alias "Grupo 3 x 2 kW" aporta un 2
    # que no identifica nada.
    puntajes = {}
    for codigo, d in catalogo.items():
        del_nombre = _comunes(claves_t, _claves(d["nombre"]))
        comunes = _comunes(claves_t, por_codigo[codigo])
        if comunes:
            puntajes[codigo] = (len(comunes), len(del_nombre),
                                any(_frecuencia_de(p, frecuencia) <= 2
                                    for p in comunes))
    if not puntajes:
        return None

    mejor = max(puntajes, key=lambda c: puntajes[c])
    cuantas, _, distintiva = puntajes[mejor]
    if cuantas >= 2:
        return mejor
    # Con una sola palabra, además de distintiva tiene que ser la única que
    # llega a ese puntaje: si empata con otro componente, no se adivina.
    empates = [c for c, v in puntajes.items() if v == puntajes[mejor]]
    return mejor if distintiva and len(empates) == 1 else None


# --------------------------------------------------- lo que ya está declarado

def aristas_declaradas(wb, hojas=("Conexiones TBEN",)):
    """`{(origen, destino): puerto}` de lo que una persona ya revisó.

    Se parsea igual que en `construir_grafo.py`: la columna "Registros
    relacionados", partida por las flechas, y una cadena A → B → C cuenta
    como dos aristas.
    """
    declaradas = {}
    for hoja in hojas:
        if hoja not in wb.sheetnames:
            continue
        for f in leer_hoja(wb, hoja):
            registros = f[3] if len(f) > 3 else ""
            if not registros:
                continue
            tramos = [t.strip() for t in re.split(r"→|↔", registros) if t.strip()]
            codigos = []
            for tramo in tramos:
                # "I020 / PS01" → PS01, y sin el "(previsto)" de las reservas.
                codigo = tramo.split("/")[-1]
                codigo = re.sub(r"\(.*?\)", "", codigo).strip()
                codigos.append(codigo)
            for a, b in zip(codigos, codigos[1:]):
                declaradas[(a, b)] = (f[0] if f else "") or hoja
    return declaradas


# ------------------------------------------------------------ las dos lecturas

_COMUN = """Leés diagramas eléctricos y respondés SOLO lo que el dibujo
muestra. El plano tiene rótulos de colores agregados con los códigos.

LO MÁS IMPORTANTE: una línea de llamada —la que une un rótulo de color con el
símbolo que nombra, normalmente punteada y del color del rótulo— NO ES UN
CONDUCTOR. Solo señala cuál componente es cuál. Confundirla con un cable es
el error más común en este tipo de plano.

Un conductor es una línea continua que va de un borne a otro. Fijate también
en las barras colectoras: varios componentes pueden colgar de la misma barra
horizontal, y entonces los alimenta la barra, no el componente de al lado.

Si no podés seguir un conductor hasta el final con seguridad, decilo: "no sé"
vale mucho más que una conexión inventada."""

DETECTAR = _COMUN + """

Te doy un catálogo de códigos. Decime cuáles aparecen dibujados o rotulados
en el plano. Ante la duda incluilo: después lo revisa una persona.
Devolvé {"presentes": ["QD01", "..."]}"""

ARRIBA = _COMUN + """

Te pregunto por UN componente: de dónde recibe su alimentación.
Devolvé {"componentes": ["..."], "seguridad": "alta|media|baja", "porque": "..."}"""

ABAJO = _COMUN + """

Te pregunto por UN componente: a qué alimenta él.
Devolvé {"componentes": ["..."], "seguridad": "alta|media|baja", "porque": "..."}"""


def _imagen(png_bytes):
    return {"type": "image_url", "image_url": {
        "url": "data:image/png;base64," + base64.b64encode(png_bytes).decode(),
        "detail": "high"}}


def _texto(t):
    return {"type": "text", "text": t}


def render(pdf, pagina, escala=3.0):
    """La página como PNG. Escala 3 da ~1700x1200, que es lo que ve el modelo."""
    import fitz
    doc = fitz.open(pdf)
    p = doc[pagina - 1]
    return p.get_pixmap(matrix=fitz.Matrix(escala, escala)).tobytes("png")


def detectar(llm, img, catalogo):
    """Qué códigos del catálogo están en el dibujo. Unión de dos pasadas.

    Unión y no intersección: perder un componente deja un hueco invisible,
    mientras que uno de más solo agrega una fila que el revisor descarta.
    """
    listado = ", ".join(f"{c} ({d['nombre']})" for c, d in catalogo.items())
    vistos = set()
    for i in (1, 2):
        r = llm.complete_json(DETECTAR, [
            _texto(f"Catálogo:\n{listado}\n\n¿Cuáles aparecen en el plano?"), img],
            label=f"detectar {i}")
        for x in (r or {}).get("presentes") or []:
            codigo = normalizar(str(x), catalogo)
            if codigo:
                vistos.add(codigo)
    return sorted(vistos)


def proponer(llm, img, codigos, catalogo, hilos=6):
    """Pregunta en las dos direcciones y arma las aristas candidatas."""
    from task_utils.llm_json import run_parallel

    def preguntar(tarea):
        codigo, sentido = tarea
        sistema = ARRIBA if sentido == "arriba" else ABAJO
        verbo = ("¿Qué lo alimenta a él?" if sentido == "arriba"
                 else "¿A qué alimenta él?")
        d = catalogo[codigo]
        r = llm.complete_json(sistema, [
            _texto(f"Componente: {codigo} — {d['nombre']}"
                   + (f" (en el plano: {d['alias']})" if d["alias"] else "")
                   + f".\n{verbo}"), img],
            label=f"{sentido} {codigo}")
        return (codigo, sentido, r or {})

    tareas = [(c, s) for c in codigos for s in ("arriba", "abajo")]
    crudas = run_parallel(tareas, preguntar, max_workers=hilos, label="topología")

    # (origen, destino) → {dirección: motivo}
    propuestas = defaultdict(dict)
    sin_reconocer = set()
    for codigo, sentido, r in crudas:
        motivo = str(r.get("porque") or "")[:200]
        for otro in (r.get("componentes") or []):
            normalizado = normalizar(str(otro), catalogo)
            if not normalizado:
                sin_reconocer.add(str(otro)[:60])
                continue
            arista = ((normalizado, codigo) if sentido == "arriba"
                      else (codigo, normalizado))
            propuestas[arista][sentido] = motivo
    return dict(propuestas), sorted(sin_reconocer)


def analizar_plano(pdf, pagina, llm=None, limite=0, hilos=6, log=print):
    """Todo el análisis de una página: catálogo, detección, topología, cotejo.

    Existe para que la pantalla de revisión y la línea de comandos corran
    exactamente lo mismo. Si una propusiera distinto de la otra, confirmar
    una arista en un lado dejaría de significar lo mismo que en el otro.
    """
    wb = openpyxl.load_workbook(EXCEL, data_only=True)
    catalogo = cargar_catalogo(wb)
    declaradas = aristas_declaradas(wb)
    log(f"catálogo: {len(catalogo)} códigos   ya declaradas: {len(declaradas)} aristas")

    if llm is None:
        sys.path.insert(0, os.path.join(RAIZ, "scripts"))
        from redescribir_figuras_con_valores import cliente_llm
        llm = cliente_llm()
    log(f"modelo: {llm.model}")

    img = _imagen(render(pdf, pagina))
    log("detectando componentes…")
    codigos = detectar(llm, img, catalogo)
    if limite:
        codigos = codigos[:limite]
    log(f"componentes: {', '.join(codigos)}")

    log(f"topología: {len(codigos) * 2} preguntas, dos por componente…")
    propuestas, sin_reconocer = proponer(llm, img, codigos, catalogo, hilos)

    aristas = []
    for (origen, destino), direcciones in sorted(propuestas.items()):
        if origen == destino:
            continue
        ya = declaradas.get((origen, destino))
        invertida = declaradas.get((destino, origen))
        aristas.append({
            "origen": origen, "destino": destino,
            "nombre_origen": catalogo[origen]["nombre"],
            "nombre_destino": catalogo[destino]["nombre"],
            "evidencia": " / ".join(f"[{s}] {m}" for s, m in sorted(direcciones.items())),
            "direcciones": sorted(direcciones),
            "conflicto": (f"ya declarada en {ya}" if ya else
                          f"CONFLICTO: {destino} → {origen} ya está declarada "
                          f"({invertida})" if invertida else ""),
        })
    log(f"aristas propuestas: {len(aristas)}")
    return {"catalogo": catalogo, "codigos": codigos, "aristas": aristas,
            "sin_reconocer": sin_reconocer, "declaradas": declaradas}


# ----------------------------------------------------------------- la salida

def filas_para_excel(propuestas, catalogo, declaradas, fuente):
    """Una fila por arista, en el orden de columnas de `Conexiones TBEN`."""
    filas = []
    for (origen, destino), direcciones in sorted(propuestas.items()):
        if origen == destino:
            continue
        ya = declaradas.get((origen, destino))
        invertida = declaradas.get((destino, origen))

        if ya:
            estado = f"ya declarada en {ya}"
        elif invertida:
            estado = f"CONFLICTO: {destino} → {origen} ya está declarada ({invertida})"
        else:
            estado = ""

        cuantas = len(direcciones)
        evidencia = " / ".join(f"[{s}] {m}" for s, m in sorted(direcciones.items()))
        filas.append([
            "",                                                   # Puerto
            f"{catalogo[origen]['nombre']} → {catalogo[destino]['nombre']}",
            "",                                                   # Función: la pone quien revisa
            f"{catalogo[origen]['id']} / {origen} → {catalogo[destino]['id']} / {destino}",
            fuente,
            evidencia,
            ("ninguna dirección la confirma" if cuantas == 0 else
             "propuesta por UNA sola lectura" if cuantas == 1 else
             "las dos lecturas coinciden (no implica que sea correcta)"),
            estado,
            "",                                                   # Conexión confirmada: la llena el revisor
            "PROPUESTA AUTOMÁTICA — revisar antes de usar",
        ])
    return filas


def escribir(ruta, filas, codigos, sin_reconocer, fuente):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Conexiones propuestas"
    ws.append([f"Propuesta automática de conexiones — {fuente}"])
    ws.append(["Generada por scripts/proponer_conexiones.py. NINGUNA fila está "
               "verificada: la columna 'Conexión confirmada' la completa una "
               "persona, y construir_grafo.py solo toma las confirmadas."])
    ws.append(COLUMNAS)
    for f in filas:
        ws.append(f)

    for ancho, letra in zip((10, 46, 20, 46, 18, 60, 34, 42, 20, 40), "ABCDEFGHIJ"):
        ws.column_dimensions[letra].width = ancho

    hoja2 = wb.create_sheet("Sin reconocer")
    hoja2.append(["Lo que el modelo nombró y no se pudo llevar a un código "
                  "del catálogo. Puede ser un componente que falta en la "
                  "hoja Nomenclatura, o algo que inventó."])
    hoja2.append([])
    for x in sin_reconocer:
        hoja2.append([x])
    hoja2.column_dimensions["A"].width = 70

    hoja3 = wb.create_sheet("Componentes detectados")
    hoja3.append(["Los códigos que el modelo dice ver en el plano."])
    hoja3.append([])
    for c in codigos:
        hoja3.append([c])

    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    wb.save(ruta)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--plano", required=True,
                    help="parte del nombre del PDF bajo data/raw_data")
    ap.add_argument("--pagina", type=int, default=1)
    ap.add_argument("--limite", type=int, default=0,
                    help="probar con los primeros N componentes")
    ap.add_argument("--hilos", type=int, default=6)
    ap.add_argument("--salida", default=SALIDA)
    args = ap.parse_args()

    carpeta = os.path.join(RAIZ, "data", "raw_data")
    candidatos = [f for f in os.listdir(carpeta)
                  if f.lower().endswith(".pdf") and args.plano.lower() in f.lower()]
    if len(candidatos) != 1:
        print(f"'{args.plano}' coincide con {len(candidatos)} PDF: {candidatos}")
        return 1
    pdf = os.path.join(carpeta, candidatos[0])
    fuente = f"{candidatos[0]} p.{args.pagina}"

    r = analizar_plano(pdf, args.pagina, limite=args.limite, hilos=args.hilos)
    catalogo, codigos = r["catalogo"], r["codigos"]
    sin_reconocer = r["sin_reconocer"]

    propuestas = {(a["origen"], a["destino"]): {d: "" for d in a["direcciones"]}
                  for a in r["aristas"]}
    filas = filas_para_excel(propuestas, catalogo, r["declaradas"], fuente)
    # La evidencia real, no la reconstruida.
    for fila, arista in zip(filas, r["aristas"]):
        fila[5] = arista["evidencia"]
    escribir(args.salida, filas, codigos, sin_reconocer, fuente)

    nuevas = sum(1 for f in filas if not f[7])
    conflictos = [f for f in filas if f[7].startswith("CONFLICTO")]
    print(f"\n  aristas propuestas     : {len(filas)}")
    print(f"     nuevas              : {nuevas}")
    print(f"     ya declaradas       : {len(filas) - nuevas - len(conflictos)}")
    print(f"     en conflicto        : {len(conflictos)}")
    for f in conflictos:
        print(f"        {f[3]}  —  {f[7]}")
    if sin_reconocer:
        print(f"  nombres sin reconocer  : {len(sin_reconocer)}")
    print(f"\nEscrito: {args.salida}")
    print("Ninguna fila está confirmada. Revisalas y completá 'Conexión confirmada'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
