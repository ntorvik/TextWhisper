"""System tray icon + context menu."""

from __future__ import annotations

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QBrush, QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

from .. import __version__
from .. import win32_window_utils as win32


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
    toggle_lock = pyqtSignal()
    quit_requested = pyqtSignal()

    def __init__(self, parent: QObject | None = None, settings=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._current_target_hwnd: int | None = None
        self._current_source: str = "none"
        # Foreground hwnd + target title captured at set_lock_state() time.
        # Cached so the label semantics (Lock vs Unlock vs Re-lock) reflect
        # the moment the state was set, not whatever is focused later, and
        # so we don't repeatedly hit Win32 just to repaint the menu.
        self._current_foreground_hwnd: int = 0
        self._current_target_title: str = ""
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
        # --- Paste target lock section ---
        self._lock_separator_top = menu.addSeparator()
        self._lock_status_action = menu.addAction("Paste target: <none>")
        self._lock_status_action.setEnabled(False)  # non-clickable status line
        self._lock_toggle_action = menu.addAction("Lock paste target → current window")
        self._lock_toggle_action.triggered.connect(self.toggle_lock.emit)
        self._lock_separator_bottom = menu.addSeparator()
        # --- end lock section ---
        menu.addAction(self.action_voice)
        menu.addAction(self.action_voice_interrupt)
        menu.addAction(self.action_settings)
        menu.addSeparator()
        menu.addAction(self.action_quit)

        self.tray.setContextMenu(menu)
        self._menu = menu
        # Spec §5.7: the toggle action label depends on which window is
        # foreground at the moment the menu opens (Unlock vs Re-lock), and
        # the cached target title is only valid for ~1 second / re-cached
        # on menu open. Refresh both right before the menu paints.
        menu.aboutToShow.connect(self._refresh_lock_labels)

        self.action_toggle.triggered.connect(self.toggle_capture)
        self.action_settings.triggered.connect(self.show_settings)
        self.action_oscilloscope.triggered.connect(self.toggle_oscilloscope)
        self.action_auto_enter.triggered.connect(self.toggle_auto_enter)
        self.action_voice.triggered.connect(self.toggle_voice)
        self.action_voice_interrupt.triggered.connect(self.interrupt_voice)
        self.action_quit.triggered.connect(self.quit_requested)

        self.tray.activated.connect(self._on_activated)
        self._refresh_lock_visibility()
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

    # --- paste-target lock surfacing ----------------------------------

    def set_lock_state(self, hwnd, source: str) -> None:
        self._current_target_hwnd = hwnd
        self._current_source = source
        # Visibility is purely a function of the master setting + source/hwnd,
        # so it only needs to refresh on actual state changes — not on every
        # menu open.
        self._refresh_lock_visibility()
        # One-shot label refresh: the lock chime + tray notification might
        # draw the user to inspect immediately, so the label/title must be
        # right even before they open the menu (which would otherwise re-run
        # this via aboutToShow).
        self._refresh_lock_labels()

    def _refresh_lock_labels(self) -> None:
        """Re-query foreground hwnd + target title and repaint the lock
        section's status line and toggle action.

        Wired to QMenu.aboutToShow so labels reflect live state at menu-open
        time per spec §5.7 (the toggle text picks Unlock vs Re-lock based on
        whether the locked window is currently foreground; the cached title
        is "re-cached on menu open"). Also called from set_lock_state for
        the immediate post-change update.
        """
        # Guard: Qt may fire aboutToShow before the lock-section actions
        # have been built (mirrors _refresh_lock_visibility's guard pattern).
        if not hasattr(self, "_lock_status_action"):
            return
        hwnd = self._current_target_hwnd
        self._current_foreground_hwnd = win32.get_foreground_window()
        self._current_target_title = (
            win32.get_window_title(hwnd) if hwnd is not None else ""
        )
        self._lock_status_action.setText(self._lock_status_label())
        self._lock_toggle_action.setText(self._lock_action_label())

    def _lock_section_visible(self) -> bool:
        if self.settings is None:
            return False
        return bool(self.settings.get("paste_lock_enabled", False))

    def _refresh_lock_visibility(self) -> None:
        visible = self._lock_section_visible()
        for attr in (
            "_lock_separator_top", "_lock_status_action",
            "_lock_toggle_action", "_lock_separator_bottom",
        ):
            if hasattr(self, attr):
                getattr(self, attr).setVisible(visible)

    def _lock_status_label(self) -> str:
        hwnd = self._current_target_hwnd
        if hwnd is None:
            return "Paste target: <none>"
        title = self._current_target_title or f"hwnd={hwnd}"
        if len(title) > 40:
            title = title[:37] + "..."
        suffix = " (sticky)" if self._current_source == "sticky" else ""
        return f"Paste target: {title}{suffix}"

    def _lock_action_label(self) -> str:
        hwnd = self._current_target_hwnd
        if hwnd is None or self._current_source != "sticky":
            return "Lock paste target → current window"
        title = self._current_target_title or f"hwnd={hwnd}"
        if len(title) > 30:
            title = title[:27] + "..."
        if self._current_foreground_hwnd == hwnd:
            return f"Unlock paste target ({title})"
        return f"Re-lock paste target → current window"
