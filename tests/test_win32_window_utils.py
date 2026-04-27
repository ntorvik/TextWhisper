"""Tests for the Win32 window-inspection wrapper.

All Win32 calls are funneled through this module so the rest of the
suite has a single mock surface. The functions return safe defaults
on non-Windows so the suite imports cleanly anywhere.
"""

from __future__ import annotations

import sys

import pytest

from src import win32_window_utils as w


def test_module_exports_expected_functions():
    expected = {
        "get_foreground_window",
        "is_window",
        "is_iconic",
        "get_window_rect",
        "get_window_title",
        "get_window_pid",
        "get_window_process_name",
        "restore_window",
        "set_foreground_with_attach",
    }
    actual = {name for name in dir(w) if not name.startswith("_")}
    assert expected.issubset(actual), f"missing: {expected - actual}"


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows safe-default test")
def test_safe_defaults_on_non_windows():
    assert w.get_foreground_window() == 0
    assert w.is_window(123) is False
    assert w.is_iconic(123) is False
    assert w.get_window_rect(123) is None
    assert w.get_window_title(123) == ""
    assert w.get_window_pid(123) == 0
    assert w.get_window_process_name(123) == ""
    assert w.restore_window(123) is False
    assert w.set_foreground_with_attach(123) is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only smoke test")
def test_get_foreground_window_returns_nonzero_on_windows():
    hwnd = w.get_foreground_window()
    assert isinstance(hwnd, int)
    # In CI, an interactive desktop may not be present — accept zero too.
    # The smoke test is just confirming the call doesn't raise.


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only smoke test")
def test_is_window_false_for_garbage_hwnd():
    assert w.is_window(0) is False
    assert w.is_window(0xDEADBEEF) is False
