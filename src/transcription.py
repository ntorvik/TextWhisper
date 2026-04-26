"""faster-whisper transcription worker.

Audio segments are submitted from the GUI thread; a background thread loads the
model once and processes segments serially. Results are emitted via Qt signals.
"""

from __future__ import annotations

import logging
import re
import threading
from queue import Empty, Queue

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

log = logging.getLogger(__name__)

# Insert a space after sentence-ending or clause punctuation when followed by
# any non-whitespace character. Whisper occasionally produces "Hello.World" or
# "yes,but" — this normalises those without disturbing things like "3.14" or
# "e.g." much (digits / further punctuation aren't word characters anyway,
# but we restrict the lookahead to non-space rather than letters specifically
# so e.g. "Wait!Now" -> "Wait! Now" while "3.14" stays "3.14" since 1 is digit
# but we still don't insert there because we only insert if followed by
# non-space — so "3.14" becomes "3. 14", which is wrong; restrict to letters).
_PUNCT_SPACE = re.compile(r"([.!?,;:])(?=[A-Za-z])")


def normalize_punctuation(text: str) -> str:
    """Add a missing space after . ? ! , ; : when immediately followed by a letter."""
    return _PUNCT_SPACE.sub(r"\1 ", text)


class TranscriptionEngine(QObject):
    transcription_ready = pyqtSignal(str)
    model_loading = pyqtSignal(bool)
    model_ready = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, settings) -> None:
        super().__init__()
        self.settings = settings
        self._queue: Queue = Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._model = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="TextWhisper-Transcribe", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._queue.put(None)
        if self._thread:
            self._thread.join(timeout=3)
        self._thread = None
        self._model = None

    def submit(self, audio: np.ndarray) -> None:
        self._queue.put(audio)

    def _resolve_device(self) -> tuple[str, str]:
        device = self.settings.get("device", "cuda")
        compute = self.settings.get("compute_type", "float16")
        if device == "auto":
            try:
                import ctranslate2

                if ctranslate2.get_cuda_device_count() > 0:
                    return "cuda", compute
            except Exception:
                pass
            return "cpu", "int8"
        if device == "cpu" and compute == "float16":
            compute = "int8"
        return device, compute

    def _load_model(self) -> bool:
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            self.error.emit(f"faster-whisper not installed: {e}")
            return False

        self.model_loading.emit(True)
        try:
            model_size = self.settings.get("model_size", "large-v3")
            device, compute = self._resolve_device()
            log.info("Loading Whisper '%s' on %s/%s ...", model_size, device, compute)
            self._model = WhisperModel(model_size, device=device, compute_type=compute)
            log.info("Whisper model ready (%s on %s/%s).", model_size, device, compute)
            self.model_ready.emit()
            return True
        except Exception as e:
            log.exception("Whisper model load failed")
            self.error.emit(
                f"Failed to load Whisper model ({self.settings.get('model_size')}, "
                f"{self.settings.get('device')}/{self.settings.get('compute_type')}): {e}"
            )
            self._model = None
            return False
        finally:
            self.model_loading.emit(False)

    def _run(self) -> None:
        if not self._load_model():
            return
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.5)
            except Empty:
                continue
            if item is None:
                break
            try:
                self._transcribe(item)
            except Exception as e:
                self.error.emit(f"Transcription failed: {e}")

    def _transcribe(self, audio: np.ndarray) -> None:
        if self._model is None:
            return
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if peak > 1.0:
            audio = audio / peak

        lang = self.settings.get("language", "auto")
        language = None if (lang in (None, "", "auto")) else lang
        duration_s = len(audio) / 16000.0

        segments, info = self._model.transcribe(
            audio,
            language=language,
            beam_size=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 250},
            condition_on_previous_text=False,
        )
        raw = "".join(seg.text for seg in segments).strip()
        text = normalize_punctuation(raw)
        log.info(
            "Transcribed %.2fs (peak=%.3f, lang=%s) -> %r",
            duration_s,
            peak,
            getattr(info, "language", language) if info else language,
            text,
        )
        if text:
            self.transcription_ready.emit(text)
        else:
            log.info("Empty transcription — nothing typed.")
