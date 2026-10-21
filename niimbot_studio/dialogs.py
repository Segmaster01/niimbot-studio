"""Dialogs: print confirmation, progress, label size, new roll, connect, printer settings."""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont
from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
                               QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QProgressBar, QPushButton, QSpinBox,
                               QVBoxLayout, QWidget)

from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from . import __version__, theme
from . import brand as brand_mod
from .config import PRESETS, LabelSize, Settings
from .document import mono_to_qimage
from .driver import bluetooth_unavailable_message
from .panels import PreviewWidget, dim, section


def _buttons(*buttons):
    row = QHBoxLayout()
    row.addStretch(1)
    for b in buttons:
        row.addWidget(b)
    return row


def primary(text) -> QPushButton:
    b = QPushButton(text)
    b.setProperty("primary", True)
    b.setDefault(True)
    return b


class BaseDialog(QDialog):
    def __init__(self, parent, title, width=460):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(width)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(24, 20, 24, 20)
        self.lay.setSpacing(10)
        head = QLabel(title)
        head.setProperty("role", "title")
        self.lay.addWidget(head)


# =============================================================================== print
class PrintDialog(BaseDialog):
    """Preview the output and choose quantity before anything is sent to the printer."""

    def __init__(self, parent, render, label: LabelSize, settings: Settings, connected: bool,
                 rfid, loaded_label: LabelSize | None, uses_sequence: bool):
        super().__init__(parent, "Print label", 560)
        self.render = render  # render(n) -> PIL 1-bit image
        self.settings = settings
        self.rfid = rfid
        self.connected = connected
        self.uses_sequence = uses_sequence

        frame = QFrame()
        frame.setObjectName("Card")
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(16, 16, 16, 12)
        self.preview = PreviewWidget()
        self.preview.setMinimumHeight(150)
        fl.addWidget(self.preview)
        self.caption = dim("", "faint")
        self.caption.setAlignment(Qt.AlignCenter)
        fl.addWidget(self.caption)
        self.lay.addWidget(frame)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        row = 0
        if uses_sequence:
            grid.addWidget(section("Numbered run"), row, 0, 1, 3)
            row += 1
            self.start = QSpinBox()
            self.start.setRange(0, 999999)
            self.start.setValue(1)
            self.count = QSpinBox()
            self.count.setRange(1, 999)
            self.count.setValue(10)
            grid.addWidget(dim("First number"), row, 0)
            grid.addWidget(self.start, row, 1)
            row += 1
            grid.addWidget(dim("How many numbers"), row, 0)
            grid.addWidget(self.count, row, 1)
            row += 1
            self.preview_n = QSpinBox()
            self.preview_n.setRange(1, 999)
            grid.addWidget(dim("Preview label #"), row, 0)
            grid.addWidget(self.preview_n, row, 1)
            row += 1
            for w in (self.start, self.count, self.preview_n):
                w.valueChanged.connect(self._update)
        grid.addWidget(section("Output"), row, 0, 1, 3)
        row += 1
        self.copies = QSpinBox()
        self.copies.setRange(1, 999)
        self.copies.setValue(1)
        self.copies.valueChanged.connect(self._update)
        grid.addWidget(dim("Copies of each label" if uses_sequence else "Quantity"), row, 0)
        grid.addWidget(self.copies, row, 1)
        row += 1

        dens = QHBoxLayout()
        dens.setSpacing(0)
        self.density = QButtonGroup(self)
        for i, (val, name) in enumerate(((1, "Light"), (2, "Normal"), (3, "Dark"))):
            b = QPushButton(name)
            b.setCheckable(True)
            b.setProperty("seg", True)
            b.setChecked(val == settings["density"])
            self.density.addButton(b, val)
            dens.addWidget(b)
        dens.addStretch(1)
        grid.addWidget(dim("Print darkness"), row, 0)
        grid.addLayout(dens, row, 1)
        for spin in self.findChildren(QSpinBox):
            spin.setFixedWidth(120)
        grid.setColumnStretch(2, 1)
        self.lay.addLayout(grid)

        self.warning = QLabel()
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet(f"color: {theme.colors()['warn']};")
        self.lay.addWidget(self.warning)
        problems = []
        if not connected:
            problems.append("The printer is not connected.")
        elif rfid is None:
            problems.append("No label roll detected. Check the roll and lid.")
        if loaded_label and loaded_label != label:
            problems.append(f"This design is {label.name}, but {loaded_label.name} labels are loaded.")
        self.problems = problems

        self.summary = dim("")
        self.print_btn = primary("Print")
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.print_btn.clicked.connect(self.accept)
        bottom = QHBoxLayout()
        bottom.addWidget(self.summary, 1)
        bottom.addWidget(cancel)
        bottom.addWidget(self.print_btn)
        self.lay.addLayout(bottom)
        self._update()

    def numbers(self) -> list[int]:
        if not self.uses_sequence:
            return [None]
        return list(range(self.start.value(), self.start.value() + self.count.value()))

    def total(self) -> int:
        return len(self.numbers()) * self.copies.value()

    def _update(self):
        nums = self.numbers()
        if self.uses_sequence:
            self.preview_n.setMaximum(len(nums))
            n = nums[min(self.preview_n.value(), len(nums)) - 1]
        else:
            n = None
        img = self.render(n)
        self.preview.set_image(mono_to_qimage(img))
        label_n = f" · showing number {n}" if n is not None else ""
        self.caption.setText(f"Exact printer output — {img.width} × {img.height} dots{label_n}")
        total = self.total()
        self.print_btn.setText(f"Print {total} label{'s' if total != 1 else ''}")
        msgs = list(self.problems)
        if self.rfid is not None:
            left = self.rfid.remaining
            self.summary.setText(f"Uses {total} of {left} remaining")
            if total > left:
                msgs.append(f"Only {left} labels are left on this roll.")
        else:
            self.summary.setText("")
        self.warning.setText("\n".join("⚠  " + m for m in msgs))
        self.warning.setVisible(bool(msgs))
        self.print_btn.setEnabled(self.connected)

    def result_options(self):
        return {"numbers": self.numbers(), "copies": self.copies.value(),
                "density": self.density.checkedId()}


