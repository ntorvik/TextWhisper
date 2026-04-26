"""Make pip-installed NVIDIA CUDA libraries discoverable on Windows.

faster-whisper / CTranslate2 dlopen ``cublas64_12.dll``, ``cudnn_*64_9.dll`` etc.
by name. The pip wheels ``nvidia-cublas-cu12`` / ``nvidia-cudnn-cu12`` ship those
DLLs under ``site-packages/nvidia/<sub>/bin/`` but Windows won't find them unless
those directories are explicitly added to the DLL search path BEFORE ctranslate2
is imported.

Call :func:`prepare_cuda_dll_search_path` once at process start (from main.py).
It is a no-op on non-Windows platforms or when the nvidia packages aren't
installed.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)


def prepare_cuda_dll_search_path() -> list[Path]:
    """Add every ``site-packages/nvidia/*/bin`` directory to the DLL search path.

    Returns the list of directories successfully added.
    """
    if sys.platform != "win32":
        return []

    added: list[Path] = []
    nvidia_root = _find_nvidia_root()
    if nvidia_root is None:
        log.info("CUDA setup: no pip-managed nvidia packages found.")
        return added

    for sub in sorted(nvidia_root.iterdir()):
        bin_dir = sub / "bin"
        if not bin_dir.is_dir():
            continue
        try:
            os.add_dll_directory(str(bin_dir))
            added.append(bin_dir)
        except OSError as e:
            log.warning("Could not add CUDA DLL dir %s: %s", bin_dir, e)

    if added:
        # Also prepend to PATH so any further dependent DLLs resolve correctly.
        os.environ["PATH"] = os.pathsep.join([str(p) for p in added] + [os.environ.get("PATH", "")])
        log.info("CUDA setup: added %d DLL directories: %s", len(added), [p.name for p in added])
    return added


def _find_nvidia_root() -> Path | None:
    try:
        import nvidia  # type: ignore[import-not-found]
    except ImportError:
        return None
    pkg_file = getattr(nvidia, "__file__", None)
    if pkg_file:
        return Path(pkg_file).resolve().parent
    paths = list(getattr(nvidia, "__path__", []))
    return Path(paths[0]).resolve() if paths else None
