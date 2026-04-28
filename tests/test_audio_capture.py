"""Tests for AudioCapture VAD segmentation logic.

These tests exercise the callback path directly (no real microphone) by feeding
synthetic audio blocks and inspecting the segments emitted via the Qt signal.
"""

from __future__ import annotations

import numpy as np

from src.audio_capture import AudioCapture
from src.settings_manager import SettingsManager

SR = AudioCapture.SAMPLE_RATE
BS = AudioCapture.BLOCK_SAMPLES


def _silence(amp: float = 0.0) -> np.ndarray:
    return np.full(BS, amp, dtype=np.float32)


def _tone(amp: float = 0.3, freq: float = 440.0) -> np.ndarray:
    t = np.arange(BS, dtype=np.float32) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _emit_block(cap: AudioCapture, block: np.ndarray) -> None:
    cap._callback(block.reshape(-1, 1), len(block), None, None)


def _make_capture(tmp_appdata, **overrides) -> tuple[AudioCapture, list[np.ndarray]]:
    sm = SettingsManager()
    for k, v in overrides.items():
        sm.set(k, v)
    cap = AudioCapture(sm)
    captured: list[np.ndarray] = []
    cap.segment_ready.connect(captured.append)
    return cap, captured


def test_segments_emit_after_silence(tmp_appdata, qapp):
    cap, segs = _make_capture(
        tmp_appdata,
        vad_threshold=0.05,
        vad_silence_ms=120,
        vad_min_segment_ms=30,
        vad_preroll_ms=0,
    )
    for _ in range(10):
        _emit_block(cap, _tone(amp=0.3))
    for _ in range(6):
        _emit_block(cap, _silence(0.0))

    qapp.processEvents()
    assert len(segs) == 1
    assert len(segs[0]) >= 10 * BS


def test_no_segment_when_only_silence(tmp_appdata, qapp):
    cap, segs = _make_capture(
        tmp_appdata,
        vad_threshold=0.05,
        vad_silence_ms=100,
        vad_preroll_ms=0,
    )
    for _ in range(20):
        _emit_block(cap, _silence(0.0))
    qapp.processEvents()
    assert segs == []