class PrintProgressDialog(BaseDialog):
    cancel_requested = Signal()

    def __init__(self, parent, total):
        super().__init__(parent, "Printing…", 380)
        self.total = total
        self.status = dim(f"Sending to printer…")
        self.lay.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.setRange(0, total)
        self.bar.setValue(0)
        self.lay.addWidget(self.bar)
        self.btn = QPushButton("Cancel")
        self.btn.clicked.connect(self._cancel)
        self.lay.addLayout(_buttons(self.btn))
        self._done = False

    def _cancel(self):
        if self._done:
            self.accept()
        else:
            self.btn.setEnabled(False)
            self.status.setText("Cancelling after the current label…")
            self.cancel_requested.emit()

    def progress(self, done, total):
        self.bar.setValue(done)
        self.status.setText(f"Printed {done} of {total}")

    def finished_print(self, ok, msg):
        self._done = True
        self.bar.setValue(self.bar.maximum() if ok else self.bar.value())
        self.status.setText(msg)
        self.status.setStyleSheet(f"color: {theme.colors()['ok' if ok else 'error']};")
        self.btn.setText("Close")
        self.btn.setEnabled(True)
        if ok:
            QTimer.singleShot(1200, self.accept)

    def reject(self):
        if self._done:
            super().reject()
        else:
            self._cancel()


