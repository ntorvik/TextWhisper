# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for TextWhisper.

Builds a one-folder distribution at dist/TextWhisper/, containing
TextWhisper.exe plus all its DLLs and Python runtime. Use the one-folder
mode (rather than --onefile) so launch is instant — no extracting a 250 MB
archive into a temp dir on every start.

Build with:
    venv\\Scripts\\activate
    pip install pyinstaller
    pyinstaller packaging/textwhisper.spec --noconfirm

The output goes in dist/TextWhisper/. Zip that whole folder for distribution.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parent

# --- Heavy third-party packages need their data + binaries collected ---
hiddenimports: list[str] = []
datas: list[tuple] = []
binaries: list[tuple] = []

for pkg in (
    "faster_whisper", "ctranslate2", "sounddevice", "pynput", "PyQt6",
    # piper-tts ships espeak-ng-data, ONNX runtime libs, and a config module
    # that PyInstaller's heuristic misses without help.
    "piper", "onnxruntime",
    # Anthropic SDK has lazy submodule loading that PyInstaller's static
    # scan misses; collect_all pulls in everything plus pydantic + httpx.
    "anthropic",
):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# nvidia-* pip wheels (CUDA runtime) — bundle their bin/ folders so we don't
# need a system-wide CUDA install on the target machine.
for nvidia_pkg in ("nvidia.cublas", "nvidia.cudnn", "nvidia.cuda_nvrtc"):
    try:
        datas += collect_data_files(nvidia_pkg)
        hiddenimports += collect_submodules(nvidia_pkg)
    except Exception:
        # Package not installed — fine, app will run on CPU.
        pass

# Bundle the icon so the .exe shows the right one in Explorer / taskbar.
icon_path = ROOT / "textwhisper.ico"

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + [
        "src.app",
        "src.audio_capture",
        "src.cuda_setup",
        "src.hotkey_manager",
        "src.keyboard_output",
        "src.settings_manager",
        "src.single_instance",
        "src.sound_player",
        "src.summarizer",
        "src.transcription",
        "src.voice",
        "src.ui.hotkey_recorder",
        "src.ui.oscilloscope",
        "src.ui.settings_dialog",
        "src.ui.tray",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib", "scipy", "pandas", "tkinter",  # not used, save ~50 MB
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TextWhisper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,           # GUI app — no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_path) if icon_path.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="TextWhisper",
)

# macOS — wrap the build into a real .app bundle.
import sys as _sys
if _sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="TextWhisper.app",
        icon=str(icon_path) if icon_path.exists() else None,
        bundle_identifier="com.ntorvik.textwhisper",
        info_plist={
            "CFBundleName": "TextWhisper",
            "CFBundleDisplayName": "TextWhisper",
            "CFBundleShortVersionString": "1.0.0",
            "CFBundleVersion": "1.0.0",
            "NSHighResolutionCapable": True,
            "LSUIElement": True,  # tray app — no Dock icon
            "NSMicrophoneUsageDescription":
                "TextWhisper records audio from your microphone to transcribe "
                "speech locally on your machine.",
            "NSAppleEventsUsageDescription":
                "TextWhisper sends keystrokes to the focused window to type "
                "your transcribed speech.",
        },
    )
