"""Soft chime tones for capture-ready / capture-stopped feedback.

Tones are pre-generated as float32 numpy arrays with cosine envelopes (no
clicks at attack/decay) and played via sounddevice's default output device.
"""

from __future__ import annotations

import logging

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)

_SAMPLE_RATE = 44100
_NOTE_DURATION_S = 0.08  # 80 ms per note — short enough to be unobtrusive


def _make_tone(freq: float, duration_s: float, volume: float) -> np.ndarray:
    """Sine tone of ``freq`` Hz with a cosine attack/decay envelope."""
    n = max(1, int(_SAMPLE_RATE * duration_s))
    t = np.arange(n, dtype=np.float32) / _SAMPLE_RATE
    sine = np.sin(2.0 * np.pi * freq * t).astype(np.float32)
    # Hann-like cosine envelope so there's no click at the start/end.
    envelope = 0.5 - 0.5 * np.cos(2.0 * np.pi * t / duration_s)
    return (sine * envelope.astype(np.float32) * float(volume)).astype(np.float32)


def _make_chime(freqs: list[float], volume: float) -> np.ndarray:
    """Two-note chime: e.g. [659, 784] for E5→G5 (ascending)."""
    notes = [_make_tone(f, _NOTE_DURATION_S, volume) for f in freqs]
    return np.concatenate(notes) if notes else np.zeros(0, dtype=np.float32)


class SoundPlayer:
    """Plays short chimes for capture lifecycle events.

    All settings reads happen at play time so toggling sound in Settings
    takes effect immediately without rebuilding the player.
    """

    READY_FREQS = (659.0, 784.0)   # E5 → G5  (ascending = "ready")
    STOP_FREQS = (784.0, 659.0)    # G5 → E5  (descending = "stopped")
    LOCK_FREQS = (523.0, 698.0)    # C5 → F5  (ascending = "lock")
    UNLOCK_FREQS = (698.0, 523.0)  # F5 → C5  (descending = "unlock")

    def __init__(self, settings) -> None:
        self.settings = settings
        # Build at default volume; rebuild on demand when volume changes.
        self._cached_volume: float | None = None
        self._ready: np.ndarray = np.zeros(0, dtype=np.float32)
        self._stop: np.ndarray = np.zeros(0, dtype=np.float32)
        self._lock_chime: np.ndarray = np.zeros(0, dtype=np.float32)
        self._unlock_chime: np.ndarray = np.zeros(0, dtype=np.float32)
        self._rebuild_if_volume_changed()

    @property
    def ready_duration_ms(self) -> int:
        return int(len(self._ready) / _SAMPLE_RATE * 1000)

    def _rebuild_if_volume_changed(self) -> None:
        v = float(self.settings.get("sound_volume", 0.15))
        v = max(0.0, min(1.0, v))
        if v != self._cached_volume:
            self._cached_volume = v
            self._ready = _make_chime(list(self.READY_FREQS), v)
            self._stop = _make_chime(list(self.STOP_FREQS), v)
            self._lock_chime = _make_chime(list(self.LOCK_FREQS), v)
            self._unlock_chime = _make_chime(list(self.UNLOCK_FREQS), v)

    def play_ready(self) -> None:
        if not bool(self.settings.get("play_ready_sound", True)):
            return
        self._rebuild_if_volume_changed()
        self._play(self._ready)

    def play_stop(self) -> None:
        if not bool(self.settings.get("play_stop_sound", False)):
            return
        self._rebuild_if_volume_changed()
        self._play(self._stop)

    def play_lock(self) -> None:
        if not bool(self.settings.get("paste_lock_play_sounds", True)):
            return
        self._rebuild_if_volume_changed()
        self._play(self._lock_chime)

    def play_unlock(self) -> None:
        if not bool(self.settings.get("paste_lock_play_sounds", True)):
            return
        self._rebuild_if_volume_changed()
        self._play(self._unlock_chime)

    def _play(self, samples: np.ndarray) -> None:
        if samples.size == 0:
            return
        try:
            sd.play(samples, _SAMPLE_RATE)
        except Exception:
            # Output device unavailable, busy, etc. — non-fatal.
            log.exception("Sound playback failed")
