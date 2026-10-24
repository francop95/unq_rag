"""
Invariantes del retrieval. Cada test corresponde a un bug real que se encontró y arregló:
si alguno se rompe, ese bug volvió.

Correr con el venv de la API, desde API/:
    ./.venv/bin/python -m pytest tests/ -q
"""
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from contexts.ChromaConnector import ChromaConnection
from contexts.advanced_retrieval import ContextExpander
from qnas.ResponseCache import ResponseCache, normalize_question


def cand(text, *, dense=None, bm25=None, visual=None, fused=0.0, **meta):
    """Candidato como los que arma _chroma_res_to_candidates."""
    c = {"doc_id": meta.pop("doc_id", text[:20]), "text": text,
         "metadata": {"file_name": "d.pdf", "page_num": 1, **meta},
         "fused_score": fused}
    if dense is not None:
        c["dense_similarity"] = dense
        c["dense_rank"] = 1
    if bm25 is not None:
        c["bm25_rank"] = bm25
    if visual is not None:
        c["visual_rank"] = visual
    return c


# ─────────────────────────────────────────────────────────── dedup por contención

def test_descarta_el_texto_contenido_en_otro():
    """Un título corto contenido en un chunk más largo no debe ocupar dos lugares."""
    corto = cand("El variador no arranca desde el teclado integrado.", dense=0.90, chunk_id="c1")
    largo = cand("Síntomas comunes. El motor no arranca. "
                 "El variador no arranca desde el teclado integrado. Acción correctiva.",
                 dense=0.70, chunk_id="c2")
    out = ChromaConnection._drop_contained_duplicates([corto, largo])
    ids = [c["metadata"]["chunk_id"] for c in out]
    assert ids == ["c2"], "debe quedar el contenedor, no el fragmento"


def test_propaga_la_similitud_del_descartado_al_contenedor():
    """Sin esto, descartar el candidato mejor puntuado tumbaba el gate de relevancia."""
    corto = cand("no arranca desde el teclado", dense=0.95, chunk_id="c1")
    largo = cand("prefacio no arranca desde el teclado y mas cosas", dense=0.60, chunk_id="c2")
    out = ChromaConnection._drop_contained_duplicates([corto, largo])
    assert out[0]["dense_similarity"] == pytest.approx(0.95)


def test_nunca_descarta_un_candidato_con_media():
    """La imagen o la tabla es el aporte de esa fuente aunque su texto sea redundante."""
    con_media = cand("la figura muestra el bloque de terminales", dense=0.60,
                     chunk_id="v", media_path="img/x.png")
    contenedor = cand("intro la figura muestra el bloque de terminales fin", dense=0.80, chunk_id="t")
    out = ChromaConnection._drop_contained_duplicates([con_media, contenedor])
    assert {c["metadata"]["chunk_id"] for c in out} == {"v", "t"}


def test_ignora_los_marcadores_de_context_expansion_al_comparar():
    """Comparar el texto completo haría que dos vecinos nunca se vieran como duplicados."""
    a = cand("[CONTEXTO PREVIO]\nalgo previo\n\n[CHUNK RELEVANTE]\nel nucleo", dense=0.8, chunk_id="a")
    b = cand("[CHUNK RELEVANTE]\nprologo el nucleo epilogo", dense=0.7, chunk_id="b")
    out = ChromaConnection._drop_contained_duplicates([a, b])
    assert [c["metadata"]["chunk_id"] for c in out] == ["b"]


# ─────────────────────────────────────────────────── facetas de figura / multi-vector

@pytest.mark.parametrize("chunk_id,esperado", [
    ("chunk_3_visual", "chunk_3"),
    ("chunk_3_ocr", "chunk_3"),
    ("chunk_3_structured", "chunk_3"),
    ("chunk_3", None),
    ("chunk_3_table_part2", None),
    (None, None),
])
def test_identifica_la_figura_de_una_faceta(chunk_id, esperado):
    assert ChromaConnection._figure_base_id(chunk_id) == esperado


