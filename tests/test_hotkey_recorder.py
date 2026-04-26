"""Tests for the Qt key → pynput translation in the HotkeyRecorder."""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt

from src.ui.hotkey_recorder import build_hotkey_string, qt_key_to_pynput


@pytest.mark.parametrize(
    "qt_key,text,expected",
    [
        (Qt.Key.Key_Z, "z", "z"),
        (Qt.Key.Key_A, "a", "a"),
        (Qt.Key.Key_Z, "Z", "z"),  # shift held — should still lowercase
        (Qt.Key.Key_F9, "", "<f9>"),
        (Qt.Key.Key_F12, "", "<f12>"),
        (Qt.Key.Key_Delete, "", "<delete>"),
        (Qt.Key.Key_Backspace, "", "<backspace>"),
        (Qt.Key.Key_Space, " ", "<space>"),
        (Qt.Key.Key_Return, "", "<enter>"),
        (Qt.Key.Key_Up, "", "<up>"),
        (Qt.Key.Key_PageDown, "", "<page_down>"),
        (Qt.Key.Key_5, "5", "5"),
        # The plus key — represented as <plus> alias to avoid colliding with
        # pynput's '+'-as-separator syntax.
        (Qt.Key.Key_Plus, "+", "<plus>"),
    ],
)
def test_qt_key_to_pynput(qt_key, text, expected):
    assert qt_key_to_pynput(qt_key, text) == expected


def test_qt_key_unknown_returns_none():
    # An unmapped Qt key with no printable text -> None
    assert qt_key_to_pynput(Qt.Key.Key_unknown, "") is None


def test_build_with_no_modifier():
    assert build_hotkey_string(Qt.KeyboardModifier.NoModifier, Qt.Key.Key_F9, "") == "<f9>"


def test_build_with_alt_z():
    chord = build_hotkey_string(Qt.KeyboardModifier.AltModifier, Qt.Key.Key_Z, "z")
    assert chord == "<alt>+z"


def test_build_with_ctrl_shift_v():
    mods = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
    chord = build_hotkey_string(mods, Qt.Key.Key_V, "V")
    assert chord == "<ctrl>+<shift>+v"


def test_build_with_ctrl_backspace():
    chord = build_hotkey_string(
        Qt.KeyboardModifier.ControlModifier, Qt.Key.Key_Backspace, ""
    )
    assert chord == "<ctrl>+<backspace>"


def test_build_unsupported_returns_none():
    chord = build_hotkey_string(
        Qt.KeyboardModifier.ControlModifier, Qt.Key.Key_unknown, ""
    )
    assert chord is None


def test_recorder_dialog_constructs(qapp):
    from src.ui.hotkey_recorder import HotkeyRecorder

    dlg = HotkeyRecorder(current="<alt>+z")
    assert dlg.preview.text() == "<alt>+z"
    assert dlg._pending == "<alt>+z"
    dlg._clear()
    assert dlg._pending is None
    assert dlg.preview.text() == "—"


def test_recorder_save_emits_captured(qapp):
    from src.ui.hotkey_recorder import HotkeyRecorder

    dlg = HotkeyRecorder()
    dlg._pending = "<ctrl>+<shift>+v"
    dlg._save()
    assert dlg.captured == "<ctrl>+<shift>+v"
    assert dlg.result() == int(dlg.DialogCode.Accepted)


def test_recorder_save_with_empty_pending_rejects(qapp):
    from src.ui.hotkey_recorder import HotkeyRecorder

    dlg = HotkeyRecorder()
    dlg._pending = None
    dlg._save()
    assert dlg.captured is None
    assert dlg.result() == int(dlg.DialogCode.Rejected)
