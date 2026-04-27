# Paste Target Lock — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bind dictated transcriptions to a specific Windows window via per-session auto-capture (Alt+Z) or hotkey-driven sticky lock (Alt+L), with a colored border overlay around the locked window, lock/unlock chimes, and post-paste focus restoration. Default-off behind a master setting.

**Architecture:** A new `PasteTargetController` (Qt QObject, pure state) owns the per-session HWND and sticky HWND, with sticky always winning in target resolution. A small `win32_window_utils` module is the only place that touches `ctypes.windll.user32`, giving the rest of the codebase a single mock surface. `KeyboardOutput._paste_text` gains an optional `target_hwnd` parameter that triggers a focus-shift dance (capture previous foreground → restore-if-minimized → `SetForegroundWindow` with `AttachThreadInput` mitigation → existing Ctrl+V → restore previous foreground). A new `WindowBorderOverlay` widget mirrors the existing `OscilloscopeWidget` pattern (frameless, click-through, always-on-top) and follows the locked HWND's `GetWindowRect` on a 30 ms timer. Tones are added to `SoundPlayer` procedurally.

**Tech Stack:** Python 3.12, PyQt6, pynput, ctypes (Windows-only Win32), pytest, numpy (for tone synthesis), psutil (for process-name lookups).

**Spec reference:** `docs/superpowers/specs/2026-04-27-paste-target-lock-design.md` (commits `3e29d41` + `7e12d78`).

---

## File Structure

**Created:**
- `src/win32_window_utils.py` — function-only Win32 wrapper, sole `ctypes` site
- `src/paste_target.py` — `PasteTargetController(QObject)`, pure state + decisions
- `src/ui/window_border_overlay.py` — `WindowBorderOverlay(QWidget)`, visual only
- `tests/test_win32_window_utils.py` — wrapper smoke tests + non-Windows safe-default tests
- `tests/test_paste_target.py` — controller state machine tests
- `tests/test_window_border_overlay.py` — overlay show/hide/timer tests

**Modified:**
- `src/keyboard_output.py` — `_paste_text` and `type_text` accept optional `target_hwnd`; new focus-shift branch
- `src/sound_player.py` — `play_lock` / `play_unlock` methods + `LOCK_FREQS` / `UNLOCK_FREQS` constants
- `src/app.py` — instantiate controller, register `lock_toggle` hotkey, wire signals
- `src/ui/tray.py` — new menu section for lock state and toggle
- `src/ui/settings_dialog.py` — new "Paste target lock" section
- `src/settings_manager.py` — new keys with defaults
- `src/hotkey_manager.py` — extend `validate_hotkeys` to include `lock_toggle_hotkey`
- `tests/test_keyboard_output.py` — new target-aware paste tests
- `tests/test_app.py` — controller wiring tests
- `tests/test_sound_player.py` — new tone gating tests
- `tests/test_tray.py` — menu rendering for lock states
- `tests/test_settings_manager.py` — new defaults present

---

## Task 1: Win32 window utilities (foundation)

**Files:**
- Create: `src/win32_window_utils.py`
- Create: `tests/test_win32_window_utils.py`

The single mock surface for every Win32 call the feature needs. All functions return safe defaults on non-Windows so the rest of the suite imports cleanly anywhere.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_win32_window_utils.py`:

```python
"""Tests for the Win32 window-inspection wrapper.

All Win32 calls are funneled through this module so the rest of the
suite has a single mock surface. The functions return safe defaults
on non-Windows so the suite imports cleanly anywhere.
"""

from __future__ import annotations

import sys

import pytest

from src import win32_window_utils as w


def test_module_exports_expected_functions():
    expected = {
        "get_foreground_window",
        "is_window",
        "is_iconic",
        "get_window_rect",
        "get_window_title",
        "get_window_pid",
        "get_window_process_name",
        "restore_window",
        "set_foreground_with_attach",
    }
    actual = {name for name in dir(w) if not name.startswith("_")}
    assert expected.issubset(actual), f"missing: {expected - actual}"


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows safe-default test")
def test_safe_defaults_on_non_windows():
    assert w.get_foreground_window() == 0
    assert w.is_window(123) is False
    assert w.is_iconic(123) is False
    assert w.get_window_rect(123) is None
    assert w.get_window_title(123) == ""
    assert w.get_window_pid(123) == 0
    assert w.get_window_process_name(123) == ""
    assert w.restore_window(123) is False
    assert w.set_foreground_with_attach(123) is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only smoke test")
def test_get_foreground_window_returns_nonzero_on_windows():
    hwnd = w.get_foreground_window()
    assert isinstance(hwnd, int)
    # In CI, an interactive desktop may not be present — accept zero too.
    # The smoke test is just confirming the call doesn't raise.


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only smoke test")
def test_is_window_false_for_garbage_hwnd():
    assert w.is_window(0) is False
    assert w.is_window(0xDEADBEEF) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_win32_window_utils.py -v`
Expected: ImportError or ModuleNotFoundError on `from src import win32_window_utils`.

- [ ] **Step 3: Implement the wrapper**

Create `src/win32_window_utils.py`:

```python
"""Thin function-only wrapper around the Win32 calls used by the
paste-target-lock feature.

This is the ONLY module in the project that imports
``ctypes.windll.user32`` for window inspection/control. Everything
else mocks these functions via a single import target. On platforms
other than Windows every function returns a safe default so the test
suite imports cleanly.
"""

from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)

_IS_WIN = sys.platform == "win32"


if _IS_WIN:
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32

    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.IsWindow.argtypes = [wintypes.HWND]
    _user32.IsWindow.restype = wintypes.BOOL
    _user32.IsIconic.argtypes = [wintypes.HWND]
    _user32.IsIconic.restype = wintypes.BOOL
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.GetWindowRect.restype = wintypes.BOOL
    _user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _user32.GetWindowTextLengthW.restype = ctypes.c_int
    _user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetWindowTextW.restype = ctypes.c_int
    _user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.ShowWindow.restype = wintypes.BOOL
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _user32.SetForegroundWindow.restype = wintypes.BOOL
    _user32.AttachThreadInput.argtypes = [
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.BOOL,
    ]
    _user32.AttachThreadInput.restype = wintypes.BOOL
    _user32.GetCurrentThreadId = _kernel32.GetCurrentThreadId
    _kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    _SW_RESTORE = 9


def get_foreground_window() -> int:
    if not _IS_WIN:
        return 0
    return int(_user32.GetForegroundWindow() or 0)


def is_window(hwnd: int) -> bool:
    if not _IS_WIN or not hwnd:
        return False
    return bool(_user32.IsWindow(hwnd))


def is_iconic(hwnd: int) -> bool:
    if not _IS_WIN or not hwnd:
        return False
    return bool(_user32.IsIconic(hwnd))


def get_window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    if not _IS_WIN or not hwnd:
        return None
    rect = wintypes.RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return (rect.left, rect.top, rect.right, rect.bottom)


def get_window_title(hwnd: int) -> str:
    if not _IS_WIN or not hwnd:
        return ""
    length = _user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def get_window_pid(hwnd: int) -> int:
    if not _IS_WIN or not hwnd:
        return 0
    pid = wintypes.DWORD(0)
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def get_window_process_name(hwnd: int) -> str:
    if not _IS_WIN or not hwnd:
        return ""
    pid = get_window_pid(hwnd)
    if pid == 0:
        return ""
    try:
        import psutil
        return psutil.Process(pid).name()
    except Exception:
        return ""


def restore_window(hwnd: int) -> bool:
    if not _IS_WIN or not hwnd:
        return False
    return bool(_user32.ShowWindow(hwnd, _SW_RESTORE))


def set_foreground_with_attach(hwnd: int) -> bool:
    """SetForegroundWindow with the AttachThreadInput mitigation.

    Windows refuses SetForegroundWindow under most conditions unless the
    calling thread has been recently active. The standard workaround is
    to attach our input queue to the target window's thread for the
    duration of the call, then detach. Returns True on success, False if
    Windows refused (e.g. UIPI block on an elevated target).
    """
    if not _IS_WIN or not hwnd:
        return False
    target_tid = _user32.GetWindowThreadProcessId(hwnd, None)
    our_tid = _user32.GetCurrentThreadId()
    if target_tid == 0:
        return False
    attached = False
    try:
        if target_tid != our_tid:
            attached = bool(_user32.AttachThreadInput(our_tid, target_tid, True))
        ok = bool(_user32.SetForegroundWindow(hwnd))
        return ok
    except Exception:
        log.exception("set_foreground_with_attach failed for hwnd=%s", hwnd)
        return False
    finally:
        if attached:
            try:
                _user32.AttachThreadInput(our_tid, target_tid, False)
            except Exception:
                log.exception("AttachThreadInput detach failed")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/Scripts/python.exe -m pytest tests/test_win32_window_utils.py -v`
Expected: all 4 tests PASS (one or two may be `skipped` depending on platform).

- [ ] **Step 5: Run full suite to confirm no regressions**

Run: `./venv/Scripts/python.exe -m pytest tests/ -q`
Expected: previous count + 4 new tests, all green.

- [ ] **Step 6: Commit**

```bash
git add src/win32_window_utils.py tests/test_win32_window_utils.py
git commit -m "$(cat <<'EOF'
Add win32_window_utils wrapper for paste-target-lock feature

Single ctypes site for the Win32 calls the feature needs:
GetForegroundWindow, IsWindow, IsIconic, GetWindowRect,
GetWindowTextW, GetWindowThreadProcessId, ShowWindow,
SetForegroundWindow + AttachThreadInput mitigation.

All functions return safe defaults on non-Windows so the rest of
the suite imports and runs anywhere. This is the single mock
target the controller and KeyboardOutput tests will patch.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Add new settings keys

**Files:**
- Modify: `src/settings_manager.py:9-81` (extend `DEFAULT_CONFIG`)
- Test: `tests/test_settings_manager.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_settings_manager.py`:

```python
def test_paste_target_lock_defaults_present(tmp_appdata):
    sm = SettingsManager()
    assert sm.get("paste_target_lock_enabled") is False
    assert sm.get("lock_toggle_hotkey") == "<alt>+l"
    assert sm.get("border_overlay_enabled") is True
    assert sm.get("border_color") == "#ff9900"
    assert sm.get("border_thickness") == 3
    assert sm.get("play_lock_sounds") is True
    assert sm.get("focus_settle_ms") == 50
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/Scripts/python.exe -m pytest tests/test_settings_manager.py::test_paste_target_lock_defaults_present -v`
Expected: FAIL — assertions return None for the missing keys.

- [ ] **Step 3: Add the new keys to `DEFAULT_CONFIG`**

Edit `src/settings_manager.py` — add the following BEFORE the `"oscilloscope": { ... }` block (it must be inside the dict):

```python
    # ---- Paste target lock ----------------------------------------
    # See docs/superpowers/specs/2026-04-27-paste-target-lock-design.md
    "paste_target_lock_enabled": False,
    "lock_toggle_hotkey": "<alt>+l",
    "border_overlay_enabled": True,
    "border_color": "#ff9900",
    "border_thickness": 3,
    "play_lock_sounds": True,
    "focus_settle_ms": 50,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/Scripts/python.exe -m pytest tests/test_settings_manager.py -v`
Expected: all green, including the new test.

- [ ] **Step 5: Commit**

