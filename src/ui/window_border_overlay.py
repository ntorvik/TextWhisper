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
        if not bool(self.settings.get("paste_lock_border_enabled", True)):
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
        color_hex = str(self.settings.get("paste_lock_border_color", "#ff9900"))
        thickness = max(1, int(self.settings.get("paste_lock_border_thickness", 3)))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        pen = QPen(QColor(color_hex), thickness)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        painter.setPen(pen)
        inset = thickness // 2
        rect = self.rect().adjusted(inset, inset, -inset, -inset)
        painter.drawRect(rect)
