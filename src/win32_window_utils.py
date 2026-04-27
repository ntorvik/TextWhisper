"""Thin function-only wrapper around the Win32 calls used by the
paste-target-lock feature.

This is the ONLY module in the project that imports
``ctypes.windll.user32`` for window inspection/control. Everything
else mocks these functions via a single import target. On platforms
other than Windows every function returns a safe default so the test
suite imports cleanly.
"""

from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)

_IS_WIN = sys.platform == "win32"


if _IS_WIN:
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32

    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.IsWindow.argtypes = [wintypes.HWND]
    _user32.IsWindow.restype = wintypes.BOOL
    _user32.IsIconic.argtypes = [wintypes.HWND]
    _user32.IsIconic.restype = wintypes.BOOL
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.GetWindowRect.restype = wintypes.BOOL
    _user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _user32.GetWindowTextLengthW.restype = ctypes.c_int
    _user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetWindowTextW.restype = ctypes.c_int
    _user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.ShowWindow.restype = wintypes.BOOL
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _user32.SetForegroundWindow.restype = wintypes.BOOL
    _user32.AttachThreadInput.argtypes = [
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.BOOL,
    ]
    _user32.AttachThreadInput.restype = wintypes.BOOL
    _user32.GetCurrentThreadId = _kernel32.GetCurrentThreadId
    _kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    _SW_RESTORE = 9


def get_foreground_window() -> int:
    if not _IS_WIN:
        return 0
    return int(_user32.GetForegroundWindow() or 0)


def is_window(hwnd: int) -> bool:
    if not _IS_WIN or not hwnd:
        return False
    return bool(_user32.IsWindow(hwnd))


def is_iconic(hwnd: int) -> bool:
    if not _IS_WIN or not hwnd:
        return False
    return bool(_user32.IsIconic(hwnd))


def get_window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    if not _IS_WIN or not hwnd:
        return None
    rect = wintypes.RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return (rect.left, rect.top, rect.right, rect.bottom)


def get_window_title(hwnd: int) -> str:
    if not _IS_WIN or not hwnd:
        return ""
    length = _user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def get_window_pid(hwnd: int) -> int:
    if not _IS_WIN or not hwnd:
        return 0
    pid = wintypes.DWORD(0)
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def get_window_process_name(hwnd: int) -> str:
    if not _IS_WIN or not hwnd:
        return ""
    pid = get_window_pid(hwnd)
    if pid == 0:
        return ""
    try:
        import psutil
        return psutil.Process(pid).name()
    except Exception:
        return ""


def restore_window(hwnd: int) -> bool:
    if not _IS_WIN or not hwnd:
        return False
    return bool(_user32.ShowWindow(hwnd, _SW_RESTORE))


def set_foreground_with_attach(hwnd: int) -> bool:
    """SetForegroundWindow with the AttachThreadInput mitigation.

    Windows refuses SetForegroundWindow under most conditions unless the
    calling thread has been recently active. The standard workaround is
    to attach our input queue to the target window's thread for the
    duration of the call, then detach. Returns True on success, False if
    Windows refused (e.g. UIPI block on an elevated target).
    """
    if not _IS_WIN or not hwnd:
        return False
    target_tid = _user32.GetWindowThreadProcessId(hwnd, None)
    our_tid = _user32.GetCurrentThreadId()
    if target_tid == 0:
        return False
    attached = False
    try:
        if target_tid != our_tid:
            attached = bool(_user32.AttachThreadInput(our_tid, target_tid, True))
        ok = bool(_user32.SetForegroundWindow(hwnd))
        return ok
    except Exception:
        log.exception("set_foreground_with_attach failed for hwnd=%s", hwnd)
        return False
    finally:
        if attached:
            try:
                _user32.AttachThreadInput(our_tid, target_tid, False)
            except Exception:
                log.exception("AttachThreadInput detach failed")
