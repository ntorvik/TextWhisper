# TextWhisper

> Local, offline voice-to-text. Press a hotkey, talk, and your words appear in whatever app has focus.

**Cross-platform** (Windows / Linux / macOS), powered by [faster-whisper](https://github.com/SYSTRAN/faster-whisper). No cloud, no API calls — everything runs on your machine.

---

## Table of contents

- [Quick start](#quick-start)
- [Features](#features)
- [Daily use](#daily-use)
- [Settings](#settings)
- [Building a standalone app](#building-a-standalone-app)
- [Project layout](#project-layout)
- [Troubleshooting](#troubleshooting)
- [License](#license)

---

## Quick start

You need **Python 3.11 or 3.12** on your PATH. Beyond that, one command installs everything.

### Windows

```bat
git clone https://github.com/ntorvik/TextWhisper.git
cd TextWhisper
install.bat
```

Then double-click `TextWhisper.lnk`. Right-click it → **Pin to taskbar** for one-click launches afterwards.

### Linux

```bash
git clone https://github.com/ntorvik/TextWhisper.git
cd TextWhisper
chmod +x install.sh run.sh scripts/*.sh
./install.sh
```

The installer drops a `.desktop` entry into `~/.local/share/applications/`. TextWhisper will appear in your application menu. To launch from a terminal: `./run.sh`.

> ⚠ **Wayland not supported.** pynput's global hotkeys require X11. Most distros let you log in with X11 from the login screen's session menu (gear icon).

### macOS

```bash
git clone https://github.com/ntorvik/TextWhisper.git
cd TextWhisper
chmod +x install.sh run.sh scripts/*.sh
./install.sh
./run.sh
```

For a real `.app` bundle you can drag to `/Applications`:

```bash
bash scripts/build-app.sh
# produces dist/TextWhisper.app
```

> ⚠ On first launch macOS will prompt to grant **Accessibility** + **Microphone** permissions to Python (or to TextWhisper.app if you built one). Both are required.

### What `install.sh` / `install.bat` does

1. Creates a Python virtual environment at `./venv/`.
2. Installs runtime dependencies (`faster-whisper`, `PyQt6`, `sounddevice`, `pynput`, `numpy`).
3. **Auto-detects an NVIDIA GPU** (via `nvidia-smi`). If found, installs pip-managed CUDA 12 runtime libraries (cuBLAS + cuDNN 9) so faster-whisper has GPU acceleration without a system-wide CUDA install. macOS skips this.
4. Creates a platform-native launcher:
   - Windows → `TextWhisper.lnk` (silent, pinnable)
   - Linux → `~/.local/share/applications/textwhisper.desktop`
   - macOS → tells you how to build a `.app` bundle

The first launch downloads the Whisper model (~1.5 GB for `large-v3`) into the Hugging Face cache. Subsequent launches load in seconds.

---

## Features

### Core
- **Global dictation hotkey** (default `Alt+Z`, configurable) — start/stop from anywhere.
- **Live transcription** — VAD segments speech on natural pauses; each utterance is typed into the focused window within a second.
- **Two output modes**:
  - *Type* — per-character keystrokes (works in most apps).
  - *Paste* — clipboard + Ctrl+V, with a real `Key.space` keystroke injected after each paste. Reliable in terminals and IDEs that strip trailing whitespace from clipboard pastes.

### Editing
- **Delete-word hotkey** (default `Delete`, configurable):
  - **Single tap** → Ctrl+Backspace (delete previous word).
  - **Double tap** → erase the entire last transcription.
  - Maintains a **stack of typed segments** — keep double-tapping to walk backwards through your dictation history.
- **Hotkey recorder** — press *Record...* in Settings and press the chord you want. Supports any key including `+`, `<f9>`, etc. Live conflict warning if you pick a chord that collides with the dictation hotkey or has no modifier.

### Visualization
- **Floating oscilloscope** — a real shaped, frameless, always-on-top window. Click-through transparent corners (Windows 11 DWM corner override + `QRegion` mask).
  - **Two visualization styles**: classic scrolling waveform, or a fixed-position FFT spectrum analyzer (VU meter).
  - **Three shapes**: rounded rectangle, pill, sharp rectangle.
  - **Drag to move, drag-to-resize** (right edge / bottom edge / corner).
  - **Configurable**: opacity, background alpha, active/idle colors (10-color palette + custom picker).

### Feedback
- **Soft chime** when capture is ready (plays *before* the mic opens, so the tone never lands in your transcription).
- **Tray icon** with start/stop, settings, oscilloscope toggle, exit. Color changes when listening.
- **Clipboard fallback** — every transcription is also copied to the clipboard, so a wrong-focus dictation can be `Ctrl+V`-ed where you needed it.

### Lifecycle
- **Single-instance lock** — clicking the launcher twice just brings up "TextWhisper is already running" instead of stacking processes.
- **Crash logger** at `logs/textwhisper.log` (rotated, 1 MB × 3) — every transcription, every hotkey event, every keyboard injection result, plus full tracebacks on errors.

### Persistence
Config lives at:
- Windows → `%APPDATA%\TextWhisper\config.json`
- Linux → `~/.config/TextWhisper/config.json`
- macOS → `~/.config/TextWhisper/config.json`

---

## Daily use

1. Wait for the soft "ready" chime / `Ready` tray tooltip — the model is loaded.
2. Click into any text field (browser, editor, chat, terminal).
3. Press **Alt+Z** — short chime, then the tray icon turns green and the oscilloscope animates.
4. Speak naturally. Each utterance is typed into the focused field after a brief pause.
5. Press **Alt+Z** again to stop.

**Delete-word workflow**:
- Press **Delete** once → previous word is removed (Ctrl+Backspace).
- Press **Delete** twice quickly → the entire last transcription is erased. Keep double-tapping to walk back through earlier segments.

Right-click the tray icon for: Start/Stop, Show/Hide oscilloscope, Settings, Exit. Double-clicking the tray icon also toggles capture.

---

## Settings

Right-click tray → **Settings...**

### Hotkeys
| | |
|--|--|
| Dictation hotkey | Press *Record...* and press the chord. Or type pynput syntax manually (`<alt>+z`, `<ctrl>+<shift>+v`, `<ctrl>+<plus>`, `<f9>`). Live conflict validation. |
| Delete-word hotkey | Same UI. Single-tap = delete word, double-tap = erase last transcription. |
| Double-tap window | Window for upgrading single-tap to double-tap. Default 350 ms. |

### Output
| | |
|--|--|
| Output method | *Type* — per-character keystrokes (works in most apps). *Paste* — clipboard + Ctrl+V (more reliable in terminals like Claude Code, Windows Terminal, IDE consoles). |
| Per-character type delay | 0–50 ms. Bump it up if a particular app drops keystrokes in Type mode. |

### Whisper engine
| | |
|--|--|
| Model | `tiny`, `base`, `small`, `medium`, `large-v3` |
| Device | `cuda`, `cpu`, `auto` |
| Compute type | `float16` (recommended on GPU), `int8_float16`, `int8`, `float32` |
| Microphone | System default or any specific input device |
| Language | `auto` (Whisper detects), or ISO code (`en`, `es`, `fr`, `ja`, …) |
| Silence pause | How long a pause must be before flushing an utterance for transcription |
| Voice threshold | RMS threshold above which audio is considered speech (0.005–0.05 typical) |

### Feedback
| | |
|--|--|
| Notifications | Toggle tray balloon toasts |
| Clipboard | Copy each transcription to clipboard (handy when focus is wrong) |
| Ready / Stop sounds | Soft two-note chime, with volume slider |

### Oscilloscope
| | |
|--|--|
| Show / Hide / Reset position / Reset size | |
| Visualization | *Waveform (scrolling)* or *Spectrum (frequency bars)* |
| Shape | *Rounded rectangle*, *Pill*, *Sharp rectangle* — the **whole window** takes the shape, not just the painted area |
| Width / Height / Opacity / Background alpha | All live-applied on save |
| Active / Idle color | 10-color palette + custom color picker |

Changes apply immediately. Switching Whisper model or device reloads the model in the background.

---

## Building a standalone app

Want a self-contained `.exe` / `.app` you can hand to someone who doesn't have Python?

### Windows

```bat
scripts\build-exe.bat
```

Output: `dist/TextWhisper/TextWhisper.exe` (~250 MB folder including all DLLs and runtime libs). Zip it and attach to a GitHub Release. Users download → unzip → double-click `TextWhisper.exe`. No Python install required.

### Linux

```bash
bash scripts/build-app.sh
```

Output: `dist/TextWhisper/TextWhisper` (binary + shared libs). For broader distribution, wrap with [appimagetool](https://github.com/AppImage/AppImageKit) to produce a single-file AppImage.

### macOS

```bash
bash scripts/build-app.sh
```

Output: `dist/TextWhisper.app` — a real macOS bundle. Drag to `/Applications`. The bundle declares the right `Info.plist` keys for microphone + Accessibility prompts.

For App Store-style distribution you'd also need a Developer ID and notarization — not in scope here.

---

## Project layout

```
TextWhisper/
  install.bat / install.sh           one-click installer entry points
  run.bat / run.sh                   developer launch (devs only — most users use the .lnk / .app / .desktop)

  main.py                            Python entry point (also what PyInstaller builds against)
  requirements.txt
  pyproject.toml                     ruff + pytest config
  LICENSE                            MIT
  README.md

  scripts/
    setup.bat / setup.sh             create venv, install deps
    setup-cuda.bat / setup-cuda.sh   pip-install CUDA 12 runtime
    create-shortcut.bat              Windows .lnk + .ico
    create-shortcut.py
    create-desktop-entry.sh          Linux .desktop entry
    build-exe.bat                    PyInstaller build (Windows)
    build-app.sh                     PyInstaller build (Linux + macOS)

  packaging/
    textwhisper.spec                 PyInstaller spec, builds for all 3 OSes

  src/
    app.py                           main TextWhisperApp wiring everything together
    cuda_setup.py                    adds pip nvidia/* DLL dirs to Windows search path
    single_instance.py               named-mutex (Win) / PID-file (Linux/macOS) lock
    settings_manager.py              JSON config in user config dir
    audio_capture.py                 sounddevice + energy-based VAD segmenter
    transcription.py                 faster-whisper worker thread
    keyboard_output.py               Type / Paste output modes
    hotkey_manager.py                pynput Listener + custom hotkey parser
    sound_player.py                  pre-generated chime tones
    ui/
      tray.py                        QSystemTrayIcon + context menu
      oscilloscope.py                shaped, masked, draggable waveform/spectrum widget
      settings_dialog.py             Qt dialog
      hotkey_recorder.py             modal "press a chord" dialog

  tests/                             207 pytest tests covering everything above
```

---

## Troubleshooting

- **`Library cublas64_12.dll is not found` (Windows)** — re-run `install.bat`, or install CUDA 12.x + cuDNN 9 system-wide.
- **No audio captured** — pick the correct mic in Settings → Microphone. Verify OS mic permissions for Python (Windows Privacy settings, macOS System Settings → Privacy → Microphone, Linux PulseAudio/PipeWire device).
- **Hotkey doesn't fire** — most likely something else on your system is consuming the key first (PowerToys Keyboard Manager, AutoHotKey, vendor keyboard software, clipboard managers, BetterTouchTool on macOS). Pick a chord with a modifier (e.g. `<ctrl>+<backspace>`); the Settings warning will flag bare/single-key hotkeys.
- **Linux: hotkeys don't work at all** — you're probably on Wayland. Log out and back in with an X11 session.
- **macOS: hotkeys don't work** — System Settings → Privacy & Security → **Accessibility** → make sure Python (or TextWhisper.app) is enabled. Same for **Microphone**.
- **Latency too high** — drop to `medium` or `small` in Settings. `large-v3` on a 16 GB GPU is sub-second per utterance; smaller models are near-instant.
- **Spaces missing between segments in a terminal app** — switch *Output method* to *Paste*. Real `Key.space` keystrokes survive terminal paste-strip behavior.
- **Run as Administrator (Windows)** if you need to dictate into elevated apps. pynput can't inject keystrokes from a non-elevated process into an elevated window.

---

## License

MIT. See [LICENSE](LICENSE).

---

## Acknowledgements

Built on:
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (CTranslate2 + Whisper)
- [pynput](https://github.com/moses-palmer/pynput) for global hotkeys + keyboard injection
- [sounddevice](https://python-sounddevice.readthedocs.io/) for microphone I/O
- [PyQt6](https://www.riverbankcomputing.com/software/pyqt/) for the UI
- [PyInstaller](https://pyinstaller.org/) for cross-platform packaging
