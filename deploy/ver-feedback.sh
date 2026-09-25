#!/usr/bin/env bash
#
# Consulta la telemetría del servidor desplegado, sin tener que recordar la
# ruta de la base ni el docker compose con sus dos ficheros.
#
#   ./deploy/ver-feedback.sh                 resumen y motivos más frecuentes
#   ./deploy/ver-feedback.sh --negativos     solo las respuestas que no sirvieron
#   ./deploy/ver-feedback.sh --id <query_id> una ejecución, chunk por chunk
#   ./deploy/ver-feedback.sh --sql "SELECT ..."   consulta libre
#   ./deploy/ver-feedback.sh --tablas        esquema de las tres tablas
#   ./deploy/ver-feedback.sh --bajar         se trae la base a data/telemetria/
#
# La base vive en el volumen montado del servidor, así que sobrevive a los
# redespliegues. El host no tiene sqlite3: todo pasa por el contenedor de la API.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
die() { printf '\n\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[ -f "$HERE/.deploy-target" ] || die "falta deploy/.deploy-target. Corré primero: ./deploy/provision.sh"
# shellcheck disable=SC1091
source "$HERE/.deploy-target"
[ -n "${IP:-}" ] && [ -n "${KEY_FILE:-}" ] || die ".deploy-target incompleto"

SSH="ssh -i $KEY_FILE -o StrictHostKeyChecking=accept-new ec2-user@$IP"
DC="docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml"
EN_API="cd /home/ec2-user/unq-rag && $DC exec -T api"

case "${1:-}" in
  --bajar)
    mkdir -p "$ROOT/Ingestion/data/telemetria"
    scp -i "$KEY_FILE" -o StrictHostKeyChecking=accept-new \
      "ec2-user@$IP:/home/ec2-user/unq-rag/Ingestion/data/telemetria/ejecuciones.sqlite3" \
      "$ROOT/Ingestion/data/telemetria/ejecuciones_servidor.sqlite3"
    echo "  en Ingestion/data/telemetria/ejecuciones_servidor.sqlite3"
    echo "  para revisarla localmente:"
    echo "    cd API && TELEMETRY_DB_PATH=../Ingestion/data/telemetria/ejecuciones_servidor.sqlite3 \\"
    echo "      python -m telemetria.revisar"
    ;;

  --tablas)
    $SSH "$EN_API python -c \"
import sqlite3
from telemetria.store import ruta_db
c = sqlite3.connect(ruta_db())
for (t,) in c.execute('SELECT name FROM sqlite_master WHERE type=\\\"table\\\" ORDER BY name'):
    if t.startswith('sqlite'): continue
    n = c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
    print(f'{t}  ({n} filas)')
    for r in c.execute(f'PRAGMA table_info({t})'):
        print(f'   {r[1]:<22} {r[2]}')
    print()
\""
    ;;

  --sql)
    [ -n "${2:-}" ] || die "falta la consulta: ./deploy/ver-feedback.sh --sql \"SELECT ...\""
    # La consulta viaja como variable de entorno, no interpolada en el código:
    # una pregunta con comillas rompería el python -c y, peor, podría ejecutar
    # algo distinto de lo que se escribió.
    $SSH "$EN_API env CONSULTA=\"$2\" python -c \"
import os, sqlite3
from telemetria.store import ruta_db
c = sqlite3.connect(ruta_db())
c.row_factory = sqlite3.Row
filas = list(c.execute(os.environ['CONSULTA']))
if not filas:
    print('(sin resultados)')
else:
    cols = filas[0].keys()
    print(' | '.join(cols))
    print('-' * 100)
    for f in filas:
        print(' | '.join(str(f[k])[:40] for k in cols))
    print(f'\\n{len(filas)} filas')
\""
    ;;

  *)
    $SSH "$EN_API python -m telemetria.revisar ${*:-}"
    ;;
esac
