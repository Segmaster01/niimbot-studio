"""Label document: scene contents, label size, file I/O, undo history and 1-bit rendering."""
from __future__ import annotations

import base64
import json
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QObject, QRectF, Signal
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QGraphicsScene

from . import items as items_mod
from .config import DEFAULT_LABEL, FILE_EXT, LabelSize
from .items import LabelItem, item_from_dict

FORMAT_VERSION = 1


class LabelScene(QGraphicsScene):
    def __init__(self, label: LabelSize):
        super().__init__()
        self.label = label
        self.update_rect()

    def update_rect(self):
        w, h = self.label.width_px, self.label.height_px
        m = max(w, h)
        self.setSceneRect(QRectF(-m, -m, w + 2 * m, h + 2 * m))

    def label_rect(self) -> QRectF:
        return QRectF(0, 0, self.label.width_px, self.label.height_px)

    def label_items(self) -> list[LabelItem]:
        """Elements in paint order (bottom first)."""
        return sorted((i for i in self.items() if isinstance(i, LabelItem)), key=lambda i: i.zValue())

    def selected_label_items(self) -> list[LabelItem]:
        return [i for i in self.label_items() if i.isSelected()]


def render_label(scene: LabelScene, n: int | None = None) -> Image.Image:
    """Render the label to a 1-bit PIL image at printer resolution (black = print)."""
    w, h = scene.label.width_px, scene.label.height_px
    img = QImage(w, h, QImage.Format_Grayscale8)
    img.fill(255)
    old_n = items_mod.variables.get("n")
    if n is not None:
        items_mod.variables["n"] = n
    items_mod.rendering_output = True
    try:
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        scene.render(p, QRectF(0, 0, w, h), QRectF(0, 0, w, h))
        p.end()
    finally:
        items_mod.rendering_output = False
        items_mod.variables["n"] = old_n
    bpl = img.bytesPerLine()
    raw = bytes(img.constBits())[: bpl * h]
    gray = Image.frombytes("L", (w, h), raw, "raw", "L", bpl)
    return gray.point(lambda v: 255 if v >= 128 else 0).convert("1", dither=Image.NONE)


def mono_to_qimage(img: Image.Image) -> QImage:
    g = img.convert("L")
    return QImage(g.tobytes(), g.width, g.height, g.width, QImage.Format_Grayscale8).copy()


class Document(QObject):
    changed = Signal()          # any content change (including undo/redo)
    label_changed = Signal()    # label size changed
    state_changed = Signal()    # dirty flag / path / undo availability

    HISTORY_LIMIT = 200

    def __init__(self, label: LabelSize = DEFAULT_LABEL):
        super().__init__()
        self.scene = LabelScene(label)
        self.path: Path | None = None
        self._history: list[dict] = []
        self._index = -1
        self._saved_index = 0
        self._z = 0
        self.reset_history()

    # ---------------------------------------------------------------- props
    @property
    def label(self) -> LabelSize:
        return self.scene.label

    def set_label(self, label: LabelSize, commit=True):
        if label == self.scene.label:
            return
        self.scene.label = label
        self.scene.update_rect()
        self.scene.update()
        self.label_changed.emit()
        if commit:
            self.commit()

    @property
    def dirty(self) -> bool:
        return self._index != self._saved_index

    @property
    def title(self) -> str:
        return self.path.stem if self.path else "Untitled"

    def is_empty(self) -> bool:
        return not self.scene.label_items()

    def uses_sequence(self) -> bool:
        return any(i.uses_sequence() for i in self.scene.label_items())

    # ---------------------------------------------------------------- items
    def add_item(self, item: LabelItem, select=True, commit=True):
        self._z += 1
        item.setZValue(self._z)
        self.scene.addItem(item)
        if select:
            self.scene.clearSelection()
            item.setSelected(True)
        if commit:
            self.commit()

    def remove_items(self, items: list[LabelItem]):
        if not items:
            return
        for it in items:
            self.scene.removeItem(it)
        self.commit()

    def restack(self, ordered: list[LabelItem]):
        for z, it in enumerate(ordered, 1):
            it.setZValue(z)
        self._z = len(ordered)

    # ---------------------------------------------------------------- history
    def snapshot(self) -> dict:
        return {"label": self.label.to_dict(),
                "items": [i.to_dict() for i in self.scene.label_items()]}

    def reset_history(self):
        self._history = [self.snapshot()]
        self._index = 0
        self._saved_index = 0
        self.state_changed.emit()

    def commit(self):
        snap = self.snapshot()
        if snap == self._history[self._index]:
            self.changed.emit()
            return
        del self._history[self._index + 1:]
        self._history.append(snap)
        if len(self._history) > self.HISTORY_LIMIT:
            self._history.pop(0)
            self._saved_index -= 1
        self._index = len(self._history) - 1
        self.changed.emit()
        self.state_changed.emit()

    def can_undo(self):
        return self._index > 0

    def can_redo(self):
        return self._index < len(self._history) - 1

    def undo(self):
        if self.can_undo():
            self._index -= 1
            self._restore(self._history[self._index])

    def redo(self):
        if self.can_redo():
            self._index += 1
            self._restore(self._history[self._index])

    def _restore(self, snap: dict):
        selected = {i.uid for i in self.scene.selected_label_items()}
        self.load_snapshot(snap)
        for it in self.scene.label_items():
            it.setSelected(it.uid in selected)
        self.changed.emit()
        self.state_changed.emit()

    def load_snapshot(self, snap: dict):
        self.scene.clearSelection()
        for it in self.scene.label_items():
            self.scene.removeItem(it)
        label = LabelSize.from_dict(snap["label"])
        if label != self.scene.label:
            self.scene.label = label
            self.scene.update_rect()
            self.label_changed.emit()
        self._z = 0
        for d in snap["items"]:
            item = item_from_dict(d)
            self._z += 1
            item.setZValue(self._z)
            self.scene.addItem(item)
        self.scene.update()

    # ---------------------------------------------------------------- files
    def save(self, path: Path):
        snap = self.snapshot()
        for d in snap["items"]:
            if isinstance(d.get("data"), (bytes, bytearray)):
                d["data"] = {"b64": base64.b64encode(d["data"]).decode()}
        doc = {"format": "niimbot-studio-label", "version": FORMAT_VERSION, **snap}
        path = Path(path)
        if path.suffix.lower() != FILE_EXT:
            path = path.with_suffix(FILE_EXT)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
        tmp.replace(path)
        self.path = path
        self._saved_index = self._index
        self.state_changed.emit()
        return path

    @staticmethod
    def read_file(path: Path) -> dict:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        if doc.get("format") != "niimbot-studio-label":
            raise ValueError("Not a Niimbot Studio label file")
        for d in doc["items"]:
            if isinstance(d.get("data"), dict) and "b64" in d["data"]:
                d["data"] = base64.b64decode(d["data"]["b64"])
        return {"label": doc["label"], "items": doc["items"]}

    def open(self, path: Path):
        snap = self.read_file(path)
        self.load_snapshot(snap)
        self.path = Path(path)
        self.reset_history()
        self.changed.emit()

    def new(self, label: LabelSize):
        self.scene.clearSelection()
        for it in self.scene.label_items():
            self.scene.removeItem(it)
        self.path = None
        self.set_label(label, commit=False)
        self.reset_history()
        self.changed.emit()
