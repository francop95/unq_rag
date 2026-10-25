"""
La pantalla de revisión de la ingesta
=====================================

Entre trocear y indexar falta que alguien mire lo que quedó. Los cuatro
informes que el pipeline genera miden estructura, no verdad: cuentan chunks,
deduplican, controlan longitudes y confianza. La chapa del motor pasó los
cuatro como válida mientras decía "Conexión A/Y" en vez de Δ/Y, "MT 160-4" en
vez de MT 802-4 y "230/380" en vez de 220/380. Lo único que agarra eso es una
persona mirando el recorte al lado de su descripción, y para eso hay que poder
ver el recorte.

Por qué un servidor aparte y no la API del asistente
----------------------------------------------------
La API es lo que está publicado: corre en el servidor, la atiende nginx detrás
de una contraseña y la usa gente de afuera. Esto apunta al revés: vive donde
están los documentos y la carpeta de corridas, lanza el pipeline como
subproceso y gasta plata en el modelo de visión. Meterlo en la API sería poner
un botón que gasta dinero del otro lado de internet, y además no serviría: en
el servidor no está ni `raw_data` ni el troceo.

Por eso escucha en 127.0.0.1 y no pide contraseña. Si se expone a la red, lo
que se expone es el botón de gastar.

Las dos pantallas leen lo mismo
-------------------------------
Todo lo que muestra sale de `task_utils.revision_corridas`, el mismo módulo
que usa `scripts/revisar_ingesta.py`, y todo lo que decide va a
`task_utils.revision_store`, la misma base. Aprobar desde el navegador y
aprobar desde la consola son la misma operación: si no lo fueran, la compuerta
no estaría prometiendo nada.
"""

import os
import sys
from typing import Any, Dict, Optional

from flask import Flask, abort, jsonify, make_response, request, send_from_directory

from task_utils import revision_corridas as disco
from task_utils import revision_store as rev

from . import trabajos
from .pagina import pagina_html

RAIZ_INGESTION = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
)

app = Flask(__name__)


# ---------------------------------------------------------------- utilidades

def _quien() -> str:
    """Quién está revisando. Sin login: el usuario del sistema alcanza.

    No es seguridad, es trazabilidad: sirve para saber a quién preguntarle por
    qué aprobó algo, y acá adentro no hay nadie más.
    """
    import getpass
    try:
        return os.getenv("REVISOR") or getpass.getuser()
    except Exception:
        return "desconocido"


def _documento_valido(documento: str) -> str:
    """Un documento que existe en disco, o 404.

    Se compara contra la lista real en vez de limpiar la cadena: cualquier
    cosa que no sea exactamente una carpeta de corridas no llega al
    `os.path.join`, y con eso no hay `../` que valga.
    """
    if documento not in disco.documentos():
        abort(404, f"no hay corridas de {documento!r}")
    return documento


def _corrida_valida(documento: str, corrida: Optional[str]) -> str:
    disponibles = disco.corridas_de(documento)
    if not disponibles:
        abort(404, f"{documento} no tiene corridas en disco")
    if not corrida:
        return disponibles[0]
    if corrida not in disponibles:
        abort(404, f"{documento} no tiene la corrida {corrida!r}")
    return corrida


def _chunk_para_pantalla(chunk: Dict[str, Any], veredicto: Optional[Dict]) -> Dict:
    recorte = disco.recorte_de(chunk)
    relativa = disco.ruta_relativa_a_datos(recorte) if recorte else None
    pagina, chunk_id = disco.clave(chunk)
    return {
        "pagina": pagina,
        "chunk_id": chunk_id,
        "tipo": chunk.get("content_type") or "",
        "visual": (chunk.get("content_type") or "") in disco.VISUALES,
        "encabezado": chunk.get("heading") or "",
        "texto": disco.texto_de(chunk),
        "imagen": f"/media/{relativa}" if relativa else None,
        "veredicto": (veredicto or {}).get("veredicto"),
        "nota": (veredicto or {}).get("nota") or "",
        "texto_revisado": (veredicto or {}).get("texto_revisado") or "",
        "quien": (veredicto or {}).get("quien") or "",
    }


