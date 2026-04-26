"""Tests for the TrayController."""

from __future__ import annotations

from src.ui.tray import TrayController


def test_initial_state_idle(qapp):
    tray = TrayController()
    assert tray.action_toggle.text() == "Start Capture"
    assert "Idle" in tray.tray.toolTip()


def test_set_active_updates_label_and_tooltip(qapp):
    tray = TrayController()
    tray.set_active(True)
    assert tray.action_toggle.text() == "Stop Capture"
    assert "Listening" in tray.tray.toolTip()
    tray.set_active(False)
    assert tray.action_toggle.text() == "Start Capture"


def test_set_oscilloscope_visible_toggles_text(qapp):
    tray = TrayController()
    tray.set_oscilloscope_visible(True)
    assert tray.action_oscilloscope.isChecked() is True
    assert "Hide" in tray.action_oscilloscope.text()
    tray.set_oscilloscope_visible(False)
    assert tray.action_oscilloscope.isChecked() is False
    assert "Show" in tray.action_oscilloscope.text()


def test_action_triggers_emit_signals(qapp):
    tray = TrayController()
    received = {"toggle": 0, "settings": 0, "oscilloscope": 0, "quit": 0}
    tray.toggle_capture.connect(lambda: received.__setitem__("toggle", received["toggle"] + 1))
    tray.show_settings.connect(lambda: received.__setitem__("settings", received["settings"] + 1))
    tray.toggle_oscilloscope.connect(
        lambda: received.__setitem__("oscilloscope", received["oscilloscope"] + 1)
    )
    tray.quit_requested.connect(lambda: received.__setitem__("quit", received["quit"] + 1))

    tray.action_toggle.trigger()
    tray.action_settings.trigger()
    tray.action_oscilloscope.trigger()
    tray.action_quit.trigger()
    qapp.processEvents()

    assert received == {"toggle": 1, "settings": 1, "oscilloscope": 1, "quit": 1}
