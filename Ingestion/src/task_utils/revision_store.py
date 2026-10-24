"""
Estado de revisión humana de una corrida de ingesta
===================================================

Entre trocear y indexar falta un paso: que alguien mire lo que quedó. Los
cuatro reportes que el pipeline ya genera —quality, validation, technical,
multimodal— miden estructura, no verdad: cuentan chunks, deduplican, controlan
longitudes y confianza. La chapa del motor pasó los cuatro como válida mientras
decía "Conexión A/Y" en vez de Δ/Y, "MT 160-4" en vez de MT 802-4 y "230/380"
en vez de 220/380. Ningún chequeo de forma podía agarrar eso.

Qué guarda
----------
Dos tablas, con la misma identidad que usa el índice: un chunk es
`(documento, página, chunk_id)` y una corrida es la carpeta con timestamp que
dejó el troceo en `chunks_data/<documento>/<corrida>/`.

- `corridas`:   el estado del documento entero (pendiente/aprobada/rechazada)
- `veredictos`: qué se decidió sobre cada artefacto

Por qué el veredicto es una acción y no un sí/no
------------------------------------------------
Todos los defectos encontrados hasta hoy fueron UNA figura dentro de un
documento: la chapa en la página 87 de la tesis, "C4 -> DRIVER" en el
conexionado, IF01 metido en la línea de potencia del plano codificado. Nunca un
documento entero.

Si rechazar significara reingestar el documento, se vuelve a pagar el troceo
—1,17 millones de tokens de salida para un manual de 126 páginas— para
corregir un recorte. Y la reparación puntual ya existe:
`redescribir_figuras_con_valores.py` relee una figura sola y
`aplicar_planos_redescritos.py` la re-embebe y le regenera las preguntas.

Así que el veredicto dice qué hacer con ESE artefacto. Dos de ellos llegan al
pipeline: `descartar` saca el chunk, y `corregido` lo indexa con el texto que
quedó en `texto_revisado`. `redescribir` y `editar` son el mismo trabajo
todavía sin hacer, y mientras estén marcados la corrida no se puede aprobar.
"""

import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

VEREDICTOS = {
    "ok": "entra al índice tal como está",
    "redescribir": "hay que volver a leer la figura",
    "editar": "hay que corregir el texto a mano",
    "corregido": "entra al índice con el texto corregido en la revisión",
    "descartar": "no indexar este chunk",
}

# Los únicos que frenan el indexado del documento. `redescribir` y `editar` son
# trabajo pendiente de la persona: mientras estén, la corrida no está lista.
# `corregido` es ese mismo trabajo ya hecho, así que no frena nada: es el
# veredicto que queda cuando la pantalla releyó la figura o alguien reescribió
# el texto y lo guardó.
PENDIENTES_DE_ACCION = ("redescribir", "editar")

ESTADOS = ("pendiente", "aprobada", "rechazada")

_RUTA_POR_DEFECTO = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "revisiones.sqlite3"
)