# =============================================================================== label size
class SizePicker(QWidget):
    """Preset list plus a custom size row."""

    def __init__(self, current: LabelSize | None):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setFixedHeight(220)
        for s in PRESETS:
            li = QListWidgetItem(f"{s.name}")
            li.setData(Qt.UserRole, s)
            self.list.addItem(li)
            if s == current:
                self.list.setCurrentItem(li)
        lay.addWidget(self.list)
        row = QHBoxLayout()
        row.addWidget(dim("Custom:"))
        self.h = QDoubleSpinBox()
        self.h.setRange(5, 15)
        self.h.setSuffix(" mm")
        self.h.setDecimals(1)
        self.l = QDoubleSpinBox()
        self.l.setRange(5, 200)
        self.l.setSuffix(" mm")
        self.l.setDecimals(1)
        cur = current or PRESETS[3]
        self.h.setValue(cur.height_mm)
        self.l.setValue(cur.length_mm)
        row.addWidget(self.h)
        row.addWidget(QLabel("×"))
        row.addWidget(self.l)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(dim("Sizes are height × length. Height is the narrow side that runs across the "
                          "printhead (max 12 mm printable on the D11).", "faint"))
        self.list.currentItemChanged.connect(self._preset)

    def _preset(self, li):
        if li:
            s = li.data(Qt.UserRole)
            self.h.setValue(s.height_mm)
            self.l.setValue(s.length_mm)

    def value(self) -> LabelSize:
        return LabelSize(self.l.value(), self.h.value())


class LabelSizeDialog(BaseDialog):
    def __init__(self, parent, current: LabelSize):
        super().__init__(parent, "Label size", 380)
        self.picker = SizePicker(current)
        self.lay.addWidget(self.picker)
        ok = primary("Apply")
        ok.clicked.connect(self.accept)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.lay.addLayout(_buttons(cancel, ok))
        self.picker.list.itemDoubleClicked.connect(lambda _: self.accept())

    def value(self):
        return self.picker.value()


class NewRollDialog(BaseDialog):
    def __init__(self, parent, rfid):
        super().__init__(parent, "New label roll", 400)
        self.lay.addWidget(dim(
            f"The printer reports a roll this app hasn't seen before "
            f"(barcode {rfid.barcode}, {rfid.total} labels). Niimbot keeps the size encrypted on "
            f"the tag, so pick the size once and it will be recognized automatically from now on."))
        self.picker = SizePicker(None)
        self.lay.addWidget(self.picker)
        ok = primary("Remember this roll")
        ok.clicked.connect(self.accept)
        cancel = QPushButton("Not now")
        cancel.clicked.connect(self.reject)
        self.lay.addLayout(_buttons(cancel, ok))

    def value(self):
        return self.picker.value()


