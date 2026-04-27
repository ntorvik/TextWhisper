# Paste Target Lock — Design Spec

**Date:** 2026-04-27
**Status:** Approved (brainstorming complete; pending implementation plan)
**Author:** Brainstormed with Claude Opus 4.7
**Target version:** 1.3.0 (next minor — adds new feature surface)

---

## 1. Motivation

When the user dictates with TextWhisper, transcribed text is injected into the currently-focused window. If focus shifts away from the intended app between pressing Alt+Z and the transcription completing — a real and frequently observed pattern, especially when the user looks away from the screen — the dictation lands in the wrong place. The current `_paste_text` path also has at least one intermittent failure mode (clipboard intact, only the trailing space lands) whose root cause is unconfirmed; that bug is tracked separately and is **not** what this feature fixes, but a target-lock mechanism would limit the blast radius of any focus-related class of failures.

The user's mental model is: *"I want my dictation to land where I told it to land, even if I look away."*

This spec defines a **paste target lock** feature: a way to bind dictated output to a specific window, with a hotkey-driven sticky lock for "I'm bouncing between apps but I want everything to go to Claude Code today" workflows, and a per-session auto-capture for "whatever was foreground when I hit Alt+Z" workflows. Both modes live behind a master setting that defaults OFF; existing behavior is preserved.

## 2. Out of scope

- Fixing the intermittent paste failure (Bug 2 in commit `242eaed`). That is a separate diagnosis effort with its own next steps (instrumented `_paste_text`, foreground/clipboard logging).
- TTS read-back integration. The voice / read-back feature is currently shelved and not under UAT.
- Cross-platform support. This feature is Windows-only at this version. Stubs in `win32_window_utils.py` return safe defaults on other platforms so the test suite still imports cleanly.
- Persistence across app restart. Sticky lock is intentionally cleared on startup (decision A in brainstorming).
- Auto-elevation. If the locked target window is owned by an elevated process and TextWhisper is not, paste silently fails and we surface a one-time tray notification. We do not attempt to relaunch elevated.

## 3. Pinned design decisions (from brainstorming)

| # | Decision |
|---|---|
| 1 | Master setting *Enable paste-target lock* — default OFF (preserves current behavior) |
| 2 | Per-session auto-capture: pressing Alt+Z captures the current foreground window as the lock target for that session; released when Alt+Z stops |
| 3 | Sticky lock via Alt+L (configurable hotkey) using **smart toggle**: capture if no lock, unlock if foreground IS the locked target, re-lock to new foreground if focused on a different window |
| 4 | Sticky lock overrides per-session capture in `current_target()` resolution |
| 5 | Tray menu mirrors lock state with a status line and a Lock/Unlock action whose label reflects the smart-toggle next-action |
| 6 | Colored border drawn around the **sticky-locked window only** — never per-session |
| 7 | Settings: *Show border* toggle + configurable border color + thickness |
| 8 | Dead-target behavior: auto-restore window if minimized; notify + skip + keep clipboard if window is closed |
| 9 | Audible lock/unlock tones (different pitches), configurable on/off |
| 10 | Focus restored to wherever the user was after each paste lands in the locked target |
| 11 | No persistence: sticky lock cleared on app restart |
| 12 | Focus shift to the locked target before paste is **expected and acceptable** — the user already accepted this in option B during brainstorming. The taskbar may briefly highlight the target |

## 4. Architecture

