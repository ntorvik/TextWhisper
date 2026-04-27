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
    sm.set("paste_lock_enabled", True)
    sm.set("paste_lock_border_enabled", True)
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
    assert o._target_hwnd == 42


def test_master_disable_setting_hides_overlay(settings, qapp):
    o = WindowBorderOverlay(settings)
    settings.set("paste_lock_border_enabled", False)
    with patch.object(w, "is_window", return_value=True), \
         patch.object(w, "is_iconic", return_value=False), \
         patch.object(w, "get_window_rect", return_value=(0, 0, 100, 100)):
        o.set_target_hwnd(42)
        o._tick()
    assert not o.isVisible()