def test_colapsa_las_facetas_de_una_figura_y_gana_la_que_tiene_imagen():
    """La faceta OCR solía ganar por score y no tiene imagen: el diagrama no llegaba a la UI."""
    ocr = cand("Diagrama electrico - Pagina 27 Texto extraido: as a =Q", dense=0.80,
               chunk_id="chunk_3_ocr", parent_chunk_id="chunk_3_ocr", fused=0.9)
    visual = cand("El diagrama muestra las conexiones del variador", dense=0.60,
                  chunk_id="chunk_3_visual", parent_chunk_id="chunk_3_visual",
                  media_path="img/d.png", fused=0.5)
    out = ChromaConnection._collapse_by_parent([ocr, visual])
    assert len(out) == 1, "las dos facetas son la misma figura"
    assert out[0]["metadata"]["media_path"] == "img/d.png", "debe sobrevivir la que trae la imagen"


def test_no_mezcla_una_figura_con_un_chunk_de_texto_del_mismo_nombre():
    figura = cand("descripcion", dense=0.8, chunk_id="chunk_3_visual", parent_chunk_id="chunk_3_visual")
    texto = cand("otro contenido", dense=0.7, chunk_id="chunk_3", parent_chunk_id="chunk_3")
    assert len(ChromaConnection._collapse_by_parent([figura, texto])) == 2


def test_colapsa_los_vectores_de_pregunta_del_mismo_contenido():
    """Un chunk indexado con N preguntas no debe ocupar N lugares del contexto."""
    q1 = cand("contenido del padre", dense=0.9, chunk_id="c1_q1",
              parent_chunk_id="c1", question="¿una?", doc_id="q1")
    q2 = cand("contenido del padre", dense=0.8, chunk_id="c1_q2",
              parent_chunk_id="c1", question="¿otra?", doc_id="q2")
    out = ChromaConnection._collapse_by_parent([q1, q2])
    assert len(out) == 1
    assert set(out[0]["matched_questions"]) == {"¿una?", "¿otra?"}


def test_descarta_las_preguntas_de_facetas_ocr():
    """Se generaron desde el rótulo de la página, no del contenido: matchean cualquier cosa."""
    de_ocr = cand("basura", content_type="synthetic_question", parent_chunk_id="chunk_3_ocr")
    de_visual = cand("descripcion", content_type="synthetic_question", parent_chunk_id="chunk_3_visual")
    contenido = cand("texto", content_type="text", parent_chunk_id="chunk_1")
    out = ChromaConnection._drop_hallucinated_ocr_questions([de_ocr, de_visual, contenido])
    assert len(out) == 2
    assert all((c["metadata"].get("parent_chunk_id") or "") != "chunk_3_ocr" for c in out)


# ───────────────────────────────────────────────────────────── context expander

def test_no_inyecta_una_faceta_hermana_como_contexto():
    """A un diagrama se le inyectaba su propio OCR ilegible como [CONTEXTO SIGUIENTE]."""
    exp = ContextExpander()
    exp._chunk_cache = {
        "d.pdf_7_chunk_2_visual": {"file_name": "d.pdf", "page_num": 7, "chunk_id": "chunk_2_visual",
                                   "original_chunk": "descripcion", "content_type": "diagram_visual",
                                   "prev_chunk_id": "d.pdf_7_chunk_1",
                                   "next_chunk_id": "d.pdf_7_chunk_2_ocr"},
        "d.pdf_7_chunk_2_ocr": {"file_name": "d.pdf", "page_num": 7, "chunk_id": "chunk_2_ocr",
                                "original_chunk": "Texto extraido: as a =Q", "content_type": "diagram_text"},
        "d.pdf_7_chunk_1": {"file_name": "d.pdf", "page_num": 7, "chunk_id": "chunk_1",
                            "original_chunk": "contexto real de la seccion", "content_type": "text"},
    }
    prev, nxt = exp.expand("d.pdf", 7, "chunk_2_visual")
    assert "contexto real" in prev
    assert nxt == "", "el vecino era su propia faceta OCR"


