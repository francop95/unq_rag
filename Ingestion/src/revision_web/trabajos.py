"""
Lo que tarda, lanzado desde la pantalla
=======================================

Tres de las cosas que ofrece la pantalla no entran en una petición HTTP:
trocear un documento son horas y plata —1,17 millones de tokens de salida para
un manual de 126 páginas—, indexarlo son minutos, y releer una figura son
cinco o seis llamadas al modelo de visión. Las tres se lanzan acá y se miran
por el log, igual que si se hubieran corrido en la terminal.

Uno a la vez, en todo el servidor
---------------------------------
No es una simplificación: dos troceos del mismo documento escriben la misma
carpeta de corrida, y dos indexados escriben el mismo índice de Chroma. El
segundo pedido se rechaza con un 409 en vez de encolarse, porque encolar
esconde el error —la persona cree que apretó una vez— y porque lo que hay que
hacer con el segundo casi siempre es no hacerlo.

El log es lo que la persona ve, no un archivo
---------------------------------------------
Se guarda en memoria, acotado: una ingesta larga imprime decenas de miles de
líneas y lo que importa es el final. El subproceso corre con
`PYTHONUNBUFFERED=1` porque si no Python junta la salida en un buffer de 8 KB
y la pantalla se queda quieta media hora antes de mostrar todo de golpe.
"""

import os
import signal
import subprocess
import sys
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

# Alcanza para ver el final de una corrida larga sin que el proceso crezca
# indefinidamente. Una ingesta completa imprime bastante más que esto.
MAX_LINEAS = 4000

_lock = threading.Lock()
_actual: Optional["Trabajo"] = None


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


class EnCurso(Exception):
    """Ya hay un trabajo corriendo; el que llega se rechaza."""


class Trabajo:
    def __init__(self, clase: str, etiqueta: str, contexto: Dict[str, Any] = None):
        self.clase = clase            # procesar | indexar | releer
        self.etiqueta = etiqueta
        self.contexto = contexto or {}
        self.estado = "corriendo"     # corriendo | ok | error | cancelado
        self.inicio = _ahora()
        self.fin: Optional[str] = None
        self.resultado: Any = None
        self.error: Optional[str] = None
        self._lineas: deque = deque(maxlen=MAX_LINEAS)
        self._proceso: Optional[subprocess.Popen] = None
        self._cancelado = False
        self._escrituras = 0          # cuántas líneas se escribieron en total

    # -- escritura, desde el hilo del trabajo --------------------------------

    def log(self, linea: str) -> None:
        with _lock:
            self._lineas.append(linea.rstrip("\n"))
            self._escrituras += 1

    def _terminar(self, estado: str, error: str = None, resultado: Any = None):
        with _lock:
            self.estado = estado
            self.error = error
            self.resultado = resultado
            self.fin = _ahora()

    # -- lectura, desde el hilo de Flask -------------------------------------

    def instantanea(self, desde: int = 0) -> Dict[str, Any]:
        """El estado del trabajo y las líneas que al cliente le faltan.

        `desde` es cuántas líneas ya tiene el cliente. Se devuelven las que
        siguen, para no remandar el log entero en cada sondeo. Si el búfer ya
        rotó y se perdieron líneas que el cliente nunca vio, se avisa en vez de
        mandar un pedazo del medio como si fuera la continuación.
        """
        with _lock:
            total = self._escrituras
            disponibles = len(self._lineas)
            primera = total - disponibles       # índice de _lineas[0]
            perdidas = max(0, primera - desde)
            arranque = max(0, desde - primera)
            lineas = list(self._lineas)[arranque:]
            return {
                "clase": self.clase,
                "etiqueta": self.etiqueta,
                "contexto": self.contexto,
                "estado": self.estado,
                "inicio": self.inicio,
                "fin": self.fin,
                "error": self.error,
                "resultado": self.resultado,
                "lineas": lineas,
                "perdidas": perdidas,
                "total": total,
            }

    def cancelar(self) -> bool:
        """Mata el subproceso. Un trabajo en hilo solo queda marcado.

        Cancelar importa: un troceo arrancado por error sigue gastando plata
        hasta que termine, y cerrar la pestaña no lo frena.
        """
        self._cancelado = True
        proceso = self._proceso
        if proceso is None or proceso.poll() is not None:
            return False
        try:
            # Al grupo entero: el pipeline larga hilos y procesos hijos, y
            # matar solo al padre deja al modelo de visión trabajando.
            os.killpg(os.getpgid(proceso.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            proceso.terminate()
        return True


def actual() -> Optional[Trabajo]:
    return _actual


def _arrancar(trabajo: Trabajo, cuerpo: Callable[[], None]) -> Trabajo:
    global _actual
    with _lock:
        if _actual is not None and _actual.estado == "corriendo":
            raise EnCurso(f"ya está corriendo: {_actual.etiqueta}")
        _actual = trabajo
    threading.Thread(target=cuerpo, daemon=True).start()
    return trabajo


def lanzar_comando(clase: str, etiqueta: str, argv: List[str], cwd: str,
                   contexto: Dict[str, Any] = None) -> Trabajo:
    """Corre un comando y vuelca su salida al log del trabajo."""
    trabajo = Trabajo(clase, etiqueta, contexto)
    trabajo.log("$ " + " ".join(argv))

    def cuerpo():
        entorno = dict(os.environ, PYTHONUNBUFFERED="1")
        try:
            proceso = subprocess.Popen(
                argv, cwd=cwd, env=entorno, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1,
                errors="replace", start_new_session=True)
        except OSError as e:
            trabajo._terminar("error", f"no se pudo lanzar: {e}")
            return
        trabajo._proceso = proceso
        for linea in proceso.stdout:
            trabajo.log(linea)
        codigo = proceso.wait()
        if trabajo._cancelado:
            trabajo.log("\n⏹  cancelado desde la pantalla.")
            trabajo._terminar("cancelado")
        elif codigo == 0:
            trabajo._terminar("ok")
        else:
            trabajo._terminar("error", f"terminó con código {codigo}")

    return _arrancar(trabajo, cuerpo)


def lanzar_funcion(clase: str, etiqueta: str, fn: Callable[[Callable[[str], None]], Any],
                   contexto: Dict[str, Any] = None) -> Trabajo:
    """Corre `fn(log)` en un hilo; lo que devuelva queda en `resultado`."""
    trabajo = Trabajo(clase, etiqueta, contexto)

    def cuerpo():
        try:
            resultado = fn(trabajo.log)
        except Exception as e:  # noqa: BLE001 — va entero al log de la pantalla
            import traceback
            trabajo.log(traceback.format_exc())
            trabajo._terminar("error", f"{type(e).__name__}: {e}")
            return
        trabajo._terminar("ok", resultado=resultado)

    return _arrancar(trabajo, cuerpo)


def python_del_entorno() -> str:
    """El intérprete con el que correr el pipeline: el mismo que este servidor.

    Si fuera otro, el subproceso no encontraría chromadb ni openai y fallaría
    con un ImportError a mitad de una corrida que ya gastó plata.
    """
    return sys.executable