# -------------------------------------------------------------------- vistas

@app.get("/")
def inicio():
    r = make_response(pagina_html())
    r.headers["Content-Type"] = "text/html; charset=utf-8"
    # La página trae su CSS y su JS adentro, así que no necesita permitir
    # ningún origen externo. Las imágenes son los recortes, servidos acá mismo.
    r.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
        "img-src 'self'; connect-src 'self'; form-action 'none'; frame-ancestors 'none'"
    )
    return r


@app.get("/media/<path:relpath>")
def media(relpath):
    """Los recortes de las figuras, servidos desde `Ingestion/data`.

    El mismo contrato que `/media` de la API —misma raíz, mismas rutas— para
    que una ruta de recorte signifique lo mismo en las dos.
    """
    base = disco.raiz_datos()
    entero = os.path.abspath(os.path.join(base, relpath))
    if not entero.startswith(base + os.sep) or not os.path.isfile(entero):
        abort(404)
    carpeta, nombre = os.path.split(entero)
    return send_from_directory(carpeta, nombre)


@app.get("/api/documentos")
def api_documentos():
    """El corpus entero: lo que se puede trocear y lo que ya se troceó."""
    por_nombre: Dict[str, Dict[str, Any]] = {}
    for d in disco.documentos_ingestables():
        por_nombre[d["nombre"]] = {**d, "corridas": [], "estado": "sin trocear"}

    for nombre in disco.documentos():
        entrada = por_nombre.setdefault(nombre, {
            # Troceado alguna vez pero ya no está en raw_data: se muestra
            # igual, porque su corrida puede estar indexada y conviene verla.
            "nombre": nombre, "archivo": "", "filtro": "", "relativa": "",
            "bytes": 0, "corridas": [], "estado": "sin trocear",
        })
        corridas = []
        for c in disco.corridas_de(nombre):
            est = rev.estado(nombre, c) or {}
            corridas.append({
                "corrida": c,
                "estado": est.get("estado") or "sin registrar",
                "decidida_en": est.get("decidida_en"),
                "quien": est.get("quien"),
            })
        entrada["corridas"] = corridas
        entrada["estado"] = corridas[0]["estado"] if corridas else "sin trocear"
        entrada["ultima"] = corridas[0]["corrida"] if corridas else None

    return jsonify({
        "documentos": sorted(por_nombre.values(), key=lambda d: d["nombre"].lower()),
        "veredictos": rev.VEREDICTOS,
        "revisor": _quien(),
    })


@app.get("/api/corrida/<documento>")
def api_corrida(documento):
    documento = _documento_valido(documento)
    corrida = _corrida_valida(documento, request.args.get("corrida"))

    chunks = disco.leer_chunks(documento, corrida)
    yas = rev.veredictos(documento, corrida)
    est = rev.estado(documento, corrida) or {}
    pendientes = rev.trabajo_pendiente(documento, corrida)

    vistos = [_chunk_para_pantalla(c, yas.get(disco.clave(c))) for c in chunks]
    visuales = [c for c in vistos if c["visual"]]

    return jsonify({
        "documento": documento,
        "corrida": corrida,
        "corridas": disco.corridas_de(documento),
        "estado": est.get("estado") or "sin registrar",
        "nota": est.get("nota") or "",
        "chunks": vistos,
        "resumen": {
            "total": len(vistos),
            "visuales": len(visuales),
            "revisados": len([c for c in vistos if c["veredicto"]]),
            "sin_revisar_visuales": len([c for c in visuales if not c["veredicto"]]),
            "pendientes": len(pendientes),
            "descartados": len([c for c in vistos if c["veredicto"] == "descartar"]),
            "corregidos": len([c for c in vistos if c["veredicto"] == "corregido"]),
        },
        "ultima_aprobada": rev.ultima_aprobada(documento),
        "grafo_desactualizado": _grafo_desactualizado(),
    })


