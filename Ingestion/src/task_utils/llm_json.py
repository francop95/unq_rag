"""
Cliente LLM para respuestas JSON
================================

Helper compartido por las etapas de enriquecimiento de la ingesta (descripción
dedicada de figuras/tablas, contexto por chunk, preguntas sintéticas).

Concentra dos cosas que antes estaban duplicadas o ausentes:
- Reintento que respeta el tiempo de espera que informa el propio proveedor
  (header `Retry-After` o el "Please try again in Xs" del mensaje 429), en vez
  de un backoff ciego que espera de más o de menos.
- Parseo tolerante de la respuesta (bloques ```json, texto alrededor del objeto).
"""

import json
import re
import time
from typing import Any, Dict, List, Optional

from logger import Logger

logger = Logger.get_logger(__name__)

try:
    from openai import RateLimitError, APIConnectionError, APITimeoutError
except Exception:  # pragma: no cover - el SDK siempre está en el entorno real
    class RateLimitError(Exception):
        pass

    class APIConnectionError(Exception):
        pass

    class APITimeoutError(Exception):
        pass


class QuotaExhaustedError(RuntimeError):
    """
    La cuenta se quedó sin crédito. No es transitorio: reintentar no sirve.
    """


# El SDK de OpenAI lanza RateLimitError para TODO HTTP 429, y el 429 de "te quedaste sin
# crédito" es indistinguible del de "pasaste el TPM". Sin separarlos, una cuenta agotada
# se reintenta con backoff exponencial como si fuera throttling.
#
# Pasó de verdad: 1760 reintentos contra un error permanente, la ingesta siguió moliendo
# los 5 documentos durante más de una hora para producir CERO, y el índice quedó vacío
# porque la limpieza previa ya había corrido. Peor todavía, la distribución de intentos
# parecía saturación de TPM (no decaía), así que el log llevaba a un diagnóstico
# equivocado.
_QUOTA_MARKERS = (
    "insufficient_quota",
    "credit_balance_exhausted",
    "no credits remaining",
    "exceeded your current quota",
)


def is_quota_exhausted(error: Exception) -> bool:
    """True si el error es de crédito/cuota agotada y no de throttling."""
    texto = str(error).lower()
    if any(marker in texto for marker in _QUOTA_MARKERS):
        return True
    # El SDK expone el código en el cuerpo de la respuesta
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        codigo = str((body.get("error") or {}).get("code") or "").lower()
        tipo = str((body.get("error") or {}).get("type") or "").lower()
        return codigo in _QUOTA_MARKERS or tipo in _QUOTA_MARKERS
    return False


def raise_if_quota_exhausted(error: Exception, label: str = "") -> None:
    """
    Convierte un 429 de facturación en QuotaExhaustedError para cortar el lote entero.

    Se llama desde los manejadores de reintento ANTES de calcular el backoff: la idea es
    que un error de crédito falle en segundos y con un mensaje claro, en vez de
    disfrazarse de rate limit durante una hora.
    """
    if is_quota_exhausted(error):
        raise QuotaExhaustedError(
            f"Sin crédito en la cuenta de OpenAI{f' [{label}]' if label else ''}. "
            "Reintentar no sirve: hay que recargar en "
            "https://platform.openai.com/settings/organization/billing/"
        ) from error


def retry_delay_from_error(
    error: Exception, attempt: int, base_delay: float = 2.0, max_delay: float = 60.0
) -> float:
    """
    Segundos a esperar antes de reintentar tras un error de rate limit.

    Prioriza el tiempo que indica el proveedor; si no está disponible, cae a un
    backoff exponencial.
    """
    try:
        response = getattr(error, "response", None)
        if response is not None:
            retry_after = response.headers.get("retry-after")
            if retry_after:
                return min(float(retry_after) + 0.5, max_delay)
    except Exception:
        pass

    try:
        match = re.search(r"try again in ([\d.]+)\s*s", str(error))
        if match:
            return min(float(match.group(1)) + 0.5, max_delay)
    except Exception:
        pass

    return min(base_delay * (2 ** attempt), max_delay)


def parse_json_response(text: str) -> Optional[Any]:
    """
    Extrae el primer objeto/array JSON de la respuesta del modelo.

    Tolera cercos ```json, prefijos/sufijos en prosa y comillas tipográficas.
    Devuelve None si no se pudo parsear nada.
    """
    if not text:
        return None

    cleaned = text.strip()

    # Quitar cercos de código
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        return json.loads(cleaned)
    except (ValueError, TypeError):
        pass

    # Buscar el primer bloque {...} o [...] balanceado de forma simple
    for opener, closer in (("{", "}"), ("[", "]")):
        start = cleaned.find(opener)
        end = cleaned.rfind(closer)
        if start != -1 and end > start:
            candidate = cleaned[start:end + 1]
            try:
                return json.loads(candidate)
            except (ValueError, TypeError):
                continue

    logger.debug(f"No se pudo parsear JSON de la respuesta: {cleaned[:200]}")
    return None


