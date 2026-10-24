"""
Abre la pantalla de revisión de la ingesta
==========================================

    python scripts/revisar_web.py                  # http://127.0.0.1:5055
    python scripts/revisar_web.py --puerto 5060

Lo mismo que `scripts/revisar_ingesta.py` hace en consola, pero con los
recortes a la vista y con los botones que lanzan el pipeline. Las dos leen las
mismas corridas y escriben la misma base, así que se pueden mezclar.

Escucha en 127.0.0.1 a propósito: lanza el troceo y la relectura de figuras,
que gastan plata, y no pide contraseña.
"""

import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "src"))

from revision_web.servidor import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
