"""Design tokens and the application stylesheet.

The single source of truth for colour, type, space and form across the UI.

Direction: **Engraver's Desk** — the interface is treated as a printed music
score and a proofing desk, not a screen. Warm ivory paper, letterpress ink,
hairline rules, and a single cinnabar accent for proof marks.

Rules this file exists to enforce:

* every colour, size and gap used anywhere in the UI comes from ``TOKENS``;
* radius is 0 everywhere (printed, not app-like);
* there are no shadows and no gradients;
* exactly one decorative accent (``ACCENT``); the two series colours
  (``A_INK`` / ``B_INK``) are data encoding, not decoration.
"""

from __future__ import annotations

import os
import string
import tempfile
from typing import Dict

# --------------------------------------------------------------------- tokens

TOKENS: Dict[str, str] = {
    # -- colour: paper -> ink, one accent ---------------------------------
    "PAPER": "#EFE9DC",      # page / window
    "SURFACE": "#F7F3E9",    # raised panel, input fill
    "SUNKEN": "#E6DECB",     # table zebra, wells, hover
    "INK": "#1C1813",        # primary text / engraving ink
    "INK2": "#574F44",       # secondary text
    "INK3": "#6E6558",       # metadata (>= 4.5:1 on SURFACE)
    "RULE": "#CFC6B2",       # hairline borders and rules
    "ACCENT": "#A9342A",     # cinnabar proof mark — < 10% of surface
    "ON_ACCENT": "#FBF7EE",  # text on accent
    "A_INK": "#2E4A72",      # series A (data, not decoration)
    "B_INK": "#9A5A2B",      # series B (data, not decoration)
    "DANGER": "#9B2C22",     # destructive / low confidence
    "WARN": "#7A4E0A",       # warnings (readable on PAPER)
    "PLATE": "#15120E",      # dark plate behind video frames
    "PLATE_INK": "#B7AC97",  # text on the dark plate (>= 4.5:1)
    # -- type: one ladder, in px ------------------------------------------
    "FS_XS": "11",
    "FS_SM": "12",
    "FS_BASE": "13",
    "FS_MD": "15",
    "FS_LG": "19",
    "FS_XL": "24",
    # -- space: one 4px-based scale ---------------------------------------
    "SP_1": "4",
    "SP_2": "6",
    "SP_3": "8",
    "SP_4": "12",
    "SP_5": "16",
    "SP_6": "24",
    "SP_7": "32",
    "SP_8": "48",
}

FONT_DISPLAY = "AppleMyungjo"       # Korean serif — titles, staves, running heads
FONT_TEXT = "Apple SD Gothic Neo"   # Korean humanist sans — body and controls
FONT_MONO = "Menlo"                 # figures and timecodes only

# Integer space scale, so Python layout code uses the same ladder as the QSS.
SPACE = {k[3:]: int(v) for k, v in TOKENS.items() if k.startswith("SP_")}

# Colours handed to the hand-painted waveform view.
WAVE = {
    "paper": TOKENS["SUNKEN"],
    "rule": TOKENS["RULE"],
    "staff": "#B9AF99",
    "a": TOKENS["A_INK"],
    "b": TOKENS["B_INK"],
    "low": TOKENS["DANGER"],
    "map": "#9A9080",
    "anchor": TOKENS["ACCENT"],
    "cut": TOKENS["ACCENT"],
    "playhead": TOKENS["INK"],
    "text": TOKENS["INK3"],
    "display_font": FONT_DISPLAY,
    "mono_font": FONT_MONO,
}


# ---------------------------------------------------------------- stylesheet