@app.get("/api/corrida/<documento>/diff")
def api_diff(documento):
    documento = _documento_valido(documento)
    corrida = _corrida_valida(documento, request.args.get("corrida"))
    previa = rev.ultima_aprobada(documento)
    if not previa:
        return jsonify({"hay": False,
                        "motivo": "todavía no hay ninguna corrida aprobada "
                                  "de este documento contra la cual comparar."})
    if previa == corrida:
        return jsonify({"hay": False,
                        "motivo": f"{corrida} ES la última aprobada."})

    d = disco.comparar(documento, corrida, previa)
    return jsonify({
        "hay": True,
        "previa": previa,
        "nuevos": [{"pagina": k[0], "chunk_id": k[1]} for k in d["nuevos"]],
        "perdidos": [{"pagina": k[0], "chunk_id": k[1]} for k in d["perdidos"]],
        "cambiados": [{"pagina": k[0], "chunk_id": k[1],
                       "antes": d["antes"][k], "ahora": d["ahora"][k]}
                      for k in d["cambiados"]],
    })


@app.post("/api/veredicto")
def api_veredicto():
    cuerpo = request.get_json(silent=True) or {}
    documento = _documento_valido(str(cuerpo.get("documento", "")))
    corrida = _corrida_valida(documento, str(cuerpo.get("corrida") or ""))
    veredicto = str(cuerpo.get("veredicto", ""))
    if veredicto not in rev.VEREDICTOS:
        abort(400, f"veredicto desconocido: {veredicto!r}")

    rev.registrar_corrida(documento, corrida)
    rev.anotar(documento, corrida,
               str(cuerpo.get("pagina", "")), str(cuerpo.get("chunk_id", "")),
               veredicto, str(cuerpo.get("nota") or ""), _quien(),
               texto_revisado=cuerpo.get("texto_revisado"))
    return jsonify({"ok": True})


@app.post("/api/decidir")
def api_decidir():
    cuerpo = request.get_json(silent=True) or {}
    documento = _documento_valido(str(cuerpo.get("documento", "")))
    corrida = _corrida_valida(documento, str(cuerpo.get("corrida") or ""))
    estado = str(cuerpo.get("estado", ""))
    if estado not in ("aprobada", "rechazada"):
        abort(400, "estado tiene que ser 'aprobada' o 'rechazada'")

    if estado == "aprobada":
        # El mismo rechazo que en consola: aprobar con trabajo marcado y sin
        # hacer sería aprobar lo que ya se dijo que está mal, y el chunk se
        # indexaría igual con el veredicto anotado al lado.
        pendientes = rev.trabajo_pendiente(documento, corrida)
        if pendientes:
            return jsonify({
                "ok": False,
                "motivo": f"hay {len(pendientes)} artefacto(s) marcados para "
                          f"rehacer y todavía no rehechos",
                "pendientes": [{"pagina": p["pagina"], "chunk_id": p["chunk_id"],
                                "veredicto": p["veredicto"]} for p in pendientes],
            }), 409

    rev.registrar_corrida(documento, corrida)
    rev.decidir(documento, corrida, estado, _quien(), str(cuerpo.get("nota") or ""))
    return jsonify({"ok": True, "estado": estado,
                    "descartados": len(rev.descartados(documento, corrida))})


# ------------------------------------------------------------------ trabajos

def _lanzar_pipeline(clase: str, etiqueta: str, filtro: str, extra: list,
                     contexto: Dict[str, Any]):
    argv = [trabajos.python_del_entorno(), "src/main_multimodal.py",
            "--solo", filtro] + extra
    return trabajos.lanzar_comando(clase, etiqueta, argv, RAIZ_INGESTION, contexto)


