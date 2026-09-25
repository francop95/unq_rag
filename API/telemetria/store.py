"""
Registro de ejecuciones y feedback
==================================

Guarda, por cada consulta, qué recuperó el sistema y qué respondió, y permite
que quien pregunta diga si la respuesta sirvió. Las dos mitades comparten el
`query_id`, que es lo que hace útil al feedback: sin saber qué chunks llegaron
y con qué score, un "la respuesta está mal" no se puede accionar.

Qué se guarda y por qué
-----------------------
Los modos de falla que este sistema tuvo en producción no se distinguen entre
sí mirando solo la respuesta:

- el fragmento correcto no se recuperó         → hay que mirar los scores
- se recuperó pero quedó fuera del top-k       → hay que mirar la posición
- se recuperó y el modelo no lo usó            → hay que mirar qué citó
- se recuperó de otro documento parecido       → hay que mirar el file_name
- la imagen mostrada no correspondía           → hay que mirar el media_path

Por eso se registra una fila por chunk recuperado con su posición, su score, si
el modelo lo citó y si traía imagen. Es la diferencia entre "no anduvo" y saber
en qué etapa se rompió.

Dónde
-----
SQLite, en un archivo aparte del índice. No comparte base con Chroma a
propósito: un problema escribiendo telemetría no puede poder dejar sin servicio
al retrieval.

Nada de esto puede romper una consulta. Todas las escrituras van envueltas: si
la telemetría falla, se registra en el log y la respuesta sigue su camino.
"""

import json
import logging
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("app.Telemetria")

_lock = threading.Lock()

RUTA_POR_DEFECTO = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..",
    "Ingestion", "data", "telemetria", "ejecuciones.sqlite3",
)


def ruta_db() -> str:
    return os.path.abspath(os.getenv("TELEMETRY_DB_PATH", RUTA_POR_DEFECTO))


# Motivos por los que una respuesta puede no servir. La lista sale de los fallos
# que este sistema tuvo de verdad, no de una lluvia de ideas: cada uno apunta a
# una etapa distinta del pipeline, que es lo que lo hace accionable.
MOTIVOS = [
    {"id": "respuesta_erronea",
     "etiqueta": "La respuesta es incorrecta",
     "ayuda": "Dice algo que contradice a los documentos"},
    {"id": "respuesta_incompleta",
     "etiqueta": "La respuesta está incompleta",
     "ayuda": "Falta información que sí está en los documentos"},
    {"id": "contexto_ausente",
     "etiqueta": "El fragmento que buscaba no aparece",
     "ayuda": "Ninguna de las fuentes listadas es la que corresponde"},
    {"id": "contexto_mal_rankeado",
     "etiqueta": "El fragmento correcto está, pero al final",
     "ayuda": "Aparece en la lista, en una posición baja"},
    {"id": "documento_equivocado",
     "etiqueta": "Responde desde el documento equivocado",
     "ayuda": "La información sale de otro manual o versión"},
    {"id": "fuentes_no_respaldan",
     "etiqueta": "Las fuentes no respaldan la respuesta",
     "ayuda": "Lo que dice no está en los fragmentos mostrados"},
    {"id": "sin_respuesta",
     "etiqueta": "Dice que no sabe y la información existe",
     "ayuda": "El documento tiene el dato pero no lo encontró"},
    {"id": "imagen_irrelevante",
     "etiqueta": "La imagen no corresponde",
     "ayuda": "Se muestra una figura que no tiene que ver"},
    {"id": "imagen_cortada",
     "etiqueta": "La imagen está mal recortada",
     "ayuda": "Se ve cortada o falta parte del contenido"},
    {"id": "otro",
     "etiqueta": "Otro motivo",
     "ayuda": "Contalo en el comentario"},
]

