"""Tests for TextWhisperApp top-level orchestration.

Heavy components (audio device, whisper model, real hotkey listener) are
mocked. We focus on:
  - hotkey dispatch (toggle vs delete)
  - single-tap → delete_word after timeout
  - double-tap → pop typed-history stack and delete that segment
  - unlimited double-taps walking the stack backwards
  - clipboard copy on transcription
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.app import TextWhisperApp


@pytest.fixture
def app(tmp_appdata, qapp):
    """Construct TextWhisperApp without starting external services."""
    with patch("src.app.HotkeyManager"), patch("src.audio_capture.sd"):
        tw = TextWhisperApp(qapp)
    yield tw
    # Stop the timer to avoid spillage between tests.
    tw._delete_timer.stop()


def test_hotkey_mapping_includes_both(app):
    mapping = app._build_hotkey_mapping()
    assert mapping == {"toggle": "<alt>+z", "delete": "<delete>"}


def test_hotkey_mapping_drops_delete_if_same_as_toggle(app):
    app.settings.set("delete_hotkey", "<alt>+z")
    mapping = app._build_hotkey_mapping()
    assert "delete" not in mapping


def test_notify_passes_through_when_enabled(app):
    app.settings.set("notifications_enabled", True)
    with patch.object(app.tray, "notify") as n:
        app._notify("title", "message")
        n.assert_called_once_with("title", "message", error=False)


def test_notify_suppressed_when_disabled(app):
    app.settings.set("notifications_enabled", False)
    with patch.object(app.tray, "notify") as n:
        app._notify("title", "message")
        app._notify("err title", "err msg", error=True)
        n.assert_not_called()


def test_engine_error_respects_notification_setting(app):
    app.settings.set("notifications_enabled", False)
    with patch.object(app.tray, "notify") as n:
        app._on_engine_error("model load failed")
        n.assert_not_called()


def test_watchdog_revives_dead_listener(app):
    with patch.object(app.hotkey, "restart_if_dead", return_value=True) as r:
        app._check_hotkey_health()
        r.assert_called_once()


def test_watchdog_noop_when_alive(app):
    with patch.object(app.hotkey, "restart_if_dead", return_value=False) as r:
        app._check_hotkey_health()
        r.assert_called_once()


def test_unknown_hotkey_is_logged(app, caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        app._on_hotkey_triggered("frobnicate")
    assert any("frobnicate" in r.message for r in caplog.records)


def test_single_tap_delete_calls_delete_word(app, qapp):
    app.settings.set("delete_double_tap_ms", 100)
    with patch.object(app.keyboard_out, "delete_word") as dw, patch.object(
        app.keyboard_out, "delete_chars"
    ) as dc:
        app._on_delete_pressed()
        assert app._delete_pending is True
        # Simulate the timer firing.
        app._on_delete_single_timeout()
        dw.assert_called_once()
        dc.assert_not_called()
        assert app._delete_pending is False


def test_double_tap_pops_history_and_deletes_segment(app):
    app._typed_history = [17]
    with patch.object(app.keyboard_out, "delete_word") as dw, patch.object(
        app.keyboard_out, "delete_chars"
    ) as dc:
        app._on_delete_pressed()
        app._on_delete_pressed()  # second tap before timeout
        dw.assert_not_called()
        dc.assert_called_once_with(17)
        assert app._typed_history == []
        assert app._delete_pending is False


def test_unlimited_double_taps_walk_history_backwards(app):
    """Three transcriptions then three double-taps must pop them in LIFO order."""
    app._typed_history = [10, 25, 8]
    with patch.object(app.keyboard_out, "delete_chars") as dc:
        # First double-tap -> pops 8.
        app._on_delete_pressed()
        app._on_delete_pressed()
        # Second double-tap -> pops 25.
        app._on_delete_pressed()
        app._on_delete_pressed()
        # Third double-tap -> pops 10.
        app._on_delete_pressed()
        app._on_delete_pressed()
        # Fourth double-tap -> nothing to pop, no delete.
        app._on_delete_pressed()
        app._on_delete_pressed()
    counts = [c.args[0] for c in dc.call_args_list]
    assert counts == [8, 25, 10]
    assert app._typed_history == []


def test_double_tap_with_no_history_does_not_send_backspaces(app):
    app._typed_history = []
    with patch.object(app.keyboard_out, "delete_chars") as dc:
        app._on_delete_pressed()
        app._on_delete_pressed()
        dc.assert_not_called()


def test_double_tap_compensates_for_printable_hotkey_chars(app):
    """A '+' hotkey types '+' into the focused window each press.

    Double-tap inserts 2 stray '+' chars before the delete fires, so the total
    backspace count must be ``segment_length + 2``.
    """
    app.settings.set("delete_hotkey", "<plus>")
    app._typed_history = [13]  # e.g. "Hello world. "
    with patch.object(app.keyboard_out, "delete_chars") as dc:
        app._on_delete_pressed()
        app._on_delete_pressed()  # second tap -> double-tap path
        dc.assert_called_once_with(15)  # 13 segment + 2 stray '+' chars
    assert app._typed_history == []
    assert app._extra_chars_typed_by_hotkey == 0


def test_single_tap_cleans_up_printable_hotkey_char_before_delete_word(app):
    app.settings.set("delete_hotkey", "+")
    app._typed_history = [13]
    with patch.object(app.keyboard_out, "delete_chars") as dc, patch.object(
        app.keyboard_out, "delete_word"
    ) as dw:
        app._on_delete_pressed()
        app._on_delete_single_timeout()
        dc.assert_called_once_with(1)  # remove the stray '+'
        dw.assert_called_once()
    assert app._extra_chars_typed_by_hotkey == 0
    # Single-tap leaves the history alone — user can still double-tap to undo
    # the whole segment afterwards.
    assert app._typed_history == [13]


def test_no_extra_cleanup_for_modifier_or_non_printable_hotkey(app):
    """<delete> doesn't insert chars, so single-tap should not pre-delete."""
    app.settings.set("delete_hotkey", "<delete>")
    app._typed_history = [13]
    with patch.object(app.keyboard_out, "delete_chars") as dc, patch.object(
        app.keyboard_out, "delete_word"
    ) as dw:
        app._on_delete_pressed()
        app._on_delete_single_timeout()
        dc.assert_not_called()
        dw.assert_called_once()


