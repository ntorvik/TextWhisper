"""Tests for the OscilloscopeWidget."""

from __future__ import annotations

import numpy as np

from src.settings_manager import SettingsManager
from src.ui.oscilloscope import OscilloscopeWidget


def _widget(tmp_appdata) -> OscilloscopeWidget:
    return OscilloscopeWidget(SettingsManager())


def test_buffer_starts_silent(tmp_appdata, qapp):
    w = _widget(tmp_appdata)
    assert np.all(w._buffer == 0.0)
    assert len(w._buffer) == int(w.BUFFER_SECONDS * w.SAMPLE_RATE)


def test_push_audio_rolls_buffer(tmp_appdata, qapp):
    w = _widget(tmp_appdata)
    n = 480
    chunk = np.full(n, 0.5, dtype=np.float32)
    w.push_audio(chunk)
    assert np.allclose(w._buffer[-n:], 0.5)
    assert np.allclose(w._buffer[:-n], 0.0)


def test_push_audio_larger_than_buffer(tmp_appdata, qapp):
    w = _widget(tmp_appdata)
    big = np.linspace(-1, 1, len(w._buffer) + 1000, dtype=np.float32)
    w.push_audio(big)
    assert np.allclose(w._buffer, big[-len(w._buffer):])


def test_clear_zeroes_buffer(tmp_appdata, qapp):
    w = _widget(tmp_appdata)
    w.push_audio(np.full(480, 0.5, dtype=np.float32))
    w.clear()
    assert np.all(w._buffer == 0.0)


def test_set_active_toggles_state(tmp_appdata, qapp):
    w = _widget(tmp_appdata)
    assert w._active is False
    w.set_active(True)
    assert w._active is True
    w.set_active(False)
    assert w._active is False


def test_persists_position_on_drag_release(tmp_appdata, qapp):
    from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
    from PyQt6.QtGui import QMouseEvent

    w = _widget(tmp_appdata)
    w.move(100, 200)
    w._drag_offset = QPoint(0, 0)
    release = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(0, 0),
        QPointF(100, 200),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    w.mouseReleaseEvent(release)

    assert w.settings.get("oscilloscope.x") == 100
    assert w.settings.get("oscilloscope.y") == 200


def test_position_clamped_to_screen(tmp_appdata, qapp):
    sm = SettingsManager()
    sm.set("oscilloscope.x", -10000)
    sm.set("oscilloscope.y", -10000)
    w = OscilloscopeWidget(sm)
    from PyQt6.QtWidgets import QApplication

    screen = QApplication.primaryScreen().availableGeometry()
    assert w.x() >= screen.x()
    assert w.y() >= screen.y()


def test_default_size_is_compact(tmp_appdata, qapp):
    w = _widget(tmp_appdata)
    assert w.width() == 320
    assert w.height() == 48


def test_apply_opacity_clamps_range(tmp_appdata, qapp):
    import pytest

    sm = SettingsManager()
    sm.set("oscilloscope.opacity", 0.05)
    w = OscilloscopeWidget(sm)
    # Qt stores opacity in 8-bit precision so clamp ends up ~0.149.
    assert w.windowOpacity() == pytest.approx(0.15, abs=0.01)

    sm.set("oscilloscope.opacity", 5.0)
    w.apply_opacity()
    assert w.windowOpacity() <= 1.0
    assert w.windowOpacity() == pytest.approx(1.0, abs=0.01)


def test_apply_size_from_settings(tmp_appdata, qapp):
    w = _widget(tmp_appdata)
    w.settings.set("oscilloscope.width", 240)
    w.settings.set("oscilloscope.height", 32)
    w.apply_size_from_settings()
    assert w.width() == 240
    assert w.height() == 32


def test_minimum_size_enforced(tmp_appdata, qapp):
    sm = SettingsManager()
    sm.set("oscilloscope.width", 10)
    sm.set("oscilloscope.height", 5)
    w = OscilloscopeWidget(sm)
    assert w.width() >= 120
    assert w.height() >= 24


