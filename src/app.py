"""Main application wiring: tray, hotkey, audio, transcription, output."""

from __future__ import annotations

import contextlib
import logging

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

        self._wire_signals()

        if bool(self.settings.get("oscilloscope.enabled", True)):
            self.oscilloscope.show()
        self.tray.set_oscilloscope_visible(self.oscilloscope.isVisible())

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
            if self._is_capturing:
                self._stop_capture()
            self.hotkey.stop()
            self.engine.stop()
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
        self.audio.error.connect(self._on_audio_error)

        self.engine.transcription_ready.connect(self._on_transcription)
        self.engine.error.connect(self._on_engine_error)
        self.engine.model_loading.connect(self._on_model_loading)
        self.engine.model_ready.connect(self._on_model_ready)

        self.tray.toggle_capture.connect(self._toggle_capture)
        self.tray.show_settings.connect(self._open_settings)
        self.tray.toggle_oscilloscope.connect(self._toggle_oscilloscope)
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

    # --- settings ------------------------------------------------------

    def _open_settings(self) -> None:
        prev_hotkey = self.settings.get("hotkey")
        prev_delete_hotkey = self.settings.get("delete_hotkey")
        prev_model = self.settings.get("model_size")
        prev_device = self.settings.get("device")
        prev_compute = self.settings.get("compute_type")
        prev_mic = self.settings.get("microphone_device")

        dlg = SettingsDialog(self.settings)
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

    # --- engine + audio callbacks --------------------------------------

    def _on_transcription(self, text: str) -> None:
        typed = self.keyboard_out.type_text(text)
        if typed > 0:
            self._typed_history.append(typed)
            if len(self._typed_history) > self._typed_history_max:
                self._typed_history = self._typed_history[-self._typed_history_max:]
        self._last_typed_text = text
        # New dictation -> reset the hotkey-stray-char counter so subsequent
        # delete presses are scoped to this segment.
        self._extra_chars_typed_by_hotkey = 0
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