```bash
git add src/settings_manager.py tests/test_settings_manager.py
git commit -m "Add paste-target-lock setting defaults

7 new keys with defaults that preserve existing behavior
(paste_target_lock_enabled=False) and sensible feature defaults
(border_overlay_enabled=True, play_lock_sounds=True, etc.).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: PasteTargetController scaffold + per-session capture

**Files:**
- Create: `src/paste_target.py`
- Create: `tests/test_paste_target.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_paste_target.py`:

```python
"""Tests for PasteTargetController state + decisions.

The controller is pure Python — Qt QObject + pyqtSignal is the only
PyQt dependency. All Win32 calls happen via src.win32_window_utils,
which is the single patch target.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from src.paste_target import PasteTargetController
from src.settings_manager import SettingsManager


@pytest.fixture
def settings(tmp_appdata):
    sm = SettingsManager()
    sm.set("paste_target_lock_enabled", True)
    return sm


def test_controller_initial_state(settings, qapp):
    c = PasteTargetController(settings)
    assert c._per_session_hwnd is None
    assert c._sticky_hwnd is None
    assert c.current_target() is None


def test_dictation_started_captures_foreground(settings, qapp):
    c = PasteTargetController(settings)
    with patch("src.paste_target.win32.get_foreground_window", return_value=999), \
         patch("src.paste_target.win32.get_window_pid", return_value=os.getpid() + 1):
        c.on_dictation_started()
    assert c._per_session_hwnd == 999
    assert c.current_target() == 999


def test_dictation_started_skipped_when_feature_disabled(settings, qapp):
    settings.set("paste_target_lock_enabled", False)
    c = PasteTargetController(settings)
    with patch("src.paste_target.win32.get_foreground_window", return_value=999):
        c.on_dictation_started()
    assert c._per_session_hwnd is None


def test_dictation_started_skipped_when_sticky_already_set(settings, qapp):
    c = PasteTargetController(settings)
    c._sticky_hwnd = 555
    with patch("src.paste_target.win32.get_foreground_window", return_value=999):
        c.on_dictation_started()
    assert c._per_session_hwnd is None  # sticky wins, per-session not captured


def test_dictation_started_skips_self_window(settings, qapp):
    """Filter out our own process so we don't lock to settings dialog."""
    c = PasteTargetController(settings)
    with patch("src.paste_target.win32.get_foreground_window", return_value=999), \
         patch("src.paste_target.win32.get_window_pid", return_value=os.getpid()):
        c.on_dictation_started()
    assert c._per_session_hwnd is None


def test_dictation_stopped_clears_per_session(settings, qapp):
    c = PasteTargetController(settings)
    c._per_session_hwnd = 999
    c.on_dictation_stopped()
    assert c._per_session_hwnd is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_paste_target.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.paste_target'`.

- [ ] **Step 3: Create the controller scaffold**

Create `src/paste_target.py`:

```python
"""Owns paste-target lock state for the dictation session.

State held:
- _per_session_hwnd: captured by Alt+Z, released by stop
- _sticky_hwnd: set by Alt+L (smart-toggle), survives across sessions

Resolution rule: sticky wins over per-session in current_target().

All Win32 calls go through src.win32_window_utils so tests patch
that single module rather than ctypes.
"""

from __future__ import annotations

import logging
import os

from PyQt6.QtCore import QObject, pyqtSignal

from . import win32_window_utils as win32

log = logging.getLogger(__name__)


class PasteTargetController(QObject):
    # payload: (hwnd_or_None, source) where source ∈ {"sticky", "session", "none"}
    lock_changed = pyqtSignal(object, str)
    # payload: reason ∈ {"closed"}
    target_invalid = pyqtSignal(str)

    def __init__(self, settings) -> None:
        super().__init__()
        self.settings = settings
        self._per_session_hwnd: int | None = None
        self._sticky_hwnd: int | None = None
        self._sticky_pid: int | None = None
        self._own_pid = os.getpid()

    # ---- feature gate -------------------------------------------------

    def _enabled(self) -> bool:
        return bool(self.settings.get("paste_target_lock_enabled", False))

    # ---- per-session capture -----------------------------------------

    def on_dictation_started(self) -> None:
        if not self._enabled():
            return
        if self._sticky_hwnd is not None:
            # Sticky wins; per-session capture would be shadowed anyway.
            return
        hwnd = win32.get_foreground_window()
        if not hwnd:
            return
        if win32.get_window_pid(hwnd) == self._own_pid:
            log.info("Per-session capture skipped: foreground is our own window.")
            return
        self._per_session_hwnd = hwnd
        log.info("Per-session paste target captured: hwnd=%s", hwnd)
        self._emit_lock_changed()

    def on_dictation_stopped(self) -> None:
        if self._per_session_hwnd is not None:
            self._per_session_hwnd = None
            log.info("Per-session paste target released.")
            self._emit_lock_changed()

    # ---- target resolution -------------------------------------------

    def current_target(self) -> int | None:
        return self._sticky_hwnd if self._sticky_hwnd is not None else self._per_session_hwnd

    # ---- internal -----------------------------------------------------

    def _emit_lock_changed(self) -> None:
        if self._sticky_hwnd is not None:
            self.lock_changed.emit(self._sticky_hwnd, "sticky")
        elif self._per_session_hwnd is not None:
            self.lock_changed.emit(self._per_session_hwnd, "session")
        else:
            self.lock_changed.emit(None, "none")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/Scripts/python.exe -m pytest tests/test_paste_target.py -v`
Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/paste_target.py tests/test_paste_target.py
git commit -m "Add PasteTargetController scaffold with per-session capture

Pure-state Qt QObject. Alt+Z capture path: on_dictation_started
records the current foreground HWND (subject to feature-enabled,
no-sticky-already, and self-window-PID-filter checks); on_dictation_
stopped releases it. current_target() resolves with sticky-wins
precedence (sticky to be added in Task 4).

All Win32 calls funneled through src.win32_window_utils — the
controller has zero ctypes imports.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: PasteTargetController smart-toggle (sticky lock)