def test_hit_edge_detects_corner(tmp_appdata, qapp):
    from PyQt6.QtCore import QPoint

    w = _widget(tmp_appdata)
    w.resize(200, 80)
    assert w._hit_edge(QPoint(199, 79)) == "br"
    assert w._hit_edge(QPoint(199, 40)) == "r"
    assert w._hit_edge(QPoint(100, 79)) == "b"
    assert w._hit_edge(QPoint(50, 40)) is None


def test_shape_radius_picks_correct_radius(tmp_appdata, qapp):
    w = _widget(tmp_appdata)
    w.resize(200, 60)
    assert w._shape_radius("rect") == 0.0
    assert w._shape_radius("rounded") == 10.0
    assert w._shape_radius("pill") == 30.0  # height/2


def test_spectrum_mode_produces_bars_in_correct_count(tmp_appdata, qapp):
    sm = SettingsManager()
    sm.set("oscilloscope.style", "spectrum")
    sm.set("oscilloscope.spectrum_bars", 24)
    w = OscilloscopeWidget(sm)
    # Push some non-trivial audio so FFT has signal.
    chunk = (0.2 * np.sin(2 * np.pi * 440 * np.arange(2048) / 16000)).astype(np.float32)
    w.push_audio(chunk)
    bands = w._compute_spectrum_bands(24)
    assert bands.shape == (24,)
    assert np.all(np.isfinite(bands))
    assert float(np.max(bands)) > 0.0  # 440Hz tone produces real signal


def test_spectrum_smoothing_decays_when_silent(tmp_appdata, qapp):
    sm = SettingsManager()
    sm.set("oscilloscope.style", "spectrum")
    sm.set("oscilloscope.spectrum_bars", 16)
    w = OscilloscopeWidget(sm)
    # Loud burst.
    chunk = (0.5 * np.sin(2 * np.pi * 1000 * np.arange(2048) / 16000)).astype(np.float32)
    w.push_audio(chunk)
    first = w._compute_spectrum_bands(16).copy()
    # Silence afterwards — values should decrease (slow decay).
    w.push_audio(np.zeros(2048, dtype=np.float32))
    second = w._compute_spectrum_bands(16)
    assert float(np.max(second)) < float(np.max(first))


def test_spectrum_bars_clamped_to_safe_range(tmp_appdata, qapp):
    sm = SettingsManager()
    sm.set("oscilloscope.spectrum_bars", 5000)  # absurd
    w = OscilloscopeWidget(sm)
    # Should not raise during paint computation.
    w._compute_spectrum_bands(36)


def test_style_setting_round_trips(tmp_appdata, qapp):
    """The style key persists and is what paintEvent reads."""
    sm = SettingsManager()
    sm.set("oscilloscope.style", "spectrum")
    w = OscilloscopeWidget(sm)
    assert w.settings.get("oscilloscope.style") == "spectrum"
    sm.set("oscilloscope.style", "waveform")
    assert w.settings.get("oscilloscope.style") == "waveform"


def test_apply_shape_settings_runs_without_error(tmp_appdata, qapp):
    """apply_shape_settings is the new public hook for refreshing the window mask.

    On the offscreen Qt plugin used in tests, setMask is a no-op (it warns
    "This plugin does not support setting window masks"), so we can only verify
    the call completes without raising and the method exists.
    """
    sm = SettingsManager()
    w = OscilloscopeWidget(sm)
    w.resize(200, 60)
    for shape in ("rounded", "pill", "rect"):
        sm.set("oscilloscope.shape", shape)
        w.apply_shape_settings()  # must not raise


