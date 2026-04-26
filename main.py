import logging
import os
import sys
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path


def _setup_logging() -> Path:
    log_dir = Path(__file__).resolve().parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "textwhisper.log"
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    # pythonw.exe has no stdout (sys.stdout is None) — only attach a stream
    # handler when running from a real console.
    if sys.stdout is not None and getattr(sys.stdout, "fileno", None):
        try:
            sys.stdout.fileno()
            stream = logging.StreamHandler(sys.stdout)
            stream.setFormatter(fmt)
            root.addHandler(stream)
        except (OSError, ValueError):
            pass
    return log_path


def main() -> int:
    log_path = _setup_logging()
    log = logging.getLogger("textwhisper")
    log.info("Starting TextWhisper (python=%s, cwd=%s)", sys.version.split()[0], os.getcwd())
    try:
        # MUST run before any ctranslate2/faster_whisper import — adds the pip
        # nvidia DLL folders to Windows' search path.
        from src.cuda_setup import prepare_cuda_dll_search_path

        prepare_cuda_dll_search_path()

        from PyQt6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

        from src.app import TextWhisperApp
        from src.single_instance import SingleInstance

        app = QApplication(sys.argv)
        app.setApplicationName("TextWhisper")
        app.setOrganizationName("TextWhisper")
        app.setQuitOnLastWindowClosed(False)

        instance = SingleInstance()
        if instance.already_running:
            log.info("Another TextWhisper instance is already running; exiting.")
            QMessageBox.information(
                None,
                "TextWhisper",
                "TextWhisper is already running.\n\nLook for the microphone icon in "
                "your system tray (you may need to click the ^ chevron).",
            )
            return 0

        if not QSystemTrayIcon.isSystemTrayAvailable():
            QMessageBox.critical(
                None,
                "TextWhisper",
                "System tray is not available on this system.",
            )
            log.error("System tray unavailable; exiting.")
            return 1

        tw = TextWhisperApp(app)
        tw.run()
        log.info("Event loop starting.")
        try:
            return app.exec()
        finally:
            instance.release()
    except Exception:
        log.exception("Fatal startup error")
        try:
            from PyQt6.QtWidgets import QApplication, QMessageBox

            _ = QApplication.instance() or QApplication(sys.argv)
            QMessageBox.critical(
                None,
                "TextWhisper - Fatal Error",
                f"Startup failed.\n\nSee log:\n{log_path}\n\n{traceback.format_exc()}",
            )
        except Exception:
            sys.stderr.write(traceback.format_exc())
        return 2


if __name__ == "__main__":
    sys.exit(main())
