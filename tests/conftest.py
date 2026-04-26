"""Test configuration: ensure the project root is importable as ``src``."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def tmp_appdata(tmp_path, monkeypatch):
    """Redirect %APPDATA% so SettingsManager writes inside a tmp dir."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return tmp_path


@pytest.fixture(scope="session")
def qapp():
    """Single QApplication for tests that need a Qt event loop."""
    from PyQt6.QtWidgets import QApplication

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app
