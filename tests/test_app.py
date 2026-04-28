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

from unittest.mock import MagicMock, patch

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


def test_delete_hotkey_ignored_when_not_capturing(app):
    """When dictation is off, the delete hotkey must NOT trigger our handler.

    pynput is a passive listener so the OS still receives the Delete keystroke
    and acts on it normally. We just don't pile our Ctrl+Backspace on top.
    """
    app._is_capturing = False
    with patch.object(app, "_on_delete_pressed") as h:
        app._on_hotkey_triggered("delete")
    h.assert_not_called()


def test_delete_hotkey_active_when_capturing(app):
    app._is_capturing = True
    with patch.object(app, "_on_delete_pressed") as h:
        app._on_hotkey_triggered("delete")
    h.assert_called_once()


def test_stop_capture_clears_pending_delete_state(app):
    """A half-open double-tap window must not survive into not-capturing
    state — once the gate kicks in, a stale single-tap timer would fire into
    a context where the user has stopped dictating."""
    app._is_capturing = True
    app._delete_pending = True
    app._extra_chars_typed_by_hotkey = 3
    app._delete_timer.start(5000)
    with patch.object(app.audio, "stop"), patch.object(app.sound_player, "play_stop"):
        app._stop_capture()
    assert app._delete_pending is False
    assert app._extra_chars_typed_by_hotkey == 0
    assert not app._delete_timer.isActive()


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
        tt.assert_called_once_with("hello world", target_hwnd=None)
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


# --- Auto-Enter ----------------------------------------------------------

def test_auto_enter_disabled_does_not_arm_timer(app):
    app.settings.set("auto_enter_enabled", False)
    with patch.object(app.keyboard_out, "type_text", return_value=10), patch.object(
        app, "_arm_auto_enter"
    ) as a:
        app._on_transcription("hello")
    a.assert_not_called()


def test_auto_enter_enabled_arms_timer_after_typing(app):
    app.settings.set("auto_enter_enabled", True)
    with patch.object(app.keyboard_out, "type_text", return_value=10), patch.object(
        app, "_arm_auto_enter"
    ) as a:
        app._on_transcription("hello")
    a.assert_called_once()


def test_auto_enter_zero_typed_does_not_arm(app):
    """Empty / failed transcriptions shouldn't trigger auto-Enter."""
    app.settings.set("auto_enter_enabled", True)
    with patch.object(app.keyboard_out, "type_text", return_value=0), patch.object(
        app, "_arm_auto_enter"
    ) as a:
        app._on_transcription("")
    a.assert_not_called()


def test_arm_auto_enter_starts_timer_and_arms_cancel(app):
    app.settings.set("auto_enter_delay_ms", 1500)
    with patch.object(app.hotkey, "arm_cancel_on_any_key") as a, patch.object(
        app._auto_enter_timer, "start"
    ) as s:
        app._arm_auto_enter()
    a.assert_called_once()
    s.assert_called_once_with(1500)


def test_cancel_auto_enter_within_synthetic_window_rearms(app):
    """A 'cancel' that fires during the echo-guard window must re-arm,
    not actually cancel — those events are our own typed keystrokes."""
    import time as time_mod

    app._typing_finished_at = time_mod.monotonic()
    app._auto_enter_timer.start(5000)
    with patch.object(app.hotkey, "arm_cancel_on_any_key") as a:
        app._cancel_auto_enter()
    a.assert_called_once()
    assert app._auto_enter_timer.isActive()
    app._auto_enter_timer.stop()


def test_cancel_auto_enter_outside_synthetic_window_stops_timer(app):
    app._typing_finished_at = 0.0  # long ago
    app._auto_enter_timer.start(5000)
    with patch.object(app.hotkey, "arm_cancel_on_any_key") as a:
        app._cancel_auto_enter()
    a.assert_not_called()
    assert not app._auto_enter_timer.isActive()


def test_auto_enter_timeout_disarms_then_sends_enter(app):
    """Timer fires -> disarm cancel BEFORE Enter so synthetic Enter doesn't loop."""
    with patch.object(app.hotkey, "disarm_cancel") as d, patch.object(
        app.keyboard_out, "send_enter"
    ) as s:
        app._on_auto_enter_timeout()
    # disarm must happen before send_enter.
    d.assert_called_once()
    s.assert_called_once()


