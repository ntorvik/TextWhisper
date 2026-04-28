# Voice Agent Polish (v1.4.0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Kill the Bluetooth-speaker → mic acoustic feedback loop, add an audio-output-device picker, and reorganize Settings tabs around the two real pipelines (Dictation and Voice Read-Back).

**Architecture:** Auto-mute the mic capture pipeline whenever Piper TTS is speaking by wiring `TTSService.speak_started` / `speak_finished` Qt signals to a small `MicMuter` helper that calls `AudioCapture.stop()` / scheduled `AudioCapture.start()` with a hardcoded 500 ms post-playback grace. Extend `voice_interrupt_hotkey` to also call a new `AudioCapture.flush()` that drops any partially-buffered echo and discards the next finished segment. New `audio_output_device` setting threads through `SoundPlayer` and `TTSService`. Settings dialog merges `Speech`+`Output` → `Dictation`, renames `Voice` → `Voice Read-Back`, and adds a new `Devices` tab.

**Tech Stack:** Python 3.12, PyQt6, sounddevice (portaudio), Piper TTS, faster-whisper, pytest.

**Branch:** `voice-agent-polish` (create from `main` at the start; merge back at the end).

**Repo root for this plan:** `E:/AI/myapps/textwhisper` (no worktree this time — branch directly).

**Spec:** `docs/superpowers/specs/2026-04-27-voice-agent-polish-design.md`

**Test runner:** `./venv/Scripts/python.exe -m pytest tests/ -q`

---

## Task 0: Branch + baseline

**Files:** none modified.

- [ ] **Step 1: Create the feature branch**

```bash
cd E:/AI/myapps/textwhisper
git checkout -b voice-agent-polish
```

- [ ] **Step 2: Confirm baseline**

```bash
git status -sb            # expect clean on voice-agent-polish
./venv/Scripts/python.exe -m pytest tests/ -q
```

Expected: `383 passed, 1 skipped` (or whatever the current main count is — record it; later tasks expect this number + their additions).

No commit.

---

## Task 1: AudioCapture.flush() — drop in-flight segment and discard next emit

**Files:**
- Modify: `src/audio_capture.py`
- Modify: `tests/test_audio_capture.py`

This is the new public method the voice-interrupt path will call. It clears the segment buffer and arms a flag that suppresses the NEXT `segment_ready` emission — covering the case where the buffer has already been concatenated into a finished segment that's queued for the transcriber.

- [ ] **Step 1: Read existing tests**

Open `tests/test_audio_capture.py` to learn the fixture shape (how `AudioCapture` is constructed, how the callback is exercised). The test you write must follow the same idiom.

- [ ] **Step 2: Write the failing test**

Append to `tests/test_audio_capture.py`:

```python
def test_flush_drops_buffered_audio_and_resets_state(qapp, tmp_appdata):
    """flush() clears any partially-captured audio + resets VAD state."""
    import numpy as np
    from src.audio_capture import AudioCapture
    from src.settings_manager import SettingsManager

    sm = SettingsManager()
    cap = AudioCapture(sm)
    # Simulate a partially-captured segment.
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
    # Simulate a fresh segment building up after the flush.
    cap._buffer.append(np.ones(16000, dtype=np.float32))
    cap._buffered_samples = 16000
    cap._has_speech = True
    cap._emit_locked(min_samples=0)

    assert received == [], "first post-flush segment must be discarded"
    assert cap._discard_next_segment is False, "flag must clear after one suppression"

    # A second segment after the flag clears emits normally.
    cap._buffer.append(np.ones(16000, dtype=np.float32))
    cap._buffered_samples = 16000
    cap._has_speech = True
    cap._emit_locked(min_samples=0)
    assert len(received) == 1, "post-flag-clear segment must emit"
```

- [ ] **Step 3: Run test to verify it fails**

```
./venv/Scripts/python.exe -m pytest tests/test_audio_capture.py::test_flush_drops_buffered_audio_and_resets_state tests/test_audio_capture.py::test_flush_then_emit_suppresses_next_segment_ready -v
```

Expected: both FAIL — `AudioCapture` has no `flush` method, no `_discard_next_segment` attr.

- [ ] **Step 4: Implement `flush()` and the suppression flag**

Edit `src/audio_capture.py`.

a) In `__init__` (after the existing `self._preroll = ...` line), initialize the flag:

```python
        # Set by flush() to drop the next finished segment. Used by the
        # voice_interrupt path so a sliver of TTS audio that leaked into
        # the mic before auto-mute kicked in cannot become a paste.
        self._discard_next_segment: bool = False
```

b) Add the public `flush` method (place it above `_callback` for visibility):

```python
    def flush(self) -> None:
        """Clear any partially-captured audio + arm one-shot suppression of
        the next emitted segment.

        Called by the voice_interrupt path. Safe to call when no segment is
        in flight (the buffer is already empty; the flag still arms, which
        is harmless because the next emit is whatever fires next)."""
        with self._lock:
            self._buffer.clear()
            self._buffered_samples = 0
            self._has_speech = False
            self._silence_blocks = 0
            self._discard_next_segment = True
```