def test_no_inyecta_boilerplate_de_pagina():
    """El pie de página del manual entraba al prompt por la puerta lateral del expander."""
    pie = {"file_name": "d.pdf", "content_type": "text",
           "original_chunk": "PowerFlex 4M Manual del usuario Publicacion 22F"}
    exp = ContextExpander()
    exp._chunk_cache = {
        "d.pdf_5_c1": {**pie, "page_num": 5, "chunk_id": "c1"},
        "d.pdf_9_c1": {**pie, "page_num": 9, "chunk_id": "c1"},   # el mismo texto repetido
        "d.pdf_5_c2": {"file_name": "d.pdf", "page_num": 5, "chunk_id": "c2",
                       "content_type": "text", "original_chunk": "contenido unico y util",
                       "prev_chunk_id": "d.pdf_5_c1", "next_chunk_id": ""},
    }
    exp._index_boilerplate()
    assert "d.pdf_5_c1" in exp._boilerplate_ids
    prev, _ = exp.expand("d.pdf", 5, "c2")
    assert prev == "", "el pie de página no debe inyectarse"


def test_el_contenido_unico_si_se_inyecta():
    exp = ContextExpander()
    exp._chunk_cache = {
        "d.pdf_5_c1": {"file_name": "d.pdf", "page_num": 5, "chunk_id": "c1",
                       "content_type": "text", "original_chunk": "Figura 3.6: Conexionado placa TBEN."},
        "d.pdf_5_c2": {"file_name": "d.pdf", "page_num": 5, "chunk_id": "c2",
                       "content_type": "text", "original_chunk": "otra cosa",
                       "prev_chunk_id": "d.pdf_5_c1", "next_chunk_id": ""},
    }
    exp._index_boilerplate()
    prev, _ = exp.expand("d.pdf", 5, "c2")
    assert "Figura 3.6" in prev


def test_lee_el_texto_legible_de_una_tabla_vecina():
    """Un vecino de tipo tabla inyectaba su JSON crudo como contexto."""
    chunk = {"content_type": "table",
             "original_chunk": json.dumps({"table_markdown": "| a | b |", "table_json": {}})}
    assert ContextExpander._readable_chunk_text(chunk) == "| a | b |"


# ───────────────────────────────────────────────────────────── caché de respuestas

@pytest.mark.parametrize("a,b", [
    ("El variador no arranca, ¿qué reviso?", "el variador no arranca que reviso"),
    ("¿Cuánto es el PAR de apriete?", "cuanto es el par de apriete"),
])
def test_la_clave_del_cache_normaliza_tildes_signos_y_mayusculas(a, b):
    assert normalize_question(a) == normalize_question(b)


def test_no_confunde_preguntas_distintas():
    """"no arranca" vs "no para" son la misma longitud y casi el mismo texto."""
    assert normalize_question("el variador no arranca desde el teclado") != \
           normalize_question("el variador no para desde el teclado")


def test_el_cache_devuelve_la_respuesta_guardada(tmp_path):
    cache = ResponseCache(str(tmp_path / "c.json"), index_path=str(tmp_path))
    cache.put("¿Qué reviso?", [{"answer": "P106"}])
    assert cache.get("que reviso") == [{"answer": "P106"}]


def test_el_cache_no_guarda_respuestas_vacias(tmp_path):
    """Cachear un "no encontré nada" congelaría ese fallo tras re-ingestar."""
    cache = ResponseCache(str(tmp_path / "c.json"), index_path=str(tmp_path))
    cache.put("¿Qué reviso?", [])
    assert cache.get("¿Qué reviso?") is None


def test_el_cache_se_descarta_si_el_indice_cambio(tmp_path):
    """Sin esto, una respuesta sobrevive a la re-ingesta citando páginas que ya no existen."""
    index = tmp_path / "idx"
    index.mkdir()
    (index / "a.bin").write_text("x")
    cache_file = str(tmp_path / "c.json")

    ResponseCache(cache_file, index_path=str(index)).put("q", [{"answer": "vieja"}])
    assert ResponseCache(cache_file, index_path=str(index)).get("q") is not None

    time.sleep(1.1)
    (index / "b.bin").write_text("y")          # simula re-ingesta
    assert ResponseCache(cache_file, index_path=str(index)).get("q") is None


