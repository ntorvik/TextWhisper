"""Microphone capture with simple energy-based VAD segmentation.

Audio comes in via a sounddevice InputStream callback (on a portaudio thread).
Each ~30 ms block is energy-checked. When sustained voice is followed by enough
silence, the buffered utterance is emitted via `segment_ready` for transcription.

Live audio is also emitted on `audio_level` for the oscilloscope. Both signals
cross thread boundaries safely via Qt's queued connections.
"""

from __future__ import annotations

import collections
import logging
import threading

import numpy as np
import sounddevice as sd
from PyQt6.QtCore import QObject, pyqtSignal

log = logging.getLogger(__name__)


class AudioCapture(QObject):
    audio_level = pyqtSignal(np.ndarray)
    segment_ready = pyqtSignal(np.ndarray)
    # Fires the moment the energy-based VAD transitions from "no speech" to
    # "speech detected." Used by the auto-Enter feature to cancel a pending
    # Enter as soon as the user starts a new utterance — the next finished
    # transcription will arm a fresh timer.
    speech_started = pyqtSignal()
    error = pyqtSignal(str)

    SAMPLE_RATE = 16000
    BLOCK_SAMPLES = 480  # 30 ms @ 16 kHz

    def __init__(self, settings) -> None:
        super().__init__()
        self.settings = settings
        self._stream: sd.InputStream | None = None
        self._lock = threading.Lock()
        self._buffer: list[np.ndarray] = []
        self._buffered_samples = 0
        self._silence_blocks = 0
        self._has_speech = False
        preroll_ms = int(self.settings.get("vad_preroll_ms", 200))
        preroll_blocks = max(1, preroll_ms // 30)
        self._preroll: collections.deque[np.ndarray] = collections.deque(maxlen=preroll_blocks)

    @property
    def is_running(self) -> bool:
        return self._stream is not None

    def start(self) -> None:
        if self._stream is not None:
            return
        device = self.settings.get("microphone_device")
        try:
            self._stream = sd.InputStream(
                device=device,
                samplerate=self.SAMPLE_RATE,
                channels=1,
                dtype="float32",
                blocksize=self.BLOCK_SAMPLES,
                callback=self._callback,
            )
            self._stream.start()
        except Exception as e:
            self._stream = None
            self.error.emit(f"Failed to start microphone: {e}")
            raise

    def stop(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        self._flush(force=True)
        with self._lock:
            self._preroll.clear()
            self._buffer.clear()
            self._buffered_samples = 0
            self._has_speech = False
            self._silence_blocks = 0

    def _callback(self, indata, frames, time_info, status) -> None:  # portaudio thread
        if status:
            # XRuns etc. — non-fatal, just keep going.
            pass
        block = indata[:, 0].astype(np.float32, copy=True)
        # Always show live audio in the oscilloscope.
        self.audio_level.emit(block)

        threshold = float(self.settings.get("vad_threshold", 0.012))
        silence_ms = int(self.settings.get("vad_silence_ms", 700))
        max_segment_ms = int(self.settings.get("vad_max_segment_ms", 25000))
        min_segment_ms = int(self.settings.get("vad_min_segment_ms", 350))
        silence_blocks_max = max(1, silence_ms // 30)
        max_segment_samples = (max_segment_ms * self.SAMPLE_RATE) // 1000
        min_segment_samples = (min_segment_ms * self.SAMPLE_RATE) // 1000

        rms = float(np.sqrt(np.mean(block * block) + 1e-12))

        speech_just_started = False
        with self._lock:
            if rms >= threshold:
                if not self._has_speech:
                    # Speech onset — flush preroll into buffer for natural attack.
                    for prev in self._preroll:
                        self._buffer.append(prev)
                        self._buffered_samples += len(prev)
                    speech_just_started = True
                self._has_speech = True
                self._silence_blocks = 0
                self._buffer.append(block)
                self._buffered_samples += len(block)
                if self._buffered_samples >= max_segment_samples:
                    self._emit_locked(min_segment_samples)
            else:
                if self._has_speech:
                    # Tail silence — keep buffering until threshold met.
                    self._buffer.append(block)
                    self._buffered_samples += len(block)
                    self._silence_blocks += 1
                    if self._silence_blocks >= silence_blocks_max:
                        self._emit_locked(min_segment_samples)
                else:
                    self._preroll.append(block)

        # Emit speech_started OUTSIDE the lock (Qt signal emit can dispatch
        # synchronously to direct-connected slots, and we don't want to hold
        # the lock across that).
        if speech_just_started:
            self.speech_started.emit()

    def _emit_locked(self, min_samples: int) -> None:
        if not self._buffer:
            self._reset_segment()
            return
        audio = np.concatenate(self._buffer)
        self._reset_segment()
        if len(audio) >= min_samples:
            log.info(
                "Segment ready: %.2fs (%d samples, peak=%.3f)",
                len(audio) / self.SAMPLE_RATE,
                len(audio),
                float(np.max(np.abs(audio))) if audio.size else 0.0,
            )
            self.segment_ready.emit(audio)
        else:
            log.debug(
                "Dropped short segment: %.3fs (< min %.3fs)",
                len(audio) / self.SAMPLE_RATE,
                min_samples / self.SAMPLE_RATE,
            )

    def _reset_segment(self) -> None:
        self._buffer.clear()
        self._buffered_samples = 0
        self._has_speech = False
        self._silence_blocks = 0

    def _flush(self, force: bool = False) -> None:
        with self._lock:
            min_segment_ms = int(self.settings.get("vad_min_segment_ms", 350))
            min_samples = 0 if force else (min_segment_ms * self.SAMPLE_RATE) // 1000
            if self._buffer:
                self._emit_locked(min_samples)