@app.post("/api/procesar")
def api_procesar():
    """Trocea un documento y para, sin gastar embeddings ni tocar el índice.

    Siempre con `--solo-chunks`: el sentido de esta pantalla es que nada se
    indexe sin que alguien lo haya mirado, y un botón que indexara de corrido
    la dejaría sin propósito.

    Y siempre con `--forzar`, que la ingesta por lotes no usa. Ahí saltear un
    documento que no cambió es lo correcto; acá la persona eligió ese
    documento y apretó el botón, así que saltearlo sería un trabajo que
    termina "bien" sin haber hecho nada.
    """
    cuerpo = request.get_json(silent=True) or {}
    filtro = str(cuerpo.get("filtro") or "").strip()
    nombre = str(cuerpo.get("documento") or filtro)
    if not filtro:
        abort(400, "falta el documento")
    try:
        t = _lanzar_pipeline("procesar", f"troceando {nombre}", filtro,
                             ["--solo-chunks", "--forzar"], {"documento": nombre})
    except trabajos.EnCurso as e:
        return jsonify({"ok": False, "motivo": str(e)}), 409
    return jsonify({"ok": True, "etiqueta": t.etiqueta})


@app.post("/api/indexar")
def api_indexar():
    """Indexa una corrida ya aprobada, reutilizando los chunks de disco.

    Con `--exigir-revision` puesto siempre, no como opción: desde acá no se
    indexa nada que no tenga el visto bueno, aunque el botón se apriete sin
    querer.
    """
    cuerpo = request.get_json(silent=True) or {}
    documento = _documento_valido(str(cuerpo.get("documento", "")))
    filtro = str(cuerpo.get("filtro") or "").strip() or documento
    try:
        t = _lanzar_pipeline("indexar", f"indexando {documento}", filtro,
                             ["--reusar-chunks", "--exigir-revision"],
                             {"documento": documento})
    except trabajos.EnCurso as e:
        return jsonify({"ok": False, "motivo": str(e)}), 409
    return jsonify({"ok": True, "etiqueta": t.etiqueta})


@app.post("/api/releer")
def api_releer():
    """Vuelve a leer UNA figura con el modelo de visión.

    Es la misma pasada que `scripts/redescribir_figuras_con_valores.py`
    —transcribir dos veces con encuadres distintos, cotejar en Python, y
    recién ahí describir— aplicada al recorte de una corrida que todavía no se
    indexó. No pisa nada: deja la propuesta en el resultado del trabajo, y
    aplicarla es un segundo gesto de la persona.
    """
    cuerpo = request.get_json(silent=True) or {}
    documento = _documento_valido(str(cuerpo.get("documento", "")))
    corrida = _corrida_valida(documento, str(cuerpo.get("corrida") or ""))
    pagina = str(cuerpo.get("pagina", ""))
    chunk_id = str(cuerpo.get("chunk_id", ""))

    objetivo = next((c for c in disco.leer_chunks(documento, corrida)
                     if disco.clave(c) == (pagina, chunk_id)), None)
    if objetivo is None:
        abort(404, f"no existe p{pagina}:{chunk_id} en {corrida}")
    recorte = disco.recorte_de(objetivo)
    if not recorte or not os.path.isfile(recorte):
        abort(400, "ese chunk no tiene un recorte en disco para releer")

    vieja = disco.texto_de(objetivo)

    def cuerpo_trabajo(log):
        sys.path.insert(0, os.path.join(RAIZ_INGESTION, "scripts"))
        from redescribir_figuras_con_valores import cliente_llm, redescribir_figura
        from task_utils.chunk_text import texto_del_chunk

        llm = cliente_llm()
        log(f"modelo: {llm.model}")
        log(f"recorte: {os.path.basename(recorte)}")
        log("transcribiendo dos veces con encuadres distintos…")
        r = redescribir_figura(
            {"ruta": recorte, "archivo": objetivo.get("file_name") or documento,
             "pagina": pagina, "descripcion_vieja": vieja}, llm)
        if not r.get("descripcion_nueva"):
            log(f"sin propuesta: {r.get('motivo')}")
            return {"hay": False, "motivo": r.get("motivo")}
        nuevo = texto_del_chunk(r["descripcion_nueva"])
        log(f"mosaicos: {r.get('mosaicos')}   "
            f"valores en disputa: {len(r.get('en_disputa') or [])}")
        for x in (r.get("descartado") or []):
            log(f"  descartado — {x}")
        return {"hay": True, "pagina": pagina, "chunk_id": chunk_id,
                "antes": vieja, "ahora": nuevo,
                "en_disputa": r.get("en_disputa") or [],
                "descartado": r.get("descartado") or []}

    try:
        t = trabajos.lanzar_funcion(
            "releer", f"releyendo p{pagina}:{chunk_id}", cuerpo_trabajo,
            {"documento": documento, "corrida": corrida,
             "pagina": pagina, "chunk_id": chunk_id})
    except trabajos.EnCurso as e:
        return jsonify({"ok": False, "motivo": str(e)}), 409

    # Queda marcado antes de que vuelva: si se cierra la pestaña a mitad, la
    # corrida no se puede aprobar hasta resolver esta figura.
    rev.registrar_corrida(documento, corrida)
    rev.anotar(documento, corrida, pagina, chunk_id, "redescribir",
               "relectura lanzada desde la pantalla", _quien())
    return jsonify({"ok": True, "etiqueta": t.etiqueta})


