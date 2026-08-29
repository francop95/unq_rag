"""Envuelve los documentos explicativos en HTML completo para abrirlos local.

Los fuentes estan escritos como fragmentos, sin doctype/head/body. Este script
genera las versiones autocontenidas, que se abren con doble clic y se ven igual.

    python3 docs/build_standalone.py                 # todos
    python3 docs/build_standalone.py el-camino-...   # uno solo
"""

import pathlib
import re
import sys

AQUI = pathlib.Path(__file__).parent
SUFIJO_LOCAL = ".local.html"

PLANTILLA = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{cabeza}</head>
<body>
{cuerpo}</body>
</html>
"""

# El <title>, el <link> de fuentes y el <style> van al head; el resto al body.
AL_HEAD = (
    r"<title>.*?</title>\s*",
    r'<link rel="stylesheet"[^>]*>\s*',
    r"<style>.*?</style>\s*",
)


def fuentes() -> list[pathlib.Path]:
    """Los .html del directorio que no son ya una salida generada."""
    if len(sys.argv) > 1:
        elegidos = []
        for nombre in sys.argv[1:]:
            p = AQUI / nombre
            if not p.suffix:
                p = p.with_suffix(".html")
            elegidos.append(p)
        return elegidos
    return sorted(p for p in AQUI.glob("*.html") if not p.name.endswith(SUFIJO_LOCAL))


def envolver(fuente: pathlib.Path) -> pathlib.Path:
    texto = fuente.read_text(encoding="utf-8")

    cabeza: list[str] = []
    cuerpo = texto
    for patron in AL_HEAD:
        m = re.search(patron, cuerpo, re.DOTALL)
        if m:
            cabeza.append(m.group(0).rstrip() + "\n")
            cuerpo = cuerpo[: m.start()] + cuerpo[m.end() :]

    salida = fuente.with_suffix("").with_suffix(".local.html")
    salida.write_text(
        PLANTILLA.format(cabeza="".join(cabeza), cuerpo=cuerpo.strip() + "\n"),
        encoding="utf-8",
    )
    return salida


def main() -> None:
    for fuente in fuentes():
        if not fuente.exists():
            print(f"no existe: {fuente.name}")
            continue
        salida = envolver(fuente)
        print(f"generado: docs/{salida.name}  ({salida.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