_QSS = string.Template(
    """
/* -- base ------------------------------------------------------------- */
QWidget {
    background: $PAPER;
    color: $INK;
    font-family: "$FONT_TEXT";
    font-size: ${FS_BASE}px;
}
QMainWindow, QDialog { background: $PAPER; }
QLabel { background: transparent; }
QToolTip {
    background: $INK; color: $PAPER; border: 1px solid $INK;
    padding: ${SP_1}px ${SP_2}px; font-size: ${FS_XS}px;
}

/* label roles */
QLabel[class="meta"]  { color: $INK3; font-size: ${FS_XS}px; }
QLabel[class="fig"]   { font-family: "$FONT_MONO"; font-size: ${FS_SM}px; color: $INK2; }
QLabel[class="strong"]{ color: $INK; font-family: "$FONT_DISPLAY"; font-size: ${FS_MD}px; }
QLabel[class="warn"]  { color: $WARN; }
QLabel[class="plate"] {
    background: $PLATE; color: $PLATE_INK; border: 1px solid $RULE;
    font-size: ${FS_XS}px;
}

/* -- running head: menu bar and tabs ---------------------------------- */
QMenuBar {
    background: $PAPER; color: $INK;
    border-bottom: 1px solid $RULE;
    padding: ${SP_1}px ${SP_2}px;
}
QMenuBar::item {
    padding: ${SP_1}px ${SP_3}px; background: transparent;
    font-family: "$FONT_DISPLAY"; font-size: ${FS_MD}px;
}
QMenuBar::item:selected { background: $SUNKEN; color: $INK; }
QMenuBar::item:pressed  { background: $ACCENT; color: $ON_ACCENT; }
QMenu {
    background: $SURFACE; border: 1px solid $RULE; padding: ${SP_1}px;
}
QMenu::item { padding: ${SP_2}px ${SP_6}px ${SP_2}px ${SP_4}px; }
QMenu::item:selected { background: $ACCENT; color: $ON_ACCENT; }
QMenu::separator { height: 1px; background: $RULE; margin: ${SP_1}px ${SP_3}px; }

QTabWidget::pane { border: none; border-top: 1px solid $RULE; background: $PAPER; }
QTabBar { qproperty-drawBase: 0; }
QTabBar::tab {
    background: transparent; color: $INK3;
    font-family: "$FONT_DISPLAY"; font-size: ${FS_LG}px;
    padding: ${SP_3}px ${SP_5}px;
    margin-right: ${SP_2}px;
    border: none; border-bottom: 2px solid transparent;
}
QTabBar::tab:hover    { color: $INK; border-bottom: 2px solid $RULE; }
QTabBar::tab:selected { color: $INK; border-bottom: 2px solid $ACCENT; }
QTabBar::tab:disabled { color: $RULE; }

/* -- section: group box ----------------------------------------------- */
QGroupBox {
    background: $SURFACE;
    border: 1px solid $RULE;
    margin-top: ${SP_4}px;
    padding: ${SP_4}px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 0px; padding: 0 ${SP_3}px 0 0;
    background: transparent; color: $INK;
    font-family: "$FONT_DISPLAY"; font-size: ${FS_MD}px;
}

/* -- controls: five states each --------------------------------------- */
QPushButton {
    background: $SURFACE; color: $INK;
    border: 1px solid $INK;
    padding: ${SP_2}px ${SP_4}px;
    min-height: 18px;
}
QPushButton:hover    { background: $SUNKEN; }
QPushButton:pressed  { background: $INK; color: $PAPER; }
QPushButton:focus    { border: 2px solid $ACCENT; padding: ${SP_2}px ${SP_4}px; }
QPushButton:disabled { background: $SURFACE; color: $INK3; border-color: $RULE; }

QPushButton[class="primary"] {
    background: $INK; color: $PAPER; border: 1px solid $INK;
    padding: ${SP_3}px ${SP_5}px; font-size: ${FS_MD}px;
}
QPushButton[class="primary"]:hover    { background: $ACCENT; border-color: $ACCENT; color: $ON_ACCENT; }
QPushButton[class="primary"]:pressed  { background: $DANGER; border-color: $DANGER; color: $ON_ACCENT; }
QPushButton[class="primary"]:focus    { border: 2px solid $ACCENT; padding: ${SP_3}px ${SP_5}px; }
QPushButton[class="primary"]:disabled { background: $SUNKEN; color: $INK3; border-color: $RULE; }

QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background: $SURFACE; color: $INK;
    border: 1px solid $RULE;
    padding: ${SP_1}px ${SP_3}px;
    selection-background-color: $ACCENT; selection-color: $ON_ACCENT;
}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QComboBox:focus,
QComboBox:on {
    border: 1px solid $ACCENT;
}
QLineEdit:disabled, QPlainTextEdit:disabled, QSpinBox:disabled,
QDoubleSpinBox:disabled, QComboBox:disabled {
    background: $SUNKEN; color: $INK3;
}
QSpinBox, QDoubleSpinBox, QLineEdit[class="fig"] { font-family: "$FONT_MONO"; }
QComboBox::drop-down { border-left: 1px solid $RULE; width: 22px; }
QComboBox QAbstractItemView {
    background: $SURFACE; border: 1px solid $RULE; outline: none;
    selection-background-color: $ACCENT; selection-color: $ON_ACCENT;
}

QCheckBox { spacing: ${SP_3}px; background: transparent; }
QCheckBox::indicator {
    width: 15px; height: 15px;
    border: 1px solid $INK; background: $SURFACE;
}
QCheckBox::indicator:hover   { border-color: $ACCENT; }
QCheckBox::indicator:focus   { border: 2px solid $ACCENT; }
QCheckBox::indicator:disabled { border-color: $RULE; background: $SUNKEN; }
QCheckBox::indicator:checked          { image: url("$CHECK_ON"); border: 1px solid $ACCENT; }
QCheckBox::indicator:checked:disabled { image: url("$CHECK_OFF"); border: 1px solid $RULE; }

QSlider::groove:horizontal { height: 3px; background: $RULE; }
QSlider::sub-page:horizontal { background: $ACCENT; height: 3px; }
QSlider::add-page:horizontal { background: $RULE; height: 3px; }
QSlider::handle:horizontal {
    background: $INK; width: 11px; height: 11px;
    margin: -5px 0; border-radius: 0;
}
QSlider::handle:horizontal:hover { background: $ACCENT; }
QSlider::handle:horizontal:focus { background: $ACCENT; }
QSlider::handle:horizontal:disabled { background: $INK3; }

QProgressBar {
    border: 1px solid $RULE; background: $SURFACE;
    text-align: center; color: $INK;
    font-family: "$FONT_MONO"; font-size: ${FS_XS}px;
    height: 15px;
}
QProgressBar::chunk { background: $ACCENT; }

/* -- data: tables ------------------------------------------------------ */
QTableWidget, QTableView {
    background: $SURFACE; alternate-background-color: $SUNKEN;
    gridline-color: $RULE; border: 1px solid $RULE;
    font-family: "$FONT_MONO"; font-size: ${FS_SM}px;
    selection-background-color: $ACCENT; selection-color: $ON_ACCENT;
    outline: none;
}
QTableView::item:focus { border: 1px solid $ACCENT; }
QHeaderView::section {
    background: $PAPER; color: $INK2;
    border: none; border-bottom: 1px solid $INK; border-right: 1px solid $RULE;
    padding: ${SP_2}px ${SP_3}px;
    font-family: "$FONT_TEXT"; font-size: ${FS_SM}px;
}
QTableCornerButton::section { background: $PAPER; border: none; border-bottom: 1px solid $INK; }

/* -- chrome ------------------------------------------------------------ */
QSplitter::handle { background: $RULE; }
QSplitter::handle:horizontal { width: 1px; }
QSplitter::handle:vertical { height: 1px; }
QStatusBar { background: $PAPER; color: $INK2; border-top: 1px solid $RULE; }
QStatusBar::item { border: none; }

QScrollBar:vertical { background: $PAPER; width: 12px; margin: 0; border-left: 1px solid $RULE; }
QScrollBar:horizontal { background: $PAPER; height: 12px; margin: 0; border-top: 1px solid $RULE; }
QScrollBar::handle:vertical   { background: $INK3; min-height: 28px; }
QScrollBar::handle:horizontal { background: $INK3; min-width: 28px; }
QScrollBar::handle:hover { background: $ACCENT; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; background: none; border: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }
"""
)


