"""Tests for the TTSService.

Real Piper synthesis + sounddevice playback aren't exercised here — they
need a downloaded voice model and an actual audio device. Instead we
patch ``piper.voice.PiperVoice`` and ``sounddevice.OutputStream`` so we
can drive the queue / interrupt / cache logic in isolation and assert
the right pieces wire up.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PyQt6.QtCore import Qt

from src.settings_manager import SettingsManager
from src.voice import TTSService

# Worker-thread emissions reach a Python list via DirectConnection so we
# don't need a Qt event loop in the test process.
_DIRECT = Qt.ConnectionType.DirectConnection


@pytest.fixture
def svc(tmp_appdata):
    s = SettingsManager()
    s.set("voice_model", "en_US-amy-medium")
    s.set("voice_rate", 1.0)
    s.set("voice_volume", 0.85)
    return TTSService(s)


def _fake_voice(sample_rate: int = 22050, n_chunks: int = 3):
    """Build a stand-in PiperVoice that yields N AudioChunks of 100 samples."""
    voice = MagicMock()
    voice.config.sample_rate = sample_rate
    chunks = []
    for i in range(n_chunks):
        c = MagicMock()
        c.audio_int16_array = (np.ones(100, dtype=np.int16) * (i + 1))
        chunks.append(c)
    voice.synthesize.return_value = iter(chunks)
    return voice


def _wait_for_idle(svc: TTSService, timeout: float = 2.0) -> bool:
    """Poll ._queue until empty AND the worker is idle (best-effort)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if svc._queue.empty() and not svc._interrupt.is_set():
            time.sleep(0.05)
            if svc._queue.empty():
                return True
        time.sleep(0.02)
    return False


def test_speak_empty_text_is_noop(svc):
    """Empty / whitespace-only input must not enqueue or start a thread."""
    svc.speak("")
    svc.speak("   ")
    svc.speak(None)  # type: ignore[arg-type]
    assert svc._queue.qsize() == 0


def test_speak_loads_voice_and_streams_chunks(svc, tmp_appdata):
    voice = _fake_voice(sample_rate=22050, n_chunks=2)
    # Fake the model files so _ensure_voice doesn't try to download.
    voices_root = tmp_appdata / "TextWhisper" / "piper-voices"
    voices_root.mkdir(parents=True, exist_ok=True)
    (voices_root / "en_US-amy-medium.onnx").write_bytes(b"x")
    (voices_root / "en_US-amy-medium.onnx.json").write_bytes(b"{}")

    stream = MagicMock()
    stream.__enter__ = lambda self: self
    stream.__exit__ = lambda *a: None

    with patch("piper.voice.PiperVoice.load", return_value=voice), patch(
        "src.voice.sd.OutputStream", return_value=stream
    ):
        svc.speak("hello world")
        _wait_for_idle(svc)
        svc.shutdown()

    voice.synthesize.assert_called_once()
    # Each AudioChunk's audio was forwarded to stream.write.
    assert stream.write.call_count == 2


def test_voice_load_is_cached(svc, tmp_appdata):
    """Two speak calls in a row must NOT reload the model."""
    voice = _fake_voice(n_chunks=1)
    voices_root = tmp_appdata / "TextWhisper" / "piper-voices"
    voices_root.mkdir(parents=True, exist_ok=True)
    (voices_root / "en_US-amy-medium.onnx").write_bytes(b"x")
    (voices_root / "en_US-amy-medium.onnx.json").write_bytes(b"{}")

    stream = MagicMock()
    stream.__enter__ = lambda self: self
    stream.__exit__ = lambda *a: None

    with patch("piper.voice.PiperVoice.load", return_value=voice) as load_mock, patch(
        "src.voice.sd.OutputStream", return_value=stream
    ):
        svc.speak("first")
        _wait_for_idle(svc)
        # Re-arm the iterator since iter() exhausts after one pass.
        c = MagicMock()
        c.audio_int16_array = np.ones(50, dtype=np.int16)
        voice.synthesize.return_value = iter([c])
        svc.speak("second")
        _wait_for_idle(svc)
        svc.shutdown()

    assert load_mock.call_count == 1


def test_download_failure_emits_error(svc, tmp_appdata):
    """Voice missing on disk + download raises -> error signal, no crash."""
    errors: list[str] = []
    svc.error.connect(errors.append, _DIRECT)

    with patch(
        "piper.download_voices.download_voice", side_effect=RuntimeError("offline")
    ):
        svc.speak("hello")
        _wait_for_idle(svc)
        svc.shutdown()

    assert any("offline" in e for e in errors), errors


