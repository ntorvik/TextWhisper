# TextWhisper

> Local, offline voice-to-text. Press a hotkey, talk, and your words appear in whatever app has focus.

**Cross-platform** (Windows / Linux / macOS), powered by [faster-whisper](https://github.com/SYSTRAN/faster-whisper). No cloud, no API calls — everything runs on your machine.

---

## Download & install

The fastest way to get TextWhisper is to grab the prebuilt installer for your OS from the **[Releases page](https://github.com/ntorvik/TextWhisper/releases/latest)**. No Python install, no command line, just download and run.

| Platform | Download | What you do |
|---|---|---|
| **Windows** | `TextWhisper-vX.Y.Z-windows-x64.zip` | Extract anywhere → double-click `TextWhisper.exe`. Right-click it for **Pin to taskbar**. |
| **Linux** *(coming soon)* | `TextWhisper-vX.Y.Z-linux-x64.tar.gz` | Extract → run `./TextWhisper`. (Currently: build from source — instructions below.) |
| **macOS** *(coming soon)* | `TextWhisper-vX.Y.Z-macos.app.zip` | Unzip → drag `TextWhisper.app` to `/Applications`. (Currently: build from source.) |

### First launch

The first time you run TextWhisper, it downloads the Whisper speech-recognition model (~1.5 GB for the default `large-v3`) into your Hugging Face cache. **This is a one-time download** — subsequent launches load in seconds. A welcome dialog tells you what's happening; the tray icon turns "Ready" and a soft chime plays when the app is good to go.

After that:

1. Click into any text field (browser, editor, chat, terminal).
2. Press **Alt+Z** — short chime, tray icon turns green.
3. Speak naturally. Each utterance is typed into the focused field after a brief pause.
4. Press **Alt+Z** again to stop.

**Delete-word workflow**:
- Press **Delete** once → previous word removed.
- Press **Delete** twice quickly → entire last transcription erased. Keep double-tapping to walk back through earlier segments.

Right-click the tray icon for: Start/Stop, Show/Hide oscilloscope, Settings, Exit. Double-clicking the tray icon also toggles capture.

---

## Features

### Core
- **Global dictation hotkey** (default `Alt+Z`, configurable) — start/stop from anywhere.
- **Live transcription** — VAD segments speech on natural pauses; each utterance is typed into the focused window within a second.
- **Two output modes**:
  - *Type* — per-character keystrokes (works in most apps).
  - *Paste* — clipboard + Ctrl+V, with a real `Key.space` keystroke injected after each paste. Reliable in terminals and IDEs that strip trailing whitespace from clipboard pastes.

### Editing
- **Delete-word hotkey** with single-tap (Ctrl+Backspace) and double-tap (erase entire last transcription) behavior. Maintains a stack of typed segments — keep double-tapping to walk backwards through your dictation history.
- **Hotkey recorder** — press *Record...* in Settings and press the chord you want. Live conflict warnings.

### Visualization
- **Floating oscilloscope** — a real shaped, frameless, always-on-top window with click-through transparent corners.
  - **Two visualization styles**: classic scrolling waveform, or fixed-position FFT spectrum analyzer (VU meter).
  - **Three shapes**: rounded rectangle, pill, sharp rectangle. The whole window takes the shape, not just the painted area (Win11 DWM corner override + `QRegion` mask).
  - **Drag to move and drag to resize**, configurable opacity, background alpha, color palette.
  - **Always on top** — periodic z-order re-assertion so it stays in front of the taskbar / desktop without stealing focus.

### Feedback
- **Soft chime** when capture is ready (plays *before* the mic opens, so the tone never lands in your transcription).
- **Tray icon** that turns green while listening.
- **Clipboard fallback** — every transcription is also copied to the clipboard, so a wrong-focus dictation can be `Ctrl+V`-ed where you needed it.

### Lifecycle
- **Single-instance lock** — clicking the launcher twice just brings up "TextWhisper is already running."
- **Crash logger** at `logs/textwhisper.log` (rotated, 1 MB × 3) records every transcription, hotkey event, and error trace.

### Hardware acceleration
- **NVIDIA GPU** (CUDA 12) is detected automatically and used if present. RTX 50-series (Blackwell) requires driver 570+ and CUDA 12.8+ runtime.
- Falls back to CPU when no GPU is available.

---

## Settings

Right-click tray → **Settings...**

### Hotkeys
| | |
|--|--|
| Dictation hotkey | Press *Record...* and press the chord. Or type pynput syntax manually (`<alt>+z`, `<ctrl>+<shift>+v`, `<f9>`, even `<plus>`). Live conflict validation. |
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
| Compute type | `float16` (GPU recommended), `int8_float16`, `int8`, `float32` |
| Microphone | System default or any specific input device |
| Language | `auto` (Whisper detects), or ISO code (`en`, `es`, `fr`, `ja`, …) |
| Silence pause | How long a pause must be before flushing an utterance |
| Voice threshold | RMS threshold above which audio is considered speech |

### Feedback
| | |
|--|--|
| Notifications | Toggle tray balloon toasts |
| Clipboard | Copy each transcription to clipboard |
| Ready / Stop sounds | Soft two-note chime, with volume slider |

### Oscilloscope
| | |
|--|--|
| Show / Hide / Reset position / Reset size | |
| Visualization | *Waveform (scrolling)* or *Spectrum (frequency bars)* |
| Shape | *Rounded rectangle*, *Pill*, *Sharp rectangle* |
| Width / Height / Opacity / Background alpha | |
| Active / Idle color | 10-color palette + custom picker |

Changes apply immediately. Switching Whisper model or device reloads the model in the background.

---

## Building from source

For developers, contributors, or users who want to run from source instead of a packaged binary.

### Requirements

- **Python 3.11 or 3.12** on PATH
- For GPU acceleration: NVIDIA GPU with CUDA 12.x. RTX 50-series (Blackwell) needs driver 570+ and CUDA 12.8+.

### One-click developer install

#### Windows
```bat
git clone https://github.com/ntorvik/TextWhisper.git
cd TextWhisper
install.bat
```
Then double-click `TextWhisper.lnk` (created in the project folder), or run `run.bat` from a terminal.

#### Linux
```bash
git clone https://github.com/ntorvik/TextWhisper.git
cd TextWhisper
chmod +x install.sh run.sh scripts/*.sh
./install.sh
```
Installer drops a `.desktop` entry into `~/.local/share/applications/`. From a terminal: `./run.sh`.

> ⚠ **Wayland not supported** — pynput's global hotkeys require X11. Most distros let you log in with X11 from the login screen's session menu.

#### macOS
```bash
git clone https://github.com/ntorvik/TextWhisper.git
cd TextWhisper
chmod +x install.sh run.sh scripts/*.sh
./install.sh
./run.sh
```

> ⚠ On first launch macOS will prompt to grant **Accessibility** + **Microphone** permissions to Python. Both are required.

### What `install.sh` / `install.bat` does

1. Creates a Python virtual environment at `./venv/`.
2. Installs runtime dependencies (`faster-whisper`, `PyQt6`, `sounddevice`, `pynput`, `numpy`).
3. Auto-detects an NVIDIA GPU (via `nvidia-smi`). If found, installs pip-managed CUDA 12 runtime libraries (cuBLAS + cuDNN 9). macOS skips this.
4. Creates a platform-native launcher (Windows `.lnk`, Linux `.desktop`, macOS instructions for building `.app`).

### Building a standalone installer yourself

Want to produce the same `TextWhisper.exe` / `TextWhisper.app` that goes on the Releases page?

| Platform | Command | Output |
|---|---|---|
| Windows | `scripts\build-exe.bat` | `dist/TextWhisper/TextWhisper.exe` (folder, ~250 MB) |
| Linux | `bash scripts/build-app.sh` | `dist/TextWhisper/TextWhisper` (binary + libs) |
| macOS | `bash scripts/build-app.sh` | `dist/TextWhisper.app` (real macOS bundle) |

For Linux you can wrap with [appimagetool](https://github.com/AppImage/AppImageKit) for a portable AppImage. For macOS broader distribution requires a Developer ID + notarization (out of scope here).

---

## Project layout

```
TextWhisper/
  install.bat / install.sh             one-click installer entry points
  run.bat / run.sh                     developer launch
  main.py                              Python entry point (also what PyInstaller builds against)
  requirements.txt
  pyproject.toml                       ruff + pytest config
  LICENSE                              MIT
  README.md

  scripts/
    setup.bat / setup.sh               create venv, install deps
    setup-cuda.bat / setup-cuda.sh     pip-install CUDA 12 runtime
    create-shortcut.bat / .py          Windows .lnk + .ico
    create-desktop-entry.sh            Linux .desktop entry
    build-exe.bat                      PyInstaller build (Windows)
    build-app.sh                       PyInstaller build (Linux + macOS)

  packaging/
    textwhisper.spec                   PyInstaller spec, builds for all 3 OSes

  src/
    app.py                             main TextWhisperApp wiring everything together
    cuda_setup.py                      adds pip nvidia/* DLL dirs to Windows search path
    single_instance.py                 named-mutex (Win) / PID-file (Linux/macOS) lock
    settings_manager.py                JSON config in user config dir
    audio_capture.py                   sounddevice + energy-based VAD segmenter
    transcription.py                   faster-whisper worker thread
    keyboard_output.py                 Type / Paste output modes
    hotkey_manager.py                  pynput Listener + custom hotkey parser
    sound_player.py                    pre-generated chime tones
    ui/
      tray.py                          QSystemTrayIcon + context menu
      oscilloscope.py                  shaped, masked, draggable waveform/spectrum widget
      settings_dialog.py               Qt dialog
      hotkey_recorder.py               modal "press a chord" dialog

  tests/                               210 pytest tests
```

---

## Troubleshooting

- **`Library cublas64_12.dll is not found` (Windows)** — the prebuilt installer should have this. If you're running from source, re-run `install.bat` so it picks up the CUDA libraries, or install CUDA 12.x + cuDNN 9 system-wide.
- **No audio captured** — pick the correct mic in Settings → Microphone. Verify OS mic permissions for Python (Windows Privacy settings, macOS System Settings → Privacy → Microphone, Linux PulseAudio/PipeWire device).
- **Hotkey doesn't fire** — most likely something else on your system is consuming the key first (PowerToys Keyboard Manager, AutoHotKey, vendor keyboard software, clipboard managers). Pick a chord with a modifier (e.g. `<ctrl>+<backspace>`); the Settings warning will flag bare/single-key hotkeys.
- **Linux: hotkeys don't work at all** — you're probably on Wayland. Log out and back in with an X11 session.
- **macOS: hotkeys don't work** — System Settings → Privacy & Security → **Accessibility** → make sure TextWhisper (or Python) is enabled. Same for **Microphone**.
- **Latency too high** — drop to `medium` or `small` in Settings. `large-v3` on a 16 GB GPU is sub-second per utterance; smaller models are near-instant.
- **Spaces missing between segments in a terminal app** — switch *Output method* to *Paste*. Real `Key.space` keystrokes survive terminal paste-strip behavior.
- **Run as Administrator (Windows)** if you need to dictate into elevated apps. Keystroke injection from a non-elevated process can't reach an elevated window.

---

## License

[MIT](LICENSE).

---

## Acknowledgements

Built on:
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (CTranslate2 + Whisper)
- [pynput](https://github.com/moses-palmer/pynput) for global hotkeys + keyboard injection
- [sounddevice](https://python-sounddevice.readthedocs.io/) for microphone I/O
- [PyQt6](https://www.riverbankcomputing.com/software/pyqt/) for the UI
- [PyInstaller](https://pyinstaller.org/) for cross-platform packaging