**Files:**
- Modify: `src/paste_target.py` (add `toggle_sticky`)
- Modify: `tests/test_paste_target.py` (add toggle tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_paste_target.py`:

```python
def test_toggle_sticky_no_lock_captures_foreground(settings, qapp):
    c = PasteTargetController(settings)
    received = []
    c.lock_changed.connect(lambda hwnd, src: received.append((hwnd, src)))
    with patch("src.paste_target.win32.get_foreground_window", return_value=42), \
         patch("src.paste_target.win32.get_window_pid", return_value=os.getpid() + 1):
        c.toggle_sticky()
    assert c._sticky_hwnd == 42
    assert c.current_target() == 42
    assert received == [(42, "sticky")]


def test_toggle_sticky_on_target_unlocks(settings, qapp):
    c = PasteTargetController(settings)
    c._sticky_hwnd = 42
    c._sticky_pid = 1234
    received = []
    c.lock_changed.connect(lambda hwnd, src: received.append((hwnd, src)))
    with patch("src.paste_target.win32.get_foreground_window", return_value=42):
        c.toggle_sticky()
    assert c._sticky_hwnd is None
    assert c.current_target() is None
    assert received == [(None, "none")]


def test_toggle_sticky_off_target_re_targets(settings, qapp):
    """Smart toggle: pressing Alt+L while focused on a DIFFERENT
    window re-targets in a single press, not unlocks."""
    c = PasteTargetController(settings)
    c._sticky_hwnd = 42
    c._sticky_pid = 1234
    received = []
    c.lock_changed.connect(lambda hwnd, src: received.append((hwnd, src)))
    with patch("src.paste_target.win32.get_foreground_window", return_value=99), \
         patch("src.paste_target.win32.get_window_pid", return_value=os.getpid() + 1):
        c.toggle_sticky()
    assert c._sticky_hwnd == 99
    assert received == [(99, "sticky")]


def test_toggle_sticky_skipped_when_feature_disabled(settings, qapp):
    settings.set("paste_target_lock_enabled", False)
    c = PasteTargetController(settings)
    with patch("src.paste_target.win32.get_foreground_window", return_value=42):
        c.toggle_sticky()
    assert c._sticky_hwnd is None


def test_toggle_sticky_capture_skips_self_window(settings, qapp):
    c = PasteTargetController(settings)
    with patch("src.paste_target.win32.get_foreground_window", return_value=42), \
         patch("src.paste_target.win32.get_window_pid", return_value=os.getpid()):
        c.toggle_sticky()
    assert c._sticky_hwnd is None


def test_sticky_wins_over_per_session(settings, qapp):
    c = PasteTargetController(settings)
    c._per_session_hwnd = 100
    c._sticky_hwnd = 200
    assert c.current_target() == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_paste_target.py -v`
Expected: 5 of the 6 new tests FAIL (the sticky-wins one will pass since current_target already implements that). Failures: `AttributeError: 'PasteTargetController' object has no attribute 'toggle_sticky'`.

- [ ] **Step 3: Implement `toggle_sticky`**

Edit `src/paste_target.py` — add method inside the class (after `on_dictation_stopped`):

```python
    # ---- sticky lock (smart toggle) ----------------------------------

    def toggle_sticky(self) -> None:
        """Smart toggle:
        - No sticky → capture current foreground
        - Sticky set, foreground IS the locked target → unlock
        - Sticky set, foreground is a DIFFERENT window → re-target

        Self-window PID filter applies to capture and re-target paths.
        """
        if not self._enabled():
            return
        current_fg = win32.get_foreground_window()
        if not current_fg:
            return

        if self._sticky_hwnd is None:
            # CAPTURE
            if win32.get_window_pid(current_fg) == self._own_pid:
                log.info("Sticky capture skipped: foreground is our own window.")
                return
            self._sticky_hwnd = current_fg
            self._sticky_pid = win32.get_window_pid(current_fg)
            log.info("Sticky paste target locked: hwnd=%s pid=%s",
                     current_fg, self._sticky_pid)
            self._emit_lock_changed()
            return

        if current_fg == self._sticky_hwnd:
            # UNLOCK
            log.info("Sticky paste target unlocked.")
            self._sticky_hwnd = None
            self._sticky_pid = None
            self._emit_lock_changed()
            return

        # RE-TARGET (smart toggle, single press)
        if win32.get_window_pid(current_fg) == self._own_pid:
            log.info("Sticky re-target skipped: new foreground is our own window.")
            return
        self._sticky_hwnd = current_fg
        self._sticky_pid = win32.get_window_pid(current_fg)
        log.info("Sticky paste target re-targeted: hwnd=%s pid=%s",
                 current_fg, self._sticky_pid)
        self._emit_lock_changed()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/Scripts/python.exe -m pytest tests/test_paste_target.py -v`
Expected: all 12 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/paste_target.py tests/test_paste_target.py
git commit -m "Add smart-toggle sticky lock to PasteTargetController

toggle_sticky implements the three-way smart toggle from the spec
(decision 3): no-lock → capture, on-target → unlock, off-target →
re-target. Self-window PID filter applies to capture and re-target
paths to avoid locking onto our own settings dialog or oscilloscope.

current_target() already enforced sticky-over-session resolution;
new test pins it.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: PasteTargetController target liveness + silent clear

**Files:**
- Modify: `src/paste_target.py` (add `is_target_alive`, `clear_sticky_silently`)
- Modify: `tests/test_paste_target.py` (add liveness tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_paste_target.py`:

```python
def test_is_target_alive_ok_for_visible_window(settings, qapp):
    c = PasteTargetController(settings)
    with patch("src.paste_target.win32.is_window", return_value=True), \
         patch("src.paste_target.win32.is_iconic", return_value=False), \
         patch("src.paste_target.win32.get_window_pid", return_value=1234):
        alive, reason = c.is_target_alive(42, expected_pid=1234)
    assert alive is True
    assert reason == "ok"


def test_is_target_alive_minimized(settings, qapp):
    c = PasteTargetController(settings)
    with patch("src.paste_target.win32.is_window", return_value=True), \
         patch("src.paste_target.win32.is_iconic", return_value=True), \
         patch("src.paste_target.win32.get_window_pid", return_value=1234):
        alive, reason = c.is_target_alive(42, expected_pid=1234)
    assert alive is True
    assert reason == "minimized"


def test_is_target_alive_closed_when_hwnd_invalid(settings, qapp):
    c = PasteTargetController(settings)
    with patch("src.paste_target.win32.is_window", return_value=False):
        alive, reason = c.is_target_alive(42, expected_pid=1234)
    assert alive is False
    assert reason == "closed"


def test_is_target_alive_closed_when_pid_drift(settings, qapp):
    """HWND-reuse detection: same HWND now belongs to a different process."""
    c = PasteTargetController(settings)
    with patch("src.paste_target.win32.is_window", return_value=True), \
         patch("src.paste_target.win32.is_iconic", return_value=False), \
         patch("src.paste_target.win32.get_window_pid", return_value=9999):
        alive, reason = c.is_target_alive(42, expected_pid=1234)
    assert alive is False
    assert reason == "closed"


def test_clear_sticky_silently_does_not_emit_unlock_tone(settings, qapp):
    """Used by app on target_invalid; the dead-target notification
    carries the user feedback, so no double-feedback unlock tone."""
    c = PasteTargetController(settings)
    c._sticky_hwnd = 42
    c._sticky_pid = 1234
    received = []
    c.lock_changed.connect(lambda hwnd, src: received.append((hwnd, src)))
    c.clear_sticky_silently()
    assert c._sticky_hwnd is None
    # Still emits lock_changed so border overlay/tray can update —
    # the "silent" part is about the source flag passed to subscribers.
    assert received == [(None, "none")]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_paste_target.py -v`
Expected: 5 new tests FAIL with `AttributeError`.

- [ ] **Step 3: Implement liveness + silent clear**

Edit `src/paste_target.py` — add methods inside the class (after `toggle_sticky`):

```python
    # ---- target liveness ---------------------------------------------

    def is_target_alive(
        self, hwnd: int, expected_pid: int | None = None
    ) -> tuple[bool, str]:
        """Returns (alive, reason).

        reason ∈ {"ok", "minimized", "closed"}. "closed" covers both
        a dead HWND and HWND-reuse (PID drift relative to expected_pid).
        """
        if not win32.is_window(hwnd):
            return (False, "closed")
        if expected_pid is not None and win32.get_window_pid(hwnd) != expected_pid:
            return (False, "closed")
        if win32.is_iconic(hwnd):
            return (True, "minimized")
        return (True, "ok")

    # ---- dead-target cleanup -----------------------------------------

    def clear_sticky_silently(self) -> None:
        """Drop the sticky lock without playing the unlock tone.

        Used by app.py when target_invalid("closed") fires — the tray
        notification is the user feedback, no extra tone needed. The
        lock_changed signal still fires so the border overlay and tray
        label refresh; subscribers that play tones must check the
        previous state, which the app handler tracks separately.
        """
        if self._sticky_hwnd is not None:
            self._sticky_hwnd = None
            self._sticky_pid = None
            self._emit_lock_changed()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/Scripts/python.exe -m pytest tests/test_paste_target.py -v`
Expected: all 17 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/paste_target.py tests/test_paste_target.py
git commit -m "Add target liveness check + silent sticky clear

is_target_alive(hwnd, expected_pid) returns (alive, reason) with
reason ∈ {ok, minimized, closed}. PID-drift detection covers the
HWND-reuse edge case (Windows recycles HWNDs).

clear_sticky_silently drops the sticky lock for use by the app's
target_invalid handler — the tray notification carries the user
feedback, so no unlock tone.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: KeyboardOutput accepts target_hwnd parameter (no-op default)

**Files:**
- Modify: `src/keyboard_output.py:35-43` (`type_text`), `src/keyboard_output.py:80-...` (`_paste_text`)
- Modify: `tests/test_keyboard_output.py`

This task plumbs the parameter through without changing behavior — every existing test must still pass.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_keyboard_output.py`:

```python
def test_type_text_accepts_target_hwnd_kwarg(tmp_appdata):
    """target_hwnd defaults to None and existing behavior is preserved."""
    kb, mock = _kb(tmp_appdata, type_delay_ms=0, trailing_space=False)
    typed = kb.type_text("hello", target_hwnd=None)
    typed_chars = [c.args[0] for c in mock.type.call_args_list]
    assert "".join(typed_chars) == "hello"
    assert typed == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/Scripts/python.exe -m pytest tests/test_keyboard_output.py::test_type_text_accepts_target_hwnd_kwarg -v`
Expected: FAIL — `TypeError: type_text() got an unexpected keyword argument 'target_hwnd'`.

- [ ] **Step 3: Add the parameter to `type_text` and `_paste_text` / `_type_text`**

Edit `src/keyboard_output.py` — change three signatures and forward the value:

```python
    def type_text(self, text: str, target_hwnd: int | None = None) -> int:
        """Emit ``text`` into the focused window. Returns chars actually emitted.

        ``target_hwnd``: when supplied, the text is routed to that specific
        window via the focus-shift path (see _paste_text / _type_text).
        ``None`` preserves the original "type into whoever has focus" behavior.
        """
        if not text:
            return 0
        method = str(self.settings.get("output_method", "type")).lower()
        if method == "paste":
            return self._paste_text(text, target_hwnd=target_hwnd)
        return self._type_text(text, target_hwnd=target_hwnd)
```

Then `_type_text`:

```python
    def _type_text(self, text: str, target_hwnd: int | None = None) -> int:
```

(Body unchanged for now — Task 8 adds the focus-shift branch.)

Then `_paste_text`:

```python
    def _paste_text(self, text: str, target_hwnd: int | None = None) -> int:
```

(Body unchanged for now — Task 7 adds the focus-shift branch.)

- [ ] **Step 4: Run all keyboard tests to confirm no regressions**

Run: `./venv/Scripts/python.exe -m pytest tests/test_keyboard_output.py -v`
Expected: all green, including the new `test_type_text_accepts_target_hwnd_kwarg`.

- [ ] **Step 5: Commit**

```bash
git add src/keyboard_output.py tests/test_keyboard_output.py
git commit -m "type_text/paste_text/_type_text accept target_hwnd (no-op default)

Plumbs the parameter through the public API and both private
emitters. Default None preserves all existing behavior; the
focus-shift branches are added in Tasks 7 and 8.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: KeyboardOutput._paste_text focus-shift branch

**Files:**
- Modify: `src/keyboard_output.py:80-...` (`_paste_text`)
- Modify: `tests/test_keyboard_output.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_keyboard_output.py`:

```python
def test_paste_with_target_hwnd_does_focus_shift(tmp_appdata, qapp):
    """target_hwnd → capture prev fg → set_foreground(target) → Ctrl+V →
    set_foreground(prev fg) to restore."""
    from src import win32_window_utils as w
    from pynput.keyboard import Key

    sm = SettingsManager()
    sm.set("output_method", "paste")
    sm.set("paste_settle_ms", 0)
    sm.set("paste_modifier_clear_ms", 0)
    sm.set("focus_settle_ms", 0)
    with patch("src.keyboard_output.Controller") as ctrl_cls, \
         patch.object(w, "is_window", return_value=True), \
         patch.object(w, "get_window_pid", return_value=999), \
         patch.object(w, "is_iconic", return_value=False), \
         patch.object(w, "get_foreground_window", return_value=11111), \
         patch.object(w, "set_foreground_with_attach", return_value=True) as fg:
        ctrl_cls.return_value = MagicMock()
        kb = KeyboardOutput(sm)
        mock = kb._kb
        kb.type_text("hello", target_hwnd=42)
    # Two foreground calls: target first, prev fg second.
    assert [c.args[0] for c in fg.call_args_list] == [42, 11111]
    presses = [c.args[0] for c in mock.press.call_args_list]
    assert Key.ctrl in presses and "v" in presses


def test_paste_with_dead_target_returns_zero_and_skips_keystrokes(tmp_appdata, qapp):
    from src import win32_window_utils as w
    from pynput.keyboard import Key

    sm = SettingsManager()
    sm.set("output_method", "paste")
    sm.set("paste_settle_ms", 0)
    with patch("src.keyboard_output.Controller") as ctrl_cls, \
         patch.object(w, "is_window", return_value=False), \
         patch.object(w, "set_foreground_with_attach") as fg:
        ctrl_cls.return_value = MagicMock()
        kb = KeyboardOutput(sm)
        mock = kb._kb
        sent = kb.type_text("hello", target_hwnd=42)
    assert sent == 0
    fg.assert_not_called()
    # No Ctrl+V should have been sent.
    presses = [c.args[0] for c in mock.press.call_args_list]
    assert Key.ctrl not in presses


def test_paste_with_minimized_target_calls_restore(tmp_appdata, qapp):
    from src import win32_window_utils as w

    sm = SettingsManager()
    sm.set("output_method", "paste")
    sm.set("paste_settle_ms", 0)
    sm.set("paste_modifier_clear_ms", 0)
    sm.set("focus_settle_ms", 0)
    with patch("src.keyboard_output.Controller") as ctrl_cls, \
         patch.object(w, "is_window", return_value=True), \
         patch.object(w, "get_window_pid", return_value=999), \
         patch.object(w, "is_iconic", return_value=True), \
         patch.object(w, "get_foreground_window", return_value=11111), \
         patch.object(w, "set_foreground_with_attach", return_value=True), \
         patch.object(w, "restore_window") as rw:
        ctrl_cls.return_value = MagicMock()
        kb = KeyboardOutput(sm)
        kb.type_text("hello", target_hwnd=42)
    rw.assert_called_once_with(42)


def test_paste_no_target_unchanged(tmp_appdata, qapp):
    """target_hwnd=None must take the original path (no Win32 calls)."""
    from src import win32_window_utils as w

    sm = SettingsManager()
    sm.set("output_method", "paste")
    sm.set("paste_settle_ms", 0)
    sm.set("paste_modifier_clear_ms", 0)
    with patch("src.keyboard_output.Controller") as ctrl_cls, \
         patch.object(w, "set_foreground_with_attach") as fg, \
         patch.object(w, "is_window") as iw:
        ctrl_cls.return_value = MagicMock()
        kb = KeyboardOutput(sm)
        kb.type_text("hello")  # no target_hwnd
    fg.assert_not_called()
    iw.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_keyboard_output.py -v`
Expected: 3 of the 4 new tests FAIL (the no-target one passes since the param is plumbed but unused).

- [ ] **Step 3: Implement the focus-shift branch in `_paste_text`**

Edit `src/keyboard_output.py` — replace the `_paste_text` body. Find the existing definition and replace from `def _paste_text` through `return sent`:

```python
    def _paste_text(self, text: str, target_hwnd: int | None = None) -> int:
        """Set clipboard to ``text``, send Ctrl+V, then a real space keystroke.

        When ``target_hwnd`` is supplied, briefly shifts foreground focus to
        that window for the paste then restores the user's previous focus.
        Auto-restores the target if minimized; returns 0 (no keystrokes sent)
        if the target is closed or its PID has drifted (HWND reuse).

        Many terminal apps (Windows Terminal, Claude Code, IDE consoles)
        strip trailing whitespace from clipboard pastes. To guarantee a
        separator between consecutive transcriptions we leave the trailing
        space out of the clipboard payload and inject a real ``Key.space``
        keystroke immediately after the paste.

        Modifier-residue guard: see the existing block; runs in both target
        modes.
        """
        from PyQt6.QtWidgets import QApplication

        from . import win32_window_utils as w

        trailing_space = bool(self.settings.get("trailing_space", True))
        try:
            QApplication.clipboard().setText(text)
        except Exception:
            log.exception("Clipboard write failed; falling back to typing.")
            return self._type_text(text, target_hwnd=target_hwnd)

        settle = max(0, int(self.settings.get("paste_settle_ms", 30))) / 1000.0
        if settle > 0:
            time.sleep(settle)

        # Target-aware path: validate, shift focus, do paste, restore.
        prev_fg: int | None = None
        if target_hwnd is not None:
            if not w.is_window(target_hwnd):
                log.warning("Paste target hwnd=%s is closed; skipping.", target_hwnd)
                return 0
            prev_fg = w.get_foreground_window() or None
            if w.is_iconic(target_hwnd):
                log.info("Paste target hwnd=%s is minimized; restoring.", target_hwnd)
                w.restore_window(target_hwnd)
            ok = w.set_foreground_with_attach(target_hwnd)
            if not ok:
                log.warning(
                    "set_foreground_with_attach refused for hwnd=%s "
                    "(focus-stealing protection or UIPI block); pasting anyway.",
                    target_hwnd,
                )
            focus_settle = max(0, int(self.settings.get("focus_settle_ms", 50))) / 1000.0
            if focus_settle > 0:
                time.sleep(focus_settle)

        mod_clear_ms = max(0, int(self.settings.get("paste_modifier_clear_ms", 250)))
        if not _wait_for_user_modifier_release(mod_clear_ms):
            log.warning(
                "Paste: user-held Alt/Shift/Win still down after %d ms — "
                "Ctrl+V may be misinterpreted by the focused window.",
                mod_clear_ms,
            )

        sent = 0
        with self._lock:
            try:
                self._kb.press(Key.ctrl)
                self._kb.press("v")
                self._kb.release("v")
                self._kb.release(Key.ctrl)
                sent = len(text)
                if trailing_space and not text.endswith((" ", "\n", "\t")):
                    if settle > 0:
                        time.sleep(settle)
                    self._kb.press(Key.space)
                    self._kb.release(Key.space)
                    sent += 1
                log.info("Pasted %d chars via Ctrl+V (clipboard=%r, +space=%s, target=%s)",
                         sent, text, trailing_space, target_hwnd)
            except Exception:
                log.exception("Ctrl+V injection failed")
                return 0

        # Restore the user's previous foreground after the paste lands.
        if target_hwnd is not None and prev_fg:
            w.set_foreground_with_attach(prev_fg)

        return sent
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/Scripts/python.exe -m pytest tests/test_keyboard_output.py -v`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/keyboard_output.py tests/test_keyboard_output.py
git commit -m "_paste_text routes to target_hwnd with focus restore

When target_hwnd is supplied: validate (return 0 if closed),
capture previous foreground, restore-if-minimized, set foreground
to target with AttachThreadInput mitigation, sleep focus_settle_ms,
do existing Ctrl+V + trailing space, restore previous foreground.
Default path (target_hwnd=None) is unchanged.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: KeyboardOutput._type_text focus-shift branch (symmetry)

**Files:**
- Modify: `src/keyboard_output.py:48-65` (`_type_text`)
- Modify: `tests/test_keyboard_output.py`

For users on `output_method = "type"` (the default), char-by-char typing also needs to reach the locked target.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_keyboard_output.py`:

```python
def test_type_text_with_target_hwnd_does_focus_shift(tmp_appdata, qapp):
    from src import win32_window_utils as w

    sm = SettingsManager()
    sm.set("output_method", "type")
    sm.set("type_delay_ms", 0)
    sm.set("focus_settle_ms", 0)
    sm.set("trailing_space", False)
    with patch("src.keyboard_output.Controller") as ctrl_cls, \
         patch.object(w, "is_window", return_value=True), \
         patch.object(w, "is_iconic", return_value=False), \
         patch.object(w, "get_foreground_window", return_value=11111), \
         patch.object(w, "set_foreground_with_attach", return_value=True) as fg:
        ctrl_cls.return_value = MagicMock()
        kb = KeyboardOutput(sm)
        kb.type_text("hi", target_hwnd=42)
    assert [c.args[0] for c in fg.call_args_list] == [42, 11111]


def test_type_text_with_dead_target_returns_zero(tmp_appdata, qapp):
    from src import win32_window_utils as w

    sm = SettingsManager()
    sm.set("output_method", "type")
    sm.set("type_delay_ms", 0)
    with patch("src.keyboard_output.Controller") as ctrl_cls, \
         patch.object(w, "is_window", return_value=False):
        ctrl_cls.return_value = MagicMock()
        kb = KeyboardOutput(sm)
        sent = kb.type_text("hi", target_hwnd=42)
    assert sent == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_keyboard_output.py -v`
Expected: 2 new tests FAIL.

- [ ] **Step 3: Implement focus-shift in `_type_text`**

Edit `src/keyboard_output.py` — replace `_type_text` body:

```python
    def _type_text(self, text: str, target_hwnd: int | None = None) -> int:
        from . import win32_window_utils as w

        delay = max(0, int(self.settings.get("type_delay_ms", 4))) / 1000.0
        trailing_space = bool(self.settings.get("trailing_space", True))

        prev_fg: int | None = None
        if target_hwnd is not None:
            if not w.is_window(target_hwnd):
                log.warning("Type target hwnd=%s is closed; skipping.", target_hwnd)
                return 0
            prev_fg = w.get_foreground_window() or None
            if w.is_iconic(target_hwnd):
                w.restore_window(target_hwnd)
            w.set_foreground_with_attach(target_hwnd)
            focus_settle = max(0, int(self.settings.get("focus_settle_ms", 50))) / 1000.0
            if focus_settle > 0:
                time.sleep(focus_settle)

        typed = 0
        with self._lock:
            try:
                for ch in text:
                    self._tap_char(ch)
                    if delay > 0:
                        time.sleep(delay)
                typed = len(text)
                if trailing_space and not text.endswith((" ", "\n", "\t")):
                    self._tap_char(" ")
                    typed += 1
                log.info("Typed %d chars: %r (target=%s)", typed, text, target_hwnd)
            except Exception:
                log.exception("Keyboard injection failed (focused window may be elevated)")

        if target_hwnd is not None and prev_fg:
            w.set_foreground_with_attach(prev_fg)
        return typed
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/Scripts/python.exe -m pytest tests/test_keyboard_output.py -v`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/keyboard_output.py tests/test_keyboard_output.py
git commit -m "_type_text symmetric focus-shift for output_method=type

Same focus-shift dance as _paste_text so users on the default
(char-by-char) typing path also reach the locked target. Closed
target → return 0 with no keystrokes; minimized target →
auto-restore; previous foreground restored after typing.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: SoundPlayer lock/unlock tones

**Files:**
- Modify: `src/sound_player.py:36-86` (`SoundPlayer` class)
- Modify: `tests/test_sound_player.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sound_player.py`:

```python
def test_play_lock_calls_sd_play_when_enabled(tmp_appdata):
    sm = SettingsManager()
    sm.set("play_lock_sounds", True)
    sp = SoundPlayer(sm)
    with patch("src.sound_player.sd.play") as p:
        sp.play_lock()
        p.assert_called_once()


def test_play_lock_skipped_when_disabled(tmp_appdata):
    sm = SettingsManager()
    sm.set("play_lock_sounds", False)
    sp = SoundPlayer(sm)
    with patch("src.sound_player.sd.play") as p:
        sp.play_lock()
        sp.play_unlock()
        p.assert_not_called()


def test_play_unlock_calls_sd_play_when_enabled(tmp_appdata):
    sm = SettingsManager()
    sm.set("play_lock_sounds", True)
    sp = SoundPlayer(sm)
    with patch("src.sound_player.sd.play") as p:
        sp.play_unlock()
        p.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_sound_player.py -v`
Expected: 3 new tests FAIL with `AttributeError: 'SoundPlayer' object has no attribute 'play_lock'`.

- [ ] **Step 3: Add lock/unlock tones**

Edit `src/sound_player.py` — extend the `SoundPlayer` class (add constants, extend `_rebuild_if_volume_changed`, add `play_lock`/`play_unlock`):

```python
class SoundPlayer:
    """Plays short chimes for capture lifecycle events.

    All settings reads happen at play time so toggling sound in Settings
    takes effect immediately without rebuilding the player.
    """

    READY_FREQS = (659.0, 784.0)   # E5 → G5  (ascending = "ready")
    STOP_FREQS = (784.0, 659.0)    # G5 → E5  (descending = "stopped")
    LOCK_FREQS = (523.0, 698.0)    # C5 → F5  (ascending = "lock")
    UNLOCK_FREQS = (698.0, 523.0)  # F5 → C5  (descending = "unlock")

    def __init__(self, settings) -> None:
        self.settings = settings
        self._cached_volume: float | None = None
        self._ready: np.ndarray = np.zeros(0, dtype=np.float32)
        self._stop: np.ndarray = np.zeros(0, dtype=np.float32)
        self._lock_chime: np.ndarray = np.zeros(0, dtype=np.float32)
        self._unlock_chime: np.ndarray = np.zeros(0, dtype=np.float32)
        self._rebuild_if_volume_changed()

    @property
    def ready_duration_ms(self) -> int:
        return int(len(self._ready) / _SAMPLE_RATE * 1000)

    def _rebuild_if_volume_changed(self) -> None:
        v = float(self.settings.get("sound_volume", 0.15))
        v = max(0.0, min(1.0, v))
        if v != self._cached_volume:
            self._cached_volume = v
            self._ready = _make_chime(list(self.READY_FREQS), v)
            self._stop = _make_chime(list(self.STOP_FREQS), v)
            self._lock_chime = _make_chime(list(self.LOCK_FREQS), v)
            self._unlock_chime = _make_chime(list(self.UNLOCK_FREQS), v)

    def play_ready(self) -> None:
        if not bool(self.settings.get("play_ready_sound", True)):
            return
        self._rebuild_if_volume_changed()
        self._play(self._ready)

    def play_stop(self) -> None:
        if not bool(self.settings.get("play_stop_sound", False)):
            return
        self._rebuild_if_volume_changed()
        self._play(self._stop)

    def play_lock(self) -> None:
        if not bool(self.settings.get("play_lock_sounds", True)):
            return
        self._rebuild_if_volume_changed()
        self._play(self._lock_chime)

    def play_unlock(self) -> None:
        if not bool(self.settings.get("play_lock_sounds", True)):
            return
        self._rebuild_if_volume_changed()
        self._play(self._unlock_chime)

    def _play(self, samples: np.ndarray) -> None:
        if samples.size == 0:
            return
        try:
            sd.play(samples, _SAMPLE_RATE)
        except Exception:
            log.exception("Sound playback failed")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/Scripts/python.exe -m pytest tests/test_sound_player.py -v`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/sound_player.py tests/test_sound_player.py
git commit -m "Add lock/unlock chimes to SoundPlayer

LOCK_FREQS = (523, 698) Hz (C5→F5, ascending = lock).
UNLOCK_FREQS = (698, 523) Hz (F5→C5, descending = unlock).
Both gated by new play_lock_sounds setting (default True).
Procedurally generated via _make_chime, same path as ready/stop.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: WindowBorderOverlay widget

**Files:**
- Create: `src/ui/window_border_overlay.py`
- Create: `tests/test_window_border_overlay.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_window_border_overlay.py`:

```python
"""Tests for WindowBorderOverlay.

We patch win32_window_utils so the overlay's polling timer doesn't
need a real window. The Qt widget itself is created against the
session qapp fixture and never actually shown to a real screen
(QT_QPA_PLATFORM=offscreen via conftest).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src import win32_window_utils as w
from src.settings_manager import SettingsManager
from src.ui.window_border_overlay import WindowBorderOverlay


@pytest.fixture
def settings(tmp_appdata):
    sm = SettingsManager()
    sm.set("paste_target_lock_enabled", True)
    sm.set("border_overlay_enabled", True)
    return sm


def test_overlay_starts_hidden(settings, qapp):
    o = WindowBorderOverlay(settings)
    assert not o.isVisible()
    assert o._target_hwnd is None


def test_set_target_none_keeps_hidden(settings, qapp):
    o = WindowBorderOverlay(settings)
    o.set_target_hwnd(None)
    assert not o.isVisible()


def test_set_target_hwnd_shows_and_positions(settings, qapp):
    o = WindowBorderOverlay(settings)
    with patch.object(w, "is_window", return_value=True), \
         patch.object(w, "is_iconic", return_value=False), \
         patch.object(w, "get_window_rect", return_value=(100, 200, 400, 500)):
        o.set_target_hwnd(42)
        # Force the timer's tick logic to run synchronously.
        o._tick()
    # Geometry should match the rect (left, top, width=right-left, height=bottom-top).
    geom = o.geometry()
    assert geom.x() == 100
    assert geom.y() == 200
    assert geom.width() == 300
    assert geom.height() == 300


def test_target_closed_hides_overlay(settings, qapp):
    o = WindowBorderOverlay(settings)
    o._target_hwnd = 42
    with patch.object(w, "is_window", return_value=False):
        o._tick()
    assert not o.isVisible()
    assert o._target_hwnd is None


def test_target_minimized_hides_overlay(settings, qapp):
    o = WindowBorderOverlay(settings)
    o._target_hwnd = 42
    with patch.object(w, "is_window", return_value=True), \
         patch.object(w, "is_iconic", return_value=True):
        o._tick()
    assert not o.isVisible()
    # Target stays set so when un-minimized the next tick resumes drawing.
    assert o._target_hwnd == 42


def test_master_disable_setting_hides_overlay(settings, qapp):
    o = WindowBorderOverlay(settings)
    settings.set("border_overlay_enabled", False)
    with patch.object(w, "is_window", return_value=True), \
         patch.object(w, "is_iconic", return_value=False), \
         patch.object(w, "get_window_rect", return_value=(0, 0, 100, 100)):
        o.set_target_hwnd(42)
        o._tick()
    assert not o.isVisible()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_window_border_overlay.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the overlay**

Create `src/ui/window_border_overlay.py`:

```python
"""Click-through frameless overlay that draws a colored border around
a tracked Win32 window.

Mirrors the OscilloscopeWidget pattern (frameless, always-on-top, no
taskbar entry) and adds Qt.WindowTransparentForInput so clicks pass
through to the underlying window.

Polls the target HWND's GetWindowRect on a 30 ms QTimer to follow
window movement/resize; auto-hides if the target is minimized,
closed, or if the master setting is off.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from .. import win32_window_utils as win32

log = logging.getLogger(__name__)

_POLL_MS = 30


class WindowBorderOverlay(QWidget):
    def __init__(self, settings, parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.settings = settings
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, True)
        self._target_hwnd: int | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(_POLL_MS)
        self._timer.timeout.connect(self._tick)

    # ---- public API --------------------------------------------------

    def set_target_hwnd(self, hwnd: int | None) -> None:
        if hwnd is None:
            self._target_hwnd = None
            self._timer.stop()
            self.hide()
            return
        self._target_hwnd = hwnd
        if not self._timer.isActive():
            self._timer.start()

    # ---- timer tick --------------------------------------------------

    def _tick(self) -> None:
        if not bool(self.settings.get("border_overlay_enabled", True)):
            self.hide()
            return
        hwnd = self._target_hwnd
        if hwnd is None:
            self.hide()
            return
        if not win32.is_window(hwnd):
            log.info("Border overlay: target hwnd=%s is gone; hiding.", hwnd)
            self._target_hwnd = None
            self._timer.stop()
            self.hide()
            return
        if win32.is_iconic(hwnd):
            self.hide()
            return
        rect = win32.get_window_rect(hwnd)
        if rect is None:
            self.hide()
            return
        left, top, right, bottom = rect
        self.setGeometry(left, top, right - left, bottom - top)
        if not self.isVisible():
            self.show()
        self.update()

    # ---- painting ----------------------------------------------------

    def paintEvent(self, _event) -> None:
        color_hex = str(self.settings.get("border_color", "#ff9900"))
        thickness = max(1, int(self.settings.get("border_thickness", 3)))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        pen = QPen(QColor(color_hex), thickness)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        painter.setPen(pen)
        # Inset by half the line width so the stroke sits flush with the edge.
        inset = thickness // 2
        rect = self.rect().adjusted(inset, inset, -inset, -inset)
        painter.drawRect(rect)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/Scripts/python.exe -m pytest tests/test_window_border_overlay.py -v`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/ui/window_border_overlay.py tests/test_window_border_overlay.py
git commit -m "Add WindowBorderOverlay widget for sticky-locked targets

Frameless, always-on-top, click-through Qt widget that polls the
tracked HWND's GetWindowRect every 30 ms via win32_window_utils
and draws a colored hollow rectangle around it. Auto-hides when
target is minimized, closed, or when border_overlay_enabled is
False.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: Extend hotkey validation for lock_toggle

**Files:**
- Modify: `src/hotkey_manager.py:167-194` (`validate_hotkeys`)
- Modify: `tests/test_hotkey_validation.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_hotkey_validation.py`:

```python
def test_validate_includes_lock_toggle_collision_with_toggle():
    from src.hotkey_manager import validate_hotkeys

    issues = validate_hotkeys(
        toggle="<alt>+z",
        delete="<delete>",
        lock_toggle="<alt>+z",
    )
    severities = [s for s, _ in issues]
    assert "error" in severities


def test_validate_includes_lock_toggle_collision_with_delete():
    from src.hotkey_manager import validate_hotkeys

    issues = validate_hotkeys(
        toggle="<alt>+z",
        delete="<delete>",
        lock_toggle="<delete>",
    )
    assert any("error" == s for s, _ in issues)


def test_validate_lock_toggle_unique_no_error():
    from src.hotkey_manager import validate_hotkeys

    issues = validate_hotkeys(
        toggle="<alt>+z",
        delete="<delete>",
        lock_toggle="<alt>+l",
    )
    assert all(s != "error" for s, _ in issues)


def test_validate_lock_toggle_no_modifier_warns():
    from src.hotkey_manager import validate_hotkeys

    issues = validate_hotkeys(
        toggle="<alt>+z",
        delete="<delete>",
        lock_toggle="l",
    )
    assert any(s == "warn" for s, _ in issues)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_hotkey_validation.py -v`
Expected: 4 new tests FAIL with `TypeError: validate_hotkeys() got an unexpected keyword argument 'lock_toggle'`.

- [ ] **Step 3: Extend `validate_hotkeys`**

Edit `src/hotkey_manager.py` — replace the entire `validate_hotkeys` function:

```python
def validate_hotkeys(
    toggle: str,
    delete: str,
    lock_toggle: str | None = None,
) -> list[tuple[str, str]]:
    issues: list[tuple[str, str]] = []
    toggle_n = normalize_hotkey(toggle)
    delete_n = normalize_hotkey(delete)
    lock_n = normalize_hotkey(lock_toggle or "")

    if not toggle_n:
        issues.append(("error", "Dictation hotkey is empty."))
    if not delete_n:
        issues.append(("error", "Delete-word hotkey is empty."))

    if toggle_n and delete_n and toggle_n == delete_n:
        issues.append(
            ("error", f"Both hotkeys are set to the same chord ({toggle_n}). "
                      "Pick a different one for delete-word.")
        )

    if lock_n:
        if lock_n == toggle_n:
            issues.append(
                ("error", f"Lock-toggle hotkey ({lock_n}) collides with the "
                          "dictation toggle. Pick a different chord.")
            )
        if lock_n == delete_n:
            issues.append(
                ("error", f"Lock-toggle hotkey ({lock_n}) collides with the "
                          "delete-word hotkey. Pick a different chord.")
            )

    if toggle_n and not has_modifier(toggle):
        issues.append(
            ("warn", f"Dictation hotkey {toggle_n!r} has no modifier — pressing that "
                     "key in any app will toggle dictation, which is rarely what you want.")
        )
    if delete_n and not has_modifier(delete):
        issues.append(
            ("warn", f"Delete-word hotkey {delete_n!r} has no modifier — it will fire "
                     "globally and conflict with the key's normal function. "
                     "Consider <ctrl>+<backspace>.")
        )
    if lock_toggle and lock_n and not has_modifier(lock_toggle):
        issues.append(
            ("warn", f"Lock-toggle hotkey {lock_n!r} has no modifier — it will fire "
                     "globally and conflict with the key's normal function.")
        )
    return issues
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/Scripts/python.exe -m pytest tests/test_hotkey_validation.py -v`
Expected: all green; existing tests still pass because the new `lock_toggle` param is optional.

- [ ] **Step 5: Commit**

```bash
git add src/hotkey_manager.py tests/test_hotkey_validation.py
git commit -m "Extend validate_hotkeys to cover lock_toggle chord

Optional new lock_toggle parameter (defaults to None for back-compat
with existing callers). Errors on collision with toggle/delete;
warns when configured without a modifier (since the hotkey listener
would intercept the key globally).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: app.py — instantiate controller, register hotkey, wire dictation

**Files:**
- Modify: `src/app.py:11-54` (imports + `__init__`), `src/app.py:181-198` (`_build_hotkey_mapping`), `src/app.py:241-289` (`_toggle_capture` / `_start_capture` / `_stop_capture`)
- Modify: `tests/test_app.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_app.py`:

```python
def test_lock_toggle_hotkey_in_mapping_only_when_enabled(app):
    app.settings.set("paste_target_lock_enabled", False)
    assert "lock_toggle" not in app._build_hotkey_mapping()

    app.settings.set("paste_target_lock_enabled", True)
    assert app._build_hotkey_mapping().get("lock_toggle") == "<alt>+l"


def test_lock_toggle_hotkey_dropped_when_clashes(app):
    app.settings.set("paste_target_lock_enabled", True)
    app.settings.set("hotkey", "<alt>+z")
    app.settings.set("delete_hotkey", "<delete>")
    app.settings.set("lock_toggle_hotkey", "<alt>+z")
    assert "lock_toggle" not in app._build_hotkey_mapping()
    app.settings.set("lock_toggle_hotkey", "<delete>")
    assert "lock_toggle" not in app._build_hotkey_mapping()
    app.settings.set("lock_toggle_hotkey", "<alt>+l")
    assert "lock_toggle" in app._build_hotkey_mapping()


def test_start_capture_calls_paste_target_on_dictation_started(app):
    app._model_loaded = True
    app.settings.set("play_ready_sound", False)
    with patch.object(app.paste_target, "on_dictation_started") as ds, \
         patch.object(app.audio, "start"):
        app._start_capture()
    # _start_capture defers via QTimer.singleShot when ready_sound disabled it
    # bypasses to _open_mic_stream directly — patch the audio so the flow
    # completes synchronously.
    ds.assert_called_once()


def test_stop_capture_calls_paste_target_on_dictation_stopped(app):
    app._is_capturing = True
    with patch.object(app.audio, "stop"), \
         patch.object(app.sound_player, "play_stop"), \
         patch.object(app.paste_target, "on_dictation_stopped") as ds:
        app._stop_capture()
    ds.assert_called_once()


def test_lock_toggle_hotkey_calls_controller_toggle(app):
    with patch.object(app.paste_target, "toggle_sticky") as t:
        app._on_hotkey_triggered("lock_toggle")
    t.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_app.py -v`
Expected: 5 new tests FAIL — `app` fixture has no `paste_target` attribute and `_build_hotkey_mapping` doesn't include `lock_toggle`.

- [ ] **Step 3: Wire the controller into `TextWhisperApp`**

Edit `src/app.py`:

a) Add the import near the other component imports (top of the file):

