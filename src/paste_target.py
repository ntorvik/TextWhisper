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
        return bool(self.settings.get("paste_lock_enabled", False))

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

    # ---- target resolution -------------------------------------------

    def current_target(self) -> int | None:
        return self._sticky_hwnd if self._sticky_hwnd is not None else self._per_session_hwnd

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
        """Drop the sticky lock for app's target_invalid handler.

        The tray notification is the user feedback, so no extra tone is
        played here. The lock_changed signal still fires (with source
        "none") so the border overlay and tray label refresh; subscribers
        that play tones must check the previous state, which the app
        handler tracks separately.
        """
        if self._sticky_hwnd is not None:
            self._sticky_hwnd = None
            self._sticky_pid = None
            self._emit_lock_changed()

    # ---- internal -----------------------------------------------------

    def _emit_lock_changed(self) -> None:
        if self._sticky_hwnd is not None:
            self.lock_changed.emit(self._sticky_hwnd, "sticky")
        elif self._per_session_hwnd is not None:
            self.lock_changed.emit(self._per_session_hwnd, "session")
        else:
            self.lock_changed.emit(None, "none")
