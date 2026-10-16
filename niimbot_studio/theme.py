"""App theme: dark Fluent-style UI, accent colors, vector icons, optional brand pack."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QIcon, QPainter, QPainterPath, QPalette, QPen,
                           QPixmap)
from PySide6.QtWidgets import QApplication

RESOURCES = Path(__file__).parent / "resources"

ACCENTS = {
    "blue": {"accent": "#177BC0", "accent_hover": "#2A8FD6", "accent_press": "#13679F",
             "accent_dark": "#193B6A", "accent_light": "#84CFEE"},
    "red": {"accent": "#C02026", "accent_hover": "#D7353B", "accent_press": "#A41C23",
            "accent_dark": "#77111B", "accent_light": "#F07A7E"},
}

BASE = {
    "bg": "#16181D",          # window
    "surface": "#1E2128",     # panels
    "surface2": "#262A33",    # inputs, cards
    "surface3": "#2F3440",    # hover
    "border": "#343A46",
    "canvas": "#0F1115",      # area around the label
    "text": "#E6E9EF",
    "text_dim": "#9AA3B2",
    "text_faint": "#646C7A",
    "ok": "#3FB97F",
    "warn": "#E5A93B",
    "error": "#E5534B",
}

_current = {**BASE, **ACCENTS["blue"], "name": "blue"}


def colors() -> dict:
    return _current


def c(name: str) -> QColor:
    return QColor(_current[name])


_brand = None


def brand():
    """The active brand pack (an empty Brand when running unbranded)."""
    global _brand
    if _brand is None:
        from . import brand as brand_mod
        _brand = brand_mod.load()
    return _brand


def accent_name(accent: str) -> str:
    return brand().accent_names.get(accent) or {"blue": "Blue", "red": "Red"}.get(accent, accent.title())


def logo_bytes() -> bytes | None:
    """The brand logo for the current accent, or None when unbranded."""
    return brand().logo(_current["name"])


def mark_pixmap(height: int) -> QPixmap:
    """Brand logo if there is one, else the built-in app icon, scaled to `height`."""
    pm = QPixmap()
    data = logo_bytes()
    if not (data and pm.loadFromData(data)):
        pm = QPixmap(str(RESOURCES / f"icon-{_current['name']}.png"))
    return pm.scaledToHeight(height, Qt.SmoothTransformation)


def app_icon() -> QIcon:
    data = brand().icon(_current["name"])
    if data:
        pm = QPixmap()
        if pm.loadFromData(data):
            return QIcon(pm)
    return QIcon(str(RESOURCES / f"icon-{_current['name']}.ico"))


def apply(app: QApplication, accent: str = "blue"):
    _current.clear()
    _current.update({**BASE, **ACCENTS.get(accent, ACCENTS["blue"]), "name": accent})
    _icon_cache.clear()
    k = _current
    app.setStyle("Fusion")
    font = QFont("Segoe UI", 9)
    font.setHintingPreference(QFont.PreferNoHinting)
    app.setFont(font)

    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(k["bg"]))
    pal.setColor(QPalette.WindowText, QColor(k["text"]))
    pal.setColor(QPalette.Base, QColor(k["surface2"]))
    pal.setColor(QPalette.AlternateBase, QColor(k["surface"]))
    pal.setColor(QPalette.Text, QColor(k["text"]))
    pal.setColor(QPalette.Button, QColor(k["surface2"]))
    pal.setColor(QPalette.ButtonText, QColor(k["text"]))
    pal.setColor(QPalette.Highlight, QColor(k["accent"]))
    pal.setColor(QPalette.HighlightedText, QColor("#FFFFFF"))
    pal.setColor(QPalette.ToolTipBase, QColor(k["surface3"]))
    pal.setColor(QPalette.ToolTipText, QColor(k["text"]))
    pal.setColor(QPalette.PlaceholderText, QColor(k["text_faint"]))
    pal.setColor(QPalette.Link, QColor(k["accent_light"]))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor(k["text_faint"]))
    app.setPalette(pal)
    app.setStyleSheet(STYLESHEET.format(**k).replace("CHECKMARK", checkmark_file()).replace("CHEVRON", chevron_file(k["text_dim"])))
    app.setWindowIcon(app_icon())


STYLESHEET = """
* {{ outline: 0; }}
QMainWindow, QDialog {{ background: {bg}; }}
QToolTip {{ background: {surface3}; color: {text}; border: 1px solid {border}; padding: 4px 8px; border-radius: 4px; }}

