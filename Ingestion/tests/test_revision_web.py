"""
Invariantes de la pantalla de revisión.

Está aparte de `test_ingestion_invariants.py` porque necesita Flask, que es
una dependencia solo de la pantalla: la ingesta y la revisión por consola
andan sin él, y el resto de la suite no se puede caer por eso.

Correr desde Ingestion/:
    ./.venv/bin/python -m pytest tests/test_revision_web.py -q
"""
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

pytest.importorskip("flask", reason="la pantalla de revisión necesita Flask")


@pytest.fixture
def pantalla(tmp_path, monkeypatch):
    """La pantalla sobre una corrida falsa y una base nueva."""
    monkeypatch.setenv("INGESTION_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REVISION_DB_PATH", str(tmp_path / "revisiones.sqlite3"))
    monkeypatch.setenv("REVISOR", "prueba")

    base = tmp_path / "chunks_data" / "doc" / "20260101_000000"
    (base / "crops").mkdir(parents=True)
    recorte = base / "crops" / "doc_p1_image_1.png"
    from PIL import Image
    Image.new("RGB", (60, 40), "white").save(recorte)

    carpeta = base / "doc_1"
    carpeta.mkdir()
    (carpeta / "doc_1_chunk_1.json").write_text(json.dumps({
        "page_num": 1, "chunk_id": "chunk_1", "content_type": "text",
        "original_chunk": "texto de la página uno"}), encoding="utf-8")
    (carpeta / "doc_1_chunk_2_visual.json").write_text(json.dumps({
        "page_num": 1, "chunk_id": "chunk_2_visual", "file_name": "doc.pdf",
        "content_type": "diagram_visual",
        "original_chunk": json.dumps({
            "image_path": str(recorte),
            "notes": {"description": "la chapa dice Conexión A/Y"}})}),
        encoding="utf-8")

    (tmp_path / "raw_data").mkdir()
    (tmp_path / "raw_data" / "doc.pdf").write_bytes(b"%PDF-1.4\n")

    from revision_web import servidor, trabajos
    from task_utils import revision_store
    trabajos._actual = None
    servidor.app.config["TESTING"] = True
    return servidor.app.test_client(), revision_store, trabajos, servidor


def _json(respuesta):
    return json.loads(respuesta.data)


# ───────────────────────────────────────────────── lo que la pantalla muestra

def test_la_figura_llega_con_su_recorte_servible(pantalla):
    """El punto entero de la pantalla es ver el recorte al lado de su
    descripción: sin la URL de la imagen, es la vista de consola con colores."""
    cliente, _, _, _ = pantalla
    d = _json(cliente.get("/api/corrida/doc"))
    figura = next(c for c in d["chunks"] if c["visual"])
    assert figura["imagen"].startswith("/media/chunks_data/")
    assert "Conexión A/Y" in figura["texto"]
    assert cliente.get(figura["imagen"]).status_code == 200


def test_no_se_puede_salir_de_data_por_la_ruta_del_media(pantalla, tmp_path):
    """`/media` publica una carpeta que está al lado de los .env del proyecto.

    Se llama a la vista directamente y no por HTTP a propósito: Werkzeug
    normaliza los `..` de la URL antes de enrutar, así que un pedido con `..`
    da 404 aunque la vista no revise nada, y la prueba pasaría igual sin la
    guardia. El `..` que importa no viene de la URL de todos modos: viene del
    `image_path` de un JSON generado por el modelo.
    """
    from werkzeug.exceptions import NotFound
    _, _, _, servidor = pantalla
    afuera = tmp_path.parent / "secreto.env"
    afuera.write_text("API_KEY=xxx", encoding="utf-8")

    with servidor.app.test_request_context("/media/x"):
        with pytest.raises(NotFound):
            servidor.media("../" + afuera.name)


def test_un_documento_que_no_existe_no_llega_al_join(pantalla):
    cliente, _, _, _ = pantalla
    assert cliente.get("/api/corrida/..").status_code == 404
    assert cliente.get("/api/corrida/doc?corrida=..").status_code == 404


# ──────────────────────────────────────────────────────── veredictos y compuerta

