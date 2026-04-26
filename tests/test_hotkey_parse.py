"""Tests for parse_hotkey_to_keys (bypasses pynput's HotKey.parse)."""

from __future__ import annotations

import pytest
from pynput.keyboard import Key, KeyCode

from src.hotkey_manager import parse_hotkey_to_keys


def _kc(c: str) -> KeyCode:
    return KeyCode.from_char(c)


def test_simple_alpha():
    assert parse_hotkey_to_keys("<alt>+z") == [Key.alt, _kc("z")]


def test_ctrl_shift_v():
    assert parse_hotkey_to_keys("<ctrl>+<shift>+v") == [Key.ctrl, Key.shift, _kc("v")]


def test_special_only():
    # Non-modifier specials are stored as KeyCode (matching what pynput's
    # listener delivers at runtime), NOT as the Key enum.
    expected = [KeyCode.from_vk(Key.delete.value.vk)]
    assert parse_hotkey_to_keys("<delete>") == expected


def test_plus_alias():
    assert parse_hotkey_to_keys("<plus>") == [_kc("+")]


def test_bare_plus_is_literal():
    assert parse_hotkey_to_keys("+") == [_kc("+")]


def test_ctrl_doubled_plus_is_ctrl_plus():
    assert parse_hotkey_to_keys("<ctrl>++") == [Key.ctrl, _kc("+")]


def test_alt_plus_via_alias():
    assert parse_hotkey_to_keys("<alt>+<plus>") == [Key.alt, _kc("+")]


def test_unknown_special_raises():
    with pytest.raises(ValueError):
        parse_hotkey_to_keys("<frobnicate>")


def test_empty_raises():
    with pytest.raises(ValueError):
        parse_hotkey_to_keys("")


@pytest.mark.parametrize(
    "hotkey,expected",
    [
        # Modifier chords don't insert text.
        ("<alt>+z", 0),
        ("<ctrl>+<backspace>", 0),
        ("<ctrl>+<shift>+v", 0),
        # Bare specials that insert printable text.
        ("<space>", 1),
        ("<enter>", 1),
        ("<tab>", 1),
        ("<plus>", 1),
        # Bare specials that don't.
        ("<delete>", 0),
        ("<f9>", 0),
        ("<home>", 0),
        ("<up>", 0),
        ("<backspace>", 0),
        # Bare printable single chars.
        ("+", 1),
        ("z", 1),
        ("5", 1),
        ("=", 1),
    ],
)
def test_chars_inserted_per_press(hotkey, expected):
    from src.hotkey_manager import chars_inserted_per_press

    assert chars_inserted_per_press(hotkey) == expected


@pytest.mark.parametrize(
    "hotkey",
    [
        "<delete>",
        "<alt>+z",
        "<ctrl>+<shift>+v",
        "<f9>",
        "<ctrl>+<backspace>",
        "<space>",
        "<enter>",
        "<f12>",
        "<insert>",
    ],
)
def test_parser_matches_pynput_HotKey_parse_for_normal_chords(hotkey):
    """Regression: every chord pynput can parse must produce equal-comparing keys.

    The bug we're guarding against: returning ``Key.delete`` (enum) when
    pynput's listener delivers ``KeyCode(vk=46)``. Those don't match in a
    HotKey._state set, so the chord never fires.
    """
    from pynput.keyboard import HotKey

    expected = HotKey.parse(hotkey)
    actual = parse_hotkey_to_keys(hotkey)
    assert set(expected) == set(actual), (
        f"hotkey {hotkey!r}: pynput={expected!r}, ours={actual!r}"
    )