def test_load_failure_emits_error(svc, tmp_appdata):
    voices_root = tmp_appdata / "TextWhisper" / "piper-voices"
    voices_root.mkdir(parents=True, exist_ok=True)
    (voices_root / "en_US-amy-medium.onnx").write_bytes(b"x")
    (voices_root / "en_US-amy-medium.onnx.json").write_bytes(b"{}")
    errors: list[str] = []
    svc.error.connect(errors.append, _DIRECT)
    with patch(
        "piper.voice.PiperVoice.load", side_effect=RuntimeError("bad onnx")
    ):
        svc.speak("hello")
        _wait_for_idle(svc)
        svc.shutdown()
    assert any("bad onnx" in e for e in errors), errors


def test_interrupt_drains_queue(svc):
    """Interrupt clears any backlog so the user doesn't have to mash."""
    svc.start()
    svc._queue.put("a")
    svc._queue.put("b")
    svc._queue.put("c")
    svc.interrupt()
    assert svc._queue.qsize() == 0
    svc.shutdown()


def test_rate_clamped_and_inverted_to_length_scale(svc, tmp_appdata):
    """voice_rate=2.0 -> length_scale=0.5; voice_rate=10.0 clamps to 2.0."""
    from piper.config import SynthesisConfig

    svc.settings.set("voice_rate", 10.0)
    voice = _fake_voice(n_chunks=1)
    voices_root = tmp_appdata / "TextWhisper" / "piper-voices"
    voices_root.mkdir(parents=True, exist_ok=True)
    (voices_root / "en_US-amy-medium.onnx").write_bytes(b"x")
    (voices_root / "en_US-amy-medium.onnx.json").write_bytes(b"{}")
    stream = MagicMock()
    stream.__enter__ = lambda self: self
    stream.__exit__ = lambda *a: None

    with patch("piper.voice.PiperVoice.load", return_value=voice), patch(
        "src.voice.sd.OutputStream", return_value=stream
    ):
        svc.speak("hi")
        _wait_for_idle(svc)
        svc.shutdown()

    cfg = voice.synthesize.call_args.kwargs["syn_config"]
    assert isinstance(cfg, SynthesisConfig)
    # 10.0 was clamped to 2.0 -> length_scale = 1/2.0 = 0.5
    assert abs(cfg.length_scale - 0.5) < 1e-6


def test_shutdown_is_idempotent(svc):
    svc.start()
    svc.shutdown()
    svc.shutdown()  # second call must not raise


def test_status_signal_emits_idle_after_speak(svc, tmp_appdata):
    voice = _fake_voice(n_chunks=1)
    voices_root = tmp_appdata / "TextWhisper" / "piper-voices"
    voices_root.mkdir(parents=True, exist_ok=True)
    (voices_root / "en_US-amy-medium.onnx").write_bytes(b"x")
    (voices_root / "en_US-amy-medium.onnx.json").write_bytes(b"{}")
    statuses: list[str] = []
    svc.status.connect(statuses.append, _DIRECT)
    stream = MagicMock()
    stream.__enter__ = lambda self: self
    stream.__exit__ = lambda *a: None

    with patch("piper.voice.PiperVoice.load", return_value=voice), patch(
        "src.voice.sd.OutputStream", return_value=stream
    ):
        svc.speak("hi")
        _wait_for_idle(svc)
        svc.shutdown()

    assert "Idle" in statuses


def test_speak_one_passes_audio_output_device_to_outputstream(tmp_appdata, qapp):
    """OutputStream constructor receives the configured device kwarg."""
    from unittest.mock import MagicMock, patch
    from src.settings_manager import SettingsManager
    from src.voice import TTSService

    sm = SettingsManager()
    sm.set("audio_output_device", 11)
    sm.set("voice_model", "fake-voice")
    svc = TTSService(sm)

    fake_voice = MagicMock()
    fake_voice.config.sample_rate = 22050
    fake_voice.synthesize.return_value = iter([])

    with patch.object(svc, "_ensure_voice", return_value=fake_voice), \
         patch("src.voice.sd.OutputStream") as os_cls, \
         patch("piper.config.SynthesisConfig"):
        os_cls.return_value.__enter__.return_value = MagicMock()
        svc._speak_one("hi")

    os_cls.assert_called_once()
    assert os_cls.call_args.kwargs.get("device") == 11