```
                       ┌────────────────────────────┐
                       │    TextWhisperApp          │
                       │  (existing orchestrator)   │
                       └────┬───────────────┬───────┘
                            │               │
            ┌───────────────┼─────┐    ┌────┴────────────────┐
            ▼               ▼     ▼    ▼                     ▼
  ┌──────────────┐  ┌──────────────────────┐  ┌─────────────────────────┐
  │ HotkeyManager│  │ PasteTargetController│  │ WindowBorderOverlay     │
  │ (existing,   │  │  (NEW)               │  │  (NEW, OscilloscopeWidget│
  │  +new chord  │  │  - per_session_hwnd  │  │   pattern: frameless,   │
  │  "lock_      │  │  - sticky_hwnd       │  │   always-on-top, click- │
  │  toggle")    │  │  - resolve_target()  │  │   through, follows HWND │
  └──────┬───────┘  │  - on_dictation_*    │  │   on a 30 ms timer)     │
         │          │  - toggle_sticky()   │  └────────────┬────────────┘
         │          └──────────┬───────────┘               │
         │                     │                           │
         │         signals     │                           │
         │   ┌─────────────────┴────────────┐              │
         │   │ lock_changed(hwnd, source)   │              │
         │   │ target_invalid(reason)       │              │
         │   └──────────────────────────────┘              │
         │                     │                           │
         ▼                     ▼                           │
  ┌──────────────┐  ┌─────────────────────────┐            │
  │ TrayController│ │   KeyboardOutput        │            │
  │ (existing,   │  │  (existing, +one        │            │
  │  +menu       │  │   target-aware branch   │            │
  │  items)      │  │   in _paste_text)       │            │
  └──────────────┘  └────────────┬────────────┘            │
                                 │                         │
                                 ▼                         │
                    ┌────────────────────────┐             │
                    │  win32_window_utils    │◀────────────┘
                    │  (NEW thin Win32 wrap) │
                    └────────────────────────┘
```

**Files added:**
- `src/paste_target.py` — `PasteTargetController(QObject)`. Pure state + decisions. No Qt UI imports beyond `QObject`/`pyqtSignal`. No `ctypes`.
- `src/ui/window_border_overlay.py` — `WindowBorderOverlay(QWidget)`. Visual only.
- `src/win32_window_utils.py` — function-only Win32 wrapper. The **only** module that imports `ctypes.windll.user32` for window inspection/control.

**Files modified (small surface):**
- `src/keyboard_output.py` — `_paste_text` gains optional `target_hwnd` parameter
- `src/sound_player.py` — `play_lock` / `play_unlock` methods
- `src/app.py` — instantiate controller, register hotkey, wire signals
- `src/ui/tray.py` — new menu section for lock state and toggle action
- `src/ui/settings_dialog.py` — new "Paste target lock" section
- `src/settings_manager.py` — new keys with defaults

## 5. Components

### 5.1 `PasteTargetController(QObject)` — `src/paste_target.py`

**Internal state:**
- `_per_session_hwnd: int | None`
- `_sticky_hwnd: int | None`
- `_sticky_pid: int | None` — captured alongside HWND, used for HWND-reuse detection
- `_settings: SettingsManager` reference

**Public methods:**
- `on_dictation_started() -> None` — captures `get_foreground_window()` into `_per_session_hwnd` if (feature enabled AND no sticky AND foreground PID != own PID)
- `on_dictation_stopped() -> None` — clears `_per_session_hwnd`; emits `lock_changed` reflecting the new effective state
- `toggle_sticky() -> None` — smart-toggle (decision 3): no lock → capture, on-target → unlock, off-target → re-target
- `current_target() -> int | None` — sticky if set, else per-session, else None
- `is_target_alive(hwnd: int) -> tuple[bool, str]` — returns `(alive, reason)` where reason is one of `"ok"`, `"minimized"`, `"closed"`. Closed includes HWND-reuse detection (PID drift).
- `clear_sticky_silently() -> None` — used by app on `target_invalid("closed")` to drop a dead lock without re-emitting unlock tone (the dead-target notification carries the user feedback)

**Signals:**
- `lock_changed = pyqtSignal(object, str)` — payload `(hwnd_or_None, source)` where source ∈ `{"sticky", "session", "none"}`
- `target_invalid = pyqtSignal(str)` — payload `reason` ∈ `{"closed"}`. (Minimized targets do not emit this — `_paste_text` auto-restores them.)

