"""Tests for the Claude-Code Stop hook script (tools/claude-code-stop-hook.py).

Imports the script as a module via importlib (no .py extension would
make this awkward, but the file name has hyphens — ``importlib.util``
gives us a clean handle anyway).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK_PATH = ROOT / "tools" / "claude-code-stop-hook.py"


@pytest.fixture(scope="module")
def hook_module():
    spec = importlib.util.spec_from_file_location("textwhisper_stop_hook", HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    yield mod


# --- _extract_text -----------------------------------------------------

def test_extract_text_string(hook_module):
    assert hook_module._extract_text("hello") == "hello"


def test_extract_text_list_of_text_blocks(hook_module):
    content = [
        {"type": "text", "text": "Hello "},
        {"type": "text", "text": "world."},
    ]
    assert hook_module._extract_text(content) == "Hello world."


def test_extract_text_skips_non_text_blocks(hook_module):
    content = [
        {"type": "tool_use", "id": "..."},
        {"type": "text", "text": "Real text."},
    ]
    assert hook_module._extract_text(content) == "Real text."


def test_extract_text_handles_string_blocks(hook_module):
    assert hook_module._extract_text(["a", "b"]) == "ab"


def test_extract_text_none_returns_empty(hook_module):
    assert hook_module._extract_text(None) == ""


# --- _last_assistant_text ---------------------------------------------

def _write_jsonl(path: Path, lines: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")


def test_last_assistant_text_message_envelope(hook_module, tmp_path):
    """Format: {"type":"...","message":{"role":"assistant","content":[...]}}"""
    p = tmp_path / "session.jsonl"
    _write_jsonl(p, [
        {"type": "user", "message": {"role": "user", "content": "hi"}},
        {"type": "assistant", "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "First reply."}],
        }},
        {"type": "user", "message": {"role": "user", "content": "more"}},
        {"type": "assistant", "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "Second reply."}],
        }},
    ])
    assert hook_module._last_assistant_text(p) == "Second reply."


def test_last_assistant_text_top_level_role(hook_module, tmp_path):
    """Format variant: {"role":"assistant","content":[...]}"""
    p = tmp_path / "session.jsonl"
    _write_jsonl(p, [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [{"type": "text", "text": "Only reply."}]},
    ])
    assert hook_module._last_assistant_text(p) == "Only reply."


def test_last_assistant_text_skips_malformed_lines(hook_module, tmp_path):
    p = tmp_path / "session.jsonl"
    p.write_text(
        '{"type":"user","message":{"role":"user","content":"hi"}}\n'
        "this is not json\n"
        '{"type":"assistant","message":{"role":"assistant","content":'
        '[{"type":"text","text":"Reply."}]}}\n',
        encoding="utf-8",
    )
    assert hook_module._last_assistant_text(p) == "Reply."


def test_last_assistant_text_missing_file(hook_module, tmp_path):
    assert hook_module._last_assistant_text(tmp_path / "nope.jsonl") == ""


def test_last_assistant_text_no_assistant_messages(hook_module, tmp_path):
    p = tmp_path / "session.jsonl"
    _write_jsonl(p, [
        {"type": "user", "message": {"role": "user", "content": "hi"}},
    ])
    assert hook_module._last_assistant_text(p) == ""


# --- _post_speak (logged path) ----------------------------------------

def test_post_speak_logs_status_on_success(hook_module, tmp_appdata, monkeypatch):
    monkeypatch.setattr(hook_module, "_hook_log", lambda m: m)  # no-op
    fake_resp = type("R", (), {"status": 202, "__enter__": lambda self: self,
                                "__exit__": lambda *a: None})()
    with patch("urllib.request.urlopen", return_value=fake_resp) as up:
        hook_module._post_speak(47821, "hello")
    up.assert_called_once()


def test_post_speak_swallows_connection_errors(hook_module, tmp_appdata):
    """TextWhisper not running -> hook must not raise."""
    import urllib.error
    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        hook_module._post_speak(47821, "hi")  # must not raise


# --- main() integration -----------------------------------------------

def test_main_no_input_returns_zero(hook_module, monkeypatch, tmp_appdata):
    monkeypatch.setattr(sys, "stdin", _FakeStdin(""))
    assert hook_module.main() == 0


def test_main_voice_disabled_quiet_noop(hook_module, monkeypatch, tmp_appdata):
    """Hook leaves a settings file with voice_enabled=False -> 0, no POST."""
    cfg_dir = hook_module._config_dir()
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.json").write_text(
        json.dumps({"voice_enabled": False}), encoding="utf-8"
    )
    event = {"transcript_path": "ignored", "session_id": "x"}
    monkeypatch.setattr(sys, "stdin", _FakeStdin(json.dumps(event)))
    with patch.object(hook_module, "_post_speak") as ps:
        rc = hook_module.main()
    assert rc == 0
    ps.assert_not_called()


def test_main_posts_assistant_text(hook_module, monkeypatch, tmp_appdata):
    cfg_dir = hook_module._config_dir()
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.json").write_text(
        json.dumps({"voice_enabled": True, "voice_ipc_port": 47821}),
        encoding="utf-8",
    )
    transcript = tmp_appdata / "session.jsonl"
    transcript.write_text(json.dumps({
        "type": "assistant",
        "message": {"role": "assistant", "content": [
            {"type": "text", "text": "I added the feature."}
        ]},
    }) + "\n", encoding="utf-8")
    event = {"transcript_path": str(transcript)}
    monkeypatch.setattr(sys, "stdin", _FakeStdin(json.dumps(event)))
    with patch.object(hook_module, "_post_speak") as ps:
        rc = hook_module.main()
    assert rc == 0
    ps.assert_called_once_with(47821, "I added the feature.")


def test_main_no_transcript_path_quiet_noop(hook_module, monkeypatch, tmp_appdata):
    cfg_dir = hook_module._config_dir()
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.json").write_text(
        json.dumps({"voice_enabled": True}), encoding="utf-8"
    )
    monkeypatch.setattr(sys, "stdin", _FakeStdin(json.dumps({"session_id": "x"})))
    with patch.object(hook_module, "_post_speak") as ps:
        rc = hook_module.main()
    assert rc == 0
    ps.assert_not_called()


def test_main_unexpected_exception_returns_zero(hook_module, monkeypatch, tmp_appdata):
    """Even an internal blow-up must not propagate to Claude Code."""
    monkeypatch.setattr(sys, "stdin", _FakeStdin('{"transcript_path":"x"}'))
    with patch.object(hook_module, "_load_settings", side_effect=RuntimeError("boom")):
        rc = hook_module.main()
    assert rc == 0


# Helper: a stdin-like object for monkeypatching sys.stdin.
class _FakeStdin:
    def __init__(self, payload: str) -> None:
        self._payload = payload

    def read(self) -> str:
        return self._payload
