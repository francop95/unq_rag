"""
Consultas guardadas sobre la telemetría
=======================================

Las preguntas que uno le hace a estos datos son casi siempre las mismas, y
escribir el SQL cada vez es la clase de fricción que hace que nadie mire los
datos. Viven acá y no en el HTML para que el panel y la línea de comandos usen
exactamente las mismas, sin dos versiones que se desincronizan.
"""

CONSULTAS = [
    {
        "id": "ultimas",
        "titulo": "Últimas consultas",
        "ayuda": "Qué se preguntó, cuánto recuperó y con qué score",
        "sql": """
SELECT substr(creada_en, 1, 19) AS cuando,
       pregunta,
       n_contextos AS contextos,
       round(score_maximo, 1) AS score_max,
       CASE WHEN desde_cache THEN 'sí' ELSE '' END AS cache,
       query_id
FROM ejecuciones
ORDER BY creada_en DESC
LIMIT 50""",
    },
    {
        "id": "sin_contexto",
        "titulo": "Consultas que no recuperaron nada",
        "ayuda": "Cero contextos: o la pregunta no tiene ancla técnica, o falta el documento",
        "sql": """
SELECT substr(creada_en, 1, 19) AS cuando,
       pregunta,
       pregunta_reformulada AS reformulada,
       CASE WHEN es_followup THEN 'sí' ELSE '' END AS con_historial,
       query_id
FROM ejecuciones
WHERE n_contextos = 0
ORDER BY creada_en DESC
LIMIT 50""",
    },
    {
        "id": "negativos",
        "titulo": "Respuestas marcadas como no útiles",
        "ayuda": "Con sus motivos y comentarios",
        "sql": """
SELECT substr(f.creado_en, 1, 19) AS cuando,
       e.pregunta,
       f.motivos_json AS motivos,
       f.comentario,
       e.n_contextos AS contextos,
       f.query_id
FROM feedback f
LEFT JOIN ejecuciones e ON e.query_id = f.query_id
WHERE f.util = 0
ORDER BY f.creado_en DESC
LIMIT 50""",
    },
    {
        "id": "motivos",
        "titulo": "Motivos más frecuentes",
        "ayuda": "Por qué falla más seguido",
        "sql": """
SELECT motivos_json AS combinacion_de_motivos,
       COUNT(*) AS veces
FROM feedback
WHERE util = 0
GROUP BY motivos_json
ORDER BY veces DESC""",
    },
    {
        "id": "documentos",
        "titulo": "Qué documentos se recuperan más",
        "ayuda": "Y cuántas veces llegan a mostrarse al usuario",
        "sql": """
SELECT file_name AS documento,
       COUNT(*) AS veces_recuperado,
       SUM(mostrado) AS veces_mostrado,
       round(AVG(score), 1) AS score_promedio,
       round(AVG(posicion), 1) AS posicion_promedio
FROM contextos
GROUP BY file_name
ORDER BY veces_recuperado DESC""",
    },
    {
        "id": "recuperado_no_mostrado",
        "titulo": "Recuperado pero no mostrado",
        "ayuda": "Chunks buenos que el usuario nunca vio: candidatos a revisar el top-k",
        "sql": """
SELECT c.posicion,
       round(c.score, 1) AS score,
       c.content_type AS tipo,
       c.file_name AS documento,
       c.page_num AS pagina,
       substr(e.pregunta, 1, 40) AS pregunta
FROM contextos c
JOIN ejecuciones e ON e.query_id = c.query_id
WHERE c.mostrado = 0 AND c.score > 60
ORDER BY c.score DESC
LIMIT 50""",
    },
    {
        "id": "satisfaccion",
        "titulo": "Resumen de satisfacción",
        "ayuda": "Cuántas consultas hubo, cuántas se valoraron y cómo",
        "sql": """
SELECT (SELECT COUNT(*) FROM ejecuciones) AS consultas,
       (SELECT COUNT(*) FROM feedback) AS con_feedback,
       (SELECT COUNT(*) FROM feedback WHERE util = 1) AS sirvieron,
       (SELECT COUNT(*) FROM feedback WHERE util = 0) AS no_sirvieron,
       (SELECT round(AVG(n_contextos), 1) FROM ejecuciones) AS contextos_promedio,
       (SELECT round(AVG(score_maximo), 1) FROM ejecuciones WHERE score_maximo IS NOT NULL) AS score_promedio""",
    },
    {
        "id": "lentas",
        "titulo": "Las consultas más lentas",
        "ayuda": "Para ver dónde se va el tiempo",
        "sql": """
SELECT substr(pregunta, 1, 50) AS pregunta,
       round(json_extract(tiempos_json, '$.total_time'), 2) AS total_s,
       round(json_extract(tiempos_json, '$.context_time'), 2) AS retrieval_s,
       round(json_extract(tiempos_json, '$.qna_time'), 2) AS generacion_s,
       n_contextos AS contextos
FROM ejecuciones
WHERE tiempos_json IS NOT NULL
ORDER BY json_extract(tiempos_json, '$.total_time') DESC
LIMIT 30""",
    },
]

POR_ID = {c["id"]: c for c in CONSULTAS}
