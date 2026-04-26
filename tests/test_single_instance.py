"""Tests for SingleInstance.

We use a per-test mutex name so the tests don't collide with the real running
TextWhisper app (which would otherwise hold the production mutex).
"""

from __future__ import annotations

import uuid

from src.single_instance import SingleInstance


def _name() -> str:
    return f"Local\\TextWhisper-Test-{uuid.uuid4().hex}"


def test_single_instance_acquires(tmp_appdata):
    inst = SingleInstance(name=_name())
    try:
        assert inst.already_running is False
    finally:
        inst.release()


def test_release_is_idempotent(tmp_appdata):
    inst = SingleInstance(name=_name())
    inst.release()
    inst.release()  # should not raise


def test_second_instance_detects_first(tmp_appdata):
    """Two SingleInstance objects with the same name in one process: second sees first."""
    name = _name()
    first = SingleInstance(name=name)
    try:
        assert first.already_running is False
        second = SingleInstance(name=name)
        try:
            assert second.already_running is True
        finally:
            second.release()
    finally:
        first.release()
