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
    sm.set("paste_lock_enabled", True)
    return sm


# ---- Per-session capture (Task 3) -----------------------------------

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
    settings.set("paste_lock_enabled", False)
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


# ---- Smart-toggle sticky lock (Task 4) ------------------------------

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
    settings.set("paste_lock_enabled", False)
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


# ---- Target liveness + silent clear (Task 5) -----------------------

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


def test_clear_sticky_silently_drops_lock_and_emits_none(settings, qapp):
    """Used by app on target_invalid; the dead-target notification
    carries the user feedback. lock_changed still fires so subscribers
    update; the source is "none" so subscribers can decide whether to
    play tones based on previous state."""
    c = PasteTargetController(settings)
    c._sticky_hwnd = 42
    c._sticky_pid = 1234
    received = []
    c.lock_changed.connect(lambda hwnd, src: received.append((hwnd, src)))
    c.clear_sticky_silently()
    assert c._sticky_hwnd is None
    assert received == [(None, "none")]