def _grafo_desactualizado():
    """¿Hay decisiones sobre conexiones más nuevas que el último grafo?

    No se reconstruye solo al aprobar, por tres motivos:

    - Aprobar casi nunca toca conexiones. La mayoría de las revisiones son
      sobre figuras, y reconstruir pagaría la extracción con el modelo cada
      vez sin que el grafo cambie en nada.
    - `construir_grafo.py` reescribe el grafo y tira las aristas extraídas;
      las repone `extraer_relaciones.py`. Correr solo el primero, que es el
      gratis, DEGRADA el grafo. O van los dos o no va ninguno, y los dos
      cuestan.
    - Gastar plata como efecto secundario de un botón que dice "Aprobar" es
      una sorpresa, y en esta pantalla todo lo que cuesta pregunta antes.

    Así que en vez de automatizarlo se lo hace imposible de olvidar: si hay
    decisiones sin reflejar, la pantalla lo dice.
    """
    grafo = os.path.join(os.path.dirname(RAIZ_INGESTION), "API", "configs", "grafo.json")
    if not os.path.isfile(grafo):
        return True
    construido = os.path.getmtime(grafo)
    for c in rev.corridas():
        for f in rev.conexiones(c["documento"], c["corrida"]):
            if f["estado"] == "propuesta":
                continue  # una propuesta no cambia el grafo
            try:
                from datetime import datetime
                if datetime.fromisoformat(f["cuando"]).timestamp() > construido:
                    return True
            except (TypeError, ValueError):
                continue
    return False


def _catalogo():
    """Los códigos de la hoja `Nomenclatura`, para los desplegables.

    El catálogo entero y no solo los códigos que el modelo nombró: si una
    arista se arma únicamente con lo ya propuesto, un componente que la
    lectura no vio —pasa con los que cuelgan de una barra colectora— no se
    puede conectar nunca, y la tabla queda incompleta para siempre.
    """
    try:
        sys.path.insert(0, os.path.join(RAIZ_INGESTION, "scripts"))
        import openpyxl
        from proponer_conexiones import EXCEL, cargar_catalogo
        catalogo = cargar_catalogo(openpyxl.load_workbook(EXCEL, data_only=True))
        return [{"codigo": c, "nombre": d["nombre"]}
                for c, d in sorted(catalogo.items())]
    except Exception as e:  # noqa: BLE001 — sin inventario la pantalla igual sirve
        print(f"no se pudo leer la nomenclatura: {e}")
        return []


