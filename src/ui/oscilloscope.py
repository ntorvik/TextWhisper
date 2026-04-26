"""Floating, frameless, always-on-top oscilloscope widget.

Supports:
  - drag to move (anywhere in the body)
  - drag near right / bottom edges (or bottom-right corner) to resize
  - configurable opacity, colors, and background alpha
  - position + size persisted via SettingsManager
  - the widget's actual shape (not just the painted shape) conforms to the
    chosen rounded/pill/rect outline via QRegion masking + Win11 DWM
    corner-preference override.
"""

from __future__ import annotations

import ctypes
import logging
import sys

import numpy as np
from PyQt6.QtCore import QPoint, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygon, QRegion
from PyQt6.QtWidgets import QApplication, QWidget

log = logging.getLogger(__name__)


# Windows 11 DWM constants for disabling system-level corner rounding.
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWCP_DEFAULT = 0
_DWMWCP_DONOTROUND = 1
_DWMWCP_ROUND = 2
_DWMWCP_ROUNDSMALL = 3

# Win32 SetWindowPos constants (used to re-assert HWND_TOPMOST z-order).
_HWND_TOPMOST = -1
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010
_SWP_SHOWWINDOW = 0x0040

RESIZE_MARGIN = 7
MIN_W = 120
MIN_H = 24
MAX_W = 2000
MAX_H = 400


