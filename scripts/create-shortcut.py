"""Generate TextWhisper.lnk in the project root + an icon file.

After running this, the ``TextWhisper.lnk`` shortcut can be:
  - double-clicked to start (no console window)
  - right-clicked → "Pin to taskbar" / "Pin to start"
  - dragged to the desktop

The shortcut points at ``venv\\Scripts\\pythonw.exe main.py`` so it never
opens a cmd window. The icon is rendered from the in-app tray icon code.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# This script lives in scripts/, so the project root is one level up.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # so ``from src.ui.tray import ...`` works

VENV_PYTHONW = ROOT / "venv" / "Scripts" / "pythonw.exe"
MAIN_PY = ROOT / "main.py"
LNK_PATH = ROOT / "TextWhisper.lnk"
ICO_PATH = ROOT / "textwhisper.ico"

# Holds a strong reference to the QApplication so its Python wrapper is not
# garbage-collected before we finish using QPixmap / QIcon below. (PyQt6 objects
# can be GC'd if nothing holds them, which produces "Must construct a
# QGuiApplication before a QPixmap" even though Qt's C++ singleton still exists.)
_QAPP = None


def _generate_icon() -> Path | None:
    """Render the in-app tray icon to a multi-resolution .ico file."""
    global _QAPP
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PyQt6.QtCore import QSize
        from PyQt6.QtGui import QImage
        from PyQt6.QtWidgets import QApplication

        from src.ui.tray import _build_icon
    except ImportError as e:
        print(f"[icon] PyQt6 import failed ({e}); skipping icon generation.")
        return None

    # Keep the strong reference around for the lifetime of this function.
    _QAPP = QApplication.instance() or QApplication(sys.argv)
    icon = _build_icon(active=False)
    sizes = [16, 32, 48, 64, 128, 256]
    images: list[QImage] = []
    for s in sizes:
        pm = icon.pixmap(QSize(s, s))
        if pm.isNull():
            continue
        images.append(pm.toImage().convertToFormat(QImage.Format.Format_ARGB32))

    if not images:
        return None

    ico_bytes = _images_to_ico(images)
    ICO_PATH.write_bytes(ico_bytes)
    print(f"[icon] Wrote {ICO_PATH} ({len(ico_bytes)} bytes, {len(images)} frames)")
    return ICO_PATH


def _images_to_ico(images: list) -> bytes:
    """Pack QImages into the Windows ICO container format (PNG-encoded frames)."""
    from PyQt6.QtCore import QBuffer, QByteArray, QIODevice

    encoded: list[tuple[int, int, bytes]] = []
    for img in images:
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buf, "PNG")
        buf.close()
        encoded.append((img.width(), img.height(), bytes(ba)))

    import struct

    out = bytearray()
    out += struct.pack("<HHH", 0, 1, len(encoded))  # ICONDIR
    offset = 6 + 16 * len(encoded)
    for w, h, data in encoded:
        bw = 0 if w >= 256 else w
        bh = 0 if h >= 256 else h
        out += struct.pack(
            "<BBBBHHII",
            bw,
            bh,
            0,
            0,
            1,
            32,
            len(data),
            offset,
        )
        offset += len(data)
    for _, _, data in encoded:
        out += data
    return bytes(out)


def create_shortcut(icon: Path | None) -> None:
    if not VENV_PYTHONW.exists():
        print(f"[shortcut] {VENV_PYTHONW} not found. Run setup.bat first.")
        sys.exit(1)
    try:
        from win32com.client import Dispatch  # type: ignore[import-not-found]
    except ImportError:
        print("[shortcut] pywin32 not installed; cannot create shortcut.")
        sys.exit(1)

    shell = Dispatch("WScript.Shell")
    sc = shell.CreateShortCut(str(LNK_PATH))
    sc.TargetPath = str(VENV_PYTHONW)
    sc.Arguments = f'"{MAIN_PY}"'
    sc.WorkingDirectory = str(ROOT)
    sc.WindowStyle = 7  # minimized; pythonw has no window anyway
    sc.Description = "TextWhisper - voice typing"
    if icon is not None and icon.exists():
        sc.IconLocation = f"{icon},0"
    sc.save()
    print(f"[shortcut] Wrote {LNK_PATH}")


if __name__ == "__main__":
    icon = _generate_icon()
    create_shortcut(icon)
    print()
    print("Done. To pin to taskbar:")
    print(f"  1. Right-click {LNK_PATH.name} -> Show more options -> Pin to taskbar")
    print("     (On Windows 11, you may need to drag the .lnk to the desktop first,")
    print("      then right-click the desktop copy and Pin to taskbar.)")
    print("  2. Or just double-click TextWhisper.lnk to launch silently.")
