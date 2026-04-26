"""Regression tests for the settings dialog warning rendering."""

from __future__ import annotations

from src.settings_manager import SettingsManager
from src.ui.settings_dialog import SettingsDialog


def test_warning_label_preserves_angle_bracket_tokens(tmp_appdata, qapp):
    """Bug: the warning was rendered as rich-text, eating <plus> as an HTML tag."""
    sm = SettingsManager()
    sm.set("hotkey", "<alt>+z")
    sm.set("delete_hotkey", "<plus>")  # bare key, no modifier -> warning expected

    dlg = SettingsDialog(sm)
    text = dlg.hotkey_warning.text()
    # The warning was set on the label even before the dialog is shown.
    assert text != ""
    # Both the literal hotkey name and the suggested replacement must be HTML
    # escaped so QLabel renders them literally instead of treating them as
    # unknown HTML tags (which would silently strip them).
    assert "&lt;plus&gt;" in text
    assert "&lt;ctrl&gt;+&lt;backspace&gt;" in text
    # The bold "Warning:" prefix must remain real markup.
    assert "<b>Warning:</b>" in text


def test_no_warning_for_clean_hotkeys(tmp_appdata, qapp):
    sm = SettingsManager()
    sm.set("hotkey", "<alt>+z")
    sm.set("delete_hotkey", "<ctrl>+<backspace>")
    dlg = SettingsDialog(sm)
    assert dlg.hotkey_warning.text() == ""
