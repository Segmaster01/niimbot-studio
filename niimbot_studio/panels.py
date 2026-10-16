"""Side panels: properties inspector, layers, printer status, output preview, design library."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPainterPath, QPixmap, QTransform
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox,
                               QDoubleSpinBox, QFontComboBox, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
                               QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSlider,
                               QSpinBox, QToolButton, QVBoxLayout, QWidget)

from . import theme
from .config import FILE_EXT, PRESETS, LabelSize
from .document import Document, LabelScene, mono_to_qimage, render_label
from .items import BarcodeItem, ImageItem, LabelItem, ShapeItem, TextItem, item_from_dict

MM = 8.0


def section(text: str) -> QLabel:
    lab = QLabel(text.upper())
    lab.setProperty("role", "section")
    return lab


def dim(text: str, role="dim") -> QLabel:
    lab = QLabel(text)
    lab.setProperty("role", role)
    lab.setWordWrap(True)
    return lab


def mm_spin(lo=-500.0, hi=500.0, step=0.125, decimals=2, suffix=" mm") -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setDecimals(decimals)
    s.setSuffix(suffix)
    s.setKeyboardTracking(False)
    s.setAlignment(Qt.AlignRight)
    return s


def tool_button(icon_name: str, tip: str, checkable=False) -> QToolButton:
    b = QToolButton()
    b.setIcon(theme.icon(icon_name))
    b.setIconSize(QSize(18, 18))
    b.setToolTip(tip)
    b.setCheckable(checkable)
    b.setAutoRaise(True)
    return b


def labelled(text: str, w: QWidget) -> QWidget:
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(3)
    lay.addWidget(dim(text, "faint"))
    lay.addWidget(w)
    return box


def slider(lo, hi) -> QSlider:
    s = QSlider(Qt.Horizontal)
    s.setRange(lo, hi)
    return s


def divider() -> QFrame:
    f = QFrame()
    f.setObjectName("Divider")
    return f


# =============================================================================== properties
class PropertiesPanel(QScrollArea):
    """Inspector for the current selection (or the label itself when nothing is selected)."""

    edited = Signal(bool)        # commit_now
    label_size_requested = Signal()
    replace_image_requested = Signal(object)

    def __init__(self, doc: Document):
        super().__init__()
        self.doc = doc
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.items: list[LabelItem] = []
        self._building = False
        self._loaded_label: LabelSize | None = None
        self.body = QWidget()
        self.body.setObjectName("Panel")
        self.setWidget(self.body)
        self.lay = QVBoxLayout(self.body)
        self.lay.setContentsMargins(16, 12, 16, 16)
        self.lay.setSpacing(8)
        self._refreshers = []
        self.rebuild()

    def set_loaded_label(self, label: LabelSize | None):
        self._loaded_label = label
        if not self.items:
            self.rebuild()

    # ------------------------------------------------------------------ building
    def _clear(self):
        self._refreshers = []
        while self.lay.count():
            w = self.lay.takeAt(0)
            if w.widget():
                widget = w.widget()
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
            elif w.layout():
                _delete_layout(w.layout())

    def rebuild(self):
        self._building = True
        self._clear()
        self.items = self.doc.scene.selected_label_items()
        if not self.items:
            self._build_label()
        elif len(self.items) > 1:
            self._build_multi()
        else:
            self._build_single(self.items[0])
        self.lay.addStretch(1)
        self._building = False
        self.refresh()

    def refresh(self):
        self._building = True
        for fn in self._refreshers:
            fn()
        self._building = False

    def _changed(self, commit_now=False):
        if self._building:
            return
        for it in self.items:
            it.invalidate()
        self.edited.emit(commit_now)

    def _bind(self, widget, getter, setter, signal_name=None, commit_now=False):
        """Two-way bind a widget to a property of every selected item."""
        def refresh():
            v = getter(self.items[0])
            if isinstance(widget, (QDoubleSpinBox, QSpinBox, QSlider)):
                if widget.value() != v:
                    widget.setValue(v)
            elif isinstance(widget, QCheckBox):
                widget.setChecked(bool(v))
            elif isinstance(widget, QFontComboBox):
                widget.setCurrentFont(QFont(v))
            elif isinstance(widget, QComboBox):
                idx = widget.findData(v)
                widget.setCurrentIndex(idx if idx >= 0 else 0)
            elif isinstance(widget, QLineEdit):
                if widget.text() != v:
                    widget.setText(v)
            elif isinstance(widget, QPlainTextEdit):
                if widget.toPlainText() != v:
                    widget.setPlainText(v)
        self._refreshers.append(refresh)

        def apply(*_):
            if self._building:
                return
            if isinstance(widget, (QDoubleSpinBox, QSpinBox, QSlider)):
                v = widget.value()
            elif isinstance(widget, QCheckBox):
                v = widget.isChecked()
            elif isinstance(widget, QFontComboBox):
                v = widget.currentFont().family()
            elif isinstance(widget, QComboBox):
                v = widget.currentData()
            elif isinstance(widget, QLineEdit):
                v = widget.text()
            else:
                v = widget.toPlainText()
            for it in self.items:
                setter(it, v)
            self._changed(commit_now)

        if signal_name:
            getattr(widget, signal_name).connect(apply)
        elif isinstance(widget, (QDoubleSpinBox, QSpinBox, QSlider)):
            widget.valueChanged.connect(apply)
        elif isinstance(widget, QCheckBox):
            widget.toggled.connect(apply)
        elif isinstance(widget, QFontComboBox):
            widget.currentFontChanged.connect(apply)
        elif isinstance(widget, QComboBox):
            widget.currentIndexChanged.connect(apply)
        elif isinstance(widget, QLineEdit):
            widget.textChanged.connect(apply)
        elif isinstance(widget, QPlainTextEdit):
            widget.textChanged.connect(apply)
        return widget

    def _segmented(self, options, getter, setter):
        """Row of checkable icon buttons acting as a radio group."""
        row = QHBoxLayout()
        row.setSpacing(2)
        group = QButtonGroup(self.body)
        buttons = {}
        for value, icon_name, tip in options:
            b = tool_button(icon_name, tip, checkable=True)
            group.addButton(b)
            buttons[value] = b
            row.addWidget(b)
            b.clicked.connect(lambda _=False, v=value: (
                [setter(it, v) for it in self.items], self._changed(True)))
        row.addStretch(1)

        def refresh():
            b = buttons.get(getter(self.items[0]))
            if b:
                b.setChecked(True)
        self._refreshers.append(refresh)
        return row

    def _toggle(self, icon_name, tip, getter, setter):
        b = tool_button(icon_name, tip, checkable=True)
        b.toggled.connect(lambda on: None if self._building else (
            [setter(it, on) for it in self.items], self._changed(True)))
        self._refreshers.append(lambda: b.setChecked(bool(getter(self.items[0]))))
        return b

    # ------------------------------------------------------------------ label (nothing selected)
    def _build_label(self):
        title = QLabel("Label")
        title.setProperty("role", "title")
        self.lay.addWidget(title)
        self.lay.addWidget(section("Label size"))
        lab = self.doc.label
        size_row = QHBoxLayout()
        size_lbl = QLabel(f"<b>{lab.name}</b>")
        size_row.addWidget(size_lbl)
        size_row.addStretch(1)
        change = QPushButton("Change…")
        change.clicked.connect(self.label_size_requested.emit)
        size_row.addWidget(change)
        self.lay.addLayout(size_row)
        self.lay.addWidget(dim(f"{lab.width_px} × {lab.height_px} dots at 203 dpi", "faint"))
        if self._loaded_label:
            if self._loaded_label == lab:
                self.lay.addWidget(dim("✓ Matches the roll loaded in the printer."))
            else:
                self.lay.addWidget(dim(f"The printer has {self._loaded_label.name} labels loaded."))
        self.lay.addSpacing(8)
        self.lay.addWidget(section("Tips"))
        tips = [
            "Drag images or .nlbl files onto the canvas.",
            "Hold <b>Alt</b> while dragging to disable snapping.",
            "Hold <b>Shift</b> to constrain a move, or to free the aspect ratio while resizing.",
            "Arrow keys nudge by 1 dot; <b>Shift</b>+arrows by 1 mm.",
            "Use <b>{n}</b> or <b>{n:03}</b> in text and barcodes to print numbered runs.",
            "<b>Ctrl</b>+wheel zooms; middle-drag pans.",
        ]
        for t in tips:
            lab = dim("• " + t)
            lab.setTextFormat(Qt.RichText)
            self.lay.addWidget(lab)

    def _build_multi(self):
        title = QLabel(f"{len(self.items)} elements")
        title.setProperty("role", "title")
        self.lay.addWidget(title)
        self.lay.addWidget(dim("Use the alignment tools in the toolbar to align the selection to each "
                               "other, or to the center of the label."))

    # ------------------------------------------------------------------ single element
    def _build_single(self, it: LabelItem):
        head = QHBoxLayout()
        title = QLabel(it.display_name())
        title.setProperty("role", "title")
        head.addWidget(title, 1)
        head.addWidget(self._toggle("lock", "Lock position", lambda i: i.locked,
                                    lambda i, v: setattr(i, "locked", v)))
        self.lay.addLayout(head)

        self.lay.addWidget(section("Position & size"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        fields = [("X", 0), ("Y", 1), ("W", 2), ("H", 3)]
        for label, idx in fields:
            spin = mm_spin(0.1 if idx >= 2 else -500)
            spin.setPrefix(f"{label}  ")

            def getter(item, idx=idx):
                g = item.geometry()
                return round([g.x(), g.y(), g.width(), g.height()][idx] / MM, 3)

            def setter(item, v, idx=idx):
                g = item.geometry()
                vals = [g.x(), g.y(), g.width(), g.height()]
                if idx >= 2 and item.KEEP_ASPECT and vals[3] > 0:
                    aspect = vals[2] / vals[3]
                    if idx == 2:
                        vals[3] = v * MM / aspect
                    else:
                        vals[2] = v * MM * aspect
                vals[idx] = v * MM
                item.set_geometry(QRectF(*vals))
            self._bind(spin, getter, setter, commit_now=True)
            grid.addWidget(spin, idx // 2, idx % 2)
        self.lay.addLayout(grid)

        rot_row = QHBoxLayout()
        rot_row.setSpacing(2)
        rot_lbl = dim("Rotation", "faint")
        rot_row.addWidget(rot_lbl)
        rot_row.addStretch(1)
        rot_val = QLabel()
        self._refreshers.append(lambda: rot_val.setText(f"{self.items[0].rot}°"))
        rot_row.addWidget(rot_val)
        for deg, tip in ((-90, "Rotate left"), (90, "Rotate right")):
            b = tool_button("rotate", tip)
            if deg < 0:
                b.setIcon(_mirrored_icon("rotate"))
            b.clicked.connect(lambda _=False, d=deg: ([i.rotate_by(d) for i in self.items], self._changed(True)))
            rot_row.addWidget(b)
        self.lay.addLayout(rot_row)
        self.lay.addWidget(divider())

        builder = {TextItem: self._build_text, ImageItem: self._build_image,
                   ShapeItem: self._build_shape, BarcodeItem: self._build_barcode}[type(it)]
        builder(it)

    def _build_text(self, it: TextItem):
        self.lay.addWidget(section("Text"))
        edit = QPlainTextEdit()
        edit.setObjectName("TextEdit")
        edit.setFixedHeight(76)
        edit.setPlaceholderText("Type label text…")
        self._bind(edit, lambda i: i.text, lambda i, v: setattr(i, "text", v))
        self.text_edit = edit
        self.lay.addWidget(edit)
        ins = QHBoxLayout()
        ins.setSpacing(4)
        ins.addWidget(dim("Insert:", "faint"))
        for label, token, tip in (("Number", "{n}", "Sequence number for numbered runs"),
                                  ("001", "{n:03}", "Zero-padded sequence number"),
                                  ("Date", "{date}", "Today's date"), ("Time", "{time}", "Current time")):
            b = QPushButton(label)
            b.setProperty("flat", True)
            b.setToolTip(f"{tip}  —  {token}")
            b.clicked.connect(lambda _=False, t=token: edit.insertPlainText(t))
            ins.addWidget(b)
        ins.addStretch(1)
        self.lay.addLayout(ins)

        self.lay.addWidget(section("Font"))
        font = QFontComboBox()
        font.setMaxVisibleItems(16)
        self._bind(font, lambda i: i.family, lambda i, v: setattr(i, "family", v), commit_now=True)
        self.lay.addWidget(font)
        row = QHBoxLayout()
        size = QDoubleSpinBox()
        size.setRange(3, 200)
        size.setDecimals(1)
        size.setSuffix(" pt")
        size.setKeyboardTracking(False)
        self._bind(size, lambda i: i.size_pt, lambda i, v: setattr(i, "size_pt", v), commit_now=True)
        row.addWidget(size, 1)
        row.addWidget(self._toggle("bold", "Bold", lambda i: i.bold, lambda i, v: setattr(i, "bold", v)))
        row.addWidget(self._toggle("italic", "Italic", lambda i: i.italic, lambda i, v: setattr(i, "italic", v)))
        row.addWidget(self._toggle("underline", "Underline", lambda i: i.underline,
                                   lambda i, v: setattr(i, "underline", v)))
        self.lay.addLayout(row)

        al = QHBoxLayout()
        al.addLayout(self._segmented([("left", "text_left", "Align left"), ("center", "text_center", "Center"),
                                      ("right", "text_right", "Align right")],
                                     lambda i: i.halign, lambda i, v: setattr(i, "halign", v)))
        al.addLayout(self._segmented([("top", "align_top", "Top"), ("middle", "align_vcenter", "Middle"),
                                      ("bottom", "align_bottom", "Bottom")],
                                     lambda i: i.valign, lambda i, v: setattr(i, "valign", v)))
        self.lay.addLayout(al)

        spacing = QSpinBox()
        spacing.setRange(-50, 200)
        spacing.setSuffix(" %")
        spacing.setPrefix("Letter spacing  ")
        spacing.setKeyboardTracking(False)
        self._bind(spacing, lambda i: int(i.letter_spacing), lambda i, v: setattr(i, "letter_spacing", float(v)),
                   commit_now=True)
        self.lay.addWidget(spacing)

        fit = QCheckBox("Shrink text to fit box")
        self._bind(fit, lambda i: i.autofit, lambda i, v: setattr(i, "autofit", v), commit_now=True)
        self.lay.addWidget(fit)
        inv = QCheckBox("White on black")
        self._bind(inv, lambda i: i.invert, lambda i, v: setattr(i, "invert", v), commit_now=True)
        self.lay.addWidget(inv)

    def _build_image(self, it: ImageItem):
        self.lay.addWidget(section("Black & white conversion"))
        mode = QComboBox()
        for m in ImageItem.DITHER_MODES:
            mode.addItem(m, m)
        self._bind(mode, lambda i: i.dither, lambda i, v: setattr(i, "dither", v), commit_now=True)
        self.lay.addWidget(labelled("Method", mode))

        thr = slider(1, 254)
        thr_box = labelled("Threshold", thr)
        self._bind(thr, lambda i: i.threshold, lambda i, v: setattr(i, "threshold", v))
        thr.sliderReleased.connect(lambda: self.edited.emit(True))
        self.lay.addWidget(thr_box)
        self._refreshers.append(lambda: thr_box.setVisible(self.items[0].dither == "Threshold"))
        mode.currentIndexChanged.connect(lambda: thr_box.setVisible(mode.currentData() == "Threshold"))

        for label, attr in (("Brightness", "brightness"), ("Contrast", "contrast")):
            s = slider(-100, 100)
            self._bind(s, lambda i, a=attr: getattr(i, a), lambda i, v, a=attr: setattr(i, a, v))
            s.sliderReleased.connect(lambda: self.edited.emit(True))
            s.setToolTip("Double-click to reset")
            self.lay.addWidget(labelled(label, s))

        row = QHBoxLayout()
        row.addWidget(self._toggle("invert", "Invert", lambda i: i.invert, lambda i, v: setattr(i, "invert", v)))
        row.addWidget(self._toggle("flip_h", "Flip horizontally", lambda i: i.flip_h,
                                   lambda i, v: setattr(i, "flip_h", v)))
        row.addWidget(self._toggle("flip_v", "Flip vertically", lambda i: i.flip_v,
                                   lambda i, v: setattr(i, "flip_v", v)))
        row.addStretch(1)
        reset = QPushButton("Reset")
        reset.setToolTip("Reset adjustments and restore the original aspect ratio")
        reset.clicked.connect(self._reset_image)
        row.addWidget(reset)
        self.lay.addLayout(row)
        replace = QPushButton("Replace image…")
        replace.clicked.connect(lambda: self.replace_image_requested.emit(self.items[0]))
        self.lay.addWidget(replace)

    def _reset_image(self):
        for it in self.items:
            if isinstance(it, ImageItem):
                it.brightness = it.contrast = 0
                it.invert = it.flip_h = it.flip_v = False
                it.threshold = 128
                g = it.geometry()
                aspect = it.natural_aspect()
                if it.rot in (90, 270):
                    aspect = 1 / aspect
                it.set_geometry(QRectF(g.x(), g.y(), g.width(), g.width() / aspect))
        self._changed(True)
        self.refresh()

    def _build_shape(self, it: ShapeItem):
        self.lay.addWidget(section("Shape"))
        kind = QComboBox()
        for k, label in (("rect", "Rectangle"), ("ellipse", "Ellipse"), ("line", "Line")):
            kind.addItem(label, k)
        self._bind(kind, lambda i: i.shape, lambda i, v: setattr(i, "shape", v), commit_now=True)
        self.lay.addWidget(labelled("Type", kind))
        stroke = mm_spin(0.125, 20)
        self._bind(stroke, lambda i: i.stroke / MM, lambda i, v: setattr(i, "stroke", max(1.0, v * MM)),
                   commit_now=True)
        self.lay.addWidget(labelled("Line thickness", stroke))
        radius = mm_spin(0, 50)
        self._bind(radius, lambda i: i.radius / MM, lambda i, v: setattr(i, "radius", v * MM), commit_now=True)
        rbox = labelled("Corner radius", radius)
        self.lay.addWidget(rbox)
        fill = QCheckBox("Filled")
        self._bind(fill, lambda i: i.fill, lambda i, v: setattr(i, "fill", v), commit_now=True)
        self.lay.addWidget(fill)
        self._refreshers.append(lambda: (rbox.setVisible(self.items[0].shape == "rect"),
                                         fill.setVisible(self.items[0].shape != "line")))

    def _build_barcode(self, it: BarcodeItem):
        self.lay.addWidget(section("Barcode"))
        kind = QComboBox()
        for k, label in BarcodeItem.TYPES.items():
            kind.addItem(label, k)
        self._bind(kind, lambda i: i.btype, lambda i, v: setattr(i, "btype", v), commit_now=True)
        self.lay.addWidget(labelled("Type", kind))
        data = QLineEdit()
        data.setPlaceholderText("Text, URL or part number")
        self._bind(data, lambda i: i.data, lambda i, v: setattr(i, "data", v))
        self.lay.addWidget(labelled("Content", data))
        self.lay.addWidget(dim("Supports {n}, {n:03} and {date} placeholders.", "faint"))
        ecc = QComboBox()
        for k, label in (("L", "Low (7%)"), ("M", "Medium (15%)"), ("Q", "Quartile (25%)"), ("H", "High (30%)")):
            ecc.addItem(label, k)
        self._bind(ecc, lambda i: i.ecc, lambda i, v: setattr(i, "ecc", v), commit_now=True)
        ecc_box = labelled("Error correction", ecc)
        self.lay.addWidget(ecc_box)
        show = QCheckBox("Show text under bars")
        self._bind(show, lambda i: i.show_text, lambda i, v: setattr(i, "show_text", v), commit_now=True)
        self.lay.addWidget(show)
        self._refreshers.append(lambda: (ecc_box.setVisible(self.items[0].btype == "qr"),
                                         show.setVisible(self.items[0].btype != "qr")))

    def focus_text(self):
        if hasattr(self, "text_edit"):
            try:
                self.text_edit.setFocus()
                self.text_edit.selectAll()
            except RuntimeError:
                pass


def _mirrored_icon(name):
    px = theme.icon(name).pixmap(QSize(18, 18))
    return QIcon(px.transformed(QTransform().scale(-1, 1)))


def _delete_layout(lay):
    while lay.count():
        it = lay.takeAt(0)
        if it.widget():
            widget = it.widget()
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        elif it.layout():
            _delete_layout(it.layout())


# =============================================================================== layers
class LayersPanel(QWidget):
    def __init__(self, doc: Document):
        super().__init__()
        self.setObjectName("Panel")
        self.doc = doc
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setIconSize(QSize(18, 18))
        self.list.itemSelectionChanged.connect(self._to_scene)
        lay.addWidget(self.list)
        self._syncing = False
        doc.scene.selectionChanged.connect(self.sync_selection)

    def rebuild(self):
        self._syncing = True
        self.list.clear()
        icons = {"text": "text", "image": "image", "shape": "rect", "barcode": "qr"}
        for it in reversed(self.doc.scene.label_items()):
            name = icons.get(it.KIND, "layers")
            if isinstance(it, ShapeItem):
                name = {"rect": "rect", "ellipse": "ellipse", "line": "line"}[it.shape]
            elif isinstance(it, BarcodeItem) and it.btype != "qr":
                name = "barcode"
            li = QListWidgetItem(theme.icon(name, theme.colors()["text_dim"]), it.display_name())
            if it.locked:
                li.setText(li.text() + "   🔒")
            li.setData(Qt.UserRole, it.uid)
            self.list.addItem(li)
            li.setSelected(it.isSelected())
        self._syncing = False

    def sync_selection(self):
        if self._syncing:
            return
        self._syncing = True
        try:
            sel = {i.uid for i in self.doc.scene.selected_label_items()}
            for row in range(self.list.count()):
                li = self.list.item(row)
                li.setSelected(li.data(Qt.UserRole) in sel)
        except RuntimeError:
            pass
        self._syncing = False

    def _to_scene(self):
        if self._syncing:
            return
        self._syncing = True
        uids = {li.data(Qt.UserRole) for li in self.list.selectedItems()}
        for it in self.doc.scene.label_items():
            it.setSelected(it.uid in uids)
        self._syncing = False


# =============================================================================== printer status
class StatusDot(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(10, 10)
        self.color = QColor("#646C7A")

    def set_color(self, c):
        self.color = QColor(c)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(self.color)
        p.drawEllipse(1, 1, 8, 8)


class PrinterPanel(QWidget):
    connect_clicked = Signal()
    settings_clicked = Signal()
    identify_clicked = Signal()
    use_label_clicked = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("Panel")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(6)

        head = QHBoxLayout()
        self.dot = StatusDot()
        head.addWidget(self.dot)
        self.name = QLabel("No printer")
        self.name.setStyleSheet("font-weight: 600;")
        head.addWidget(self.name, 1)
        self.battery = QLabel()
        head.addWidget(self.battery)
        self.settings_btn = tool_button("settings", "Printer settings")
        self.settings_btn.clicked.connect(self.settings_clicked.emit)
        head.addWidget(self.settings_btn)
        lay.addLayout(head)
        self.state = dim("Not connected", "faint")
        lay.addWidget(self.state)
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setProperty("primary", True)
        self.connect_btn.clicked.connect(self.connect_clicked.emit)
        lay.addWidget(self.connect_btn)

        self.card = QFrame()
        self.card.setObjectName("Card")
        cl = QVBoxLayout(self.card)
        cl.setContentsMargins(12, 10, 12, 12)
        cl.setSpacing(4)
        cl.addWidget(section("Loaded labels"))
        self.label_name = QLabel("—")
        self.label_name.setProperty("role", "title")
        cl.addWidget(self.label_name)
        self.roll = dim("", "faint")
        cl.addWidget(self.roll)
        self.bar = QProgressBar()
        self.bar.setFixedHeight(6)
        self.bar.setTextVisible(False)
        cl.addWidget(self.bar)
        rem_row = QHBoxLayout()
        self.remaining = dim("")
        rem_row.addWidget(self.remaining, 1)
        self.action = QPushButton()
        self.action.setProperty("flat", True)
        self.action.clicked.connect(self._action)
        rem_row.addWidget(self.action)
        cl.addLayout(rem_row)
        lay.addWidget(self.card)
        self.card.hide()
        self._action_kind = None

    def set_state(self, state, msg, name=""):
        k = theme.colors()
        color = {"connected": k["ok"], "connecting": k["warn"], "scanning": k["warn"]}.get(state, k["text_faint"])
        self.dot.set_color(color)
        if state == "connected":
            self.name.setText(name or "Printer")
            self.state.setText("Connected")
            self.connect_btn.setText("Disconnect")
            self.connect_btn.setProperty("primary", False)
        else:
            if name:
                self.name.setText(name)
            self.state.setText(msg or {"connecting": "Connecting…", "scanning": "Searching…"}.get(state, "Not connected"))
            self.connect_btn.setText("Cancel" if state == "connecting" else "Connect")
            self.connect_btn.setProperty("primary", state == "disconnected")
            self.battery.clear()
            if state != "connecting":
                self.card.hide()
        self.connect_btn.style().unpolish(self.connect_btn)
        self.connect_btn.style().polish(self.connect_btn)

    def set_battery(self, level):
        if level is None:
            self.battery.clear()
            return
        level = max(0, min(4, int(level)))
        color = theme.colors()["error"] if level <= 1 else theme.colors()["text_dim"]
        self.battery.setPixmap(theme.icon(f"battery{level}", color).pixmap(QSize(20, 20)))
        self.battery.setToolTip(f"Battery {level * 25}%")

    def set_roll(self, rfid, label: LabelSize | None, matches: bool):
        if rfid is None:
            self.card.show()
            self.label_name.setText("No labels detected")
            self.roll.setText("Load a Niimbot roll and close the lid.")
            self.bar.hide()
            self.remaining.setText("")
            self.action.hide()
            return
        self.card.show()
        self.bar.show()
        self.label_name.setText(label.name if label else "Unknown roll")
        self.roll.setText(f"Roll {rfid.serial}")
        self.bar.setRange(0, max(1, rfid.total))
        self.bar.setValue(rfid.remaining)
        self.remaining.setText(f"<b>{rfid.remaining}</b> of {rfid.total} left")
        self.remaining.setTextFormat(Qt.RichText)
        if label is None:
            self._action_kind = "identify"
            self.action.setText("Set size…")
            self.action.show()
        elif not matches:
            self._action_kind = "use"
            self.action.setText("Use this size")
            self.action.show()
        else:
            self.action.hide()

    def _action(self):
        (self.identify_clicked if self._action_kind == "identify" else self.use_label_clicked).emit()


class PreviewWidget(QWidget):
    """Shows the exact 1-bit image that will be sent to the printer."""

    def __init__(self):
        super().__init__()
        self.img: QImage | None = None
        self.setMinimumHeight(70)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def set_image(self, img: QImage | None):
        self.img = img
        self.updateGeometry()
        self.update()

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        if not self.img:
            return 70
        return int(min(220, (w - 8) * self.img.height() / self.img.width() + 8))

    def sizeHint(self):
        return QSize(260, self.heightForWidth(260))

    def paintEvent(self, _):
        if not self.img:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        aw, ah = self.width() - 8, self.height() - 8
        s = min(aw / self.img.width(), ah / self.img.height())
        w, h = self.img.width() * s, self.img.height() * s
        r = QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)
        path = QPainterPath()
        path.addRoundedRect(r, 12 * s, 12 * s)
        p.setClipPath(path)
        p.setRenderHint(QPainter.SmoothPixmapTransform, s < 1)
        p.drawImage(r, self.img)


# =============================================================================== library
class LibraryPanel(QWidget):
    open_requested = Signal(str)

    def __init__(self, folder: Path):
        super().__init__()
        self.setObjectName("Panel")
        self.folder = folder
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search designs")
        self.search.addAction(theme.icon("search", theme.colors()["text_faint"], 16), QLineEdit.LeadingPosition)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        top.addWidget(self.search, 1)
        folder_btn = tool_button("folder", "Open library folder")
        folder_btn.clicked.connect(self._open_folder)
        top.addWidget(folder_btn)
        lay.addLayout(top)
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setIconSize(QSize(200, 64))
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setMovement(QListWidget.Static)
        self.list.setSpacing(6)
        self.list.setWordWrap(True)
        self.list.setUniformItemSizes(False)
        self.list.itemDoubleClicked.connect(lambda li: self.open_requested.emit(li.data(Qt.UserRole)))
        self.list.setToolTip("Double-click to open")
        lay.addWidget(self.list, 1)
        self.empty = dim(f"Designs you save to\n{folder}\nshow up here.", "faint")
        self.empty.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.empty)
        self.watcher = QFileSystemWatcher([str(folder)])
        self.watcher.directoryChanged.connect(lambda _: self._timer.start())
        self._timer = QTimer(self, singleShot=True, interval=400)
        self._timer.timeout.connect(self.refresh)
        self._thumbs: dict = {}
        self.refresh()

    def set_folder(self, folder: Path):
        self.watcher.removePaths(self.watcher.directories())
        self.folder = folder
        self.watcher.addPath(str(folder))
        self.refresh()

    def refresh(self):
        self.list.clear()
        files = sorted(self.folder.glob(f"*{FILE_EXT}"), key=lambda f: f.stat().st_mtime, reverse=True)
        for f in files:
            li = QListWidgetItem(self._thumb(f), f.stem)
            li.setData(Qt.UserRole, str(f))
            li.setToolTip(str(f))
            li.setSizeHint(QSize(210, 92))
            self.list.addItem(li)
        self.empty.setVisible(not files)
        self.list.setVisible(bool(files))
        self._filter(self.search.text())

    def _thumb(self, f: Path) -> QIcon:
        key = (str(f), f.stat().st_mtime)
        if key in self._thumbs:
            return self._thumbs[key]
        try:
            snap = Document.read_file(f)
            scene = LabelScene(LabelSize.from_dict(snap["label"]))
            for z, d in enumerate(snap["items"]):
                it = item_from_dict(d)
                it.setZValue(z)
                scene.addItem(it)
            img = mono_to_qimage(render_label(scene))
            scene.clear()
            pm = _rounded_thumb(img, QSize(200, 64))
        except Exception:  # noqa: BLE001 - unreadable files get a blank tile
            pm = QPixmap(200, 64)
            pm.fill(Qt.transparent)
        ic = QIcon(pm)
        self._thumbs[key] = ic
        return ic

    def _filter(self, text):
        t = text.lower().strip()
        for row in range(self.list.count()):
            li = self.list.item(row)
            li.setHidden(bool(t) and t not in li.text().lower())

    def _open_folder(self):
        import os
        os.startfile(self.folder)  # noqa: S606 - opens Explorer


def _rounded_thumb(img: QImage, box: QSize) -> QPixmap:
    s = min(box.width() / img.width(), box.height() / img.height())
    w, h = img.width() * s, img.height() * s
    pm = QPixmap(box)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    r = QRectF((box.width() - w) / 2, (box.height() - h) / 2, w, h)
    path = QPainterPath()
    path.addRoundedRect(r, 8 * s, 8 * s)
    p.setClipPath(path)
    p.drawImage(r, img)
    p.end()
    return pm


__all__ = ["PropertiesPanel", "LayersPanel", "PrinterPanel", "PreviewWidget", "LibraryPanel", "PRESETS"]
