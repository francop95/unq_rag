"""
Medidor de consumo de la API de OpenAI
======================================

La ingesta es un proceso batch que cuesta dinero de verdad: cada página de PDF
pasa por un modelo de visión, cada recorte de figura por otra llamada, y cada
chunk por una de enriquecimiento. Hasta acá cada respuesta traía su campo
`usage` y el pipeline lo tiraba, así que la única forma de saber cuánto salió
una corrida era mirar la factura al día siguiente.

Esto acumula los tokens de las cinco llamadas que hace el pipeline —chunking
de página, pasada de figuras, enriquecimiento, embeddings y el proveedor de
embeddings— y los convierte a dólares.

Sobre los precios
-----------------
Los tokens son medidos y exactos. Los precios de PRECIOS_USD son de referencia
y cambian sin aviso: verificar en https://openai.com/api/pricing/ antes de
tomar una decisión de plata. Se pueden pisar sin tocar el código con la
variable de entorno OPENAI_PRECIOS, en formato
`modelo:entrada_por_1M:salida_por_1M`, separados por coma.

Un modelo sin precio conocido cuenta igual sus tokens y aparece en el reporte
marcado como sin tarifar, que es mucho mejor que asumir cero y reportar un
total que miente.
"""

import os
import threading
from collections import defaultdict
from typing import Any, Dict, Optional, Tuple

# USD por 1M de tokens: (entrada, salida). Los embeddings solo cobran entrada.
PRECIOS_USD: Dict[str, Tuple[float, float]] = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4-0613": (30.00, 60.00),
    "text-embedding-3-large": (0.13, 0.0),
    "text-embedding-3-small": (0.02, 0.0),
}


def _precios_del_entorno() -> Dict[str, Tuple[float, float]]:
    crudo = os.getenv("OPENAI_PRECIOS", "").strip()
    if not crudo:
        return {}
    salida = {}
    for entrada in crudo.split(","):
        partes = entrada.strip().split(":")
        if len(partes) != 3:
            continue
        try:
            salida[partes[0].strip()] = (float(partes[1]), float(partes[2]))
        except ValueError:
            continue
    return salida


def _tarifa(modelo: str) -> Optional[Tuple[float, float]]:
    """Tarifa del modelo, tolerando sufijos de fecha ('gpt-4o-2024-08-06')."""
    tabla = {**PRECIOS_USD, **_precios_del_entorno()}
    if modelo in tabla:
        return tabla[modelo]
    # El más largo que sea prefijo: evita que 'gpt-4' matchee 'gpt-4o-mini'
    candidatos = [k for k in tabla if modelo.startswith(k)]
    return tabla[max(candidatos, key=len)] if candidatos else None


class MedidorDeConsumo:
    """
    Acumulador de tokens por (etapa, modelo).

    Es thread-safe porque las pasadas de figuras y de enriquecimiento corren
    con un ThreadPoolExecutor: sin el lock, dos hilos que suman a la vez
    pierden llamadas y el total queda por debajo del real, que es justo el
    error que no se quiere en un medidor de costos.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._datos: Dict[Tuple[str, str], Dict[str, int]] = defaultdict(
            lambda: {"entrada": 0, "salida": 0, "llamadas": 0}
        )

    def registrar(self, etapa: str, modelo: str, usage: Any) -> None:
        """
        Suma el `usage` de una respuesta. Nunca lanza: un medidor que rompe la
        ingesta es peor que no tener medidor.
        """
        if usage is None:
            return
        try:
            entrada = int(getattr(usage, "prompt_tokens", None)
                          or getattr(usage, "input_tokens", None) or 0)
            salida = int(getattr(usage, "completion_tokens", None)
                         or getattr(usage, "output_tokens", None) or 0)
        except (TypeError, ValueError):
            return

        with self._lock:
            d = self._datos[(etapa, modelo or "?")]
            d["entrada"] += entrada
            d["salida"] += salida
            d["llamadas"] += 1

    def total_usd(self) -> Tuple[float, bool]:
        """(costo acumulado, si quedó alguna llamada sin tarifar)."""
        total = 0.0
        faltantes = False
        with self._lock:
            items = list(self._datos.items())
        for (_, modelo), d in items:
            tarifa = _tarifa(modelo)
            if tarifa is None:
                faltantes = True
                continue
            total += d["entrada"] * tarifa[0] / 1e6 + d["salida"] * tarifa[1] / 1e6
        return total, faltantes

    def tokens_sin_tarifa(self) -> Tuple[int, int]:
        """(entrada, salida) de los modelos que no tienen precio en la tabla."""
        entrada = salida = 0
        with self._lock:
            items = list(self._datos.items())
        for (_, modelo), d in items:
            if _tarifa(modelo) is None:
                entrada += d["entrada"]
                salida += d["salida"]
        return entrada, salida

    def reporte(self) -> str:
        with self._lock:
            items = sorted(self._datos.items(), key=lambda kv: -(kv[1]["entrada"] + kv[1]["salida"]))
        if not items:
            return "Sin llamadas a la API registradas."

        lineas = [
            "%-22s %-26s %9s %11s %11s %10s" % (
                "etapa", "modelo", "llamadas", "entrada", "salida", "USD"),
            "-" * 94,
        ]
        for (etapa, modelo), d in items:
            tarifa = _tarifa(modelo)
            if tarifa is None:
                costo = "sin tarifa"
            else:
                costo = "%.4f" % (d["entrada"] * tarifa[0] / 1e6 + d["salida"] * tarifa[1] / 1e6)
            lineas.append("%-22s %-26s %9d %11d %11d %10s" % (
                etapa[:22], modelo[:26], d["llamadas"], d["entrada"], d["salida"], costo))

        total, faltantes = self.total_usd()
        lineas.append("-" * 94)
        lineas.append("%-60s %32s" % ("TOTAL", "USD %.4f" % total))
        if faltantes:
            lineas.append("Hay modelos sin precio en la tabla: el total es un piso, no el costo real.")
        lineas.append("Precios de referencia; verificar en https://openai.com/api/pricing/")
        return "\n".join(lineas)

    def reiniciar(self) -> None:
        with self._lock:
            self._datos.clear()


# Instancia compartida por todo el pipeline. Es global a propósito: las
# llamadas salen de cinco módulos distintos y pasar el medidor por parámetro
# obligaría a cambiar la firma de media docena de funciones que no tienen nada
# que ver con medir.
MEDIDOR = MedidorDeConsumo()


def registrar(etapa: str, modelo: str, usage: Any) -> None:
    MEDIDOR.registrar(etapa, modelo, usage)