@app.get("/api/conexiones/<documento>")
def api_conexiones(documento):
    """Las aristas de un plano, propuestas y revisadas."""
    documento = _documento_valido(documento)
    corrida = _corrida_valida(documento, request.args.get("corrida"))

    # Las páginas que tienen una figura son las que vale la pena analizar:
    # una página de puro texto no tiene topología que leer.
    paginas = sorted({c["pagina"] for c in
                      (_chunk_para_pantalla(x, None)
                       for x in disco.leer_chunks(documento, corrida))
                      if c["visual"] and c["imagen"]},
                     key=lambda x: int(x) if x.isdigit() else 0)

    filas = rev.conexiones(documento, corrida)
    return jsonify({
        "documento": documento, "corrida": corrida,
        "paginas": paginas,
        "conexiones": filas,
        "catalogo": _catalogo(),
        "estados": rev.ESTADOS_CONEXION,
        "resumen": {
            "total": len(filas),
            "propuestas": len([c for c in filas if c["estado"] == "propuesta"]),
            "confirmadas": len([c for c in filas if c["estado"] == "confirmada"]),
            "descartadas": len([c for c in filas if c["estado"] == "descartada"]),
            "conflictos": len([c for c in filas
                               if (c["conflicto"] or "").startswith("CONFLICTO")]),
        },
    })


@app.post("/api/conexiones/veredicto")
def api_conexion_veredicto():
    """Confirma, descarta o agrega a mano una arista."""
    cuerpo = request.get_json(silent=True) or {}
    documento = _documento_valido(str(cuerpo.get("documento", "")))
    corrida = _corrida_valida(documento, str(cuerpo.get("corrida") or ""))
    estado = str(cuerpo.get("estado", ""))
    if estado not in rev.ESTADOS_CONEXION:
        abort(400, f"estado desconocido: {estado!r}")
    origen = str(cuerpo.get("origen", "")).strip()
    destino = str(cuerpo.get("destino", "")).strip()
    if not origen or not destino or origen == destino:
        abort(400, "hacen falta un origen y un destino distintos")

    rev.anotar_conexion(documento, corrida, str(cuerpo.get("pagina", "")),
                        origen, destino, estado,
                        str(cuerpo.get("nota") or ""), _quien())
    return jsonify({"ok": True})


@app.post("/api/conexiones/proponer")
def api_conexiones_proponer():
    """Lanza la lectura del plano y guarda lo que proponga, sin confirmar nada.

    Es un trabajo largo —unas 50 llamadas al modelo de visión— así que va por
    el mismo carril que el troceo y se sigue por el log.
    """
    cuerpo = request.get_json(silent=True) or {}
    documento = _documento_valido(str(cuerpo.get("documento", "")))
    corrida = _corrida_valida(documento, str(cuerpo.get("corrida") or ""))
    pagina = str(cuerpo.get("pagina", "1"))

    pdf = next((os.path.join(disco.raiz_corpus(), d["relativa"])
                for d in disco.documentos_ingestables()
                if d["nombre"] == documento), None)
    if not pdf or not pdf.lower().endswith(".pdf"):
        abort(400, "la propuesta de conexiones solo corre sobre un PDF del corpus")

    def cuerpo_trabajo(log):
        sys.path.insert(0, os.path.join(RAIZ_INGESTION, "scripts"))
        from proponer_conexiones import analizar_plano

        r = analizar_plano(pdf, int(pagina), log=log)
        nuevas = rev.guardar_conexiones(documento, corrida, pagina, r["aristas"])
        log(f"guardadas {nuevas} aristas nuevas "
            f"(las ya revisadas no se tocan)")
        if r["sin_reconocer"]:
            log("nombres que no se pudieron llevar a un código del catálogo: "
                + ", ".join(r["sin_reconocer"]))
        return {"aristas": len(r["aristas"]), "nuevas": nuevas,
                "sin_reconocer": r["sin_reconocer"]}

    try:
        t = trabajos.lanzar_funcion(
            "conexiones", f"leyendo conexiones de {documento} p.{pagina}",
            cuerpo_trabajo, {"documento": documento, "corrida": corrida})
    except trabajos.EnCurso as e:
        return jsonify({"ok": False, "motivo": str(e)}), 409
    return jsonify({"ok": True, "etiqueta": t.etiqueta})