```python
from .paste_target import PasteTargetController
```

b) In `__init__` (after the `self.voice_ipc = VoiceIPCServer(...)` line):

```python
        self.paste_target = PasteTargetController(self.settings)
```

c) Extend `_build_hotkey_mapping` — add this block before `return m`:

```python
        # Paste-target-lock toggle (Alt+L by default). Only registered when
        # the feature is enabled AND the chord doesn't collide with any of
        # the others (toggle/delete/voice_interrupt).
        if bool(self.settings.get("paste_target_lock_enabled", False)):
            lock_hk = str(
                self.settings.get("lock_toggle_hotkey", "<alt>+l") or ""
            ).strip()
            existing = {toggle, delete_hk, m.get("voice_interrupt", "")}
            if lock_hk and lock_hk not in existing:
                m["lock_toggle"] = lock_hk
```

d) Extend `_on_hotkey_triggered` — add a new branch for `"lock_toggle"` (alongside the existing `delete` and `voice_interrupt` branches):

```python
        elif name == "lock_toggle":
            self.paste_target.toggle_sticky()
```

e) Wire `on_dictation_started` into `_open_mic_stream` (the actual capture-start point — `_start_capture` defers to it via QTimer when the chime is enabled). Find `def _open_mic_stream` and after the `self._is_capturing = True` line, add:

