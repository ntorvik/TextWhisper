"""System tray icon + context menu."""

from __future__ import annotations

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QBrush, QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

from .. import __version__


def _build_icon(active: bool) -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    bg = QColor(64, 220, 140) if active else QColor(110, 120, 140)
    p.setBrush(QBrush(bg))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(4, 4, 56, 56)

    p.setBrush(QColor(20, 22, 28))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(26, 16, 12, 24, 6, 6)

    pen = QPen(QColor(20, 22, 28))
    pen.setWidth(2)
    p.setPen(pen)
    p.drawLine(22, 36, 22, 40)
    p.drawLine(42, 36, 42, 40)
    p.drawArc(22, 30, 20, 14, 0, -180 * 16)
    p.drawLine(32, 44, 32, 50)
    p.drawLine(26, 50, 38, 50)
    p.end()
    return QIcon(pm)


class TrayController(QObject):
    toggle_capture = pyqtSignal()
    show_settings = pyqtSignal()
    toggle_oscilloscope = pyqtSignal()
    toggle_auto_enter = pyqtSignal()
    toggle_voice = pyqtSignal()
    interrupt_voice = pyqtSignal()
    quit_requested = pyqtSignal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.tray = QSystemTrayIcon(parent)
        self.tray.setIcon(_build_icon(False))
        self.tray.setToolTip(f"TextWhisper v{__version__} - Idle")

        menu = QMenu()
        # Convention for all toggle items: the menu text says what the NEXT
        # click will do. No checkmarks (a checkmark plus a "Hide …" label
        # reads contradictorily).
        self.action_toggle = QAction("Start Capture")
        self.action_oscilloscope = QAction("Show Oscilloscope")
        self.action_auto_enter = QAction("Enable Auto-Enter")
        self.action_voice = QAction("Enable Voice Read-Back")
        # Disabled until a read-back is in progress — mirrors how the
        # Start/Stop Capture item only flips when there's something to
        # toggle. Phase 5 wires this active/inactive from the TTS service.
        self.action_voice_interrupt = QAction("Stop Reading")
        self.action_voice_interrupt.setEnabled(False)
        self.action_settings = QAction("Settings...")
        self.action_quit = QAction("Exit")

        menu.addAction(self.action_toggle)
        menu.addSeparator()
        menu.addAction(self.action_oscilloscope)
        menu.addAction(self.action_auto_enter)
        menu.addAction(self.action_voice)
        menu.addAction(self.action_voice_interrupt)
        menu.addAction(self.action_settings)
        menu.addSeparator()
        menu.addAction(self.action_quit)

        self.tray.setContextMenu(menu)
        self._menu = menu

        self.action_toggle.triggered.connect(self.toggle_capture)
        self.action_settings.triggered.connect(self.show_settings)
        self.action_oscilloscope.triggered.connect(self.toggle_oscilloscope)
        self.action_auto_enter.triggered.connect(self.toggle_auto_enter)
        self.action_voice.triggered.connect(self.toggle_voice)
        self.action_voice_interrupt.triggered.connect(self.interrupt_voice)
        self.action_quit.triggered.connect(self.quit_requested)

        self.tray.activated.connect(self._on_activated)
        self.tray.show()

    def _on_activated(self, reason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.DoubleClick,
            QSystemTrayIcon.ActivationReason.MiddleClick,
        ):
            self.toggle_capture.emit()

    def set_active(self, active: bool) -> None:
        self.tray.setIcon(_build_icon(active))
        suffix = "Listening" if active else "Idle"
        self.tray.setToolTip(f"TextWhisper v{__version__} - {suffix}")
        self.action_toggle.setText("Stop Capture" if active else "Start Capture")

    def set_oscilloscope_visible(self, visible: bool) -> None:
        # Same convention as the Start/Stop Capture item: the menu text says
        # what the NEXT action will do. No check mark.
        self.action_oscilloscope.setText("Hide Oscilloscope" if visible else "Show Oscilloscope")

    def set_auto_enter_enabled(self, enabled: bool) -> None:
        self.action_auto_enter.setText(
            "Disable Auto-Enter" if enabled else "Enable Auto-Enter"
        )

    def set_voice_enabled(self, enabled: bool) -> None:
        self.action_voice.setText(
            "Disable Voice Read-Back" if enabled else "Enable Voice Read-Back"
        )

    def set_voice_speaking(self, speaking: bool) -> None:
        """Enable the 'Stop Reading' menu item only while a read-back is
        in progress, so a stale click can't ghost-call interrupt."""
        self.action_voice_interrupt.setEnabled(bool(speaking))

    def set_status(self, text: str) -> None:
        self.tray.setToolTip(f"TextWhisper v{__version__} - {text}")

    def notify(self, title: str, message: str, *, error: bool = False) -> None:
        icon = (
            QSystemTrayIcon.MessageIcon.Critical
            if error
            else QSystemTrayIcon.MessageIcon.Information
        )
        self.tray.showMessage(title, message, icon, 3000)
