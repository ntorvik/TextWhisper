# TextWhisper

> Local, offline voice-to-text — with optional voice read-back of Claude Code. Press a hotkey, talk, your words appear in whatever app has focus. Optionally listen to Claude Code's responses through a local neural voice instead of staring at the screen.

**Cross-platform** (Windows / Linux / macOS), powered by [faster-whisper](https://github.com/SYSTRAN/faster-whisper). Transcription runs entirely on your machine — no cloud, no API calls. The optional voice read-back uses [Piper](https://github.com/rhasspy/piper) (local neural TTS) plus a small [Anthropic Haiku](https://www.anthropic.com/) summarisation pass to deliver a peer-toned, hands-free pairing loop with Claude Code.

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
- **Audio device routing** — pick a specific microphone for input *and* a separate output device for chimes + read-back. The Devices tab automatically deduplicates devices that PortAudio lists once per host API and prefers WASAPI on Windows for lowest latency.

### Editing
- **Delete-word hotkey** (default `Delete`, configurable):
  - **Single tap** → Ctrl+Backspace (delete previous word).
  - **Double tap** → erase the entire last transcription.
  - Maintains a **stack of typed segments** — keep double-tapping to walk backwards through your dictation history.
- **Auto-Enter** — after a transcription is typed, optionally press Enter for you N ms later. Pressing any key during the window silently cancels the pending Enter. Useful for fully hands-free Claude Code / chat workflows.
- **Continuation detection** ("treat short pauses as commas") — Whisper transcribes each VAD-cut segment in isolation and ends every segment with a period, even when you were just taking a breath. With this on, if you resume speaking within the continuation window, that trailing period is rewritten as `,` and the next segment's first letter lowercased. Result: one flowing sentence instead of choppy stand-alone ones.
- **Hotkey recorder** — press *Record...* in Settings and press the chord you want. Supports any key including `+`, `<f9>`, etc. Live conflict warning if you pick a chord that collides with the dictation hotkey or has no modifier.

### Voice read-back of Claude Code
The hands-free other half of TextWhisper. A small [Stop hook](tools/claude-code-stop-hook.py) registered with Claude Code POSTs each finished assistant turn to TextWhisper, which summarises it via Anthropic Haiku and speaks the summary aloud through Piper (a local neural TTS) — letting you keep your eyes off the screen while pairing.
- **Peer-toned summaries** — the prompt frames the model as a teammate, pushes for 1-2 sentence gists with contractions, and explicitly strips Claude Code's own self-asked "Want me to also...?" tail.
- **Smart follow-up gate** — TextWhisper only adds a varied "want me to walk through it?" line when the raw response was actually substantial (`> 800 chars`, contains a fenced code block, or `>= 3` paragraphs — all tunable in `config.json`). No grating "let me know if you want more" on trivial responses.
- **Voice interrupt hotkey** (default `Ctrl+Alt+S`) cuts speech mid-stream and flushes any in-flight audio.
- **Mic auto-mutes** while TTS is speaking, so the AI's voice never gets re-transcribed back into your dictation if you press the dictation hotkey too early.
- **Stays on Haiku** for cost — the persona is encoded in the prompt, not the model. ~5x cheaper than Sonnet for the same loop.
- Optional summarisation can be turned off entirely (`voice_summarize: false` in config) to read raw assistant text verbatim.

### Paste-target lock
For long sessions where you want every dictation to land in one specific window even if focus drifts:
- **Smart toggle hotkey** (default `Alt+L`): lock current window / re-lock to current / unlock if already locked here.
- **Colored border** around the locked window (configurable color + thickness, can be disabled).
- **Optional lock/unlock chimes**.

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
- **Crash logger** at `%APPDATA%\TextWhisper\logs\textwhisper.log` (Windows) or `~/.config/TextWhisper/logs/textwhisper.log` (Linux/macOS), rotated at 1 MB × 3 — every transcription, every hotkey event, every keyboard injection result, plus full tracebacks on errors. The Anthropic API key is **never** logged.

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

### Voice read-back setup (optional)

To listen to Claude Code responses through TTS instead of reading them:

1. Open Settings → **Voice Read-Back**, tick *Enable read-back*, paste an Anthropic API key (or set `ANTHROPIC_API_KEY` in your env), and click **Test voice**. The first test triggers a one-time Piper + voice-model download (~110 MB).
2. Register the bundled Stop hook with Claude Code by adding this to `~/.claude/settings.json`:
   ```json
   {
     "hooks": {
       "Stop": [
         {
           "matcher": "*",
           "hooks": [
             {"type": "command",
              "command": "py \"PATH/TO/TextWhisper/tools/claude-code-stop-hook.py\""}
           ]
         }
       ]
     }
   }
   ```
   Linux/macOS: replace `py` with `python3` and use a forward-slash path.
3. Use Claude Code as normal. Each finished response is summarised by Haiku and spoken by Piper. Press **Ctrl+Alt+S** to interrupt mid-sentence; ask a follow-up by just dictating "tell me more about that" — it goes to Claude Code as the next turn and the response gets read back the same way.

Tuning: if the follow-up "want me to walk through it?" line fires too often, bump `voice_followup_min_chars` in `%APPDATA%\TextWhisper\config.json` (default 800).

---

## Settings

Right-click tray → **Settings...** Tabs: *Hotkeys*, *Devices*, *Dictation*, *Paste Lock*, *Voice Read-Back*, *Feedback*, *Oscilloscope*, *About*.

### Hotkeys
| | |
|--|--|
| Dictation hotkey | Press *Record...* and press the chord. Or type pynput syntax manually (`<alt>+z`, `<ctrl>+<shift>+v`, `<ctrl>+<plus>`, `<f9>`). Live conflict validation. |
| Delete-word hotkey | Same UI. Single-tap = delete word, double-tap = erase last transcription. |
| Double-tap window | Window for upgrading single-tap to double-tap. Default 350 ms. |

### Devices
| | |
|--|--|
| Microphone input | System default or any specific input device. List is automatically deduplicated and prefers WASAPI on Windows. |
| Audio output | Where chimes and Piper TTS read-back are routed. Useful when you have a Bluetooth headset for AI voice + a separate speaker for system audio. |

### Dictation
Speech-to-text pipeline (Whisper engine + VAD + text output method).

| | |
|--|--|
| Whisper model | `tiny`, `base`, `small`, `medium`, `large-v3`, `large-v3-turbo` |
| Compute device | `cuda`, `cpu`, `auto` |
| Compute type | `float16` (recommended on GPU), `int8_float16`, `int8`, `float32` |
| Language | `auto` (Whisper detects), or ISO code (`en`, `es`, `fr`, `ja`, …) |
| Silence pause | How long a pause must be before flushing an utterance for transcription |
| Voice threshold | RMS threshold above which audio is considered speech (0.005–0.05 typical) |
| Continuation | "Treat short pauses as commas" toggle + window |
| Output method | *Type* — per-character keystrokes (works in most apps). *Paste* — clipboard + Ctrl+V (more reliable in terminals like Claude Code, Windows Terminal, IDE consoles). |
| Per-character type delay | 0–50 ms. Bump it up if a particular app drops keystrokes in Type mode. |
| Auto-Enter | Toggle + delay (200–30000 ms). Press any key during the delay to silently cancel. |

### Paste Lock
| | |
|--|--|
| Enable paste-target lock | Master toggle. |
| Lock toggle hotkey | Default `Alt+L`. Smart toggle: lock current / re-lock / unlock. |
| Border around locked window | On/off, color (palette + custom picker), thickness 1–10 px. |
| Play tone on lock/unlock | Optional chime. |

### Voice Read-Back
Read Claude Code's responses aloud via Piper neural TTS, summarised by Anthropic Haiku.

| | |
|--|--|
| Voice read-back | Master toggle. |
| Engine | `piper` (local neural). |
| Voice model | Piper voice ID (e.g. `en_US-amy-medium`). Downloaded on first use into `%APPDATA%\TextWhisper\piper\voices\`. Editable — type any model from `rhasspy/piper-voices` on Hugging Face. |
| Rate | 0.5–2.0× speech rate multiplier. |
| Volume | 0–100%. |
| Summarise | When on, each response is rewritten by Haiku into a peer-toned 1-2 sentence read-back. When off, raw assistant text is read verbatim (brutal — only useful for cost-sensitive setups). |
| Anthropic API key | Stored locally in `config.json`, never logged. Leave blank to fall back to `ANTHROPIC_API_KEY`. |
| Interrupt hotkey | Default `Ctrl+Alt+S` — cuts speech mid-stream and flushes audio. |
| IPC port | Localhost port the Claude Code Stop hook POSTs to. Default 47821. |
| Test voice | Plays a short sample with the current model + rate + volume. First click triggers the one-time Piper + voice-model download (~110 MB). |

Three follow-up-gate thresholds (`voice_followup_min_chars`, `voice_followup_min_paragraphs`, `voice_followup_invite_on_code`) live in `config.json` only — tweak them if invitations fire too often or never.

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

Changes apply immediately. Switching Whisper model or device reloads the model in the background. Hotkey and microphone changes apply on save.

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
    paste_target.py                  paste-target lock state machine
    win32_window_utils.py            Win32 window discovery / focus helpers
    mic_muter.py                     auto-mutes the mic while Piper TTS speaks
    voice.py                         Piper neural TTS engine + voice-model cache
    voice_server.py                  ThreadingHTTPServer accepting /speak from the Stop hook
    summarizer.py                    Anthropic Haiku call with peer-tone prompt + follow-up gate
    ui/
      tray.py                        QSystemTrayIcon + context menu
      oscilloscope.py                shaped, masked, draggable waveform/spectrum widget
      settings_dialog.py             Qt dialog
      hotkey_recorder.py             modal "press a chord" dialog
      window_border_overlay.py       colored frame around the locked paste target

  tools/
    claude-code-stop-hook.py         stdlib-only bridge: Claude Code Stop event → TextWhisper /speak

  tests/                             400+ pytest tests covering everything above
```

---

## Troubleshooting

- **`Library cublas64_12.dll is not found` (Windows)** — re-run `install.bat`, or install CUDA 12.x + cuDNN 9 system-wide.
- **No audio captured** — pick the correct mic in Settings → Devices → Microphone input. Verify OS mic permissions for Python (Windows Privacy settings, macOS System Settings → Privacy → Microphone, Linux PulseAudio/PipeWire device).
- **Hotkey doesn't fire** — most likely something else on your system is consuming the key first (PowerToys Keyboard Manager, AutoHotKey, vendor keyboard software, clipboard managers, BetterTouchTool on macOS). Pick a chord with a modifier (e.g. `<ctrl>+<backspace>`); the Settings warning will flag bare/single-key hotkeys.
- **Linux: hotkeys don't work at all** — you're probably on Wayland. Log out and back in with an X11 session.
- **macOS: hotkeys don't work** — System Settings → Privacy & Security → **Accessibility** → make sure Python (or TextWhisper.app) is enabled. Same for **Microphone**.
- **Latency too high** — drop to `medium` or `small` in Settings. `large-v3` on a 16 GB GPU is sub-second per utterance; smaller models are near-instant.
- **Spaces missing between segments in a terminal app** — switch *Output method* to *Paste*. Real `Key.space` keystrokes survive terminal paste-strip behavior.
- **Run as Administrator (Windows)** if you need to dictate into elevated apps. pynput can't inject keystrokes from a non-elevated process into an elevated window.
- **Voice read-back: nothing happens after a Claude Code response** — verify *Enable read-back* is on in the Voice tab, an Anthropic API key is set (Settings or `ANTHROPIC_API_KEY`), and the Stop hook is registered in `~/.claude/settings.json`. Check `%APPDATA%\TextWhisper\stop-hook.log` for hook errors.
- **Voice read-back: invitations like "want me to walk through it?" fire on every response** — the gate is configurable: bump `voice_followup_min_chars` in `config.json` (default 800). To suppress invitations entirely, raise it to a very large number.
- **Voice read-back going to the wrong speakers** — pick a specific output device in Settings → Devices. Routing chimes + TTS to a Bluetooth headset while keeping system audio on speakers is a common setup.

---

## License

MIT. See [LICENSE](LICENSE).

---

## Acknowledgements

Built on:
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (CTranslate2 + Whisper) — transcription
- [Piper](https://github.com/rhasspy/piper) — local neural TTS for read-back
- [Anthropic Python SDK](https://github.com/anthropics/anthropic-sdk-python) — Haiku summarisation pass
- [pynput](https://github.com/moses-palmer/pynput) for global hotkeys + keyboard injection
- [sounddevice](https://python-sounddevice.readthedocs.io/) for microphone I/O
- [PyQt6](https://www.riverbankcomputing.com/software/pyqt/) for the UI
- [PyInstaller](https://pyinstaller.org/) for cross-platform packaging