# =============================================================================== connect
class ConnectDialog(BaseDialog):
    def __init__(self, parent, service):
        super().__init__(parent, "Connect a printer", 420)
        self.service = service
        self._devices: list[tuple] = []
        self._error = None
        self._connect_error = None
        self._awaiting_connect = False
        self._connect_in_progress = False
        self._scanning = False
        self._scan_id = None
        self.lay.addWidget(dim(
            "Switch the printer on. It doesn't need to be paired in Windows settings. "
            "Close the Niimbot app on your phone first, because it can hold the printer's connection."))
        self.list = QListWidget()
        self.list.setFixedHeight(160)
        self.list.setIconSize(QSize(20, 20))
        self.lay.addWidget(self.list)
        self.show_all = QCheckBox("Show all Bluetooth devices")
        self.show_all.setToolTip(
            "List every advertising device (name, address, signal, services), for troubleshooting. "
            "The same list is written to niimbot.log next to settings.json.")
        self.show_all.toggled.connect(lambda _on: self._refresh_list())
        self.lay.addWidget(self.show_all)
        self.status = dim("", "faint")
        self.lay.addWidget(self.status)
        self.rescan = QPushButton("Search again")
        self.rescan.clicked.connect(self.scan)
        self.ok = primary("Connect")
        self.ok.setEnabled(False)
        self.ok.clicked.connect(self._start_connect)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(self.rescan)
        row.addStretch(1)
        row.addWidget(cancel)
        row.addWidget(self.ok)
        self.lay.addLayout(row)
        self.list.currentItemChanged.connect(
            lambda cur: self.ok.setEnabled(cur is not None and not self._connect_in_progress))
        self.list.itemDoubleClicked.connect(lambda _: self._start_connect())
        service.device_detected.connect(self._on_device)
        service.scan_finished.connect(self._finished)
        service.connect_failed.connect(self._on_connect_failed)
        service.state_changed.connect(self._on_state)
        self.scan()

    def scan(self):
        if self._awaiting_connect:
            self.service.disconnect()
            self._awaiting_connect = False
            self._connect_in_progress = False
        self._devices = []
        self._error = None
        self._connect_error = None
        self._scanning = True
        self.rescan.setEnabled(False)
        self._refresh_list()
        self._scan_id = self.service.scan()

    def _start_connect(self):
        if not self.selected() or self._connect_in_progress:
            return
        self._awaiting_connect = True
        self._connect_in_progress = True
        self._connect_error = None
        self.ok.setEnabled(False)
        self.rescan.setEnabled(False)
        self._refresh_list()
        addr, name = self.selected()
        self.service.connect_to(addr, name)

    def _on_state(self, state, _msg):
        if self._awaiting_connect and state == "connected":
            self._awaiting_connect = False
            self._connect_in_progress = False
            self.accept()

    def _on_connect_failed(self, msg):
        if not self._awaiting_connect:
            return
        self._connect_in_progress = False
        self._connect_error = msg
        self.rescan.setEnabled(True)
        self._refresh_list()
        QMessageBox.warning(self, "Couldn't connect", msg)

    def _on_device(self, scan_id, addr, name, rssi, matched, service_uuids=()):
        if scan_id != self._scan_id:
            return
        row = (addr, name or "", rssi, bool(matched), tuple(service_uuids or ()))
        for i, existing in enumerate(self._devices):
            if existing[0] == addr:
                self._devices[i] = row
                break
        else:
            self._devices.append(row)
        self._refresh_list()

    def _finished(self, scan_id, error):
        if scan_id != self._scan_id:
            return
        self._scanning = False
        self._error = error or None
        if not self._connect_in_progress:
            self.rescan.setEnabled(True)
        self._refresh_list()

    def _visible(self):
        if self.show_all.isChecked():
            return list(self._devices)
        return [row for row in self._devices if row[3]]

    def _row_text(self, addr, name, rssi, matched, service_uuids=()):
        if matched and not name:
            label = "Niimbot printer"
        else:
            label = name or "(no name)"
        text = f"{label}    {addr}"
        if self.show_all.isChecked():
            rssi_text = f"{rssi} dBm" if rssi is not None else "rssi n/a"
            services = ", ".join(service_uuids) if service_uuids else "no services"
            text += f"    {rssi_text}    {services}"
        return text

    def _status_text(self):
        if self._connect_error:
            return self._connect_error
        if self._awaiting_connect:
            sel = self.selected()
            who = (sel[1] or sel[0]) if sel else "the printer"
            return f"Connecting to {who}…"
        if self._error:
            return bluetooth_unavailable_message(self._error)
        rows = self._visible()
        n = len(rows)
        show_all = self.show_all.isChecked()
        noun = "device" if show_all else "printer"
        plural = "s" if n != 1 else ""
        if self._scanning:
            if n:
                return f"Searching… found {n} {noun}{plural}."
            if show_all:
                return "Searching for Bluetooth devices…"
            return "Searching for Niimbot printers…"
        if n == 0:
            if show_all:
                return ("No Bluetooth devices found. Make sure the printer is on and nearby, "
                        "then search again.")
            return "No printers found. Make sure it's on and nearby, then search again."
        return f"Found {n} {noun}{plural}."

    def _refresh_list(self):
        previous = self.selected()
        self.list.blockSignals(True)
        self.list.clear()
        restore = None
        for addr, name, rssi, matched, service_uuids in self._visible():
            li = QListWidgetItem(theme.icon("bluetooth"),
                                 self._row_text(addr, name, rssi, matched, service_uuids))
            li.setData(Qt.UserRole, (addr, name))
            self.list.addItem(li)
            if previous and previous[0] == addr:
                restore = self.list.count() - 1
        if self.list.count() and restore is None:
            restore = 0
        if restore is not None:
            self.list.setCurrentRow(restore)
        self.list.blockSignals(False)
        self.ok.setEnabled(self.list.currentItem() is not None and not self._connect_in_progress)
        self.status.setText(self._status_text())

    def selected(self):
        li = self.list.currentItem()
        return li.data(Qt.UserRole) if li else None

    def done(self, r):
        stop = self._awaiting_connect and r != QDialog.Accepted
        for sig, slot in ((self.service.device_detected, self._on_device),
                          (self.service.scan_finished, self._finished),
                          (self.service.connect_failed, self._on_connect_failed),
                          (self.service.state_changed, self._on_state)):
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        if stop:
            self.service.disconnect()
        super().done(r)