def test_respeta_el_limite_de_entradas(tmp_path):
    cache = ResponseCache(str(tmp_path / "c.json"), max_entries=3, index_path=str(tmp_path))
    for i in range(6):
        cache.put(f"pregunta {i}", [{"answer": str(i)}])
    assert len(cache._entries) == 3


# ───────────────────────────────────────────── selección de modelo por proveedor

def test_query_intent_elige_el_modelo_del_proveedor_configurado():
    """Estaba fijo en data["gemini_model"], una clave que este proyecto no define."""
    from models.QueryIntent import QueryIntent
    base = {
        "query_id": "t", "query_intent_model_type": "openai", "query_intent_max_tokens": 100,
        "new_query_intent_prompt": "{} {}", "query_intent_sys_msg": "s",
        "query_intent_categories": ["generic", "new", "follow-up", "invalid"],
        "prev_conv_threshold": 1, "completion_failure": "Failure", "completion_success": "Success",
        "query": "q", "gpt_ans_type": "gpt", "query_intent_max_retries": 1,
        "openai_model1": "gpt-4.1",
    }
    assert QueryIntent(dict(base)).model_selected == "gpt-4.1"
    with pytest.raises(ValueError):
        QueryIntent({**base, "query_intent_model_type": "inventado"})


# ──────────────────────────────────────────── cupo de asientos para otros documentos

def test_el_cupo_entra_cuando_un_documento_llena_el_top_k():
    """Un documento grande puede llenar el top_k con chunks legítimos y dejar
    afuera al que tiene la respuesta: el caso ARGEE, donde el manual del
    fabricante (3973 vectores) tapaba al programa instalado (83)."""
    grandes = [cand(f"manual {i}", dense=0.72 - i * 0.005, file_name="manual.pdf")
               for i in range(12)]
    propio = cand("programa real", dense=0.64, file_name="programa.pdf")
    sel, entraron = ChromaConnection._aplicar_cupo_otros_documentos(
        grandes + [propio], top_k=10, cupo=2)
    docs = [c["metadata"]["file_name"] for c in sel]
    assert len(sel) == 10
    assert "programa.pdf" in docs, "el documento tapado tiene que entrar"
    assert docs.count("manual.pdf") == 9, "solo cede los asientos que hagan falta"
    assert entraron == {"programa.pdf"}


def test_el_cupo_no_desplaza_a_quien_entro_por_merito():
    """Los asientos que ceden son los del dominante, nunca los de un tercer
    documento que ya había entrado por score."""
    grandes = [cand(f"manual {i}", dense=0.90 - i * 0.01, file_name="manual.pdf")
               for i in range(9)]
    tercero = cand("tercero", dense=0.85, file_name="otro.pdf")
    tapado = cand("tapado", dense=0.50, file_name="programa.pdf")
    sel, _ = ChromaConnection._aplicar_cupo_otros_documentos(
        grandes + [tercero, tapado], top_k=10, cupo=2)
    docs = [c["metadata"]["file_name"] for c in sel]
    assert "otro.pdf" in docs and "programa.pdf" in docs
    assert docs.count("manual.pdf") == 8


def test_el_cupo_no_hace_nada_si_ya_hay_variedad():
    """Si el top_k ya trae suficientes documentos distintos, el orden por score
    no se toca: el cupo es un piso, no una cuota obligatoria."""
    cands = ([cand(f"a{i}", dense=0.9 - i * 0.01, file_name="a.pdf") for i in range(8)]
             + [cand("b", dense=0.80, file_name="b.pdf"),
                cand("c", dense=0.79, file_name="c.pdf"),
                cand("a9", dense=0.10, file_name="a.pdf")])
    sel, entraron = ChromaConnection._aplicar_cupo_otros_documentos(cands, top_k=10, cupo=2)
    assert entraron == set()
    assert [c["text"] for c in sel] == [c["text"] for c in
            sorted(cands, key=lambda c: c["dense_similarity"], reverse=True)[:10]]


def test_el_cupo_no_inventa_candidatos():
    """Si todo lo que sobrevivió a los gates es de un solo documento, el cupo
    no puede —ni debe— traer nada de otro lado."""
    cands = [cand(f"a{i}", dense=0.9 - i * 0.01, file_name="a.pdf") for i in range(15)]
    sel, entraron = ChromaConnection._aplicar_cupo_otros_documentos(cands, top_k=10, cupo=2)
    assert len(sel) == 10 and entraron == set()