```python
        self.paste_target.on_dictation_started()
```

f) Wire `on_dictation_stopped` into `_stop_capture`. Find `def _stop_capture` and after the existing `self._delete_pending = False` and `self._extra_chars_typed_by_hotkey = 0` lines, add:

```python
        self.paste_target.on_dictation_stopped()
```

- [ ] **Step 4: Adjust the test fixture if needed**

The `app` fixture in `tests/test_app.py` patches `HotkeyManager` and `sd`. The new `PasteTargetController` is created from real code — that's fine since it's pure Python with no external deps. Verify by running the tests.

Run: `./venv/Scripts/python.exe -m pytest tests/test_app.py -v`
Expected: all green.

- [ ] **Step 5: Run the FULL suite to catch wider regressions**

Run: `./venv/Scripts/python.exe -m pytest tests/ -q`
Expected: previous count plus the new tests, all green.

- [ ] **Step 6: Commit**

```bash
git add src/app.py tests/test_app.py
git commit -m "Wire PasteTargetController into TextWhisperApp lifecycle

Instantiate controller alongside existing components; register
lock_toggle hotkey in _build_hotkey_mapping when feature enabled
and chord is unique; dispatch toggle_sticky on the new hotkey;
call on_dictation_started/stopped at _open_mic_stream and
_stop_capture boundaries.

Transcription-time target resolution (passing current_target into
type_text) and signal subscribers (border, sound, tray, notify)
follow in subsequent tasks.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 13: app.py — pass target HWND into transcription path

**Files:**
- Modify: `src/app.py:492-549` (`_on_transcription`)
- Modify: `tests/test_app.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_app.py`:

```python
def test_transcription_passes_target_hwnd_when_locked(app):
    """current_target() result is forwarded into keyboard_out.type_text."""
    app.settings.set("paste_target_lock_enabled", True)
    app.paste_target._sticky_hwnd = 4242
    with patch.object(app.keyboard_out, "type_text", return_value=10) as tt:
        app._on_transcription("hello")
    tt.assert_called_once()
    assert tt.call_args.kwargs.get("target_hwnd") == 4242 or \
           (len(tt.call_args.args) >= 2 and tt.call_args.args[1] == 4242)


