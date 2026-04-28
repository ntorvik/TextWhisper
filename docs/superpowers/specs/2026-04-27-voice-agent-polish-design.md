# Voice Agent Polish (v1.4.0) — Design

**Date:** 2026-04-27
**Status:** Design — pending implementation

## 1. Why

Two related pain points surfaced once voice read-back went into daily use against a Bluetooth speaker:

1. **Acoustic feedback loop.** Piper TTS plays Claude's response through the speaker; the mic picks up the speaker output; Whisper transcribes it; auto-enter pastes it as the next prompt; repeat until interrupt. Today the only mitigation is the `voice_interrupt_hotkey`, which only stops the TTS — the in-flight audio fragment still gets transcribed and pasted.
2. **No way to pick the audio output device.** Settings exposes microphone selection but not speaker selection. A user routing TTS to one device while keeping system sounds on another (the common case for Bluetooth headsets, headphones, etc.) has no way to do it.

A third concern is structural: the `Speech` and `Output` settings tabs both configure the same dictation pipeline, and `Voice` is the only tab that's purely about TTS. The naming makes the boundary fuzzy.

## 2. Scope

In:
- Auto-mute the mic capture pipeline whenever Piper TTS is speaking, with a fixed 500 ms post-playback grace period.
- Extend `voice_interrupt_hotkey` so it also discards any in-flight transcription buffer.
- New `audio_output_device` setting + UI control. Wire it into `SoundPlayer` and `TTSService`.
- Settings tab reorg: merge `Speech` + `Output` into a single `Dictation` tab; rename `Voice` → `Voice Read-Back`; new `Devices` tab containing the mic and speaker pickers.

Out:
- Acoustic echo cancellation (true barge-in). Out of scope; would require a third-party DSP library and Bluetooth-latency alignment work.
- Wake-word detection. Out of scope.
- Per-event audio routing (different device for chimes vs TTS). One global output device for all app-generated audio.
- A configurable grace period. Hardcoded constant in v1.4.0; promote to a setting only if it ever fails for a real user.
- A "Test output" button on the Devices tab. YAGNI — the existing chimes already exercise the output path on every dictation cycle.

## 3. Decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | Auto-mute is wired via existing `TTSService.speak_started` / `speak_finished` Qt signals, not by polling state. | Signals already exist; no new threading hazards. |
| D2 | Mute = `AudioCapture.stop()`; resume = `AudioCapture.start()` after a 500 ms `QTimer.singleShot`. Not a "pause" semantic. | `AudioCapture` has no pause API; stop/start is what already works for hotkey-driven dictation. |
| D3 | Grace period is 500 ms, hardcoded in code (no setting). | Bluetooth speaker tail-out + portaudio buffering settle inside that window for typical hardware. YAGNI on a setting until evidence shows it varies. |
| D4 | If the user is mid-dictation when TTS starts, the dictation is cut short (lose the in-progress utterance). | Acceptable trade-off vs. the alternative (continued feedback loop). The case is rare in practice — TTS only fires on Claude's `Stop` event, which is not concurrent with user dictation. |
| D5 | `voice_interrupt_hotkey` ALSO calls a new `AudioCapture.flush()` method that drops any partially-captured audio + sets a "discard next transcription result" flag. | Closes the worst-case glitch where a sliver of TTS leaks into the mic before mute kicks in. |
| D6 | New setting `audio_output_device: int \| None` (None = system default). Stored in `SettingsManager.DEFAULT_CONFIG`. | Mirrors the existing `microphone_device: None` shape. |
| D7 | Output device is enumerated via `sd.query_devices(kind='output')` at dialog-open time, not cached. | Matches existing pattern for the mic combo box; handles hot-plug. |
| D8 | `SoundPlayer._play` switches from `sd.play(samples, _SAMPLE_RATE)` to `sd.play(samples, _SAMPLE_RATE, device=self._output_device())` where `_output_device` reads the setting. `TTSService._speak_one` adds `device=...` to the `sd.OutputStream(...)` constructor. | Smallest possible diff to wire routing. |
| D9 | Tab order: Hotkeys → Devices → Dictation → Voice Read-Back → Feedback → Oscilloscope → Paste Lock → About (8 tabs total, down from 9). | Pipeline-aligned, fewer tabs, no overcrowding. |
| D10 | `Speech` tab merged into `Dictation`: model, compute device, language, VAD silence/threshold, continuation detection, paste/type method, type delay all live on one tab. `microphone_device` moves to `Devices`. | Every control on the Dictation tab matters for one user task; the merge eliminates the "which tab has the type delay?" hunt. |
| D11 | No setting key renames; only widget-position changes and one new key. Settings file from v1.3.0 loads cleanly into v1.4.0. | Avoids migration complexity; old configs Just Work. |