# ────────────────────────────────────────── citas: qué declaró el modelo vs qué se le dio

def _retriever(context_rows):
    """RetrieverQna con lo mínimo para ejercitar parse_response, sin tocar la red."""
    import pandas as pd
    from qnas.RetrieverQna_multimodal import RetrieverQna

    r = object.__new__(RetrieverQna)
    r.query_id = "t"
    r.completion_success = "Success"
    r.text_column = "Text"
    r.filename_column = "File Name"
    r.similarity_column = "Similarity Score"
    r.page_number_column = "Page Number"
    r.default_response_dict = {"question": "q", "answer": "", "file_name": [""],
                               "page": [0], "global": [False], "similarity_score": 0,
                               "ans_type": "gpt", "sources": []}
    # Los pone __init__ y los llena get_message_prompt; acá no corre ninguno
    # de los dos, así que van explícitos en vez de con getattr defensivo en
    # producción: que parse_response dependa de ellos es parte del contrato.
    r.glosario_ctx_id = None
    r.glosario_texto = ""
    r.context_df = pd.DataFrame(context_rows)
    return r


def _fila(nombre, pagina, score, media=None):
    return {"File Name": nombre, "Page Number": pagina, "Similarity Score": score,
            "Text": f"texto de {nombre}", "content_type": "diagram_visual" if media else "text",
            "media_path": media, "image_path": None}


def _respuesta(referred, contexto):
    """Corre parse_response con la respuesta cruda que habría devuelto el LLM."""
    r = _retriever(contexto)
    data = {"gpt_response_raw": [{"res_status": "Success", "response": json.dumps(
        {"output": {"answer": "x", "referred_contexts": referred, "used_plans": False}})}],
        "attach_electric_diagrams": False}
    return r.parse_response(data)["gpt_response"][0]["sources"]


def test_solo_se_marca_citado_lo_que_el_modelo_declaro():
    """La figura del motor se citó; las tres del manual del variador no.

    Sin esta distinción, preguntar "¿el motor está en estrella o triángulo?"
    mostraba, al mismo nivel que la foto de la chapa del motor, la tabla de
    fallos del PowerFlex y dos diagramas de conexión a tierra: contexto
    recuperado, no respaldo de lo que se respondió.
    """
    ctx = [_fila("tesis.pdf", 16, 58.5, media="img/motor.png"),
           _fila("powerflex.pdf", 18, 55.1, media="img/tierra.png"),
           _fila("powerflex.pdf", 80, 54.9, media="tab/fallos.json")]
    srcs = _respuesta([1], ctx)
    citadas = [s for s in srcs if s["citado"]]
    assert [s["page"] for s in citadas] == [16]
    assert {s["page"] for s in srcs if not s["citado"]} == {18, 80}


def test_lo_no_citado_igual_viaja():
    """Se pliega, no se tira: el modelo a veces omite citar la figura de la que
    sacó el dato, y descartarla dejaría la respuesta sin nada verificable."""
    ctx = [_fila("a.pdf", 1, 60.0, media="img/a.png"),
           _fila("b.pdf", 2, 55.0, media="img/b.png")]
    assert len(_respuesta([1], ctx)) == 2


def test_el_respaldo_sin_citas_no_se_hace_pasar_por_citado():
    """Cuando el modelo no cita nada, nada queda marcado como citado: lo que
    sabemos es qué tuvo a la vista, no qué usó.

    Viajan todos, ordenados por score, y es el frontend el que muestra arriba
    los más relevantes (splitSources) porque es el que sabe que no hubo
    declaración. Recortar acá a tres perdía los otros siete para siempre."""
    ctx = [_fila(f"d{i}.pdf", i, 70.0 - i) for i in range(5)]
    srcs = _respuesta([], ctx)
    assert len(srcs) == 5
    assert all(s["citado"] is False for s in srcs)
    assert [s["similarity_score"] for s in srcs] == sorted(
        (s["similarity_score"] for s in srcs), reverse=True)


