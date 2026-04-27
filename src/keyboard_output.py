"""Type or paste transcribed text into the currently focused window."""

from __future__ import annotations

import logging
import sys
import threading
import time

from pynput.keyboard import Controller, Key

log = logging.getLogger(__name__)


# Whitespace chars get sent as their dedicated virtual-key event rather than
# as a Unicode codepoint. Some terminal apps (Windows Terminal, Claude Code,
# IDE consoles) silently drop bare Unicode spaces injected via the type()
# fast-path, but reliably handle real Key.space / Key.enter / Key.tab events.
_VK_FOR_WHITESPACE: dict[str, Key] = {
    " ": Key.space,
    "\n": Key.enter,
    "\t": Key.tab,
}


# Win32 virtual-key codes for modifiers we never want to be pressed when we
# inject Ctrl+V. ALT, SHIFT, the two WIN keys. Polled via GetAsyncKeyState.
_MODIFIER_VKS: tuple[int, ...] = (0x12, 0x10, 0x5B, 0x5C)
_KEY_DOWN_BIT = 0x8000


def _user_modifier_held() -> bool:
    """True iff the user is physically holding Alt/Shift/Win right now.

    Returns False on non-Windows platforms (the modifier-residue race this
    guards against is a Windows SendInput timing issue; other backends can
    extend if needed).
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        get_state = ctypes.windll.user32.GetAsyncKeyState
        return any(get_state(vk) & _KEY_DOWN_BIT for vk in _MODIFIER_VKS)
    except Exception:
        # If we can't query, assume clean — the worst case is the existing
        # buggy behavior, not a regression.
        return False


def _wait_for_user_modifier_release(timeout_ms: int, poll_ms: int = 10) -> bool:
    """Block up to ``timeout_ms`` for the user to release Alt/Shift/Win.

    Returns True if all are released within the window, False on timeout.
    Caller can proceed regardless — the wait is best-effort.
    """
    if timeout_ms <= 0 or not _user_modifier_held():
        return True
    deadline = time.monotonic() + (timeout_ms / 1000.0)
    sleep_s = max(0.001, poll_ms / 1000.0)
    while time.monotonic() < deadline:
        time.sleep(sleep_s)
        if not _user_modifier_held():
            return True
    return False


class KeyboardOutput:
    def __init__(self, settings, on_target_invalid=None) -> None:
        self.settings = settings
        self._kb = Controller()
        self._lock = threading.Lock()
        # Optional callback invoked with a reason string when a target_hwnd
        # paste/type fails because the target is closed (or PID-drifted).
        # The app uses this to surface a tray notification and clear the
        # sticky lock without coupling KeyboardOutput to Qt signals.
        self._on_target_invalid = on_target_invalid

    # ------------------------------------------------------------------
    # Public typing entry point
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Mode 1: char-by-char typing
    # ------------------------------------------------------------------

    def _type_text(self, text: str, target_hwnd: int | None = None) -> int:
        from . import win32_window_utils as w

        delay = max(0, int(self.settings.get("type_delay_ms", 4))) / 1000.0
        trailing_space = bool(self.settings.get("trailing_space", True))

        prev_fg: int | None = None
        if target_hwnd is not None:
            if not w.is_window(target_hwnd):
                log.warning("Type target hwnd=%s is closed; skipping.", target_hwnd)
                if self._on_target_invalid:
                    self._on_target_invalid("closed")
                return 0
            prev_fg = w.get_foreground_window() or None
            if w.is_iconic(target_hwnd):
                w.restore_window(target_hwnd)
            ok = w.set_foreground_with_attach(target_hwnd)
            if not ok:
                log.warning(
                    "set_foreground_with_attach refused for hwnd=%s "
                    "(focus-stealing protection or UIPI block); typing anyway.",
                    target_hwnd,
                )
            focus_settle = max(0, int(self.settings.get("paste_lock_focus_settle_ms", 50))) / 1000.0
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

    def _tap_char(self, ch: str) -> None:
        """Emit a single character. Whitespace goes via dedicated VK events."""
        vk = _VK_FOR_WHITESPACE.get(ch)
        if vk is not None:
            self._kb.press(vk)
            self._kb.release(vk)
        else:
            self._kb.type(ch)

    # ------------------------------------------------------------------
    # Mode 2: clipboard paste
    # ------------------------------------------------------------------

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
                if self._on_target_invalid:
                    self._on_target_invalid("closed")
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
            focus_settle = max(0, int(self.settings.get("paste_lock_focus_settle_ms", 50))) / 1000.0
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

    # ------------------------------------------------------------------
    # Deletion (unchanged)
    # ------------------------------------------------------------------

    def delete_word(self) -> None:
        with self._lock:
            try:
                self._kb.press(Key.ctrl)
                self._kb.press(Key.backspace)
                self._kb.release(Key.backspace)
                self._kb.release(Key.ctrl)
                log.info("Sent Ctrl+Backspace (delete previous word).")
            except Exception:
                log.exception("delete_word failed")

    def send_enter(self) -> None:
        """Send a single Enter keystroke (used by the auto-Enter feature)."""
        with self._lock:
            try:
                self._kb.press(Key.enter)
                self._kb.release(Key.enter)
                log.info("Sent Enter (auto-Enter).")
            except Exception:
                log.exception("send_enter failed")

    def replace_last_period_with_comma(self, had_trailing_space: bool) -> None:
        """Backspace over the trailing '.' (and optional space) and emit ','.

        Used by the continuation-detection feature: Whisper transcribes each
        VAD-cut segment in isolation and reflexively ends each one with a
        period, even when the user was just taking a breath mid-sentence.
        When the user resumes speaking within the continuation window, we
        retroactively demote that period to a comma so the result reads as
        one flowing sentence.
        """
        n = 2 if had_trailing_space else 1
        with self._lock:
            try:
                for _ in range(n):
                    self._kb.press(Key.backspace)
                    self._kb.release(Key.backspace)
                self._tap_char(",")
                if had_trailing_space:
                    self._tap_char(" ")
                log.info(
                    "Replaced trailing %r with %r (continuation, trailing_space=%s).",
                    ". " if had_trailing_space else ".",
                    ", " if had_trailing_space else ",",
                    had_trailing_space,
                )
            except Exception:
                log.exception("replace_last_period_with_comma failed")

    def delete_chars(self, count: int) -> None:
        if count <= 0:
            return
        delay = max(0, int(self.settings.get("type_delay_ms", 4))) / 1000.0
        with self._lock:
            try:
                for _ in range(count):
                    self._kb.press(Key.backspace)
                    self._kb.release(Key.backspace)
                    if delay > 0:
                        time.sleep(delay)
                log.info("Sent %d backspaces.", count)
            except Exception:
                log.exception("delete_chars failed (count=%d)", count)
