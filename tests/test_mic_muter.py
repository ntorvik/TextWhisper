"""Tests for MicMuter — auto-mute mic during Piper TTS."""

from __future__ import annotations

from unittest.mock import MagicMock


def test_mic_muter_pauses_capture_on_tts_started(qapp):
    from src.mic_muter import MicMuter
    audio = MagicMock()
    audio.is_running = True
    mm = MicMuter(audio)
    mm.on_tts_started()
    audio.stop.assert_called_once()
    assert mm.is_muted is True


def test_mic_muter_does_not_pause_when_capture_already_stopped(qapp):
    from src.mic_muter import MicMuter
    audio = MagicMock()
    audio.is_running = False
    mm = MicMuter(audio)
    mm.on_tts_started()
    audio.stop.assert_not_called()
    assert mm.is_muted is True


def test_mic_muter_resumes_capture_after_grace(qapp):
    """speak_finished schedules a timer; after it fires, audio.start runs."""
    import time
    from PyQt6.QtCore import QCoreApplication
    from src.mic_muter import MicMuter

    audio = MagicMock()
    audio.is_running = False  # we stopped it on speak_started
    mm = MicMuter(audio, _resume_grace_ms=10)
    mm._was_running_before_mute = True
    mm._is_muted = True
    mm.on_tts_finished()
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline and not audio.start.called:
        QCoreApplication.processEvents()
        time.sleep(0.005)
    audio.start.assert_called_once()
    assert mm.is_muted is False


def test_mic_muter_does_not_resume_when_was_not_running(qapp):
    """If capture wasn't running before TTS started, don't auto-start it."""
    import time
    from PyQt6.QtCore import QCoreApplication
    from src.mic_muter import MicMuter

    audio = MagicMock()
    audio.is_running = False
    mm = MicMuter(audio, _resume_grace_ms=10)
    mm._was_running_before_mute = False
    mm._is_muted = True
    mm.on_tts_finished()
    deadline = time.monotonic() + 0.1
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.005)
    audio.start.assert_not_called()
    assert mm.is_muted is False


def test_mic_muter_back_to_back_tts_cancels_pending_resume(qapp):
    """Two TTS calls in quick succession: pending resume timer is cancelled."""
    import time
    from PyQt6.QtCore import QCoreApplication
    from src.mic_muter import MicMuter

    audio = MagicMock()
    audio.is_running = True
    mm = MicMuter(audio, _resume_grace_ms=200)
    mm.on_tts_started()
    mm.on_tts_finished()
    assert mm._resume_timer.isActive()
    mm.on_tts_started()
    assert not mm._resume_timer.isActive(), "pending resume must be cancelled"
    deadline = time.monotonic() + 0.3
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.005)
    audio.start.assert_not_called()