def test_las_citadas_van_antes_que_las_demas_aunque_puntuen_menos():
    """`sources` se consume también sin mirar `citado` (telemetría, API): el
    orden tiene que poner primero lo que el modelo dijo haber usado."""
    ctx = [_fila("ruido.pdf", 1, 72.0, media="img/ruido.png"),
           _fila("usado.pdf", 2, 51.0, media="img/usado.png")]
    srcs = _respuesta([2], ctx)
    assert [s["file_name"] for s in srcs] == ["usado.pdf", "ruido.pdf"]


def test_el_contexto_no_citado_viaja_entero_no_solo_el_que_tiene_imagen():
    """"Ver todo el contexto recuperado" mostraba la mitad.

    La función que juntaba lo no citado solo miraba los chunks con media, que
    era su propósito original. Con `citado` separando respaldo de contexto, eso
    dejó invisibles los fragmentos de texto: de los 10 entregados para "¿el
    motor está en estrella o triángulo?" llegaban 5, y los 5 que faltaban eran
    justamente los que el usuario quería revisar.
    """
    ctx = [_fila("citado.pdf", 1, 60.0, media="img/a.png"),
           _fila("con_imagen.pdf", 2, 58.0, media="img/b.png"),
           _fila("solo_texto.pdf", 3, 57.0),
           _fila("tambien_texto.pdf", 4, 56.0)]
    srcs = _respuesta([1], ctx)
    assert len(srcs) == 4, "no se pierde ningún fragmento entregado"
    assert {s["file_name"] for s in srcs if not s["citado"]} == {
        "con_imagen.pdf", "solo_texto.pdf", "tambien_texto.pdf"}


def test_no_muestra_dos_veces_la_misma_figura():
    """Dos chunks pueden apuntar al mismo png; mostrarlo dos veces simula que
    hay dos evidencias donde hay una."""
    ctx = [_fila("a.pdf", 1, 60.0, media="img/misma.png"),
           _fila("b.pdf", 2, 55.0, media="img/misma.png")]
    assert len(_respuesta([1], ctx)) == 1


# ─────────────────────────────── planos adjuntos: declarar cuesta algo

def _con_planos(referred, contexto, planos_usados=None, extra=None):
    r = _retriever(contexto)
    salida = {"answer": "x", "referred_contexts": referred}
    if planos_usados is not None:
        salida["planos_usados"] = planos_usados
    salida.update(extra or {})
    data = {"gpt_response_raw": [{"res_status": "Success",
                                  "response": json.dumps({"output": salida})}],
            "attach_electric_diagrams": True,
            "electric_diagram_path": __file__,          # cualquier archivo que exista
            "tben_diagram_path": __file__}
    return r, r.parse_response(data)["gpt_response"][0]["sources"]


def test_un_plano_solo_queda_citado_si_el_modelo_dice_que_leyo_en_el():
    """`used_plans` era un booleano sobre algo que viaja adjunto SIEMPRE, y no
    distinguía "lo usé" de "estaba ahí": sobre la misma pregunta dio True,
    False, True, True mientras referred_contexts daba [9] las cuatro veces."""
    _, srcs = _con_planos([], [_fila("a.pdf", 1, 60.0)],
                          planos_usados=[{"archivo": os.path.basename(__file__),
                                          "dato": "el diferencial es de 40A 30mA"}])
    planos = [s for s in srcs if s.get("is_attached_plan")]
    assert len(planos) == 2, "los dos planos viajan"
    assert sum(1 for p in planos if p["citado"]) == 2, "mismo basename: los dos citados"
    assert all(p["text"] for p in planos), "y con el dato que dijo haber leído"


def test_un_plano_sin_declarar_viaja_pero_no_citado():
    """No se esconde —el usuario puede querer abrirlo— pero no es respaldo."""
    _, srcs = _con_planos([], [_fila("a.pdf", 1, 60.0)], planos_usados=[])
    planos = [s for s in srcs if s.get("is_attached_plan")]
    assert len(planos) == 2
    assert not any(p["citado"] for p in planos)


