"""Tests for the Anthropic summariser used by the TTS read-back path."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.settings_manager import SettingsManager
from src.summarizer import (
    SUMMARY_SYSTEM_PROMPT,
    MissingAPIKeyError,
    Summarizer,
    _classify_response,
    _redact,
    _render_prompt,
)


@pytest.fixture
def settings(tmp_appdata):
    s = SettingsManager()
    s.set("anthropic_api_key", "")  # simulate empty
    return s


def _fake_message(text: str):
    block = MagicMock()
    block.text = text
    msg = MagicMock()
    msg.content = [block]
    return msg


# --- API key resolution -------------------------------------------------

def test_settings_key_takes_precedence_over_env(settings, monkeypatch):
    settings.set("anthropic_api_key", "from-settings")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    s = Summarizer(settings)
    assert s._resolve_api_key() == "from-settings"


def test_env_used_when_settings_empty(settings, monkeypatch):
    settings.set("anthropic_api_key", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    s = Summarizer(settings)
    assert s._resolve_api_key() == "from-env"


def test_no_key_raises_missing_error(settings, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    s = Summarizer(settings)
    with pytest.raises(MissingAPIKeyError):
        s.summarize("a long enough response\nwith newlines and ```code```\nlines")


# --- Short-response shortcut -------------------------------------------

def test_short_plain_response_skips_api_call(settings):
    """No newlines, no code fences, <= 200 chars -> return as-is."""
    s = Summarizer(settings)
    out = s.summarize("OK, done.")
    assert out == "OK, done."
    # _client must still be unset because we never hit the SDK.
    assert s._client is None


def test_short_with_newline_still_calls_api(settings, monkeypatch):
    """Newline in the response means it's structured — summarise it."""
    settings.set("anthropic_api_key", "test-key")
    s = Summarizer(settings)
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _fake_message("Two lines summary.")
    with patch("anthropic.Anthropic", return_value=fake_client):
        out = s.summarize("Line one\nLine two")
    assert out == "Two lines summary."
    fake_client.messages.create.assert_called_once()


def test_empty_input_returns_empty(settings):
    s = Summarizer(settings)
    assert s.summarize("") == ""
    assert s.summarize("   \n   ") == ""


# --- Full SDK path -----------------------------------------------------

def test_summarize_uses_haiku_with_system_prompt(settings):
    settings.set("anthropic_api_key", "test-key")
    settings.set("voice_summarize_model", "claude-haiku-4-5")
    s = Summarizer(settings)
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _fake_message("The summary.")
    long_text = "Long technical response.\n" * 30
    with patch("anthropic.Anthropic", return_value=fake_client):
        out = s.summarize(long_text)
    assert out == "The summary."
    args, kwargs = fake_client.messages.create.call_args
    assert kwargs["model"] == "claude-haiku-4-5"
    assert kwargs["system"] == SUMMARY_SYSTEM_PROMPT
    assert kwargs["messages"] == [{"role": "user", "content": long_text.strip()}]
    assert isinstance(kwargs["max_tokens"], int)


def test_client_is_cached_across_calls(settings):
    settings.set("anthropic_api_key", "test-key")
    s = Summarizer(settings)
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _fake_message("ok")
    long = "A response.\n" * 30
    with patch("anthropic.Anthropic", return_value=fake_client) as ctor:
        s.summarize(long)
        s.summarize(long)
    # Anthropic() ctor called only once; the cached client is reused.
    assert ctor.call_count == 1


def test_summary_text_is_concatenated_across_blocks(settings):
    """Anthropic content can be multi-block — assemble them all."""
    settings.set("anthropic_api_key", "test-key")
    s = Summarizer(settings)
    block_a = MagicMock()
    block_a.text = "First. "
    block_b = MagicMock()
    block_b.text = "Second."
    msg = MagicMock()
    msg.content = [block_a, block_b]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = msg
    with patch("anthropic.Anthropic", return_value=fake_client):
        out = s.summarize("Long ... response\n" * 30)
    assert out == "First. Second."


def test_block_without_text_attribute_skipped(settings):
    """Tool-use blocks have no .text — they shouldn't break summarisation."""
    settings.set("anthropic_api_key", "test-key")
    s = Summarizer(settings)
    text_block = MagicMock()
    text_block.text = "Real summary."
    tool_block = MagicMock(spec=[])  # no attributes
    msg = MagicMock()
    msg.content = [tool_block, text_block]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = msg
    with patch("anthropic.Anthropic", return_value=fake_client):
        out = s.summarize("Long response\n" * 30)
    assert out == "Real summary."


# --- Logging redaction --------------------------------------------------

