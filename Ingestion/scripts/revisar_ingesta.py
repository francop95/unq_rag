"""
Revisión humana de lo que dejó el troceo, antes de indexarlo
============================================================

El pipeline ya genera cuatro reportes de validación, y los cuatro miden
estructura: cuántos chunks, cuántos duplicados, qué longitud, qué confianza. La
chapa del motor pasó los cuatro como válida mientras decía "Conexión A/Y" en
vez de Δ/Y y "MT 160-4" en vez de MT 802-4. Lo único que agarra eso es alguien
mirando el recorte al lado de su descripción.

Esto es esa pantalla, en consola. Existe en consola y no en el navegador por la
misma razón que `telemetria/revisar.py`: si revisarlo cuesta trabajo, no se
revisa, y una herramienta que ya funciona hoy vale más que una interfaz que
llega la semana que viene.

    python scripts/revisar_ingesta.py                       # qué hay pendiente
    python scripts/revisar_ingesta.py tesis                 # revisar una corrida
    python scripts/revisar_ingesta.py tesis --todo          # también el texto
    python scripts/revisar_ingesta.py tesis --ok p87:chunk_3_visual
    python scripts/revisar_ingesta.py tesis --redescribir p87:chunk_3_visual
    python scripts/revisar_ingesta.py tesis --descartar p12:chunk_1 --nota "duplicado"
    python scripts/revisar_ingesta.py tesis --aprobar
    python scripts/revisar_ingesta.py tesis --diff          # qué cambió desde lo aprobado

El recorte no se puede dibujar en una terminal, así que se imprime su ruta y la
URL con la que la API ya lo sirve: `MEDIA_BASE_DIR` es `Ingestion/data`, así que
los crops son accesibles sin agregar un solo endpoint.
"""

import argparse
import getpass
import json
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "src"))

from task_utils.chunk_text import readable_chunk_text  # noqa: E402
from task_utils import revision_store as rev  # noqa: E402

CHUNKS_DATA = os.path.join(RAIZ, "data", "chunks_data")

# Las figuras y las tablas primero: son ~300 de 1569 chunks y ahí vivieron
# todos los defectos encontrados hasta hoy.
VISUALES = ("diagram_visual", "image", "table", "diagram_description")

BASE_MEDIA = os.getenv("MEDIA_BASE_URL", "http://127.0.0.1:5000/media")


def corridas_de(documento: str):
    """Las corridas de troceo de un documento, de más nueva a más vieja."""
    base = os.path.join(CHUNKS_DATA, documento)
    if not os.path.isdir(base):
        return []
    return sorted((d for d in os.listdir(base)
                   if os.path.isdir(os.path.join(base, d))), reverse=True)


def documentos_que_coinciden(texto: str):
    if not os.path.isdir(CHUNKS_DATA):
        return []
    return sorted(d for d in os.listdir(CHUNKS_DATA)
                  if os.path.isdir(os.path.join(CHUNKS_DATA, d))
                  and texto.lower() in d.lower())


def leer_chunks(documento: str, corrida: str):
    """Los chunks de una corrida, en orden de página."""
    base = os.path.join(CHUNKS_DATA, documento, corrida)
    salida = []
    for carpeta in sorted(os.listdir(base)):
        ruta = os.path.join(base, carpeta)
        if not os.path.isdir(ruta) or carpeta == "crops":
            continue
        for nombre in sorted(os.listdir(ruta)):
            if not nombre.endswith(".json"):
                continue
            try:
                with open(os.path.join(ruta, nombre), encoding="utf-8") as f:
                    datos = json.load(f)
            except (OSError, ValueError) as e:
                print(f"   ⚠️  no se pudo leer {nombre}: {e}")
                continue
            salida.extend(datos if isinstance(datos, list) else [datos])

    def orden(c):
        try:
            return (int(c.get("page_num", 0)), str(c.get("chunk_id", "")))
        except (TypeError, ValueError):
            return (0, str(c.get("chunk_id", "")))

    return sorted(salida, key=orden)


def _recorte(chunk):
    """La ruta del recorte de una figura, si tiene."""
    original = chunk.get("original_chunk")
    if isinstance(original, str):
        try:
            original = json.loads(original)
        except ValueError:
            return None
    if not isinstance(original, dict):
        return None
    return original.get("image_path") or original.get("media_path")


