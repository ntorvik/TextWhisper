"""Tests for TranscriptionEngine wiring.

faster-whisper itself is heavy and GPU-dependent, so we patch the model class
to assert the engine calls it with the right arguments and propagates results
via Qt signals.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np

from src.settings_manager import SettingsManager
from src.transcription import TranscriptionEngine


def _segments(text: str) -> list:
    return [SimpleNamespace(text=text)]


def _engine(tmp_appdata, **overrides) -> TranscriptionEngine:
    sm = SettingsManager()
    for k, v in overrides.items():
        sm.set(k, v)
    return TranscriptionEngine(sm)


def test_transcribe_emits_text(tmp_appdata, qapp):
    eng = _engine(tmp_appdata, language="en", model_size="tiny", device="cpu", compute_type="int8")

    fake_model = MagicMock()
    fake_model.transcribe.return_value = (_segments(" hello world"), object())

    received: list[str] = []
    eng.transcription_ready.connect(received.append)

    with patch("faster_whisper.WhisperModel", return_value=fake_model) as model_cls:
        eng.start()
        eng.submit(np.zeros(16000, dtype=np.float32))
        for _ in range(50):
            qapp.processEvents()
            if received:
                break
            threading.Event().wait(0.05)
        eng.stop()

    model_cls.assert_called_once()
    args, kwargs = fake_model.transcribe.call_args
    assert kwargs.get("language") == "en"
    assert kwargs.get("vad_filter") is True
    assert kwargs.get("beam_size") == 5
    assert received == ["hello world"]


def test_auto_language_passes_none(tmp_appdata, qapp):
    eng = _engine(tmp_appdata, language="auto", device="cpu", compute_type="int8")
    fake_model = MagicMock()
    fake_model.transcribe.return_value = (_segments("ok"), object())

    with patch("faster_whisper.WhisperModel", return_value=fake_model):
        eng.start()
        eng.submit(np.zeros(8000, dtype=np.float32))
        for _ in range(50):
            qapp.processEvents()
            if fake_model.transcribe.called:
                break
            threading.Event().wait(0.05)
        eng.stop()

    assert fake_model.transcribe.called
    _, kwargs = fake_model.transcribe.call_args
    assert kwargs.get("language") is None


def test_resolve_device_cpu_downgrades_float16(tmp_appdata):
    eng = _engine(tmp_appdata, device="cpu", compute_type="float16")
    device, compute = eng._resolve_device()
    assert device == "cpu"
    assert compute == "int8"


def test_resolve_device_cuda_keeps_compute(tmp_appdata):
    eng = _engine(tmp_appdata, device="cuda", compute_type="float16")
    device, compute = eng._resolve_device()
    assert device == "cuda"
    assert compute == "float16"


def test_model_load_failure_emits_error(tmp_appdata, qapp):
    eng = _engine(tmp_appdata, device="cpu", compute_type="int8")
    errors: list[str] = []
    eng.error.connect(errors.append)

    with patch("faster_whisper.WhisperModel", side_effect=RuntimeError("boom")):
        eng.start()
        for _ in range(50):
            qapp.processEvents()
            if errors:
                break
            threading.Event().wait(0.05)
        eng.stop()

    assert errors and "boom" in errors[0]


def test_empty_transcription_does_not_emit(tmp_appdata, qapp):
    eng = _engine(tmp_appdata, device="cpu", compute_type="int8")
    fake_model = MagicMock()
    fake_model.transcribe.return_value = (_segments("   "), object())

    received: list[str] = []
    eng.transcription_ready.connect(received.append)

    with patch("faster_whisper.WhisperModel", return_value=fake_model):
        eng.start()
        eng.submit(np.zeros(8000, dtype=np.float32))
        for _ in range(20):
            qapp.processEvents()
            threading.Event().wait(0.05)
        eng.stop()

    assert received == []


def test_loud_audio_normalized_before_transcribe(tmp_appdata, qapp):
    eng = _engine(tmp_appdata, device="cpu", compute_type="int8")
    fake_model = MagicMock()
    fake_model.transcribe.return_value = (_segments("x"), object())

    with patch("faster_whisper.WhisperModel", return_value=fake_model):
        eng.start()
        loud = np.full(8000, 5.0, dtype=np.float32)
        eng.submit(loud)
        for _ in range(50):
            qapp.processEvents()
            if fake_model.transcribe.called:
                break
            threading.Event().wait(0.05)
        eng.stop()

    args, _ = fake_model.transcribe.call_args
    sent = args[0]
    assert float(np.max(np.abs(sent))) <= 1.0 + 1e-6