class LLMJsonClient:
    """
    Envoltorio fino sobre el cliente OpenAI para pedir respuestas JSON.

    Soporta contenido multimodal (texto + imágenes) para las pasadas dedicadas
    sobre recortes de figuras y tablas.
    """

    def __init__(
        self,
        client,
        model: str,
        max_retries: int = 8,
        temperature: float = 0.0,
        max_output_tokens: int = 1500,
    ):
        self.client = client
        self.model = model
        self.max_retries = max_retries
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens

    def complete_json(
        self,
        system_prompt: str,
        user_content: Any,
        label: str = "llm",
    ) -> Optional[Any]:
        """
        Pide una respuesta JSON al modelo.

        Args:
            system_prompt: mensaje de sistema
            user_content: string, o lista de partes al estilo Chat Completions
                (`{"type": "text", ...}` / `{"type": "image_url", ...}`)
            label: etiqueta para los logs

        Returns:
            El objeto parseado, o None si falló definitivamente.
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        for attempt in range(self.max_retries):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=self.temperature,
                    response_format={"type": "json_object"},
                )
                content = response.choices[0].message.content
                parsed = parse_json_response(content)
                if parsed is None:
                    logger.warning(f"[{label}] Respuesta no parseable como JSON")
                return parsed

            except (RateLimitError, APIConnectionError, APITimeoutError) as e:
                # Un 429 por falta de crédito llega como RateLimitError: cortar el lote
                # en vez de reintentarlo con backoff (ver raise_if_quota_exhausted).
                raise_if_quota_exhausted(e, label)
                if attempt >= self.max_retries - 1:
                    logger.error(f"[{label}] Error persistente tras {self.max_retries} intentos: {e}")
                    return None
                delay = retry_delay_from_error(e, attempt)
                logger.warning(
                    f"[{label}] Rate limit / red. Reintentando en {delay:.1f}s "
                    f"(intento {attempt + 1}/{self.max_retries})"
                )
                time.sleep(delay)

            except Exception as e:
                # response_format json_object no está soportado por algunos
                # modelos/versiones: se reintenta una vez sin ese parámetro.
                if "response_format" in str(e) and attempt == 0:
                    logger.warning(f"[{label}] response_format no soportado, reintentando sin él")
                    try:
                        response = self.client.chat.completions.create(
                            model=self.model,
                            messages=messages,
                            temperature=self.temperature,
                        )
                        return parse_json_response(response.choices[0].message.content)
                    except Exception as inner:
                        logger.error(f"[{label}] Falló el reintento sin response_format: {inner}")
                        return None
                # Defensa en profundidad: si el 429 de cuota llega envuelto en otra
                # excepción (o el SDK cambia de tipo), igual tiene que cortar el lote y
                # no quedar tapado como "error no recuperable" de un chunk suelto.
                raise_if_quota_exhausted(e, label)
                logger.error(f"[{label}] Error no recuperable: {e}")
                return None

        return None


def image_content_part(image_path: str) -> Optional[Dict[str, Any]]:
    """Construye la parte `image_url` (data URI base64) para un recorte local."""
    import base64
    import os

    if not image_path or not os.path.isfile(image_path):
        return None

    try:
        with open(image_path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("utf-8")
        return {
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{encoded}", "detail": "high"},
        }
    except Exception as e:
        logger.warning(f"No se pudo codificar la imagen {image_path}: {e}")
        return None


def text_content_part(text: str) -> Dict[str, Any]:
    return {"type": "text", "text": text}


def run_parallel(tasks: List[Any], worker, max_workers: int, label: str = "tarea") -> List[Any]:
    """
    Ejecuta `worker(task)` sobre cada elemento en paralelo, preservando el orden.

    Las llamadas al LLM son I/O de red: el paralelismo acotado es lo que hace
    viable enriquecer cientos de chunks. Un fallo individual devuelve None en su
    posición en vez de abortar el lote.

    La excepción es QuotaExhaustedError: esa SÍ aborta, porque si la cuenta no tiene
    crédito ninguna de las llamadas siguientes va a funcionar y seguir es tiempo tirado.
    """
    from concurrent.futures import ThreadPoolExecutor

    if not tasks:
        return []

    workers = max(1, int(max_workers))
    results: List[Any] = [None] * len(tasks)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_index = {
            executor.submit(worker, task): i for i, task in enumerate(tasks)
        }
        for future, index in future_to_index.items():
            try:
                results[index] = future.result()
            except QuotaExhaustedError:
                logger.error(f"[{label}] Sin crédito en la cuenta: se aborta el lote")
                raise
            except Exception as e:
                logger.error(f"[{label}] Falló el elemento {index}: {e}")
                results[index] = None

    return results