def _url_media(ruta: str):
    """La URL con la que la API sirve ese archivo, si cae bajo Ingestion/data."""
    datos = os.path.abspath(os.path.join(RAIZ, "data"))
    entero = os.path.abspath(ruta)
    if not entero.startswith(datos + os.sep):
        return None
    return f"{BASE_MEDIA}/{os.path.relpath(entero, datos).replace(os.sep, '/')}"


def _marca(veredicto):
    return {None: "[ · ]", "ok": "[ ok]", "redescribir": "[REH]",
            "editar": "[EDI]", "descartar": "[ ✗ ]"}.get(veredicto, "[ ? ]")


def mostrar(documento, corrida, todo=False):
    chunks = leer_chunks(documento, corrida)
    yas = rev.veredictos(documento, corrida)
    est = rev.estado(documento, corrida) or {"estado": "sin registrar"}

    print(f"\n{documento}")
    print(f"corrida {corrida}   estado: {est['estado']}")
    print(f"{len(chunks)} chunks   ({len(yas)} ya revisados)\n")

    visuales = [c for c in chunks if c.get("content_type") in VISUALES]
    textos = [c for c in chunks if c.get("content_type") not in VISUALES]

    def bloque(titulo, lista, completo):
        if not lista:
            return
        print(f"── {titulo} ({len(lista)}) " + "─" * max(0, 56 - len(titulo)))
        for c in lista:
            pag, cid = str(c.get("page_num")), str(c.get("chunk_id"))
            v = yas.get((pag, cid))
            print(f"\n{_marca(v['veredicto'] if v else None)} p{pag}:{cid}"
                  f"   [{c.get('content_type')}]")
            if v and v.get("nota"):
                print(f"      nota: {v['nota']}")
            recorte = _recorte(c)
            if recorte:
                print(f"      recorte: {recorte}")
                url = _url_media(recorte)
                if url:
                    print(f"      ver    : {url}")
            texto = readable_chunk_text(c).strip()
            if not completo:
                texto = texto[:400] + ("…" if len(texto) > 400 else "")
            for linea in texto.split("\n"):
                print(f"      {linea}")
        print()

    # Completo siempre para lo visual: la descripción al lado del recorte es
    # justamente lo que se vino a revisar, y recortarla esconde el error.
    bloque("FIGURAS Y TABLAS", visuales, completo=True)
    if todo:
        bloque("TEXTO", textos, completo=False)
    elif textos:
        print(f"── TEXTO ({len(textos)}) ── se omite; --todo para verlo\n")

    falta = [c for c in (visuales if not todo else chunks)
             if (str(c.get("page_num")), str(c.get("chunk_id"))) not in yas]
    pend = rev.trabajo_pendiente(documento, corrida)
    if falta:
        print(f"sin revisar: {len(falta)}")
    if pend:
        print(f"marcados para rehacer y todavía no rehechos: {len(pend)}")
        for p in pend:
            print(f"   p{p['pagina']}:{p['chunk_id']} -> {p['veredicto']}"
                  f"   ({rev.VEREDICTOS[p['veredicto']]})")