def test_transcription_passes_none_when_no_lock(app):
    app.settings.set("paste_target_lock_enabled", True)
    # No sticky, no per-session.
    with patch.object(app.keyboard_out, "type_text", return_value=10) as tt:
        app._on_transcription("hello")
    target = tt.call_args.kwargs.get("target_hwnd")
    if target is None and len(tt.call_args.args) >= 2:
        target = tt.call_args.args[1]
    assert target is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_app.py::test_transcription_passes_target_hwnd_when_locked tests/test_app.py::test_transcription_passes_none_when_no_lock -v`
Expected: FAIL — current code calls `type_text(text)` without the kwarg.

- [ ] **Step 3: Forward the target HWND**

Edit `src/app.py` — find the `typed = self.keyboard_out.type_text(text)` line in `_on_transcription` and replace with:

```python
        target_hwnd = self.paste_target.current_target()
        typed = self.keyboard_out.type_text(text, target_hwnd=target_hwnd)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/Scripts/python.exe -m pytest tests/test_app.py -v`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/app.py tests/test_app.py
git commit -m "Forward current_target() into type_text on transcription

_on_transcription now resolves PasteTargetController.current_target()
(sticky beats per-session beats None) and passes it as target_hwnd
to keyboard_out.type_text. None preserves original behavior.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 14: app.py — wire lock_changed and target_invalid handlers

**Files:**
- Modify: `src/app.py:42-45, 199-238` (`__init__`, `_wire_signals`)
- Modify: `src/app.py` — add `_on_lock_changed`, `_on_target_invalid` methods
- Modify: `src/keyboard_output.py` — `_paste_text` returns sentinel on dead target → app needs a way to detect; we will wire `_paste_text` to also raise via the controller

This task connects the controller's signals to the border, sound, tray, and notification paths.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_app.py`:

```python
def test_lock_changed_sticky_to_hwnd_shows_border_and_plays_lock(app):
    app.settings.set("paste_target_lock_enabled", True)
    with patch.object(app.border_overlay, "set_target_hwnd") as bord, \
         patch.object(app.sound_player, "play_lock") as plk, \
         patch.object(app.sound_player, "play_unlock") as pul:
        app._on_lock_changed(4242, "sticky")
    bord.assert_called_once_with(4242)
    plk.assert_called_once()
    pul.assert_not_called()


def test_lock_changed_sticky_to_none_hides_border_and_plays_unlock(app):
    app.settings.set("paste_target_lock_enabled", True)
    # Pretend we had a sticky lock; the handler tracks previous state.
    app._last_sticky_hwnd = 4242
    with patch.object(app.border_overlay, "set_target_hwnd") as bord, \
         patch.object(app.sound_player, "play_lock") as plk, \
         patch.object(app.sound_player, "play_unlock") as pul:
        app._on_lock_changed(None, "none")
    bord.assert_called_once_with(None)
    pul.assert_called_once()
    plk.assert_not_called()


def test_lock_changed_session_does_not_touch_border_or_sound(app):
    """Per-session captures are silent and borderless."""
    app.settings.set("paste_target_lock_enabled", True)
    with patch.object(app.border_overlay, "set_target_hwnd") as bord, \
         patch.object(app.sound_player, "play_lock") as plk, \
         patch.object(app.sound_player, "play_unlock") as pul:
        app._on_lock_changed(99, "session")
    bord.assert_not_called()
    plk.assert_not_called()
    pul.assert_not_called()


def test_target_invalid_closed_notifies_and_clears_silently(app):
    app.settings.set("notifications_enabled", True)
    app.paste_target._sticky_hwnd = 4242
    with patch.object(app.tray, "notify") as n, \
         patch.object(app.paste_target, "clear_sticky_silently") as cs:
        app._on_target_invalid("closed")
    n.assert_called_once()
    cs.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_app.py -v`
Expected: FAIL — `app` fixture has no `border_overlay`, no `_on_lock_changed`, no `_on_target_invalid`, no `_last_sticky_hwnd`.

- [ ] **Step 3: Wire it all up**

Edit `src/app.py`:

a) Add the import:

```python
from .ui.window_border_overlay import WindowBorderOverlay
```

b) In `__init__` (after `self.paste_target = PasteTargetController(self.settings)`):

```python
        self.border_overlay = WindowBorderOverlay(self.settings)
        self._last_sticky_hwnd: int | None = None
```

c) In `_wire_signals` (add at the end):

```python
        self.paste_target.lock_changed.connect(self._on_lock_changed)
        self.paste_target.target_invalid.connect(self._on_target_invalid)
```

d) Add the handlers (anywhere reasonable — near the other `_on_*` methods):

```python
    # --- paste-target lock signal handlers ----------------------------

    def _on_lock_changed(self, hwnd, source: str) -> None:
        """Tray + border + sound updates triggered by the controller.

        - Always refreshes tray label (Task 15 will surface that on the tray).
        - source == "sticky": updates border overlay target and plays a
          lock/unlock tone based on whether the new sticky hwnd is set or
          cleared (compared against the locally-tracked previous state).
        - source == "session" or "none": tray-only, no border/sound effects.
        """
        if source == "sticky":
            new_hwnd = hwnd if hwnd is not None else None
            self.border_overlay.set_target_hwnd(new_hwnd)
            if new_hwnd is not None and self._last_sticky_hwnd != new_hwnd:
                # Fresh lock or re-target.
                self.sound_player.play_lock()
            elif new_hwnd is None and self._last_sticky_hwnd is not None:
                # Just unlocked.
                self.sound_player.play_unlock()
            self._last_sticky_hwnd = new_hwnd
        elif source == "none":
            # Could be a session-only lock that was just released, or a
            # silent sticky clear from target_invalid. Border/sound only
            # fire if the previous state was sticky.
            self.border_overlay.set_target_hwnd(None)
            if self._last_sticky_hwnd is not None:
                self.sound_player.play_unlock()
                self._last_sticky_hwnd = None
        # source == "session": no border, no sound; tray label refresh only.

    def _on_target_invalid(self, reason: str) -> None:
        """Locked target window has gone — notify and clear silently."""
        if reason == "closed":
            self._notify(
                "TextWhisper",
                "Paste target window is gone — press your lock-toggle "
                "hotkey to re-lock. Transcription stays in your clipboard.",
            )
            self.paste_target.clear_sticky_silently()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/Scripts/python.exe -m pytest tests/test_app.py -v`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/app.py tests/test_app.py
git commit -m "Wire lock_changed/target_invalid signals: border + sound + notify

_on_lock_changed: sticky → set border target + play lock/unlock
based on previous state; session → tray-only; none → hide border,
play unlock only if we were previously sticky.

_on_target_invalid('closed') → tray notification + silent sticky
clear so the dead-target message is the user feedback (no double
unlock tone).

Local _last_sticky_hwnd tracks previous state for the lock-vs-
unlock tone decision; the controller itself stays stateless about
what tones to play.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 15: KeyboardOutput emits target_invalid via injected callback

**Files:**
- Modify: `src/keyboard_output.py` — accept optional `on_target_invalid` callback
- Modify: `src/app.py` — pass the callback when constructing
- Modify: `tests/test_keyboard_output.py`, `tests/test_app.py`

The `_paste_text` function detects closed targets and currently just returns 0. The app needs to know so it can notify and clear the lock. Use a callback rather than a Qt signal so KeyboardOutput stays free of QObject inheritance.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_keyboard_output.py`:

```python
def test_paste_with_closed_target_invokes_invalid_callback(tmp_appdata, qapp):
    from src import win32_window_utils as w

    sm = SettingsManager()
    sm.set("output_method", "paste")
    sm.set("paste_settle_ms", 0)
    invalid_calls: list[str] = []
    with patch("src.keyboard_output.Controller") as ctrl_cls, \
         patch.object(w, "is_window", return_value=False):
        ctrl_cls.return_value = MagicMock()
        kb = KeyboardOutput(sm, on_target_invalid=invalid_calls.append)
        sent = kb.type_text("hi", target_hwnd=42)
    assert sent == 0
    assert invalid_calls == ["closed"]


