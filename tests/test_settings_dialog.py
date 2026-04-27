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


def test_settings_dialog_has_paste_target_lock_section(qapp, tmp_appdata):
    from src.settings_manager import SettingsManager
    from src.ui.settings_dialog import SettingsDialog

    sm = SettingsManager()
    dlg = SettingsDialog(sm)
    names = {w.objectName() for w in dlg.findChildren(object) if w.objectName()}
    expected = {
        "paste_lock_enabled_check",
        "paste_lock_hotkey_edit",
        "paste_lock_border_enabled_check",
        "paste_lock_border_color_button",
        "paste_lock_border_thickness_spin",
        "paste_lock_play_sounds_check",
    }
    missing = expected - names
    assert not missing, f"missing widgets: {missing}"


def test_paste_target_lock_section_writes_back_to_settings(
    qapp, tmp_appdata, monkeypatch
):
    from PyQt6.QtWidgets import QMessageBox

    from src.settings_manager import SettingsManager
    from src.ui.settings_dialog import SettingsDialog

    # Use chord-style hotkeys so _save() doesn't pop a modal warning.
    sm = SettingsManager()
    sm.set("hotkey", "<alt>+z")
    sm.set("delete_hotkey", "<ctrl>+<backspace>")
    dlg = SettingsDialog(sm)
    enable_check = dlg.findChild(object, "paste_lock_enabled_check")
    assert enable_check is not None
    enable_check.setChecked(True)
    # Bypass the dialog actually closing + any residual modal.
    dlg.accept = lambda: None
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *a, **kw: QMessageBox.StandardButton.Ok
    )
    dlg._save()
    assert sm.get("paste_lock_enabled") is True
