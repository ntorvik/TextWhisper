"""Settings dialog for TextWhisper."""

from __future__ import annotations

import html

import sounddevice as sd
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..hotkey_manager import validate_hotkeys
from .hotkey_recorder import HotkeyRecorder

_LANGUAGES = [
    "auto", "en", "es", "fr", "de", "it", "pt", "nl",
    "ja", "ko", "zh", "ru", "ar", "hi", "pl", "sv", "tr",
]

_PALETTE = [
    ("Mint",     "#40dc8c"),
    ("Cyan",     "#3dd6e0"),
    ("Sky",      "#5aa9ff"),
    ("Iris",     "#8a7bff"),
    ("Magenta",  "#ff5cc4"),
    ("Coral",    "#ff7a59"),
    ("Amber",    "#ffc857"),
    ("Lime",     "#bce046"),
    ("Slate",    "#7884a0"),
    ("White",    "#e6ecff"),
]


class ColorButton(QPushButton):
    """Push button that opens a palette + custom QColorDialog and stores hex."""

    def __init__(self, initial: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(28)
        self.setMinimumWidth(110)
        self._color = QColor(initial) if QColor(initial).isValid() else QColor("#40dc8c")
        self._refresh_label()
        self.clicked.connect(self._pick)

    def hex_value(self) -> str:
        return self._color.name()

    def set_hex(self, value: str) -> None:
        c = QColor(value)
        if c.isValid():
            self._color = c
            self._refresh_label()

    def _refresh_label(self) -> None:
        text_color = "#000000" if self._color.lightnessF() > 0.6 else "#ffffff"
        self.setText(self._color.name())
        self.setStyleSheet(
            f"QPushButton {{ background:{self._color.name()}; color:{text_color};"
            "  border:1px solid #555; border-radius:4px; padding:4px 8px; }}"
        )

    def _pick(self) -> None:
        menu_color = _palette_pick(self._color, self)
        if menu_color is not None and menu_color.isValid():
            self._color = menu_color
            self._refresh_label()


def _palette_pick(initial: QColor, parent: QWidget) -> QColor | None:
    """Show a small palette dialog with named swatches + 'Custom...' button."""
    dlg = QDialog(parent)
    dlg.setWindowTitle("Pick a color")
    layout = QVBoxLayout(dlg)
    chosen: dict[str, QColor] = {}

    def make_swatch(name: str, hex_: str) -> QPushButton:
        btn = QPushButton(name)
        btn.setFixedSize(80, 36)
        text_color = "#000000" if QColor(hex_).lightnessF() > 0.6 else "#ffffff"
        btn.setStyleSheet(
            f"QPushButton {{ background:{hex_}; color:{text_color};"
            "  border:1px solid #555; border-radius:4px; }}"
        )

        def click():
            chosen["c"] = QColor(hex_)
            dlg.accept()

        btn.clicked.connect(click)
        return btn

    row1 = QHBoxLayout()
    row2 = QHBoxLayout()
    for i, (name, hex_) in enumerate(_PALETTE):
        (row1 if i < 5 else row2).addWidget(make_swatch(name, hex_))
    layout.addLayout(row1)
    layout.addLayout(row2)

    custom_btn = QPushButton("Custom color...")

    def custom():
        c = QColorDialog.getColor(initial, dlg, "Choose custom color")
        if c.isValid():
            chosen["c"] = c
            dlg.accept()

    custom_btn.clicked.connect(custom)
    cancel_btn = QPushButton("Cancel")
    cancel_btn.clicked.connect(dlg.reject)
    btns = QHBoxLayout()
    btns.addWidget(custom_btn)
    btns.addStretch(1)
    btns.addWidget(cancel_btn)
    layout.addLayout(btns)

    if dlg.exec():
        return chosen.get("c")
    return None


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("TextWhisper Settings")
        self.setMinimumWidth(520)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)

        self.tabs = QTabWidget(self)
        self.tabs.addTab(self._build_hotkeys_tab(), "Hotkeys")
        self.tabs.addTab(self._build_speech_tab(), "Speech")
        self.tabs.addTab(self._build_output_tab(), "Output")
        self.tabs.addTab(self._build_feedback_tab(), "Feedback")
        self.tabs.addTab(self._build_oscilloscope_tab(), "Oscilloscope")
        self.tabs.addTab(self._build_about_tab(), "About")

        info = QLabel(
            "Note: changing model or device reloads Whisper in the background. "
            "Hotkey and microphone changes apply on save."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #888;")

        save_btn = QPushButton("Save")
        cancel_btn = QPushButton("Cancel")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._save)
        cancel_btn.clicked.connect(self.reject)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(cancel_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs)
        layout.addSpacing(4)
        layout.addWidget(info)
        layout.addSpacing(4)
        layout.addLayout(btn_row)

        # Hotkey live-validation must run after both line edits exist.
        self.hotkey_edit.textChanged.connect(self._refresh_hotkey_warning)
        self.delete_hotkey_edit.textChanged.connect(self._refresh_hotkey_warning)
        self._refresh_hotkey_warning()

    # -------------------------------------------------------------------
    # Tabs
    # -------------------------------------------------------------------

    def _build_hotkeys_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)

        self.hotkey_edit = QLineEdit(str(self.settings.get("hotkey", "<alt>+z")))
        self.hotkey_edit.setPlaceholderText("<alt>+z")
        self.hotkey_edit.setToolTip(
            "Click Record to capture a key combination, or type it manually "
            "(pynput syntax: <alt>+z, <ctrl>+<shift>+v)."
        )
        record_hotkey_btn = QPushButton("Record...")
        record_hotkey_btn.clicked.connect(lambda: self._record_into(self.hotkey_edit))
        form.addRow("Dictation hotkey:", self._row(self.hotkey_edit, record_hotkey_btn))

        self.delete_hotkey_edit = QLineEdit(str(self.settings.get("delete_hotkey", "<delete>")))
        self.delete_hotkey_edit.setPlaceholderText("<delete>")
        self.delete_hotkey_edit.setToolTip(
            "Single tap = delete previous word.  Double tap = delete the entire last "
            "transcription.  A bare modifier-less key like <delete> will conflict "
            "with that key's normal function — prefer e.g. <ctrl>+<backspace> if so."
        )
        record_delete_btn = QPushButton("Record...")
        record_delete_btn.clicked.connect(lambda: self._record_into(self.delete_hotkey_edit))
        form.addRow("Delete-word hotkey:", self._row(self.delete_hotkey_edit, record_delete_btn))

        self.double_tap_spin = QSpinBox()
        self.double_tap_spin.setRange(100, 1000)
        self.double_tap_spin.setSingleStep(50)
        self.double_tap_spin.setSuffix(" ms")
        self.double_tap_spin.setValue(int(self.settings.get("delete_double_tap_ms", 350)))
        self.double_tap_spin.setToolTip(
            "Window for detecting a double-tap on the delete hotkey."
        )
        form.addRow("Double-tap window:", self.double_tap_spin)

        self.hotkey_warning = QLabel("")
        self.hotkey_warning.setWordWrap(True)
        self.hotkey_warning.setVisible(False)
        form.addRow("", self.hotkey_warning)
        return page

    def _build_speech_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)

        self.model_combo = QComboBox()
        self.model_combo.addItems(
            ["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"]
        )
        self.model_combo.setCurrentText(str(self.settings.get("model_size", "large-v3")))
        form.addRow("Whisper model:", self.model_combo)

        self.device_combo = QComboBox()
        self.device_combo.addItems(["cuda", "cpu", "auto"])
        self.device_combo.setCurrentText(str(self.settings.get("device", "cuda")))
        form.addRow("Device:", self.device_combo)

        self.compute_combo = QComboBox()
        self.compute_combo.addItems(["float16", "int8_float16", "int8", "float32"])
        self.compute_combo.setCurrentText(str(self.settings.get("compute_type", "float16")))
        form.addRow("Compute type:", self.compute_combo)

        self.mic_combo = QComboBox()
        self.mic_combo.addItem("System default", None)
        try:
            for idx, dev in enumerate(sd.query_devices()):
                if int(dev.get("max_input_channels", 0)) > 0:
                    self.mic_combo.addItem(f"{idx}: {dev['name']}", idx)
        except Exception as e:
            self.mic_combo.addItem(f"(error listing devices: {e})", None)
        current_mic = self.settings.get("microphone_device")
        for i in range(self.mic_combo.count()):
            if self.mic_combo.itemData(i) == current_mic:
                self.mic_combo.setCurrentIndex(i)
                break
        form.addRow("Microphone:", self.mic_combo)

        self.lang_combo = QComboBox()
        self.lang_combo.setEditable(True)
        self.lang_combo.addItems(_LANGUAGES)
        self.lang_combo.setCurrentText(str(self.settings.get("language", "auto")))
        form.addRow("Language:", self.lang_combo)

        self.silence_spin = QSpinBox()
        self.silence_spin.setRange(200, 3000)
        self.silence_spin.setSingleStep(50)
        self.silence_spin.setSuffix(" ms")
        self.silence_spin.setValue(int(self.settings.get("vad_silence_ms", 700)))
        form.addRow("Silence pause:", self.silence_spin)

        self.thresh_spin = QDoubleSpinBox()
        self.thresh_spin.setRange(0.001, 0.5)
        self.thresh_spin.setSingleStep(0.002)
        self.thresh_spin.setDecimals(3)
        self.thresh_spin.setValue(float(self.settings.get("vad_threshold", 0.012)))
        form.addRow("Voice threshold (RMS):", self.thresh_spin)
        return page

    def _build_output_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)

        self.output_method_combo = QComboBox()
        self.output_method_combo.addItem("Type (char-by-char keystrokes)", "type")
        self.output_method_combo.addItem("Paste (clipboard + Ctrl+V)", "paste")
        current_method = str(self.settings.get("output_method", "type"))
        for i in range(self.output_method_combo.count()):
            if self.output_method_combo.itemData(i) == current_method:
                self.output_method_combo.setCurrentIndex(i)
                break
        self.output_method_combo.setToolTip(
            "Type: simulates keystrokes for each character. Works in most apps.\n"
            "Paste: writes the text to the clipboard and sends Ctrl+V. More "
            "reliable in terminal apps (Claude Code, Windows Terminal, IDE "
            "consoles) where bare-character injection sometimes drops spaces."
        )
        form.addRow("Output method:", self.output_method_combo)

        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(0, 50)
        self.delay_spin.setSuffix(" ms")
        self.delay_spin.setValue(int(self.settings.get("type_delay_ms", 4)))
        form.addRow("Per-character type delay:", self.delay_spin)

        self.auto_enter_check = QCheckBox(
            "Auto-press Enter after each transcription (hands-free)"
        )
        self.auto_enter_check.setChecked(
            bool(self.settings.get("auto_enter_enabled", False))
        )
        self.auto_enter_check.setToolTip(
            "After your transcription is typed, automatically press Enter "
            "after the delay below — useful for fully hands-free chat / "
            "Claude Code workflows.\n\n"
            "Pressing ANY key during the delay silently cancels that pending "
            "Enter. The next transcription re-arms it."
        )
        form.addRow("Auto-Enter:", self.auto_enter_check)

        self.auto_enter_delay_spin = QSpinBox()
        self.auto_enter_delay_spin.setRange(200, 30000)
        self.auto_enter_delay_spin.setSingleStep(250)
        self.auto_enter_delay_spin.setSuffix(" ms")
        self.auto_enter_delay_spin.setValue(
            int(self.settings.get("auto_enter_delay_ms", 3000))
        )
        form.addRow("Auto-Enter delay:", self.auto_enter_delay_spin)
        return page

    def _build_feedback_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)

        self.notifications_check = QCheckBox("Show tray pop-up notifications")
        self.notifications_check.setChecked(
            bool(self.settings.get("notifications_enabled", True))
        )
        self.notifications_check.setToolTip(
            "When off, no balloon toasts are shown — startup, ready, or error messages "
            "still go to logs/textwhisper.log and the tray icon tooltip."
        )
        form.addRow("Notifications:", self.notifications_check)

        self.clipboard_check = QCheckBox("Copy each transcription to the clipboard")
        self.clipboard_check.setChecked(bool(self.settings.get("clipboard_enabled", True)))
        self.clipboard_check.setToolTip(
            "If your focus isn't on a text field, you can paste the missed dictation "
            "with Ctrl+V."
        )
        form.addRow("Clipboard:", self.clipboard_check)

        self.ready_sound_check = QCheckBox("Play soft chime when capture is ready")
        self.ready_sound_check.setChecked(bool(self.settings.get("play_ready_sound", True)))
        self.ready_sound_check.setToolTip(
            "A short two-note ascending chime plays after pressing the dictation "
            "hotkey, BEFORE the microphone opens — so the mic doesn't pick up the tone."
        )
        form.addRow("Ready sound:", self.ready_sound_check)

        self.stop_sound_check = QCheckBox("Play soft chime when capture stops")
        self.stop_sound_check.setChecked(bool(self.settings.get("play_stop_sound", False)))
        form.addRow("Stop sound:", self.stop_sound_check)

        self.sound_vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.sound_vol_slider.setRange(0, 100)
        self.sound_vol_slider.setSingleStep(5)
        self.sound_vol_slider.setValue(
            int(round(float(self.settings.get("sound_volume", 0.15)) * 100))
        )
        self.sound_vol_value = QLabel(f"{self.sound_vol_slider.value()}%")
        self.sound_vol_slider.valueChanged.connect(
            lambda v: self.sound_vol_value.setText(f"{v}%")
        )
        form.addRow("Sound volume:", self._row(self.sound_vol_slider, self.sound_vol_value))
        return page

    def _build_oscilloscope_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)

        self.osc_check = QCheckBox("Show oscilloscope")
        self.osc_check.setChecked(bool(self.settings.get("oscilloscope.enabled", True)))
        form.addRow("Oscilloscope:", self.osc_check)

        self.style_combo = QComboBox()
        self.style_combo.addItem("Waveform (scrolling)", "waveform")
        self.style_combo.addItem("Spectrum (frequency bars)", "spectrum")
        current_style = str(self.settings.get("oscilloscope.style", "waveform"))
        for i in range(self.style_combo.count()):
            if self.style_combo.itemData(i) == current_style:
                self.style_combo.setCurrentIndex(i)
                break
        self.style_combo.setToolTip(
            "Waveform: classic scrolling oscilloscope — recent audio scrolls "
            "right-to-left.\n"
            "Spectrum: fixed-position frequency bars that bounce up and down "
            "based on the energy in each band."
        )
        form.addRow("Visualization:", self.style_combo)

        self.shape_combo = QComboBox()
        self.shape_combo.addItem("Rounded rectangle", "rounded")
        self.shape_combo.addItem("Pill", "pill")
        self.shape_combo.addItem("Sharp rectangle", "rect")
        current_shape = str(self.settings.get("oscilloscope.shape", "rounded"))
        for i in range(self.shape_combo.count()):
            if self.shape_combo.itemData(i) == current_shape:
                self.shape_combo.setCurrentIndex(i)
                break
        form.addRow("Shape:", self.shape_combo)

        self.osc_w_spin = QSpinBox()
        self.osc_w_spin.setRange(120, 2000)
        self.osc_w_spin.setSingleStep(20)
        self.osc_w_spin.setSuffix(" px")
        self.osc_w_spin.setValue(int(self.settings.get("oscilloscope.width", 320)))
        form.addRow("Width:", self.osc_w_spin)

        self.osc_h_spin = QSpinBox()
        self.osc_h_spin.setRange(24, 400)
        self.osc_h_spin.setSingleStep(4)
        self.osc_h_spin.setSuffix(" px")
        self.osc_h_spin.setValue(int(self.settings.get("oscilloscope.height", 48)))
        form.addRow("Height:", self.osc_h_spin)

        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(15, 100)
        self.opacity_slider.setSingleStep(5)
        opacity_pct = int(round(float(self.settings.get("oscilloscope.opacity", 0.85)) * 100))
        self.opacity_slider.setValue(max(15, min(100, opacity_pct)))
        self.opacity_value = QLabel(f"{self.opacity_slider.value()}%")
        self.opacity_slider.valueChanged.connect(
            lambda v: self.opacity_value.setText(f"{v}%")
        )
        form.addRow("Window opacity:", self._row(self.opacity_slider, self.opacity_value))

        self.bg_slider = QSlider(Qt.Orientation.Horizontal)
        self.bg_slider.setRange(0, 255)
        self.bg_slider.setValue(int(self.settings.get("oscilloscope.background_alpha", 130)))
        self.bg_value = QLabel(str(self.bg_slider.value()))
        self.bg_slider.valueChanged.connect(lambda v: self.bg_value.setText(str(v)))
        form.addRow("Background alpha:", self._row(self.bg_slider, self.bg_value))

        self.color_active_btn = ColorButton(
            str(self.settings.get("oscilloscope.color_active", "#40dc8c"))
        )
        form.addRow("Active color:", self.color_active_btn)

        self.color_idle_btn = ColorButton(
            str(self.settings.get("oscilloscope.color_idle", "#7884a0"))
        )
        form.addRow("Idle color:", self.color_idle_btn)

        self.osc_reset_pos_btn = QPushButton("Reset position")
        self.osc_reset_pos_btn.clicked.connect(self._reset_osc_pos)
        self.osc_reset_size_btn = QPushButton("Reset size")
        self.osc_reset_size_btn.clicked.connect(self._reset_osc_size)
        form.addRow("", self._row(self.osc_reset_pos_btn, self.osc_reset_size_btn, stretch=True))
        return page

    def _build_about_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(20, 20, 20, 20)
        v.setSpacing(10)

        # Detect whether we're running from source or from a PyInstaller bundle.
        import sys
        build_mode = "PyInstaller .exe" if getattr(sys, "frozen", False) else "Source (Python)"

        title = QLabel("<h2>TextWhisper</h2>")
        title.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(title)

        version_label = QLabel(f"<b>Version:</b> {__version__}    <b>Build:</b> {build_mode}")
        version_label.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(version_label)

        tagline = QLabel(
            "Local, offline voice-to-text. Press a hotkey, talk, your words appear "
            "in whatever app has focus. Nothing is sent to the cloud."
        )
        tagline.setWordWrap(True)
        tagline.setStyleSheet("color: #aaa;")
        v.addWidget(tagline)

        v.addSpacing(8)

        repo = QLabel(
            '<a href="https://github.com/ntorvik/TextWhisper" '
            'style="color:#5aa9ff;">https://github.com/ntorvik/TextWhisper</a>'
        )
        repo.setTextFormat(Qt.TextFormat.RichText)
        repo.setOpenExternalLinks(True)
        v.addWidget(repo)

        license_label = QLabel("Released under the <b>MIT License</b>.")
        license_label.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(license_label)

        v.addSpacing(8)

        ack_header = QLabel("<b>Built on top of:</b>")
        ack_header.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(ack_header)

        ack = QLabel(
            "• <a href='https://github.com/SYSTRAN/faster-whisper' style='color:#5aa9ff;'>"
            "faster-whisper</a> (CTranslate2 + Whisper) for transcription<br>"
            "• <a href='https://github.com/moses-palmer/pynput' style='color:#5aa9ff;'>"
            "pynput</a> for global hotkeys + keyboard injection<br>"
            "• <a href='https://python-sounddevice.readthedocs.io/' style='color:#5aa9ff;'>"
            "sounddevice</a> for microphone I/O<br>"
            "• <a href='https://www.riverbankcomputing.com/software/pyqt/' style='color:#5aa9ff;'>"
            "PyQt6</a> for the UI<br>"
            "• <a href='https://pyinstaller.org/' style='color:#5aa9ff;'>PyInstaller</a> "
            "for cross-platform packaging"
        )
        ack.setTextFormat(Qt.TextFormat.RichText)
        ack.setOpenExternalLinks(True)
        ack.setWordWrap(True)
        v.addWidget(ack)

        v.addStretch(1)

        log_path_label = QLabel(
            "<small style='color:#888;'>Logs: <code>%APPDATA%\\TextWhisper\\logs\\textwhisper.log</code><br>"
            "Config: <code>%APPDATA%\\TextWhisper\\config.json</code></small>"
        )
        log_path_label.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(log_path_label)
        return page

    @staticmethod
    def _row(*widgets: QWidget, stretch: bool = False) -> QWidget:
        """Wrap widgets in a horizontal layout for use as a single form-row value."""
        h = QHBoxLayout()
        h.setContentsMargins(0, 0, 0, 0)
        for i, w in enumerate(widgets):
            h.addWidget(w, 1 if (i == 0 and not stretch) else 0)
        if stretch:
            h.addStretch(1)
        wrap = QWidget()
        wrap.setLayout(h)
        return wrap

    def _reset_osc_pos(self) -> None:
        self.settings.set("oscilloscope.x", None)
        self.settings.set("oscilloscope.y", None)
        QMessageBox.information(
            self,
            "Position reset",
            "Oscilloscope will recenter at the bottom of the screen on next show.",
        )

    def _reset_osc_size(self) -> None:
        self.osc_w_spin.setValue(320)
        self.osc_h_spin.setValue(48)
        self.opacity_slider.setValue(85)
        self.bg_slider.setValue(130)

    def _record_into(self, line_edit: QLineEdit) -> None:
        dlg = HotkeyRecorder(self, current=line_edit.text().strip())
        if dlg.exec() and dlg.captured:
            line_edit.setText(dlg.captured)

    def _refresh_hotkey_warning(self) -> None:
        issues = validate_hotkeys(
            self.hotkey_edit.text().strip(),
            self.delete_hotkey_edit.text().strip(),
        )
        if not issues:
            self.hotkey_warning.setVisible(False)
            self.hotkey_warning.setText("")
            return

        has_error = any(level == "error" for level, _ in issues)
        color = "#e85a5a" if has_error else "#e0a64a"
        prefix = "Error" if has_error else "Warning"
        # The label renders as rich-text (because of <br> / <b>), so escape the
        # dynamic message bodies — otherwise tokens like <plus> or
        # <ctrl>+<backspace> get stripped as unknown HTML tags.
        lines = [f"<b>{prefix}:</b> {html.escape(msg)}" for _, msg in issues]
        self.hotkey_warning.setText("<br>".join(lines))
        self.hotkey_warning.setStyleSheet(
            f"color: {color}; padding: 4px; border: 1px solid {color}; border-radius: 4px;"
        )
        self.hotkey_warning.setVisible(True)

    def _save(self) -> None:
        hotkey = self.hotkey_edit.text().strip() or "<alt>+z"
        delete_hk = self.delete_hotkey_edit.text().strip() or "<delete>"
        issues = validate_hotkeys(hotkey, delete_hk)
        if any(level == "error" for level, _ in issues):
            QMessageBox.warning(
                self,
                "Hotkey conflict",
                "Cannot save — fix the highlighted hotkey error first.",
            )
            return
        warns = [m for level, m in issues if level == "warn"]
        if warns:
            mb = QMessageBox(self)
            mb.setIcon(QMessageBox.Icon.Warning)
            mb.setWindowTitle("Hotkey warning")
            mb.setTextFormat(Qt.TextFormat.PlainText)
            mb.setText(
                "These hotkeys may conflict:\n\n  - "
                + "\n  - ".join(warns)
                + "\n\nSave anyway?"
            )
            mb.setStandardButtons(
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel
            )
            mb.setDefaultButton(QMessageBox.StandardButton.Save)
            if mb.exec() != QMessageBox.StandardButton.Save:
                return
        self.settings.set("hotkey", hotkey)
        self.settings.set("delete_hotkey", delete_hk)
        self.settings.set("delete_double_tap_ms", int(self.double_tap_spin.value()))
        self.settings.set("clipboard_enabled", bool(self.clipboard_check.isChecked()))
        self.settings.set(
            "notifications_enabled", bool(self.notifications_check.isChecked())
        )
        self.settings.set("play_ready_sound", bool(self.ready_sound_check.isChecked()))
        self.settings.set("play_stop_sound", bool(self.stop_sound_check.isChecked()))
        self.settings.set("sound_volume", round(self.sound_vol_slider.value() / 100.0, 2))
        self.settings.set("auto_enter_enabled", bool(self.auto_enter_check.isChecked()))
        self.settings.set("auto_enter_delay_ms", int(self.auto_enter_delay_spin.value()))
        self.settings.set("model_size", self.model_combo.currentText())
        self.settings.set("device", self.device_combo.currentText())
        self.settings.set("compute_type", self.compute_combo.currentText())
        self.settings.set("microphone_device", self.mic_combo.currentData())
        self.settings.set("language", self.lang_combo.currentText().strip() or "auto")
        self.settings.set("vad_silence_ms", int(self.silence_spin.value()))
        self.settings.set("vad_threshold", float(self.thresh_spin.value()))
        self.settings.set("type_delay_ms", int(self.delay_spin.value()))
        self.settings.set("output_method", str(self.output_method_combo.currentData()))
        self.settings.set("oscilloscope.enabled", bool(self.osc_check.isChecked()))
        self.settings.set("oscilloscope.width", int(self.osc_w_spin.value()))
        self.settings.set("oscilloscope.height", int(self.osc_h_spin.value()))
        self.settings.set("oscilloscope.color_active", self.color_active_btn.hex_value())
        self.settings.set("oscilloscope.color_idle", self.color_idle_btn.hex_value())
        self.settings.set("oscilloscope.opacity", round(self.opacity_slider.value() / 100.0, 2))
        self.settings.set("oscilloscope.background_alpha", int(self.bg_slider.value()))
        self.settings.set("oscilloscope.shape", str(self.shape_combo.currentData()))
        self.settings.set("oscilloscope.style", str(self.style_combo.currentData()))
        self.accept()
