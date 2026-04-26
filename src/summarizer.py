"""Conversational summariser for Claude-Code-Stop-hook payloads.

Each Claude turn lands here as raw assistant text (often hundreds of
lines, with code blocks, file paths, command output, and other
listen-hostile content). We hand it to Claude Haiku with a system
prompt that demands a 2-3 sentence spoken-style summary, suitable for
piping straight into Piper.

The API key is loaded — in this priority order — from
:class:`SettingsManager` (key ``anthropic_api_key``), then from the
``ANTHROPIC_API_KEY`` environment variable. If neither is set,
:meth:`Summarizer.summarize` raises :class:`MissingAPIKeyError` so the
Stop-hook script can surface a clear message instead of silently
swallowing the failure.

Logging is deliberately careful: the API key is NEVER logged, and we
truncate input/output text in logs to avoid accidentally echoing
sensitive content into ``%APPDATA%\\TextWhisper\\logs\\``.
"""

from __future__ import annotations

import logging
import os
from typing import Protocol

log = logging.getLogger(__name__)


SUMMARY_SYSTEM_PROMPT = (
    "You are TextWhisper's voice read-back assistant. "
    "The user dictates to Claude Code by voice and listens to the "
    "responses through TTS instead of reading the screen. Each input "
    "you receive is the FULL raw response Claude Code just emitted "
    "(possibly including code blocks, file paths, shell output, "
    "and lists). "
    "\n\nYour job: rewrite the response as a 2-3 sentence "
    "conversational summary the user can hear, not a transcript. "
    "Lead with the answer or outcome. Skip code, file paths, command "
    "lines, and 'I will now / let me' filler. If the response was "
    "asking the user a question, lead with the question. If it "
    "completed work, say what was done and the result. "
    "\n\nWrite for the EAR: short clauses, no bullet points, no "
    "markdown, no parentheticals, no jargon the user wouldn't have "
    "used in their own request. Don't say 'the assistant said' — "
    "speak as the assistant. Output only the summary text, no "
    "preamble, no quotes."
)


class MissingAPIKeyError(RuntimeError):
    """Raised when no Anthropic API key is available in settings or env."""


class _SettingsLike(Protocol):
    def get(self, key: str, default=None): ...


def _redact(text: str, limit: int = 80) -> str:
    """Trim ``text`` for safe logging — no secrets, no walls of code."""
    text = text.replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


class Summarizer:
    """Wraps the Anthropic SDK with TextWhisper's read-back-specific prompt.

    Constructed lazily — the SDK is only imported when ``summarize()`` is
    first called. Keeps test environments and cold app starts cheap.
    """

    def __init__(self, settings: _SettingsLike) -> None:
        self.settings = settings
        self._client = None  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def summarize(self, raw_response: str) -> str:
        """Return a 2-3 sentence spoken-style summary of ``raw_response``.

        Raises :class:`MissingAPIKeyError` if no key is configured.
        Returns the raw_response unchanged if it's already short enough
        (saves an API call when Claude only said "OK." or similar).
        """
        text = (raw_response or "").strip()
        if not text:
            return ""

        # Tiny responses are already speakable — don't waste a token.
        if len(text) <= 200 and "\n" not in text and "```" not in text:
            return text

        client = self._ensure_client()
        model = str(self.settings.get("voice_summarize_model", "claude-haiku-4-5"))
        log.info(
            "Summarizer.summarize: model=%s input_len=%d input_preview=%r",
            model, len(text), _redact(text),
        )
        message = client.messages.create(
            model=model,
            max_tokens=400,
            system=SUMMARY_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": text}],
        )
        # Anthropic returns a list of content blocks; we only ask for text.
        out_parts: list[str] = []
        for block in message.content:
            block_text = getattr(block, "text", None)
            if block_text:
                out_parts.append(block_text)
        summary = "".join(out_parts).strip()
        log.info(
            "Summarizer.summarize: output_len=%d output_preview=%r",
            len(summary), _redact(summary),
        )
        return summary

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_api_key(self) -> str:
        """Settings first (per-user, lives in %APPDATA%), env as fallback."""
        from_settings = str(self.settings.get("anthropic_api_key", "") or "").strip()
        if from_settings:
            return from_settings
        from_env = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        return from_env

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        api_key = self._resolve_api_key()
        if not api_key:
            raise MissingAPIKeyError(
                "No Anthropic API key configured. Add one in "
                "Settings → Voice → Anthropic API key, or set the "
                "ANTHROPIC_API_KEY environment variable."
            )
        # Imported here so app import doesn't pay for the SDK unless
        # summarisation is actually used.
        from anthropic import Anthropic
        # NEVER log the api_key argument here.
        self._client = Anthropic(api_key=api_key)
        return self._client
