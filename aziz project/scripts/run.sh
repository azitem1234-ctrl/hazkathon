#!/usr/bin/env bash
# Launch the EcoBiz Copilot dashboard: ./scripts/run.sh  (PORT=9000 to change port)
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d .venv ]; then
    echo "Creating virtual environment…"
    python3 -m venv .venv
    .venv/bin/pip install -q -r requirements.txt
fi

echo "Serving on http://127.0.0.1:${PORT:-8000}"
exec .venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port "${PORT:-8000}"
