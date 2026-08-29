"""
Contextual Retrieval + Preguntas Sintéticas
===========================================

Dos técnicas que atacan el problema central de este corpus: un chunk aislado
pierde el contexto que lo hace recuperable, y el usuario pregunta por *síntomas*
("no calienta") mientras el manual está escrito en lenguaje de *especificación*
("no hay entrada de alimentación eléctrica al variador").

1. CONTEXTUAL RETRIEVAL (contextual chunk embeddings)
   Antes de embeber, se prepende al chunk 1-2 frases generadas por LLM que lo
   sitúan en su documento ("Esto es de la tabla de resolución de problemas del
   capítulo 4, sobre fallas de arranque del variador PowerFlex 4M"). El vector
   resultante ya no depende de que el propio chunk se explique solo. Es la
   técnica publicada por Anthropic como "Contextual Retrieval".

2. PREGUNTAS SINTÉTICAS (multi-vector / query generation)
   Por cada chunk se generan preguntas que ese chunk responde, y cada pregunta se
   indexa como un vector adicional que apunta al MISMO contenido. Cierra la
   brecha de vocabulario entre cómo pregunta un técnico y cómo está redactado el
   manual.

Ambas salen de UNA sola llamada por chunk (mismo input, dos salidas).
"""

import json
from typing import Any, Dict, List, Optional

from logger import Logger
from task_utils.chunk_text import readable_chunk_text
from task_utils.llm_json import LLMJsonClient, run_parallel, text_content_part

logger = Logger.get_logger(__name__)


SYSTEM_PROMPT_TEMPLATE = (
    "Eres un indexador experto de documentación técnica industrial. Tu trabajo es "
    "hacer que un fragmento de manual sea fácil de recuperar por búsqueda semántica.\n\n"
    "Recibirás: metadatos del documento, el fragmento a procesar y los fragmentos "
    "vecinos (para que entiendas el contexto, NO para describirlos).\n\n"
    "Devuelve SOLO un objeto JSON con:\n"
    '- "context": 1-2 frases que sitúen el fragmento dentro del documento. Debe '
    "responder implícitamente: ¿de qué equipo/sección habla y qué tipo de "
    "información es? Escribilo como texto corrido que pueda prependerse al "
    "fragmento. NO repitas el fragmento ni lo resumas: agregá lo que le falta "
    "para entenderse solo.\n"
    '- "questions": {min_questions} a {max_questions} preguntas concretas, en el lenguaje que usaría un ' 
    "técnico de mantenimiento, que ESTE fragmento responde. Incluí al menos una "
    "formulada como síntoma o falla observable si el fragmento lo permite "
    '(ej. "¿por qué el motor no arranca?"). Preguntas cortas y específicas.\n\n'
    "Respondé en el mismo idioma del fragmento. No inventes datos que no estén "
    "en el fragmento o sus vecinos."
)