IDS_MOTIVOS = {m["id"] for m in MOTIVOS}

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS ejecuciones (
    query_id        TEXT PRIMARY KEY,
    creada_en       TEXT NOT NULL,
    pregunta        TEXT,
    respuesta       TEXT,
    conv_id         TEXT,
    es_followup     INTEGER,
    pregunta_reformulada TEXT,
    respondio       INTEGER,   -- el LLM dio una respuesta por buena
    desde_cache     INTEGER,
    indice          TEXT,
    modelo_respuesta TEXT,
    proveedor_embeddings TEXT,
    modelo_embeddings TEXT,
    dimension_embeddings INTEGER,
    top_k           INTEGER,
    umbral_similitud REAL,
    gate_cruzado    INTEGER,
    n_contextos     INTEGER,
    n_con_imagen    INTEGER,
    score_maximo    REAL,
    tiempos_json    TEXT,
    extra_json      TEXT
);

CREATE TABLE IF NOT EXISTS contextos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    query_id        TEXT NOT NULL,
    posicion        INTEGER NOT NULL,   -- 1 = el más relevante
    file_name       TEXT,
    page_num        TEXT,
    chunk_id        TEXT,
    content_type    TEXT,
    score           REAL,
    citado          INTEGER,            -- el modelo lo citó explícitamente
    mostrado        INTEGER,            -- salió en las fuentes de la respuesta
    media_path      TEXT,
    texto           TEXT,
    FOREIGN KEY (query_id) REFERENCES ejecuciones(query_id)
);
CREATE INDEX IF NOT EXISTS idx_contextos_query ON contextos(query_id);