# --------------------------------------------------------- generated assets

_ASSET_DIR = os.path.join(tempfile.gettempdir(), "pianosync-assets")


def _render_check(fill: str, mark: str, path: str, ratio: int = 2) -> None:
    """Render a proof-mark checkbox: cinnabar plate with an ivory check.

    A drawn mark (not just a fill colour) so the checked state never relies on
    colour alone.
    """
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QPainter, QPen, QPixmap

    size = 15 * ratio
    pix = QPixmap(size, size)
    pix.setDevicePixelRatio(ratio)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(fill))
    painter.drawRect(0, 0, size, size)
    pen = QPen(QColor(mark))
    pen.setWidthF(2.0 * ratio)
    pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
    painter.setPen(pen)
    unit = size / 15.0
    painter.drawPolyline(
        [
            QPointF(3.5 * unit, 7.8 * unit),
            QPointF(6.2 * unit, 10.6 * unit),
            QPointF(11.6 * unit, 4.4 * unit),
        ]
    )
    painter.end()
    pix.save(path)


def _ensure_assets() -> Dict[str, str]:
    os.makedirs(_ASSET_DIR, exist_ok=True)
    on = os.path.join(_ASSET_DIR, "check_on.png")
    off = os.path.join(_ASSET_DIR, "check_off.png")
    _render_check(TOKENS["ACCENT"], TOKENS["ON_ACCENT"], on)
    _render_check(TOKENS["SUNKEN"], TOKENS["INK3"], off)
    return {"CHECK_ON": on.replace("\\", "/"), "CHECK_OFF": off.replace("\\", "/")}


def build_stylesheet() -> str:
    """Return the full Qt style sheet with every token substituted."""
    values = dict(TOKENS)
    values["FONT_TEXT"] = FONT_TEXT
    values["FONT_MONO"] = FONT_MONO
    values["FONT_DISPLAY"] = FONT_DISPLAY
    values.update(_ensure_assets())
    return _QSS.substitute(values)


def apply_theme(app) -> None:
    """Apply fonts, palette, style and style sheet to a QApplication."""
    from PySide6.QtGui import QColor, QFont, QPalette
    from PySide6.QtWidgets import QStyleFactory

    fusion = QStyleFactory.create("Fusion")
    if fusion is not None:
        app.setStyle(fusion)

    font = QFont(FONT_TEXT)
    font.setPixelSize(int(TOKENS["FS_BASE"]))
    app.setFont(font)

    t = TOKENS
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(t["PAPER"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(t["INK"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(t["SURFACE"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(t["SUNKEN"]))
    palette.setColor(QPalette.ColorRole.Text, QColor(t["INK"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(t["SURFACE"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(t["INK"]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(t["ACCENT"]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(t["ON_ACCENT"]))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(t["INK"]))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(t["PAPER"]))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(t["INK3"]))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(t["INK3"]))

    app.setPalette(palette)
    app.setStyleSheet(build_stylesheet())