def test_tray_toggle_auto_enter_flips_setting_and_label(app):
    """Tray menu item toggles the setting and updates the tray label."""
    app.settings.set("auto_enter_enabled", False)
    with patch.object(app.tray, "set_auto_enter_enabled") as set_label:
        app._toggle_auto_enter()
    assert app.settings.get("auto_enter_enabled") is True
    set_label.assert_called_once_with(True)

    with patch.object(app.tray, "set_auto_enter_enabled") as set_label:
        app._toggle_auto_enter()
    assert app.settings.get("auto_enter_enabled") is False
    set_label.assert_called_once_with(False)


def test_tray_toggle_auto_enter_off_stops_pending_timer(app):
    """If a timer was armed, turning Auto-Enter off mid-window kills it."""
    app.settings.set("auto_enter_enabled", True)
    app._auto_enter_timer.start(5000)
    with patch.object(app.hotkey, "disarm_cancel") as d:
        app._toggle_auto_enter()
    assert not app._auto_enter_timer.isActive()
    d.assert_called_once()


def test_speech_started_cancels_pending_auto_enter(app):
    """New utterance during the auto-Enter window must abort the pending Enter.

    The next finished transcription will arm a fresh timer.
    """
    app._auto_enter_timer.start(5000)
    with patch.object(app.hotkey, "disarm_cancel") as d:
        app._on_speech_started()
    assert not app._auto_enter_timer.isActive()
    d.assert_called_once()


def test_speech_started_noop_when_no_timer_pending(app):
    """If no auto-Enter is pending, speech-onset must NOT touch the cancel hook."""
    assert not app._auto_enter_timer.isActive()
    with patch.object(app.hotkey, "disarm_cancel") as d:
        app._on_speech_started()
    d.assert_not_called()


def test_speech_started_marks_user_is_speaking(app):
    """speech_started must flip the speaking flag so a transcription that
    arrives mid-utterance defers arming the auto-Enter timer."""
    assert app._user_is_speaking is False
    app._on_speech_started()
    assert app._user_is_speaking is True


def test_speech_ended_clears_user_is_speaking(app):
    """speech_ended is the silence-after-speech boundary — flag goes False."""
    app._user_is_speaking = True
    app._on_speech_ended()
    assert app._user_is_speaking is False


def test_transcription_during_active_speech_defers_arming(app):
    """Bug fix: if user resumed speaking while Whisper was transcribing the
    previous segment, _on_transcription must NOT arm the timer — otherwise
    the 3 s window ticks down DURING the user's continued speech and Enter
    fires mid-thought.
    """
    app.settings.set("auto_enter_enabled", True)
    app._user_is_speaking = True  # user resumed talking during transcription
    with patch.object(app.keyboard_out, "type_text", return_value=10), patch.object(
        app, "_arm_auto_enter"
    ) as a:
        app._on_transcription("hello")
    a.assert_not_called()


def test_transcription_after_silence_arms_normally(app):
    """If user has actually fallen silent (speech_ended was observed) before
    the transcription returns, the timer arms normally."""
    app.settings.set("auto_enter_enabled", True)
    app._user_is_speaking = False
    with patch.object(app.keyboard_out, "type_text", return_value=10), patch.object(
        app, "_arm_auto_enter"
    ) as a:
        app._on_transcription("hello")
    a.assert_called_once()


def test_resume_then_silence_then_transcription_arms(app):
    """Full repro of the bug scenario: resume during transcription #1 keeps
    the timer disarmed, but once the user falls silent again and
    transcription #2 arrives, that one DOES arm."""
    app.settings.set("auto_enter_enabled", True)

    # Transcription #1 lands while user is mid-resumed-speech.
    app._on_speech_started()
    assert app._user_is_speaking is True
    with patch.object(app.keyboard_out, "type_text", return_value=10), patch.object(
        app, "_arm_auto_enter"
    ) as a1:
        app._on_transcription("first part")
    a1.assert_not_called()

    # User finishes resumed utterance — silence detected.
    app._on_speech_ended()
    assert app._user_is_speaking is False

    # Transcription #2 lands after the user has fallen silent.
    with patch.object(app.keyboard_out, "type_text", return_value=10), patch.object(
        app, "_arm_auto_enter"
    ) as a2:
        app._on_transcription("second part")
    a2.assert_called_once()