**Settings consumed:**
- `paste_target_lock_enabled` (gates everything)

### 5.2 `WindowBorderOverlay(QWidget)` — `src/ui/window_border_overlay.py`

Mirrors the `OscilloscopeWidget` pattern. Frameless, always-on-top, click-through (`Qt.WindowTransparentForInput`), no taskbar entry, transparent background. `paintEvent` draws a single hollow rectangle in the configured color and thickness, inset 1 px so the border sits flush with the target window's edge.

**Public method:** `set_target_hwnd(hwnd: int | None) -> None`. None hides the widget. Valid HWND starts a `QTimer(30 ms)` that:
1. Calls `is_window(hwnd)` → if False, hides and stops the timer
2. Calls `is_iconic(hwnd)` → if True, hides (window is in the taskbar — nothing to outline)
3. Calls `get_window_rect(hwnd)` → repositions/resizes the widget to that rect

**Settings consumed (re-read on `settings.changed`):**
- `border_overlay_enabled` (master gate; False → always hidden regardless of `set_target_hwnd`)
- `border_color`
- `border_thickness`

### 5.3 `win32_window_utils` — `src/win32_window_utils.py`

Pure functions. All return safe defaults on `sys.platform != "win32"` (most return `None`/`0`/`False`/`""`).

```python
def get_foreground_window() -> int: ...
def is_window(hwnd: int) -> bool: ...
def is_iconic(hwnd: int) -> bool: ...
def get_window_rect(hwnd: int) -> tuple[int, int, int, int] | None: ...
def get_window_title(hwnd: int) -> str: ...
def get_window_pid(hwnd: int) -> int: ...
def get_window_process_name(hwnd: int) -> str: ...  # via psutil
def restore_window(hwnd: int) -> bool: ...
def set_foreground_with_attach(hwnd: int) -> bool: ...
```

`set_foreground_with_attach` implements the `AttachThreadInput` mitigation for Windows' anti-focus-stealing protections: attach our thread's input queue to the target window's thread, call `SetForegroundWindow`, detach. Returns True on success, False if Windows refused (e.g. UIPI block).

### 5.4 `KeyboardOutput._paste_text` modification

Signature change: `_paste_text(self, text: str, target_hwnd: int | None = None) -> int`.

When `target_hwnd is None` (default): unchanged behavior, runs the existing modifier-clear + Ctrl+V + space sequence.

When `target_hwnd` is supplied:
1. Set clipboard with `text` (existing)
2. Sleep `paste_settle_ms` (existing)
3. Check `is_window(target_hwnd)` and PID match → if either fails, return 0 (caller handles the dead-target notification)
4. Capture `prev_fg = get_foreground_window()`
5. If `is_iconic(target_hwnd)`: call `restore_window(target_hwnd)`
6. Call `set_foreground_with_attach(target_hwnd)`. Sleep `focus_settle_ms` (new setting, default 50 ms). Log warning if the call returned False but proceed anyway.
7. Run existing modifier-clear + Ctrl+V + trailing space
8. Call `set_foreground_with_attach(prev_fg)` to restore user's focus

`type_text` is also extended to accept and forward `target_hwnd`. `_type_text` (char-by-char mode) gets the same focus-shift wrapper for symmetry.

### 5.5 `SoundPlayer` extension

Two new methods:
```python
def play_lock(self) -> None: ...
def play_unlock(self) -> None: ...
```

Both gated by new setting `play_lock_sounds` (default True). Tones are generated procedurally with numpy via the existing `_make_chime` helper (matches `play_ready`/`play_stop` pattern — no .wav assets). New constants: `LOCK_FREQS = (523.0, 698.0)` (C5→F5, ascending = "lock") and `UNLOCK_FREQS = (698.0, 523.0)` (F5→C5, descending = "unlock"). Reuses the existing playback path.

### 5.6 `app.py` wiring