class ContextualEnricher:
    """
    Genera contexto situacional y preguntas sintéticas para cada chunk.
    """

    def __init__(
        self,
        client,
        model: str,
        concurrency: int = 4,
        max_questions: int = 5,
        neighbor_chars: int = 600,
        chunk_chars: int = 4000,
    ):
        self.llm = LLMJsonClient(client=client, model=model, temperature=0.0)
        self.concurrency = max(1, int(concurrency))
        self.max_questions = max(1, int(max_questions))
        # El prompt tenía "3 a 5 preguntas" fijo, así que max_questions solo TRUNCABA la
        # lista: subir el config de 5 a 8 no cambió nada (media 4.4, máximo 5 medidos
        # sobre el índice). Ahora el número que se configura es el que se le pide.
        self._system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            min_questions=max(1, self.max_questions - 2),
            max_questions=self.max_questions,
        )
        self.neighbor_chars = neighbor_chars
        self.chunk_chars = chunk_chars

    # ---------------- Texto legible por chunk ----------------
    @staticmethod
    def readable_text(chunk: Dict[str, Any]) -> str:
        """Representación textual del chunk (fuente única: task_utils.chunk_text)."""
        return readable_chunk_text(chunk)

    # ---------------- Prompt por chunk ----------------
    def _build_user_content(
        self,
        chunk: Dict[str, Any],
        prev_chunk: Optional[Dict[str, Any]],
        next_chunk: Optional[Dict[str, Any]],
        document_outline: str,
    ) -> List[Dict[str, Any]]:
        hierarchy = " > ".join(
            str(chunk.get(field, "")).strip()
            for field in ("document_chapter", "document_section", "section_number")
            if str(chunk.get(field, "")).strip()
        )

        lines = [
            f"Documento: {chunk.get('file_name', '')}",
            f"Página: {chunk.get('page_num', '')}",
        ]
        if hierarchy:
            lines.append(f"Ubicación en el documento: {hierarchy}")
        if document_outline:
            lines.append(f"Índice del documento (referencia):\n{document_outline}")
        lines.append(f"Tipo de contenido: {chunk.get('content_type', 'text')}")

        if prev_chunk is not None:
            lines.append(
                "--- FRAGMENTO ANTERIOR (solo contexto) ---\n"
                + self.readable_text(prev_chunk)[: self.neighbor_chars]
            )
        if next_chunk is not None:
            lines.append(
                "--- FRAGMENTO SIGUIENTE (solo contexto) ---\n"
                + self.readable_text(next_chunk)[: self.neighbor_chars]
            )

        lines.append(
            "--- FRAGMENTO A PROCESAR ---\n"
            + self.readable_text(chunk)[: self.chunk_chars]
        )

        return [text_content_part("\n\n".join(lines))]

    # ---------------- API pública ----------------
    def enrich(
        self,
        chunks: List[Dict[str, Any]],
        document_outline: str = "",
    ) -> Dict[str, int]:
        """
        Añade `context_summary` y `synthetic_questions` a cada chunk, en sitio.

        Se asume que `chunks` viene en orden documental (los vecinos se toman de
        las posiciones adyacentes de la lista).

        Returns:
            Estadísticas del enriquecimiento.
        """
        if not chunks:
            return {"enriched": 0, "questions": 0, "failed": 0}

        logger.info(
            f"Contextual Retrieval: enriqueciendo {len(chunks)} chunks "
            f"con concurrencia={self.concurrency}"
        )

        indexed = list(enumerate(chunks))

        def worker(item):
            i, chunk = item
            prev_chunk = chunks[i - 1] if i > 0 else None
            next_chunk = chunks[i + 1] if i < len(chunks) - 1 else None
            content = self._build_user_content(chunk, prev_chunk, next_chunk, document_outline)
            return self.llm.complete_json(
                system_prompt=self._system_prompt,
                user_content=content,
                label=f"context_p{chunk.get('page_num')}_{chunk.get('chunk_id')}",
            )

        results = run_parallel(indexed, worker, self.concurrency, label="contextual")

        stats = {"enriched": 0, "questions": 0, "failed": 0}
        for chunk, result in zip(chunks, results):
            if not isinstance(result, dict):
                stats["failed"] += 1
                continue

            context = str(result.get("context") or "").strip()
            if context:
                chunk["context_summary"] = context
                stats["enriched"] += 1

            questions = result.get("questions")
            if isinstance(questions, list):
                cleaned = [
                    str(q).strip() for q in questions
                    if isinstance(q, (str, int, float)) and str(q).strip()
                ][: self.max_questions]
                if cleaned:
                    chunk["synthetic_questions"] = cleaned
                    stats["questions"] += len(cleaned)

        logger.info(
            f"Contextual Retrieval: {stats['enriched']} chunks con contexto, "
            f"{stats['questions']} preguntas generadas, {stats['failed']} fallos"
        )
        return stats