CREATE TABLE IF NOT EXISTS feedback (
    id              TEXT PRIMARY KEY,
    query_id        TEXT NOT NULL,
    creado_en       TEXT NOT NULL,
    util            INTEGER NOT NULL,   -- 1 sirvió, 0 no sirvió
    motivos_json    TEXT,
    comentario      TEXT,
    FOREIGN KEY (query_id) REFERENCES ejecuciones(query_id)
);
CREATE INDEX IF NOT EXISTS idx_feedback_query ON feedback(query_id);
CREATE INDEX IF NOT EXISTS idx_feedback_util  ON feedback(util);
"""


def _conectar() -> sqlite3.Connection:
    ruta = ruta_db()
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    con = sqlite3.connect(ruta, timeout=10.0)
    # WAL: la API corre con varios workers de gunicorn y el modo por defecto
    # serializa lectores contra escritores. Acá las escrituras son chicas y
    # frecuentes, y una consulta no puede esperar por ellas.
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(_ESQUEMA)
    return con


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


def _texto(v: Any, tope: int = 2000) -> Optional[str]:
    if v is None:
        return None
    s = str(v)
    return s[:tope]


def registrar_ejecucion(data: Dict[str, Any],
                        respuesta: Optional[Dict[str, Any]] = None,
                        tiempos: Optional[Dict[str, float]] = None,
                        desde_cache: bool = False) -> None:
    """
    Deja registrada una consulta con todo lo que hizo falta para responderla.

    Se llama al final del flujo, cuando ya existen el contexto recuperado y la
    respuesta. Nunca lanza: una consulta respondida no se puede perder porque
    falle el registro.
    """
    try:
        _registrar(data, respuesta, tiempos, desde_cache)
    except Exception as e:  # pragma: no cover - defensivo a propósito
        logger.warning(f"[Telemetría] no se pudo registrar la ejecución: {e}")


def _registrar(data, respuesta, tiempos, desde_cache) -> None:
    query_id = str(data.get("query_id") or "")
    if not query_id:
        return

    # El dataframe con los chunks recuperados vive en `context_dataframe`; la
    # lista `context_dataframes` acumula uno por fuente cuando hay varias. Se
    # toma el primero no vacío.
    df = data.get("context_dataframe")
    if df is None or getattr(df, "empty", True):
        for cand in (data.get("context_dataframes") or []):
            if cand is not None and not getattr(cand, "empty", True):
                df = cand
                break
    filas: List[tuple] = []
    n_con_imagen = 0
    score_max = None

    # Qué fuentes terminó mostrando la respuesta, para poder distinguir
    # "no se recuperó" de "se recuperó y no se mostró".
    mostrados = set()
    citados = set()
    if isinstance(respuesta, list) and respuesta:
        r0 = respuesta[0] if isinstance(respuesta[0], dict) else {}
        for s in (r0.get("sources") or []):
            clave = (str(s.get("file_name") or ""), str(s.get("page") or ""))
            mostrados.add(clave)

    if df is not None and getattr(df, "empty", True) is False:
        for pos, (_, row) in enumerate(df.iterrows(), start=1):
            def col(*nombres, defecto=None):
                for n in nombres:
                    if n in row and row[n] is not None:
                        return row[n]
                return defecto

            score = col("Similarity Score", "similarity", defecto=None)
            try:
                score = float(score) if score is not None else None
            except (TypeError, ValueError):
                score = None
            if score is not None:
                score_max = score if score_max is None else max(score_max, score)

            media = col("media_path", "image_path")
            if media:
                n_con_imagen += 1

            fname = str(col("File Name", "file_name", defecto="") or "")
            page = str(col("Page Number", "page_num", defecto="") or "")
            filas.append((
                query_id, pos, fname, page,
                _texto(col("chunk_id"), 200),
                _texto(col("content_type"), 60),
                score,
                1 if (fname, page) in citados else 0,
                1 if (fname, page) in mostrados else 0,
                _texto(media, 500),
                _texto(col("Text", "text"), 2000),
            ))

    r0 = {}
    if isinstance(respuesta, list) and respuesta and isinstance(respuesta[0], dict):
        r0 = respuesta[0]

    fila_ejec = (
        query_id, _ahora(),
        _texto(data.get("query"), 4000),
        _texto(r0.get("answer"), 20000),
        _texto(data.get("conv_id"), 200),
        # Si esta consulta llegó CON historial, no la bandera de configuración
        # `is_followup` —que solo dice si el manejo de follow-ups está activado—.
        # Registrar la bandera hacía que toda consulta figurara como follow-up y
        # el dato no distinguía nada.
        1 if (data.get("conv_history_df") is not None
              and not getattr(data.get("conv_history_df"), "empty", True)) else 0,
        _texto(data.get("updated_query"), 4000),
        1 if data.get("gpt_ans_found") else 0,
        1 if desde_cache else 0,
        _texto(data.get("chroma_index_name"), 200),
        _texto(data.get("openai_model") or data.get("model"), 100),
        _texto(data.get("embedding_provider"), 40),
        _texto(data.get("embedding_model_name") or data.get("openai_emb_model"), 100),
        int(data.get("expected_embedding_dimension") or 0) or None,
        int(data.get("chroma_top_n_contexts") or 0) or None,
        float(data.get("min_context_similarity_score") or 0) or None,
        1 if data.get("cross_doc_gate_enabled") else 0,
        len(filas), n_con_imagen, score_max,
        json.dumps(tiempos or {}, ensure_ascii=False),
        json.dumps({"similarity_score": r0.get("similarity_score"),
                    "is_valid": r0.get("is_valid"),
                    "files": r0.get("files"),
                    # La categoría del clasificador decide si hubo retrieval o si
                    # la consulta se cortó antes. Sin esto, una respuesta genérica
                    # y una respuesta con 0 resultados se ven igual.
                    "intencion": (data.get("query_intent") or {}).get("question_type"),
                    "pregunta_invalida": bool(data.get("invalid_question_found")),
                    "respuesta_generica": bool(data.get("generic_ans_found")),
                    }, ensure_ascii=False)[:4000],
    )

    with _lock:
        con = _conectar()
        try:
            con.execute(
                "INSERT OR REPLACE INTO ejecuciones VALUES (" + ",".join("?" * len(fila_ejec)) + ")",
                fila_ejec,
            )
            con.execute("DELETE FROM contextos WHERE query_id = ?", (query_id,))
            if filas:
                con.executemany(
                    "INSERT INTO contextos (query_id,posicion,file_name,page_num,chunk_id,"
                    "content_type,score,citado,mostrado,media_path,texto) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    filas,
                )
            con.commit()
        finally:
            con.close()
    logger.info(f"[{query_id}] [Telemetría] ejecución registrada: {len(filas)} contextos")


def guardar_feedback(query_id: str, util: bool,
                     motivos: Optional[List[str]] = None,
                     comentario: Optional[str] = None) -> Dict[str, Any]:
    """
    Guarda la valoración de una respuesta. Devuelve el registro creado.

    Los motivos desconocidos se descartan en vez de rechazar el envío entero:
    perder la opinión de alguien porque el frontend mandó una etiqueta vieja
    sería el peor intercambio posible.
    """
    motivos = [m for m in (motivos or []) if m in IDS_MOTIVOS]
    fid = str(uuid.uuid4())
    with _lock:
        con = _conectar()
        try:
            con.execute(
                "INSERT INTO feedback (id,query_id,creado_en,util,motivos_json,comentario) "
                "VALUES (?,?,?,?,?,?)",
                (fid, str(query_id), _ahora(), 1 if util else 0,
                 json.dumps(motivos, ensure_ascii=False), _texto(comentario, 8000)),
            )
            con.commit()
        finally:
            con.close()
    logger.info(f"[{query_id}] [Telemetría] feedback: {'útil' if util else 'no útil'} {motivos}")
    return {"id": fid, "query_id": query_id, "util": util,
            "motivos": motivos, "comentario": comentario}


def listar_feedback(limite: int = 100, solo_negativos: bool = False) -> List[Dict[str, Any]]:
    """El feedback recibido, con la pregunta y la respuesta que lo motivaron."""
    con = _conectar()
    try:
        con.row_factory = sqlite3.Row
        sql = ("SELECT f.*, e.pregunta, e.respuesta, e.n_contextos, e.score_maximo, e.indice "
               "FROM feedback f LEFT JOIN ejecuciones e ON e.query_id = f.query_id ")
        if solo_negativos:
            sql += "WHERE f.util = 0 "
        sql += "ORDER BY f.creado_en DESC LIMIT ?"
        filas = [dict(r) for r in con.execute(sql, (limite,))]
    finally:
        con.close()
    for f in filas:
        try:
            f["motivos"] = json.loads(f.pop("motivos_json") or "[]")
        except Exception:
            f["motivos"] = []
    return filas


def ver_ejecucion(query_id: str) -> Optional[Dict[str, Any]]:
    """Todo lo registrado de una consulta: la ejecución, sus contextos y su feedback."""
    con = _conectar()
    try:
        con.row_factory = sqlite3.Row
        e = con.execute("SELECT * FROM ejecuciones WHERE query_id = ?", (query_id,)).fetchone()
        if e is None:
            return None
        ejec = dict(e)
        ejec["contextos"] = [dict(r) for r in con.execute(
            "SELECT * FROM contextos WHERE query_id = ? ORDER BY posicion", (query_id,))]
        ejec["feedback"] = [dict(r) for r in con.execute(
            "SELECT * FROM feedback WHERE query_id = ? ORDER BY creado_en", (query_id,))]
    finally:
        con.close()
    for f in ejec["feedback"]:
        try:
            f["motivos"] = json.loads(f.pop("motivos_json") or "[]")
        except Exception:
            f["motivos"] = []
    for k in ("tiempos_json", "extra_json"):
        try:
            ejec[k.replace("_json", "")] = json.loads(ejec.pop(k) or "{}")
        except Exception:
            ejec[k.replace("_json", "")] = {}
    return ejec
