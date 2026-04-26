#!/usr/bin/env bash
# scripts/build-app.sh -- PyInstaller build for Linux + macOS.
#
# On Linux:  produces dist/TextWhisper/TextWhisper (binary + bundled libs).
#            Wrap in AppImage for distribution if desired.
# On macOS:  produces dist/TextWhisper.app (a real bundled .app you can
#            drag to /Applications).
#
# Run from the project root after install.sh has finished.

set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -f "venv/bin/activate" ]]; then
    echo "[ERROR] venv not found. Run ./install.sh first." >&2
    exit 1
fi

# shellcheck disable=SC1091
source venv/bin/activate

if ! python -m pip show pyinstaller >/dev/null 2>&1; then
    echo "Installing PyInstaller into the venv..."
    python -m pip install pyinstaller
fi

if [[ ! -f "textwhisper.ico" ]]; then
    echo "Generating textwhisper.ico ..."
    python scripts/create-shortcut.py || true
fi

echo
echo "Building TextWhisper (this can take 5-10 minutes)..."
echo

# The same spec works on all three platforms — PyInstaller's COLLECT step
# produces the right output kind for each (folder on Win/Linux, .app on macOS).
python -m PyInstaller packaging/textwhisper.spec --noconfirm --clean

echo
echo "============================================================"
case "$(uname)" in
    Darwin)
        echo " Build complete. Output: dist/TextWhisper.app"
        echo
        echo " To install: drag dist/TextWhisper.app to /Applications."
        echo " First launch: macOS will prompt for Accessibility +"
        echo " Microphone permissions; both must be granted."
        ;;
    Linux)
        echo " Build complete. Output: dist/TextWhisper/TextWhisper"
        echo
        echo " To distribute: tar+gzip the dist/TextWhisper/ folder, or"
        echo " wrap with appimagetool to produce a portable AppImage."
        ;;
    *)
        echo " Build complete. Output: dist/TextWhisper/"
        ;;
esac
echo "============================================================"