def mostrar_diff(documento, corrida):
    """Qué cambió respecto de la última corrida aprobada.

    Es la vista que más defectos por minuto encuentra: revisar 1569 chunks de
    cero no se hace dos veces, y los que cambiaron son pocos. Así se vio que un
    barrido de valores había degradado la página 87, que perdió su "2 kW" y
    recuperó un "2.5A" inventado que una corrección anterior había sacado.
    """
    previa = rev.ultima_aprobada(documento)
    if not previa:
        print(f"{documento}: no hay ninguna corrida aprobada contra la cual comparar.")
        return
    if previa == corrida:
        print(f"{documento}: la corrida {corrida} ES la última aprobada.")
        return

    def por_clave(c):
        return {(str(x.get("page_num")), str(x.get("chunk_id"))):
                readable_chunk_text(x).strip() for x in c}

    antes = por_clave(leer_chunks(documento, previa))
    ahora = por_clave(leer_chunks(documento, corrida))

    nuevos = sorted(set(ahora) - set(antes))
    perdidos = sorted(set(antes) - set(ahora))
    cambiados = sorted(k for k in set(antes) & set(ahora) if antes[k] != ahora[k])

    print(f"\n{documento}: {corrida} contra la aprobada {previa}")
    print(f"  nuevos {len(nuevos)}   perdidos {len(perdidos)}   cambiados {len(cambiados)}\n")
    for k in perdidos:
        print(f"  ✗ desapareció p{k[0]}:{k[1]}")
    for k in nuevos:
        print(f"  + nuevo       p{k[0]}:{k[1]}")
    for k in cambiados:
        print(f"\n  ~ cambió      p{k[0]}:{k[1]}")
        print(f"      antes ({len(antes[k])} car): {antes[k][:220]}")
        print(f"      ahora ({len(ahora[k])} car): {ahora[k][:220]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("documento", nargs="?", default="",
                    help="parte del nombre del documento a revisar")
    ap.add_argument("--corrida", default="", help="una corrida puntual (default: la última)")
    ap.add_argument("--todo", action="store_true", help="mostrar también los chunks de texto")
    ap.add_argument("--diff", action="store_true", help="comparar con la última aprobada")
    for v in rev.VEREDICTOS:
        ap.add_argument(f"--{v}", metavar="PAG:CHUNK", action="append", default=[],
                        help=rev.VEREDICTOS[v])
    ap.add_argument("--nota", default="", help="nota para los veredictos de esta corrida")
    ap.add_argument("--aprobar", action="store_true")
    ap.add_argument("--rechazar", action="store_true")
    ap.add_argument("--quien", default=getpass.getuser())
    args = ap.parse_args()

    if not args.documento:
        pend = rev.corridas("pendiente")
        if not pend:
            print("No hay corridas pendientes de revisión.")
            return 0
        print(f"{len(pend)} corrida(s) pendientes:\n")
        for c in pend:
            print(f"  {c['documento']}")
            print(f"     corrida {c['corrida']}   registrada {c['registrada_en'][:19]}")
        print("\nRevisá una con:  python scripts/revisar_ingesta.py <parte del nombre>")
        return 0

    coincidencias = documentos_que_coinciden(args.documento)
    if not coincidencias:
        print(f"Ningún documento troceado coincide con {args.documento!r}.")
        return 1
    if len(coincidencias) > 1:
        print(f"{args.documento!r} coincide con varios; precisá más:")
        for d in coincidencias:
            print(f"   {d}")
        return 1
    documento = coincidencias[0]

    corrida = args.corrida or (corridas_de(documento) or [None])[0]
    if not corrida:
        print(f"{documento}: no tiene ninguna corrida de troceo en disco.")
        return 1
    rev.registrar_corrida(documento, corrida)

    anotados = 0
    for veredicto in rev.VEREDICTOS:
        for objetivo in getattr(args, veredicto.replace("-", "_")):
            if ":" not in objetivo:
                print(f"'{objetivo}' no tiene forma PAGINA:CHUNK (ej. p87:chunk_3_visual)")
                return 1
            pag, cid = objetivo.split(":", 1)
            rev.anotar(documento, corrida, pag.lstrip("pP"), cid,
                       veredicto, args.nota, args.quien)
            print(f"  {_marca(veredicto)} p{pag.lstrip('pP')}:{cid}")
            anotados += 1

    if args.rechazar:
        rev.decidir(documento, corrida, "rechazada", args.quien, args.nota)
        print(f"\n{documento} / {corrida}: RECHAZADA. No se va a indexar.")
        return 0

    if args.aprobar:
        pendiente = rev.trabajo_pendiente(documento, corrida)
        if pendiente:
            # Aprobar con esto encima sería aprobar lo que ya se dijo que está
            # mal: el veredicto quedaría registrado y el chunk se indexaría igual.
            print(f"\nNo se aprueba: hay {len(pendiente)} artefacto(s) marcados para "
                  f"rehacer y todavía no rehechos.")
            for p in pendiente:
                print(f"   p{p['pagina']}:{p['chunk_id']} -> {p['veredicto']}")
            print("\nRehacelos, volvé a trocear o marcalos de otra forma, y aprobá después.")
            return 1
        rev.decidir(documento, corrida, "aprobada", args.quien, args.nota)
        desc = rev.descartados(documento, corrida)
        print(f"\n{documento} / {corrida}: APROBADA"
              + (f", con {len(desc)} chunk(s) descartados." if desc else "."))
        print("Indexala con:  python src/main_multimodal.py --reusar-chunks "
              f"--solo '{documento}'")
        return 0

    if args.diff:
        mostrar_diff(documento, corrida)
        return 0
    if not anotados:
        mostrar(documento, corrida, todo=args.todo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
