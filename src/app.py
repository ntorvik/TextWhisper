"""Main application wiring: tray, hotkey, audio, transcription, output."""

from __future__ import annotations

import contextlib
import logging
import time

from PyQt6.QtCore import QObject, Qt, QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox

from .audio_capture import AudioCapture
from .hotkey_manager import HotkeyManager, chars_inserted_per_press
from .keyboard_output import KeyboardOutput
from .settings_manager import SettingsManager
from .sound_player import SoundPlayer
from .transcription import TranscriptionEngine
from .ui.oscilloscope import OscilloscopeWidget
from .ui.settings_dialog import SettingsDialog
from .ui.tray import TrayController
from .voice import TTSService

log = logging.getLogger(__name__)


class TextWhisperApp(QObject):
    def __init__(self, qapp: QApplication) -> None:
        super().__init__()
        self.qapp = qapp

        self.settings = SettingsManager()

        self.audio = AudioCapture(self.settings)
        self.engine = TranscriptionEngine(self.settings)
        self.keyboard_out = KeyboardOutput(self.settings)
        self.sound_player = SoundPlayer(self.settings)
        self.hotkey = HotkeyManager(self._build_hotkey_mapping())

        self.tray = TrayController(parent=self)
        self.oscilloscope = OscilloscopeWidget(self.settings)
        self.tts = TTSService(self.settings)

        self._is_capturing = False
        self._model_loaded = False

        # Stack of typed-segment lengths. Each transcription pushes its char
        # count; each double-tap delete pops the most recent and erases that
        # many characters. Lets the user keep double-tapping back through
        # earlier segments as long as the focused window's content matches.
        self._typed_history: list[int] = []
        self._typed_history_max = 50  # cap so it can't grow unbounded
        self._last_typed_text = ""

        # Stray characters the delete hotkey itself inserts into the focused
        # window (e.g. when the hotkey is "+" or any other printable key).
        # Incremented on every delete-press, consumed on cleanup.
        self._extra_chars_typed_by_hotkey = 0

        # Double-tap detection for the delete hotkey.
        self._delete_pending = False
        self._delete_timer = QTimer(self)
        self._delete_timer.setSingleShot(True)
        self._delete_timer.timeout.connect(self._on_delete_single_timeout)

        # Watchdog: if the pynput listener thread dies (e.g. an internal
        # exception while processing a synthetic Controller event), revive
        # it so hotkeys keep working.
        self._hotkey_watchdog = QTimer(self)
        self._hotkey_watchdog.setInterval(2000)
        self._hotkey_watchdog.timeout.connect(self._check_hotkey_health)

        # Auto-Enter: timer + state.
        self._auto_enter_timer = QTimer(self)
        self._auto_enter_timer.setSingleShot(True)
        self._auto_enter_timer.timeout.connect(self._on_auto_enter_timeout)
        # Wall-clock timestamp of the most recent transcription typing —
        # used to ignore "any key" cancel events caused by our own synthetic
        # keystrokes echoing back through the listener.
        self._typing_finished_at = 0.0
        self._SYNTHETIC_ECHO_GUARD_S = 0.15
        # True between an audio.speech_started and the next audio.speech_ended.
        # If a transcription completes while this is True, the user resumed
        # talking during transcription latency — defer arming the auto-Enter
        # timer until they actually fall silent again (the next transcription
        # cycle will arm it).
        self._user_is_speaking = False
        # Continuation detection ("Treat short pauses as commas"). Whisper
        # transcribes each VAD-cut segment in isolation and reflexively ends
        # each one with '.'. When the user resumes speaking quickly we
        # retroactively demote that '.' to ',' and lowercase the next segment.
        self._last_segment_ended_with_period = False
        self._continuation_pending = False

        self._wire_signals()

        if bool(self.settings.get("oscilloscope.enabled", True)):
            self.oscilloscope.show()
        self.tray.set_oscilloscope_visible(self.oscilloscope.isVisible())
        self.tray.set_auto_enter_enabled(
            bool(self.settings.get("auto_enter_enabled", False))
        )

    # --- lifecycle -----------------------------------------------------

    def run(self) -> None:
        self.engine.start()
        self.hotkey.start()
        self._hotkey_watchdog.start()
        first_run = self._is_first_run()
        if first_run:
            QMessageBox.information(
                None,
                "TextWhisper — First-time setup",
                "Welcome to TextWhisper.\n\n"
                "On first launch the Whisper speech-recognition model is "
                "downloaded automatically (~1.5 GB for the default 'large-v3' "
                "model). This is a one-time download — subsequent launches "
                "load in seconds.\n\n"
                "The download happens in the background after you click OK. "
                "The microphone tray icon will turn its 'Ready' colour and a "
                "soft chime will play when the model has finished loading and "
                "TextWhisper is ready to use.",
            )
        self._notify(
            "TextWhisper",
            f"Press {self.settings.get('hotkey')} to dictate. Loading Whisper model...",
        )

    def _is_first_run(self) -> bool:
        """True iff this is the first time TextWhisper has launched on this machine.

        Marker file lives in the same directory as ``config.json``.
        """
        cfg_path = self.settings.config_path
        marker = cfg_path.parent / ".welcome_shown"
        if marker.exists():
            return False
        with contextlib.suppress(OSError):
            marker.write_text("ok", encoding="utf-8")
        return True

    def _notify(self, title: str, message: str, *, error: bool = False) -> None:
        """Tray balloon, suppressed when ``notifications_enabled`` is False."""
        if not bool(self.settings.get("notifications_enabled", True)):
            return
        self.tray.notify(title, message, error=error)

    def quit(self) -> None:
        try:
            self._hotkey_watchdog.stop()
            self._auto_enter_timer.stop()
            self.hotkey.disarm_cancel()
            if self._is_capturing:
                self._stop_capture()
            self.hotkey.stop()
            self.engine.stop()
            self.tts.shutdown()
        finally:
            self.qapp.quit()

    def _check_hotkey_health(self) -> None:
        """Called every 2 s by the watchdog timer."""
        if self.hotkey.restart_if_dead():
            log.warning("Watchdog: hotkey listener was dead, revived it.")

    # --- signal wiring -------------------------------------------------

    def _build_hotkey_mapping(self) -> dict[str, str]:
        m: dict[str, str] = {}
        toggle = str(self.settings.get("hotkey", "<alt>+z") or "").strip()
        if toggle:
            m["toggle"] = toggle
        delete_hk = str(self.settings.get("delete_hotkey", "<delete>") or "").strip()
        if delete_hk and delete_hk != toggle:
            m["delete"] = delete_hk
        return m

    def _wire_signals(self) -> None:
        self.hotkey.triggered.connect(self._on_hotkey_triggered, Qt.ConnectionType.QueuedConnection)
        self.hotkey.error.connect(self._on_hotkey_error)

        self.audio.audio_level.connect(
            self.oscilloscope.push_audio, Qt.ConnectionType.QueuedConnection
        )
        self.audio.segment_ready.connect(
            self.engine.submit, Qt.ConnectionType.QueuedConnection
        )
        self.audio.speech_started.connect(
            self._on_speech_started, Qt.ConnectionType.QueuedConnection
        )
        self.audio.speech_ended.connect(
            self._on_speech_ended, Qt.ConnectionType.QueuedConnection
        )
        self.audio.error.connect(self._on_audio_error)

        self.engine.transcription_ready.connect(self._on_transcription)
        self.engine.error.connect(self._on_engine_error)
        self.engine.model_loading.connect(self._on_model_loading)
        self.engine.model_ready.connect(self._on_model_ready)

        self.tray.toggle_capture.connect(self._toggle_capture)
        self.tray.show_settings.connect(self._open_settings)
        self.tray.toggle_oscilloscope.connect(self._toggle_oscilloscope)
        self.tray.toggle_auto_enter.connect(self._toggle_auto_enter)
        self.tray.quit_requested.connect(self.quit)

    # --- capture control -----------------------------------------------

    def _toggle_capture(self) -> None:
        if self._is_capturing:
            self._stop_capture()
        else:
            self._start_capture()

    def _start_capture(self) -> None:
        if not self._model_loaded:
            self._notify(
                "TextWhisper",
                "Whisper model is still loading. Try again in a moment.",
            )
            return
        # Play the "ready" chime FIRST, then open the microphone after the
        # tone has finished. This way the mic never picks up our own tone.
        if bool(self.settings.get("play_ready_sound", True)):
            self.sound_player.play_ready()
            wait_ms = self.sound_player.ready_duration_ms + 30
            QTimer.singleShot(wait_ms, self._open_mic_stream)
        else:
            self._open_mic_stream()

    def _open_mic_stream(self) -> None:
        try:
            self.audio.start()
        except Exception as e:
            QMessageBox.warning(None, "TextWhisper", f"Could not start microphone: {e}")
            return
        self._is_capturing = True
        self.tray.set_active(True)
        self.oscilloscope.set_active(True)

    def _stop_capture(self) -> None:
        self.audio.stop()
        self._is_capturing = False
        # Capture is off — user is no longer "speaking" from this app's POV,
        # even if AudioCapture was force-flushed mid-utterance and never
        # emitted speech_ended. Without this reset, an in-flight transcription
        # arriving after stop would be treated as "still speaking" and skip
        # arming auto-Enter.
        self._user_is_speaking = False
        # Continuation context dies with the capture session.
        self._last_segment_ended_with_period = False
        self._continuation_pending = False
        self.tray.set_active(False)
        self.oscilloscope.set_active(False)
        self.oscilloscope.clear()
        self.sound_player.play_stop()

    # --- oscilloscope toggle ------------------------------------------

    def _toggle_oscilloscope(self) -> None:
        if self.oscilloscope.isVisible():
            self.oscilloscope.hide()
            self.settings.set("oscilloscope.enabled", False)
        else:
            self.oscilloscope.show()
            self.settings.set("oscilloscope.enabled", True)
        self.tray.set_oscilloscope_visible(self.oscilloscope.isVisible())

    def _toggle_auto_enter(self) -> None:
        new_state = not bool(self.settings.get("auto_enter_enabled", False))
        self.settings.set("auto_enter_enabled", new_state)
        self.tray.set_auto_enter_enabled(new_state)
        # If we're turning it OFF mid-pending, stop the live timer too.
        if not new_state and self._auto_enter_timer.isActive():
            self._auto_enter_timer.stop()
            self.hotkey.disarm_cancel()
        log.info("Auto-Enter %s via tray.", "enabled" if new_state else "disabled")

    # --- settings ------------------------------------------------------

    def _open_settings(self) -> None:
        prev_hotkey = self.settings.get("hotkey")
        prev_delete_hotkey = self.settings.get("delete_hotkey")
        prev_model = self.settings.get("model_size")
        prev_device = self.settings.get("device")
        prev_compute = self.settings.get("compute_type")
        prev_mic = self.settings.get("microphone_device")

        dlg = SettingsDialog(self.settings, tts=self.tts)
        if not dlg.exec():
            return

        if (
            self.settings.get("hotkey") != prev_hotkey
            or self.settings.get("delete_hotkey") != prev_delete_hotkey
        ):
            self.hotkey.update_mapping(self._build_hotkey_mapping())

        engine_dirty = (
            self.settings.get("model_size") != prev_model
            or self.settings.get("device") != prev_device
            or self.settings.get("compute_type") != prev_compute
        )
        if engine_dirty:
            self._reload_engine()

        if self.settings.get("microphone_device") != prev_mic and self._is_capturing:
            self._stop_capture()
            self._start_capture()

        self.oscilloscope.apply_size_from_settings()
        self.oscilloscope.apply_opacity()
        self.oscilloscope.apply_color_settings()
        self.oscilloscope.apply_shape_settings()

        # Tray label for the Auto-Enter toggle reflects whatever was saved.
        self.tray.set_auto_enter_enabled(
            bool(self.settings.get("auto_enter_enabled", False))
        )

        if bool(self.settings.get("oscilloscope.enabled", True)):
            if not self.oscilloscope.isVisible():
                self.oscilloscope.show()
        elif self.oscilloscope.isVisible():
            self.oscilloscope.hide()
        self.tray.set_oscilloscope_visible(self.oscilloscope.isVisible())

    def _reload_engine(self) -> None:
        with contextlib.suppress(TypeError, RuntimeError):
            self.audio.segment_ready.disconnect(self.engine.submit)
        self._model_loaded = False
        self.engine.stop()
        self.engine = TranscriptionEngine(self.settings)
        self.audio.segment_ready.connect(
            self.engine.submit, Qt.ConnectionType.QueuedConnection
        )
        self.engine.transcription_ready.connect(self._on_transcription)
        self.engine.error.connect(self._on_engine_error)
        self.engine.model_loading.connect(self._on_model_loading)
        self.engine.model_ready.connect(self._on_model_ready)
        self.engine.start()

    # --- hotkey dispatch ----------------------------------------------

    def _on_hotkey_triggered(self, name: str) -> None:
        log.info("Hotkey triggered: %s", name)
        if name == "toggle":
            self._toggle_capture()
        elif name == "delete":
            self._on_delete_pressed()
        else:
            log.warning("Unknown hotkey: %s", name)

    def _on_delete_pressed(self) -> None:
        # If the delete hotkey is a printable key (e.g. "+"), each press also
        # inserts that char into the focused window because pynput's hotkey
        # listener does not suppress events. Track those so we can clean them
        # up before/alongside the intended action.
        n_per_tap = chars_inserted_per_press(
            str(self.settings.get("delete_hotkey", ""))
        )
        self._extra_chars_typed_by_hotkey += n_per_tap

        log.info(
            "Delete press: pending=%s, history_depth=%d, extra_from_hotkey=%d",
            self._delete_pending,
            len(self._typed_history),
            self._extra_chars_typed_by_hotkey,
        )
        if self._delete_pending:
            # Second tap within window -> pop the most recent segment and
            # erase its char count plus the stray hotkey chars from this cycle.
            self._delete_timer.stop()
            self._delete_pending = False
            segment_len = self._typed_history.pop() if self._typed_history else 0
            n = segment_len + self._extra_chars_typed_by_hotkey
            if n > 0:
                log.info(
                    "Double-tap delete: erasing %d chars "
                    "(segment=%d + hotkey_extras=%d). %d earlier segment(s) remain.",
                    n,
                    segment_len,
                    self._extra_chars_typed_by_hotkey,
                    len(self._typed_history),
                )
                self.keyboard_out.delete_chars(n)
                self._extra_chars_typed_by_hotkey = 0
            else:
                log.info(
                    "Double-tap delete: history empty — nothing to erase. "
                    "(Earlier transcriptions were already removed or were never tracked.)"
                )
            # Manual edit invalidates any pending continuation.
            self._last_segment_ended_with_period = False
            self._continuation_pending = False
            return
        # Defer single-tap action so a second press can upgrade to double-tap.
        self._delete_pending = True
        wait_ms = max(100, int(self.settings.get("delete_double_tap_ms", 350)))
        log.info("First-tap delete: scheduling word-delete in %d ms.", wait_ms)
        self._delete_timer.start(wait_ms)

    def _on_delete_single_timeout(self) -> None:
        if not self._delete_pending:
            log.info("Delete single-tap timeout fired but not pending — already handled.")
            return
        self._delete_pending = False
        # First clean up the stray char(s) the hotkey itself typed.
        if self._extra_chars_typed_by_hotkey > 0:
            log.info(
                "Single-tap delete: cleaning up %d hotkey-typed char(s) before word delete.",
                self._extra_chars_typed_by_hotkey,
            )
            self.keyboard_out.delete_chars(self._extra_chars_typed_by_hotkey)
            self._extra_chars_typed_by_hotkey = 0
        log.info("Single-tap delete: erasing previous word.")
        self.keyboard_out.delete_word()
        # Manual edit invalidates any pending continuation.
        self._last_segment_ended_with_period = False
        self._continuation_pending = False

    # --- engine + audio callbacks --------------------------------------

    def _on_transcription(self, text: str) -> None:
        # Continuation: previous segment's '.' was demoted to ',' on resume.
        # Lowercase this segment's first letter so the result reads as one
        # flowing sentence ("...world, but I changed my mind.").
        if self._continuation_enabled() and self._continuation_pending and text:
            if text[0].isupper():
                text = text[0].lower() + text[1:]
            self._continuation_pending = False
        # In-text demotion: if Whisper returned this segment WHILE the user
        # was still actively speaking (resumed during transcription latency),
        # the trailing '.' is spurious. Replace it before typing and mark the
        # NEXT segment as a continuation too.
        if (
            self._continuation_enabled()
            and self._user_is_speaking
            and self._ends_with_single_period(text)
        ):
            text = text[:-1] + ","
            self._continuation_pending = True
            log.info("Continuation in-text — segment '.' demoted before typing.")

        typed = self.keyboard_out.type_text(text)
        if typed > 0:
            self._typed_history.append(typed)
            if len(self._typed_history) > self._typed_history_max:
                self._typed_history = self._typed_history[-self._typed_history_max:]
            self._last_segment_ended_with_period = self._ends_with_single_period(text)
        self._last_typed_text = text
        # New dictation -> reset the hotkey-stray-char counter so subsequent
        # delete presses are scoped to this segment.
        self._extra_chars_typed_by_hotkey = 0
        # Mark when we finished typing, for the auto-Enter synthetic-echo guard.
        self._typing_finished_at = time.monotonic()
        log.info(
            "Transcription typed: chars=%d, history_depth=%d",
            typed,
            len(self._typed_history),
        )
        if bool(self.settings.get("clipboard_enabled", True)):
            try:
                QApplication.clipboard().setText(text)
                log.info("Copied transcription to clipboard (%d chars).", len(text))
            except Exception:
                log.exception("Clipboard write failed")
        # Hands-free auto-Enter: arm a timer and a one-shot any-key cancel.
        # If the user resumed speaking while Whisper was transcribing this
        # segment, defer — the next transcription cycle (after they actually
        # fall silent again) will arm the timer instead. Otherwise the 3 s
        # window would tick down DURING the user's continued speech.
        if typed > 0 and bool(self.settings.get("auto_enter_enabled", False)):
            if self._user_is_speaking:
                log.info(
                    "Auto-Enter NOT armed — user resumed speaking during "
                    "transcription; deferring until they fall silent again."
                )
            else:
                self._arm_auto_enter()

    # --- auto-Enter ----------------------------------------------------

    def _arm_auto_enter(self) -> None:
        delay_ms = max(200, int(self.settings.get("auto_enter_delay_ms", 3000)))
        self._auto_enter_timer.stop()
        self.hotkey.arm_cancel_on_any_key(self._cancel_auto_enter)
        self._auto_enter_timer.start(delay_ms)
        log.info("Auto-Enter armed: pressing Enter in %d ms unless cancelled.", delay_ms)

    def _cancel_auto_enter(self) -> None:
        # Ignore "cancel" events that fire within the synthetic-echo guard
        # window — those are almost certainly our own typed keystrokes
        # bouncing back through the listener. Re-arm so the next genuine
        # keypress still cancels.
        if (time.monotonic() - self._typing_finished_at) < self._SYNTHETIC_ECHO_GUARD_S:
            self.hotkey.arm_cancel_on_any_key(self._cancel_auto_enter)
            return
        if self._auto_enter_timer.isActive():
            self._auto_enter_timer.stop()
            log.info("Auto-Enter cancelled by user keypress.")

    def _on_auto_enter_timeout(self) -> None:
        # Disarm cancel BEFORE sending Enter so our own Enter doesn't trip
        # a subsequent (already disarmed) cancel.
        self.hotkey.disarm_cancel()
        self.keyboard_out.send_enter()
        # The cursor has moved past the previous segment's trailing period,
        # so any pending continuation is moot.
        self._last_segment_ended_with_period = False
        self._continuation_pending = False
        log.info("Auto-Enter fired.")

    def _on_speech_started(self) -> None:
        """User started a new utterance — cancel any pending auto-Enter.

        The next finished transcription will arm a fresh timer. This stops
        the timer from firing during a brief pause-then-resume mid-thought.

        Also flips :attr:`_user_is_speaking` so that if Whisper finishes the
        previous segment while the user is mid-utterance, ``_on_transcription``
        will defer arming the timer instead of starting it during speech.

        If continuation detection is enabled and the previous typed segment
        ended in '.' within the continuation window, retroactively demote
        that period to a comma — Whisper added it because each segment is
        transcribed in isolation, but the user actually meant a comma-pause.
        """
        self._user_is_speaking = True
        self._maybe_demote_previous_period()
        if self._auto_enter_timer.isActive():
            self._auto_enter_timer.stop()
            self.hotkey.disarm_cancel()
            log.info("Auto-Enter cancelled — new voice input detected.")

    def _on_speech_ended(self) -> None:
        """Sustained silence after speech — user has actually stopped talking.

        Clears the speaking flag so the next finished transcription is allowed
        to arm the auto-Enter timer.
        """
        self._user_is_speaking = False

    # --- continuation detection ---------------------------------------

    def _continuation_enabled(self) -> bool:
        return bool(self.settings.get("continuation_detection_enabled", False))

    def _continuation_window_s(self) -> float:
        return max(100, int(self.settings.get("continuation_window_ms", 500))) / 1000.0

    def _maybe_demote_previous_period(self) -> None:
        """If the last typed segment ended in '.' AND we're inside the
        continuation window, backspace the period and emit ',' instead."""
        if not self._continuation_enabled():
            return
        if not self._last_segment_ended_with_period:
            return
        if (time.monotonic() - self._typing_finished_at) >= self._continuation_window_s():
            return
        had_trailing_space = bool(self.settings.get("trailing_space", True))
        self.keyboard_out.replace_last_period_with_comma(had_trailing_space)
        self._continuation_pending = True
        self._last_segment_ended_with_period = False
        log.info("Continuation detected — previous '.' demoted to ','.")

    @staticmethod
    def _ends_with_single_period(text: str) -> bool:
        """True iff ``text`` ends with exactly one '.', not '..' or '...'."""
        return text.endswith(".") and not text.endswith("..")

    def _on_engine_error(self, message: str) -> None:
        self._notify("TextWhisper - Whisper error", message, error=True)

    def _on_audio_error(self, message: str) -> None:
        self._notify("TextWhisper - Audio error", message, error=True)

    def _on_hotkey_error(self, message: str) -> None:
        self._notify("TextWhisper - Hotkey error", message, error=True)

    def _on_model_loading(self, loading: bool) -> None:
        self.tray.set_status("Loading model..." if loading else "Idle")

    def _on_model_ready(self) -> None:
        self._model_loaded = True
        self.tray.set_status("Ready")
        self._notify(
            "TextWhisper",
            f"Ready. Press {self.settings.get('hotkey')} to dictate.",
        )