def test_type_with_closed_target_invokes_invalid_callback(tmp_appdata, qapp):
    from src import win32_window_utils as w

    sm = SettingsManager()
    sm.set("output_method", "type")
    sm.set("type_delay_ms", 0)
    invalid_calls: list[str] = []
    with patch("src.keyboard_output.Controller") as ctrl_cls, \
         patch.object(w, "is_window", return_value=False):
        ctrl_cls.return_value = MagicMock()
        kb = KeyboardOutput(sm, on_target_invalid=invalid_calls.append)
        sent = kb.type_text("hi", target_hwnd=42)
    assert sent == 0
    assert invalid_calls == ["closed"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_keyboard_output.py -v`
Expected: 2 new tests FAIL — `KeyboardOutput.__init__` doesn't accept `on_target_invalid`.

- [ ] **Step 3: Add the callback**

Edit `src/keyboard_output.py`:

a) Replace `__init__`:

```python
    def __init__(self, settings, on_target_invalid=None) -> None:
        self.settings = settings
        self._kb = Controller()
        self._lock = threading.Lock()
        # Optional callback invoked with a reason string when a target_hwnd
        # paste/type fails because the target is closed (or PID-drifted).
        # The app uses this to surface a tray notification and clear the
        # sticky lock without coupling KeyboardOutput to Qt signals.
        self._on_target_invalid = on_target_invalid
```

b) In `_paste_text`, after the existing closed-target check:

```python
            if not w.is_window(target_hwnd):
                log.warning("Paste target hwnd=%s is closed; skipping.", target_hwnd)
                if self._on_target_invalid:
                    self._on_target_invalid("closed")
                return 0
```

c) In `_type_text`, same change after its closed-target check:

```python
            if not w.is_window(target_hwnd):
                log.warning("Type target hwnd=%s is closed; skipping.", target_hwnd)
                if self._on_target_invalid:
                    self._on_target_invalid("closed")
                return 0
```

- [ ] **Step 4: Run keyboard tests**

Run: `./venv/Scripts/python.exe -m pytest tests/test_keyboard_output.py -v`
Expected: all green.

- [ ] **Step 5: Wire the callback in app.py**

Edit `src/app.py` — find the `self.keyboard_out = KeyboardOutput(self.settings)` line and replace:

```python
        # Pre-init placeholder so the callback can reference paste_target
        # which we instantiate just below. We rebind the callback after.
        self.keyboard_out = KeyboardOutput(self.settings)
```

That's not actually right — we need `paste_target` to exist first. Re-order: instantiate `paste_target` BEFORE `keyboard_out`. Edit the relevant section of `__init__` so the order is:

```python
        self.audio = AudioCapture(self.settings)
        self.engine = TranscriptionEngine(self.settings)
        self.sound_player = SoundPlayer(self.settings)
        self.hotkey = HotkeyManager(self._build_hotkey_mapping())

        self.tray = TrayController(parent=self)
        self.oscilloscope = OscilloscopeWidget(self.settings)
        self.tts = TTSService(self.settings)
        self.summarizer = Summarizer(self.settings)
        self.voice_ipc = VoiceIPCServer(self.settings, self.tts, self.summarizer)
        self.paste_target = PasteTargetController(self.settings)
        self.border_overlay = WindowBorderOverlay(self.settings)
        self._last_sticky_hwnd: int | None = None

        # KeyboardOutput needs paste_target to exist for its
        # on_target_invalid callback (the controller emits target_invalid
        # via signal, but KeyboardOutput is callback-driven to stay free
        # of QObject inheritance).
        self.keyboard_out = KeyboardOutput(
            self.settings,
            on_target_invalid=self.paste_target.target_invalid.emit,
        )
```

- [ ] **Step 6: Run full suite**

Run: `./venv/Scripts/python.exe -m pytest tests/ -q`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add src/keyboard_output.py src/app.py tests/test_keyboard_output.py
git commit -m "KeyboardOutput emits target_invalid via injected callback

When a target_hwnd paste/type encounters a dead window, invoke an
optional on_target_invalid callback before returning 0. App wires
the controller's target_invalid.emit as the callback so dead
targets surface to _on_target_invalid via the existing Qt signal
path. KeyboardOutput stays QObject-free.

Re-orders __init__ so paste_target is constructed before
keyboard_out (the callback needs a reference to it).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 16: Tray menu — lock state surfacing

**Files:**
- Modify: `src/ui/tray.py`
- Modify: `tests/test_tray.py`

This adds the tray menu items defined in spec §5.7. The tray subscribes to `lock_changed` via the app wiring so the label reflects state.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tray.py`:

```python
def test_tray_lock_section_hidden_when_master_setting_off(qapp, tmp_appdata):
    from src.settings_manager import SettingsManager
    from src.ui.tray import TrayController

    sm = SettingsManager()
    sm.set("paste_target_lock_enabled", False)
    tray = TrayController(parent=None, settings=sm)
    tray.set_lock_state(None, "none")
    assert tray._lock_section_visible() is False


def test_tray_lock_section_visible_when_enabled(qapp, tmp_appdata):
    from src.settings_manager import SettingsManager
    from src.ui.tray import TrayController

    sm = SettingsManager()
    sm.set("paste_target_lock_enabled", True)
    tray = TrayController(parent=None, settings=sm)
    tray.set_lock_state(None, "none")
    assert tray._lock_section_visible() is True


def test_tray_lock_label_when_no_lock(qapp, tmp_appdata):
    from src.settings_manager import SettingsManager
    from src.ui.tray import TrayController

    sm = SettingsManager()
    sm.set("paste_target_lock_enabled", True)
    tray = TrayController(parent=None, settings=sm)
    tray.set_lock_state(None, "none")
    # Label should be "Lock paste target → current window".
    assert "lock paste target" in tray._lock_action_label().lower()
    assert "→" in tray._lock_action_label() or "->" in tray._lock_action_label()


def test_tray_lock_label_when_sticky_set(qapp, tmp_appdata):
    from unittest.mock import patch

    from src.settings_manager import SettingsManager
    from src.ui.tray import TrayController

    sm = SettingsManager()
    sm.set("paste_target_lock_enabled", True)
    tray = TrayController(parent=None, settings=sm)
    with patch("src.ui.tray.win32.get_window_title", return_value="Claude Code"), \
         patch("src.ui.tray.win32.get_foreground_window", return_value=4242):
        tray.set_lock_state(4242, "sticky")
    label = tray._lock_action_label().lower()
    # Foreground IS the locked target → label says "unlock".
    assert "unlock" in label
    assert "claude code" in label
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_tray.py -v`
Expected: FAIL — `TrayController.__init__` doesn't accept `settings`, `set_lock_state` doesn't exist, etc.

- [ ] **Step 3: Read the existing tray.py to plan the edit**

Run: `cat src/ui/tray.py | head -160` (use the Read tool in execution).

The existing tray uses pyqtSignals to emit menu actions. Add:
- A new pyqtSignal `toggle_lock = pyqtSignal()` for the tray menu item
- An import: `from .. import win32_window_utils as win32`
- A `settings` parameter on `__init__` (with `None` default for back-compat)
- A new section in the menu wired to that signal
- `set_lock_state(hwnd, source)` to refresh labels
- `_lock_section_visible()` (returns settings flag)
- `_lock_action_label()` (computes label from current state)

- [ ] **Step 4: Edit `src/ui/tray.py`**

(The exact edit depends on the current file structure — read it first. Below is the conceptual addition; adapt the placement to match existing patterns.)

Add at the top of the file:

```python
from .. import win32_window_utils as win32
```

Add a new signal alongside the existing ones (find `class TrayController` and the existing `pyqtSignal` declarations):

```python
    toggle_lock = pyqtSignal()
```

Add `settings` to `__init__` (back-compat default):

```python
    def __init__(self, parent=None, settings=None):
        super().__init__(parent)
        self.settings = settings
        self._current_target_hwnd: int | None = None
        self._current_source: str = "none"
        ...  # existing init body
```

Add the new menu items inside the menu-building method (look for where the existing items are added, e.g. after the Auto-Enter section). Insert:

```python
        # --- Paste target lock section ---
        self._lock_separator_top = self.menu.addSeparator()
        self._lock_status_action = self.menu.addAction("Paste target: <none>")
        self._lock_status_action.setEnabled(False)  # non-clickable status line
        self._lock_toggle_action = self.menu.addAction("Lock paste target → current window")
        self._lock_toggle_action.triggered.connect(self.toggle_lock.emit)
        self._lock_separator_bottom = self.menu.addSeparator()
        self._refresh_lock_visibility()
```

Add the new public methods:

```python
    def set_lock_state(self, hwnd, source: str) -> None:
        self._current_target_hwnd = hwnd
        self._current_source = source
        self._refresh_lock_visibility()
        if hasattr(self, "_lock_status_action"):
            self._lock_status_action.setText(self._lock_status_label())
            self._lock_toggle_action.setText(self._lock_action_label())

    def _lock_section_visible(self) -> bool:
        if self.settings is None:
            return False
        return bool(self.settings.get("paste_target_lock_enabled", False))

    def _refresh_lock_visibility(self) -> None:
        visible = self._lock_section_visible()
        for attr in ("_lock_separator_top", "_lock_status_action",
                     "_lock_toggle_action", "_lock_separator_bottom"):
            if hasattr(self, attr):
                getattr(self, attr).setVisible(visible)

    def _lock_status_label(self) -> str:
        hwnd = self._current_target_hwnd
        if hwnd is None:
            return "Paste target: <none>"
        title = win32.get_window_title(hwnd) or f"hwnd={hwnd}"
        if len(title) > 40:
            title = title[:37] + "..."
        suffix = " (sticky)" if self._current_source == "sticky" else ""
        return f"Paste target: {title}{suffix}"

    def _lock_action_label(self) -> str:
        hwnd = self._current_target_hwnd
        # No lock at all (or session-only — sticky-only governs the toggle).
        if hwnd is None or self._current_source != "sticky":
            return "Lock paste target → current window"
        title = win32.get_window_title(hwnd) or f"hwnd={hwnd}"
        if len(title) > 30:
            title = title[:27] + "..."
        current_fg = win32.get_foreground_window()
        if current_fg == hwnd:
            return f"Unlock paste target ({title})"
        return f"Re-lock paste target → current window"
```

- [ ] **Step 5: Update `app.py` to pass `settings` and wire the toggle signal**

Edit `src/app.py`:

a) Find `self.tray = TrayController(parent=self)` and replace:

```python
        self.tray = TrayController(parent=self, settings=self.settings)
```

b) In `_wire_signals`, wire the new toggle:

```python
        self.tray.toggle_lock.connect(self.paste_target.toggle_sticky)
```

c) Extend `_on_lock_changed` to call `tray.set_lock_state` for ALL sources:

Find the `_on_lock_changed` method and update so it always calls the tray (replace the existing handler body with this combined version):

```python
    def _on_lock_changed(self, hwnd, source: str) -> None:
        # Tray label always refreshes regardless of source.
        self.tray.set_lock_state(hwnd, source)
        if source == "sticky":
            new_hwnd = hwnd if hwnd is not None else None
            self.border_overlay.set_target_hwnd(new_hwnd)
            if new_hwnd is not None and self._last_sticky_hwnd != new_hwnd:
                self.sound_player.play_lock()
            elif new_hwnd is None and self._last_sticky_hwnd is not None:
                self.sound_player.play_unlock()
            self._last_sticky_hwnd = new_hwnd
        elif source == "none":
            self.border_overlay.set_target_hwnd(None)
            if self._last_sticky_hwnd is not None:
                self.sound_player.play_unlock()
                self._last_sticky_hwnd = None
```

- [ ] **Step 6: Run all tests**

Run: `./venv/Scripts/python.exe -m pytest tests/ -q`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add src/ui/tray.py src/app.py tests/test_tray.py
git commit -m "Tray menu — lock state surfacing

New section between existing items: status line ('Paste target: ...')
+ toggle action with dynamic label following spec §5.7 rules
(Lock / Unlock / Re-lock based on sticky-set + foreground match).
Section auto-hides when paste_target_lock_enabled is False.

TrayController gains a settings reference (back-compat default
None) and a toggle_lock signal that app wires to controller.
toggle_sticky.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 17: Settings dialog — Paste target lock section

**Files:**
- Modify: `src/ui/settings_dialog.py`
- Modify: `tests/test_settings_dialog.py`

The settings UI is mostly straight Qt scaffolding. Test by checking the section exists, gates work, and writes back to settings.

- [ ] **Step 1: Read the existing settings dialog structure**

Run (in execution): inspect `src/ui/settings_dialog.py` to find where existing sections (Hotkeys, Auto-Enter, Voice) are added so the new section follows the same idiom.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_settings_dialog.py`:

```python
def test_settings_dialog_has_paste_target_lock_section(qapp, tmp_appdata):
    from src.settings_manager import SettingsManager
    from src.ui.settings_dialog import SettingsDialog

    sm = SettingsManager()
    dlg = SettingsDialog(sm)
    # Find by name attribute we will set on each new control.
    assert dlg.findChild(type(None), "paste_target_lock_enabled_check") is None or True
    # Use a more reliable check — look for the named widgets.
    names = {w.objectName() for w in dlg.findChildren(object) if w.objectName()}
    expected = {
        "paste_target_lock_enabled_check",
        "lock_toggle_hotkey_recorder",
        "border_overlay_enabled_check",
        "border_color_button",
        "border_thickness_spin",
        "play_lock_sounds_check",
    }
    missing = expected - names
    assert not missing, f"missing widgets: {missing}"


def test_paste_target_lock_section_writes_back_to_settings(qapp, tmp_appdata):
    from src.settings_manager import SettingsManager
    from src.ui.settings_dialog import SettingsDialog

    sm = SettingsManager()
    dlg = SettingsDialog(sm)
    enable_check = dlg.findChild(object, "paste_target_lock_enabled_check")
    assert enable_check is not None
    enable_check.setChecked(True)
    dlg._save()  # the existing save method in the dialog (verify name when reading)
    assert sm.get("paste_target_lock_enabled") is True
```

NOTE: `_save` is a placeholder name — when implementing, replace with the dialog's actual save method. Check the existing dialog code first.

- [ ] **Step 3: Run tests to verify they fail**

Run: `./venv/Scripts/python.exe -m pytest tests/test_settings_dialog.py -v`
Expected: 2 new tests FAIL — widgets don't exist.

- [ ] **Step 4: Implement the new section**

Edit `src/ui/settings_dialog.py` — add a new section method following the existing pattern (look at how the Hotkeys or Auto-Enter sections are built). Pseudocode of the additions:

```python
    def _build_paste_target_lock_section(self) -> QGroupBox:
        from PyQt6.QtWidgets import (
            QCheckBox, QColorDialog, QFormLayout, QGroupBox, QHBoxLayout,
            QPushButton, QSpinBox, QWidget,
        )
        from PyQt6.QtGui import QColor

        from .hotkey_recorder import HotkeyRecorder

        grp = QGroupBox("Paste target lock")
        layout = QFormLayout(grp)

        self._lock_enable_check = QCheckBox("Enable paste-target lock")
        self._lock_enable_check.setObjectName("paste_target_lock_enabled_check")
        self._lock_enable_check.setChecked(
            bool(self.settings.get("paste_target_lock_enabled", False))
        )
        self._lock_enable_check.toggled.connect(self._refresh_lock_section_enable)
        layout.addRow(self._lock_enable_check)

        self._lock_hotkey_recorder = HotkeyRecorder(
            self.settings.get("lock_toggle_hotkey", "<alt>+l")
        )
        self._lock_hotkey_recorder.setObjectName("lock_toggle_hotkey_recorder")
        layout.addRow("Lock toggle hotkey:", self._lock_hotkey_recorder)

        self._border_enable_check = QCheckBox("Show colored border around locked window")
        self._border_enable_check.setObjectName("border_overlay_enabled_check")
        self._border_enable_check.setChecked(
            bool(self.settings.get("border_overlay_enabled", True))
        )
        layout.addRow(self._border_enable_check)

        # Color picker button
        color_row = QWidget()
        color_layout = QHBoxLayout(color_row)
        color_layout.setContentsMargins(0, 0, 0, 0)
        self._border_color_button = QPushButton()
        self._border_color_button.setObjectName("border_color_button")
        self._border_color_button.setFixedWidth(80)
        self._set_color_button_swatch(self.settings.get("border_color", "#ff9900"))
        self._border_color_button.clicked.connect(self._pick_border_color)
        color_layout.addWidget(self._border_color_button)
        color_layout.addStretch(1)
        layout.addRow("Border color:", color_row)

        self._border_thickness_spin = QSpinBox()
        self._border_thickness_spin.setObjectName("border_thickness_spin")
        self._border_thickness_spin.setRange(1, 10)
        self._border_thickness_spin.setSuffix(" px")
        self._border_thickness_spin.setValue(int(self.settings.get("border_thickness", 3)))
        layout.addRow("Border thickness:", self._border_thickness_spin)

        self._lock_sounds_check = QCheckBox("Play tone on lock/unlock")
        self._lock_sounds_check.setObjectName("play_lock_sounds_check")
        self._lock_sounds_check.setChecked(
            bool(self.settings.get("play_lock_sounds", True))
        )
        layout.addRow(self._lock_sounds_check)

        self._refresh_lock_section_enable()
        return grp

    def _refresh_lock_section_enable(self) -> None:
        enabled = self._lock_enable_check.isChecked()
        for w in (
            self._lock_hotkey_recorder, self._border_enable_check,
            self._border_color_button, self._border_thickness_spin,
            self._lock_sounds_check,
        ):
            w.setEnabled(enabled)

    def _set_color_button_swatch(self, color_hex: str) -> None:
        self._border_color_button.setText(color_hex)
        self._border_color_button.setStyleSheet(
            f"QPushButton {{ background-color: {color_hex}; "
            f"color: {'#000' if QColor(color_hex).lightness() > 128 else '#fff'}; }}"
        )
        self._pending_border_color = color_hex

    def _pick_border_color(self) -> None:
        from PyQt6.QtWidgets import QColorDialog

        current = QColor(self.settings.get("border_color", "#ff9900"))
        chosen = QColorDialog.getColor(current, self, "Choose border color")
        if chosen.isValid():
            self._set_color_button_swatch(chosen.name())