QMenuBar {{ background: {bg}; color: {text_dim}; padding: 2px 4px; }}
QMenuBar::item {{ padding: 5px 10px; border-radius: 4px; background: transparent; }}
QMenuBar::item:selected {{ background: {surface3}; color: {text}; }}
QMenu {{ background: {surface}; border: 1px solid {border}; padding: 4px; border-radius: 6px; }}
QMenu::item {{ padding: 6px 28px 6px 12px; border-radius: 4px; color: {text}; }}
QMenu::item:selected {{ background: {accent}; color: white; }}
QMenu::item:disabled {{ color: {text_faint}; }}
QMenu::separator {{ height: 1px; background: {border}; margin: 4px 6px; }}
QMenu::icon {{ padding-left: 8px; }}

QToolBar {{ background: {bg}; border: none; border-bottom: 1px solid {border}; padding: 4px 8px; spacing: 2px; }}
QToolBar::separator {{ background: {border}; width: 1px; margin: 6px 6px; }}
QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 6px; padding: 5px; color: {text}; }}
QToolButton:hover {{ background: {surface3}; }}
QToolButton:pressed {{ background: {surface2}; }}
QToolButton:checked {{ background: {accent_dark}; border-color: {accent}; }}
QToolButton:disabled {{ color: {text_faint}; }}
QToolButton[popupMode="1"] {{ padding-right: 14px; }}
QToolButton#PrintButton {{ background: {accent}; color: white; font-weight: 600; padding: 6px 16px; }}
QToolButton#PrintButton:hover {{ background: {accent_hover}; }}
QToolButton#PrintButton:pressed {{ background: {accent_press}; }}
QToolButton#PrintButton:disabled {{ background: {surface3}; color: {text_faint}; }}

QDockWidget {{ color: {text_dim}; font-weight: 600; titlebar-close-icon: none; }}
QDockWidget::title {{ background: {surface}; padding: 8px 12px; border-bottom: 1px solid {border}; text-align: left; }}
QWidget#Panel {{ background: {surface}; }}

QLabel {{ color: {text}; background: transparent; }}
QLabel[role="dim"] {{ color: {text_dim}; }}
QLabel[role="faint"] {{ color: {text_faint}; }}
QLabel[role="section"] {{ color: {text_dim}; font-size: 8pt; font-weight: 700; letter-spacing: 1px; padding-top: 6px; }}
QLabel[role="title"] {{ font-size: 13pt; font-weight: 600; }}
QLabel[role="big"] {{ font-size: 18pt; font-weight: 600; }}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QFontComboBox {{
    background: {surface2}; color: {text}; border: 1px solid {border}; border-radius: 6px;
    padding: 4px 8px; selection-background-color: {accent}; min-height: 20px; }}
QLineEdit:hover, QPlainTextEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover, QFontComboBox:hover {{ border-color: #46505F; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QFontComboBox:focus {{
    border-color: {accent}; }}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{ color: {text_faint}; background: {surface}; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ width: 0; border: none; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{ image: url(CHEVRON); width: 10px; height: 10px; margin-right: 8px; }}
QComboBox QAbstractItemView {{ background: {surface}; border: 1px solid {border}; selection-background-color: {accent}; padding: 4px; outline: 0; }}

QPushButton {{ background: {surface2}; color: {text}; border: 1px solid {border}; border-radius: 6px; padding: 6px 14px; min-height: 18px; }}
QPushButton:hover {{ background: {surface3}; }}
QPushButton:pressed {{ background: {surface}; }}
QPushButton:disabled {{ color: {text_faint}; }}
QPushButton:checked {{ background: {accent_dark}; border-color: {accent}; }}
QPushButton[primary="true"] {{ background: {accent}; border-color: {accent}; color: white; font-weight: 600; }}
QPushButton[primary="true"]:hover {{ background: {accent_hover}; }}
QPushButton[primary="true"]:pressed {{ background: {accent_press}; }}
QPushButton[primary="true"]:disabled {{ background: {surface3}; border-color: {border}; color: {text_faint}; }}
QPushButton[flat="true"] {{ background: transparent; border-color: transparent; }}
QPushButton[flat="true"]:hover {{ background: {surface3}; }}
QPushButton[seg="true"] {{ border-radius: 0; padding: 5px 8px; }}