@app.post("/api/grafo")
def api_grafo():
    """Reconstruye el grafo de componentes que la API inyecta en el prompt.

    Son dos pasos y van en ese orden: el primero lo reescribe desde el
    inventario y lo confirmado en esta pantalla, el segundo le agrega lo que
    extrae de los textos. Correr solo el segundo deja un grafo a medias.

    Hay que correrlo después de confirmar conexiones: hasta entonces el grafo
    sigue afirmándole al modelo lo que leyó mal la pasada de visión.
    """
    py = trabajos.python_del_entorno()
    try:
        t = trabajos.lanzar_secuencia(
            "grafo", "reconstruyendo el grafo de componentes",
            [[py, "scripts/construir_grafo.py"],
             [py, "scripts/extraer_relaciones.py", "--escribir"]],
            RAIZ_INGESTION, {})
    except trabajos.EnCurso as e:
        return jsonify({"ok": False, "motivo": str(e)}), 409
    return jsonify({"ok": True, "etiqueta": t.etiqueta})


@app.post("/api/desplegar")
def api_desplegar():
    """Sube el código y el índice a la demo y levanta el stack allá.

    Es el único botón de la pantalla que se ve desde afuera: hasta acá todo
    pasaba en esta máquina. Si la instancia está apagada, el script la
    enciende.
    """
    guion = os.path.join(os.path.dirname(RAIZ_INGESTION), "deploy", "upload-data.sh")
    if not os.path.isfile(guion):
        abort(400, "no encuentro deploy/upload-data.sh")
    try:
        t = trabajos.lanzar_comando(
            "desplegar", "subiendo a la demo", ["bash", guion],
            os.path.dirname(RAIZ_INGESTION), {})
    except trabajos.EnCurso as e:
        return jsonify({"ok": False, "motivo": str(e)}), 409
    return jsonify({"ok": True, "etiqueta": t.etiqueta})


@app.get("/api/trabajo")
def api_trabajo():
    t = trabajos.actual()
    if t is None:
        return jsonify({"hay": False})
    try:
        desde = int(request.args.get("desde", 0))
    except ValueError:
        desde = 0
    return jsonify({"hay": True, **t.instantanea(desde)})


@app.post("/api/trabajo/cancelar")
def api_cancelar():
    t = trabajos.actual()
    if t is None or t.estado != "corriendo":
        return jsonify({"ok": False, "motivo": "no hay nada corriendo"}), 409
    return jsonify({"ok": t.cancelar()})


# ------------------------------------------------------------------ arranque

def main() -> int:
    import argparse

    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("--puerto", type=int, default=5055)
    p.add_argument("--host", default="127.0.0.1",
                   help="por defecto solo local; exponerlo expone el botón de gastar")
    args = p.parse_args()

    if args.host not in ("127.0.0.1", "localhost"):
        print(f"⚠️  escuchando en {args.host}: esta pantalla lanza el pipeline y "
              f"gasta plata, y no pide contraseña.")
    print(f"\n  Revisión de la ingesta: http://127.0.0.1:{args.puerto}")
    print(f"  corridas: {disco.raiz_chunks()}")
    print(f"  corpus  : {disco.raiz_corpus()}")
    print(f"  base    : {rev.ruta_db()}\n")
    app.run(host=args.host, port=args.puerto, debug=False, threaded=True)
    return 0