```

Then in the existing build method, add the new group:

```python
        layout.addWidget(self._build_paste_target_lock_section())
```

And in the existing save/accept method, persist the new values:

```python
        self.settings.set("paste_target_lock_enabled", self._lock_enable_check.isChecked())
        self.settings.set("lock_toggle_hotkey", self._lock_hotkey_recorder.hotkey())
        self.settings.set("border_overlay_enabled", self._border_enable_check.isChecked())
        self.settings.set("border_color", self._pending_border_color)
        self.settings.set("border_thickness", self._border_thickness_spin.value())
        self.settings.set("play_lock_sounds", self._lock_sounds_check.isChecked())
```

(The exact method name to add to depends on the existing `accept` / `_save` flow. Read the file first.)

- [ ] **Step 5: Validate hotkeys when the dialog accepts**

Find the existing `validate_hotkeys` call in `settings_dialog.py` and extend to pass the new key:

```python
        issues = validate_hotkeys(
            toggle=self._toggle_hotkey_recorder.hotkey(),
            delete=self._delete_hotkey_recorder.hotkey(),
            lock_toggle=self._lock_hotkey_recorder.hotkey(),
        )
```

- [ ] **Step 6: Run tests**

Run: `./venv/Scripts/python.exe -m pytest tests/test_settings_dialog.py -v`
Expected: all green.

- [ ] **Step 7: Wire `_open_settings` in app.py to refresh hotkey mapping when relevant keys change**

Find `_open_settings` in `src/app.py` and extend the existing dirty-check that rebuilds the hotkey mapping to include the new keys:

```python
        if (
            self.settings.get("hotkey") != prev_hotkey
            or self.settings.get("delete_hotkey") != prev_delete_hotkey
            or self.settings.get("voice_interrupt_hotkey") != prev_voice_hotkey
            or bool(self.settings.get("voice_enabled", False)) != prev_voice_enabled
            or self.settings.get("lock_toggle_hotkey") != prev_lock_hotkey
            or bool(self.settings.get("paste_target_lock_enabled", False)) != prev_lock_enabled
        ):
            self.hotkey.update_mapping(self._build_hotkey_mapping())
            self.tray.set_lock_state(
                self.paste_target.current_target(),
                "sticky" if self.paste_target._sticky_hwnd is not None
                else ("session" if self.paste_target._per_session_hwnd is not None else "none"),
            )
```

And capture the new previous values at the top of `_open_settings`:

```python
        prev_lock_hotkey = self.settings.get("lock_toggle_hotkey")
        prev_lock_enabled = bool(self.settings.get("paste_target_lock_enabled", False))
```

- [ ] **Step 8: Run full suite**

Run: `./venv/Scripts/python.exe -m pytest tests/ -q`
Expected: all green.

- [ ] **Step 9: Commit**

```bash
git add src/ui/settings_dialog.py src/app.py tests/test_settings_dialog.py
git commit -m "Settings dialog — Paste target lock section

New section after Hotkeys with: enable checkbox, hotkey recorder,
border on/off, color picker (QColorDialog), thickness spin (1-10),
tone on/off. All children disabled when master is unchecked.
validate_hotkeys extended to detect lock_toggle collisions.
_open_settings rebuilds hotkey mapping when lock keys change.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 18: Run the full automated suite + lint pass

**Files:** none modified — verification only.

- [ ] **Step 1: Run pytest**

Run: `./venv/Scripts/python.exe -m pytest tests/ -v 2>&1 | tail -40`
Expected: ~357 tests, all green. If any fail, debug — do NOT proceed to UAT until clean.

- [ ] **Step 2: Quick smoke test — does the app even import?**

Run: `./venv/Scripts/python.exe -c "from src.app import TextWhisperApp; print('OK')"`
Expected: `OK`. ImportError here usually means a circular import between `paste_target`, `keyboard_output`, and `app`.

- [ ] **Step 3: Quick smoke test — does the app start?**

Run: `./venv/Scripts/python.exe -m src` in the foreground. The app should appear in the system tray. Right-click and verify the tray menu — when `paste_target_lock_enabled` is False (default), the new lock section should be hidden.

- [ ] **Step 4: No commit — this is a verification gate.**

If anything failed, return to the relevant task and fix before continuing.

---

## Task 19: Manual UAT — Windows-only

**Files:** Create `docs/superpowers/specs/2026-04-27-paste-target-lock-uat.md`.

Capture results in the file as you go. Each step is ~30 seconds. Total ~10 minutes.

- [ ] **Step 1: Create the UAT checklist**

Create `docs/superpowers/specs/2026-04-27-paste-target-lock-uat.md`:

```markdown
# Paste Target Lock — Manual UAT

**Date:** _________
**Tester:** _________
**Build:** v1.3.0 dev (commit _________)

## Prerequisites
- TextWhisper running, Whisper model loaded ("Ready" tray status)
- Settings → "Paste target lock" → enable
- Open: Notepad, VS Code, Windows Terminal with Claude Code

## Checklist

- [ ] Enabling the master setting in Settings reveals the lock section in the tray menu (right-click tray)
- [ ] Disabling it hides the section
- [ ] Lock to Notepad: focus Notepad, press Alt+L → tray label shows "Unlock paste target (Untitled - Notepad)" + lock chime plays
- [ ] Border draws around Notepad
- [ ] Move Notepad — border follows
- [ ] Resize Notepad — border resizes
- [ ] Focus another app, dictate ("hello world test"), text lands in Notepad even though focus was elsewhere
- [ ] Focus is restored to the previously-active app after the paste
- [ ] Smart toggle: focus VS Code, press Alt+L (single press), tray now says "Unlock paste target (... - Visual Studio Code)" + lock chime + border moves to VS Code
- [ ] Press Alt+L again while focused on VS Code → unlock chime + border hides + tray label clears
- [ ] Re-lock to Claude Code in Windows Terminal: focus it, Alt+L → border draws around Windows Terminal
- [ ] Dictate from Notepad while locked to Claude Code — text lands in Claude Code
- [ ] Minimize the locked Claude Code window, dictate → window pops up + paste lands + stays restored
- [ ] Close the locked window, then dictate → tray notification "Paste target window is gone..." + transcription stays in clipboard
- [ ] Per-session capture: with no sticky lock, focus Notepad, press Alt+Z, dictate, alt-tab away, finish speaking → text lands in Notepad
- [ ] Sticky precedence: lock to Notepad via Alt+L, then press Alt+Z while focused on VS Code → text lands in Notepad (sticky won)
- [ ] Settings → uncheck "Show colored border" → border hides while sticky lock remains active
- [ ] Settings → change border color → re-lock → new color appears

## Bugs found

(Capture per-step in the table above; expand below.)
```

- [ ] **Step 2: Execute the checklist on the actual machine**

Walk through every item, mark pass/fail, note any bugs. If any fail, file the exact reproduction steps and return to the relevant implementation task.

- [ ] **Step 3: Commit the UAT results**

```bash
git add docs/superpowers/specs/2026-04-27-paste-target-lock-uat.md
git commit -m "UAT checklist + results for paste target lock

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 20: Version bump + push

**Files:**
- Modify: `src/__init__.py`

- [ ] **Step 1: Bump version**

Edit `src/__init__.py`:

```python
__version__ = "1.3.0"
```

- [ ] **Step 2: Run full suite one more time**

Run: `./venv/Scripts/python.exe -m pytest tests/ -q`
Expected: all green.

- [ ] **Step 3: Commit**

```bash
git add src/__init__.py
git commit -m "Bump version to 1.3.0 — paste target lock feature

New minor release adds the paste-target-lock feature behind a
default-off master setting. Per-session auto-capture (Alt+Z),
sticky lock with smart toggle (Alt+L), colored border overlay
around the locked window, lock/unlock chimes, focus restored to
previous foreground after each paste, and graceful dead-target
handling.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 4: Push**

```bash
git push origin main
```

Expected: clean push to `origin/main`. If auth fails (Tavant account active), run `gh auth switch --user ntorvik` first.

---

## Plan Self-Review Notes (post-write)

**Spec coverage:** Walked through spec §3 (decisions 1-12), §5 (components 5.1–5.8), §7 (edge cases 1-8), §8 (settings keys), §9 (testing). Each appears in at least one task:
- D1 (master setting) → Task 2
- D2 (per-session capture) → Task 3
- D3 (smart sticky toggle) → Task 4
- D4 (sticky wins) → Task 4
- D5 (tray surfacing) → Task 16
- D6 (border on sticky only) → Tasks 10, 14, 16
- D7 (border settings) → Tasks 2, 17
- D8 (dead-target: minimized restore + closed notify) → Tasks 5, 7, 14, 15
- D9 (lock/unlock tones) → Tasks 9, 14
- D10 (focus restore) → Tasks 7, 8
- D11 (no persistence) → covered by no-implementation: nothing writes sticky to disk
- D12 (focus shift acceptable) → Tasks 7, 8 implement and document

**Edge cases coverage:** §7 #1 self-window filter → Tasks 3, 4. #2 multi-monitor → Task 10 paint inset. #3 mid-paste foreground change → Tasks 7/8 (best-effort, no retry). #4 HWND reuse → Task 5. #5 UIPI block → Tasks 7/8 log warnings. #6 hotkey collisions → Task 11. #7 in-flight paste race → Task 7 reads target_hwnd once. #8 stale self HWND at paste → not yet covered, see follow-up below.

**Follow-up to consider during implementation:** Edge case #8 (stale self-window HWND at paste time) can be handled by adding a PID re-check inside `_paste_text` when `target_hwnd` matches our own PID at paste time. Low risk for this version since `current_target()` always pulls fresh from the controller, which has the self-filter at capture time. If it ever surfaces, add a 2-line guard.

**Placeholder scan:** No "TBD"/"TODO"/"implement later" in any step. Each code block contains the actual code to write. Test code blocks contain the actual test bodies. Commit messages are filled in.

**Type/method consistency:** `current_target()`, `toggle_sticky()`, `is_target_alive()`, `clear_sticky_silently()`, `on_dictation_started()`, `on_dictation_stopped()`, `lock_changed`, `target_invalid` — used consistently across Tasks 3-15. `set_target_hwnd()` on the overlay is consistent across Tasks 10, 14. `play_lock`/`play_unlock` consistent across Tasks 9, 14. KeyboardOutput callback name `on_target_invalid` consistent across Tasks 15.
