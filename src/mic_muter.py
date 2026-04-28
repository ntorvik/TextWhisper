"""Auto-mute the mic capture during Piper TTS playback.

Wired to TTSService.speak_started / speak_finished Qt signals from
TextWhisperApp. Stops the AudioCapture stream the instant TTS starts so
the speaker output cannot loop back into the mic; on TTS finish, waits
500 ms (Bluetooth tail-out + portaudio settle) before restarting capture.

If the mic wasn't running when TTS started (e.g. user wasn't dictating),
this controller still tracks the muted state so speak_finished is a no-op
on the resume side — it doesn't auto-START capture; it only restores the
state that was running before.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, QTimer

log = logging.getLogger(__name__)

_DEFAULT_RESUME_GRACE_MS = 500


class MicMuter(QObject):
    def __init__(self, audio, _resume_grace_ms: int = _DEFAULT_RESUME_GRACE_MS) -> None:
        super().__init__()
        self._audio = audio
        self._is_muted = False
        self._was_running_before_mute = False
        self._resume_timer = QTimer(self)
        self._resume_timer.setSingleShot(True)
        self._resume_timer.setInterval(_resume_grace_ms)
        self._resume_timer.timeout.connect(self._do_resume)

    @property
    def is_muted(self) -> bool:
        return self._is_muted

    def on_tts_started(self) -> None:
        if self._resume_timer.isActive():
            self._resume_timer.stop()
        if self._is_muted:
            return
        was_running = bool(getattr(self._audio, "is_running", False))
        self._was_running_before_mute = was_running
        if was_running:
            try:
                self._audio.stop()
            except Exception:
                log.exception("MicMuter: audio.stop() failed")
        self._is_muted = True
        log.info("Mic muted for TTS playback (was_running=%s)", was_running)

    def on_tts_finished(self) -> None:
        if not self._is_muted:
            return
        self._resume_timer.start()

    def _do_resume(self) -> None:
        try:
            if self._was_running_before_mute:
                self._audio.start()
                log.info("Mic unmuted after TTS grace period")
            else:
                log.debug("Mic was not running before TTS; skip auto-start")
        except Exception:
            log.exception("MicMuter: audio.start() failed")
        finally:
            self._is_muted = False
            self._was_running_before_mute = False