def test_sin_contexto_y_sin_declaracion_los_planos_siguen_siendo_la_fuente(tmp_path):
    """Si no hubo contexto textual, los planos son lo único de donde pudo salir
    la respuesta: no marcarlos la dejaría sin ninguna referencia."""
    _, srcs = _con_planos([], [])
    planos = [s for s in srcs if s.get("is_attached_plan")]
    assert planos and all(p["citado"] for p in planos)


# ─────────────────────────────── el inventario, que no sale del índice

def test_el_inventario_citado_aparece_como_fuente():
    """El glosario se pegaba al final del prompt sin context_id, así que era una
    fuente real e inatribuible: el modelo respondía citando "el inventario de
    componentes" y el usuario no lo encontraba entre las fuentes."""
    from qnas.RetrieverQna_multimodal import INVENTARIO_FUENTE
    ctx = [_fila("a.pdf", 1, 60.0), _fila("b.pdf", 2, 55.0)]
    r = _retriever(ctx)
    r.glosario_ctx_id = 3                 # va después de los 2 chunks
    r.glosario_texto = "M01 = Motor del forzador [conexión estrella según tesis]"
    data = {"gpt_response_raw": [{"res_status": "Success", "response": json.dumps(
        {"output": {"answer": "x", "referred_contexts": [1, 3]}})}],
        "attach_electric_diagrams": False}
    srcs = r.parse_response(data)["gpt_response"][0]["sources"]
    inv = [s for s in srcs if s["file_name"] == INVENTARIO_FUENTE]
    assert len(inv) == 1 and inv[0]["citado"]
    assert "estrella según tesis" in inv[0]["text"]


def test_el_id_del_inventario_no_se_toma_como_indice_del_dataframe():
    """Es un context_id sin fila en context_df: si no se saca antes, el filtro
    de rango lo descarta como inválido y la cita se pierde en silencio."""
    ctx = [_fila("a.pdf", 1, 60.0)]
    r = _retriever(ctx)
    r.glosario_ctx_id = 2
    r.glosario_texto = "glosario"
    data = {"gpt_response_raw": [{"res_status": "Success", "response": json.dumps(
        {"output": {"answer": "x", "referred_contexts": [2]}})}],
        "attach_electric_diagrams": False}
    srcs = r.parse_response(data)["gpt_response"][0]["sources"]
    assert any(s["citado"] for s in srcs), "la cita al inventario no se pierde"
    assert not any(s["file_name"] == "a.pdf" and s["citado"] for s in srcs)


def test_los_prompts_se_pueden_formatear():
    """Una llave sin escapar en el prompt lo rompe entero, y en silencio.

    `RETRIEVER_GPT_QNA_PROMPT` pasa por .format(contexto, pregunta), así que un
    ejemplo de JSON escrito con llaves simples se interpreta como campo: el
    .format tira KeyError, QuestionAnswer se lo come y el usuario recibe
    "I don't know" sin nada en la interfaz que diga que hubo un error. Pasó con
    el ejemplo de "planos_usados".
    """
    from configs.Configuration import Configuration as C
    assert C.RETRIEVER_GPT_QNA_PROMPT.format("CONTEXTO", "PREGUNTA")


@pytest.mark.parametrize("real,declarado,esperado", [
    # El prompt de Configuration llama al plano "plano_distribucion_electrica.pdf"
    # y el archivo es "1_Plano distribucion electrica_v1.pdf". El modelo escribe
    # el del prompt, y con cotejo exacto la declaración se perdía entera: decía
    # haber leído ahí el diferencial de 40A/30mA y el plano salía como no citado.
    ("1_Plano distribucion electrica_v1.pdf", "plano_distribucion_electrica.pdf", "sí"),
    ("conexionadoTben.pdf", "conexionTben.pdf", "sí"),
    # Pero parecido no es cualquier cosa: un plano distinto no hereda la cita.
    ("conexionadoTben.pdf", "plano_distribucion_electrica.pdf", None),
])
def test_un_nombre_de_plano_aproximado_no_cuesta_la_cita(real, declarado, esperado):
    from qnas.RetrieverQna_multimodal import RetrieverQna
    assert RetrieverQna._dato_declarado(real, [(declarado, "sí")]) == esperado