def test_transcription_resets_hotkey_extra_counter(app):
    app._extra_chars_typed_by_hotkey = 7
    with patch.object(app.keyboard_out, "type_text", return_value=10):
        app._on_transcription("hello")
    assert app._extra_chars_typed_by_hotkey == 0


def test_transcription_pushes_to_history_and_copies_clipboard(app):
    from PyQt6.QtWidgets import QApplication

    with patch.object(app.keyboard_out, "type_text", return_value=12) as tt:
        app._on_transcription("hello world")
        tt.assert_called_once_with("hello world")
    assert app._typed_history == [12]
    assert app._last_typed_text == "hello world"
    assert QApplication.clipboard().text() == "hello world"


def test_multiple_transcriptions_stack_in_history(app):
    with patch.object(app.keyboard_out, "type_text", side_effect=[6, 7, 8]):
        app._on_transcription("hello")
        app._on_transcription("world!")
        app._on_transcription("foo bar")
    assert app._typed_history == [6, 7, 8]


def test_history_cap_drops_oldest(app):
    app._typed_history_max = 3
    with patch.object(app.keyboard_out, "type_text", side_effect=[1, 2, 3, 4, 5]):
        for s in ("a", "b", "c", "d", "e"):
            app._on_transcription(s)
    assert app._typed_history == [3, 4, 5]


def test_zero_length_transcription_not_pushed_to_history(app):
    """Empty / failed-typing transcriptions shouldn't pollute the stack."""
    with patch.object(app.keyboard_out, "type_text", return_value=0):
        app._on_transcription("")
    assert app._typed_history == []


def test_start_capture_plays_ready_sound_before_opening_mic(app):
    """The chime fires first, then a QTimer.singleShot defers the mic open."""
    app._model_loaded = True
    app.settings.set("play_ready_sound", True)
    with patch.object(app.sound_player, "play_ready") as p, patch(
        "src.app.QTimer"
    ) as t:
        app._start_capture()
        p.assert_called_once()
        t.singleShot.assert_called_once()


def test_start_capture_skips_chime_when_disabled(app):
    app._model_loaded = True
    app.settings.set("play_ready_sound", False)
    with patch.object(app.sound_player, "play_ready") as p, patch.object(
        app, "_open_mic_stream"
    ) as o:
        app._start_capture()
        p.assert_not_called()
        o.assert_called_once()


def test_stop_capture_plays_stop_sound(app):
    app._is_capturing = True
    with patch.object(app.audio, "stop"), patch.object(
        app.sound_player, "play_stop"
    ) as ps:
        app._stop_capture()
        ps.assert_called_once()


def test_transcription_skips_clipboard_when_disabled(app):
    from PyQt6.QtWidgets import QApplication

    QApplication.clipboard().setText("preexisting")
    app.settings.set("clipboard_enabled", False)
    with patch.object(app.keyboard_out, "type_text", return_value=4):
        app._on_transcription("test")
    assert QApplication.clipboard().text() == "preexisting"
