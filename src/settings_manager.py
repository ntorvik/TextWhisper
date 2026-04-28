import copy
import json
import os
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal

DEFAULT_CONFIG: dict[str, Any] = {
    "hotkey": "<alt>+z",
    "delete_hotkey": "<delete>",
    "delete_double_tap_ms": 350,
    "clipboard_enabled": True,
    "notifications_enabled": True,
    "play_ready_sound": True,
    "play_stop_sound": False,
    "sound_volume": 0.15,
    # Auto-Enter: after a transcription is typed, optionally press Enter for
    # you N ms later — useful for hands-free Claude Code / chat workflows.
    # Cancelled silently if you press ANY key during the window.
    "auto_enter_enabled": False,
    "auto_enter_delay_ms": 3000,
    # "Treat short pauses as commas": Whisper transcribes each VAD-cut
    # segment in isolation and reflexively ends each one with a period —
    # even when the user was just taking a breath mid-sentence. With this
    # enabled, if the user resumes speaking within `continuation_window_ms`
    # of a typed segment that ended in '.', that period is rewritten as a
    # ',' and the next segment's first letter is lowercased.
    "continuation_detection_enabled": False,
    "continuation_window_ms": 500,
    # Voice (TTS read-back of Claude Code responses).
    # The hands-free other half of TextWhisper: when Claude Code finishes a
    # response, a Stop hook hands the assistant message to TextWhisper,
    # Anthropic Haiku summarises it into a conversational 2-3 sentence
    # read-back, and Piper speaks it aloud via the user's chosen voice.
    "voice_enabled": False,
    "voice_engine": "piper",  # "piper" — neural local; future: "sapi", "elevenlabs"
    "voice_model": "en_US-amy-medium",  # Piper voice model identifier
    "voice_rate": 1.0,    # 0.5 .. 2.0 — multiplier for speech rate
    "voice_volume": 0.85, # 0.0 .. 1.0
    "voice_summarize": True,  # if False, read raw assistant text verbatim
    "voice_summarize_model": "claude-haiku-4-5",
    # Follow-up invitation gate. The summariser appends a short, varied
    # "want me to walk through it?" line ONLY when the raw response was
    # substantial — otherwise it's grating to hear the offer every turn.
    # Substantial = ANY of: char_count > min_chars, contains a fenced
    # code block (when invite_on_code), or paragraph_count >= min_paragraphs.
    # Tuning lives in config.json only; not exposed in the Settings UI.
    "voice_followup_min_chars": 800,
    "voice_followup_min_paragraphs": 3,
    "voice_followup_invite_on_code": True,
    # API key lives ONLY in config.json (which is in %APPDATA%, never in
    # the repo) or in the ANTHROPIC_API_KEY env var. Never logged. Empty
    # string means "fall back to env".
    "anthropic_api_key": "",
    # Hotkey to interrupt an in-progress read-back ("shut up" key).
    "voice_interrupt_hotkey": "<ctrl>+<alt>+s",
    # Localhost port the Stop-hook script POSTs to.
    "voice_ipc_port": 47821,
    "model_size": "large-v3",
    "compute_type": "float16",
    "device": "cuda",
    "language": "auto",
    "microphone_device": None,
    "audio_output_device": None,    # int (portaudio device index) or None for system default
    "vad_silence_ms": 700,
    "vad_threshold": 0.012,
    "vad_min_segment_ms": 350,
    "vad_max_segment_ms": 25000,
    "vad_preroll_ms": 200,
    "type_delay_ms": 4,
    "trailing_space": True,
    "output_method": "type",  # "type" — char-by-char typing; "paste" — clipboard + Ctrl+V
    "paste_settle_ms": 30,    # short pause before Ctrl+V so the clipboard is ready
    # Max time to wait for the user to release Alt/Shift/Win before pressing
    # Ctrl+V. Guards against the toggle-hotkey-residue race where Alt is still
    # held when transcription completes, turning Ctrl+V into Ctrl+Alt+V.
    "paste_modifier_clear_ms": 250,
    # ---- Paste target lock ----------------------------------------
    # See docs/superpowers/specs/2026-04-27-paste-target-lock-design.md
    "paste_lock_enabled": False,
    "paste_lock_hotkey": "<alt>+l",
    "paste_lock_border_enabled": True,
    "paste_lock_border_color": "#ff9900",
    "paste_lock_border_thickness": 3,
    "paste_lock_play_sounds": True,
    "paste_lock_focus_settle_ms": 50,
    "oscilloscope": {
        "enabled": True,
        "x": None,
        "y": None,
        "width": 320,
        "height": 48,
        "color_idle": "#7884a0",
        "color_active": "#40dc8c",
        "opacity": 0.85,
        "background_alpha": 130,
        "shape": "rounded",
        # "waveform" — rolling scrolling waveform (classic oscilloscope look)
        # "spectrum" — fixed-position frequency-band bars that bounce up/down
        "style": "waveform",
        "spectrum_bars": 36,
    },
}


def _config_dir() -> Path:
    base = os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / ".config"
    path = root / "TextWhisper"
    path.mkdir(parents=True, exist_ok=True)
    return path


class SettingsManager(QObject):
    """JSON-backed config persisted to %APPDATA%/TextWhisper/config.json."""

    changed = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.config_path = _config_dir() / "config.json"
        self._data: dict[str, Any] = copy.deepcopy(DEFAULT_CONFIG)
        self.load()

    def load(self) -> None:
        if self.config_path.exists():
            try:
                with open(self.config_path, encoding="utf-8") as f:
                    user = json.load(f)
                self._data = self._deep_merge(DEFAULT_CONFIG, user)
            except Exception:
                self._data = copy.deepcopy(DEFAULT_CONFIG)
        self.save()

    def save(self) -> None:
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
        except OSError:
            pass

    @staticmethod
    def _deep_merge(defaults: dict, override: dict) -> dict:
        out: dict[str, Any] = {}
        for k, v in defaults.items():
            if isinstance(v, dict):
                child = override.get(k, {}) if isinstance(override, dict) else {}
                out[k] = SettingsManager._deep_merge(v, child if isinstance(child, dict) else {})
            else:
                out[k] = override.get(k, v) if isinstance(override, dict) else v
        return out

    def get(self, key: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in key.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return node

    def set(self, key: str, value: Any) -> None:
        parts = key.split(".")
        node = self._data
        for part in parts[:-1]:
            if part not in node or not isinstance(node[part], dict):
                node[part] = {}
            node = node[part]
        node[parts[-1]] = value
        self.save()
        self.changed.emit(key)

    def all(self) -> dict[str, Any]:
        return self._data
