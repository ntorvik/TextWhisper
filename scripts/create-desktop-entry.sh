#!/usr/bin/env bash
# scripts/create-desktop-entry.sh -- write a freedesktop.org .desktop file
# pointing at this clone's run.sh, so TextWhisper appears in the user's
# application menu (GNOME, KDE, XFCE, etc.).

set -euo pipefail
cd "$(dirname "$0")/.."

if [[ "$(uname)" != "Linux" ]]; then
    echo "[INFO] .desktop entries are Linux-only — skipping."
    exit 0
fi

ROOT="$(pwd)"
APPS_DIR="$HOME/.local/share/applications"
mkdir -p "$APPS_DIR"
DESKTOP_FILE="$APPS_DIR/textwhisper.desktop"

# Generate the icon if it isn't already there (re-uses the in-app tray icon).
if [[ ! -f "textwhisper.ico" ]] && [[ -f "venv/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source venv/bin/activate
    python scripts/create-shortcut.py || true
fi

ICON_LINE=""
if [[ -f "$ROOT/textwhisper.ico" ]]; then
    ICON_LINE="Icon=$ROOT/textwhisper.ico"
fi

cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=TextWhisper
GenericName=Voice typing
Comment=Local offline voice-to-text
Exec=$ROOT/run.sh
Path=$ROOT
Terminal=false
Categories=Utility;AudioVideo;
$ICON_LINE
EOF

chmod +x "$DESKTOP_FILE"

# Refresh the desktop database so the entry appears immediately in some DEs.
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPS_DIR" || true
fi

echo "[OK] Wrote $DESKTOP_FILE"