def test_stop_capture_resets_user_is_speaking(app):
    """Manual stop mid-utterance must clear the speaking flag — otherwise an
    in-flight transcription arriving after stop would be skipped forever."""
    app._is_capturing = True
    app._user_is_speaking = True
    with patch.object(app.audio, "stop"), patch.object(app.sound_player, "play_stop"):
        app._stop_capture()
    assert app._user_is_speaking is False


# --- Continuation detection ('Treat short pauses as commas') -------------

def test_continuation_disabled_no_demote_on_resume(app):
    """Default off: speech_started after a period-ending segment must NOT
    backspace-and-rewrite, even within the window."""
    import time as time_mod

    app.settings.set("continuation_detection_enabled", False)
    app._last_segment_ended_with_period = True
    app._typing_finished_at = time_mod.monotonic()
    with patch.object(app.keyboard_out, "replace_last_period_with_comma") as r:
        app._on_speech_started()
    r.assert_not_called()
    assert app._continuation_pending is False


def test_continuation_enabled_demotes_period_within_window(app):
    """Enabled + within window + period-ending segment -> demote to comma."""
    import time as time_mod

    app.settings.set("continuation_detection_enabled", True)
    app.settings.set("continuation_window_ms", 600)
    app.settings.set("trailing_space", True)
    app._last_segment_ended_with_period = True
    app._typing_finished_at = time_mod.monotonic()
    with patch.object(app.keyboard_out, "replace_last_period_with_comma") as r:
        app._on_speech_started()
    r.assert_called_once_with(True)
    assert app._continuation_pending is True
    assert app._last_segment_ended_with_period is False


def test_continuation_outside_window_does_not_demote(app):
    """Resume after the configured window -> period stays."""
    app.settings.set("continuation_detection_enabled", True)
    app.settings.set("continuation_window_ms", 500)
    app._last_segment_ended_with_period = True
    app._typing_finished_at = 0.0  # long ago
    with patch.object(app.keyboard_out, "replace_last_period_with_comma") as r:
        app._on_speech_started()
    r.assert_not_called()
    assert app._continuation_pending is False


def test_continuation_no_period_does_not_demote(app):
    """If the previous segment didn't end in '.', nothing to demote."""
    import time as time_mod

    app.settings.set("continuation_detection_enabled", True)
    app._last_segment_ended_with_period = False
    app._typing_finished_at = time_mod.monotonic()
    with patch.object(app.keyboard_out, "replace_last_period_with_comma") as r:
        app._on_speech_started()
    r.assert_not_called()


def test_continuation_pending_lowercases_next_segment(app):
    """After demote, the next transcription's first letter is lowercased."""
    app.settings.set("continuation_detection_enabled", True)
    app.settings.set("auto_enter_enabled", False)
    app._continuation_pending = True
    app._user_is_speaking = False
    with patch.object(app.keyboard_out, "type_text", return_value=10) as t:
        app._on_transcription("World today")
    t.assert_called_once_with("world today", target_hwnd=None)
    assert app._continuation_pending is False


def test_continuation_pending_skipped_when_disabled(app):
    """Stale flag must not affect output if the feature is now disabled."""
    app.settings.set("continuation_detection_enabled", False)
    app._continuation_pending = True
    with patch.object(app.keyboard_out, "type_text", return_value=10) as t:
        app._on_transcription("World today")
    t.assert_called_once_with("World today", target_hwnd=None)  # untouched


def test_in_text_demote_when_user_still_speaking(app):
    """If transcription returns while user_is_speaking=True, demote the
    trailing '.' BEFORE typing and mark next segment as continuation."""
    app.settings.set("continuation_detection_enabled", True)
    app.settings.set("auto_enter_enabled", False)
    app._user_is_speaking = True
    app._continuation_pending = False
    with patch.object(app.keyboard_out, "type_text", return_value=10) as t:
        app._on_transcription("Hello world.")
    t.assert_called_once_with("Hello world,", target_hwnd=None)
    assert app._continuation_pending is True


def test_ellipsis_not_demoted(app):
    """Text ending with '...' is left alone — that's not a sentence end to
    rewrite, that's intentional ellipsis."""
    app.settings.set("continuation_detection_enabled", True)
    app._user_is_speaking = True
    with patch.object(app.keyboard_out, "type_text", return_value=10) as t:
        app._on_transcription("Wait...")
    t.assert_called_once_with("Wait...", target_hwnd=None)  # untouched