## 4. Architecture

```
                ┌────────────────────────────────┐
                │       TextWhisperApp           │
                │                                │
   speak_started│                                │  flush()
   ────────────▶│  MicMuter._on_tts_started ───▶ │ AudioCapture.stop()
   speak_finished                                │
   ────────────▶│  MicMuter._on_tts_finished ──▶ │ QTimer 500ms ──▶ AudioCapture.start()
                │                                │
   voice_interrupt                               │
   ────────────▶│  _on_hotkey_triggered ────────▶│ TTSService.interrupt()
                │                                │ AudioCapture.flush()
                │                                │
                └────────────────────────────────┘
                       │                ▲
                       │                │
                       ▼                │
                ┌──────────────┐  ┌─────────────────┐
                │ TTSService   │  │ AudioCapture    │
                │ sd.Output    │  │ sd.InputStream  │
                │ (device=...) │  │ (device=mic)    │
                └──────────────┘  └─────────────────┘
                       │
                       ▼
                ┌──────────────┐
                │ SoundPlayer  │
                │ sd.play      │
                │ (device=...) │
                └──────────────┘
```

`MicMuter` is a small new class (or set of methods directly on `TextWhisperApp` if it stays small enough). It owns the post-TTS grace timer and the active-mute state so that two back-to-back TTS calls don't race the resume timer.

## 5. Components

### 5.1 `src/audio_capture.py` — new public `flush()` method

```python
def flush(self) -> None:
    """Discard any partially-buffered audio + mark the next finished
    segment as discardable. Used by the voice_interrupt path so a
    sliver of TTS that leaked into the mic before auto-mute kicked
    in cannot become a paste."""
```

Internals: clears `self._buffer` (or whatever the segment buffer is named), sets a `_discard_next` flag that the existing emit-on-segment-end path checks before forwarding to the transcription engine.

### 5.2 `src/app.py` — auto-mute wiring

A new `MicMuter` helper (instance attribute on `TextWhisperApp`) owns:
- `is_muted: bool`
- `_resume_timer: QTimer` (single-shot, 500 ms)
- `on_tts_started()` slot — calls `audio.stop()`, sets `is_muted = True`, cancels any pending resume timer.
- `on_tts_finished()` slot — starts the 500 ms timer; on fire, calls `audio.start()` and clears `is_muted`.

Wiring in `_wire_signals`:
```python
self.tts.speak_started.connect(self.mic_muter.on_tts_started)
self.tts.speak_finished.connect(self.mic_muter.on_tts_finished)
```

The 500 ms constant lives in `mic_muter.py` as `_RESUME_GRACE_MS = 500`.

### 5.3 `src/app.py` — interrupt flush

In the existing `_on_hotkey_triggered("voice_interrupt")` branch, add one line after the existing `self.tts.interrupt()` call:

```python
self.audio.flush()
```

### 5.4 `src/sound_player.py` — output device

`SoundPlayer.__init__` reads `audio_output_device` from settings on first use. `_play` passes it to `sd.play`:

```python
def _output_device(self):
    val = self.settings.get("audio_output_device")
    return val  # None falls through to portaudio default

def _play(self, samples: np.ndarray) -> None:
    sd.play(samples, _SAMPLE_RATE, device=self._output_device())
```

The setting is read on every `_play` call (not cached) so a Settings-dialog change takes effect without restart.

### 5.5 `src/voice.py` — output device on TTS stream

`TTSService._speak_one` adds `device=` to the `OutputStream` constructor:

```python
device = self.settings.get("audio_output_device")
with sd.OutputStream(
    samplerate=...,
    channels=...,
    dtype=...,
    device=device,
) as stream:
    ...
```

`device=None` falls through to portaudio default — no change in behavior for users who haven't set one.

### 5.6 `src/settings_manager.py` — new key

Add to `DEFAULT_CONFIG`:
```python
"audio_output_device": None,
```