def test_aprobar_se_frena_igual_que_en_consola(pantalla):
    """Las dos pantallas escriben la misma base. Si el navegador aprobara con
    trabajo pendiente, el veredicto quedaría anotado y el chunk se indexaría
    igual: exactamente lo que la compuerta existe para impedir."""
    cliente, store, _, _ = pantalla
    cliente.post("/api/veredicto", json={
        "documento": "doc", "corrida": "20260101_000000", "pagina": "1",
        "chunk_id": "chunk_2_visual", "veredicto": "redescribir"})

    r = cliente.post("/api/decidir", json={
        "documento": "doc", "corrida": "20260101_000000", "estado": "aprobada"})
    assert r.status_code == 409
    assert _json(r)["pendientes"][0]["chunk_id"] == "chunk_2_visual"
    assert store.estado("doc", "20260101_000000")["estado"] == "pendiente"

    cliente.post("/api/veredicto", json={
        "documento": "doc", "corrida": "20260101_000000", "pagina": "1",
        "chunk_id": "chunk_2_visual", "veredicto": "corregido",
        "texto_revisado": "la chapa dice Conexión Δ/Y"})
    r = cliente.post("/api/decidir", json={
        "documento": "doc", "corrida": "20260101_000000", "estado": "aprobada"})
    assert r.status_code == 200
    assert store.correcciones("doc", "20260101_000000") == {
        ("1", "chunk_2_visual"): "la chapa dice Conexión Δ/Y"}


def test_un_veredicto_inventado_se_rechaza(pantalla):
    cliente, _, _, _ = pantalla
    r = cliente.post("/api/veredicto", json={
        "documento": "doc", "corrida": "20260101_000000", "pagina": "1",
        "chunk_id": "chunk_1", "veredicto": "mas o menos"})
    assert r.status_code == 400


# ───────────────────────────────────────────────────── lo que lanzan los botones

def test_el_boton_de_trocear_nunca_indexa(pantalla, monkeypatch):
    """El sentido de la pantalla es que nada entre al índice sin que alguien
    lo haya mirado. Si el botón de procesar corriera el pipeline entero, la
    revisión llegaría después de los hechos."""
    cliente, _, trabajos, _ = pantalla
    lanzados = []
    monkeypatch.setattr(trabajos, "lanzar_comando",
                        lambda c, e, argv, cwd, ctx=None: lanzados.append(argv)
                        or _trabajo_falso(trabajos, c, e, ctx))

    cliente.post("/api/procesar", json={"documento": "doc", "filtro": "doc.pdf"})
    assert "--solo-chunks" in lanzados[0]
    assert "--reusar-chunks" not in lanzados[0]
    # Y `--forzar`, o el botón terminaría "bien" sin haber troceado nada:
    # el manifiesto saltea un documento cuyo hash no cambió.
    assert "--forzar" in lanzados[0]


def test_el_boton_de_indexar_siempre_exige_revision(pantalla, monkeypatch):
    """Sin `--exigir-revision` el botón indexaría una corrida pendiente, y el
    gesto de aprobar no significaría nada."""
    cliente, _, trabajos, _ = pantalla
    lanzados = []
    monkeypatch.setattr(trabajos, "lanzar_comando",
                        lambda c, e, argv, cwd, ctx=None: lanzados.append(argv)
                        or _trabajo_falso(trabajos, c, e, ctx))

    cliente.post("/api/indexar", json={"documento": "doc", "filtro": "doc.pdf"})
    assert "--exigir-revision" in lanzados[0]
    assert "--reusar-chunks" in lanzados[0]


def test_el_filtro_lleva_la_extension_para_no_procesar_dos_documentos(pantalla):
    """`--solo` matchea por subcadena: "Plano distribucion electrica" también
    agarra "1_Plano distribucion electrica_v1.pdf", y trocear dos documentos
    cuando se pidió uno cuesta horas de modelo de visión."""
    cliente, _, _, _ = pantalla
    d = _json(cliente.get("/api/documentos"))
    doc = next(x for x in d["documentos"] if x["nombre"] == "doc")
    assert doc["filtro"] == "doc.pdf"


