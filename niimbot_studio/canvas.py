"""Label canvas view: selection, move/resize handles, smart snapping guides, zoom, pan."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QCursor, QImage, QKeyEvent, QPainter, QPainterPath,
                           QPen)
from PySide6.QtWidgets import QFrame, QGraphicsView

from . import theme
from .document import Document
from .driver import PRINTHEAD_PX
from .items import LabelItem

HANDLE_PX = 8
SNAP_PX = 8
CENTER_GUIDE = QColor("#FF4FD8")
HANDLE_CURSORS = [Qt.SizeFDiagCursor, Qt.SizeVerCursor, Qt.SizeBDiagCursor, Qt.SizeHorCursor,
                  Qt.SizeFDiagCursor, Qt.SizeVerCursor, Qt.SizeBDiagCursor, Qt.SizeHorCursor]
# Which edges each handle moves: (left, top, right, bottom)
HANDLE_EDGES = [(1, 1, 0, 0), (0, 1, 0, 0), (0, 1, 1, 0), (0, 0, 1, 0),
                (0, 0, 1, 1), (0, 0, 0, 1), (1, 0, 0, 1), (1, 0, 0, 0)]


class LabelView(QGraphicsView):
    edited = Signal()                    # a move/resize/nudge finished
    cursor_moved = Signal(float, float)  # scene dots
    zoom_changed = Signal(float)
    files_dropped = Signal(list, QPointF)
    item_double_clicked = Signal(object)
    context_menu = Signal(object)        # global QPoint

    def __init__(self, doc: Document):
        super().__init__(doc.scene)
        self.doc = doc
        self.setFrameShape(QFrame.NoFrame)
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setRubberBandSelectionMode(Qt.IntersectsItemShape)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.StrongFocus)

        self.snap_enabled = True
        self.grid_enabled = False
        self.grid_dots = 8
        self.pixel_preview = False
        self.preview_image: QImage | None = None
        self.fit_mode = True

        self._mode = None          # None / "move" / "resize" / "pan"
        self._press_scene = QPointF()
        self._start_geoms: dict = {}
        self._handle = -1
        self._guides: list[tuple[str, float, QColor]] = []
        self._moved = False
        self._hover_item = None
        self._pan_start = None
        doc.scene.selectionChanged.connect(self.viewport().update)
        doc.label_changed.connect(self.zoom_fit)

    # ================================================================ zoom
    def zoom(self) -> float:
        return self.transform().m11()

    def set_zoom(self, z: float, anchor_mouse=False):
        z = max(0.5, min(40.0, z))
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse if anchor_mouse
                                     else QGraphicsView.AnchorViewCenter)
        self.resetTransform()
        self.scale(z, z)
        self.zoom_changed.emit(z)
        self.viewport().update()

    def zoom_fit(self):
        self.fit_mode = True
        r = self.doc.scene.label_rect()
        vw, vh = self.viewport().width() - 120, self.viewport().height() - 120
        if vw <= 0 or vh <= 0:
            return
        z = min(vw / r.width(), vh / r.height())
        self.set_zoom(z)
        self.centerOn(r.center())

    def zoom_step(self, factor: float, anchor_mouse=False):
        self.fit_mode = False
        self.set_zoom(self.zoom() * factor, anchor_mouse)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.fit_mode:
            self.zoom_fit()

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            self.zoom_step(1.15 if e.angleDelta().y() > 0 else 1 / 1.15, anchor_mouse=True)
        elif e.modifiers() & Qt.ShiftModifier:
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - e.angleDelta().y())
        else:
            super().wheelEvent(e)

    # ================================================================ painting
    def drawBackground(self, p: QPainter, rect: QRectF):
        k = theme.colors()
        p.fillRect(rect, QColor(k["canvas"]))
        lr = self.doc.scene.label_rect()
        z = self.zoom()
        radius = 1.5 * 8  # rounded label corners (~1.5 mm)

        # soft shadow
        for i, a in enumerate((28, 18, 10)):
            d = (i + 1) * 2 / z
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, a))
            p.drawRoundedRect(lr.adjusted(-d, -d + 2 / z, d, d + 3 / z), radius + d, radius + d)
        p.setBrush(QColor("#FFFFFF"))
        p.drawRoundedRect(lr, radius, radius)

        # Areas the 12 mm printhead cannot reach on taller labels.
        extra = lr.height() - PRINTHEAD_PX
        if extra > 0:
            band = extra / 2
            hatch = QBrush(QColor(0, 0, 0, 30), Qt.BDiagPattern)
            hatch.setTransform(hatch.transform().scale(1 / z, 1 / z))
            path = QPainterPath()
            path.addRoundedRect(lr, radius, radius)
            p.save()
            p.setClipPath(path)
            p.fillRect(QRectF(lr.left(), lr.top(), lr.width(), band), hatch)
            p.fillRect(QRectF(lr.left(), lr.bottom() - band, lr.width(), band), hatch)
            p.restore()

        if self.grid_enabled and self.grid_dots * z >= 5:
            p.save()
            p.setPen(QPen(QColor(0, 0, 0, 55), 0))
            step = self.grid_dots
            pts = []
            x = step
            while x < lr.width():
                y = step
                while y < lr.height():
                    pts.append(QPointF(x, y))
                    y += step
                x += step
            pen = QPen(QColor(0, 0, 0, 70), 1.6 / z)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawPoints(pts)
            p.restore()

        # subtle center ticks on the label edges
        p.setPen(QPen(QColor(k["accent"]), 0))
        t = 6 / z
        cx, cy = lr.center().x(), lr.center().y()
        for a, b in (((cx, lr.top()), (cx, lr.top() + t)), ((cx, lr.bottom()), (cx, lr.bottom() - t)),
                     ((lr.left(), cy), (lr.left() + t, cy)), ((lr.right(), cy), (lr.right() - t, cy))):
            p.drawLine(QPointF(*a), QPointF(*b))

    def drawForeground(self, p: QPainter, rect: QRectF):
        k = theme.colors()
        z = self.zoom()
        lr = self.doc.scene.label_rect()
        if self.pixel_preview and self.preview_image is not None:
            p.save()
            p.setRenderHint(QPainter.SmoothPixmapTransform, False)
            path = QPainterPath()
            path.addRoundedRect(lr, 12, 12)
            p.setClipPath(path)
            p.drawImage(lr, self.preview_image)
            p.restore()

        accent = QColor(k["accent"])
        if self._hover_item is not None and not self._hover_item.isSelected() and self._mode is None:
            p.setPen(QPen(QColor(k["accent_light"]), 1 / z, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(self._hover_item.geometry())

        sel = self.doc.scene.selected_label_items()
        p.setBrush(Qt.NoBrush)
        for it in sel:
            pen = QPen(accent, 1.5 / z)
            if it.locked:
                pen.setStyle(Qt.DashLine)
            p.setPen(pen)
            p.drawRect(it.geometry())
        if len(sel) > 1:
            p.setPen(QPen(accent, 1 / z, Qt.DotLine))
            p.drawRect(self._union(sel).adjusted(-3 / z, -3 / z, 3 / z, 3 / z))
        if len(sel) == 1 and not sel[0].locked:
            hs = HANDLE_PX / z
            p.setPen(QPen(accent, 1.2 / z))
            p.setBrush(QColor("#FFFFFF"))
            for pt in self._handle_points(sel[0].geometry()):
                p.drawRoundedRect(QRectF(pt.x() - hs / 2, pt.y() - hs / 2, hs, hs), 1.5 / z, 1.5 / z)

        for orient, v, color in self._guides:
            pen = QPen(color, 1 / z)
            p.setPen(pen)
            if orient == "v":
                p.drawLine(QPointF(v, lr.top() - 14 / z), QPointF(v, lr.bottom() + 14 / z))
            else:
                p.drawLine(QPointF(lr.left() - 14 / z, v), QPointF(lr.right() + 14 / z, v))

        if self._mode == "move" and sel:
            self._draw_badge(p, self._union(sel), f"{self._union(sel).x() / 8:.2f}, {self._union(sel).y() / 8:.2f} mm")
        elif self._mode == "resize" and sel:
            g = sel[0].geometry()
            self._draw_badge(p, g, f"{g.width() / 8:.2f} × {g.height() / 8:.2f} mm")

    def _draw_badge(self, p, g: QRectF, text):
        z = self.zoom()
        p.save()
        p.resetTransform()
        anchor = self.mapFromScene(QPointF(g.center().x(), g.bottom()))
        f = p.font()
        f.setPixelSize(11)
        p.setFont(f)
        w = p.fontMetrics().horizontalAdvance(text) + 14
        r = QRectF(anchor.x() - w / 2, anchor.y() + 10, w, 20)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.colors()["accent"]))
        p.drawRoundedRect(r, 4, 4)
        p.setPen(QColor("white"))
        p.drawText(r, Qt.AlignCenter, text)
        p.restore()
        del z

    # ================================================================ helpers
    @staticmethod
    def _union(items) -> QRectF:
        r = QRectF()
        for it in items:
            r = r.united(it.geometry())
        return r

    @staticmethod
    def _handle_points(g: QRectF):
        l, t, r, b = g.left(), g.top(), g.right(), g.bottom()
        cx, cy = g.center().x(), g.center().y()
        return [QPointF(l, t), QPointF(cx, t), QPointF(r, t), QPointF(r, cy),
                QPointF(r, b), QPointF(cx, b), QPointF(l, b), QPointF(l, cy)]

    def _handle_at(self, scene_pt: QPointF) -> int:
        sel = self.doc.scene.selected_label_items()
        if len(sel) != 1 or sel[0].locked:
            return -1
        tol = (HANDLE_PX / 2 + 3) / self.zoom()
        for i, pt in enumerate(self._handle_points(sel[0].geometry())):
            if abs(pt.x() - scene_pt.x()) <= tol and abs(pt.y() - scene_pt.y()) <= tol:
                return i
        return -1

    def _item_at(self, scene_pt: QPointF) -> LabelItem | None:
        # Prefer an already-selected item so overlapping selections stay draggable.
        candidates = [i for i in reversed(self.doc.scene.label_items()) if i.geometry().contains(scene_pt)]
        for it in candidates:
            if it.isSelected():
                return it
        return candidates[0] if candidates else None

    # ================================================================ snapping
    def _targets(self, exclude):
        lr = self.doc.scene.label_rect()
        xs = [(0.0, CENTER_GUIDE, 0), (lr.width() / 2, CENTER_GUIDE, 2), (lr.width(), CENTER_GUIDE, 0)]
        ys = [(0.0, CENTER_GUIDE, 0), (lr.height() / 2, CENTER_GUIDE, 2), (lr.height(), CENTER_GUIDE, 0)]
        extra = lr.height() - PRINTHEAD_PX
        if extra > 0:  # printable-area edges on tall labels
            ys += [(extra / 2, CENTER_GUIDE, 0), (lr.height() - extra / 2, CENTER_GUIDE, 0)]
        item_color = QColor(theme.colors()["accent_light"])
        for it in self.doc.scene.label_items():
            if it in exclude:
                continue
            g = it.geometry()
            xs += [(g.left(), item_color, 1), (g.center().x(), item_color, 1), (g.right(), item_color, 1)]
            ys += [(g.top(), item_color, 1), (g.center().y(), item_color, 1), (g.bottom(), item_color, 1)]
        return xs, ys

    def _best(self, values, targets, th):
        """Find the closest (delta, target) for any of `values` against `targets`."""
        best = None
        for v in values:
            for t, color, prio in targets:
                d = t - v
                if abs(d) <= th:
                    score = abs(d) - prio * 0.01
                    if best is None or score < best[0]:
                        best = (score, d, t, color)
        return best

    def _snap_disabled(self):
        from PySide6.QtWidgets import QApplication
        return not self.snap_enabled or QApplication.keyboardModifiers() & Qt.AltModifier

    def _snap_move(self, r: QRectF, exclude) -> tuple[float, float]:
        self._guides = []
        if self._snap_disabled():
            return 0.0, 0.0
        th = SNAP_PX / self.zoom()
        xs, ys = self._targets(exclude)
        dx = dy = 0.0
        bx = self._best([r.left(), r.center().x(), r.right()], xs, th)
        by = self._best([r.top(), r.center().y(), r.bottom()], ys, th)
        if bx:
            dx = bx[1]
        elif self.grid_enabled:
            dx = round(r.left() / self.grid_dots) * self.grid_dots - r.left()
        if by:
            dy = by[1]
        elif self.grid_enabled:
            dy = round(r.top() / self.grid_dots) * self.grid_dots - r.top()
        moved = r.translated(dx, dy)
        # show every guide the snapped rect now touches
        for vals, targets, orient in (([moved.left(), moved.center().x(), moved.right()], xs, "v"),
                                      ([moved.top(), moved.center().y(), moved.bottom()], ys, "h")):
            seen = set()
            for v in vals:
                for t, color, _ in targets:
                    if abs(t - v) < 0.01 and round(t, 2) not in seen:
                        seen.add(round(t, 2))
                        self._guides.append((orient, t, color))
        return dx, dy

    def _snap_value(self, v, targets, th, orient):
        if self._snap_disabled():
            return v
        b = self._best([v], targets, th)
        if b:
            self._guides.append((orient, b[2], b[3]))
            return b[2]
        if self.grid_enabled:
            return round(v / self.grid_dots) * self.grid_dots
        return v

    # ================================================================ mouse
    def mousePressEvent(self, e):
        sp = self.mapToScene(e.position().toPoint())
        self._press_scene = sp
        self._moved = False
        if e.button() == Qt.MiddleButton or (e.button() == Qt.LeftButton and e.modifiers() & Qt.ControlModifier
                                             and e.modifiers() & Qt.ShiftModifier):
            self._mode = "pan"
            self._pan_start = e.position()
            self.viewport().setCursor(Qt.ClosedHandCursor)
            return
        if e.button() == Qt.RightButton:
            it = self._item_at(sp)
            if it and not it.isSelected():
                self.doc.scene.clearSelection()
                it.setSelected(True)
            self.context_menu.emit(e.globalPosition().toPoint())
            return
        if e.button() != Qt.LeftButton:
            return super().mousePressEvent(e)

        h = self._handle_at(sp)
        if h >= 0:
            item = self.doc.scene.selected_label_items()[0]
            self._mode = "resize"
            self._handle = h
            self._start_geoms = {item: item.geometry()}
            return
        it = self._item_at(sp)
        if it is not None:
            if e.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier):
                it.setSelected(not it.isSelected())
                return
            if not it.isSelected():
                self.doc.scene.clearSelection()
                it.setSelected(True)
            sel = self.doc.scene.selected_label_items()
            if any(not s.locked for s in sel):
                self._mode = "move"
                self._start_geoms = {s: s.geometry() for s in sel if not s.locked}
            return
        self._mode = None
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        sp = self.mapToScene(e.position().toPoint())
        self.cursor_moved.emit(sp.x(), sp.y())
        if self._mode == "pan":
            d = e.position() - self._pan_start
            self._pan_start = e.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(d.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(d.y()))
            return
        if self._mode == "move":
            delta = sp - self._press_scene
            if not self._moved and (abs(delta.x()) + abs(delta.y())) * self.zoom() < 3:
                return
            self._moved = True
            items = list(self._start_geoms)
            union = QRectF()
            for g in self._start_geoms.values():
                union = union.united(g)
            if e.modifiers() & Qt.ShiftModifier:  # constrain to one axis
                if abs(delta.x()) > abs(delta.y()):
                    delta.setY(0)
                else:
                    delta.setX(0)
            moved = union.translated(delta)
            dx, dy = self._snap_move(moved, set(items))
            for it, g in self._start_geoms.items():
                it.setPos(g.topLeft() + delta + QPointF(dx, dy))
            self.viewport().update()
            return
        if self._mode == "resize":
            self._do_resize(sp, e.modifiers())
            self._moved = True
            return
        super().mouseMoveEvent(e)
        if self._mode is None and not (e.buttons() & Qt.LeftButton):
            h = self._handle_at(sp)
            it = self._item_at(sp)
            if h >= 0:
                self.viewport().setCursor(HANDLE_CURSORS[h])
            elif it is not None and not it.locked:
                self.viewport().setCursor(Qt.SizeAllCursor)
            else:
                self.viewport().setCursor(Qt.ArrowCursor)
            if it is not self._hover_item:
                self._hover_item = it
                self.viewport().update()

    def _do_resize(self, sp: QPointF, mods):
        item, g0 = next(iter(self._start_geoms.items()))
        el, et, er, eb = HANDLE_EDGES[self._handle]
        self._guides = []
        th = SNAP_PX / self.zoom()
        xs, ys = self._targets({item})
        left, top, right, bottom = g0.left(), g0.top(), g0.right(), g0.bottom()
        if el:
            left = self._snap_value(sp.x(), xs, th, "v")
        if er:
            right = self._snap_value(sp.x(), xs, th, "v")
        if et:
            top = self._snap_value(sp.y(), ys, th, "h")
        if eb:
            bottom = self._snap_value(sp.y(), ys, th, "h")
        m = item.MIN_SIZE
        if right - left < m:
            if el:
                left = right - m
            else:
                right = left + m
        if bottom - top < m:
            if et:
                top = bottom - m
            else:
                bottom = top + m
        keep = bool(item.KEEP_ASPECT) != bool(mods & Qt.ShiftModifier)
        if keep and g0.height() > 0:
            aspect = g0.width() / g0.height()
            w, h = right - left, bottom - top
            if (el or er) and (et or eb):      # corner: follow the dominant axis
                if w / aspect > h:
                    h = w / aspect
                else:
                    w = h * aspect
            elif el or er:
                h = w / aspect
            else:
                w = h * aspect
            if el:
                left = right - w
            else:
                right = left + w
            if et:
                top = bottom - h
            else:
                bottom = top + h
            if not (et or eb):  # edge handle: keep vertical center
                c = g0.center().y()
                top, bottom = c - h / 2, c + h / 2
            if not (el or er):
                c = g0.center().x()
                left, right = c - w / 2, c + w / 2
        item.set_geometry(QRectF(QPointF(left, top), QPointF(right, bottom)))
        self.viewport().update()

    def mouseReleaseEvent(self, e):
        if self._mode == "pan":
            self._mode = None
            self.viewport().setCursor(Qt.ArrowCursor)
            return
        if self._mode in ("move", "resize"):
            changed = self._moved
            self._mode = None
            self._guides = []
            self._start_geoms = {}
            self.viewport().update()
            if changed:
                self.edited.emit()
            return
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        it = self._item_at(self.mapToScene(e.position().toPoint()))
        if it is not None:
            self.item_double_clicked.emit(it)
        else:
            super().mouseDoubleClickEvent(e)

    def leaveEvent(self, e):
        self._hover_item = None
        self.viewport().update()
        super().leaveEvent(e)

    # ================================================================ keyboard
    def keyPressEvent(self, e: QKeyEvent):
        steps = {Qt.Key_Left: (-1, 0), Qt.Key_Right: (1, 0), Qt.Key_Up: (0, -1), Qt.Key_Down: (0, 1)}
        if e.key() in steps:
            sel = [i for i in self.doc.scene.selected_label_items() if not i.locked]
            if sel:
                mult = 8 if e.modifiers() & Qt.ShiftModifier else 1
                dx, dy = steps[e.key()]
                for it in sel:
                    it.moveBy(dx * mult, dy * mult)
                self.viewport().update()
                self._nudged = True
                return
        if e.key() == Qt.Key_Escape:
            self.doc.scene.clearSelection()
            return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e: QKeyEvent):
        arrows = (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down)
        if e.key() in arrows and not e.isAutoRepeat() and getattr(self, "_nudged", False):
            self._nudged = False
            self.edited.emit()  # one undo step per nudge gesture
            return
        super().keyReleaseEvent(e)

    # ================================================================ drag & drop
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() or e.mimeData().hasImage():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths, self.mapToScene(e.position().toPoint()))
            e.acceptProposedAction()

    def set_preview(self, img: QImage | None):
        self.preview_image = img
        if self.pixel_preview:
            self.viewport().update()

    def cursor_scene_pos(self) -> QPointF:
        return self.mapToScene(self.viewport().mapFromGlobal(QCursor.pos()))