def ruta_db() -> str:
    return os.path.abspath(os.getenv("REVISION_DB_PATH", _RUTA_POR_DEFECTO))


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conectar() -> sqlite3.Connection:
    ruta = ruta_db()
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    con = sqlite3.connect(ruta)
    con.row_factory = sqlite3.Row
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS corridas (
            documento     TEXT NOT NULL,
            corrida       TEXT NOT NULL,
            sha256        TEXT,
            registrada_en TEXT NOT NULL,
            estado        TEXT NOT NULL,
            decidida_en   TEXT,
            quien         TEXT,
            nota          TEXT,
            PRIMARY KEY (documento, corrida)
        );
        CREATE TABLE IF NOT EXISTS veredictos (
            documento TEXT NOT NULL,
            corrida   TEXT NOT NULL,
            pagina    TEXT NOT NULL,
            chunk_id  TEXT NOT NULL,
            veredicto TEXT NOT NULL,
            nota      TEXT,
            quien     TEXT,
            cuando    TEXT NOT NULL,
            PRIMARY KEY (documento, corrida, pagina, chunk_id)
        );
        """
    )
    # La corrección vino después que la tabla, y hay bases con veredictos ya
    # cargados: agregarla al CREATE no alcanza, porque ese CREATE no se vuelve
    # a ejecutar sobre una tabla que ya existe.
    columnas = {f["name"] for f in con.execute("PRAGMA table_info(veredictos)")}
    if "texto_revisado" not in columnas:
        con.execute("ALTER TABLE veredictos ADD COLUMN texto_revisado TEXT")
    return con


def registrar_corrida(documento: str, corrida: str, sha256: Optional[str] = None) -> str:
    """Deja constancia de que hay una corrida para revisar. Devuelve su estado.

    Es idempotente a propósito: volver a correr el troceo del mismo documento
    no debe borrar los veredictos ya cargados sobre esa misma carpeta.
    """
    with _conectar() as con:
        fila = con.execute(
            "SELECT estado FROM corridas WHERE documento=? AND corrida=?",
            (documento, corrida),
        ).fetchone()
        if fila:
            return fila["estado"]
        con.execute(
            "INSERT INTO corridas (documento, corrida, sha256, registrada_en, estado) "
            "VALUES (?,?,?,?,'pendiente')",
            (documento, corrida, sha256, _ahora()),
        )
    return "pendiente"


def anotar(documento: str, corrida: str, pagina: Any, chunk_id: str,
           veredicto: str, nota: str = "", quien: str = "",
           texto_revisado: Optional[str] = None) -> None:
    """Deja el veredicto de un artefacto, y opcionalmente su texto corregido.

    `texto_revisado` tiene tres valores con tres significados distintos:
    `None` deja la corrección como estaba —marcar "ok" sobre algo ya corregido
    no debe borrar la corrección—, una cadena la reemplaza, y `""` la borra.

    Cambiar un veredicto sobre una corrida ya aprobada la devuelve a
    pendiente. La aprobación fue sobre un estado concreto: si después se marca
    una figura para rehacer, o se descarta un chunk, o se corrige un texto, lo
    que se aprobó ya no es lo que se indexaría. Sin esto la pantalla mostraba
    "aprobada" y "1 marcado para rehacer" al mismo tiempo, y `--exigir-revision`
    le habría dado paso.
    """
    if veredicto not in VEREDICTOS:
        raise ValueError(f"veredicto desconocido: {veredicto!r}; "
                         f"son {', '.join(VEREDICTOS)}")
    with _conectar() as con:
        previo = con.execute(
            "SELECT veredicto, texto_revisado FROM veredictos "
            "WHERE documento=? AND corrida=? AND pagina=? AND chunk_id=?",
            (documento, corrida, str(pagina), chunk_id),
        ).fetchone()
        con.execute(
            "INSERT INTO veredictos "
            "(documento, corrida, pagina, chunk_id, veredicto, nota, quien, "
            " cuando, texto_revisado) "
            "VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(documento, corrida, pagina, chunk_id) DO UPDATE SET "
            "veredicto=excluded.veredicto, nota=excluded.nota, "
            "quien=excluded.quien, cuando=excluded.cuando, "
            "texto_revisado=COALESCE(excluded.texto_revisado, "
            "                        veredictos.texto_revisado)",
            (documento, corrida, str(pagina), chunk_id, veredicto, nota, quien,
             _ahora(), texto_revisado),
        )
        # Solo si cambió el dictamen: retocar la nota no invalida nada.
        cambio = (previo is None
                  or previo["veredicto"] != veredicto
                  or (texto_revisado is not None
                      and texto_revisado != (previo["texto_revisado"] or "")))
        if cambio:
            con.execute(
                "UPDATE corridas SET estado='pendiente', decidida_en=NULL "
                "WHERE documento=? AND corrida=? AND estado='aprobada'",
                (documento, corrida),
            )


def veredictos(documento: str, corrida: str) -> Dict[tuple, Dict[str, Any]]:
    """`{(pagina, chunk_id): {...}}` de lo ya revisado en esa corrida."""
    with _conectar() as con:
        filas = con.execute(
            "SELECT * FROM veredictos WHERE documento=? AND corrida=?",
            (documento, corrida),
        ).fetchall()
    return {(f["pagina"], f["chunk_id"]): dict(f) for f in filas}


def descartados(documento: str, corrida: str) -> set:
    """Los `(pagina, chunk_id)` que NO deben indexarse."""
    return {k for k, v in veredictos(documento, corrida).items()
            if v["veredicto"] == "descartar"}


def correcciones(documento: str, corrida: str) -> Dict[tuple, str]:
    """`{(pagina, chunk_id): texto}` de lo que una persona reescribió.

    El texto corregido vive acá y no en el JSON del troceo a propósito: ese
    JSON es la captura cruda de lo que leyó el modelo, y pisarlo borraría la
    evidencia de qué había que corregir. La compuerta lo inyecta en el chunk
    justo antes de enriquecer, en el mismo lugar donde saca los descartados.
    """
    return {k: v["texto_revisado"]
            for k, v in veredictos(documento, corrida).items()
            if (v.get("texto_revisado") or "").strip()}


def decidir(documento: str, corrida: str, estado: str,
            quien: str = "", nota: str = "") -> None:
    if estado not in ESTADOS:
        raise ValueError(f"estado desconocido: {estado!r}; son {', '.join(ESTADOS)}")
    with _conectar() as con:
        con.execute(
            "UPDATE corridas SET estado=?, decidida_en=?, quien=?, nota=? "
            "WHERE documento=? AND corrida=?",
            (estado, _ahora(), quien, nota, documento, corrida),
        )


def estado(documento: str, corrida: str) -> Optional[Dict[str, Any]]:
    with _conectar() as con:
        fila = con.execute(
            "SELECT * FROM corridas WHERE documento=? AND corrida=?",
            (documento, corrida),
        ).fetchone()
    return dict(fila) if fila else None


def trabajo_pendiente(documento: str, corrida: str) -> List[Dict[str, Any]]:
    """Artefactos marcados para rehacer y todavía no rehechos.

    Aprobar con estos encima sería aprobar lo que ya se dijo que está mal.
    """
    return [v for v in veredictos(documento, corrida).values()
            if v["veredicto"] in PENDIENTES_DE_ACCION]


def corridas(estado_filtro: Optional[str] = None) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM corridas"
    args: tuple = ()
    if estado_filtro:
        sql += " WHERE estado=?"
        args = (estado_filtro,)
    sql += " ORDER BY registrada_en DESC"
    with _conectar() as con:
        return [dict(f) for f in con.execute(sql, args).fetchall()]


def ultima_aprobada(documento: str) -> Optional[str]:
    """La corrida aprobada más reciente de un documento, para comparar contra ella."""
    with _conectar() as con:
        fila = con.execute(
            "SELECT corrida FROM corridas WHERE documento=? AND estado='aprobada' "
            "ORDER BY decidida_en DESC LIMIT 1",
            (documento,),
        ).fetchone()
    return fila["corrida"] if fila else None
