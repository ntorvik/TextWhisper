#!/usr/bin/env bash
# scripts/setup-cuda.sh -- install pip-managed CUDA 12 runtime DLLs (Linux only).
# macOS doesn't have CUDA; this script is a no-op there.

set -euo pipefail
cd "$(dirname "$0")/.."

if [[ "$(uname)" != "Linux" ]]; then
    echo "[INFO] CUDA install skipped — not on Linux."
    exit 0
fi

if [[ ! -f "venv/bin/activate" ]]; then
    echo "[ERROR] venv not found. Run scripts/setup.sh first." >&2
    exit 1
fi

# shellcheck disable=SC1091
source venv/bin/activate

echo "Installing pip-managed CUDA 12 runtime libraries (cuBLAS + cuDNN 9)..."
python -m pip install --upgrade nvidia-cublas-cu12 nvidia-cudnn-cu12