def test_max_segment_forces_flush(tmp_appdata, qapp):
    cap, segs = _make_capture(
        tmp_appdata,
        vad_threshold=0.05,
        vad_silence_ms=10000,
        vad_max_segment_ms=300,
        vad_min_segment_ms=30,
        vad_preroll_ms=0,
    )
    blocks_for_300ms = (300 * SR // 1000) // BS + 2
    for _ in range(blocks_for_300ms):
        _emit_block(cap, _tone(amp=0.3))
    qapp.processEvents()
    assert len(segs) >= 1


def test_min_segment_drops_brief_blip(tmp_appdata, qapp):
    cap, segs = _make_capture(
        tmp_appdata,
        vad_threshold=0.05,
        vad_silence_ms=60,
        vad_min_segment_ms=300,
        vad_preroll_ms=0,
    )
    _emit_block(cap, _tone(amp=0.3))
    for _ in range(6):
        _emit_block(cap, _silence(0.0))
    qapp.processEvents()
    assert segs == []


def test_preroll_prepended_on_speech_onset(tmp_appdata, qapp):
    cap, segs = _make_capture(
        tmp_appdata,
        vad_threshold=0.05,
        vad_silence_ms=120,
        vad_min_segment_ms=30,
        vad_preroll_ms=90,
    )
    for _ in range(5):
        _emit_block(cap, _silence(0.0))
    for _ in range(5):
        _emit_block(cap, _tone(amp=0.3))
    for _ in range(6):
        _emit_block(cap, _silence(0.0))
    qapp.processEvents()
    assert len(segs) == 1
    expected_min_blocks = 5 + (90 // 30)
    assert len(segs[0]) >= expected_min_blocks * BS


def test_audio_level_emitted_each_block(tmp_appdata, qapp):
    cap, _ = _make_capture(tmp_appdata, vad_threshold=0.05, vad_preroll_ms=0)
    levels: list[np.ndarray] = []
    cap.audio_level.connect(levels.append)
    for _ in range(4):
        _emit_block(cap, _tone(amp=0.2))
    qapp.processEvents()
    assert len(levels) == 4
    for chunk in levels:
        assert chunk.shape == (BS,)


def test_two_utterances_emit_two_segments(tmp_appdata, qapp):
    cap, segs = _make_capture(
        tmp_appdata,
        vad_threshold=0.05,
        vad_silence_ms=90,
        vad_min_segment_ms=30,
        vad_preroll_ms=0,
    )
    for _ in range(8):
        _emit_block(cap, _tone(amp=0.3))
    for _ in range(6):
        _emit_block(cap, _silence(0.0))
    for _ in range(8):
        _emit_block(cap, _tone(amp=0.3))
    for _ in range(6):
        _emit_block(cap, _silence(0.0))
    qapp.processEvents()
    assert len(segs) == 2


def test_speech_started_fires_once_per_onset(tmp_appdata, qapp):
    """speech_started fires exactly once on each transition silence -> speech."""
    cap, _ = _make_capture(
        tmp_appdata,
        vad_threshold=0.05,
        vad_silence_ms=90,
        vad_min_segment_ms=30,
        vad_preroll_ms=0,
    )
    onsets: list[None] = []
    cap.speech_started.connect(lambda: onsets.append(None))

    # First utterance: silence -> speech transition fires once.
    for _ in range(3):
        _emit_block(cap, _silence(0.0))
    for _ in range(8):
        _emit_block(cap, _tone(amp=0.3))
    qapp.processEvents()
    assert len(onsets) == 1

    # Continued speech does NOT re-fire.
    for _ in range(8):
        _emit_block(cap, _tone(amp=0.3))
    qapp.processEvents()
    assert len(onsets) == 1

    # Silence then a new utterance -> second onset.
    for _ in range(6):
        _emit_block(cap, _silence(0.0))
    for _ in range(8):
        _emit_block(cap, _tone(amp=0.3))
    qapp.processEvents()
    assert len(onsets) == 2


def test_speech_started_not_emitted_on_pure_silence(tmp_appdata, qapp):
    cap, _ = _make_capture(
        tmp_appdata,
        vad_threshold=0.05,
        vad_silence_ms=90,
        vad_preroll_ms=0,
    )
    onsets: list[None] = []
    cap.speech_started.connect(lambda: onsets.append(None))
    for _ in range(20):
        _emit_block(cap, _silence(0.0))
    qapp.processEvents()
    assert onsets == []


def test_speech_ended_fires_on_silence_after_speech(tmp_appdata, qapp):
    """speech_ended fires once the silence threshold is reached after speech."""
    cap, _ = _make_capture(
        tmp_appdata,
        vad_threshold=0.05,
        vad_silence_ms=90,
        vad_min_segment_ms=30,
        vad_preroll_ms=0,
    )
    ends: list[None] = []
    cap.speech_ended.connect(lambda: ends.append(None))

    for _ in range(8):
        _emit_block(cap, _tone(amp=0.3))
    assert ends == []  # still mid-speech

    for _ in range(6):
        _emit_block(cap, _silence(0.0))
    qapp.processEvents()
    assert len(ends) == 1


def test_speech_ended_fires_even_when_segment_dropped(tmp_appdata, qapp):
    """A speech-burst too short to meet vad_min_segment_ms still ends in
    silence — speech_ended must fire so the auto-Enter listener can clear
    its 'user is speaking' flag even when no transcription will follow."""
    cap, segs = _make_capture(
        tmp_appdata,
        vad_threshold=0.05,
        vad_silence_ms=60,
        vad_min_segment_ms=300,
        vad_preroll_ms=0,
    )
    ends: list[None] = []
    cap.speech_ended.connect(lambda: ends.append(None))

    _emit_block(cap, _tone(amp=0.3))  # ~30 ms blip < 300 ms minimum
    for _ in range(6):
        _emit_block(cap, _silence(0.0))
    qapp.processEvents()
    assert segs == []
    assert len(ends) == 1


def test_speech_ended_not_emitted_on_max_segment_chunking(tmp_appdata, qapp):
    """Max-segment forced flush is mid-speech chunking — user is still
    talking, so speech_ended must NOT fire there."""
    cap, _ = _make_capture(
        tmp_appdata,
        vad_threshold=0.05,
        vad_silence_ms=10000,
        vad_max_segment_ms=300,
        vad_min_segment_ms=30,
        vad_preroll_ms=0,
    )
    ends: list[None] = []
    cap.speech_ended.connect(lambda: ends.append(None))
    blocks_for_300ms = (300 * SR // 1000) // BS + 2
    for _ in range(blocks_for_300ms):
        _emit_block(cap, _tone(amp=0.3))
    qapp.processEvents()
    assert ends == []


def test_speech_ended_not_emitted_on_pure_silence(tmp_appdata, qapp):
    """Without any speech, silence shouldn't emit speech_ended."""
    cap, _ = _make_capture(
        tmp_appdata,
        vad_threshold=0.05,
        vad_silence_ms=90,
        vad_preroll_ms=0,
    )
    ends: list[None] = []
    cap.speech_ended.connect(lambda: ends.append(None))
    for _ in range(20):
        _emit_block(cap, _silence(0.0))
    qapp.processEvents()
    assert ends == []


def test_speech_ended_fires_once_per_utterance(tmp_appdata, qapp):
    """Two utterances → two speech_ended events."""
    cap, _ = _make_capture(
        tmp_appdata,
        vad_threshold=0.05,
        vad_silence_ms=90,
        vad_min_segment_ms=30,
        vad_preroll_ms=0,
    )
    ends: list[None] = []
    cap.speech_ended.connect(lambda: ends.append(None))

    for _ in range(8):
        _emit_block(cap, _tone(amp=0.3))
    for _ in range(6):
        _emit_block(cap, _silence(0.0))
    for _ in range(8):
        _emit_block(cap, _tone(amp=0.3))
    for _ in range(6):
        _emit_block(cap, _silence(0.0))
    qapp.processEvents()
    assert len(ends) == 2


def test_flush_drops_buffered_audio_and_resets_state(qapp, tmp_appdata):
    """flush() clears any partially-captured audio + resets VAD state."""
    import numpy as np
    from src.audio_capture import AudioCapture
    from src.settings_manager import SettingsManager

    sm = SettingsManager()
    cap = AudioCapture(sm)
    cap._buffer.append(np.ones(480, dtype=np.float32))
    cap._buffered_samples = 480
    cap._has_speech = True
    cap._silence_blocks = 5

    cap.flush()

    assert cap._buffer == []
    assert cap._buffered_samples == 0
    assert cap._has_speech is False
    assert cap._silence_blocks == 0
    assert cap._discard_next_segment is True


def test_flush_then_emit_suppresses_next_segment_ready(qapp, tmp_appdata):
    """After flush(), the next finished segment is dropped (no emit)."""
    import numpy as np
    from src.audio_capture import AudioCapture
    from src.settings_manager import SettingsManager

    sm = SettingsManager()
    cap = AudioCapture(sm)
    received: list = []
    cap.segment_ready.connect(received.append)

    cap.flush()
    cap._buffer.append(np.ones(16000, dtype=np.float32))
    cap._buffered_samples = 16000
    cap._has_speech = True
    cap._emit_locked(min_samples=0)

    assert received == [], "first post-flush segment must be discarded"
    assert cap._discard_next_segment is False, "flag must clear after one suppression"

    cap._buffer.append(np.ones(16000, dtype=np.float32))
    cap._buffered_samples = 16000
    cap._has_speech = True
    cap._emit_locked(min_samples=0)
    assert len(received) == 1, "post-flag-clear segment must emit"