c) Modify `_emit_locked` to honor the flag. Replace the existing method body (lines ~151-170) with:

```python
    def _emit_locked(self, min_samples: int) -> None:
        if not self._buffer:
            self._reset_segment()
            return
        audio = np.concatenate(self._buffer)
        self._reset_segment()
        if self._discard_next_segment:
            self._discard_next_segment = False
            log.info(
                "Dropped post-flush segment: %.2fs (%d samples) — "
                "voice_interrupt suppression",
                len(audio) / self.SAMPLE_RATE,
                len(audio),
            )
            return
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
```

- [ ] **Step 5: Run tests to verify they pass**

```
./venv/Scripts/python.exe -m pytest tests/test_audio_capture.py -v
```

Expected: all green (existing + 2 new).

- [ ] **Step 6: Run full suite**

```
./venv/Scripts/python.exe -m pytest tests/ -q
```

Expected: baseline + 2 new tests, all green.

- [ ] **Step 7: Commit**

```bash
git add src/audio_capture.py tests/test_audio_capture.py
git commit -m "$(cat <<'EOF'
AudioCapture.flush(): drop in-flight segment + suppress next emit

Adds a public flush() method that clears the partially-buffered
segment + sets a one-shot _discard_next_segment flag. The next
_emit_locked sees the flag, drops the segment, and clears the
flag. Used by the voice_interrupt path (Task 4) so a sliver of
TTS audio that leaked into the mic before auto-mute kicked in
cannot become a paste.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: New `audio_output_device` setting + thread it through SoundPlayer and TTSService

**Files:**
- Modify: `src/settings_manager.py`
- Modify: `src/sound_player.py`
- Modify: `src/voice.py`
- Modify: `tests/test_settings_manager.py`
- Modify: `tests/test_sound_player.py`
- Modify: `tests/test_voice.py`

Single setting key, two consumers. Low risk; default `None` keeps current behavior identical.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_settings_manager.py`:

```python
def test_audio_output_device_default_is_none(tmp_appdata):
    from src.settings_manager import SettingsManager
    sm = SettingsManager()
    assert sm.get("audio_output_device") is None
```

Append to `tests/test_sound_player.py`:

```python
def test_play_passes_audio_output_device_to_sd(tmp_appdata, qapp):
    from unittest.mock import patch
    from src.settings_manager import SettingsManager
    from src.sound_player import SoundPlayer

    sm = SettingsManager()
    sm.set("audio_output_device", 7)
    sm.set("play_ready_sound", True)
    sp = SoundPlayer(sm)
    with patch("src.sound_player.sd.play") as p:
        sp.play_ready()
    p.assert_called_once()
    # device kwarg or 3rd positional arg.
    call = p.call_args
    device = call.kwargs.get("device")
    if device is None and len(call.args) >= 3:
        device = call.args[2]
    assert device == 7


def test_play_passes_none_when_setting_absent(tmp_appdata, qapp):
    from unittest.mock import patch
    from src.settings_manager import SettingsManager
    from src.sound_player import SoundPlayer

    sm = SettingsManager()
    sm.set("play_ready_sound", True)
    sp = SoundPlayer(sm)
    with patch("src.sound_player.sd.play") as p:
        sp.play_ready()
    call = p.call_args
    device = call.kwargs.get("device")
    if device is None and len(call.args) >= 3:
        device = call.args[2]
    assert device is None
```

Append to `tests/test_voice.py`:

```python
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
    fake_voice.synthesize.return_value = iter([])  # no audio chunks

    with patch.object(svc, "_ensure_voice", return_value=fake_voice), \
         patch("src.voice.sd.OutputStream") as os_cls, \
         patch("piper.config.SynthesisConfig"):
        os_cls.return_value.__enter__.return_value = MagicMock()
        svc._speak_one("hi")

    os_cls.assert_called_once()
    assert os_cls.call_args.kwargs.get("device") == 11
```

- [ ] **Step 2: Run them to confirm failure**

```
./venv/Scripts/python.exe -m pytest tests/test_settings_manager.py::test_audio_output_device_default_is_none tests/test_sound_player.py::test_play_passes_audio_output_device_to_sd tests/test_voice.py::test_speak_one_passes_audio_output_device_to_outputstream -v
```

Expected: 3 FAIL.

- [ ] **Step 3: Add the setting key**

Edit `src/settings_manager.py`. Find the `DEFAULT_CONFIG` dict and add (group with the other audio-related keys, e.g. right after `"microphone_device": None,`):

```python
    "audio_output_device": None,    # int (portaudio device index) or None for system default
```

- [ ] **Step 4: Wire it into `SoundPlayer._play`**

Edit `src/sound_player.py`. Replace the existing `_play` method:

```python
    def _play(self, samples: np.ndarray) -> None:
        if samples.size == 0:
            return
        device = self.settings.get("audio_output_device")
        try:
            sd.play(samples, _SAMPLE_RATE, device=device)
        except Exception:
            # Output device unavailable, busy, etc. — non-fatal.
            log.exception("Sound playback failed")
```

- [ ] **Step 5: Wire it into `TTSService._speak_one`**