Adds:
- Instantiate `self.paste_target = PasteTargetController(self.settings)`
- Extend `_build_hotkey_mapping` to include `"lock_toggle": <alt>+l` when `paste_target_lock_enabled`
- New `_on_target_invalid(reason)` handler: tray notification + `controller.clear_sticky_silently()`
- New `_on_lock_changed(hwnd, source)` handler: always refreshes the tray label. If `source == "sticky"`: also `border.set_target_hwnd(hwnd)` and plays a tone with these rules — `sound.play_lock()` when the new sticky hwnd is non-None (covers both fresh-lock and re-target transitions); `sound.play_unlock()` when the new sticky hwnd is None. The handler tracks the previous sticky state internally to make this decision.
- `_toggle_capture` → call `paste_target.on_dictation_started/stopped` at the right points
- `_on_hotkey_triggered("lock_toggle")` → `paste_target.toggle_sticky()`
- `_on_transcription` → resolve `paste_target.current_target()`, pass into `keyboard_out.type_text(text, target_hwnd=target)`

### 5.7 `tray.py` additions

New section in the tray menu (between "Auto-Enter" and "Voice"):
```
─────────────────────────────────
Paste target: <none|window title (truncated)>     ← non-clickable
─────────────────────────────────
Lock paste target → current window                 ← dynamic label
─────────────────────────────────
```

Label rules (driven by smart-toggle semantics):
- No sticky lock: *"Lock paste target → current window"*
- Sticky lock set, current foreground IS the target: *"Unlock paste target (\<title\>)"*
- Sticky lock set, current foreground is something else: *"Re-lock paste target → current window"*

Hidden entirely when `paste_target_lock_enabled` is False.

Window title for the status line is fetched via `get_window_title(hwnd)`, truncated to 40 chars, cached for 1 second (re-cached on menu open).

### 5.8 `settings_dialog.py` — new section

New collapsible section *"Paste target lock"* placed after *"Hotkeys"*:

| Control | Type | Setting key | Default |
|---|---|---|---|
| Enable paste-target lock | Checkbox | `paste_target_lock_enabled` | False |
| Lock toggle hotkey | HotkeyRecorder | `lock_toggle_hotkey` | `<alt>+l` |
| Show border around locked window | Checkbox | `border_overlay_enabled` | True |
| Border color | QColorDialog button | `border_color` | `#ff9900` |
| Border thickness | QSpinBox (1–10) | `border_thickness` | 3 |
| Play tone on lock/unlock | Checkbox | `play_lock_sounds` | True |

All children disabled when the master enable is unchecked.

`HotkeyRecorder`-recorded `lock_toggle_hotkey` is validated against the existing chords (`hotkey`, `delete_hotkey`, `voice_interrupt_hotkey`) using the same `validate_hotkeys` pattern, extended to include the new key.

## 6. Data flow (summary)

- **Alt+Z press:** HotkeyManager → app.`_toggle_capture` → AudioCapture.start + PasteTargetController.on_dictation_started → captures foreground (subject to filters) → emits `lock_changed(hwnd, "session")` → tray label updates only.
- **Alt+L press:** HotkeyManager → app dispatch → controller.toggle_sticky → smart-toggle decision → emits `lock_changed(hwnd_or_None, "sticky")` → border show/hide + lock/unlock tone + tray label.
- **Transcription completes:** engine signal → app.`_on_transcription` → resolve `controller.current_target()` → `keyboard_out.type_text(text, target_hwnd=target)` → focus shift → Ctrl+V → focus restore.
- **Closed target detected during paste:** `_paste_text` returns 0 + emits `target_invalid("closed")` → app shows tray notification → controller clears sticky silently → border hides → tray label clears.

## 7. Edge cases

