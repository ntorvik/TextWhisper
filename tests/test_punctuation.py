"""Tests for punctuation-space normalization in transcription."""

from __future__ import annotations

import pytest

from src.transcription import normalize_punctuation


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Hello.World", "Hello. World"),
        ("Wait!Now go.", "Wait! Now go."),
        ("Really?Yes,really.", "Really? Yes, really."),
        ("alpha;beta:gamma,delta", "alpha; beta: gamma, delta"),
        ("Hello. World", "Hello. World"),
        ("3.14 is pi", "3.14 is pi"),
        ("e.g.right", "e. g. right"),
        ("end.", "end."),
        ("", ""),
        ("Period.At end.", "Period. At end."),
        ("multiple!!!Now", "multiple!!! Now"),
        ("comma,then text", "comma, then text"),
    ],
)
def test_normalize_punctuation(raw: str, expected: str):
    assert normalize_punctuation(raw) == expected