Edit `src/voice.py`. Find the `with sd.OutputStream(...)` block in `_speak_one` (around line 154) and add `device=...`:

```python
        device = self.settings.get("audio_output_device")
        try:
            with sd.OutputStream(
                samplerate=sample_rate,
                channels=1,
                dtype="int16",
                device=device,
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
```

(Replace the existing `try/finally` block, preserving the inner loop and the `speak_finished.emit()` in `finally`.)

- [ ] **Step 6: Run tests**

```
./venv/Scripts/python.exe -m pytest tests/test_settings_manager.py tests/test_sound_player.py tests/test_voice.py -v
```

Expected: all green (existing + 3 new).

- [ ] **Step 7: Run full suite**

```
./venv/Scripts/python.exe -m pytest tests/ -q
```

Expected: baseline + 5 new (Task 1's 2 + Task 2's 3), all green.

- [ ] **Step 8: Commit**

```bash
git add src/settings_manager.py src/sound_player.py src/voice.py tests/test_settings_manager.py tests/test_sound_player.py tests/test_voice.py
git commit -m "$(cat <<'EOF'
Audio output device setting threaded through SoundPlayer + TTSService

New audio_output_device key (default None = system default).
SoundPlayer._play passes device= to sd.play; TTSService._speak_one
passes device= to sd.OutputStream. None preserves current behavior
for users who haven't set one.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: MicMuter helper + auto-mute wiring in TextWhisperApp

**Files:**
- Create: `src/mic_muter.py`
- Modify: `src/app.py`
- Create: `tests/test_mic_muter.py`
- Modify: `tests/test_app.py`

Small dedicated helper class so the app stays tidy. Owns the mute state + post-TTS grace timer.

- [ ] **Step 1: Write the failing tests for `MicMuter`**

Create `tests/test_mic_muter.py`:

```python
"""Tests for MicMuter — auto-mute mic during Piper TTS."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


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
    # Still mark muted: speak_finished must still resume.
    assert mm.is_muted is True


def test_mic_muter_resumes_capture_after_grace(qapp, qtbot=None):
    """speak_finished schedules a 500 ms timer; after it fires, audio.start runs."""
    from PyQt6.QtCore import QCoreApplication
    from src.mic_muter import MicMuter

    audio = MagicMock()
    audio.is_running = False  # we stopped it on speak_started
    mm = MicMuter(audio, _resume_grace_ms=10)  # short grace for test
    mm._was_running_before_mute = True  # we started in capture mode
    mm._is_muted = True
    mm.on_tts_finished()
    # Spin the event loop until the QTimer fires.
    import time
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline and not audio.start.called:
        QCoreApplication.processEvents()
        time.sleep(0.005)
    audio.start.assert_called_once()
    assert mm.is_muted is False


def test_mic_muter_does_not_resume_when_was_not_running(qapp):
    """If capture wasn't running before TTS started, don't auto-start it."""
    from PyQt6.QtCore import QCoreApplication
    from src.mic_muter import MicMuter

    audio = MagicMock()
    audio.is_running = False
    mm = MicMuter(audio, _resume_grace_ms=10)
    mm._was_running_before_mute = False
    mm._is_muted = True
    mm.on_tts_finished()
    import time
    deadline = time.monotonic() + 0.1
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.005)
    audio.start.assert_not_called()
    assert mm.is_muted is False


def test_mic_muter_back_to_back_tts_cancels_pending_resume(qapp):
    """Two TTS calls in quick succession: pending resume timer is cancelled."""
    from PyQt6.QtCore import QCoreApplication
    from src.mic_muter import MicMuter

    audio = MagicMock()
    audio.is_running = True
    mm = MicMuter(audio, _resume_grace_ms=200)
    # First speak.
    mm.on_tts_started()
    mm.on_tts_finished()
    # Before the timer fires, second speak starts.
    assert mm._resume_timer.isActive()
    mm.on_tts_started()
    assert not mm._resume_timer.isActive(), "pending resume must be cancelled"
    # Drain remaining event loop time; audio.start should NOT have been called.
    import time
    deadline = time.monotonic() + 0.3
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.005)
    audio.start.assert_not_called()
```

- [ ] **Step 2: Run them to confirm failure**

```
./venv/Scripts/python.exe -m pytest tests/test_mic_muter.py -v
```

Expected: all FAIL — module doesn't exist.

- [ ] **Step 3: Implement `MicMuter`**

Create `src/mic_muter.py`:

```python
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
        # Cancel any pending resume from a prior speak_finished (back-to-back TTS).
        if self._resume_timer.isActive():
            self._resume_timer.stop()
        if self._is_muted:
            # Already muted from a prior TTS; just keep state, no double-stop.
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
        # Defer resume so Bluetooth tail-out and portaudio buffers settle.
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
```

- [ ] **Step 4: Run MicMuter tests**

```
./venv/Scripts/python.exe -m pytest tests/test_mic_muter.py -v
```

Expected: all 5 green.

- [ ] **Step 5: Wire `MicMuter` into `TextWhisperApp`**

Edit `src/app.py`.

a) Add the import near the other component imports at the top:

```python
from .mic_muter import MicMuter
```

b) In `__init__`, instantiate after `self.tts = TTSService(self.settings)` (find that line — it should already exist; if not, place after `self.audio = AudioCapture(...)`):

```python
        self.mic_muter = MicMuter(self.audio)
```

c) In `_wire_signals`, after the existing TTS signal connections (find `self.tts.status.connect(...)` or similar), add:

```python
        self.tts.speak_started.connect(self.mic_muter.on_tts_started)
        self.tts.speak_finished.connect(self.mic_muter.on_tts_finished)
```

- [ ] **Step 6: Add an integration test in `tests/test_app.py`**

Append:

```python
def test_app_wires_tts_signals_to_mic_muter(app):
    """speak_started → mic_muter.on_tts_started; speak_finished → on_tts_finished."""
    from unittest.mock import patch
    with patch.object(app.mic_muter, "on_tts_started") as on_start, \
         patch.object(app.mic_muter, "on_tts_finished") as on_end:
        app.tts.speak_started.emit()
        app.tts.speak_finished.emit()
    on_start.assert_called_once()
    on_end.assert_called_once()
```

- [ ] **Step 7: Run app tests + full suite**

```
./venv/Scripts/python.exe -m pytest tests/test_app.py tests/test_mic_muter.py -v
./venv/Scripts/python.exe -m pytest tests/ -q
```

Expected: all green; baseline + Task 1 (2) + Task 2 (3) + Task 3 (5+1) = baseline + 11.

- [ ] **Step 8: Commit**

```bash
git add src/mic_muter.py src/app.py tests/test_mic_muter.py tests/test_app.py
git commit -m "$(cat <<'EOF'
Auto-mute mic during Piper TTS via new MicMuter helper

Wires TTSService.speak_started → AudioCapture.stop and
speak_finished → 500 ms QTimer → AudioCapture.start. Tracks
"was the mic running before mute?" so we don't auto-START
capture if the user wasn't dictating in the first place.
Back-to-back TTS calls cancel any pending resume timer so the
mic stays down across both utterances.

Closes the Bluetooth-speaker → mic feedback loop without any
user-facing setting (500 ms grace is hardcoded).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: voice_interrupt_hotkey also flushes AudioCapture

**Files:**
- Modify: `src/app.py`
- Modify: `tests/test_app.py`

One-line wiring change. The `voice_interrupt` branch in `_on_hotkey_triggered` already calls `self.tts.interrupt()`; add `self.audio.flush()` next to it.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_app.py`:

```python
def test_voice_interrupt_hotkey_flushes_audio(app):
    """Pressing voice_interrupt cuts TTS AND flushes any in-flight audio
    so a sliver of TTS that leaked into the mic before auto-mute kicked
    in cannot become a paste."""
    from unittest.mock import patch
    with patch.object(app.tts, "interrupt") as ti, \
         patch.object(app.audio, "flush") as af:
        app._on_hotkey_triggered("voice_interrupt")
    ti.assert_called_once()
    af.assert_called_once()
```

- [ ] **Step 2: Run to confirm fail**

```
./venv/Scripts/python.exe -m pytest tests/test_app.py::test_voice_interrupt_hotkey_flushes_audio -v
```

Expected: FAIL — `audio.flush` is not called from the handler.

- [ ] **Step 3: Wire it**

Edit `src/app.py` — find the `voice_interrupt` branch in `_on_hotkey_triggered`. It currently looks like:

```python
        elif name == "voice_interrupt":
            self.tts.interrupt()
```

Replace with:

```python
        elif name == "voice_interrupt":
            self.tts.interrupt()
            # Discard any partially-captured audio + suppress the next
            # finished segment so a sliver of TTS that leaked into the
            # mic before auto-mute kicked in cannot become a paste.
            self.audio.flush()
```

- [ ] **Step 4: Run tests**

```
./venv/Scripts/python.exe -m pytest tests/test_app.py -v
./venv/Scripts/python.exe -m pytest tests/ -q
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/app.py tests/test_app.py
git commit -m "$(cat <<'EOF'
voice_interrupt_hotkey also flushes in-flight audio

The hotkey already stops TTS; now it also calls AudioCapture.flush()
so any half-captured echo (a sliver of TTS that leaked in before the
auto-mute fired) is dropped before becoming a paste.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Settings dialog — Devices tab + Speech/Output merge into Dictation + Voice rename

**Files:**
- Modify: `src/ui/settings_dialog.py`
- Modify: `tests/test_settings_dialog.py`

Single biggest task — but it's almost all Qt scaffolding. Method renames, one new method, one tab order rewrite, and three new persistence lines.

- [ ] **Step 1: Re-read the settings dialog and understand what's where**

Use the Read tool on `src/ui/settings_dialog.py`. Take note of:

- `__init__` (line 140) — tab construction at lines 152-158.
- `_build_speech_tab` (line 322) — has `model_combo`, `device_combo`, `compute_combo`, `mic_combo`, `lang_combo`, `silence_spin`, `thresh_spin`, `continuation_check`, `continuation_window_spin`.
- `_build_output_tab` (line 414) — has `output_method_combo`, `delay_spin`, `auto_enter_check`, `auto_enter_delay_spin`.
- `_build_voice_tab` (line 596) — keep this method body unchanged; only rename it.
- `_save` — find every `self.settings.set(...)` line; nothing about persistence changes except adding `audio_output_device`.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_settings_dialog.py`:

```python
def test_settings_dialog_has_devices_tab_with_both_combos(qapp, tmp_appdata):
    from src.settings_manager import SettingsManager
    from src.ui.settings_dialog import SettingsDialog

    sm = SettingsManager()
    dlg = SettingsDialog(sm)
    names = {w.objectName() for w in dlg.findChildren(object) if w.objectName()}
    assert "mic_combo" in names
    assert "audio_output_combo" in names


def test_settings_dialog_tab_order_matches_spec(qapp, tmp_appdata):
    from src.settings_manager import SettingsManager
    from src.ui.settings_dialog import SettingsDialog

    sm = SettingsManager()
    dlg = SettingsDialog(sm)
    titles = [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())]
    assert titles == [
        "Hotkeys",
        "Devices",
        "Dictation",
        "Paste Lock",
        "Voice Read-Back",
        "Feedback",
        "Oscilloscope",
        "About",
    ]


def test_settings_dialog_dictation_tab_contains_output_widgets(qapp, tmp_appdata):
    """The merged Dictation tab must contain widgets that previously lived
    on the Output tab (output_method_combo, delay_spin, auto_enter_check)."""
    from src.settings_manager import SettingsManager
    from src.ui.settings_dialog import SettingsDialog

    sm = SettingsManager()
    dlg = SettingsDialog(sm)
    # Locate the Dictation tab widget.
    dictation_idx = next(
        i for i in range(dlg.tabs.count()) if dlg.tabs.tabText(i) == "Dictation"
    )
    page = dlg.tabs.widget(dictation_idx)
    assert page.findChild(object, "output_method_combo") is not None
    assert page.findChild(object, "delay_spin") is not None
    assert page.findChild(object, "auto_enter_check") is not None


def test_audio_output_device_persists_through_save(qapp, tmp_appdata):
    from src.settings_manager import SettingsManager
    from src.ui.settings_dialog import SettingsDialog

    sm = SettingsManager()
    dlg = SettingsDialog(sm)
    combo = dlg.findChild(object, "audio_output_combo")
    assert combo is not None
    # Force a non-default selection if any exists (otherwise stays None).
    if combo.count() > 1:
        combo.setCurrentIndex(1)
        expected = combo.itemData(1)
    else:
        expected = None
    dlg.accept = lambda: None
    dlg._save()
    assert sm.get("audio_output_device") == expected
```

NOTE: the existing tests reference `_build_speech_tab` / `_build_output_tab` / `_build_voice_tab` only by name (e.g. tab title strings). After this task, tab titles change. If a test asserts on `tabText(i) == "Speech"`, update it. Run the full suite during step 4 to find these.

- [ ] **Step 3: Run failing tests**

```
./venv/Scripts/python.exe -m pytest tests/test_settings_dialog.py -v
```

Expected: 4 new tests FAIL.

- [ ] **Step 4: Implement changes**

Edit `src/ui/settings_dialog.py`.

a) **Add object names to existing widgets** so the tests can find them. In the existing `_build_speech_tab` (about to be renamed) and `_build_output_tab` (about to be merged in), add `setObjectName` calls right after each control creation:

```python
        self.mic_combo = QComboBox()
        self.mic_combo.setObjectName("mic_combo")  # NEW LINE
```

```python
        self.output_method_combo = QComboBox()
        self.output_method_combo.setObjectName("output_method_combo")  # NEW LINE
```

```python
        self.delay_spin = QSpinBox()
        self.delay_spin.setObjectName("delay_spin")  # NEW LINE
```

```python
        self.auto_enter_check = QCheckBox(...)
        self.auto_enter_check.setObjectName("auto_enter_check")  # NEW LINE
```

b) **Rename `_build_speech_tab` → `_build_dictation_tab` and merge the Output controls into it.** The whole method becomes:

```python
    def _build_dictation_tab(self) -> QWidget:
        """Speech-to-text pipeline: Whisper engine + VAD + text output method.

        Replaces the previous separate Speech and Output tabs (v1.4.0)."""
        page = QWidget()
        form = QFormLayout(page)

        self.model_combo = QComboBox()
        self.model_combo.addItems(
            ["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"]
        )
        self.model_combo.setCurrentText(str(self.settings.get("model_size", "large-v3")))
        form.addRow("Whisper model:", self.model_combo)

        self.device_combo = QComboBox()
        self.device_combo.addItems(["cuda", "cpu", "auto"])
        self.device_combo.setCurrentText(str(self.settings.get("device", "cuda")))
        form.addRow("Compute device:", self.device_combo)

        self.compute_combo = QComboBox()
        self.compute_combo.addItems(["float16", "int8_float16", "int8", "float32"])
        self.compute_combo.setCurrentText(str(self.settings.get("compute_type", "float16")))
        form.addRow("Compute type:", self.compute_combo)

        self.lang_combo = QComboBox()
        self.lang_combo.setEditable(True)
        self.lang_combo.addItems(_LANGUAGES)
        self.lang_combo.setCurrentText(str(self.settings.get("language", "auto")))
        form.addRow("Language:", self.lang_combo)

        self.silence_spin = QSpinBox()
        self.silence_spin.setRange(200, 3000)
        self.silence_spin.setSingleStep(50)
        self.silence_spin.setSuffix(" ms")
        self.silence_spin.setValue(int(self.settings.get("vad_silence_ms", 700)))
        form.addRow("Silence pause:", self.silence_spin)

        self.thresh_spin = QDoubleSpinBox()
        self.thresh_spin.setRange(0.001, 0.5)
        self.thresh_spin.setSingleStep(0.002)
        self.thresh_spin.setDecimals(3)
        self.thresh_spin.setValue(float(self.settings.get("vad_threshold", 0.012)))
        form.addRow("Voice threshold (RMS):", self.thresh_spin)

        self.continuation_check = QCheckBox("Treat short pauses as commas")
        self.continuation_check.setChecked(
            bool(self.settings.get("continuation_detection_enabled", False))
        )
        self.continuation_check.setToolTip(
            "Whisper transcribes each VAD-cut segment in isolation and ends "
            "every segment with a period — even when you were just taking a "
            "breath mid-sentence.\n\n"
            "When enabled, if you resume speaking within the continuation "
            "window of a typed segment that ended in '.', that period is "
            "demoted to ',' and the next segment's first letter is "
            "lowercased. Result: one flowing sentence instead of choppy "
            "stand-alone sentences."
        )
        form.addRow("Continuation:", self.continuation_check)

        self.continuation_window_spin = QSpinBox()
        self.continuation_window_spin.setRange(100, 2000)
        self.continuation_window_spin.setSingleStep(50)
        self.continuation_window_spin.setSuffix(" ms")
        self.continuation_window_spin.setValue(
            int(self.settings.get("continuation_window_ms", 500))
        )
        self.continuation_window_spin.setToolTip(
            "How quickly you must resume speaking after a typed segment for "
            "its trailing period to be demoted to a comma."
        )
        self.continuation_window_spin.setEnabled(self.continuation_check.isChecked())
        self.continuation_check.toggled.connect(
            self.continuation_window_spin.setEnabled
        )
        form.addRow("Continuation window:", self.continuation_window_spin)

        # --- Text output method (was on the separate Output tab) ---
        self.output_method_combo = QComboBox()
        self.output_method_combo.setObjectName("output_method_combo")
        self.output_method_combo.addItem("Type (char-by-char keystrokes)", "type")
        self.output_method_combo.addItem("Paste (clipboard + Ctrl+V)", "paste")
        current_method = str(self.settings.get("output_method", "type"))
        for i in range(self.output_method_combo.count()):
            if self.output_method_combo.itemData(i) == current_method:
                self.output_method_combo.setCurrentIndex(i)
                break
        self.output_method_combo.setToolTip(
            "Type: simulates keystrokes for each character. Works in most apps.\n"
            "Paste: writes the text to the clipboard and sends Ctrl+V. More "
            "reliable in terminal apps (Claude Code, Windows Terminal, IDE "
            "consoles) where bare-character injection sometimes drops spaces."
        )
        form.addRow("Output method:", self.output_method_combo)

        self.delay_spin = QSpinBox()
        self.delay_spin.setObjectName("delay_spin")
        self.delay_spin.setRange(0, 50)
        self.delay_spin.setSuffix(" ms")
        self.delay_spin.setValue(int(self.settings.get("type_delay_ms", 4)))
        form.addRow("Per-character type delay:", self.delay_spin)

        self.auto_enter_check = QCheckBox(
            "Auto-press Enter after each transcription (hands-free)"
        )
        self.auto_enter_check.setObjectName("auto_enter_check")
        self.auto_enter_check.setChecked(
            bool(self.settings.get("auto_enter_enabled", False))
        )
        self.auto_enter_check.setToolTip(
            "After your transcription is typed, automatically press Enter "
            "after the delay below — useful for fully hands-free chat / "
            "Claude Code workflows.\n\n"
            "Pressing ANY key during the delay silently cancels that pending "
            "Enter. The next transcription re-arms it."
        )
        form.addRow("Auto-Enter:", self.auto_enter_check)

        self.auto_enter_delay_spin = QSpinBox()
        self.auto_enter_delay_spin.setRange(200, 30000)
        self.auto_enter_delay_spin.setSingleStep(250)
        self.auto_enter_delay_spin.setSuffix(" ms")
        self.auto_enter_delay_spin.setValue(
            int(self.settings.get("auto_enter_delay_ms", 3000))
        )
        form.addRow("Auto-Enter delay:", self.auto_enter_delay_spin)
        return page
```

c) **Delete the old `_build_output_tab` method entirely** (it was merged in above). Remove lines from `def _build_output_tab` through its `return page`.

d) **Add the new `_build_devices_tab` method.** Place it just after the new `_build_dictation_tab`:

```python
    def _build_devices_tab(self) -> QWidget:
        """Audio I/O hardware: microphone input and speaker output."""
        page = QWidget()
        form = QFormLayout(page)

        self.mic_combo = QComboBox()
        self.mic_combo.setObjectName("mic_combo")
        self.mic_combo.addItem("System default", None)
        try:
            for idx, dev in enumerate(sd.query_devices()):
                if int(dev.get("max_input_channels", 0)) > 0:
                    self.mic_combo.addItem(f"{idx}: {dev['name']}", idx)
        except Exception as e:
            self.mic_combo.addItem(f"(error listing devices: {e})", None)
        current_mic = self.settings.get("microphone_device")
        for i in range(self.mic_combo.count()):
            if self.mic_combo.itemData(i) == current_mic:
                self.mic_combo.setCurrentIndex(i)
                break
        form.addRow("Microphone input:", self.mic_combo)

        self.audio_output_combo = QComboBox()
        self.audio_output_combo.setObjectName("audio_output_combo")
        self.audio_output_combo.addItem("System default", None)
        try:
            for idx, dev in enumerate(sd.query_devices()):
                if int(dev.get("max_output_channels", 0)) > 0:
                    self.audio_output_combo.addItem(f"{idx}: {dev['name']}", idx)
        except Exception as e:
            self.audio_output_combo.addItem(f"(error listing devices: {e})", None)
        current_out = self.settings.get("audio_output_device")
        for i in range(self.audio_output_combo.count()):
            if self.audio_output_combo.itemData(i) == current_out:
                self.audio_output_combo.setCurrentIndex(i)
                break
        self.audio_output_combo.setToolTip(
            "Where chimes and Piper TTS read-back are routed. Useful when "
            "you have a Bluetooth headset for AI voice + a separate speaker "
            "for system audio. Changes apply immediately on Save."
        )
        form.addRow("Audio output:", self.audio_output_combo)

        return page
```

e) **Rename `_build_voice_tab` → `_build_voice_readback_tab`.** Update all references (caller in `__init__`). The body of the method stays unchanged.

f) **Rewrite the tab construction in `__init__` (around line 152).** Replace the existing block:

```python
        self.tabs.addTab(self._build_hotkeys_tab(), "Hotkeys")
        self.tabs.addTab(self._build_paste_lock_tab(), "Paste Lock")
        self.tabs.addTab(self._build_speech_tab(), "Speech")
        self.tabs.addTab(self._build_output_tab(), "Output")
        self.tabs.addTab(self._build_feedback_tab(), "Feedback")
        self.tabs.addTab(self._build_oscilloscope_tab(), "Oscilloscope")
        self.tabs.addTab(self._build_voice_tab(), "Voice")
        self.tabs.addTab(self._build_about_tab(), "About")
```

with:

```python
        self.tabs.addTab(self._build_hotkeys_tab(), "Hotkeys")
        self.tabs.addTab(self._build_devices_tab(), "Devices")
        self.tabs.addTab(self._build_dictation_tab(), "Dictation")
        self.tabs.addTab(self._build_paste_lock_tab(), "Paste Lock")
        self.tabs.addTab(self._build_voice_readback_tab(), "Voice Read-Back")
        self.tabs.addTab(self._build_feedback_tab(), "Feedback")
        self.tabs.addTab(self._build_oscilloscope_tab(), "Oscilloscope")
        self.tabs.addTab(self._build_about_tab(), "About")
```

(Note: the existing v1.3.0 layout had Paste Lock at position 1 because Task 17 inserted it. After the reorg, Paste Lock moves to position 3. This matches the spec's tab order.)

g) **Add `audio_output_device` persistence in `_save`.** Find the existing `self.settings.set("microphone_device", ...)` line and add immediately after:

```python
        self.settings.set("audio_output_device", self.audio_output_combo.currentData())
```

(`microphone_device` itself stays unchanged in `_save` — the line already reads from `self.mic_combo`, which now lives on the Devices tab but the attribute name didn't change.)

- [ ] **Step 5: Run dialog tests**

```
./venv/Scripts/python.exe -m pytest tests/test_settings_dialog.py -v
```

Expected: 4 new tests pass; existing tests pass.

- [ ] **Step 6: Run full suite to catch any test that asserted on tab titles**

```
./venv/Scripts/python.exe -m pytest tests/ -q
```

Expected: green. If any test fails because it referenced `"Speech"` / `"Output"` / `"Voice"` tab titles directly, update those test assertions to match the new titles (the dialog code itself is correct).

- [ ] **Step 7: Commit**

```bash
git add src/ui/settings_dialog.py tests/test_settings_dialog.py
git commit -m "$(cat <<'EOF'
Settings: Devices tab + merge Speech+Output → Dictation, rename Voice

Tab reorg per spec §5.7:
  Hotkeys → Devices → Dictation → Paste Lock →
  Voice Read-Back → Feedback → Oscilloscope → About

- New Devices tab: microphone input (moved from Speech) +
  audio_output_device (new combo, persists to settings).
- Speech + Output tabs merged into a single Dictation tab —
  every control on it matters for the same user task.
- Voice tab renamed Voice Read-Back for clarity (only the title
  + builder method name change; widget contents unchanged).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Verification gate

**Files:** none modified.

- [ ] **Step 1: Run the full suite**

```
./venv/Scripts/python.exe -m pytest tests/ -q
```

Expected: baseline + 11 new (Task 1×2, Task 2×3, Task 3×6, Task 4×1, Task 5×4 → +16 not 11; recount during execution and update if needed). **All green.**

- [ ] **Step 2: Import smoke**

```
./venv/Scripts/python.exe -c "from src.app import TextWhisperApp; from src.mic_muter import MicMuter; print('OK')"
```

Expected: `OK`.

- [ ] **Step 3: Manual sanity check (optional, but recommended for the UX side)**

Launch the app, open Settings, eyeball the new tab order. Confirm:
- 8 tabs in order: Hotkeys, Devices, Dictation, Paste Lock, Voice Read-Back, Feedback, Oscilloscope, About.
- Devices tab shows mic + audio output combos.
- Dictation tab has Whisper model + output method + auto-enter all together.
- Voice Read-Back tab still has all the Piper/summarize/API key controls.

Save, close, reopen — verify all selections round-trip.

No commit. If anything failed, return to the relevant task and fix.

---

## Task 7: Version bump v1.4.0 + push

**Files:**
- Modify: `src/__init__.py`

- [ ] **Step 1: Bump version**

Edit `src/__init__.py`:

```python
__version__ = "1.4.0"
```

- [ ] **Step 2: Final test run**

```
./venv/Scripts/python.exe -m pytest tests/ -q
```

Expected: all green.

- [ ] **Step 3: Commit**

```bash
git add src/__init__.py
git commit -m "$(cat <<'EOF'
Bump version to 1.4.0 — voice agent polish

Adds:
- Auto-mute mic during Piper TTS playback (kills the
  Bluetooth-speaker → mic acoustic feedback loop)
- voice_interrupt_hotkey also flushes any in-flight ASR buffer
- audio_output_device setting (route chimes + TTS to a chosen
  speaker)
- Settings tab reorg: new Devices tab; Speech+Output merged into
  Dictation; Voice renamed Voice Read-Back

No new external API surface. Old v1.3.0 configs load cleanly.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Merge to main + push**

```bash
git checkout main
git merge --no-ff voice-agent-polish -m "Merge voice-agent-polish: v1.4.0"
git push origin main
git branch -d voice-agent-polish
```

If `git push` errors with 403 / Tavant account: `gh auth switch --user ntorvik` first.

---

## Self-Review Notes

**Spec coverage walkthrough:**
- Spec §3 D1 (TTS signals) → Task 3
- D2 (stop/start, not pause) → Task 3
- D3 (500 ms hardcoded) → Task 3 (`_DEFAULT_RESUME_GRACE_MS`)
- D4 (mid-dictation cut acceptable) → Task 3 (test `test_mic_muter_does_not_resume_when_was_not_running` covers the converse; cut behavior is implicit in `audio.stop()`)
- D5 (interrupt also flushes) → Tasks 1 + 4
- D6 (`audio_output_device: int|None`) → Task 2
- D7 (enumerate at dialog open) → Task 5
- D8 (device kwarg threaded) → Task 2
- D9 (8-tab order) → Task 5
- D10 (Speech+Output merge) → Task 5
- D11 (no setting renames) → Task 2 (only adds, no rename)

**§5.1–§5.8 component coverage:** every section maps to a task. §5.8 tests are inlined into each task.

**§6 edge cases:**
- #1 back-to-back TTS → Task 3 test `test_mic_muter_back_to_back_tts_cancels_pending_resume`
- #2 mid-dictation cut → Task 3 (audio.stop is explicit; user pain accepted per D4)
- #3 output device unplugged → existing TTSService error path; `SoundPlayer._play` swallows; covered by current behavior
- #4 device toggle mid-sentence → next `_speak_one` reads fresh setting; in-flight stream finishes on old device (acceptable)
- #5 interrupt with no TTS → Task 1 test (`test_flush_drops_buffered_audio_and_resets_state` runs against empty buffer fine)
- #6 old config without key → Task 2 test (`test_audio_output_device_default_is_none`)
- #7 bookmarked tabs not applicable → no test needed

**Placeholder scan:** No "TBD"/"TODO"/"implement later" anywhere. Each code block has the actual content. Test bodies are real assertions. Commit messages are filled in.

**Type/method consistency:**
- `AudioCapture.flush()` — used identically in Tasks 1 and 4.
- `MicMuter.on_tts_started` / `on_tts_finished` — used identically in Tasks 3 (definition) and integration test.
- `audio_output_device` setting key — used identically in Tasks 2 (consumer) and 5 (UI persistence).
- Tab title strings match between Task 5 implementation and Task 5 tests.

**Test count expectation:**
- Task 1: +2
- Task 2: +3
- Task 3: +5 (mic_muter) +1 (app integration) = +6
- Task 4: +1
- Task 5: +4
- **Total new tests: +16.** Baseline (post-Task 0) + 16 = ~399 expected at Task 6.