1. **Self-window filter:** PID-equality check rejects any HWND owned by our own process at capture time (covers settings dialog, oscilloscope, border overlay, tray).
2. **Multi-monitor + DPI:** Border overlay uses Qt's high-DPI handling and `GetWindowRect`'s native pixel coordinates; multi-monitor spans handled by Qt.
3. **Foreground change mid-paste:** Single-attempt focus-shift; if Windows refuses or user moves focus, paste lands wherever the user moved focus to (best-effort, no retry).
4. **HWND reuse:** Detected via PID comparison at `is_target_alive` time; PID drift → treat as closed.
5. **UAC-elevated target:** UIPI silently blocks `SendInput`. Detected via `set_foreground_with_attach` returning False; one-time tray notification advising elevation.
6. **Hotkey collisions:** `validate_hotkeys` extended to include `lock_toggle_hotkey` against the three existing chords; same warn/error pattern as today.
7. **Race: toggle during in-flight paste:** `_paste_text` reads `target_hwnd` once at entry; in-flight paste completes against captured target. Next paste picks up the new state.
8. **Stale self-window HWND at paste time:** `_paste_text` re-checks PID; mismatch → treat as closed.

## 8. New / modified settings keys

```python
# Added to DEFAULT_CONFIG:
"paste_target_lock_enabled": False,
"lock_toggle_hotkey": "<alt>+l",
"border_overlay_enabled": True,
"border_color": "#ff9900",
"border_thickness": 3,
"play_lock_sounds": True,
"focus_settle_ms": 50,
```

The existing `_deep_merge` merges these into any pre-existing user config without clobbering anything.

## 9. Testing strategy

### Automated (target +35 tests; suite goes from 322 → ~357)

- `tests/test_paste_target.py` (NEW, ~15 tests): controller state machine, smart toggle, target resolution, alive checks
- `tests/test_keyboard_output.py` (extend, ~5 tests): paste-with-target HWND, dead-target return, minimized auto-restore, focus-shift call sites, none-target unchanged behavior
- `tests/test_win32_window_utils.py` (NEW, ~3 tests): non-Windows safe defaults; Windows-only smoke
- `tests/test_app.py` (extend, ~7 tests): wiring of controller into capture/transcription/hotkey paths and signal subscribers
- `tests/test_sound_player.py` (extend, ~2 tests): lock/unlock sound gating
- `tests/test_tray.py` (extend, ~2 tests): dynamic label rules, section visibility under master setting
- `tests/test_settings_manager.py` (extend, ~1 test): new defaults present and merged

All Win32 calls patched via `src.win32_window_utils.X` — single mock surface for the rest of the suite.

### Manual UAT (Windows-only)

Lives at `docs/superpowers/specs/2026-04-27-paste-target-lock-uat.md`. 17 items covering:
- Lock + dictate-from-elsewhere works for: Notepad, VS Code (Electron), Claude Code in Windows Terminal
- Border draws, follows drag/resize, crosses monitor boundaries
- Smart toggle re-targets in one press
- Focus is restored after each paste
- Tones play/can-be-disabled
- Minimized target pops up + paste lands
- Closed target → notification + clipboard preserved
- Per-session vs sticky precedence
- Master setting OFF hides the entire UI surface

## 10. Open questions / explicit non-decisions

None at design time. All decisions captured in §3.

## 11. Implementation phases (rough)

This is a sketch for the implementation plan that the next step (`writing-plans`) will flesh out:

1. **Phase 1:** `win32_window_utils.py` + tests (foundational, no integration)
2. **Phase 2:** `PasteTargetController` + tests (pure state, no UI)
3. **Phase 3:** `KeyboardOutput._paste_text` target-aware branch + tests
4. **Phase 4:** `WindowBorderOverlay` widget + manual visual check
5. **Phase 5:** `SoundPlayer` extension + asset files
6. **Phase 6:** `app.py` wiring + tests
7. **Phase 7:** Tray + Settings dialog UI + tests
8. **Phase 8:** Manual UAT pass on Windows; bug fixes
9. **Phase 9:** Version bump 1.2.1 → 1.3.0; commit + push

Each phase is independently committable and testable.