QCheckBox {{ color: {text}; spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid #4A5361; background: {surface2}; }}
QCheckBox::indicator:hover {{ border-color: {accent}; }}
QCheckBox::indicator:checked {{ background: {accent}; border-color: {accent}; image: url(CHECKMARK); }}

QSlider::groove:horizontal {{ height: 4px; background: {surface3}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {accent}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: white; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}

QProgressBar {{ background: {surface3}; border: none; border-radius: 3px; height: 6px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {accent}; border-radius: 3px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {surface3}; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #4A5361; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {surface3}; border-radius: 3px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QListWidget, QTreeWidget, QListView {{ background: {surface}; border: none; color: {text}; outline: 0; }}
QDialog QListWidget {{ background: {surface2}; border: 1px solid {border}; border-radius: 6px; padding: 4px; }}
QListWidget::item {{ border-radius: 6px; padding: 4px; }}
QListWidget::item:hover {{ background: {surface3}; }}
QListWidget::item:selected {{ background: {accent_dark}; color: {text}; }}

QStatusBar {{ background: {bg}; color: {text_dim}; border-top: 1px solid {border}; }}
QStatusBar QLabel {{ color: {text_dim}; padding: 0 8px; }}
QStatusBar::item {{ border: none; }}

QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {text_dim}; padding: 6px 12px; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {text}; border-bottom-color: {accent}; }}

QFrame#Card {{ background: {surface2}; border: 1px solid {border}; border-radius: 8px; }}
QFrame#Banner {{ background: #2B2414; border: 1px solid #5C4A1C; border-radius: 6px; }}
QFrame#UpdateBanner {{ background: {surface2}; border: 1px solid {accent}; border-radius: 6px; }}
QFrame#Divider {{ background: {border}; max-height: 1px; min-height: 1px; }}
QSplitter::handle {{ background: {border}; }}
QMainWindow::separator {{ background: {border}; width: 1px; height: 1px; }}
QMainWindow::separator:hover {{ background: {accent}; }}
"""

# ------------------------------------------------------------------ icons
MDL2 = {
    "new": 0xE8A5, "open": 0xE8E5, "save": 0xE74E, "saveas": 0xE792, "undo": 0xE7A7,
    "redo": 0xE7A6, "print": 0xE749, "text": 0xE8D2, "image": 0xE91B, "rect": 0xE739,
    "ellipse": 0xEA3A, "line": 0xE738, "qr": 0xED14, "zoom_in": 0xE8A3, "zoom_out": 0xE71F,
    "fit": 0xE9A6, "grid": 0xF0E2, "bluetooth": 0xE702, "settings": 0xE713, "delete": 0xE74D,
    "copy": 0xE8C8, "paste": 0xE77F, "lock": 0xE72E, "unlock": 0xE785, "rotate": 0xE7AD,
    "library": 0xE8F1, "refresh": 0xE72C, "export": 0xEDE1, "ruler": 0xED5E, "duplicate": 0xF413,
    "bold": 0xE8DD, "italic": 0xE8DB, "underline": 0xE8DC, "add": 0xE710, "check": 0xE73E,
    "warn": 0xE7BA, "info": 0xE946, "layers": 0xE81E, "cut": 0xE8C6, "folder": 0xE838,
    "tag": 0xE8EC, "close": 0xE711, "more": 0xE712, "link": 0xE71B,
    "text_left": 0xE8E4, "text_center": 0xE8E3, "text_right": 0xE8E2, "search": 0xE721,
}

_icon_cache: dict = {}


def icon(name: str, color: str | None = None, size: int = 20) -> QIcon:
    key = (name, color, size)
    if key in _icon_cache:
        return _icon_cache[key]
    ic = QIcon()
    for scale in (1, 2):
        ic.addPixmap(_render_icon(name, QColor(color or _current["text"]), size, scale), QIcon.Normal)
        ic.addPixmap(_render_icon(name, QColor(_current["text_faint"]), size, scale), QIcon.Disabled)
    _icon_cache[key] = ic
    return ic


def _render_icon(name, color, size, scale) -> QPixmap:
    px = QPixmap(size * scale, size * scale)
    px.setDevicePixelRatio(scale)
    px.fill(Qt.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)
    if name in MDL2:
        f = QFont("Segoe MDL2 Assets")
        f.setPixelSize(round(size * 0.8))
        p.setFont(f)
        p.setPen(color)
        p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, chr(MDL2[name]))
    else:
        _CUSTOM[name](p, color, size)
    p.end()
    return px


def _pen(color, w=1.4):
    pen = QPen(color, w)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


def _align_icon(axis, where):
    """Two bars aligned to a guide line. axis 'h' = horizontal alignment (left/center/right)."""
    def draw(p, col, s):
        u = s / 20
        p.setPen(_pen(col, 1.4))
        bars = [(11, 4.5), (7, 11.5)]  # (length, offset across)
        if axis == "h":
            gx = {"start": 3, "center": 10, "end": 17}[where] * u
            p.drawLine(QPointF(gx, 2 * u), QPointF(gx, 18 * u))
            for length, off in bars:
                L = length * u
                x = {"start": gx + 1.5 * u, "center": gx - L / 2, "end": gx - 1.5 * u - L}[where]
                p.fillRect(QRectF(x, off * u, L, 4 * u), col)
        else:
            gy = {"start": 3, "center": 10, "end": 17}[where] * u
            p.drawLine(QPointF(2 * u, gy), QPointF(18 * u, gy))
            for length, off in bars:
                L = length * u
                y = {"start": gy + 1.5 * u, "center": gy - L / 2, "end": gy - 1.5 * u - L}[where]
                p.fillRect(QRectF(off * u, y, 4 * u, L), col)
    return draw


def _center_on_label(axis):
    def draw(p, col, s):
        u = s / 20
        p.setPen(_pen(col, 1.2))
        p.drawRoundedRect(QRectF(2 * u, 4 * u, 16 * u, 12 * u), 2 * u, 2 * u)
        dash = QPen(col, 1.2, Qt.DotLine)
        p.setPen(dash)
        if axis == "h":
            p.drawLine(QPointF(10 * u, 4 * u), QPointF(10 * u, 16 * u))
            p.fillRect(QRectF(7 * u, 8 * u, 6 * u, 4 * u), col)
        else:
            p.drawLine(QPointF(2 * u, 10 * u), QPointF(18 * u, 10 * u))
            p.fillRect(QRectF(6 * u, 8 * u, 8 * u, 4 * u), col)
    return draw


def _barcode(p, col, s):
    u = s / 20
    x = 3.0
    for w in (1, 2, 1, 1, 3, 1, 2, 1, 1, 2):
        p.fillRect(QRectF(x * u, 4 * u, w * 0.75 * u, 12 * u), col)
        x += w * 0.75 + 0.9


def _z(front):
    def draw(p, col, s):
        u = s / 20
        back = QRectF(3 * u, 3 * u, 10 * u, 10 * u)
        fore = QRectF(7 * u, 7 * u, 10 * u, 10 * u)
        p.setBrush(Qt.NoBrush)
        if front:
            p.setPen(_pen(col, 1.2)); p.drawRect(back)
            p.fillRect(fore, col)
        else:
            p.fillRect(back, col)
            p.setCompositionMode(QPainter.CompositionMode_Clear)
            p.fillRect(fore.adjusted(-1.5 * u, -1.5 * u, 0, 0), Qt.transparent)
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
            p.setPen(_pen(col, 1.2)); p.drawRect(fore)
    return draw


def _snap(p, col, s):
    u = s / 20
    p.setPen(_pen(col, 1.4))
    p.drawLine(QPointF(10 * u, 2 * u), QPointF(10 * u, 18 * u))
    p.drawLine(QPointF(2 * u, 10 * u), QPointF(18 * u, 10 * u))
    p.setBrush(Qt.NoBrush)
    p.drawRect(QRectF(5 * u, 5 * u, 10 * u, 10 * u))
    p.setBrush(col)
    p.drawEllipse(QPointF(10 * u, 10 * u), 1.8 * u, 1.8 * u)


def _pixels(p, col, s):
    u = s / 20
    pattern = ["1010", "0101", "1011", "0111"]
    for r, row in enumerate(pattern):
        for k, bit in enumerate(row):
            if bit == "1":
                p.fillRect(QRectF((3 + k * 3.6) * u, (3 + r * 3.6) * u, 3.2 * u, 3.2 * u), col)


def _battery(level):
    def draw(p, col, s):
        u = s / 20
        p.setPen(_pen(col, 1.2))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(2 * u, 6 * u, 14 * u, 8 * u), 1.5 * u, 1.5 * u)
        p.fillRect(QRectF(16.5 * u, 8.5 * u, 1.5 * u, 3 * u), col)
        w = 11 * u * max(0, min(4, level)) / 4
        if w:
            p.fillRect(QRectF(3.5 * u, 7.5 * u, w, 5 * u), col)
    return draw


def _distribute(axis):
    def draw(p, col, s):
        u = s / 20
        p.setPen(_pen(col, 1.2))
        if axis == "h":
            p.drawLine(QPointF(2 * u, 3 * u), QPointF(2 * u, 17 * u))
            p.drawLine(QPointF(18 * u, 3 * u), QPointF(18 * u, 17 * u))
            p.fillRect(QRectF(7.5 * u, 6 * u, 5 * u, 8 * u), col)
        else:
            p.drawLine(QPointF(3 * u, 2 * u), QPointF(17 * u, 2 * u))
            p.drawLine(QPointF(3 * u, 18 * u), QPointF(17 * u, 18 * u))
            p.fillRect(QRectF(6 * u, 7.5 * u, 8 * u, 5 * u), col)
    return draw


def _invert(p, col, s):
    u = s / 20
    path = QPainterPath()
    path.addEllipse(QRectF(3 * u, 3 * u, 14 * u, 14 * u))
    p.setPen(_pen(col, 1.3))
    p.drawPath(path)
    half = QPainterPath()
    half.moveTo(10 * u, 3 * u)
    half.arcTo(QRectF(3 * u, 3 * u, 14 * u, 14 * u), 90, -180)
    half.closeSubpath()
    p.fillPath(half, col)


def _flip(axis):
    def draw(p, col, s):
        u = s / 20
        p.setPen(_pen(col, 1.2))
        if axis == "h":
            p.drawLine(QPointF(10 * u, 2 * u), QPointF(10 * u, 18 * u))
            left = QPainterPath(); left.moveTo(8 * u, 5 * u); left.lineTo(8 * u, 15 * u); left.lineTo(2 * u, 15 * u); left.closeSubpath()
            p.fillPath(left, col)
            right = QPainterPath(); right.moveTo(12 * u, 5 * u); right.lineTo(12 * u, 15 * u); right.lineTo(18 * u, 15 * u); right.closeSubpath()
            p.drawPath(right)
        else:
            p.drawLine(QPointF(2 * u, 10 * u), QPointF(18 * u, 10 * u))
            top = QPainterPath(); top.moveTo(5 * u, 8 * u); top.lineTo(15 * u, 8 * u); top.lineTo(15 * u, 2 * u); top.closeSubpath()
            p.fillPath(top, col)
            bot = QPainterPath(); bot.moveTo(5 * u, 12 * u); bot.lineTo(15 * u, 12 * u); bot.lineTo(15 * u, 18 * u); bot.closeSubpath()
            p.drawPath(bot)
    return draw


def _logo(p, col, s):
    data = logo_bytes()
    pm = QPixmap()
    if data and pm.loadFromData(data):
        pm = pm.scaled(s, s, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        p.drawPixmap((s - pm.width()) // 2, (s - pm.height()) // 2, pm)


_CUSTOM = {
    "align_left": _align_icon("h", "start"), "align_hcenter": _align_icon("h", "center"),
    "align_right": _align_icon("h", "end"), "align_top": _align_icon("v", "start"),
    "align_vcenter": _align_icon("v", "center"), "align_bottom": _align_icon("v", "end"),
    "center_h": _center_on_label("h"), "center_v": _center_on_label("v"),
    "barcode": _barcode, "front": _z(True), "back": _z(False), "snap": _snap,
    "pixels": _pixels, "invert": _invert, "flip_h": _flip("h"), "flip_v": _flip("v"),
    "distribute_h": _distribute("h"), "distribute_v": _distribute("v"), "logo": _logo,
    **{f"battery{i}": _battery(i) for i in range(5)},
}


def checkmark_file() -> str:
    """A white checkmark PNG used by the checkbox stylesheet."""
    path = Path.home() / "AppData" / "Local" / "Temp" / "niimbot_studio_check.png"
    if not path.exists():
        px = QPixmap(32, 32)
        px.fill(Qt.transparent)
        p = QPainter(px)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(_pen(QColor("white"), 4))
        p.drawPolyline([QPointF(7, 16.5), QPointF(13.5, 23), QPointF(25, 9.5)])
        p.end()
        px.save(str(path))
    return path.as_posix()


def chevron_file(color: str) -> str:
    """A small down chevron PNG used by the combo box stylesheet."""
    path = Path.home() / "AppData" / "Local" / "Temp" / f"niimbot_studio_chevron_{color.strip('#')}.png"
    if not path.exists():
        px = QPixmap(20, 20)
        px.fill(Qt.transparent)
        p = QPainter(px)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(_pen(QColor(color), 2.4))
        p.drawPolyline([QPointF(4, 7.5), QPointF(10, 13.5), QPointF(16, 7.5)])
        p.end()
        px.save(str(path))
    return path.as_posix()
