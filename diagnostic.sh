#!/usr/bin/env bash
# ==============================================================================
# Digital Signage Client Diagnostic Tool
# ==============================================================================
set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Locate Python binary (Installed venv -> Local venv -> System python3)
if [ -x "/opt/digitalsignage/venv/bin/python" ]; then
    PYTHON_BIN="/opt/digitalsignage/venv/bin/python"
    PYTHONPATH="/opt/digitalsignage/client:${PYTHONPATH}"
elif [ -x "${SCRIPT_DIR}/.venv/bin/python" ]; then
    PYTHON_BIN="${SCRIPT_DIR}/.venv/bin/python"
    PYTHONPATH="${SCRIPT_DIR}:${PYTHONPATH}"
else
    PYTHON_BIN="$(which python3 || echo "python")"
    PYTHONPATH="${SCRIPT_DIR}:${PYTHONPATH}"
fi

export PYTHONPATH

if [ "$1" == "--json" ]; then
    "${PYTHON_BIN}" -m app.diagnostic --json
else
    "${PYTHON_BIN}" -m app.diagnostic "$@"
fi
