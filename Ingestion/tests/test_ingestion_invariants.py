"""
Invariantes de la ingesta. Cada test corresponde a un bug real que se encontró y arregló.

Correr desde Ingestion/:
    ./.venv/bin/python -m pytest tests/test_ingestion_invariants.py -q
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "eval")))

from task_utils.chunk_text import readable_chunk_text
from task_utils.contextual_enricher import build_question_chunks
from task_utils.diagram_processor import ElectricalDiagramProcessor
from task_utils.llm_json import (
    QuotaExhaustedError, is_quota_exhausted, raise_if_quota_exhausted, retry_delay_from_error,
)
from task_utils.table_processor import TableProcessor
from task_utils.technical_validators import DiagramLabelValidator


# ─────────────────────────────────────────────────────────── texto legible del chunk

def test_una_tabla_devuelve_su_markdown_no_el_json():
    """El JSON crudo llegaba al LLM y al usuario como si fuera el contenido del chunk."""
    chunk = {"content_type": "table",
             "original_chunk": json.dumps({"table_markdown": "| Causa | Acción |",
                                           "table_json": {"rows": [["a", "b"]]}})}
    assert readable_chunk_text(chunk) == "| Causa | Acción |"


def test_una_tabla_sin_markdown_cae_a_las_filas():
    chunk = {"content_type": "table",
             "original_chunk": json.dumps({"table_json": {"rows": [["Causa", "Acción"], ["x", "y"]]}})}
    salida = readable_chunk_text(chunk)
    assert "Causa" in salida and "y" in salida


def test_una_imagen_devuelve_su_descripcion():
    chunk = {"content_type": "diagram_visual",
             "original_chunk": json.dumps({"notes": {"description": "El diagrama muestra los bornes"},
                                           "image_path": "/x.png"})}
    assert "bornes" in readable_chunk_text(chunk)


def test_el_texto_plano_pasa_sin_tocar():
    assert readable_chunk_text({"content_type": "text", "original_chunkable": None,
                                "original_chunk": "5. Verifique la entrada de paro."}) \
        == "5. Verifique la entrada de paro."


# ─────────────────────────────────────────────────────── legibilidad del OCR

# Textos tomados literalmente del corpus (98 chunks OCR de la ingesta anterior).
@pytest.mark.parametrize("texto,legible", [
    # Basura: el patrón dominante es ruido de 1-2 letras mezclado con símbolos
    ("as a =Q ada 218 2 >| > 2 0 w io 3 2 i A", False),
    ("3_3 200000 3| + Os — % mi", False),
    ("— a E", False),
    ("3 q 2 a o a 0 it if a if", False),      # todas alfabéticas pero de 1-2 letras
    ("38 Re 7", False),
    # Útiles: epígrafes de figura y etiquetas reales del tablero
    ("5 Ñ AN Th. Y Is Y, i— y 4 Figura 1.1: A la izquierda, secadero de pastas eléctrico", True),
    ("2 3 4 | | 6 | 7 | 9 | E/S Mi2 X3 OO C4 CO DRIVER RESISTENCIAS O C5 X2 EXRTACTOR", True),
    ("3 y PO? r - - rm = ~ » a - if te 13 - Figura 5.1: A la izquierda, montaje de klixon", True),
])
def test_el_gate_de_ocr_separa_basura_de_texto_real(texto, legible):
    """96% de los recortes daban basura, y el enriquecedor les inventaba preguntas encima."""
    assert ElectricalDiagramProcessor._ocr_is_legible(texto) is legible


def test_las_palabras_funcionales_no_cortan_la_racha():
    """Una regla anterior exigía 3+ letras y "de"/"la"/"y" rompían toda frase en español."""
    assert ElectricalDiagramProcessor._ocr_is_legible(
        "montaje de klixon sobre la chapa de acero") is True


# ─────────────────────────────────────────────────────── validador de diagramas

def test_descarta_una_imagen_que_el_modelo_declara_vacia():
    """4 recortes en blanco arrastraban 16 preguntas sintéticas plausibles."""
    chunk = {"content_type": "diagram_visual",
             "original_chunk": json.dumps({"notes": {"description":
                 "La imagen está en blanco y no contiene información visible."}})}
    fatal, avisos = DiagramLabelValidator().validate(chunk)
    assert fatal is True and avisos


def test_conserva_un_diagrama_real():
    chunk = {"content_type": "diagram_visual",
             "original_chunk": json.dumps({"notes": {"description":
                 "El diagrama muestra las conexiones de un variador de frecuencia."}})}
    assert DiagramLabelValidator().validate(chunk)[0] is False


def test_no_descarta_una_imagen_sin_descripcion():
    """Sigue siendo indexable por CLIP: descartarla perdía su única representación."""
    chunk = {"content_type": "diagram_visual", "original_chunk": json.dumps({"notes": ""})}
    assert DiagramLabelValidator().validate(chunk)[0] is False


def test_no_toca_un_chunk_de_texto_que_menciona_la_frase():
    chunk = {"content_type": "text", "original_chunk": "La imagen está en blanco en el original"}
    assert DiagramLabelValidator().validate(chunk) == (False, [])


# ─────────────────────────────────────────────────── facetas que crea una figura

def test_una_figura_ya_no_genera_la_faceta_structured():
    """Tenía texto IDÉNTICO a la visual: 568 vectores (13% del índice) sin aportar nada."""
    proc = ElectricalDiagramProcessor(use_ocr=False)
    chunk = {"chunk_id": "chunk_3", "file_name": "d.pdf", "page_num": 7,
             "content_type": "image", "original_chunk": json.dumps({"notes": {"description": "x"}})}
    tipos = [c["content_type"] for c in proc.create_enhanced_diagram_chunks(chunk)]
    assert tipos == ["diagram_visual"]
    assert "diagram_description" not in tipos


# ─────────────────────────────────────────────── preguntas sintéticas

def _chunk(cid, texto, preguntas, **extra):
    return {"file_name": "d.pdf", "page_num": 1, "chunk_id": cid, "content_type": "text",
            "original_chunk": texto, "synthetic_questions": preguntas, **extra}


def test_deduplica_preguntas_con_el_mismo_texto():
    """Mismo texto = mismo embedding = desempate arbitrario entre contenidos distintos."""
    chunks = [_chunk("c1", "uno", ["¿Cómo se mide la humedad?"]),
              _chunk("c2", "dos", ["  ¿cómo se MIDE la humedad?  ", "¿Y la presión?"])]
    salida = build_question_chunks(chunks)
    assert len(salida) == 2
    assert [q["chunk_id"] for q in salida] == ["c1_q1", "c2_q2"]


def test_el_set_de_duplicados_se_comparte_entre_llamadas():
    """Los super-chunks se enriquecen en una pasada aparte y repiten preguntas de sus hijos."""
    vistas = set()
    build_question_chunks([_chunk("c1", "uno", ["¿Qué es un klixon?"])], vistas)
    segunda = build_question_chunks([_chunk("sc1", "uno dos", ["¿Qué es un klixon?", "¿Y el rango?"])], vistas)
    assert len(segunda) == 1 and segunda[0]["question"] == "¿Y el rango?"


def test_no_genera_preguntas_para_una_faceta_ocr():
    chunks = [_chunk("c3_ocr", "Texto extraido: basura", ["¿Qué muestra el diagrama?"],
                     content_type="diagram_text", skip_synthetic_questions=True)]
    assert build_question_chunks(chunks) == []


def test_la_pregunta_se_embebe_pero_se_almacena_el_contenido_del_padre():
    """Es el núcleo del multi-vector: el vector matchea la pregunta, el LLM recibe el contenido."""
    salida = build_question_chunks([_chunk("c1", "El teclado no está habilitado", ["¿Por qué no arranca?"])])
    q = salida[0]
    assert q["embed_text"] == "¿Por qué no arranca?"
    assert q["original_chunk"] == "El teclado no está habilitado"
    assert q["parent_chunk_id"] == "c1"


# ─────────────────────────────────────── cadena de vecinos (misma lógica que la ingesta)

def _figure_group(c):
    chunk_id = str(c.get("chunk_id", ""))
    for suffix in ("_ocr", "_structured", "_visual"):
        if chunk_id.endswith(suffix):
            return f"{c.get('file_name','')}_{c.get('page_num','')}_{chunk_id[:-len(suffix)]}"
    return None


def _link(orden):
    def cid(c):
        return f"{c.get('file_name','')}_{c.get('page_num','')}_{c.get('chunk_id','')}"
    for i, chunk in enumerate(orden):
        group = _figure_group(chunk)
        p = i - 1
        while p >= 0 and group is not None and _figure_group(orden[p]) == group:
            p -= 1
        n = i + 1
        while n < len(orden) and group is not None and _figure_group(orden[n]) == group:
            n += 1
        chunk["prev_chunk_id"] = cid(orden[p]) if p >= 0 else ""
        chunk["next_chunk_id"] = cid(orden[n]) if n < len(orden) else ""
    return orden


def test_las_facetas_de_una_figura_no_son_vecinas_entre_si():
    """La cadena secuencial plana las volvía vecinas: 361 de 1544 enlaces."""
    orden = _link([
        {"file_name": "d", "page_num": 7, "chunk_id": "chunk_1"},
        {"file_name": "d", "page_num": 7, "chunk_id": "chunk_2_visual"},
        {"file_name": "d", "page_num": 7, "chunk_id": "chunk_2_ocr"},
        {"file_name": "d", "page_num": 8, "chunk_id": "chunk_1"},
    ])
    visual, ocr = orden[1], orden[2]
    assert visual["next_chunk_id"] == "d_8_chunk_1", "no debe apuntar a su hermana"
    assert ocr["prev_chunk_id"] == "d_7_chunk_1"
    # Las dos facetas comparten los vecinos de afuera: la figura es una unidad de lectura
    assert visual["prev_chunk_id"] == ocr["prev_chunk_id"]


# ───────────────────────── tablas índice de parámetros (el techo medido del retrieval)

# Forma real de la p46 del manual del variador: el grupo en la primera columna y DOS
# parámetros por fila, así que partir por filas no baja la densidad.
GRILLA = [
    ["Grupo", "Parámetros", "", "", ""],
    ["Programa básico", "Volt placa motor", "P101", "Modo de Paro", "P107"],
    ["", "Hz placa motor", "P102", "Referencia Veloc", "P108"],
    ["", "Intens SC Motor", "P103", "Tiempo acel. 1", "P109"],
    ["", "Frecuencia Mín.", "P104", "Tiempo decel. 1", "P110"],
]


def test_detecta_una_tabla_indice_de_parametros():
    assert TableProcessor.is_parameter_index_table(GRILLA) is True


def test_no_confunde_una_tabla_normal_con_un_indice():
    normal = [["Causas", "Indicación", "Acción correctiva"],
              ["El teclado no está habilitado", "LED apagado", "Establezca P106 en 0"]]
    assert TableProcessor.is_parameter_index_table(normal) is False


def test_empareja_cada_nombre_con_su_codigo():
    pares = dict(TableProcessor._param_pairs(GRILLA))
    assert pares["Volt placa motor"] == "P101"
    assert pares["Modo de Paro"] == "P107"
    assert pares["Tiempo decel. 1"] == "P110"


def test_parte_por_parametro_y_no_por_fila():
    """8 parámetros en 4 filas: partir por filas daría 1 chunk, por parámetro da 2."""
    chunk = {"chunk_id": "chunk_2", "file_name": "d.pdf", "page_num": 46,
             "content_type": "table",
             "original_chunk": json.dumps({"table_json": {"rows": GRILLA},
                                           "table_markdown": "irrelevante"})}
    proc = TableProcessor()
    assert proc.needs_splitting(chunk) is True, "aunque tenga pocas filas"
    partes = proc.split_table(chunk)
    assert len(partes) == 2, "8 parámetros / 4 por chunk"
    primero = json.loads(partes[0]["original_chunk"])["table_markdown"]
    assert "Volt placa motor" in primero and "P101" in primero
    # Y el chunk resultante es chico: es lo que hace discriminante al embedding
    assert len(primero) < 260


def test_el_grupo_no_es_el_encabezado_de_columna():
    """Una versión anterior prefijaba "Grupo: Grupo" agarrando el nombre de la columna."""
    proc = TableProcessor()
    chunk = {"chunk_id": "c", "file_name": "d.pdf", "page_num": 46, "content_type": "table",
             "original_chunk": json.dumps({"table_json": {"rows": GRILLA}, "table_markdown": ""})}
    md = json.loads(proc.split_table(chunk)[0]["original_chunk"])["table_markdown"]
    assert "Grupo: Grupo" not in md
    assert "Programa básico" in md


def test_las_partes_siguen_siendo_content_type_table():
    """Un content_type propio hacía que el indexador las descartara en silencio."""
    proc = TableProcessor()
    chunk = {"chunk_id": "c", "file_name": "d.pdf", "page_num": 46, "content_type": "table",
             "original_chunk": json.dumps({"table_json": {"rows": GRILLA}, "table_markdown": ""})}
    assert all(p["content_type"] == "table" for p in proc.split_table(chunk))


# ───────────────── cuota agotada vs rate limit (los dos llegan como HTTP 429)

# Mensajes tomados literalmente de lo que devolvió la API.
SIN_CREDITO = (
    "Error code: 429 - {'error': {'message': 'You have no credits remaining. Add credits "
    "to continue using the API at https://platform.openai.com/settings/organization/"
    "billing/.', 'type': 'insufficient_quota', 'param': None, "
    "'code': 'credit_balance_exhausted'}}"
)
THROTTLING = (
    "Error code: 429 - {'error': {'message': 'Rate limit reached for gpt-4o-mini in "
    "organization org-x on tokens per min (TPM): Limit 30000. Please try again in 1.5s.', "
    "'type': 'tokens', 'code': 'rate_limit_exceeded'}}"
)


def test_distingue_cuota_agotada_de_throttling():
    """Sin esto se reintentó 1760 veces un error permanente, disfrazado de rate limit."""
    assert is_quota_exhausted(Exception(SIN_CREDITO)) is True
    assert is_quota_exhausted(Exception(THROTTLING)) is False


def test_lee_el_codigo_del_cuerpo_de_la_respuesta():
    """El SDK expone el código en .body, no solo en el mensaje."""
    class ErrorConBody(Exception):
        body = {"error": {"code": "insufficient_quota", "message": "..."}}
    assert is_quota_exhausted(ErrorConBody()) is True


def test_la_cuota_agotada_corta_el_lote():
    with pytest.raises(QuotaExhaustedError) as info:
        raise_if_quota_exhausted(Exception(SIN_CREDITO), "enriquecimiento")
    assert "Sin crédito" in str(info.value)
    assert "enriquecimiento" in str(info.value)


def test_el_throttling_no_corta_el_lote():
    raise_if_quota_exhausted(Exception(THROTTLING), "enriquecimiento")   # no debe lanzar


def test_el_throttling_sigue_respetando_el_tiempo_del_proveedor():
    """El backoff que honra el "try again in Xs" no se rompió con el cambio."""
    assert retry_delay_from_error(Exception(THROTTLING), attempt=0) == pytest.approx(2.0)


def test_tolera_el_marcador_de_nota_al_pie_en_el_codigo():
    """En la p28 del manual los códigos vienen como "P106(1)"; el patrón estricto los perdía."""
    filas = [["N.º", "Señal", "Descripción", "Parám."],
             ["01", "Paro", "Debe haber un puente instalado", "P106(1)"],
             ["02", "Marcha", "Entrada de arranque", "P107(1)"],
             ["03", "Dirección", "Sentido de giro", "A434"],
             ["04", "Preselección", "Frecuencia fija", "A410"],
             ["05", "Reset", "Borrar fallo", "A450"],
             ["06", "Local", "Control por teclado", "P106"]]
    pares = dict(TableProcessor._param_pairs(filas))
    assert pares["Debe haber un puente instalado"] == "P106(1)"
    assert TableProcessor.is_parameter_index_table(filas) is True


def test_el_numero_de_preguntas_del_prompt_sigue_al_config():
    """max_questions solo truncaba la lista: el prompt pedía "3 a 5" fijo."""
    import re as _re
    from task_utils.contextual_enricher import ContextualEnricher
    for configurado, esperado in ((5, "3 a 5"), (8, "6 a 8"), (2, "1 a 2")):
        enricher = ContextualEnricher(client=None, model="x", max_questions=configurado)
        pedido = _re.search(r'"questions": (\d+ a \d+)', enricher._system_prompt).group(1)
        assert pedido == esperado, f"con max_questions={configurado} pide {pedido}"


def test_el_error_de_cuota_corta_complete_json_sin_reintentar():
    """Antes se comía 8 reintentos con backoff por cada llamada, y seguía con la siguiente."""
    import httpx
    from openai import RateLimitError
    from task_utils.llm_json import LLMJsonClient, run_parallel

    cuerpo = {"error": {"message": "You have no credits remaining.",
                        "type": "insufficient_quota", "code": "credit_balance_exhausted"}}
    respuesta = httpx.Response(
        429, request=httpx.Request("POST", "https://api.openai.com/v1/x"), json=cuerpo)

    class ClienteSinCredito:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    raise RateLimitError("Error code: 429 - " + str(cuerpo),
                                         response=respuesta, body=cuerpo)

    cliente = LLMJsonClient(client=ClienteSinCredito(), model="x", max_retries=8)
    with pytest.raises(QuotaExhaustedError):
        cliente.complete_json("sys", "user", label="t")

    # Y run_parallel no lo convierte en None: aborta el lote entero
    with pytest.raises(QuotaExhaustedError):
        run_parallel([1, 2, 3], lambda x: cliente.complete_json("s", "u"), max_workers=2)


# ───────────── tablas de referencia por código (el contenido más valioso del corpus)

# Forma real de la tabla de fallos del variador, p80: cada fila indexada por un código y
# autocontenida. Es la consulta canónica de un técnico: "me tira F048, qué hago".
TABLA_FALLOS = [
    ["N.º", "Fallo", "Tipo", "Descripción", "Acción"],
    ["F12", "Sobrecorr. HW", "②", "La corriente de salida excedió...", "Revise la programación..."],
    ["F13", "Fallo tierra", "②", "Se detectó corriente excesiva...", "Revise el motor y el cableado"],
    ["F33", "Int. rearme auto", "②", "El variador intentó sin éxito...", "Corrija la causa del fallo"],
    ["F38", "Fase U a tierra", "②", "Se detectó un fallo de tierra...", "Revise el cableado"],
    ["F39", "Fase V a tierra", "②", "Se detectó un fallo de tierra...", "Revise el cableado"],
    ["F40", "Fase W a tierra", "②", "Se detectó un fallo de tierra...", "Revise el cableado"],
]


def test_detecta_una_tabla_de_referencia_por_codigo():
    assert TableProcessor.is_code_reference_table(TABLA_FALLOS) is True


def test_no_confunde_una_tabla_normal_con_una_de_codigos():
    """Mencionar un código al pasar no la convierte en tabla de referencia."""
    normal = [["Causas", "Indicación", "Acción correctiva"],
              ["El teclado no está habilitado", "LED apagado", "Establezca P106 en 0"],
              ["La entrada de paro no está", "Ninguna", "Cablee las entradas"],
              ["Datos del motor mal cargados", "Ninguna", "Revise P101 y P102"],
              ["Falta alimentación", "Display apagado", "Verifique la tensión"]]
    assert TableProcessor.is_code_reference_table(normal) is False


def test_parte_la_tabla_de_fallos_en_chunks_de_pocas_filas():
    """
    Medido sobre los 17 códigos del manual: el chunk con 10 códigos devolvía la parte
    equivocada en 9 de 10 consultas, y el de 3 acertaba las 3. El embedding de un chunk
    con 10 fallas distintas es el promedio de 10 cosas sin relación.
    """
    chunk = {"chunk_id": "chunk_1", "file_name": "d.pdf", "page_num": 80,
             "content_type": "table",
             "original_chunk": json.dumps({"table_json": {"rows": TABLA_FALLOS},
                                           "table_markdown": "irrelevante"})}
    proc = TableProcessor()
    assert proc.needs_splitting(chunk) is True
    partes = proc.split_table(chunk)
    assert len(partes) == 2, "6 filas de datos / 3 por chunk"
    for parte in partes:
        md = json.loads(parte["original_chunk"])["table_markdown"]
        codigos = [c for c in ("F12", "F13", "F33", "F38", "F39", "F40") if c in md]
        assert len(codigos) <= 3, f"un chunk quedó con {len(codigos)} códigos"
        assert "N.º" in md, "el encabezado se repite en cada parte"


def test_una_tabla_de_codigos_corta_no_se_parte():
    corta = [TABLA_FALLOS[0]] + TABLA_FALLOS[1:4]     # 3 filas de datos
    chunk = {"chunk_id": "c", "file_name": "d.pdf", "page_num": 80, "content_type": "table",
             "original_chunk": json.dumps({"table_json": {"rows": corta}, "table_markdown": "x"})}
    assert TableProcessor().split_table(chunk) == [chunk]


# ───────────── verificación de que la respuesta esté en el texto recuperado

from answer_check import anclas, es_verificable, respuesta_presente


@pytest.mark.parametrize("clave,esperadas", [
    ("P101", ["p101"]),
    ("P106(1)", ["p106"]),      # sin el marcador: matchea "P106" y "P106(1)"
    ("216.0 mm (8.50 pulgadas)", ["216.0mm"]),
    ("2 A", ["2a"]),
    ("0.50 m/s", ["0.50m/s"]),
    ("0 a 300°C", ["300°c"]),   # la "a" del rango NO se lee como amperes
    ("P101, P102 y P103", ["p101", "p102", "p103"]),
])
def test_extrae_anclas_de_codigos_y_valores_con_unidad(clave, esperadas):
    encontradas = anclas(clave)
    for e in esperadas:
        assert e in encontradas, f"falta {e} en {encontradas}"


@pytest.mark.parametrize("clave,verificable", [
    ("P101", True),
    ("2 A", True),
    ("El sistema corta automáticamente los actuadores", True),
    ("0", False),            # un dígito suelto matchea cualquier texto
    ("50", False),
    ("2", False),
])
def test_declara_no_verificable_lo_que_no_puede_discriminar(clave, verificable):
    """Preferimos informar cuántas se pudo verificar antes que inflar el número."""
    assert es_verificable(clave) is verificable


def test_encuentra_la_respuesta_por_ancla():
    ok, motivo = respuesta_presente("P101", ["| Volt placa motor | P101 |"])
    assert ok is True and "ancla" in motivo


def test_el_ancla_matchea_con_o_sin_espacio():
    """La tabla dice "2A" y la clave "2 A", o al revés."""
    assert respuesta_presente("2 A", ["corriente máxima 2A por canal"])[0] is True
    assert respuesta_presente("2A", ["corriente máxima de 2 A por canal"])[0] is True


def test_detecta_cuando_llegó_la_pagina_pero_no_la_fila():
    """El caso real: el chunk es de la tabla de fallos correcta pero de otra parte."""
    ok, motivo = respuesta_presente("F048", ["| F63 | Sobrecorr. SW | ... | F64 | F70 |"])
    assert ok is False and "ninguna ancla" in motivo


def test_la_prosa_se_verifica_por_solape():
    clave = "El sistema corta automáticamente los actuadores"
    assert respuesta_presente(clave, ["ante sobretemperatura el sistema corta los actuadores"])[0] is True
    assert respuesta_presente(clave, ["el variador regula la velocidad del forzador"])[0] is False


def test_una_clave_no_verificable_devuelve_None():
    ok, _ = respuesta_presente("0", ["cualquier texto con un 0 adentro"])
    assert ok is None


def test_encuentra_el_valor_cuando_la_unidad_esta_en_el_encabezado():
    """
    Caso real del eval: la tabla de velocidades de aire pone "v [m/s]" en el encabezado y
    la celda trae solo "0.79". Exigir valor+unidad adyacentes daba por no encontrada una
    respuesta que estaba textualmente en el chunk recuperado.
    """
    texto = "| Ciclo 150 % | v [m/s] | |---|---| | Bandeja 1 | 0.95 | | Bandeja 3 | 0.79 |"
    assert respuesta_presente("0.79 m/s", [texto])[0] is True


def test_la_coma_decimal_matchea_el_punto():
    """La clave decía "0,82 m/s" y la tabla "0.82": contaba como fallo de retrieval."""
    assert respuesta_presente("0,82 m/s", ["la velocidad fue de 0.82 m/s"])[0] is True


def test_un_numero_poco_especifico_no_se_usa_como_ancla():
    """"2 A" no debe anclar en el "2" suelto: aparecería en cualquier texto."""
    assert "2" not in anclas("2 A")
    assert anclas("50") == []
