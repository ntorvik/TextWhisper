"""Piper-based TTS service for read-back of Claude responses.

Each :meth:`speak` request is queued onto a single worker thread that:

1. Ensures the configured Piper voice model is downloaded (cached under
   ``%APPDATA%\\TextWhisper\\piper-voices\\``).
2. Loads the model into memory (loaded once, kept warm).
3. Streams ``voice.synthesize(text)`` chunks into a sounddevice OutputStream
   so playback starts within ~half a second instead of waiting for the
   full WAV to render.
4. Honours :meth:`interrupt` — sets a flag the worker checks between
   chunks so the user can cut off rambling read-backs immediately.

Heavy work (download, model load, synthesis) all happens off the GUI
thread; status / error / lifecycle are surfaced via Qt signals.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
from pathlib import Path

import numpy as np
import sounddevice as sd
from PyQt6.QtCore import QObject, pyqtSignal

log = logging.getLogger(__name__)


def _voices_dir() -> Path:
    base = os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / ".config"
    path = root / "TextWhisper" / "piper-voices"
    path.mkdir(parents=True, exist_ok=True)
    return path


class TTSService(QObject):
    """Queue-driven Piper TTS pump.

    Public API: :meth:`speak`, :meth:`interrupt`, :meth:`shutdown`.
    Signals: :attr:`status`, :attr:`error`, :attr:`speak_started`,
    :attr:`speak_finished`.
    """

    # Human-readable progress string ("Downloading en_US-amy-medium...",
    # "Loading model...", "Speaking...", "Idle"). Used by the Voice tab to
    # update its status label without re-reading service internals.
    status = pyqtSignal(str)
    error = pyqtSignal(str)
    speak_started = pyqtSignal()
    speak_finished = pyqtSignal()

    def __init__(self, settings) -> None:
        super().__init__()
        self.settings = settings
        self._queue: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._interrupt = threading.Event()
        self._thread: threading.Thread | None = None
        # Cache of loaded voice models keyed by model id. Loading is a
        # couple seconds — worth keeping warm.
        self._loaded: dict[str, object] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="TextWhisper-TTS", daemon=True
        )
        self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        self._interrupt.set()
        self._queue.put(None)
        if self._thread:
            self._thread.join(timeout=3)
        self._thread = None

    def speak(self, text: str) -> None:
        """Enqueue ``text`` for read-back. No-op on empty input."""
        text = (text or "").strip()
        if not text:
            return
        self.start()
        self._queue.put(text)

    def interrupt(self) -> None:
        """Cut off the current playback. Pending queued items also drop."""
        self._interrupt.set()
        # Drain pending queue so an interrupt also clears the backlog —
        # otherwise the user would have to mash the hotkey N times.
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                break
            self._interrupt.clear()
            try:
                self._speak_one(str(item))
            except Exception as e:
                log.exception("TTS speak failed")
                self.error.emit(f"Read-back failed: {e}")
            finally:
                self.status.emit("Idle")

    def _speak_one(self, text: str) -> None:
        model_id = str(self.settings.get("voice_model", "en_US-amy-medium"))
        rate = float(self.settings.get("voice_rate", 1.0))
        volume = float(self.settings.get("voice_volume", 0.85))

        voice = self._ensure_voice(model_id)
        if voice is None:
            return

        from piper.config import SynthesisConfig

        # Piper's length_scale is the inverse of speech rate: smaller =
        # faster speech. Clamp to 0.5x..2.0x to match the Settings UI.
        rate = max(0.5, min(2.0, rate))
        length_scale = 1.0 / rate
        cfg = SynthesisConfig(length_scale=length_scale, volume=volume)

        sample_rate = int(getattr(voice.config, "sample_rate", 22050))
        log.info(
            "TTS speak: model=%s rate=%.2fx (length_scale=%.3f) volume=%.2f sr=%d",
            model_id, rate, length_scale, volume, sample_rate,
        )
        self.status.emit("Speaking...")
        self.speak_started.emit()
        try:
            with sd.OutputStream(
                samplerate=sample_rate, channels=1, dtype="int16"
            ) as stream:
                for chunk in voice.synthesize(text, syn_config=cfg):
                    if self._interrupt.is_set() or self._stop.is_set():
                        log.info("TTS speak: interrupted by user.")
                        break
                    audio = chunk.audio_int16_array
                    if audio.ndim == 1:
                        audio = audio.reshape(-1, 1)
                    stream.write(audio.astype(np.int16, copy=False))
        finally:
            self.speak_finished.emit()

    def _ensure_voice(self, model_id: str):
        """Return a loaded :class:`PiperVoice`, downloading + loading if
        needed. Cached across calls so the model doesn't reload per turn.
        """
        if model_id in self._loaded:
            return self._loaded[model_id]

        voices_root = _voices_dir()
        model_path = voices_root / f"{model_id}.onnx"
        cfg_path = voices_root / f"{model_id}.onnx.json"

        if not model_path.exists() or not cfg_path.exists():
            self.status.emit(f"Downloading {model_id}...")
            try:
                from piper.download_voices import download_voice
                download_voice(model_id, voices_root)
            except Exception as e:
                log.exception("Piper voice download failed: %s", model_id)
                self.error.emit(
                    f"Could not download voice {model_id!r}: {e}. "
                    "Check your internet connection or pick a different "
                    "voice in Settings → Voice."
                )
                return None

        self.status.emit(f"Loading {model_id}...")
        try:
            from piper.voice import PiperVoice
            voice = PiperVoice.load(model_path, config_path=cfg_path)
        except Exception as e:
            log.exception("Piper voice load failed: %s", model_id)
            self.error.emit(f"Could not load voice {model_id!r}: {e}")
            return None
        self._loaded[model_id] = voice
        return voice
