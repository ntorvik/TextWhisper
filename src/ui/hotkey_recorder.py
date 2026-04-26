"""Modal dialog that captures a key combination and emits a pynput hotkey string.

Usage::

    dlg = HotkeyRecorder(parent=self, current="<alt>+z")
    if dlg.exec():
        new_hotkey = dlg.captured  # e.g. "<ctrl>+<shift>+v"
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QKeyEvent
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

_MODIFIER_KEYS = {
    Qt.Key.Key_Shift,
    Qt.Key.Key_Control,
    Qt.Key.Key_Alt,
    Qt.Key.Key_AltGr,
    Qt.Key.Key_Meta,
    Qt.Key.Key_Super_L,
    Qt.Key.Key_Super_R,
    Qt.Key.Key_CapsLock,
    Qt.Key.Key_NumLock,
    Qt.Key.Key_ScrollLock,
}

_SPECIAL_KEYS: dict[int, str] = {
    Qt.Key.Key_F1: "<f1>",
    Qt.Key.Key_F2: "<f2>",
    Qt.Key.Key_F3: "<f3>",
    Qt.Key.Key_F4: "<f4>",
    Qt.Key.Key_F5: "<f5>",
    Qt.Key.Key_F6: "<f6>",
    Qt.Key.Key_F7: "<f7>",
    Qt.Key.Key_F8: "<f8>",
    Qt.Key.Key_F9: "<f9>",
    Qt.Key.Key_F10: "<f10>",
    Qt.Key.Key_F11: "<f11>",
    Qt.Key.Key_F12: "<f12>",
    Qt.Key.Key_F13: "<f13>",
    Qt.Key.Key_F14: "<f14>",
    Qt.Key.Key_F15: "<f15>",
    Qt.Key.Key_F16: "<f16>",
    Qt.Key.Key_Delete: "<delete>",
    Qt.Key.Key_Backspace: "<backspace>",
    Qt.Key.Key_Tab: "<tab>",
    Qt.Key.Key_Backtab: "<tab>",
    Qt.Key.Key_Return: "<enter>",
    Qt.Key.Key_Enter: "<enter>",
    Qt.Key.Key_Escape: "<esc>",
    Qt.Key.Key_Space: "<space>",
    Qt.Key.Key_Up: "<up>",
    Qt.Key.Key_Down: "<down>",
    Qt.Key.Key_Left: "<left>",
    Qt.Key.Key_Right: "<right>",
    Qt.Key.Key_Home: "<home>",
    Qt.Key.Key_End: "<end>",
    Qt.Key.Key_PageUp: "<page_up>",
    Qt.Key.Key_PageDown: "<page_down>",
    Qt.Key.Key_Insert: "<insert>",
    Qt.Key.Key_Print: "<print_screen>",
    Qt.Key.Key_Pause: "<pause>",
    Qt.Key.Key_Menu: "<menu>",
}


def qt_key_to_pynput(qt_key: int, text: str) -> str | None:
    """Translate a Qt.Key + event.text() to a pynput hotkey token.

    Returns ``None`` if the key cannot be represented (e.g. dead key, unknown).
    """
    if qt_key in _SPECIAL_KEYS:
        return _SPECIAL_KEYS[qt_key]
    if Qt.Key.Key_A <= qt_key <= Qt.Key.Key_Z:
        return chr(qt_key).lower()
    if Qt.Key.Key_0 <= qt_key <= Qt.Key.Key_9:
        return chr(qt_key)
    if text and len(text) == 1 and text.isprintable():
        ch = text.lower()
        # '+' collides with pynput's separator — represent it as the alias.
        if ch == "+":
            return "<plus>"
        return ch
    return None


def build_hotkey_string(modifiers: Qt.KeyboardModifier, qt_key: int, text: str) -> str | None:
    """Combine Qt modifiers + main key into a pynput hotkey string."""
    key_token = qt_key_to_pynput(qt_key, text)
    if key_token is None:
        return None
    parts: list[str] = []
    if modifiers & Qt.KeyboardModifier.ControlModifier:
        parts.append("<ctrl>")
    if modifiers & Qt.KeyboardModifier.AltModifier:
        parts.append("<alt>")
    if modifiers & Qt.KeyboardModifier.ShiftModifier:
        parts.append("<shift>")
    if modifiers & Qt.KeyboardModifier.MetaModifier:
        parts.append("<cmd>")
    parts.append(key_token)
    return "+".join(parts)


class HotkeyRecorder(QDialog):
    """Modal capture dialog. Press the desired chord; Esc to cancel."""

    def __init__(self, parent=None, current: str = "") -> None:
        super().__init__(parent)
        self.captured: str | None = None
        self.setWindowTitle("Record hotkey")
        self.setModal(True)
        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)
        prompt = QLabel("Press the key combination you want to use.")
        prompt.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(prompt)

        self.preview = QLabel(current or "—")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont()
        font.setPointSize(14)
        font.setBold(True)
        self.preview.setFont(font)
        self.preview.setStyleSheet(
            "padding: 12px; border: 1px solid #555; border-radius: 6px; "
            "background: rgba(255,255,255,0.04);"
        )
        layout.addWidget(self.preview)

        hint = QLabel("(Backspace clears, Esc cancels, Save accepts.)")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(hint)

        buttons = QHBoxLayout()
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._clear)
        clear_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        cancel_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._save)
        save_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        buttons.addWidget(clear_btn)
        buttons.addStretch(1)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(save_btn)
        layout.addLayout(buttons)

        # Make sure the dialog itself receives all key events.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._pending: str | None = None
        if current:
            self._pending = current

    def _clear(self) -> None:
        self._pending = None
        self.preview.setText("—")
        self.setFocus()

    def _save(self) -> None:
        if not self._pending:
            self.reject()
            return
        self.captured = self._pending
        self.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (Qt API)
        key = event.key()
        if key == Qt.Key.Key_Escape:
            super().keyPressEvent(event)
            self.reject()
            return
        if key in _MODIFIER_KEYS:
            # Wait for a non-modifier so we can build a chord.
            event.accept()
            return
        chord = build_hotkey_string(event.modifiers(), key, event.text())
        if chord is None:
            event.accept()
            return
        self._pending = chord
        self.preview.setText(chord)
        event.accept()