def test_question_mark_not_demoted(app):
    """'?' is a stronger sentence ender than '.' — leave it alone."""
    app.settings.set("continuation_detection_enabled", True)
    app._user_is_speaking = True
    with patch.object(app.keyboard_out, "type_text", return_value=10) as t:
        app._on_transcription("Really?")
    t.assert_called_once_with("Really?", target_hwnd=None)


def test_last_segment_period_flag_tracked_across_transcriptions(app):
    """After typing a '.'-ending segment, flag goes True; after a non-'.'
    segment, flag goes False."""
    app.settings.set("auto_enter_enabled", False)
    app._user_is_speaking = False
    app._continuation_pending = False
    with patch.object(app.keyboard_out, "type_text", return_value=10):
        app._on_transcription("Hello world.")
    assert app._last_segment_ended_with_period is True
    with patch.object(app.keyboard_out, "type_text", return_value=10):
        app._on_transcription("then nothing")
    assert app._last_segment_ended_with_period is False


def test_auto_enter_timeout_clears_continuation_state(app):
    """Once Enter is pressed the cursor moved past the previous segment;
    any pending continuation context is moot."""
    app._last_segment_ended_with_period = True
    app._continuation_pending = True
    with patch.object(app.hotkey, "disarm_cancel"), patch.object(
        app.keyboard_out, "send_enter"
    ):
        app._on_auto_enter_timeout()
    assert app._last_segment_ended_with_period is False
    assert app._continuation_pending is False


def test_stop_capture_clears_continuation_state(app):
    app._is_capturing = True
    app._last_segment_ended_with_period = True
    app._continuation_pending = True
    with patch.object(app.audio, "stop"), patch.object(app.sound_player, "play_stop"):
        app._stop_capture()
    assert app._last_segment_ended_with_period is False
    assert app._continuation_pending is False


def test_double_tap_delete_clears_continuation_state(app):
    """Popping a typed segment off the stack invalidates the continuation
    context — that period is gone now."""
    app._typed_history = [11]
    app._last_segment_ended_with_period = True
    app._continuation_pending = True
    app._delete_pending = True
    with patch.object(app.keyboard_out, "delete_chars"):
        app._on_delete_pressed()
    assert app._last_segment_ended_with_period is False
    assert app._continuation_pending is False


def test_single_tap_delete_clears_continuation_state(app):
    app._delete_pending = True
    app._last_segment_ended_with_period = True
    app._continuation_pending = True
    with patch.object(app.keyboard_out, "delete_word"), patch.object(
        app.keyboard_out, "delete_chars"
    ):
        app._on_delete_single_timeout()
    assert app._last_segment_ended_with_period is False
    assert app._continuation_pending is False


# --- Voice hotkey + tray wiring -----------------------------------------

def test_voice_hotkey_in_mapping_only_when_enabled(app):
    app.settings.set("voice_enabled", False)
    app.settings.set("voice_interrupt_hotkey", "<ctrl>+<alt>+s")
    assert "voice_interrupt" not in app._build_hotkey_mapping()

    app.settings.set("voice_enabled", True)
    assert app._build_hotkey_mapping().get("voice_interrupt") == "<ctrl>+<alt>+s"


def test_voice_hotkey_dropped_when_clashes_with_toggle_or_delete(app):
    app.settings.set("voice_enabled", True)
    app.settings.set("hotkey", "<alt>+z")
    app.settings.set("delete_hotkey", "<delete>")
    app.settings.set("voice_interrupt_hotkey", "<alt>+z")  # clashes with toggle
    assert "voice_interrupt" not in app._build_hotkey_mapping()
    app.settings.set("voice_interrupt_hotkey", "<delete>")  # clashes with delete
    assert "voice_interrupt" not in app._build_hotkey_mapping()
    app.settings.set("voice_interrupt_hotkey", "<ctrl>+<alt>+s")  # unique
    assert "voice_interrupt" in app._build_hotkey_mapping()


def test_voice_interrupt_hotkey_calls_tts_interrupt(app):
    with patch.object(app.tts, "interrupt") as i:
        app._on_hotkey_triggered("voice_interrupt")
    i.assert_called_once()


def test_tray_toggle_voice_flips_setting_and_label(app):
    app.settings.set("voice_enabled", False)
    with patch.object(app.tray, "set_voice_enabled") as set_label, patch.object(
        app.voice_ipc, "start"
    ) as ipc_start, patch.object(app.hotkey, "update_mapping"):
        app._toggle_voice()
    assert app.settings.get("voice_enabled") is True
    set_label.assert_called_once_with(True)
    ipc_start.assert_called_once()


