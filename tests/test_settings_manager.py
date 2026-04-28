"""Tests for SettingsManager: defaults, persistence, dot-paths, deep merge."""

from __future__ import annotations

import json
from pathlib import Path

from src.settings_manager import DEFAULT_CONFIG, SettingsManager


def test_creates_config_with_defaults_on_first_run(tmp_appdata):
    sm = SettingsManager()
    cfg_path = Path(tmp_appdata) / "TextWhisper" / "config.json"
    assert cfg_path.exists()

    written = json.loads(cfg_path.read_text())
    assert written["hotkey"] == DEFAULT_CONFIG["hotkey"]
    assert written["model_size"] == DEFAULT_CONFIG["model_size"]
    assert written["oscilloscope"]["enabled"] is True
    assert sm.get("hotkey") == DEFAULT_CONFIG["hotkey"]


def test_dot_path_get_and_default(tmp_appdata):
    sm = SettingsManager()
    assert sm.get("oscilloscope.width") == DEFAULT_CONFIG["oscilloscope"]["width"]
    assert sm.get("missing.key", "fallback") == "fallback"
    assert sm.get("oscilloscope.x") is None


def test_dot_path_set_creates_nested(tmp_appdata):
    sm = SettingsManager()
    sm.set("oscilloscope.x", 123)
    sm.set("oscilloscope.y", 456)
    assert sm.get("oscilloscope.x") == 123
    assert sm.get("oscilloscope.y") == 456

    written = json.loads(Path(sm.config_path).read_text())
    assert written["oscilloscope"]["x"] == 123
    assert written["oscilloscope"]["y"] == 456


def test_set_creates_new_branch(tmp_appdata):
    sm = SettingsManager()
    sm.set("ui.theme.color", "dark")
    assert sm.get("ui.theme.color") == "dark"


def test_user_overrides_preserved_across_load(tmp_appdata):
    sm1 = SettingsManager()
    sm1.set("hotkey", "<ctrl>+<shift>+v")
    sm1.set("vad_silence_ms", 1234)

    sm2 = SettingsManager()
    assert sm2.get("hotkey") == "<ctrl>+<shift>+v"
    assert sm2.get("vad_silence_ms") == 1234
    assert sm2.get("model_size") == DEFAULT_CONFIG["model_size"]


def test_corrupt_config_falls_back_to_defaults(tmp_appdata):
    cfg_dir = Path(tmp_appdata) / "TextWhisper"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.json").write_text("{not valid json")

    sm = SettingsManager()
    assert sm.get("hotkey") == DEFAULT_CONFIG["hotkey"]


def test_partial_user_config_merged_with_defaults(tmp_appdata):
    cfg_dir = Path(tmp_appdata) / "TextWhisper"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.json").write_text(json.dumps({"oscilloscope": {"x": 50}}))

    sm = SettingsManager()
    assert sm.get("oscilloscope.x") == 50
    assert sm.get("oscilloscope.y") is None
    assert sm.get("oscilloscope.width") == DEFAULT_CONFIG["oscilloscope"]["width"]
    assert sm.get("model_size") == DEFAULT_CONFIG["model_size"]


def test_changed_signal_emits_with_key(tmp_appdata, qapp):
    sm = SettingsManager()
    received: list[str] = []
    sm.changed.connect(received.append)
    sm.set("hotkey", "<alt>+x")
    qapp.processEvents()
    assert "hotkey" in received


def test_paste_target_lock_defaults_present(tmp_appdata):
    sm = SettingsManager()
    assert sm.get("paste_lock_enabled") is False
    assert sm.get("paste_lock_hotkey") == "<alt>+l"
    assert sm.get("paste_lock_border_enabled") is True
    assert sm.get("paste_lock_border_color") == "#ff9900"
    assert sm.get("paste_lock_border_thickness") == 3
    assert sm.get("paste_lock_play_sounds") is True
    assert sm.get("paste_lock_focus_settle_ms") == 50


def test_audio_output_device_default_is_none(tmp_appdata):
    from src.settings_manager import SettingsManager
    sm = SettingsManager()
    assert sm.get("audio_output_device") is None