class OscilloscopeWidget(QWidget):
    BUFFER_SECONDS = 2.0
    SAMPLE_RATE = 16000

    def __init__(self, settings) -> None:
        super().__init__(None)
        self.settings = settings

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)
        self.setWindowTitle("TextWhisper Oscilloscope")
        self.setMinimumSize(MIN_W, MIN_H)
        self.setMaximumSize(MAX_W, MAX_H)

        w = max(MIN_W, int(self.settings.get("oscilloscope.width", 320)))
        h = max(MIN_H, int(self.settings.get("oscilloscope.height", 48)))
        self.resize(w, h)
        self.apply_opacity()

        self._buffer_size = int(self.BUFFER_SECONDS * self.SAMPLE_RATE)
        self._buffer = np.zeros(self._buffer_size, dtype=np.float32)
        self._active = False
        # Smoothed spectrum-band magnitudes for the spectrum visualisation.
        # Shape grows to match the configured bar count on first frame.
        self._spectrum_smoothed: np.ndarray | None = None

        self._drag_offset: QPoint | None = None
        self._resize_edge: str | None = None
        self._resize_start_pos: QPoint | None = None
        self._resize_start_size: QSize | None = None
        self._resize_start_topleft: QPoint | None = None

        self._restore_position()

        self._timer = QTimer(self)
        self._timer.setInterval(33)  # ~30 fps
        self._timer.timeout.connect(self.update)
        self._timer.start()

        # Re-assert topmost z-order every 2 s. Windows can demote
        # WindowStaysOnTopHint widgets behind the taskbar when the user
        # clicks on the desktop or the taskbar gains focus. This timer
        # restores HWND_TOPMOST without stealing focus.
        self._zorder_timer = QTimer(self)
        self._zorder_timer.setInterval(2000)
        self._zorder_timer.timeout.connect(self._enforce_topmost)
        self._zorder_timer.start()

        self._update_shape_mask()

    # --- public API ----------------------------------------------------

    def push_audio(self, chunk: np.ndarray) -> None:
        n = len(chunk)
        if n <= 0:
            return
        if n >= self._buffer_size:
            self._buffer[:] = chunk[-self._buffer_size:]
        else:
            self._buffer = np.roll(self._buffer, -n)
            self._buffer[-n:] = chunk

    def clear(self) -> None:
        self._buffer.fill(0.0)
        self.update()

    def set_active(self, active: bool) -> None:
        self._active = active
        self.update()

    def apply_color_settings(self) -> None:
        self.update()

    def apply_shape_settings(self) -> None:
        """Re-apply the window mask so the widget itself takes on the new shape."""
        self._update_shape_mask()
        self.update()

    def apply_opacity(self) -> None:
        opacity = float(self.settings.get("oscilloscope.opacity", 0.85))
        opacity = max(0.15, min(1.0, opacity))
        self.setWindowOpacity(opacity)

    def apply_size_from_settings(self) -> None:
        w = max(MIN_W, int(self.settings.get("oscilloscope.width", 320)))
        h = max(MIN_H, int(self.settings.get("oscilloscope.height", 48)))
        if (w, h) != (self.width(), self.height()):
            self.resize(w, h)

    # --- shape masking + Windows-11 corner override --------------------

    def _update_shape_mask(self) -> None:
        """Make the widget's actual region match the chosen shape.

        Without this, Windows 11 DWM draws its own subtle rounded corners on
        every top-level window and our shape only appears as a paint inside
        the OS-imposed rounded rectangle. We:
          1. Tell DWM not to round our window (DWMWCP_DONOTROUND)
          2. Apply a QRegion mask so input/visibility match the shape exactly.
        """
        self._set_dwm_corner_preference(_DWMWCP_DONOTROUND)

        shape = str(self.settings.get("oscilloscope.shape", "rounded")).lower()
        radius = self._shape_radius(shape)
        if radius <= 0.5:
            self.clearMask()
            return

        # Build a polygon approximation of the rounded rect via QPainterPath.
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), radius, radius)
        polygon: QPolygon = path.toFillPolygon().toPolygon()
        self.setMask(QRegion(polygon))

    def _set_dwm_corner_preference(self, preference: int) -> None:
        """Disable (or set) Win11 DWM corner rounding for this window."""
        if sys.platform != "win32":
            return
        try:
            hwnd = int(self.winId())
            if hwnd == 0:
                return
            value = ctypes.c_int(preference)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd),
                ctypes.c_uint(_DWMWA_WINDOW_CORNER_PREFERENCE),
                ctypes.byref(value),
                ctypes.sizeof(value),
            )
        except (OSError, AttributeError):
            # DWM API unavailable (older Windows, or running under WSLg etc.)
            log.debug("DwmSetWindowAttribute unavailable — falling back to mask only.")

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().resizeEvent(event)
        self._update_shape_mask()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().showEvent(event)
        # winId() is only valid once the window has been created; calling
        # _update_shape_mask here ensures DWM gets the corner override even
        # on first show.
        self._update_shape_mask()
        self._enforce_topmost()

    # --- topmost z-order enforcement -----------------------------------

    def _enforce_topmost(self) -> None:
        """Re-assert that this widget sits in the always-on-top z-order layer.

        On Windows, ``Qt.WindowType.WindowStaysOnTopHint`` is honored at
        creation time but can be quietly demoted when the desktop or taskbar
        receives focus. We re-issue ``SetWindowPos(HWND_TOPMOST, ...)`` on a
        timer with the no-activate flag so the widget never grabs focus.
        On other platforms we fall back to ``QWidget.raise_()``.
        """
        if not self.isVisible():
            return
        if sys.platform == "win32":
            try:
                hwnd = int(self.winId())
                if hwnd == 0:
                    return
                ctypes.windll.user32.SetWindowPos(
                    ctypes.c_void_p(hwnd),
                    ctypes.c_void_p(_HWND_TOPMOST),
                    0, 0, 0, 0,
                    _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE,
                )
            except (OSError, AttributeError):
                # user32 unavailable for some reason — fall back.
                self.raise_()
        else:
            self.raise_()

    # --- positioning ---------------------------------------------------

    def _restore_position(self) -> None:
        x = self.settings.get("oscilloscope.x")
        y = self.settings.get("oscilloscope.y")
        screen = QApplication.primaryScreen().availableGeometry()
        if x is None or y is None:
            x = (screen.width() - self.width()) // 2 + screen.x()
            y = screen.y() + screen.height() - self.height() - 60
        x = max(screen.x(), min(int(x), screen.x() + screen.width() - self.width()))
        y = max(screen.y(), min(int(y), screen.y() + screen.height() - self.height()))
        self.move(x, y)

    # --- painting ------------------------------------------------------

    def _accent_color(self) -> QColor:
        key = "oscilloscope.color_active" if self._active else "oscilloscope.color_idle"
        default = "#40dc8c" if self._active else "#7884a0"
        c = QColor(str(self.settings.get(key, default)))
        if not c.isValid():
            c = QColor(default)
        return c

    def _shape_radius(self, shape: str) -> float:
        if shape == "pill":
            return self.height() / 2.0
        if shape == "rect":
            return 0.0
        return 10.0

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        shape = str(self.settings.get("oscilloscope.shape", "rounded")).lower()
        radius = self._shape_radius(shape)

        # Background.
        bg_alpha = int(self.settings.get("oscilloscope.background_alpha", 130))
        bg_alpha = max(0, min(255, bg_alpha))
        painter.setBrush(QColor(14, 16, 22, bg_alpha))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(self.rect(), radius, radius)

        # Clip every subsequent draw (waveform/spectrum/grip) to the same
        # rounded outline, so picking pill / rect / rounded visibly affects
        # the bars too — not just the (often nearly transparent) backing.
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()), radius, radius)
        painter.setClipPath(clip)

        accent = self._accent_color()
        style = str(self.settings.get("oscilloscope.style", "waveform")).lower()
        if style == "spectrum":
            self._paint_spectrum(painter, accent)
        else:
            self._paint_waveform(painter, accent, shape, radius)

        # Subtle resize affordance in bottom-right (drawn after clipping so
        # it's also constrained to the shape).
        grip = QColor(accent.red(), accent.green(), accent.blue(), 110)
        painter.setPen(QPen(grip, 1))
        for i in range(3):
            off = 3 + i * 3
            painter.drawLine(
                self.width() - off,
                self.height() - 3,
                self.width() - 3,
                self.height() - off,
            )

    # --- visualization: classic scrolling waveform ---------------------

    def _paint_waveform(self, painter: QPainter, accent: QColor, shape: str, radius: float) -> None:
        guide = QColor(accent.red(), accent.green(), accent.blue(), 48)
        painter.setPen(QPen(guide, 1))
        mid_y = self.height() / 2
        pad_x = int(radius * 0.8) if shape == "pill" else 8
        pad_x = max(6, min(pad_x, self.width() // 3))
        painter.drawLine(pad_x, int(mid_y), self.width() - pad_x, int(mid_y))
        draw_w = max(1, self.width() - 2 * pad_x)
        amp = (self.height() / 2) * 0.85
        n = len(self._buffer)
        cols = draw_w
        samples_per_col = max(1, n // cols)

        pen = QPen(accent, 1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)

        for x in range(cols):
            i0 = x * samples_per_col
            i1 = min(n, i0 + samples_per_col)
            if i0 >= i1:
                continue
            chunk = self._buffer[i0:i1]
            v = float(np.max(np.abs(chunk)))
            v = min(1.0, v * 4.0)
            if v < 0.005:
                continue
            bar_h = max(1.0, v * amp)
            cx = x + pad_x
            painter.drawLine(cx, int(mid_y - bar_h), cx, int(mid_y + bar_h))

    # --- visualization: spectrum analyzer (FFT bars) -------------------

    def _paint_spectrum(self, painter: QPainter, accent: QColor) -> None:
        n_bars = max(8, min(int(self.settings.get("oscilloscope.spectrum_bars", 36)), 200))
        bands = self._compute_spectrum_bands(n_bars)

        guide = QColor(accent.red(), accent.green(), accent.blue(), 36)
        painter.setPen(QPen(guide, 1))
        mid_y = self.height() / 2
        pad_x = max(6, min(self.height() // 2, self.width() // 4))
        painter.drawLine(pad_x, int(mid_y), self.width() - pad_x, int(mid_y))

        draw_w = max(1, self.width() - 2 * pad_x)
        amp = (self.height() / 2) * 0.9

        pen = QPen(accent, 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)

        # Distribute bars evenly across the drawable width.
        for i, mag in enumerate(bands):
            v = float(min(1.0, mag))
            if v < 0.01:
                continue
            bar_h = max(1.0, v * amp)
            cx = pad_x + (i + 0.5) * draw_w / n_bars
            painter.drawLine(int(cx), int(mid_y - bar_h), int(cx), int(mid_y + bar_h))

    def _compute_spectrum_bands(self, n_bars: int) -> np.ndarray:
        """FFT the most-recent ~64 ms of audio and aggregate into ``n_bars`` bands.

        Uses log-spaced band edges across the speech-relevant frequency range
        (~80 Hz to ~7 kHz) and an asymmetric envelope follower (fast attack,
        slow decay) so bars settle visibly per band rather than flickering.
        """
        n_fft = 1024
        if len(self._buffer) >= n_fft:
            window_samples = self._buffer[-n_fft:]
        else:
            window_samples = self._buffer
            n_fft = max(64, len(window_samples))

        if n_fft < 64:
            return np.zeros(n_bars, dtype=np.float32)

        # Hann window to reduce spectral leakage.
        window = np.hanning(n_fft).astype(np.float32)
        windowed = window_samples[-n_fft:] * window

        # Real FFT magnitude.
        spectrum = np.abs(np.fft.rfft(windowed)).astype(np.float32) / (n_fft / 2)

        # Map FFT bins -> log-spaced bands across speech range.
        sr = self.SAMPLE_RATE
        bin_hz = sr / n_fft
        fmin, fmax = 80.0, 7000.0
        # Compute band-edge bin indices.
        edges = np.geomspace(fmin, fmax, n_bars + 1)
        bin_edges = np.clip((edges / bin_hz).astype(int), 1, len(spectrum) - 1)

        bands = np.zeros(n_bars, dtype=np.float32)
        for i in range(n_bars):
            lo, hi = bin_edges[i], max(bin_edges[i] + 1, bin_edges[i + 1])
            bands[i] = float(np.mean(spectrum[lo:hi]) if hi > lo else spectrum[lo])

        # Perceptual scaling — sqrt is a cheap stand-in for log.
        bands = np.sqrt(bands * 8.0)
        # Clip into [0, 1.5] so very loud bursts stay visible without clipping.
        bands = np.clip(bands, 0.0, 1.5)

        # Asymmetric smoothing: fast attack, slow decay.
        if self._spectrum_smoothed is None or len(self._spectrum_smoothed) != n_bars:
            self._spectrum_smoothed = np.zeros(n_bars, dtype=np.float32)
        prev = self._spectrum_smoothed
        new = np.where(bands > prev, bands, prev * 0.86 + bands * 0.14)
        self._spectrum_smoothed = new
        return new

    # --- mouse: move + resize -----------------------------------------

    def _hit_edge(self, pos: QPoint) -> str | None:
        right = pos.x() >= self.width() - RESIZE_MARGIN
        bottom = pos.y() >= self.height() - RESIZE_MARGIN
        if right and bottom:
            return "br"
        if right:
            return "r"
        if bottom:
            return "b"
        return None

    def _cursor_for_edge(self, edge: str | None) -> Qt.CursorShape:
        if edge == "br":
            return Qt.CursorShape.SizeFDiagCursor
        if edge == "r":
            return Qt.CursorShape.SizeHorCursor
        if edge == "b":
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.SizeAllCursor

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position().toPoint()
        edge = self._hit_edge(pos)
        if edge is not None:
            self._resize_edge = edge
            self._resize_start_pos = event.globalPosition().toPoint()
            self._resize_start_size = self.size()
            self._resize_start_topleft = self.frameGeometry().topLeft()
        else:
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._resize_edge is not None and self._resize_start_pos is not None:
            delta = event.globalPosition().toPoint() - self._resize_start_pos
            new_w = self._resize_start_size.width()
            new_h = self._resize_start_size.height()
            if "r" in self._resize_edge:
                new_w = self._resize_start_size.width() + delta.x()
            if "b" in self._resize_edge:
                new_h = self._resize_start_size.height() + delta.y()
            new_w = max(MIN_W, min(MAX_W, new_w))
            new_h = max(MIN_H, min(MAX_H, new_h))
            self.resize(new_w, new_h)
            event.accept()
            return
        if self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        # Hover — update cursor based on edge proximity.
        edge = self._hit_edge(event.position().toPoint())
        self.setCursor(self._cursor_for_edge(edge))

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._resize_edge is not None:
            self._resize_edge = None
            self._resize_start_pos = None
            self._resize_start_size = None
            self._resize_start_topleft = None
            self.settings.set("oscilloscope.width", int(self.width()))
            self.settings.set("oscilloscope.height", int(self.height()))
            event.accept()
            return
        if self._drag_offset is not None:
            self._drag_offset = None
            self.settings.set("oscilloscope.x", int(self.x()))
            self.settings.set("oscilloscope.y", int(self.y()))
            event.accept()

    def leaveEvent(self, _event) -> None:
        if self._resize_edge is None and self._drag_offset is None:
            self.setCursor(Qt.CursorShape.SizeAllCursor)
