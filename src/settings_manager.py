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
    "model_size": "large-v3",
    "compute_type": "float16",
    "device": "cuda",
    "language": "auto",
    "microphone_device": None,
    "vad_silence_ms": 700,
    "vad_threshold": 0.012,
    "vad_min_segment_ms": 350,
    "vad_max_segment_ms": 25000,
    "vad_preroll_ms": 200,
    "type_delay_ms": 4,
    "trailing_space": True,
    "output_method": "type",  # "type" — char-by-char typing; "paste" — clipboard + Ctrl+V
    "paste_settle_ms": 30,    # short pause before Ctrl+V so the clipboard is ready
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
