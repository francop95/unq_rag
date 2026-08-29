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