def test_resize_event_calls_update_shape_mask(tmp_appdata, qapp, monkeypatch):
    """ResizeEvent must refresh the mask so the shape follows widget size."""
    from PyQt6.QtCore import QSize
    from PyQt6.QtGui import QResizeEvent

    sm = SettingsManager()
    sm.set("oscilloscope.shape", "pill")
    w = OscilloscopeWidget(sm)
    calls = []
    monkeypatch.setattr(w, "_update_shape_mask", lambda: calls.append(None))
    # Simulate a resize event directly — bypasses the offscreen platform's
    # quirk of not delivering resize events to unshown widgets.
    w.resizeEvent(QResizeEvent(QSize(400, 80), QSize(200, 60)))
    assert calls, "_update_shape_mask should be called from resizeEvent"


def test_dwm_call_skipped_on_non_windows(tmp_appdata, qapp, monkeypatch):
    """Only attempts the DWM call on Windows; quietly no-ops elsewhere."""
    import src.ui.oscilloscope as osc

    sm = SettingsManager()
    w = OscilloscopeWidget(sm)
    monkeypatch.setattr(osc.sys, "platform", "linux")
    # Should not raise even if dwmapi isn't reachable.
    w._set_dwm_corner_preference(osc._DWMWCP_DONOTROUND)


def test_enforce_topmost_when_hidden_is_noop(tmp_appdata, qapp):
    """Hidden widgets shouldn't trigger any z-order calls."""
    sm = SettingsManager()
    w = OscilloscopeWidget(sm)
    # Widget is not shown in offscreen tests — should silently skip.
    w._enforce_topmost()  # must not raise


def test_enforce_topmost_uses_setwindowpos_on_windows(tmp_appdata, qapp, monkeypatch):
    """On Windows, ``SetWindowPos`` is called with HWND_TOPMOST + no-activate."""
    import src.ui.oscilloscope as osc

    sm = SettingsManager()
    w = OscilloscopeWidget(sm)
    monkeypatch.setattr(w, "isVisible", lambda: True)
    monkeypatch.setattr(w, "winId", lambda: 0xDEADBEEF)

    monkeypatch.setattr(osc.sys, "platform", "win32")

    calls = []

    class FakeUser32:
        def SetWindowPos(self, *args):
            calls.append(args)
            return 1

    class FakeWinDLL:
        def __init__(self):
            self.user32 = FakeUser32()

    monkeypatch.setattr(osc.ctypes, "windll", FakeWinDLL())
    w._enforce_topmost()
    assert len(calls) == 1
    # Args: (hwnd, HWND_TOPMOST, x, y, cx, cy, flags)
    args = calls[0]
    assert args[2] == 0 and args[3] == 0 and args[4] == 0 and args[5] == 0
    flags = args[6]
    assert flags & osc._SWP_NOMOVE
    assert flags & osc._SWP_NOSIZE
    assert flags & osc._SWP_NOACTIVATE  # no focus stolen


def test_enforce_topmost_falls_back_to_raise_on_non_windows(tmp_appdata, qapp, monkeypatch):
    import src.ui.oscilloscope as osc

    sm = SettingsManager()
    w = OscilloscopeWidget(sm)
    monkeypatch.setattr(w, "isVisible", lambda: True)
    monkeypatch.setattr(osc.sys, "platform", "linux")
    raise_calls = []
    monkeypatch.setattr(w, "raise_", lambda: raise_calls.append(None))
    w._enforce_topmost()
    assert raise_calls == [None]


def test_accent_color_reads_from_settings(tmp_appdata, qapp):
    sm = SettingsManager()
    sm.set("oscilloscope.color_active", "#ff00aa")
    sm.set("oscilloscope.color_idle", "#0099ff")
    w = OscilloscopeWidget(sm)

    w.set_active(False)
    assert w._accent_color().name() == "#0099ff"

    w.set_active(True)
    assert w._accent_color().name() == "#ff00aa"


def test_accent_color_falls_back_when_invalid(tmp_appdata, qapp):
    sm = SettingsManager()
    sm.set("oscilloscope.color_active", "not-a-color")
    w = OscilloscopeWidget(sm)
    w.set_active(True)
    assert w._accent_color().name() == "#40dc8c"
