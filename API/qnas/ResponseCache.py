"""
Caché de respuestas por coincidencia EXACTA de la pregunta (texto normalizado).

Por qué exacto y no semántico, que sería lo "de manual": se midió la similitud coseno
(text-embedding-3-large) entre pares de preguntas de este dominio y **las bandas se
solapan**, así que no existe un umbral que separe "es la misma pregunta" de "es otra":

    corriente máxima de ENTRADA  vs  de SALIDA              0.916   ← distinto
    "el variador no arranca desde el teclado, ¿qué reviso?"
      vs la misma con otra redacción                        0.929   ← igual
    control SNK de dos hilos     vs  control SRC             0.720   ← distinto
    "no arranca desde el teclado" vs "no para desde el teclado"  0.865  ← distinto

Con el umbral 0.95 que estaba configurado no habría acertado ni las reformulaciones
legítimas (0.87–0.93), y bajarlo a 0.90 para capturarlas metería adentro los pares que
significan lo contrario. En un asistente de mantenimiento eso devuelve el número de
parámetro equivocado o el borne equivocado: el modo de falla es peligroso, no molesto.

La coincidencia exacta normalizada cubre el caso real —la misma persona repreguntando, o
la consulta compartida entre turnos— con riesgo cero y sin pagar un embedding. Reemplaza
el camino anterior (`CachedQna` sobre Azure Search), que además nunca tuvo ruta de
escritura: leía de un índice que nadie poblaba, así que siempre fallaba.
"""
import json
import logging
import os
import re
import time
import unicodedata
from typing import Any, Dict, List, Optional

logger = logging.getLogger("app.ResponseCache")


def normalize_question(text: str) -> str:
    """
    Clave de caché: minúsculas, sin tildes, sin puntuación, espacios colapsados.

    Así "El variador no arranca desde el teclado, ¿qué reviso?" y
    "el variador no arranca desde el teclado que reviso" son la misma entrada, que es lo
    que pasa cuando alguien la vuelve a tipear. Lo que NO hace es acercar preguntas
    distintas: si cambia una palabra de contenido, la clave cambia.
    """
    text = unicodedata.normalize("NFKD", str(text or "").lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())


class ResponseCache:
    """Caché en memoria con respaldo en disco, para que sobreviva a un reinicio."""

    def __init__(
        self,
        path: str,
        max_entries: int = 500,
        ttl_seconds: Optional[int] = None,
        index_path: Optional[str] = None,
    ):
        self.path = path
        self.max_entries = max_entries
        # 0 y None significan lo mismo: sin expiración
        self.ttl_seconds = ttl_seconds or None
        self.index_path = index_path
        self._entries: Dict[str, Dict[str, Any]] = {}
        self._load()

    @staticmethod
    def _index_fingerprint(index_path: Optional[str]) -> str:
        """
        Huella del índice del que salieron las respuestas cacheadas: cantidad de
        archivos y mtime más reciente bajo el directorio de Chroma. Cualquier
        re-ingesta la cambia.
        """
        if not index_path or not os.path.isdir(index_path):
            return ""
        newest, count = 0.0, 0
        for root, _dirs, files in os.walk(index_path):
            for name in files:
                try:
                    newest = max(newest, os.path.getmtime(os.path.join(root, name)))
                    count += 1
                except OSError:
                    continue
        return f"{count}:{int(newest)}"

    def _load(self) -> None:
        """
        Carga el caché y lo DESCARTA si el índice cambió desde que se escribió.

        Sin esto, una respuesta guardada sobrevive a una re-ingesta y se sirve como
        buena citando páginas y chunks que ya no existen. Se resuelve acá y no llamando
        a `invalidate()` desde la ingesta a propósito: API/ e Ingestion/ son servicios
        separados, y un caché que se valida solo no depende de que alguien se acuerde.
        """
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                stored = json.load(f)
        except Exception as e:
            logger.warning(f"[ResponseCache] No se pudo cargar {self.path}: {e}")
            return

        # Formato: {"fingerprint": "...", "entries": {...}}. Un archivo viejo sin
        # fingerprint se descarta, que es lo conservador.
        fingerprint = self._index_fingerprint(self.index_path)
        if not isinstance(stored, dict) or stored.get("fingerprint") != fingerprint:
            logger.info(
                "[ResponseCache] El índice cambió desde la última corrida: "
                "se descarta el caché de respuestas"
            )
            self._entries = {}
            self._save()
            return

        self._entries = stored.get("entries") or {}
        logger.info(f"[ResponseCache] {len(self._entries)} respuestas cacheadas reusadas")

    def _save(self) -> None:
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            payload = {
                "fingerprint": self._index_fingerprint(self.index_path),
                "entries": self._entries,
            }
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"[ResponseCache] No se pudo guardar {self.path}: {e}")

    def get(self, question: str) -> Optional[List[Dict[str, Any]]]:
        entry = self._entries.get(normalize_question(question))
        if not entry:
            return None
        if self.ttl_seconds and (time.time() - entry.get("stored_at", 0)) > self.ttl_seconds:
            return None
        return entry.get("response")

    def put(self, question: str, response: List[Dict[str, Any]]) -> None:
        """
        Guarda una respuesta. Solo se llama con respuestas válidas: cachear un "no
        encontré información" congelaría ese fallo para siempre, incluso después de
        re-ingestar el documento que faltaba.
        """
        if not response:
            return
        key = normalize_question(question)
        if not key:
            return

        self._entries[key] = {"response": response, "stored_at": time.time()}

        # Poda LRU-ish por antigüedad de escritura (no hay tracking de lecturas: para
        # unos cientos de entradas no vale la complejidad).
        if len(self._entries) > self.max_entries:
            sobra = len(self._entries) - self.max_entries
            for old_key in sorted(self._entries, key=lambda k: self._entries[k]["stored_at"])[:sobra]:
                del self._entries[old_key]

        self._save()

    def invalidate(self) -> int:
        """Vacía el caché. Hay que llamarlo después de re-ingestar: las respuestas
        guardadas citan páginas y chunks del índice viejo."""
        n = len(self._entries)
        self._entries = {}
        self._save()
        logger.info(f"[ResponseCache] Invalidado: {n} entradas borradas")
        return n