def test_redact_short_text_unchanged():
    assert _redact("hi there") == "hi there"


def test_redact_long_text_truncated():
    s = "x" * 200
    out = _redact(s, limit=20)
    assert len(out) == 20
    assert out.endswith("...")


def test_redact_collapses_newlines():
    assert _redact("line one\nline two") == "line one line two"


def test_summarize_does_not_log_api_key(settings, caplog, monkeypatch):
    """Sanity check: nothing in the log records contains the key string."""
    settings.set("anthropic_api_key", "sk-ant-shouldnt-appear-in-logs")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-also-shouldnt-appear")
    s = Summarizer(settings)
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _fake_message("ok")
    with patch("anthropic.Anthropic", return_value=fake_client), caplog.at_level("INFO"):
        s.summarize("Long response that triggers the API call.\n" * 30)
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "sk-ant-shouldnt-appear-in-logs" not in joined
    assert "sk-ant-also-shouldnt-appear" not in joined


# --- Follow-up invitation gate -----------------------------------------

def test_classify_short_modest_response(settings):
    """Tiny single-paragraph response with no code is modest."""
    c = _classify_response("Fixed the typo on line 42.", settings)
    assert c["is_substantial"] is False
    assert c["has_code"] is False
    assert c["paragraph_count"] == 1


def test_classify_long_response_is_substantial(settings):
    """Char count above the threshold is enough on its own."""
    c = _classify_response("x" * 1000, settings)
    assert c["is_substantial"] is True
    assert c["char_count"] == 1000


def test_classify_code_block_is_substantial(settings):
    """A fenced code block flips substantial regardless of length."""
    text = "Done. ```py\ndef f(): pass\n```"
    c = _classify_response(text, settings)
    assert c["has_code"] is True
    assert c["is_substantial"] is True


def test_classify_multiple_paragraphs_is_substantial(settings):
    """Three+ paragraphs (blank-line separated) flips substantial."""
    text = "First.\n\nSecond.\n\nThird."
    c = _classify_response(text, settings)
    assert c["paragraph_count"] == 3
    assert c["is_substantial"] is True


def test_classify_invite_on_code_can_be_disabled(settings):
    """Turning off invite_on_code drops the code-block signal."""
    settings.set("voice_followup_invite_on_code", False)
    c = _classify_response("Done. ```py\ndef f(): pass\n```", settings)
    assert c["has_code"] is True
    assert c["is_substantial"] is False  # only signal was code, now disabled


def test_classify_thresholds_are_settings_driven(settings):
    """Lowering min_chars makes shorter input substantial."""
    settings.set("voice_followup_min_chars", 10)
    c = _classify_response("a" * 50, settings)
    assert c["is_substantial"] is True


def test_render_prompt_modest_excludes_invite_clause():
    prompt = _render_prompt(is_substantial=False)
    assert "cutting-room floor" not in prompt
    assert "Output only the spoken text" in prompt


def test_render_prompt_substantial_includes_invite_clause():
    prompt = _render_prompt(is_substantial=True)
    assert "cutting-room floor" in prompt
    assert "vary, do not repeat verbatim" in prompt
    assert "Output only the spoken text" in prompt


def test_summary_system_prompt_constant_matches_modest_render():
    """Public constant must match the modest-case rendered prompt."""
    assert SUMMARY_SYSTEM_PROMPT == _render_prompt(is_substantial=False)


def test_substantial_input_sends_invite_clause_to_haiku(settings):
    """Long/code-heavy input → system arg contains the invite clause."""
    settings.set("anthropic_api_key", "test-key")
    s = Summarizer(settings)
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _fake_message("Done. Want to dig in?")
    long_text_with_code = "Long response.\n" * 30 + "\n```py\ncode here\n```"
    with patch("anthropic.Anthropic", return_value=fake_client):
        s.summarize(long_text_with_code)
    _, kwargs = fake_client.messages.create.call_args
    assert "cutting-room floor" in kwargs["system"]


def test_modest_input_omits_invite_clause(settings):
    """Single-paragraph short response → invite clause absent."""
    settings.set("anthropic_api_key", "test-key")
    s = Summarizer(settings)
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _fake_message("Got it — done.")
    # Force an API call by including a newline so the fast-path bypass skips,
    # but keep it well below the 800-char threshold and single-paragraph.
    modest_text = "Fixed the bug.\nAll tests pass."
    with patch("anthropic.Anthropic", return_value=fake_client):
        s.summarize(modest_text)
    _, kwargs = fake_client.messages.create.call_args
    assert "cutting-room floor" not in kwargs["system"]
    assert kwargs["system"] == SUMMARY_SYSTEM_PROMPT