def test_no_arrancan_dos_trabajos_a_la_vez(pantalla):
    """Dos troceos del mismo documento escriben la misma carpeta y dos
    indexados el mismo índice de Chroma. Encolar escondería el error: la
    persona cree que apretó una vez."""
    cliente, _, trabajos, _ = pantalla
    _trabajo_falso(trabajos, "procesar", "algo largo", {}, corriendo=True)

    r = cliente.post("/api/procesar", json={"documento": "doc", "filtro": "doc.pdf"})
    assert r.status_code == 409
    assert "algo largo" in _json(r)["motivo"]
    r = cliente.post("/api/indexar", json={"documento": "doc", "filtro": "doc.pdf"})
    assert r.status_code == 409


def _trabajo_falso(trabajos, clase, etiqueta, contexto, corriendo=False):
    t = trabajos.Trabajo(clase, etiqueta, contexto)
    if not corriendo:
        t._terminar("ok")
    trabajos._actual = t
    return t


# ─────────────────────────────────────────────────────────── relectura de figuras

def test_releer_propone_pero_no_pisa_nada(pantalla, monkeypatch):
    """La relectura es una propuesta, no una corrección: aplicarla es un
    segundo gesto de la persona. Y mientras tanto la figura queda marcada,
    así que cerrar la pestaña a mitad no deja la corrida aprobable."""
    cliente, store, trabajos, _ = pantalla
    sys.path.insert(0, os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "scripts")))
    import redescribir_figuras_con_valores as R

    monkeypatch.setattr(R, "cliente_llm", lambda *a, **k: type("L", (), {"model": "falso"})())
    monkeypatch.setattr(R, "redescribir_figura", lambda fig, llm, solo_conexiones=False: {
        **fig, "descripcion_nueva": {"resumen": "la chapa dice Conexión Δ/Y"},
        "mosaicos": 4, "en_disputa": [], "descartado": []})

    r = cliente.post("/api/releer", json={
        "documento": "doc", "corrida": "20260101_000000",
        "pagina": "1", "chunk_id": "chunk_2_visual"})
    assert r.status_code == 200

    limite = time.time() + 10
    while trabajos.actual().estado == "corriendo" and time.time() < limite:
        time.sleep(0.05)
    t = trabajos.actual()
    assert t.estado == "ok", t.error

    assert t.resultado["ahora"] == "la chapa dice Conexión Δ/Y"
    assert "Conexión A/Y" in t.resultado["antes"]
    # Propuesta: todavía no hay corrección guardada, pero sí trabajo pendiente.
    assert store.correcciones("doc", "20260101_000000") == {}
    assert [p["chunk_id"] for p in store.trabajo_pendiente("doc", "20260101_000000")] \
        == ["chunk_2_visual"]


def test_no_se_relee_un_chunk_sin_recorte(pantalla):
    cliente, _, _, _ = pantalla
    r = cliente.post("/api/releer", json={
        "documento": "doc", "corrida": "20260101_000000",
        "pagina": "1", "chunk_id": "chunk_1"})
    assert r.status_code == 400


# ──────────────────────────────────────────────────────────────────── el log

def test_el_log_no_remanda_lo_que_el_cliente_ya_tiene(pantalla):
    """Una ingesta imprime decenas de miles de líneas y la pantalla sondea
    cada segundo: remandar el log entero cada vez lo vuelve inusable."""
    _, _, trabajos, _ = pantalla
    t = trabajos.Trabajo("procesar", "x")
    for i in range(5):
        t.log(f"linea {i}")
    assert [l for l in t.instantanea(0)["lineas"]] == [f"linea {i}" for i in range(5)]
    assert t.instantanea(3)["lineas"] == ["linea 3", "linea 4"]
    assert t.instantanea(5)["lineas"] == []


def test_si_el_buffer_rota_se_avisa_en_vez_de_mentir(pantalla, monkeypatch):
    """Mandar un pedazo del medio como si fuera la continuación deja un log
    que parece completo y no lo es."""
    _, _, trabajos, _ = pantalla
    monkeypatch.setattr(trabajos, "MAX_LINEAS", 3)
    t = trabajos.Trabajo("procesar", "x")
    for i in range(10):
        t.log(f"linea {i}")
    foto = t.instantanea(0)
    assert foto["perdidas"] == 7
    assert foto["lineas"] == ["linea 7", "linea 8", "linea 9"]
    assert foto["total"] == 10