def test_tray_toggle_voice_off_stops_ipc_and_interrupts_tts(app):
    app.settings.set("voice_enabled", True)
    app.voice_ipc._httpd = MagicMock()  # simulate "running"
    with patch.object(app.voice_ipc, "stop") as ipc_stop, patch.object(
        app.tts, "interrupt"
    ) as ti, patch.object(app.tray, "set_voice_enabled"), patch.object(
        app.hotkey, "update_mapping"
    ):
        app._toggle_voice()
    assert app.settings.get("voice_enabled") is False
    ipc_stop.assert_called_once()
    ti.assert_called_once()


def test_tray_interrupt_voice_calls_tts_interrupt(app):
    with patch.object(app.tts, "interrupt") as i:
        app._interrupt_voice()
    i.assert_called_once()


def test_full_continuation_flow_end_to_end(app):
    """Type 'Hello world.' -> resume within window -> next segment 'But I
    changed my mind.' -> result types as 'Hello world,' (via post-hoc
    demote) and 'but I changed my mind.' (via continuation_pending)."""
    import time as time_mod

    app.settings.set("continuation_detection_enabled", True)
    app.settings.set("continuation_window_ms", 600)
    app.settings.set("auto_enter_enabled", False)
    app.settings.set("trailing_space", True)

    # Segment #1 typed.
    with patch.object(app.keyboard_out, "type_text", return_value=12):
        app._on_transcription("Hello world.")
    assert app._last_segment_ended_with_period is True
    app._typing_finished_at = time_mod.monotonic()  # ensure 'now'

    # User resumes within the window — period demoted, continuation pending.
    with patch.object(app.keyboard_out, "replace_last_period_with_comma") as r:
        app._on_speech_started()
    r.assert_called_once_with(True)
    assert app._continuation_pending is True

    # User pauses (silence detected) — speaking flag clears.
    app._on_speech_ended()
    assert app._user_is_speaking is False

    # Segment #2 typed — first letter lowercased due to continuation.
    with patch.object(app.keyboard_out, "type_text", return_value=10) as t:
        app._on_transcription("But I changed my mind.")
    t.assert_called_once_with("but I changed my mind.", target_hwnd=None)
    assert app._continuation_pending is False
    assert app._last_segment_ended_with_period is True


def test_transcription_skips_clipboard_when_disabled(app):
    from PyQt6.QtWidgets import QApplication

    QApplication.clipboard().setText("preexisting")
    app.settings.set("clipboard_enabled", False)
    with patch.object(app.keyboard_out, "type_text", return_value=4):
        app._on_transcription("test")
    assert QApplication.clipboard().text() == "preexisting"


# --- Paste-target lock wiring -------------------------------------------

def test_lock_toggle_hotkey_in_mapping_only_when_enabled(app):
    app.settings.set("paste_lock_enabled", False)
    assert "lock_toggle" not in app._build_hotkey_mapping()

    app.settings.set("paste_lock_enabled", True)
    assert app._build_hotkey_mapping().get("lock_toggle") == "<alt>+l"


def test_lock_toggle_hotkey_dropped_when_clashes(app):
    app.settings.set("paste_lock_enabled", True)
    app.settings.set("hotkey", "<alt>+z")
    app.settings.set("delete_hotkey", "<delete>")
    app.settings.set("paste_lock_hotkey", "<alt>+z")
    assert "lock_toggle" not in app._build_hotkey_mapping()
    app.settings.set("paste_lock_hotkey", "<delete>")
    assert "lock_toggle" not in app._build_hotkey_mapping()
    app.settings.set("paste_lock_hotkey", "<alt>+l")
    assert "lock_toggle" in app._build_hotkey_mapping()


def test_open_mic_stream_calls_paste_target_on_dictation_started(app):
    """_open_mic_stream is the actual capture-start point (start_capture
    defers to it via QTimer when ready chime is enabled)."""
    with patch.object(app.audio, "start"), \
         patch.object(app.paste_target, "on_dictation_started") as ds:
        app._open_mic_stream()
    ds.assert_called_once()


def test_stop_capture_calls_paste_target_on_dictation_stopped(app):
    app._is_capturing = True
    with patch.object(app.audio, "stop"), \
         patch.object(app.sound_player, "play_stop"), \
         patch.object(app.paste_target, "on_dictation_stopped") as ds:
        app._stop_capture()
    ds.assert_called_once()


