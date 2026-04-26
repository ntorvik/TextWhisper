#!/usr/bin/env bash
# scripts/setup.sh -- create venv and install Python dependencies on Linux/macOS.

set -euo pipefail
cd "$(dirname "$0")/.."

# Pick a python interpreter — prefer python3.12, then python3.11, then python3.
PY=""
for candidate in python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PY="$candidate"
        break
    fi
done
if [[ -z "$PY" ]]; then
    echo "[ERROR] No suitable Python found. Install Python 3.11+ first." >&2
    exit 1
fi

if [[ ! -d "venv" ]]; then
    echo "Creating virtual environment with $PY..."
    "$PY" -m venv venv
fi

# shellcheck disable=SC1091
source venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
