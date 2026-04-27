"""Tests for SoundPlayer.

We patch sounddevice's ``play`` so no audio actually leaves the test runner.
"""

from __future__ import annotations

from unittest.mock import patch

import numpy as np

from src.settings_manager import SettingsManager
from src.sound_player import SoundPlayer, _make_chime, _make_tone


def test_make_tone_has_smooth_envelope():
    samples = _make_tone(659.0, 0.05, 0.2)
    assert samples.dtype == np.float32
    assert samples.size > 0
    # First and last samples are essentially silent (cosine envelope).
    assert abs(samples[0]) < 0.01
    assert abs(samples[-1]) < 0.01
    # Peak is bounded by volume.
    assert float(np.max(np.abs(samples))) <= 0.2 + 1e-6


def test_make_chime_concatenates_notes():
    chime = _make_chime([400.0, 800.0], volume=0.1)
    assert chime.dtype == np.float32
    # Two notes of equal length.
    assert chime.size > 0
    half = chime.size // 2
    assert abs(half * 2 - chime.size) <= 1


def test_play_ready_calls_sd_play_when_enabled(tmp_appdata):
    sm = SettingsManager()
    sm.set("play_ready_sound", True)
    sp = SoundPlayer(sm)
    with patch("src.sound_player.sd.play") as p:
        sp.play_ready()
        p.assert_called_once()


def test_play_ready_skipped_when_disabled(tmp_appdata):
    sm = SettingsManager()
    sm.set("play_ready_sound", False)
    sp = SoundPlayer(sm)
    with patch("src.sound_player.sd.play") as p:
        sp.play_ready()
        p.assert_not_called()


def test_play_stop_default_off(tmp_appdata):
    sm = SettingsManager()
    sp = SoundPlayer(sm)
    with patch("src.sound_player.sd.play") as p:
        sp.play_stop()
        p.assert_not_called()


def test_play_stop_when_enabled(tmp_appdata):
    sm = SettingsManager()
    sm.set("play_stop_sound", True)
    sp = SoundPlayer(sm)
    with patch("src.sound_player.sd.play") as p:
        sp.play_stop()
        p.assert_called_once()


def test_volume_change_rebuilds_tones(tmp_appdata):
    sm = SettingsManager()
    sm.set("sound_volume", 0.1)
    sp = SoundPlayer(sm)
    initial_peak = float(np.max(np.abs(sp._ready)))
    sm.set("sound_volume", 0.5)
    sp._rebuild_if_volume_changed()
    new_peak = float(np.max(np.abs(sp._ready)))
    assert new_peak > initial_peak


def test_volume_clamped_to_range(tmp_appdata):
    sm = SettingsManager()
    sm.set("sound_volume", 5.0)
    sp = SoundPlayer(sm)
    assert float(np.max(np.abs(sp._ready))) <= 1.0


def test_play_failure_swallowed(tmp_appdata):
    """Output device errors must not crash capture lifecycle."""
    sm = SettingsManager()
    sm.set("play_ready_sound", True)
    sp = SoundPlayer(sm)
    with patch("src.sound_player.sd.play", side_effect=RuntimeError("no device")):
        sp.play_ready()  # must not raise


def test_ready_duration_is_positive(tmp_appdata):
    sm = SettingsManager()
    sp = SoundPlayer(sm)
    assert sp.ready_duration_ms > 0


def test_play_lock_calls_sd_play_when_enabled(tmp_appdata):
    sm = SettingsManager()
    sm.set("paste_lock_play_sounds", True)
    sp = SoundPlayer(sm)
    with patch("src.sound_player.sd.play") as p:
        sp.play_lock()
        p.assert_called_once()


def test_play_lock_skipped_when_disabled(tmp_appdata):
    sm = SettingsManager()
    sm.set("paste_lock_play_sounds", False)
    sp = SoundPlayer(sm)
    with patch("src.sound_player.sd.play") as p:
        sp.play_lock()
        sp.play_unlock()
        p.assert_not_called()


def test_play_unlock_calls_sd_play_when_enabled(tmp_appdata):
    sm = SettingsManager()
    sm.set("paste_lock_play_sounds", True)
    sp = SoundPlayer(sm)
    with patch("src.sound_player.sd.play") as p:
        sp.play_unlock()
        p.assert_called_once()
