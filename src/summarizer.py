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


_SYSTEM_PROMPT_HEAD = (
    "You are TextWhisper's voice read-back assistant. The user is in a "
    "hands-free pairing session with Claude Code: they dictate questions, "
    "and you read back what Claude Code just said over TTS. Speak as a "
    "peer — like a teammate sitting next to them — not a narrator reading "
    "the screen."
    "\n\n"
    "Your job: rewrite the input as 1-2 spoken sentences, casual and "
    "confident. Lead with the headline (what was done, the answer, the "
    "question Claude Code asked). When useful, follow it with the *why* "
    "in the same breath. Skip code, file paths, command output, and "
    "'let me / I will now' filler."
    "\n\n"
    "Write for the EAR: short clauses, contractions allowed, no markdown, "
    "no bullet points, no parentheticals. Don't say 'the assistant said' — "
    "speak as the assistant."
    "\n\n"
    "If the input ends with Claude Code asking the user a question "
    "(e.g., \"Want me to also add X?\", \"Should I refactor Y?\"), DROP "
    "that question. Do not echo it."
)

_INVITE_CLAUSE_SUBSTANTIAL = (
    "Because there was a lot left on the cutting-room floor, end with ONE "
    "short, varied invitation to go deeper — phrase it differently each "
    "time. Examples (vary, do not repeat verbatim): \"Want me to walk "
    "through it?\" / \"Should I unpack that?\" / \"Curious about any of "
    "it?\" / \"Want the details?\""
)

_SYSTEM_PROMPT_TAIL = (
    "Output only the spoken text, no preamble, no quotes, no stage directions."
)


class _SettingsLike(Protocol):
    def get(self, key: str, default=None): ...


def _render_prompt(is_substantial: bool) -> str:
    """Build the system prompt with or without the follow-up invitation clause."""
    parts = [_SYSTEM_PROMPT_HEAD]
    if is_substantial:
        parts.append(_INVITE_CLAUSE_SUBSTANTIAL)
    parts.append(_SYSTEM_PROMPT_TAIL)
    return "\n\n".join(parts)


SUMMARY_SYSTEM_PROMPT = _render_prompt(is_substantial=False)


def _classify_response(text: str, settings: _SettingsLike) -> dict:
    """Decide whether a response is *substantial* enough to warrant a follow-up invite.

    Substantial if ANY hold (all thresholds tunable via settings):
      - char_count > voice_followup_min_chars
      - contains a fenced code block AND voice_followup_invite_on_code
      - paragraph_count >= voice_followup_min_paragraphs
    """
    char_count = len(text)
    has_code = "```" in text
    paragraph_count = sum(1 for p in text.split("\n\n") if p.strip())
    min_chars = int(settings.get("voice_followup_min_chars", 800))
    min_paragraphs = int(settings.get("voice_followup_min_paragraphs", 3))
    invite_on_code = bool(settings.get("voice_followup_invite_on_code", True))
    is_substantial = (
        char_count > min_chars
        or (has_code and invite_on_code)
        or paragraph_count >= min_paragraphs
    )
    return {
        "char_count": char_count,
        "has_code": has_code,
        "paragraph_count": paragraph_count,
        "is_substantial": is_substantial,
    }


class MissingAPIKeyError(RuntimeError):
    """Raised when no Anthropic API key is available in settings or env."""


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
        classification = _classify_response(text, self.settings)
        system_prompt = _render_prompt(classification["is_substantial"])
        log.info(
            "Summarizer.summarize: model=%s input_len=%d substantial=%s "
            "(chars=%d paragraphs=%d code=%s) input_preview=%r",
            model, len(text), classification["is_substantial"],
            classification["char_count"], classification["paragraph_count"],
            classification["has_code"], _redact(text),
        )
        message = client.messages.create(
            model=model,
            max_tokens=400,
            system=system_prompt,
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
