#!/usr/bin/env bash
# run.sh -- launch TextWhisper from the project venv.
# Most users will launch via the platform launcher (Linux: app menu;
# macOS: TextWhisper.app once built). This script is the fallback.

set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f "venv/bin/activate" ]]; then
    echo "[ERROR] venv not found. Run ./install.sh first." >&2
    exit 1
fi

# shellcheck disable=SC1091
source venv/bin/activate

mkdir -p logs
echo "Starting TextWhisper... (log: logs/textwhisper.log)"
python main.py
