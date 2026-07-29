#!/usr/bin/env python
# coding: utf-8

from collections import defaultdict
from typing import List, Any, Dict
import logging

# logging
logger = logging.getLogger('app.FormatResponse')

# To format the response to handle multiple document and multiple page citation
def _to_list_flat(x: Any) -> List[Any]:
    """Normaliza a lista 1D (si viene como escalar, lista o lista de listas)."""
    if x is None:
        return []
    if isinstance(x, (str, bytes)):
        return [x]
    if not isinstance(x, (list, tuple)):
        return [x]
    # aplana un nivel si hay listas anidadas
    out = []
    for el in x:
        if isinstance(el, (list, tuple)):
            out.extend(el)
        else:
            out.append(el)
    return out

def format_response(responses: List[Dict[str, Any]], query_id: str) -> List[Dict[str, Any]]:
    """
    Estructura de salida por item:
      {
        ...otras claves de `res` salvo file_name/page...,
        "files": [
          {"file_name": "<str>", "pages": [<int>, ...]},
          ...
        ]
      }
    """
    logger.info(f"[{query_id}] [FormatResponse] Inside format_response")
    formated_responses: List[Dict[str, Any]] = []
    try:
        for res in responses or []:
            res_dict: Dict[str, Any] = {}

            # Copia todas las claves excepto file_name/page
            for k, v in (res or {}).items():
                if k not in ("file_name", "page"):
                    res_dict[k] = v

            if not res.get("is_valid"):
                res_dict["files"] = []
                formated_responses.append(res_dict)
                continue

            file_names = _to_list_flat(res.get("file_name"))
            pages      = _to_list_flat(res.get("page"))

            # Si las longitudes no coinciden, truncamos al mínimo para zipear sin errores
            n = min(len(file_names), len(pages))
            file_names = file_names[:n]
            pages      = pages[:n]

            # Normaliza tipos
            norm_pairs = []
            for f, p in zip(file_names, pages):
                # Convierte nombre de archivo a str (si viene como lista/dict accidentalmente)
                try:
                    f_str = str(f) if not isinstance(f, str) else f
                except Exception:
                    f_str = repr(f)

                # Convierte página a int si posible; si no, a str
                try:
                    p_val = int(p)
                except Exception:
                    try:
                        p_val = int(float(p))
                    except Exception:
                        p_val = p  # deja como está (str u otro)
                norm_pairs.append((f_str, p_val))

            # Agrupa en dict -> set(páginas)
            grouped: Dict[str, set] = defaultdict(set)
            for f_str, p_val in norm_pairs:
                grouped[f_str].add(p_val)

            # Construye lista final (ordena páginas y elimina duplicados)
            doc_list = []
            for f_str, pset in grouped.items():
                pages_sorted = sorted(pset, key=lambda x: (isinstance(x, str), x))
                doc_list.append({"file_name": f_str, "pages": pages_sorted})

            res_dict["files"] = doc_list
            formated_responses.append(res_dict)

        logger.info(f"[{query_id}] [FormatResponse] Response formatting done")
    except Exception as e:
        logger.error(f"[{query_id}][FormatResponse] Formatting responses failed: {e}", exc_info=True)

    return formated_responses