"""Tests for the hotkey validator and normalizer."""

from __future__ import annotations

import pytest

from src.hotkey_manager import has_modifier, normalize_hotkey, validate_hotkeys


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("<alt>+z", True),
        ("<ctrl>+<shift>+v", True),
        ("<ctrl>+<backspace>", True),
        ("<delete>", False),
        ("a", False),
        ("<f9>", False),
        ("", False),
    ],
)
def test_has_modifier(raw, expected):
    assert has_modifier(raw) is expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("<Alt>+Z", "<alt>+z"),
        ("<CTRL>+<Shift>+v", "<ctrl>+<shift>+v"),
        ("<shift>+<ctrl>+v", "<ctrl>+<shift>+v"),  # modifiers sorted
        ("<delete>", "<delete>"),
        ("", ""),
    ],
)
def test_normalize_hotkey(raw, expected):
    assert normalize_hotkey(raw) == expected


def test_collision_detected():
    issues = validate_hotkeys("<alt>+z", "<alt>+z")
    assert any(level == "error" and "same chord" in msg for level, msg in issues)


def test_empty_toggle_is_error():
    issues = validate_hotkeys("", "<delete>")
    assert any(level == "error" and "Dictation" in msg for level, msg in issues)


def test_empty_delete_is_error():
    issues = validate_hotkeys("<alt>+z", "")
    assert any(level == "error" and "Delete-word" in msg for level, msg in issues)


def test_bare_delete_warns():
    issues = validate_hotkeys("<alt>+z", "<delete>")
    warns = [m for level, m in issues if level == "warn"]
    assert any("no modifier" in m for m in warns)


def test_bare_toggle_warns():
    issues = validate_hotkeys("z", "<ctrl>+<backspace>")
    warns = [m for level, m in issues if level == "warn"]
    assert any("no modifier" in m for m in warns)


def test_clean_pair_no_issues():
    assert validate_hotkeys("<alt>+z", "<ctrl>+<backspace>") == []


def test_modifier_order_does_not_create_false_collision():
    issues = validate_hotkeys("<ctrl>+<shift>+v", "<shift>+<ctrl>+v")
    # Same chord, just typed differently — should still be flagged.
    assert any(level == "error" for level, _ in issues)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("<plus>", "<plus>"),
        ("+", "<plus>"),                          # bare '+' treated as literal
        ("<ctrl>++", "<ctrl>+<plus>"),            # doubled '+' = ctrl + plus key
        ("<alt>+<plus>", "<alt>+<plus>"),
    ],
)
def test_plus_key_is_recognised(raw, expected):
    assert normalize_hotkey(raw) == expected


def test_plus_key_passes_validation():
    assert validate_hotkeys("<alt>+z", "<plus>") == [
        ("warn", "Delete-word hotkey '<plus>' has no modifier — it will fire "
                 "globally and conflict with the key's normal function. "
                 "Consider <ctrl>+<backspace>."),
    ]


def test_ctrl_plus_passes_validation_clean():
    # Ctrl+Plus is a well-formed chord with a modifier and shouldn't warn.
    assert validate_hotkeys("<alt>+z", "<ctrl>+<plus>") == []


def test_validate_includes_lock_toggle_collision_with_toggle():
    from src.hotkey_manager import validate_hotkeys

    issues = validate_hotkeys(
        toggle="<alt>+z",
        delete="<delete>",
        lock_toggle="<alt>+z",
    )
    severities = [s for s, _ in issues]
    assert "error" in severities


def test_validate_includes_lock_toggle_collision_with_delete():
    from src.hotkey_manager import validate_hotkeys

    issues = validate_hotkeys(
        toggle="<alt>+z",
        delete="<delete>",
        lock_toggle="<delete>",
    )
    assert any("error" == s for s, _ in issues)


def test_validate_lock_toggle_unique_no_error():
    from src.hotkey_manager import validate_hotkeys

    issues = validate_hotkeys(
        toggle="<alt>+z",
        delete="<delete>",
        lock_toggle="<alt>+l",
    )
    assert all(s != "error" for s, _ in issues)


def test_validate_lock_toggle_no_modifier_warns():
    from src.hotkey_manager import validate_hotkeys

    issues = validate_hotkeys(
        toggle="<alt>+z",
        delete="<delete>",
        lock_toggle="l",
    )
    assert any(s == "warn" for s, _ in issues)
