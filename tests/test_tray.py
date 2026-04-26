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


def test_set_oscilloscope_visible_toggles_text_only(qapp):
    """Action is intentionally NOT checkable — text alone conveys state."""
    tray = TrayController()
    assert tray.action_oscilloscope.isCheckable() is False
    tray.set_oscilloscope_visible(True)
    assert "Hide" in tray.action_oscilloscope.text()
    tray.set_oscilloscope_visible(False)
    assert "Show" in tray.action_oscilloscope.text()


def test_set_auto_enter_enabled_toggles_text(qapp):
    """Auto-Enter tray item follows the same text-only convention."""
    tray = TrayController()
    assert tray.action_auto_enter.isCheckable() is False
    tray.set_auto_enter_enabled(False)
    assert tray.action_auto_enter.text() == "Enable Auto-Enter"
    tray.set_auto_enter_enabled(True)
    assert tray.action_auto_enter.text() == "Disable Auto-Enter"


def test_action_triggers_emit_signals(qapp):
    tray = TrayController()
    received = {
        "toggle": 0,
        "settings": 0,
        "oscilloscope": 0,
        "auto_enter": 0,
        "voice": 0,
        "voice_interrupt": 0,
        "quit": 0,
    }
    tray.toggle_capture.connect(lambda: received.__setitem__("toggle", received["toggle"] + 1))
    tray.show_settings.connect(lambda: received.__setitem__("settings", received["settings"] + 1))
    tray.toggle_oscilloscope.connect(
        lambda: received.__setitem__("oscilloscope", received["oscilloscope"] + 1)
    )
    tray.toggle_auto_enter.connect(
        lambda: received.__setitem__("auto_enter", received["auto_enter"] + 1)
    )
    tray.toggle_voice.connect(lambda: received.__setitem__("voice", received["voice"] + 1))
    # interrupt_voice is wired to an action that's disabled by default —
    # enable it for this test so we can verify the signal path.
    tray.set_voice_speaking(True)
    tray.interrupt_voice.connect(
        lambda: received.__setitem__("voice_interrupt", received["voice_interrupt"] + 1)
    )
    tray.quit_requested.connect(lambda: received.__setitem__("quit", received["quit"] + 1))

    tray.action_toggle.trigger()
    tray.action_settings.trigger()
    tray.action_oscilloscope.trigger()
    tray.action_auto_enter.trigger()
    tray.action_voice.trigger()
    tray.action_voice_interrupt.trigger()
    tray.action_quit.trigger()
    qapp.processEvents()

    assert received == {
        "toggle": 1,
        "settings": 1,
        "oscilloscope": 1,
        "auto_enter": 1,
        "voice": 1,
        "voice_interrupt": 1,
        "quit": 1,
    }


def test_set_voice_enabled_toggles_text(qapp):
    tray = TrayController()
    assert tray.action_voice.isCheckable() is False
    tray.set_voice_enabled(False)
    assert tray.action_voice.text() == "Enable Voice Read-Back"
    tray.set_voice_enabled(True)
    assert tray.action_voice.text() == "Disable Voice Read-Back"


def test_voice_interrupt_disabled_until_speaking(qapp):
    tray = TrayController()
    assert tray.action_voice_interrupt.isEnabled() is False
    tray.set_voice_speaking(True)
    assert tray.action_voice_interrupt.isEnabled() is True
    tray.set_voice_speaking(False)
    assert tray.action_voice_interrupt.isEnabled() is False
