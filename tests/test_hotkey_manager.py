"""Tests for HotkeyManager.

The pynput Listener is mocked so no real low-level keyboard hook is installed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.hotkey_manager import HotkeyManager


def test_start_creates_listener_with_all_hotkeys():
    hk = HotkeyManager({"toggle": "<alt>+z", "delete": "<delete>"})
    with patch("src.hotkey_manager.Listener") as listener_cls:
        listener_cls.return_value = MagicMock()
        hk.start()
        listener_cls.assert_called_once()
        listener_cls.return_value.start.assert_called_once()
        assert len(hk._hotkeys) == 2


def test_start_is_idempotent():
    hk = HotkeyManager({"toggle": "<alt>+z"})
    with patch("src.hotkey_manager.Listener") as listener_cls:
        listener_cls.return_value = MagicMock()
        hk.start()
        hk.start()
        assert listener_cls.call_count == 1


def test_update_mapping_recreates_listener():
    hk = HotkeyManager({"toggle": "<alt>+z"})
    with patch("src.hotkey_manager.Listener") as listener_cls:
        listener_cls.return_value = MagicMock()
        hk.start()
        hk.update_mapping({"toggle": "<ctrl>+<shift>+v", "delete": "<delete>"})
        assert listener_cls.call_count == 2
        assert "toggle" in hk.mapping


def test_invalid_hotkey_emits_error_signal(qapp):
    hk = HotkeyManager({"toggle": "<frobnicate>+z"})
    errors: list[str] = []
    hk.error.connect(errors.append)
    hk.start()
    qapp.processEvents()
    assert errors
    assert "frobnicate" in errors[0]
    assert hk._listener is None


def test_stop_clears_listener():
    hk = HotkeyManager({"toggle": "<alt>+z"})
    with patch("src.hotkey_manager.Listener") as listener_cls:
        listener = MagicMock()
        listener_cls.return_value = listener
        hk.start()
        hk.stop()
        listener.stop.assert_called_once()
        assert hk._listener is None
        assert hk._hotkeys == []


def test_empty_mapping_is_a_noop():
    hk = HotkeyManager({})
    with patch("src.hotkey_manager.Listener") as listener_cls:
        hk.start()
        listener_cls.assert_not_called()


def test_listener_routes_press_and_release_to_hotkeys(qapp):
    hk = HotkeyManager({"toggle": "<alt>+z"})
    captured: dict = {}

    def fake_listener_init(on_press=None, on_release=None):
        captured["on_press"] = on_press
        captured["on_release"] = on_release
        listener = MagicMock()
        listener.canonical = lambda k: k
        return listener

    with patch("src.hotkey_manager.Listener", side_effect=fake_listener_init):
        hk.start()
        on_press_cb = MagicMock()
        on_release_cb = MagicMock()
        fake_hotkey = MagicMock()
        fake_hotkey.press = on_press_cb
        fake_hotkey.release = on_release_cb
        hk._hotkeys = [fake_hotkey]
        captured["on_press"]("a")
        captured["on_release"]("a")
        on_press_cb.assert_called_once_with("a")
        on_release_cb.assert_called_once_with("a")


def test_listener_callback_exception_does_not_kill_thread(qapp, caplog):
    """A buggy hotkey callback must not propagate out of the listener thread."""
    import logging

    hk = HotkeyManager({"toggle": "<alt>+z"})
    captured: dict = {}

    def fake_listener_init(on_press=None, on_release=None):
        captured["on_press"] = on_press
        captured["on_release"] = on_release
        listener = MagicMock()
        listener.canonical = lambda k: k
        return listener

    with patch("src.hotkey_manager.Listener", side_effect=fake_listener_init):
        hk.start()
        boom = MagicMock()
        boom.press.side_effect = RuntimeError("kaboom")
        boom.release.side_effect = RuntimeError("kaboom")
        hk._hotkeys = [boom]
        with caplog.at_level(logging.ERROR):
            captured["on_press"]("a")
            captured["on_release"]("a")
    assert any("kaboom" in r.message or "kaboom" in str(r.exc_info) for r in caplog.records)


def test_is_alive_reports_listener_state():
    hk = HotkeyManager({"toggle": "<alt>+z"})
    assert hk.is_alive is False
    with patch("src.hotkey_manager.Listener") as listener_cls:
        listener = MagicMock()
        listener.is_alive.return_value = True
        listener_cls.return_value = listener
        hk.start()
        assert hk.is_alive is True
        listener.is_alive.return_value = False
        assert hk.is_alive is False


def test_restart_if_dead_revives_dead_listener():
    hk = HotkeyManager({"toggle": "<alt>+z"})
    with patch("src.hotkey_manager.Listener") as listener_cls:
        dead = MagicMock()
        dead.is_alive.return_value = False
        alive = MagicMock()
        alive.is_alive.return_value = True
        listener_cls.side_effect = [dead, alive]
        hk.start()
        assert hk._listener is dead
        assert hk.restart_if_dead() is True
        assert hk._listener is alive


def test_restart_if_dead_noop_when_alive():
    hk = HotkeyManager({"toggle": "<alt>+z"})
    with patch("src.hotkey_manager.Listener") as listener_cls:
        listener = MagicMock()
        listener.is_alive.return_value = True
        listener_cls.return_value = listener
        hk.start()
        assert hk.restart_if_dead() is False


def test_reset_state_clears_hotkey_internals(qapp):
    hk = HotkeyManager({"toggle": "<alt>+z", "delete": "<delete>"})
    with patch("src.hotkey_manager.Listener") as listener_cls:
        listener_cls.return_value = MagicMock()
        hk.start()
        for hot in hk._hotkeys:
            hot._state.add("STUCK_KEY")
        for hot in hk._hotkeys:
            assert "STUCK_KEY" in hot._state
        hk.reset_state()
        for hot in hk._hotkeys:
            assert hot._state == set()