# =============================================================================== settings
def calibration_image(label: LabelSize) -> Image.Image:
    W, H = label.width_px, min(label.height_px, 96)
    img = Image.new("1", (W, label.height_px), 1)
    d = ImageDraw.Draw(img)
    top = (label.height_px - H) // 2
    d.rectangle([0, top, W - 1, top + H - 1], outline=0)
    for x in range(0, W, 8):
        L = 12 if x % 40 == 0 else 5
        d.line([x, top, x, top + L], fill=0)
        d.line([x, top + H - 1 - L, x, top + H - 1], fill=0)
    for y in range(0, H, 8):
        L = 12 if y % 40 == 0 else 5
        d.line([0, top + y, L, top + y], fill=0)
        d.line([W - 1 - L, top + y, W - 1, top + y], fill=0)
    d.line([W // 2, top + 20, W // 2, top + H - 20], fill=0)
    d.line([W // 2 - 30, top + H // 2, W // 2 + 30, top + H // 2], fill=0)
    try:
        f = ImageFont.truetype("arial.ttf", 14)
    except OSError:
        f = ImageFont.load_default()
    for x in range(40, W - 20, 40):
        d.text((x - 4 if x < 80 else x - 8, top + 14), str(x // 8), fill=0, font=f)
    return img


class OffsetStepper(QWidget):
    """One alignment axis: direction buttons around a plain-language readout of the shift."""

    changed = Signal(int)
    LIMIT = 40  # dots (5 mm)

    def __init__(self, title, button_texts, directions, value):
        super().__init__()
        self.value = value
        self.directions = directions  # (negative, positive) direction names
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        name = dim(title)
        name.setFixedWidth(80)
        lay.addWidget(name)
        self.minus = QPushButton(button_texts[0])
        self.plus = QPushButton(button_texts[1])
        self.readout = QLabel()
        self.readout.setAlignment(Qt.AlignCenter)
        self.readout.setMinimumWidth(150)
        for b, step in ((self.minus, -1), (self.plus, 1)):
            b.setFixedWidth(84)
            b.setAutoRepeat(True)
            b.setAutoRepeatDelay(400)
            b.clicked.connect(lambda _=False, d=step: self._step(d))
        lay.addWidget(self.minus)
        lay.addWidget(self.readout, 1)
        lay.addWidget(self.plus)
        self._refresh()

    def _step(self, d):
        v = max(-self.LIMIT, min(self.LIMIT, self.value + d))
        if v != self.value:
            self.value = v
            self._refresh()
            self.changed.emit(v)

    def _refresh(self):
        v = self.value
        if v == 0:
            text = "No shift"
        else:
            direction = self.directions[1] if v > 0 else self.directions[0]
            text = f"<b>{abs(v)}</b> dot{'s' if abs(v) != 1 else ''} {direction}  "                    f"<span style='color:{theme.colors()['text_faint']}'>({abs(v) / 8:.2f} mm)</span>"
        self.readout.setText(text)
        self.minus.setEnabled(v > -self.LIMIT)
        self.plus.setEnabled(v < self.LIMIT)


class PrinterSettingsDialog(BaseDialog):
    calibration_print = Signal(int, int)
    accent_changed = Signal(str)
    library_changed = Signal(str)
    brand_changed = Signal()
    check_updates_requested = Signal()

    SHUTDOWN = [(1, "15 minutes"), (2, "30 minutes"), (3, "45 minutes"), (4, "60 minutes")]

    def __init__(self, parent, service, settings: Settings, info):
        super().__init__(parent, "Settings", 480)
        self.service = service
        self.settings = settings

        self.lay.addWidget(section("Printer"))
        if info and service.connected:
            grid = QGridLayout()
            grid.setVerticalSpacing(4)
            rows = [("Name", service.name), ("Address", service.address), ("Serial", info.serial),
                    ("Firmware", info.software_version), ("Hardware", info.hardware_version)]
            for r, (k, v) in enumerate(rows):
                grid.addWidget(dim(k, "faint"), r, 0)
                grid.addWidget(QLabel(str(v)), r, 1)
            grid.setColumnStretch(1, 1)
            self.lay.addLayout(grid)
            row = QHBoxLayout()
            row.addWidget(dim("Auto power-off"))
            self.shutdown = QComboBox()
            for val, name in self.SHUTDOWN:
                self.shutdown.addItem(name, val)
            idx = self.shutdown.findData(info.auto_shutdown)
            self.shutdown.setCurrentIndex(max(0, idx))
            self.shutdown.currentIndexChanged.connect(
                lambda: service.set_auto_shutdown(self.shutdown.currentData()))
            row.addStretch(1)
            row.addWidget(self.shutdown)
            self.lay.addLayout(row)
            snd = QHBoxLayout()
            for item, name in ((1, "Connection sound"), (2, "Power sound")):
                cb = QCheckBox(name)
                cb.setChecked(True)
                cb.setToolTip("The printer can't report this setting, so changes are sent when toggled.")
                cb.toggled.connect(lambda on, i=item: service.set_sound(i, on))
                snd.addWidget(cb)
            snd.addStretch(1)
            self.lay.addLayout(snd)
        else:
            self.lay.addWidget(dim("Connect the printer to see its details and change its settings.", "faint"))

        row = QHBoxLayout()
        row.addWidget(dim("Default darkness"))
        row.addStretch(1)
        self.density = QComboBox()
        for val, name in ((1, "Light"), (2, "Normal"), (3, "Dark")):
            self.density.addItem(name, val)
        self.density.setCurrentIndex(self.density.findData(settings["density"]))
        self.density.currentIndexChanged.connect(lambda: settings.__setitem__("density", self.density.currentData()))
        row.addWidget(self.density)
        self.lay.addLayout(row)

        self.lay.addWidget(section("Print alignment"))
        self.lay.addWidget(dim("Shift every print to line up with the label edges. "
                               "Print the ruler, then nudge until its border sits evenly inside the label.", "faint"))
        self.ox = OffsetStepper("Horizontal", ("← Left", "Right →"), ("left", "right"), settings["offset_x"])
        self.oy = OffsetStepper("Vertical", ("↑ Up", "Down ↓"), ("up", "down"), settings["offset_y"])
        self.ox.changed.connect(lambda v: settings.__setitem__("offset_x", v))
        self.oy.changed.connect(lambda v: settings.__setitem__("offset_y", v))
        self.lay.addWidget(self.ox)
        self.lay.addWidget(self.oy)
        ruler = QPushButton("Print ruler")
        ruler.setIcon(theme.icon("ruler"))
        ruler.setEnabled(service.connected)
        ruler.clicked.connect(lambda: self.calibration_print.emit(self.ox.value, self.oy.value))
        self.lay.addLayout(_buttons(ruler))

        self.lay.addWidget(section("Appearance & files"))
        row = QHBoxLayout()
        row.addWidget(dim("Accent color"))
        row.addStretch(1)
        acc = QComboBox()
        acc.addItem(theme.accent_name("blue"), "blue")
        acc.addItem(theme.accent_name("red"), "red")
        acc.setCurrentIndex(acc.findData(settings["accent"] or "blue"))
        acc.currentIndexChanged.connect(lambda: self.accent_changed.emit(acc.currentData()))
        row.addWidget(acc)
        self.lay.addLayout(row)
        row = QHBoxLayout()
        self.lib = QLineEdit(str(settings.library_dir))
        self.lib.setReadOnly(True)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(labelled_inline("Library", self.lib), 1)
        row.addWidget(browse)
        self.lay.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(dim("Brand pack"))
        self.brand_label = QLabel()
        row.addWidget(self.brand_label, 1)
        load = QPushButton("Load…")
        load.setToolTip("Load a .nsbrand file with your organization's logo and app icon")
        load.clicked.connect(self._load_brand)
        row.addWidget(load)
        self.remove_brand = QPushButton("Remove")
        self.remove_brand.clicked.connect(self._remove_brand)
        row.addWidget(self.remove_brand)
        self.lay.addLayout(row)
        self._show_brand()

        self.lay.addWidget(section("Updates"))
        row = QHBoxLayout()
        row.addWidget(dim(f"Version {__version__}"))
        row.addStretch(1)
        auto = QCheckBox("Check automatically")
        auto.setChecked(bool(settings["check_updates"]))
        auto.toggled.connect(lambda on: settings.__setitem__("check_updates", on))
        row.addWidget(auto)
        check = QPushButton("Check now")
        check.clicked.connect(self.check_updates_requested.emit)
        row.addWidget(check)
        self.lay.addLayout(row)

        self.lay.addWidget(dim(f"Learned label rolls: {len(settings['learned_barcodes'])}. Designs and settings "
                               "stay on this PC; the only network request is the optional update check.", "faint"))
        close = primary("Done")
        close.clicked.connect(self.accept)
        self.lay.addLayout(_buttons(close))

    def _show_brand(self):
        b = theme.brand()
        user_pack = brand_mod.USER_BRAND.exists()
        if b.name or b.path:
            where = "" if user_pack else "  (installed with the app)"
            self.brand_label.setText(f"<b>{b.name or b.path.stem}</b>{where}")
        else:
            self.brand_label.setText("None, using the default look")
        self.remove_brand.setEnabled(user_pack)

    def _load_brand(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load brand pack", str(Path.home()),
                                              f"Brand packs (*{brand_mod.BRAND_EXT})")
        if not path:
            return
        try:
            b = brand_mod.install(Path(path))
        except ValueError as e:
            QMessageBox.warning(self, "Brand pack", str(e))
            return
        self.brand_label.setText(f"<b>{b.name or 'Brand pack'}</b> (applies after restart)")
        self.remove_brand.setEnabled(True)
        self.brand_changed.emit()

    def _remove_brand(self):
        brand_mod.remove()
        self.brand_label.setText("Removed (applies after restart)")
        self.remove_brand.setEnabled(False)
        self.brand_changed.emit()

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Library folder", self.lib.text())
        if d:
            self.lib.setText(d)
            self.settings["library_dir"] = d
            self.library_changed.emit(d)


def labelled_inline(text, w):
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(dim(text))
    lay.addWidget(w, 1)
    return box


__all__ = ["PrintDialog", "PrintProgressDialog", "LabelSizeDialog", "NewRollDialog", "ConnectDialog",
           "PrinterSettingsDialog", "calibration_image", "QColor", "QPainter"]
