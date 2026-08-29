#!/usr/bin/env bash
# Suite de regresión. Cada test corresponde a un bug real que se encontró y arregló:
# si alguno se rompe, ese bug volvió.
#
# Los dos proyectos tienen venv propio (son servicios desplegables por separado), así
# que se corren por separado.
set -o pipefail
fallos=0

echo "════ API: invariantes del retrieval ════"
( cd API && TOKENIZERS_PARALLELISM=false ./.venv/bin/python -m pytest tests/test_retrieval_invariants.py -q ) || fallos=1

echo
echo "════ Ingestion: invariantes de la ingesta ════"
( cd Ingestion && ./.venv/bin/python -m pytest tests/test_ingestion_invariants.py -q ) || fallos=1

echo
if [ "$fallos" -eq 0 ]; then
  echo "✅ Todo verde"
else
  echo "❌ Hay tests fallando"
fi
exit $fallos