def test_lock_toggle_hotkey_calls_controller_toggle(app):
    with patch.object(app.paste_target, "toggle_sticky") as t:
        app._on_hotkey_triggered("lock_toggle")
    t.assert_called_once()


def test_transcription_passes_target_hwnd_when_locked(app):
    """current_target() result is forwarded into keyboard_out.type_text."""
    app.settings.set("paste_lock_enabled", True)
    app.paste_target._sticky_hwnd = 4242
    with patch.object(app.keyboard_out, "type_text", return_value=10) as tt:
        app._on_transcription("hello")
    tt.assert_called_once()
    target = tt.call_args.kwargs.get("target_hwnd")
    if target is None and len(tt.call_args.args) >= 2:
        target = tt.call_args.args[1]
    assert target == 4242


def test_transcription_passes_none_when_no_lock(app):
    app.settings.set("paste_lock_enabled", True)
    with patch.object(app.keyboard_out, "type_text", return_value=10) as tt:
        app._on_transcription("hello")
    target = tt.call_args.kwargs.get("target_hwnd")
    if target is None and len(tt.call_args.args) >= 2:
        target = tt.call_args.args[1]
    assert target is None


def test_lock_changed_sticky_to_hwnd_shows_border_and_plays_lock(app):
    app.settings.set("paste_lock_enabled", True)
    with patch.object(app.border_overlay, "set_target_hwnd") as bord, \
         patch.object(app.sound_player, "play_lock") as plk, \
         patch.object(app.sound_player, "play_unlock") as pul:
        app._on_lock_changed(4242, "sticky")
    bord.assert_called_once_with(4242)
    plk.assert_called_once()
    pul.assert_not_called()


def test_lock_changed_sticky_to_none_hides_border_and_plays_unlock(app):
    app.settings.set("paste_lock_enabled", True)
    app._last_sticky_hwnd = 4242
    with patch.object(app.border_overlay, "set_target_hwnd") as bord, \
         patch.object(app.sound_player, "play_lock") as plk, \
         patch.object(app.sound_player, "play_unlock") as pul:
        app._on_lock_changed(None, "none")
    bord.assert_called_once_with(None)
    pul.assert_called_once()
    plk.assert_not_called()


def test_lock_changed_session_does_not_touch_border_or_sound(app):
    """Per-session captures are silent and borderless."""
    app.settings.set("paste_lock_enabled", True)
    with patch.object(app.border_overlay, "set_target_hwnd") as bord, \
         patch.object(app.sound_player, "play_lock") as plk, \
         patch.object(app.sound_player, "play_unlock") as pul:
        app._on_lock_changed(99, "session")
    bord.assert_not_called()
    plk.assert_not_called()
    pul.assert_not_called()


def test_target_invalid_closed_notifies_and_clears_silently(app):
    app.settings.set("notifications_enabled", True)
    app.paste_target._sticky_hwnd = 4242
    with patch.object(app.tray, "notify") as n, \
         patch.object(app.paste_target, "clear_sticky_silently") as cs:
        app._on_target_invalid("closed")
    n.assert_called_once()
    cs.assert_called_once()


def test_app_wires_tts_signals_to_mic_muter(app):
    """speak_started → mic_muter.on_tts_started; speak_finished → on_tts_finished."""
    on_start = MagicMock()
    on_end = MagicMock()
    app.tts.speak_started.connect(on_start)
    app.tts.speak_finished.connect(on_end)
    # Sanity: the muter is wired and reacts to the emits too.
    app.mic_muter._is_muted = False
    app.tts.speak_started.emit()
    assert app.mic_muter.is_muted is True
    on_start.assert_called_once()
    app.tts.speak_finished.emit()
    on_end.assert_called_once()
    # speak_finished should have armed the resume timer (proof the slot ran).
    assert app.mic_muter._resume_timer.isActive()


def test_voice_interrupt_hotkey_flushes_audio(app):
    """Pressing voice_interrupt cuts TTS AND flushes any in-flight audio
    so a sliver of TTS that leaked into the mic before auto-mute kicked
    in cannot become a paste."""
    from unittest.mock import patch
    with patch.object(app.tts, "interrupt") as ti, \
         patch.object(app.audio, "flush") as af:
        app._on_hotkey_triggered("voice_interrupt")
    ti.assert_called_once()
    af.assert_called_once()
