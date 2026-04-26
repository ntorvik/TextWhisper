#!/usr/bin/env python3
"""TextWhisper Stop-hook bridge for Claude Code.

Register this script in ``~/.claude/settings.json``:

    {
      "hooks": {
        "Stop": [
          {
            "matcher": "*",
            "hooks": [
              {"type": "command",
               "command": "py \"%USERPROFILE%/.claude/textwhisper-stop.py\""}
            ]
          }
        ]
      }
    }

Linux/macOS: replace the command with
``python3 ~/.claude/textwhisper-stop.py``.

Behaviour:

1. Reads the Stop-event JSON from stdin (Claude Code's contract).
2. Opens the transcript JSONL file referenced in that JSON, finds the
   LAST assistant message, and concatenates its text content blocks.
3. Reads ``%APPDATA%/TextWhisper/config.json`` to discover the IPC port
   and the ``voice_enabled`` flag (we exit cleanly if voice is off, so
   the user can leave the hook installed permanently).
4. POSTs ``{"text": "<assistant message>"}`` to
   ``http://127.0.0.1:<port>/speak`` — TextWhisper handles
   summarisation + Piper playback.
5. NEVER blocks Claude Code: any error here is swallowed (logged to a
   small file in %APPDATA%) so a Stop-hook crash can't break the
   user's session.

This script intentionally uses ONLY the Python standard library — it
runs in whichever Python the user has on PATH, not TextWhisper's
bundled venv. No third-party dependencies.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
import urllib.error
import urllib.request
from pathlib import Path


def _config_dir() -> Path:
    base = os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / ".config"
    return root / "TextWhisper"


def _hook_log(message: str) -> None:
    """Append-only debug log so the user can diagnose silent failures
    without re-running Claude Code under a debugger."""
    try:
        log_dir = _config_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / "stop-hook.log", "a", encoding="utf-8") as f:
            f.write(message.rstrip() + "\n")
    except Exception:
        pass  # The whole point is to never break the host. Drop on floor.


def _load_settings() -> dict:
    cfg = _config_dir() / "config.json"
    if not cfg.exists():
        return {}
    try:
        with open(cfg, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception as e:
        _hook_log(f"Could not read config.json: {e}")
        return {}


def _last_assistant_text(transcript_path: Path) -> str:
    """Walk a Claude Code session JSONL, return the LAST assistant turn's
    concatenated text content blocks."""
    if not transcript_path.exists():
        return ""
    last_text = ""
    try:
        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # Format tolerant: assistant turns can appear as
                # {"type": "assistant", "message": {"role":"assistant",
                #  "content":[{"type":"text","text":"..."}]}}
                # OR with role at top level. We try both.
                msg = obj.get("message") if isinstance(obj, dict) else None
                role = (
                    obj.get("type")
                    or obj.get("role")
                    or (msg.get("role") if isinstance(msg, dict) else None)
                )
                if role != "assistant":
                    continue
                content = (msg or obj).get("content") if isinstance(msg or obj, dict) else None
                text = _extract_text(content)
                if text:
                    last_text = text  # keep walking; we want the LAST one.
    except Exception as e:
        _hook_log(f"Transcript read failed: {e}")
    return last_text.strip()


def _extract_text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out: list[str] = []
        for block in content:
            if isinstance(block, dict):
                # {"type":"text","text":"..."}
                if block.get("type") == "text" and isinstance(block.get("text"), str):
                    out.append(block["text"])
            elif isinstance(block, str):
                out.append(block)
        return "".join(out)
    return ""


def _post_speak(port: int, text: str) -> None:
    url = f"http://127.0.0.1:{int(port)}/speak"
    body = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=2) as resp:
            _hook_log(f"POST {url} -> {resp.status}")
    except urllib.error.URLError as e:
        # TextWhisper not running, port wrong, or firewall blocking 127.x —
        # not our problem. Just log and exit cleanly.
        _hook_log(f"Could not reach TextWhisper at {url}: {e}")


def main() -> int:
    try:
        raw = sys.stdin.read()
        if not raw:
            return 0
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            _hook_log(f"stdin was not JSON: {raw[:200]!r}")
            return 0

        settings = _load_settings()
        if not bool(settings.get("voice_enabled", False)):
            return 0  # User has read-back disabled — quiet no-op.

        transcript = event.get("transcript_path")
        if not transcript:
            _hook_log("No transcript_path in Stop event.")
            return 0

        text = _last_assistant_text(Path(transcript))
        if not text:
            _hook_log("No assistant text extracted from transcript.")
            return 0

        port = int(settings.get("voice_ipc_port", 47821) or 47821)
        _post_speak(port, text)
    except Exception:
        _hook_log("UNEXPECTED:\n" + traceback.format_exc())
    # Always exit 0 — Stop hooks should never block the parent.
    return 0


if __name__ == "__main__":
    sys.exit(main())