def build_question_chunks(
    chunks: List[Dict[str, Any]],
    seen_questions: Optional[set] = None,
) -> List[Dict[str, Any]]:
    """
    Convierte las `synthetic_questions` de cada chunk en chunks indexables aparte.

    Cada chunk-pregunta se EMBEBE con el texto de la pregunta (campo `embed_text`)
    pero ALMACENA el contenido del chunk padre como documento, así el vector
    matchea la forma en que pregunta el usuario mientras que lo que se devuelve al
    LLM sigue siendo el contenido real. No requiere cambios del lado de consulta.
    """
    question_chunks: List[Dict[str, Any]] = []

    # Una pregunta idéntica sobre dos contenidos distintos produce dos vectores con el
    # MISMO embedding: el desempate entre ellos queda arbitrario, y el usuario recibe
    # una página u otra por azar. Medido sobre el índice anterior: 155 textos de
    # pregunta apuntaban a más de un chunk (322 vectores), con casos como "¿cómo se
    # calcula la humedad absoluta en el secadero?" repetido en las páginas 37, 53 y 63.
    # Se conserva la primera aparición: el chunk pierde ese vector pero le quedan los
    # demás, y el contenido sigue alcanzable por su propio vector de texto.
    # El caller puede pasar su propio set para que la deduplicación cruce varias
    # llamadas (los super-chunks se enriquecen en una pasada aparte, y al contener el
    # texto de sus hijos el LLM les genera preguntas muy parecidas).
    seen_questions = seen_questions if seen_questions is not None else set()
    duplicates = 0

    for parent in chunks:
        questions = parent.get("synthetic_questions") or []
        if not questions:
            continue

        # Las facetas OCR de un diagrama no llevan preguntas: su texto son etiquetas
        # sueltas, y el LLM terminaba generándolas desde el rótulo de la página.
        if parent.get("skip_synthetic_questions"):
            continue

        parent_text = ContextualEnricher.readable_text(parent)
        if not parent_text.strip():
            continue

        for i, question in enumerate(questions, start=1):
            normalized = " ".join(str(question).lower().split())
            if not normalized or normalized in seen_questions:
                duplicates += 1
                continue
            seen_questions.add(normalized)
            question_chunks.append({
                "file_name": parent.get("file_name"),
                "page_num": parent.get("page_num"),
                "chunk_id": f"{parent.get('chunk_id')}_q{i}",
                "page_metadata": parent.get("page_metadata"),
                "content_type": "synthetic_question",
                # Documento almacenado = contenido real del padre
                "original_chunk": parent_text,
                # Vector = la pregunta
                "embed_text": question,
                "parent_chunk_id": parent.get("chunk_id"),
                "question": question,
                "context_summary": parent.get("context_summary", ""),
                # Heredar jerarquía para que los filtros por capítulo sigan andando
                "document_section": parent.get("document_section", ""),
                "document_chapter": parent.get("document_chapter", ""),
                "section_number": parent.get("section_number", ""),
                "hierarchy_path": parent.get("hierarchy_path", ""),
                "prev_chunk_id": parent.get("prev_chunk_id", ""),
                "next_chunk_id": parent.get("next_chunk_id", ""),
            })

    logger.info(
        f"Preguntas sintéticas: {len(question_chunks)} vectores adicionales"
        + (f" ({duplicates} descartadas por texto duplicado)" if duplicates else "")
    )
    return question_chunks


def build_document_outline(chunks: List[Dict[str, Any]], max_entries: int = 40) -> str:
    """
    Esquema compacto del documento a partir de la metadata jerárquica de los chunks.

    Se pasa como referencia en el prompt en lugar del documento completo: da al
    modelo el "mapa" del manual con una fracción de los tokens.
    """
    seen: List[str] = []
    for chunk in chunks:
        for field in ("document_chapter", "document_section"):
            value = str(chunk.get(field, "")).strip()
            if value and value.lower() not in ("nan", "none") and value not in seen:
                seen.append(value)
        if len(seen) >= max_entries:
            break
    return "\n".join(f"- {entry}" for entry in seen[:max_entries])
