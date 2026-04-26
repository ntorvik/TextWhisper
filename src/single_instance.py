"""Cross-process single-instance lock.

On Windows uses a named ``CreateMutex`` — the OS guarantees only one process
holding a particular mutex name. On other platforms falls back to a PID lock
file in the user's config dir.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)

_DEFAULT_MUTEX_NAME = "Global\\TextWhisper-SingleInstance-7d2e5b41"


class SingleInstance:
    """Acquires a system-wide named lock. ``already_running`` reports the result."""

    def __init__(self, name: str | None = None) -> None:
        self.already_running = False
        self._handle = None
        self._lockfile: Path | None = None
        self._name = name or _DEFAULT_MUTEX_NAME
        if sys.platform == "win32":
            self._acquire_windows()
        else:
            self._acquire_pidfile()

    # --- Windows: named mutex ------------------------------------------

    def _acquire_windows(self) -> None:
        try:
            import win32api  # type: ignore[import-not-found]
            import win32event  # type: ignore[import-not-found]
            import winerror  # type: ignore[import-not-found]
        except ImportError:
            log.warning("pywin32 not available — single-instance lock disabled.")
            return
        try:
            self._handle = win32event.CreateMutex(None, False, self._name)
            if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
                self.already_running = True
                # Don't keep the handle around if someone else owns the mutex.
                win32api.CloseHandle(self._handle)
                self._handle = None
        except Exception:
            log.exception("Mutex creation failed; allowing duplicate instance.")

    # --- Fallback: PID file --------------------------------------------

    def _acquire_pidfile(self) -> None:
        cfg = Path(os.environ.get("APPDATA") or Path.home() / ".config") / "TextWhisper"
        cfg.mkdir(parents=True, exist_ok=True)
        self._lockfile = cfg / "textwhisper.pid"
        if self._lockfile.exists():
            try:
                pid = int(self._lockfile.read_text().strip())
                if _pid_alive(pid):
                    self.already_running = True
                    return
            except (ValueError, OSError):
                pass
        try:
            self._lockfile.write_text(str(os.getpid()))
        except OSError:
            log.exception("Could not write PID lockfile.")

    # ---------------------------------------------------------------

    def release(self) -> None:
        if self._handle is not None:
            try:
                import win32api  # type: ignore[import-not-found]

                win32api.CloseHandle(self._handle)
            except Exception:
                pass
            self._handle = None
        if self._lockfile is not None and self._lockfile.exists():
            try:
                if self._lockfile.read_text().strip() == str(os.getpid()):
                    self._lockfile.unlink()
            except OSError:
                pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True
