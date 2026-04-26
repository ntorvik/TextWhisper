#!/usr/bin/env bash
# install.sh -- one-click installer for TextWhisper on Linux / macOS.
#
# Steps:
#   1. Create the Python venv and install runtime dependencies.
#   2. (Linux only, optional) Install pip-managed CUDA 12 runtime if an
#      NVIDIA GPU is detected.
#   3. (Linux) Create a .desktop launcher in ~/.local/share/applications/.
#      (macOS) Build a real .app bundle via PyInstaller (optional, slow).
#
# After this finishes, run ./run.sh or use your platform's app launcher.

set -euo pipefail

cd "$(dirname "$0")"

echo
echo "==========================================================="
echo "                  TextWhisper Installer"
echo "==========================================================="
echo

# ---------- 1. Python deps ----------
echo "[1/3] Setting up Python virtual environment + dependencies..."
bash scripts/setup.sh
echo "       ...done."
echo

# ---------- 2. CUDA (Linux only) ----------
echo "[2/3] Detecting NVIDIA GPU..."
if [[ "$(uname)" == "Linux" ]] && command -v nvidia-smi >/dev/null 2>&1; then
    echo "       NVIDIA GPU detected. Installing CUDA 12 runtime libraries..."
    if bash scripts/setup-cuda.sh; then
        echo "       ...done."
    else
        echo "       [WARN] CUDA library install failed. App will fall back to CPU mode."
    fi
elif [[ "$(uname)" == "Darwin" ]]; then
    echo "       macOS detected. CUDA is not available on Apple Silicon — the"
    echo "       app will run in CPU mode (or use Apple's NEON via faster-whisper)."
else
    echo "       No NVIDIA GPU detected. App will run in CPU mode."
fi
echo

# ---------- 3. Platform-specific launcher ----------
echo "[3/3] Creating launcher for your desktop environment..."
case "$(uname)" in
    Linux)
        bash scripts/create-desktop-entry.sh && \
            echo "       ...done. Look for 'TextWhisper' in your application menu." || \
            echo "       [WARN] .desktop entry creation failed — use ./run.sh."
        ;;
    Darwin)
        echo "       To install as a real .app bundle, run:  bash scripts/build-app.sh"
        echo "       For now, launch with:                   ./run.sh"
        ;;
    *)
        echo "       Unknown platform — launch with: ./run.sh"
        ;;
esac
echo

echo "==========================================================="
echo "                Installation complete."
echo "==========================================================="
echo
echo "First launch downloads the Whisper model (~1.5 GB) into the"
echo "Hugging Face cache. Subsequent launches load in seconds."
echo
echo "NOTE for Linux users: pynput global hotkeys require an X11"
echo "session. Wayland is not currently supported (a known pynput"
echo "limitation). Most distros let you log in with X11 from the"
echo "login screen's session menu."
echo
echo "NOTE for macOS users: on first launch, macOS will prompt you"
echo "to grant Accessibility + Microphone permissions to Python."
echo "Both must be granted for global hotkeys and dictation to work."
echo