No migration logic. Old config files without this key get the default on first load via existing `.get(key, default)` semantics.

### 5.7 `src/ui/settings_dialog.py` — tab reorg

Tabs in `__init__`:
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

Method renames:
- `_build_speech_tab` → `_build_dictation_tab`. Add the existing Output-tab controls (paste/type combo, type delay spin) at the bottom. Remove the `microphone_device` combo (moves to Devices).
- `_build_output_tab` → deleted; its contents are absorbed into `_build_dictation_tab`.
- `_build_voice_tab` → `_build_voice_readback_tab` (no behavior change, name only — for consistency with the tab title).

New method `_build_devices_tab`:
- `QComboBox` for microphone — populated from `sd.query_devices(kind='input')`. Re-uses the existing combo-build logic from the old Speech tab.
- `QComboBox` for audio output — populated from `sd.query_devices(kind='output')`. New code, mirrors the mic combo's pattern.
- Both combo boxes have a "(System default)" first entry that maps to `None`.

`_save` updates: persist `audio_output_device` from the new combo. The existing `microphone_device` save line stays unchanged (just reads from the combo on the new tab instead of the old one).

### 5.8 Tests

New tests:
- `test_mic_muter_pauses_on_tts_started`
- `test_mic_muter_resumes_after_grace_on_tts_finished` (use `qtbot.waitUntil` or fake the timer)
- `test_voice_interrupt_calls_audio_flush`
- `test_audio_capture_flush_drops_buffered_segment`
- `test_settings_dialog_has_devices_tab_with_both_combos`
- `test_audio_output_device_setting_persists`
- `test_sound_player_passes_device_to_sd_play`
- `test_tts_service_passes_device_to_output_stream`

Existing tests that reference `_build_speech_tab` / `_build_output_tab` / `_build_voice_tab` need their tab-name strings updated. The tab-finding tests should keep working because they look up by `objectName`, not tab title — but verify during implementation.

## 6. Edge cases

1. **Two TTS calls in rapid succession.** `speak_started → mute, speak_finished → schedule resume, speak_started again before resume fires → cancel resume, stay muted.` `MicMuter` tracks the timer so it can be canceled.
2. **TTS starts while user is mid-dictation.** D4: cut the dictation. The active `AudioCapture` segment is dropped because `stop()` ends the stream.
3. **Output device unplugged at runtime.** `sd.OutputStream` raises; `TTSService` already catches and emits `error` signal. `SoundPlayer._play` will swallow the exception silently — same as today's behavior when audio fails.
4. **User toggles `audio_output_device` while TTS is mid-sentence.** New device takes effect on the NEXT `_speak_one` invocation; the in-flight stream finishes on the old device. Acceptable.
5. **`voice_interrupt_hotkey` fires when no TTS is playing.** `TTSService.interrupt()` is a no-op (already); `AudioCapture.flush()` is also safe to call when no segment is in flight (clears an empty buffer, no-op).
6. **Old config without `audio_output_device` key.** `.get("audio_output_device")` returns None via DEFAULT_CONFIG; portaudio default is used. No migration needed.
7. **Speech + Output tab merge breaks bookmarked tabs.** Not applicable — tab order isn't persisted; the dialog opens to tab 0 every time.

## 7. Settings keys

```python
# DEFAULT_CONFIG additions:
"audio_output_device": None,    # int (portaudio device index) or None for system default

# Existing keys preserved (no rename):
"microphone_device": None,      # moves UI position only
```

## 8. Acceptance criteria

1. With Bluetooth speaker + mic + Claude Code Stop hook installed, ten consecutive Claude turns complete with no feedback-loop paste.
2. Pressing `voice_interrupt_hotkey` mid-TTS stops the read-back AND prevents any pasted echo even if the mic was already capturing.
3. Settings → Devices shows both mic and speaker pickers; selecting a non-default speaker routes Piper TTS and chimes through it after Save (no app restart).
4. The 8-tab layout matches §5.7's order; all existing settings round-trip through Save → close → reopen.
5. v1.3.0 config files load v1.4.0 without errors.

## 9. Out-of-scope (deferred)

- AEC / true barge-in.
- Wake-word "Hey TextWhisper" trigger.
- Per-event device routing (chimes on speaker A, TTS on speaker B).
- Configurable post-TTS grace period.
- "Test output" / "Test mic" buttons on the Devices tab.
