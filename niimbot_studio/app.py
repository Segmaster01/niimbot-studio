"""Niimbot Studio main window."""
from __future__ import annotations

import base64
import json
import re
import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, QMimeData, QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication, QImage, QKeySequence, QPixmap
from PySide6.QtWidgets import (QApplication, QDockWidget, QFileDialog, QFrame, QHBoxLayout,
                               QLabel, QMainWindow, QMenu, QMessageBox, QProgressDialog, QPushButton,
                               QSizePolicy,
                               QTabWidget, QToolBar, QToolButton, QVBoxLayout, QWidget)

import time
import webbrowser

from . import GITHUB_REPO, __version__, theme, updater
from .canvas import LabelView
from .config import APP_NAME, FILE_EXT, LabelSize, Settings
from .dialogs import (ConnectDialog, LabelSizeDialog, NewRollDialog, PrintDialog,
                      PrinterSettingsDialog, PrintProgressDialog, calibration_image)
from .document import Document, mono_to_qimage, render_label
from .items import BarcodeItem, ImageItem, LabelItem, ShapeItem, TextItem, item_from_dict
from .panels import (LayersPanel, LibraryPanel, PreviewWidget, PrinterPanel, PropertiesPanel,
                     section)
from .printer_service import PrinterService

MIME_ITEMS = "application/x-niimbot-studio-items"
IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif *.tiff *.ico)"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff", ".ico"}


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self.loaded_label: LabelSize | None = None
        self.rfid = None
        self.device_info = None
        self._asked_barcodes: set[str] = set()
        self._banner_dismissed_for = None

        self.doc = Document(LabelSize.from_dict(settings["last_label"]))
        self.view = LabelView(self.doc)
        self.view.snap_enabled = settings["snap"]
        self.view.grid_enabled = settings["show_grid"]
        self.view.pixel_preview = settings["pixel_preview"]
        self.service = PrinterService()
        self._release = None
        self.updater = updater.Updater()
        self.updater.checked.connect(self._on_update_checked)

        self._build_central()
        self._build_docks()
        self._build_actions()
        self._build_menus()
        self._build_toolbars()
        self._build_statusbar()
        self._wire()

        self.resize(1440, 860)
        if settings["window_geometry"]:
            self.restoreGeometry(QByteArray.fromBase64(settings["window_geometry"].encode()))
        if settings["window_state"]:
            self.restoreState(QByteArray.fromBase64(settings["window_state"].encode()))
        self._update_title()
        self._update_actions()
        self._schedule_preview()
        self.props.rebuild()

        if settings["printer_address"] and settings["auto_connect"]:
            QTimer.singleShot(300, lambda: self.service.connect_to(settings["printer_address"],
                                                                   settings["printer_name"]))

    # ======================================================================= layout
    def _build_central(self):
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.banner = QFrame()
        self.banner.setObjectName("Banner")
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(12, 6, 8, 6)
        warn = QLabel()
        warn.setPixmap(theme.icon("info", theme.colors()["warn"], 16).pixmap(16, 16))
        bl.addWidget(warn)
        self.banner_text = QLabel()
        bl.addWidget(self.banner_text, 1)
        self.banner_use = QPushButton("Use loaded size")
        self.banner_use.setProperty("primary", True)
        self.banner_use.clicked.connect(self.use_loaded_label)
        bl.addWidget(self.banner_use)
        dismiss = QPushButton("Dismiss")
        dismiss.setProperty("flat", True)
        dismiss.clicked.connect(self._dismiss_banner)
        bl.addWidget(dismiss)
        wrap = QWidget()
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(12, 8, 12, 0)
        wl.addWidget(self.banner)
        self.banner_wrap = wrap
        wrap.hide()
        lay.addWidget(wrap)

        self.update_banner = QFrame()
        self.update_banner.setObjectName("UpdateBanner")
        ul = QHBoxLayout(self.update_banner)
        ul.setContentsMargins(12, 6, 8, 6)
        up_icon = QLabel()
        up_icon.setPixmap(theme.icon("refresh", theme.colors()["accent_light"], 16).pixmap(16, 16))
        ul.addWidget(up_icon)
        self.update_text = QLabel()
        ul.addWidget(self.update_text, 1)
        notes = QPushButton("What's new")
        notes.setProperty("flat", True)
        notes.clicked.connect(lambda: webbrowser.open(self._release.page_url) if self._release else None)
        ul.addWidget(notes)
        self.update_btn = QPushButton("Update now")
        self.update_btn.setProperty("primary", True)
        self.update_btn.clicked.connect(self.install_update)
        ul.addWidget(self.update_btn)
        skip = QPushButton("Skip this version")
        skip.setProperty("flat", True)
        skip.clicked.connect(self._skip_update)
        ul.addWidget(skip)
        uwrap = QWidget()
        uwl = QVBoxLayout(uwrap)
        uwl.setContentsMargins(12, 8, 12, 0)
        uwl.addWidget(self.update_banner)
        self.update_wrap = uwrap
        uwrap.hide()
        lay.addWidget(uwrap)
        lay.addWidget(self.view, 1)
        self.setCentralWidget(central)

    def _dock(self, title, widget, area, name):
        d = QDockWidget(title, self)
        d.setObjectName(name)
        d.setWidget(widget)
        d.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetClosable)
        self.addDockWidget(area, d)
        return d

    def _build_docks(self):
        # Right: printer + output preview, then properties/layers.
        printer_box = QWidget()
        printer_box.setObjectName("Panel")
        pl = QVBoxLayout(printer_box)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(0)
        self.printer_panel = PrinterPanel()
        pl.addWidget(self.printer_panel)
        prev_wrap = QWidget()
        pw = QVBoxLayout(prev_wrap)
        pw.setContentsMargins(16, 4, 16, 12)
        pw.setSpacing(4)
        pw.addWidget(section("Print preview"))
        self.preview = PreviewWidget()
        self.preview.setToolTip("The exact black & white image the printer will receive")
        pw.addWidget(self.preview)
        pl.addWidget(prev_wrap)
        self.printer_dock = self._dock("PRINTER", printer_box, Qt.RightDockWidgetArea, "printer")

        self.props = PropertiesPanel(self.doc)
        self.layers = LayersPanel(self.doc)
        tabs = QTabWidget()
        tabs.setDocumentMode(True)
        tabs.addTab(self.props, "Properties")
        tabs.addTab(self.layers, "Layers")
        self.inspector_dock = self._dock("INSPECTOR", tabs, Qt.RightDockWidgetArea, "inspector")
        self.inspector_dock.setMinimumWidth(300)

        self.library = LibraryPanel(self.settings.library_dir)
        self.library_dock = self._dock("LIBRARY", self.library, Qt.LeftDockWidgetArea, "library")
        self.library_dock.setMinimumWidth(240)
        self.resizeDocks([self.library_dock, self.inspector_dock], [250, 320], Qt.Horizontal)

    # ======================================================================= actions
    def _act(self, text, icon=None, shortcut=None, slot=None, checkable=False, tip=None):
        a = QAction(text, self)
        if icon:
            a.setIcon(theme.icon(icon))
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        if slot:
            a.triggered.connect(slot)
        a.setCheckable(checkable)
        tip = tip or text.replace("&", "").replace("…", "")
        a.setToolTip(f"{tip}  ({QKeySequence(shortcut).toString()})" if shortcut else tip)
        self.addAction(a)
        return a

    def _build_actions(self):
        A = self._act
        self.a_new = A("&New", "new", "Ctrl+N", self.file_new)
        self.a_open = A("&Open…", "open", "Ctrl+O", self.file_open)
        self.a_save = A("&Save", "save", "Ctrl+S", self.file_save)
        self.a_save_as = A("Save &As…", "saveas", "Ctrl+Shift+S", self.file_save_as)
        self.a_export = A("&Export PNG…", "export", "Ctrl+E", self.export_png)
        self.a_print = A("&Print…", "print", "Ctrl+P", self.print_label)
        self.a_settings = A("Se&ttings…", "settings", "Ctrl+,", self.open_settings)
        self.a_quit = A("E&xit", None, "Alt+F4", self.close)

        self.a_undo = A("&Undo", "undo", "Ctrl+Z", self.undo)
        self.a_redo = A("&Redo", "redo", "Ctrl+Y", self.redo)
        self.a_cut = A("Cu&t", "cut", "Ctrl+X", self.cut)
        self.a_copy = A("&Copy", "copy", "Ctrl+C", self.copy)
        self.a_paste = A("&Paste", "paste", "Ctrl+V", self.paste)
        self.a_dup = A("&Duplicate", "duplicate", "Ctrl+D", self.duplicate)
        self.a_delete = A("De&lete", "delete", "Delete", self.delete)
        self.a_select_all = A("Select &All", None, "Ctrl+A", self.select_all)

        self.a_text = A("&Text", "text", "T", lambda: self.insert_text(), tip="Add text")
        self.a_image = A("&Image…", "image", "I", self.insert_image, tip="Add image")
        brand_name = theme.brand().name or "company"
        self.a_logo = A(f"{brand_name} &logo", "logo", None, self.insert_logo, tip=f"Add {brand_name} logo")
        self.a_logo.setVisible(theme.logo_bytes() is not None)
        self.a_rect = A("&Rectangle", "rect", "R", lambda: self.insert_shape("rect"), tip="Add rectangle / border")
        self.a_ellipse = A("&Ellipse", "ellipse", "E", lambda: self.insert_shape("ellipse"), tip="Add ellipse")
        self.a_line = A("&Line", "line", "L", lambda: self.insert_shape("line"), tip="Add line")
        self.a_qr = A("&QR code", "qr", "Q", lambda: self.insert_barcode("qr"), tip="Add QR code")
        self.a_barcode = A("&Barcode", "barcode", "B", lambda: self.insert_barcode("code128"),
                           tip="Add Code 128 barcode")

        self.a_al = A("Align left", "align_left", "Ctrl+Shift+Left", lambda: self.align("left"))
        self.a_ahc = A("Align centers horizontally", "align_hcenter", None, lambda: self.align("hcenter"))
        self.a_ar = A("Align right", "align_right", "Ctrl+Shift+Right", lambda: self.align("right"))
        self.a_at = A("Align top", "align_top", "Ctrl+Shift+Up", lambda: self.align("top"))
        self.a_avc = A("Align middles vertically", "align_vcenter", None, lambda: self.align("vcenter"))
        self.a_ab = A("Align bottom", "align_bottom", "Ctrl+Shift+Down", lambda: self.align("bottom"))
        self.a_ch = A("Center horizontally on label", "center_h", "Ctrl+Shift+H", lambda: self.center_on_label(True, False))
        self.a_cv = A("Center vertically on label", "center_v", "Ctrl+Shift+V", lambda: self.center_on_label(False, True))
        self.a_dh = A("Distribute horizontally", "distribute_h", None, lambda: self.distribute("h"))
        self.a_dv = A("Distribute vertically", "distribute_v", None, lambda: self.distribute("v"))
        self.a_front = A("Bring to front", "front", "Ctrl+Shift+]", lambda: self.restack("front"))
        self.a_forward = A("Bring forward", None, "Ctrl+]", lambda: self.restack("forward"))
        self.a_backward = A("Send backward", None, "Ctrl+[", lambda: self.restack("backward"))
        self.a_back = A("Send to back", "back", "Ctrl+Shift+[", lambda: self.restack("back"))
        self.a_rot_r = A("Rotate right", "rotate", "Ctrl+R", lambda: self.rotate(90))
        self.a_rot_l = A("Rotate left", None, "Ctrl+Shift+R", lambda: self.rotate(-90))
        self.a_lock = A("Lock / unlock", "lock", "Ctrl+L", self.toggle_lock)

        self.a_zoom_in = A("Zoom in", "zoom_in", "Ctrl+=", lambda: self.view.zoom_step(1.25))
        self.a_zoom_out = A("Zoom out", "zoom_out", "Ctrl+-", lambda: self.view.zoom_step(1 / 1.25))
        self.a_zoom_fit = A("Fit label", "fit", "Ctrl+0", self.view.zoom_fit)
        self.a_zoom_100 = A("Actual size", None, "Ctrl+1", self._zoom_actual)
        self.a_snap = A("Smart snapping", "snap", None, self._toggle_snap, checkable=True,
                        tip="Snap to label center, edges and other elements (hold Alt to bypass)")
        self.a_grid = A("Grid", "grid", "Ctrl+'", self._toggle_grid, checkable=True, tip="Show 1 mm grid and snap to it")
        self.a_pixels = A("Pixel preview", "pixels", "Ctrl+Shift+P", self._toggle_pixels, checkable=True,
                          tip="Show the exact printed pixels on the canvas")
        self.a_snap.setChecked(self.view.snap_enabled)
        self.a_grid.setChecked(self.view.grid_enabled)
        self.a_pixels.setChecked(self.view.pixel_preview)

        self.a_connect = A("&Connect…", "bluetooth", None, self.connect_printer)
        self.a_refresh = A("&Refresh status", "refresh", "F5", self.service.refresh)
        self.a_label_size = A("Label &size…", "tag", None, self.change_label_size)
        self.a_use_loaded = A("Use &loaded label size", None, None, self.use_loaded_label)
        self.a_check_updates = A("Check for &updates…", "refresh", None, lambda: self.check_updates(manual=True))
        self.a_project = A("&Project page", "link", None,
                           lambda: webbrowser.open(f"https://github.com/{GITHUB_REPO}"))
        self.a_about = A("&About Niimbot Studio", "info", None, self.about)

        self.selection_actions = [self.a_cut, self.a_copy, self.a_dup, self.a_delete, self.a_ch, self.a_cv,
                                  self.a_front, self.a_forward, self.a_backward, self.a_back, self.a_rot_r,
                                  self.a_rot_l, self.a_lock, self.a_al, self.a_ahc, self.a_ar, self.a_at,
                                  self.a_avc, self.a_ab]

    def _build_menus(self):
        mb = self.menuBar()
        m = mb.addMenu("&File")
        for a in (self.a_new, self.a_open):
            m.addAction(a)
        self.recent_menu = m.addMenu("Open &recent")
        m.addSeparator()
        for a in (self.a_save, self.a_save_as, self.a_export):
            m.addAction(a)
        m.addSeparator()
        m.addAction(self.a_print)
        m.addSeparator()
        m.addAction(self.a_settings)
        m.addSeparator()
        m.addAction(self.a_quit)
        self._rebuild_recent()

        m = mb.addMenu("&Edit")
        for a in (self.a_undo, self.a_redo, None, self.a_cut, self.a_copy, self.a_paste, self.a_dup,
                  self.a_delete, None, self.a_select_all):
            m.addSeparator() if a is None else m.addAction(a)

        m = mb.addMenu("&Insert")
        for a in (self.a_text, self.a_image, self.a_logo, None, self.a_rect, self.a_ellipse, self.a_line,
                  None, self.a_qr, self.a_barcode):
            m.addSeparator() if a is None else m.addAction(a)

        m = mb.addMenu("&Arrange")
        for a in (self.a_ch, self.a_cv, None, self.a_al, self.a_ahc, self.a_ar, self.a_at, self.a_avc,
                  self.a_ab, None, self.a_dh, self.a_dv, None, self.a_front, self.a_forward,
                  self.a_backward, self.a_back, None, self.a_rot_r, self.a_rot_l, None, self.a_lock):
            m.addSeparator() if a is None else m.addAction(a)

        m = mb.addMenu("&View")
        for a in (self.a_zoom_in, self.a_zoom_out, self.a_zoom_fit, self.a_zoom_100, None, self.a_snap,
                  self.a_grid, self.a_pixels, None):
            m.addSeparator() if a is None else m.addAction(a)
        for d in (self.library_dock, self.inspector_dock, self.printer_dock):
            m.addAction(d.toggleViewAction())

        m = mb.addMenu("&Printer")
        for a in (self.a_connect, self.a_refresh, None, self.a_label_size, self.a_use_loaded, None,
                  self.a_settings):
            m.addSeparator() if a is None else m.addAction(a)

        m = mb.addMenu("&Help")
        for a in (self.a_check_updates, self.a_project, None, self.a_about):
            m.addSeparator() if a is None else m.addAction(a)

    def _build_toolbars(self):
        tb = QToolBar("Main")
        tb.setObjectName("main_toolbar")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))
        tb.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.addToolBar(Qt.TopToolBarArea, tb)
        brand = QLabel()
        pm = theme.mark_pixmap(26)
        brand.setPixmap(pm)
        brand.setContentsMargins(4, 0, 6, 0)
        tb.addWidget(brand)
        title = QLabel("Niimbot Studio")
        title.setStyleSheet("font-size: 11pt; font-weight: 600; padding-right: 10px;")
        tb.addWidget(title)
        tb.addSeparator()
        for a in (self.a_new, self.a_open, self.a_save, None, self.a_undo, self.a_redo, None):
            tb.addSeparator() if a is None else tb.addAction(a)
        for a in (self.a_text, self.a_image, self.a_logo, self.a_rect, self.a_ellipse, self.a_line,
                  self.a_qr, self.a_barcode):
            tb.addAction(a)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        self.label_btn = QToolButton()
        self.label_btn.setIcon(theme.icon("tag"))
        self.label_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.label_btn.setToolTip("Label size")
        self.label_btn.clicked.connect(self.change_label_size)
        tb.addWidget(self.label_btn)
        tb.addSeparator()
        print_btn = QToolButton()
        print_btn.setObjectName("PrintButton")
        print_btn.setDefaultAction(self.a_print)
        print_btn.setText("Print")
        print_btn.setIcon(theme.icon("print", "#FFFFFF"))
        print_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        tb.addWidget(print_btn)

        tb2 = QToolBar("Arrange")
        tb2.setObjectName("arrange_toolbar")
        tb2.setMovable(False)
        tb2.setIconSize(QSize(18, 18))
        self.addToolBarBreak()
        self.addToolBar(Qt.TopToolBarArea, tb2)
        for a in (self.a_ch, self.a_cv, None, self.a_al, self.a_ahc, self.a_ar, self.a_at, self.a_avc,
                  self.a_ab, None, self.a_dh, self.a_dv, None, self.a_front, self.a_back, self.a_rot_r,
                  self.a_lock, self.a_delete):
            tb2.addSeparator() if a is None else tb2.addAction(a)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb2.addWidget(spacer)
        for a in (self.a_snap, self.a_grid, self.a_pixels, None, self.a_zoom_out):
            tb2.addSeparator() if a is None else tb2.addAction(a)
        self.zoom_label = QToolButton()
        self.zoom_label.setText("100%")
        self.zoom_label.setToolTip("Fit label (Ctrl+0)")
        self.zoom_label.clicked.connect(self.view.zoom_fit)
        self.zoom_label.setFixedWidth(56)
        tb2.addWidget(self.zoom_label)
        tb2.addAction(self.a_zoom_in)
        tb2.addAction(self.a_zoom_fit)

    def _build_statusbar(self):
        sb = self.statusBar()
        self.status_pos = QLabel("")
        self.status_label = QLabel("")
        sb.addPermanentWidget(self.status_pos)
        sb.addPermanentWidget(self.status_label)

    def _wire(self):
        d = self.doc
        d.changed.connect(self._on_doc_changed)
        d.state_changed.connect(self._update_title)
        d.state_changed.connect(self._update_actions)
        d.label_changed.connect(self._on_label_changed)
        d.scene.selectionChanged.connect(self._on_selection)
        self.view.edited.connect(self._on_canvas_edit)
        self.view.cursor_moved.connect(lambda x, y: self.status_pos.setText(f"{x / 8:.2f}, {y / 8:.2f} mm"))
        self.view.zoom_changed.connect(lambda z: self.zoom_label.setText(f"{z / 4 * 100:.0f}%"))
        self.view.files_dropped.connect(self._on_drop)
        self.view.item_double_clicked.connect(self._on_double_click)
        self.view.context_menu.connect(self._context_menu)
        self.props.edited.connect(self._on_props_edit)
        self.props.label_size_requested.connect(self.change_label_size)
        self.props.replace_image_requested.connect(self.replace_image)
        self.library.open_requested.connect(lambda p: self.open_path(Path(p)))
        self.printer_panel.connect_clicked.connect(self._printer_button)
        self.printer_panel.settings_clicked.connect(self.open_settings)
        self.printer_panel.identify_clicked.connect(lambda: self._identify_roll(force=True))
        self.printer_panel.use_label_clicked.connect(self.use_loaded_label)

        s = self.service
        s.state_changed.connect(self._on_printer_state)
        s.device_info.connect(self._on_device_info)
        s.status.connect(lambda st: self.printer_panel.set_battery(st.battery))
        s.rfid.connect(self._on_rfid)

        self._commit_timer = QTimer(self, singleShot=True, interval=700)
        self._commit_timer.timeout.connect(self.doc.commit)
        self._preview_timer = QTimer(self, singleShot=True, interval=90)
        self._preview_timer.timeout.connect(self._update_preview)

    # ======================================================================= doc events
    def _on_doc_changed(self):
        self._schedule_preview()
        self.layers.rebuild()
        self._update_title()
        self._update_actions()
        self.view.viewport().update()

    def _on_selection(self):
        self.props.rebuild()
        self._update_actions()

    def _on_canvas_edit(self):
        self.doc.commit()
        self.props.refresh()

    def _on_props_edit(self, commit_now):
        self.view.viewport().update()
        self._schedule_preview()
        if commit_now:
            self._commit_timer.stop()
            self.doc.commit()
        else:
            self._commit_timer.start()

    def _on_label_changed(self):
        self.settings["last_label"] = self.doc.label.to_dict()
        self._update_banner()
        self.props.rebuild()
        self._schedule_preview()

    def _schedule_preview(self):
        self._preview_timer.start()

    def _update_preview(self):
        img = mono_to_qimage(render_label(self.doc.scene))
        self.preview.set_image(img)
        self.view.set_preview(img)
        lab = self.doc.label
        self.label_btn.setText(f" {lab.name}")
        self.status_label.setText(f"{lab.name}  ·  {lab.width_px} × {lab.height_px} dots")

    def _update_title(self):
        mark = "● " if self.doc.dirty else ""
        self.setWindowTitle(f"{mark}{self.doc.title} — {APP_NAME}")

    def _update_actions(self):
        sel = self.doc.scene.selected_label_items()
        for a in self.selection_actions:
            a.setEnabled(bool(sel))
        self.a_dh.setEnabled(len(sel) >= 3)
        self.a_dv.setEnabled(len(sel) >= 3)
        self.a_undo.setEnabled(self.doc.can_undo())
        self.a_redo.setEnabled(self.doc.can_redo())
        self.a_use_loaded.setEnabled(self.loaded_label is not None and self.loaded_label != self.doc.label)
        self.a_lock.setIcon(theme.icon("unlock" if sel and all(i.locked for i in sel) else "lock"))

    def _commit_now(self):
        self._commit_timer.stop()
        self.doc.commit()

    # ======================================================================= file
    def _maybe_save(self) -> bool:
        if not self.doc.dirty or self.doc.is_empty() and self.doc.path is None:
            return True
        r = QMessageBox.question(self, APP_NAME, f"Save changes to “{self.doc.title}”?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
        if r == QMessageBox.Save:
            return self.file_save()
        return r == QMessageBox.Discard

    def file_new(self):
        if not self._maybe_save():
            return
        self.doc.new(self.loaded_label or self.doc.label)
        self.props.rebuild()
        self.view.zoom_fit()

    def file_open(self):
        if not self._maybe_save():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open label", str(self.settings.library_dir),
                                              f"Niimbot Studio labels (*{FILE_EXT})")
        if path:
            self.open_path(Path(path), ask=False)

    def open_path(self, path: Path, ask=True):
        if ask and not self._maybe_save():
            return
        try:
            self.doc.open(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, APP_NAME, f"Couldn't open {path.name}:\n{e}")
            return
        self.settings.add_recent(str(path))
        self._rebuild_recent()
        self.props.rebuild()
        self.view.zoom_fit()
        self._update_banner()

    def _suggest_name(self) -> str:
        for it in self.doc.scene.label_items():
            if isinstance(it, TextItem) and it.text.strip():
                name = re.sub(r'[\\/:*?"<>|{}\n\r]+', " ", it.text.strip().splitlines()[0]).strip()
                if name:
                    return name[:48]
        return "Label"

    def file_save(self) -> bool:
        self._commit_now()
        if self.doc.path is None:
            return self.file_save_as()
        try:
            self.doc.save(self.doc.path)
        except OSError as e:
            QMessageBox.warning(self, APP_NAME, f"Couldn't save:\n{e}")
            return False
        self.statusBar().showMessage(f"Saved {self.doc.path.name}", 3000)
        return True

    def file_save_as(self) -> bool:
        self._commit_now()
        start = self.doc.path or (self.settings.library_dir / f"{self._suggest_name()}{FILE_EXT}")
        path, _ = QFileDialog.getSaveFileName(self, "Save label", str(start),
                                              f"Niimbot Studio labels (*{FILE_EXT})")
        if not path:
            return False
        try:
            saved = self.doc.save(Path(path))
        except OSError as e:
            QMessageBox.warning(self, APP_NAME, f"Couldn't save:\n{e}")
            return False
        self.settings.add_recent(str(saved))
        self._rebuild_recent()
        self.statusBar().showMessage(f"Saved {saved.name}", 3000)
        return True

    def export_png(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export PNG", str(Path.home() / "Pictures" / f"{self._suggest_name()}.png"),
                                              "PNG image (*.png)")
        if path:
            render_label(self.doc.scene).save(path)
            self.statusBar().showMessage(f"Exported {Path(path).name}", 3000)

    def _rebuild_recent(self):
        self.recent_menu.clear()
        files = [p for p in self.settings["recent_files"] if Path(p).exists()]
        for p in files:
            self.recent_menu.addAction(Path(p).name, lambda p=p: self.open_path(Path(p)))
        self.recent_menu.setEnabled(bool(files))

    # ======================================================================= edit
    def undo(self):
        self._commit_now()
        self.doc.undo()
        self.props.rebuild()

    def redo(self):
        self.doc.redo()
        self.props.rebuild()

    def selected(self) -> list[LabelItem]:
        return self.doc.scene.selected_label_items()

    def copy(self):
        items = self.selected()
        if not items:
            return
        data = []
        for it in items:
            d = it.to_dict()
            if isinstance(d.get("data"), bytes):
                d["data"] = {"b64": base64.b64encode(d["data"]).decode()}
            data.append(d)
        mime = QMimeData()
        mime.setData(MIME_ITEMS, json.dumps(data).encode())
        mime.setImageData(mono_to_qimage(render_label(self.doc.scene)))
        QGuiApplication.clipboard().setMimeData(mime)

    def cut(self):
        self.copy()
        self.delete()

    def paste(self):
        mime = QGuiApplication.clipboard().mimeData()
        if mime.hasFormat(MIME_ITEMS):
            data = json.loads(bytes(mime.data(MIME_ITEMS)).decode())
            self._paste_dicts(data, offset=8)
        elif mime.hasImage():
            img: QImage = mime.imageData()
            from PySide6.QtCore import QBuffer, QIODevice
            buf = QBuffer()
            buf.open(QIODevice.WriteOnly)
            img.save(buf, "PNG")
            self._add_image_bytes(bytes(buf.data()))
        elif mime.hasUrls():
            self._on_drop([u.toLocalFile() for u in mime.urls() if u.isLocalFile()], None)
        elif mime.hasText() and mime.text().strip():
            self.insert_text(mime.text().strip())

    def _paste_dicts(self, data, offset=8):
        self.doc.scene.clearSelection()
        import uuid
        for d in data:
            if isinstance(d.get("data"), dict) and "b64" in d["data"]:
                d["data"] = base64.b64decode(d["data"]["b64"])
            d = dict(d, uid=uuid.uuid4().hex, x=d["x"] + offset, y=d["y"] + offset)
            it = item_from_dict(d)
            self.doc.add_item(it, select=False, commit=False)
            it.setSelected(True)
        self.doc.commit()

    def duplicate(self):
        items = self.selected()
        if items:
            self._paste_dicts([it.to_dict() for it in items], offset=8)

    def delete(self):
        self.doc.remove_items(self.selected())

    def select_all(self):
        for it in self.doc.scene.label_items():
            it.setSelected(True)

    # ======================================================================= insert
    def _center(self, w, h) -> QRectF:
        lr = self.doc.scene.label_rect()
        return QRectF(lr.center().x() - w / 2, lr.center().y() - h / 2, w, h)

    def insert_text(self, text="Text"):
        lr = self.doc.scene.label_rect()
        printable = min(lr.height(), 96)
        it = TextItem(text)
        it.set_geometry(self._center(lr.width() - 16, printable - 16))
        self.doc.add_item(it)
        QTimer.singleShot(0, self.props.focus_text)

    def insert_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "Add image", str(Path.home() / "Pictures"), IMAGE_FILTER)
        if path:
            self._add_image_bytes(Path(path).read_bytes())

    def insert_logo(self):
        data = theme.logo_bytes()
        if data:
            self._add_image_bytes(data)

    def _add_image_bytes(self, data: bytes, at: QPointF | None = None):
        lr = self.doc.scene.label_rect()
        try:
            it = ImageItem.from_file_bytes(data, lr.width() - 8, min(lr.height(), 96) - 8)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, APP_NAME, f"Couldn't read that image:\n{e}")
            return
        g = self._center(it._w, it._h)
        if at is not None:
            g.moveCenter(at)
        it.set_geometry(g)
        self.doc.add_item(it)

    def replace_image(self, item: ImageItem):
        path, _ = QFileDialog.getOpenFileName(self, "Replace image", str(Path.home() / "Pictures"), IMAGE_FILTER)
        if not path:
            return
        try:
            new = ImageItem.from_file_bytes(Path(path).read_bytes(), 1000, 1000)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, APP_NAME, f"Couldn't read that image:\n{e}")
            return
        item.apply_props({"data": new.data, "dither": new.dither})
        g = item.geometry()
        aspect = item.natural_aspect() if item.rot in (0, 180) else 1 / item.natural_aspect()
        item.set_geometry(QRectF(g.x(), g.y(), g.height() * aspect, g.height()))
        self.doc.commit()
        self.props.rebuild()

    def insert_shape(self, shape):
        lr = self.doc.scene.label_rect()
        it = ShapeItem(shape)
        if shape == "rect":  # default rectangle = border around the printable area
            extra = max(0, lr.height() - 96) / 2
            g = QRectF(2, extra + 2, lr.width() - 4, lr.height() - 2 * extra - 4)
        elif shape == "ellipse":
            g = self._center(48, 48)
        else:
            g = self._center(lr.width() * 0.6, 4)
        it.set_geometry(g)
        self.doc.add_item(it)

    def insert_barcode(self, btype):
        lr = self.doc.scene.label_rect()
        h = min(lr.height(), 96) - 8
        if btype == "qr":
            it = BarcodeItem("qr", "BIN-{n:03}", h, h)
            it.set_geometry(QRectF(8, (lr.height() - h) / 2, h, h))
        else:
            it = BarcodeItem("code128", "BIN-{n:03}")
            it.set_geometry(self._center(lr.width() * 0.8, h))
        self.doc.add_item(it)

    def _on_drop(self, paths, pos):
        labels = [p for p in paths if p.lower().endswith(FILE_EXT)]
        if labels:
            self.open_path(Path(labels[0]))
            return
        for p in paths:
            if Path(p).suffix.lower() in IMAGE_EXTS:
                self._add_image_bytes(Path(p).read_bytes(), pos)

    def _on_double_click(self, item):
        if isinstance(item, TextItem):
            self.inspector_dock.show()
            self.inspector_dock.widget().setCurrentIndex(0)
            self.props.focus_text()
        elif isinstance(item, ImageItem):
            self.replace_image(item)

    def _context_menu(self, global_pos):
        m = QMenu(self)
        if self.selected():
            for a in (self.a_cut, self.a_copy, self.a_paste, self.a_dup, self.a_delete, None, self.a_ch, self.a_cv,
                      None, self.a_front, self.a_forward, self.a_backward, self.a_back, None, self.a_rot_r,
                      self.a_rot_l, self.a_lock):
                m.addSeparator() if a is None else m.addAction(a)
        else:
            for a in (self.a_paste, None, self.a_text, self.a_image, self.a_logo, self.a_rect, self.a_qr,
                      self.a_barcode, None, self.a_select_all, self.a_label_size):
                m.addSeparator() if a is None else m.addAction(a)
        m.exec(global_pos)

    # ======================================================================= arrange
    def _movable(self):
        return [i for i in self.selected() if not i.locked]

    def _move_to(self, it, x=None, y=None):
        g = it.geometry()
        it.setPos(g.x() if x is None else x, g.y() if y is None else y)

    def align(self, how):
        items = self._movable()
        if not items:
            return
        if len(self.selected()) == 1:
            ref = self.doc.scene.label_rect()
        else:
            ref = QRectF()
            for i in self.selected():
                ref = ref.united(i.geometry())
        for it in items:
            g = it.geometry()
            if how == "left":
                self._move_to(it, x=ref.left())
            elif how == "right":
                self._move_to(it, x=ref.right() - g.width())
            elif how == "hcenter":
                self._move_to(it, x=ref.center().x() - g.width() / 2)
            elif how == "top":
                self._move_to(it, y=ref.top())
            elif how == "bottom":
                self._move_to(it, y=ref.bottom() - g.height())
            elif how == "vcenter":
                self._move_to(it, y=ref.center().y() - g.height() / 2)
        self._after_arrange()

    def center_on_label(self, horizontal, vertical):
        items = self._movable()
        if not items:
            return
        union = QRectF()
        for i in items:
            union = union.united(i.geometry())
        lr = self.doc.scene.label_rect()
        dx = lr.center().x() - union.center().x() if horizontal else 0
        dy = lr.center().y() - union.center().y() if vertical else 0
        for it in items:
            it.moveBy(dx, dy)
        self._after_arrange()

    def distribute(self, axis):
        items = self._movable()
        if len(items) < 3:
            return
        if axis == "h":
            items.sort(key=lambda i: i.geometry().left())
            first, last = items[0].geometry(), items[-1].geometry()
            gap = (last.right() - first.left() - sum(i.geometry().width() for i in items)) / (len(items) - 1)
            x = first.left()
            for it in items:
                self._move_to(it, x=x)
                x += it.geometry().width() + gap
        else:
            items.sort(key=lambda i: i.geometry().top())
            first, last = items[0].geometry(), items[-1].geometry()
            gap = (last.bottom() - first.top() - sum(i.geometry().height() for i in items)) / (len(items) - 1)
            y = first.top()
            for it in items:
                self._move_to(it, y=y)
                y += it.geometry().height() + gap
        self._after_arrange()

    def restack(self, how):
        order = self.doc.scene.label_items()
        sel = [i for i in order if i.isSelected()]
        if not sel:
            return
        rest = [i for i in order if not i.isSelected()]
        if how == "front":
            order = rest + sel
        elif how == "back":
            order = sel + rest
        else:
            step = 1 if how == "forward" else -1
            seq = order if step > 0 else list(reversed(order))
            for i in range(len(seq) - 2, -1, -1):
                if seq[i].isSelected() and not seq[i + 1].isSelected():
                    seq[i], seq[i + 1] = seq[i + 1], seq[i]
            order = seq if step > 0 else list(reversed(seq))
        self.doc.restack(order)
        self._after_arrange()

    def rotate(self, deg):
        for it in self._movable():
            it.rotate_by(deg)
        self._after_arrange()

    def toggle_lock(self):
        items = self.selected()
        lock = not all(i.locked for i in items)
        for it in items:
            it.locked = lock
        self._after_arrange()
        self.props.rebuild()

    def _after_arrange(self):
        self.view.viewport().update()
        self.doc.commit()
        self.props.refresh()

    # ======================================================================= view
    def _zoom_actual(self):
        # "Actual size": 8 dots per mm shown at ~4 px per dot (≈ physical size on a ~100 dpi screen)
        self.view.fit_mode = False
        self.view.set_zoom(4.0)

    def _toggle_snap(self, on):
        self.view.snap_enabled = on
        self.settings["snap"] = on

    def _toggle_grid(self, on):
        self.view.grid_enabled = on
        self.settings["show_grid"] = on
        self.view.viewport().update()

    def _toggle_pixels(self, on):
        self.view.pixel_preview = on
        self.settings["pixel_preview"] = on
        self.view.viewport().update()

    # ======================================================================= label size
    def change_label_size(self):
        dlg = LabelSizeDialog(self, self.doc.label)
        if dlg.exec():
            self._set_label(dlg.value())

    def _set_label(self, size: LabelSize):
        old = self.doc.label
        self.doc.set_label(size)
        # keep content centered when the label grows or shrinks
        dx = (size.width_px - old.width_px) / 2
        dy = (size.height_px - old.height_px) / 2
        if dx or dy:
            for it in self.doc.scene.label_items():
                it.moveBy(dx, dy)
            self.doc.commit()
        self.view.zoom_fit()

    def use_loaded_label(self):
        if self.loaded_label:
            self._set_label(self.loaded_label)

    def _dismiss_banner(self):
        self._banner_dismissed_for = (self.loaded_label, self.doc.label)
        self.banner_wrap.hide()

    def _update_banner(self):
        lab = self.loaded_label
        show = bool(lab) and lab != self.doc.label and self._banner_dismissed_for != (lab, self.doc.label)
        if show:
            self.banner_text.setText(f"The printer has <b>{lab.name}</b> labels loaded, but this design is "
                                     f"<b>{self.doc.label.name}</b>.")
        self.banner_wrap.setVisible(show)
        self._update_actions()
        self.printer_panel.set_roll(self.rfid, lab, lab == self.doc.label) if self.service.connected else None

    # ======================================================================= printer
    def _printer_button(self):
        if self.service.state in ("connected", "connecting"):
            self.service.disconnect()
        else:
            self.connect_printer()

    def connect_printer(self):
        dlg = ConnectDialog(self, self.service)
        if dlg.exec() and dlg.selected():
            addr, name = dlg.selected()
            self.settings["printer_address"] = addr
            self.settings["printer_name"] = name
            self.service.connect_to(addr, name)

    def _on_printer_state(self, state, msg):
        self.printer_panel.set_state(state, msg, self.service.name or self.settings["printer_name"])
        if state == "connected":
            self.statusBar().showMessage(f"Connected to {self.service.name}", 4000)
            self.a_connect.setText("Re&connect…")
        if state != "connected":
            self.loaded_label = None
            self.rfid = None
            self._update_banner()

    def _on_device_info(self, info):
        self.device_info = info
        self.printer_panel.set_battery(info.battery)

    def _on_rfid(self, r):
        self.rfid = r
        if r is None:
            self.loaded_label = None
            if self.service.connected:
                self.printer_panel.set_roll(None, None, False)
            self._update_banner()
            return
        self.loaded_label = self.settings.label_for_barcode(r.barcode)
        if self.loaded_label is None:
            self._identify_roll()
        self._adopt_loaded_label()
        self.printer_panel.set_roll(r, self.loaded_label, self.loaded_label == self.doc.label)
        self._update_banner()

    def _adopt_loaded_label(self):
        """A new, untouched design follows the label roll that's in the printer."""
        if self.loaded_label and self.doc.path is None and self.doc.is_empty() and \
                self.doc.label != self.loaded_label:
            self.doc.new(self.loaded_label)
            self.props.rebuild()

    def _identify_roll(self, force=False):
        r = self.rfid
        if r is None or (r.barcode in self._asked_barcodes and not force):
            return
        self._asked_barcodes.add(r.barcode)
        dlg = NewRollDialog(self, r)
        if dlg.exec():
            self.settings.learn_barcode(r.barcode, dlg.value())
            self.loaded_label = dlg.value()
            self._adopt_loaded_label()
            self.printer_panel.set_roll(r, self.loaded_label, self.loaded_label == self.doc.label)
            self._update_banner()

    def open_settings(self):
        dlg = PrinterSettingsDialog(self, self.service, self.settings, self.device_info)
        dlg.calibration_print.connect(self._print_calibration)
        dlg.accent_changed.connect(self._set_accent)
        dlg.library_changed.connect(lambda d: self.library.set_folder(Path(d)))
        dlg.check_updates_requested.connect(lambda: self.check_updates(manual=True))
        dlg.exec()

    def _set_accent(self, accent):
        self.settings["accent"] = accent
        QMessageBox.information(self, APP_NAME, "The new accent color is applied the next time Niimbot Studio starts.")

    def _print_calibration(self, ox, oy):
        img = calibration_image(self.loaded_label or self.doc.label)
        self._run_print([img], 1, self.settings["density"], ox, oy)

    def print_label(self):
        self._commit_now()
        if self.doc.is_empty():
            QMessageBox.information(self, APP_NAME, "The label is empty. Add some text or an image first.")
            return
        if self.service.printing:
            return
        dlg = PrintDialog(self, lambda n: render_label(self.doc.scene, n), self.doc.label, self.settings,
                          self.service.connected, self.rfid, self.loaded_label, self.doc.uses_sequence())
        if not dlg.exec():
            return
        opts = dlg.result_options()
        images = [render_label(self.doc.scene, n) for n in opts["numbers"]]
        self._run_print(images, opts["copies"], opts["density"], self.settings["offset_x"], self.settings["offset_y"])

    def _run_print(self, images, copies, density, ox, oy):
        total = len(images) * copies
        prog = PrintProgressDialog(self, total)
        s = self.service
        s.print_progress.connect(prog.progress)
        s.print_finished.connect(prog.finished_print)
        prog.cancel_requested.connect(s.cancel_print)
        s.print_labels(images, copies, density, ox, oy)
        prog.exec()
        s.print_progress.disconnect(prog.progress)
        s.print_finished.disconnect(prog.finished_print)

    # ======================================================================= updates
    def _maybe_auto_check(self):
        if self.settings["check_updates"] and time.time() - (self.settings["last_update_check"] or 0) > 12 * 3600:
            self.check_updates(manual=False)

    def check_updates(self, manual=False):
        self._manual_check = manual
        if manual:
            self.statusBar().showMessage("Checking for updates…")
        self.updater.check()

    def _on_update_checked(self, release, error):
        self.settings["last_update_check"] = time.time()
        manual = getattr(self, "_manual_check", False)
        self.statusBar().clearMessage()
        if error:
            if manual:
                QMessageBox.information(self, APP_NAME, f"Couldn't check for updates right now.\n\n{error}")
            return
        if release is None:
            if manual:
                QMessageBox.information(self, APP_NAME, f"You're up to date (version {__version__}).")
            return
        if not manual and release.version == self.settings["skipped_version"]:
            return
        self._release = release
        self.update_text.setText(f"<b>Niimbot Studio {release.version}</b> is available. You have {__version__}.")
        self.update_btn.setText("Update now" if updater.can_self_update() else "Download")
        self.update_wrap.show()

    def _skip_update(self):
        if self._release:
            self.settings["skipped_version"] = self._release.version
        self.update_wrap.hide()

    def install_update(self):
        rel = self._release
        if rel is None:
            return
        if not updater.can_self_update():  # running from source: just open the release page
            webbrowser.open(rel.page_url)
            return
        if not self._maybe_save():
            return
        dlg = QProgressDialog(f"Downloading Niimbot Studio {rel.version}…", "Cancel", 0, max(1, rel.size), self)
        dlg.setWindowTitle("Updating")
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.canceled.connect(self.updater.cancel)

        def progress(done, total):
            dlg.setMaximum(max(1, total))
            dlg.setValue(done)

        def finished(path, error):
            self.updater.download_progress.disconnect(progress)
            self.updater.downloaded.disconnect(finished)
            dlg.close()
            if error:
                if error != "Cancelled":
                    QMessageBox.warning(self, APP_NAME, f"The download failed:\n{error}")
                return
            updater.launch_installer(path)
            self._skip_save_prompt = True
            self.close()

        self.updater.download_progress.connect(progress)
        self.updater.downloaded.connect(finished)
        self.updater.download(rel)
        dlg.show()

    def about(self):
        b = theme.brand()
        extra = f"<br>Brand pack: {b.name}" if b.name else ""
        QMessageBox.about(self, f"About {APP_NAME}",
                          f"<h3>{APP_NAME} {__version__}</h3>"
                          "<p>Offline label designer and Bluetooth driver for Niimbot D11 printers. "
                          "No account or cloud service required.</p>"
                          f"<p><a href='https://github.com/{GITHUB_REPO}'>github.com/{GITHUB_REPO}</a>{extra}</p>"
                          "<p style='color:gray'>Not affiliated with or endorsed by NIIMBOT.</p>")

    # ======================================================================= lifecycle
    def closeEvent(self, e):
        if not getattr(self, "_skip_save_prompt", False) and not self._maybe_save():
            e.ignore()
            return
        self.settings["window_geometry"] = bytes(self.saveGeometry().toBase64()).decode()
        self.settings["window_state"] = bytes(self.saveState().toBase64()).decode()
        self.teardown()
        e.accept()

    def teardown(self):
        """Stop background work and silence the scene before Qt starts destroying items.

        Item deletion during shutdown emits selectionChanged; handlers touching
        half-destroyed items can crash the process.
        """
        if getattr(self, "_torn_down", False):
            return
        self._torn_down = True
        self._preview_timer.stop()
        self._commit_timer.stop()
        self.doc.scene.blockSignals(True)
        self.service.shutdown()

    def showEvent(self, e):
        super().showEvent(e)
        QTimer.singleShot(0, self.view.zoom_fit)
        if not getattr(self, "_update_scheduled", False):
            self._update_scheduled = True
            QTimer.singleShot(4000, self._maybe_auto_check)
        if not self.settings["printer_address"]:
            QTimer.singleShot(600, self.connect_printer)


def _install_crash_handler():
    """The packaged app has no console: log unexpected errors and tell the user where to find them."""
    import traceback

    from .config import APP_DIR
    log = APP_DIR / "crash.log"

    def hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            APP_DIR.mkdir(parents=True, exist_ok=True)
            with open(log, "a", encoding="utf-8") as f:
                f.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')}  v{__version__}\n{text}")
        except OSError:
            pass
        if QApplication.instance():
            QMessageBox.critical(None, APP_NAME, f"Something went wrong:\n{exc}\n\nDetails were saved to:\n{log}")

    sys.excepthook = hook


def main():
    _install_crash_handler()
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NiimbotStudio.App")
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    settings = Settings()
    theme.apply(app, settings["accent"] or "blue")
    win = MainWindow(settings)
    app.aboutToQuit.connect(win.teardown)
    win.show()
    for arg in sys.argv[1:]:
        if arg.lower().endswith(FILE_EXT) and Path(arg).exists():
            win.open_path(Path(arg), ask=False)
            break
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
