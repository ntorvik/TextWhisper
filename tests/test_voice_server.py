"""Tests for the localhost voice IPC server.

Boots the server on a random free port for each test, hits it with the
stdlib's urllib, and asserts on TTS / Summarizer interactions.
"""

from __future__ import annotations

import json
import socket
import time
import urllib.request
from unittest.mock import MagicMock

import pytest

from src.settings_manager import SettingsManager
from src.voice_server import VoiceIPCServer


def _free_port() -> int:
    """Reserve and immediately close a port — kernel won't reuse it for
    a few hundred ms which is plenty for the test to bind."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def server(tmp_appdata):
    s = SettingsManager()
    s.set("voice_enabled", True)
    s.set("voice_summarize", True)
    s.set("voice_ipc_port", _free_port())
    tts = MagicMock()
    summarizer = MagicMock()
    summarizer.summarize.side_effect = lambda x: f"summary({x})"
    srv = VoiceIPCServer(s, tts, summarizer)
    srv.start()
    # Tiny pause so serve_forever is actually accepting.
    time.sleep(0.05)
    yield srv, tts, summarizer
    srv.stop()


def _post(port: int, path: str, payload: dict | None) -> tuple[int, dict]:
    body = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8") or "{}")


def _get(port: int, path: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}{path}", timeout=2
        ) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8") or "{}")


# --- /health ------------------------------------------------------------

def test_health_returns_version_and_voice_state(server):
    srv, _, _ = server
    code, body = _get(srv.port, "/health")
    assert code == 200
    assert body["ok"] is True
    assert "version" in body
    assert body["voice_enabled"] is True


# --- /speak -------------------------------------------------------------

def test_speak_summarises_then_calls_tts(server):
    srv, tts, summ = server
    code, body = _post(srv.port, "/speak", {"text": "Long technical response."})
    assert code == 202
    summ.summarize.assert_called_once_with("Long technical response.")
    tts.speak.assert_called_once_with("summary(Long technical response.)")
    assert body["ok"] is True


def test_speak_skips_summary_when_flag_false(server):
    srv, tts, summ = server
    code, _ = _post(
        srv.port, "/speak", {"text": "Verbatim text.", "summarize": False}
    )
    assert code == 202
    summ.summarize.assert_not_called()
    tts.speak.assert_called_once_with("Verbatim text.")


def test_speak_returns_204_when_voice_disabled(server):
    srv, tts, _ = server
    srv.settings.set("voice_enabled", False)
    code, body = _post(srv.port, "/speak", {"text": "Hello."})
    assert code == 204
    tts.speak.assert_not_called()


def test_speak_400_on_empty_text(server):
    srv, tts, _ = server
    code, body = _post(srv.port, "/speak", {"text": ""})
    assert code == 400
    assert body["error"] == "empty_text"
    tts.speak.assert_not_called()


def test_speak_falls_back_to_raw_on_summariser_failure(server):
    srv, tts, summ = server
    summ.summarize.side_effect = RuntimeError("no key")
    code, _ = _post(srv.port, "/speak", {"text": "Long response."})
    assert code == 202
    tts.speak.assert_called_once_with("Long response.")


def test_speak_400_on_malformed_json(server):
    srv, _, _ = server
    req = urllib.request.Request(
        f"http://127.0.0.1:{srv.port}/speak",
        data=b"not json",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=2)
        raise AssertionError("expected HTTPError")
    except urllib.error.HTTPError as e:
        assert e.code == 400


# --- /interrupt --------------------------------------------------------

def test_interrupt_calls_tts_interrupt(server):
    srv, tts, _ = server
    code, body = _post(srv.port, "/interrupt", {})
    assert code == 200
    assert body["ok"] is True
    tts.interrupt.assert_called_once()


# --- /<unknown> --------------------------------------------------------

def test_unknown_path_returns_404(server):
    srv, _, _ = server
    code, _ = _get(srv.port, "/nope")
    assert code == 404


# --- Lifecycle ---------------------------------------------------------

def test_start_is_idempotent(tmp_appdata):
    s = SettingsManager()
    s.set("voice_ipc_port", _free_port())
    srv = VoiceIPCServer(s, MagicMock(), MagicMock())
    srv.start()
    port = srv.port
    srv.start()  # must not rebind / change ports
    assert srv.port == port
    srv.stop()


def test_stop_is_idempotent(tmp_appdata):
    s = SettingsManager()
    s.set("voice_ipc_port", _free_port())
    srv = VoiceIPCServer(s, MagicMock(), MagicMock())
    srv.start()
    srv.stop()
    srv.stop()  # second call must not raise


# urllib.error import — tests above reference it, ensure available.
import urllib.error  # noqa: E402
