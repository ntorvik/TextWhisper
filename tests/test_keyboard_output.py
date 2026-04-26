"""Tests for KeyboardOutput.

We patch pynput's Controller so no real keystrokes are sent.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.keyboard_output import KeyboardOutput
from src.settings_manager import SettingsManager


def _kb(tmp_appdata, **overrides) -> tuple[KeyboardOutput, MagicMock]:
    sm = SettingsManager()
    for k, v in overrides.items():
        sm.set(k, v)
    with patch("src.keyboard_output.Controller") as ctrl_cls:
        ctrl_cls.return_value = MagicMock()
        kb = KeyboardOutput(sm)
        return kb, kb._kb


def test_empty_text_is_noop(tmp_appdata):
    kb, mock = _kb(tmp_appdata, type_delay_ms=0)
    typed = kb.type_text("")
    assert typed == 0
    mock.type.assert_not_called()


def test_simple_text_typed_with_trailing_space(tmp_appdata):
    kb, mock = _kb(tmp_appdata, type_delay_ms=0, trailing_space=True)
    typed = kb.type_text("hello world")
    # Spaces go through press/release, all other chars through type().
    typed_chars = [c.args[0] for c in mock.type.call_args_list]
    assert "".join(typed_chars) == "helloworld"
    # Two press/release pairs for the two spaces (one inside, one trailing).
    from pynput.keyboard import Key

    space_presses = [c.args[0] for c in mock.press.call_args_list if c.args[0] == Key.space]
    space_releases = [c.args[0] for c in mock.release.call_args_list if c.args[0] == Key.space]
    assert len(space_presses) == 2
    assert len(space_releases) == 2
    assert typed == len("hello world ")


def test_no_trailing_space_when_disabled(tmp_appdata):
    kb, mock = _kb(tmp_appdata, type_delay_ms=0, trailing_space=False)
    typed = kb.type_text("hello")
    calls = [c.args[0] for c in mock.type.call_args_list]
    assert "".join(calls) == "hello"
    assert typed == len("hello")


def test_no_double_trailing_space(tmp_appdata):
    kb, mock = _kb(tmp_appdata, type_delay_ms=0, trailing_space=True)
    typed = kb.type_text("ends already ")
    # 'ends already ' has one inner space + one trailing space — both as Key.space,
    # and we should NOT add another trailing one since it ends with space.
    from pynput.keyboard import Key

    typed_chars = [c.args[0] for c in mock.type.call_args_list]
    assert "".join(typed_chars) == "endsalready"
    space_presses = [c.args[0] for c in mock.press.call_args_list if c.args[0] == Key.space]
    assert len(space_presses) == 2
    assert typed == len("ends already ")


def test_no_extra_space_after_newline(tmp_appdata):
    kb, mock = _kb(tmp_appdata, type_delay_ms=0, trailing_space=True)
    typed = kb.type_text("line\n")
    # Newline goes through Key.enter, not type(); 'line' chars go through type().
    from pynput.keyboard import Key

    typed_chars = [c.args[0] for c in mock.type.call_args_list]
    assert "".join(typed_chars) == "line"
    enter_presses = [c.args[0] for c in mock.press.call_args_list if c.args[0] == Key.enter]
    assert len(enter_presses) == 1
    assert typed == len("line\n")


def test_per_character_typing_when_delay_set(tmp_appdata):
    kb, mock = _kb(tmp_appdata, type_delay_ms=1, trailing_space=False)
    typed = kb.type_text("abc")
    calls = [c.args[0] for c in mock.type.call_args_list]
    assert calls == ["a", "b", "c"]
    assert typed == 3


def test_paste_mode_clipboard_has_no_trailing_space(tmp_appdata, qapp):
    """Trailing space is NOT in the clipboard — terminals strip it.
    A real Key.space keystroke is injected separately for the separator.
    """
    from pynput.keyboard import Key
    from PyQt6.QtWidgets import QApplication

    sm = SettingsManager()
    sm.set("output_method", "paste")
    sm.set("paste_settle_ms", 0)
    sm.set("trailing_space", True)
    with patch("src.keyboard_output.Controller") as ctrl_cls:
        ctrl_cls.return_value = MagicMock()
        kb = KeyboardOutput(sm)
        mock = kb._kb
        kb.type_text("hello world")

    # Clipboard payload deliberately omits the trailing space.
    assert QApplication.clipboard().text() == "hello world"
    presses = [c.args[0] for c in mock.press.call_args_list]
    releases = [c.args[0] for c in mock.release.call_args_list]
    # Ctrl+V was sent.
    assert Key.ctrl in presses and "v" in presses
    assert Key.ctrl in releases and "v" in releases
    # Real Key.space was sent as the separator.
    assert Key.space in presses
    assert Key.space in releases


def test_paste_mode_no_separator_when_trailing_space_disabled(tmp_appdata, qapp):
    from pynput.keyboard import Key

    sm = SettingsManager()
    sm.set("output_method", "paste")
    sm.set("paste_settle_ms", 0)
    sm.set("trailing_space", False)
    with patch("src.keyboard_output.Controller") as ctrl_cls:
        ctrl_cls.return_value = MagicMock()
        kb = KeyboardOutput(sm)
        mock = kb._kb
        kb.type_text("hello")

    presses = [c.args[0] for c in mock.press.call_args_list]
    assert Key.space not in presses


def test_paste_mode_no_separator_when_text_already_ends_in_space(tmp_appdata, qapp):
    from pynput.keyboard import Key

    sm = SettingsManager()
    sm.set("output_method", "paste")
    sm.set("paste_settle_ms", 0)
    sm.set("trailing_space", True)
    with patch("src.keyboard_output.Controller") as ctrl_cls:
        ctrl_cls.return_value = MagicMock()
        kb = KeyboardOutput(sm)
        mock = kb._kb
        kb.type_text("ends already ")
    presses = [c.args[0] for c in mock.press.call_args_list]
    assert Key.space not in presses


def test_paste_mode_returns_total_chars_sent(tmp_appdata, qapp):
    sm = SettingsManager()
    sm.set("output_method", "paste")
    sm.set("paste_settle_ms", 0)
    sm.set("trailing_space", True)
    with patch("src.keyboard_output.Controller") as ctrl_cls:
        ctrl_cls.return_value = MagicMock()
        kb = KeyboardOutput(sm)
        sent = kb.type_text("hello world")
    # 11 chars in clipboard payload + 1 trailing space keystroke.
    assert sent == 12


def test_typing_failure_swallowed(tmp_appdata):
    kb, mock = _kb(tmp_appdata, type_delay_ms=0)
    mock.type.side_effect = RuntimeError("focus failed")
    typed = kb.type_text("anything")
    assert typed == 0


def test_delete_word_sends_ctrl_backspace(tmp_appdata):
    from pynput.keyboard import Key

    kb, mock = _kb(tmp_appdata)
    kb.delete_word()

    presses = [c.args[0] for c in mock.press.call_args_list]
    releases = [c.args[0] for c in mock.release.call_args_list]
    assert Key.ctrl in presses
    assert Key.backspace in presses
    assert Key.backspace in releases
    assert Key.ctrl in releases


def test_delete_chars_sends_n_backspaces(tmp_appdata):
    from pynput.keyboard import Key

    kb, mock = _kb(tmp_appdata, type_delay_ms=0)
    kb.delete_chars(5)

    backspace_presses = [
        c.args[0] for c in mock.press.call_args_list if c.args[0] == Key.backspace
    ]
    assert len(backspace_presses) == 5


def test_delete_chars_zero_is_noop(tmp_appdata):
    kb, mock = _kb(tmp_appdata, type_delay_ms=0)
    kb.delete_chars(0)
    mock.press.assert_not_called()


def test_delete_chars_failure_swallowed(tmp_appdata):
    kb, mock = _kb(tmp_appdata, type_delay_ms=0)
    mock.press.side_effect = RuntimeError("focus failed")
    kb.delete_chars(3)  # should not raise


def test_send_enter_sends_enter_keystroke(tmp_appdata):
    from pynput.keyboard import Key

    kb, mock = _kb(tmp_appdata)
    kb.send_enter()
    presses = [c.args[0] for c in mock.press.call_args_list]
    releases = [c.args[0] for c in mock.release.call_args_list]
    assert Key.enter in presses
    assert Key.enter in releases


def test_replace_last_period_with_comma_with_trailing_space(tmp_appdata):
    """trailing_space=True: erase '. ' (2 chars), emit ',' + ' '."""
    from pynput.keyboard import Key

    kb, mock = _kb(tmp_appdata)
    kb.replace_last_period_with_comma(had_trailing_space=True)

    backspaces = [
        c.args[0] for c in mock.press.call_args_list if c.args[0] == Key.backspace
    ]
    assert len(backspaces) == 2
    typed_chars = [c.args[0] for c in mock.type.call_args_list]
    assert "".join(typed_chars) == ","
    space_presses = [c.args[0] for c in mock.press.call_args_list if c.args[0] == Key.space]
    assert len(space_presses) == 1


def test_replace_last_period_with_comma_without_trailing_space(tmp_appdata):
    """trailing_space=False: erase '.' (1 char), emit ',' (no space)."""
    from pynput.keyboard import Key

    kb, mock = _kb(tmp_appdata)
    kb.replace_last_period_with_comma(had_trailing_space=False)

    backspaces = [
        c.args[0] for c in mock.press.call_args_list if c.args[0] == Key.backspace
    ]
    assert len(backspaces) == 1
    typed_chars = [c.args[0] for c in mock.type.call_args_list]
    assert "".join(typed_chars) == ","
    space_presses = [c.args[0] for c in mock.press.call_args_list if c.args[0] == Key.space]
    assert space_presses == []
