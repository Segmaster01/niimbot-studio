"""Label elements. Scene units are printer dots (8 dots = 1 mm)."""
from __future__ import annotations

import datetime
import io
import re
import uuid

from PIL import Image, ImageEnhance, ImageOps
from PySide6.QtCore import QRectF, QSizeF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QImage, QPainter, QPen
from PySide6.QtWidgets import QGraphicsItem, QGraphicsObject

BLACK = QColor(0, 0, 0)
WHITE = QColor(255, 255, 255)
PT_TO_DOTS = 203 / 72

# Values substituted into {n}, {date}, {time} placeholders. The print dialog
# changes "n" for each label of a numbered run.
variables = {"n": 1}
# True while rendering the 1-bit print image (suppresses on-screen-only hints).
rendering_output = False

_VAR_RE = re.compile(r"\{(n|date|time)(?::(\d+))?\}")


def expand(text: str) -> str:
    def sub(m):
        name, width = m.group(1), m.group(2)
        if name == "n":
            return str(variables.get("n", 1)).zfill(int(width or 0))
        now = datetime.datetime.now()
        return now.strftime("%Y-%m-%d") if name == "date" else now.strftime("%H:%M")
    return _VAR_RE.sub(sub, text)


def has_sequence(text: str) -> bool:
    return any(m.group(1) == "n" for m in _VAR_RE.finditer(text or ""))


class LabelItem(QGraphicsObject):
    KIND = ""
    KEEP_ASPECT = False
    MIN_SIZE = 4

    def __init__(self, w: float = 80, h: float = 40):
        super().__init__()
        self._w, self._h = float(w), float(h)
        self.rot = 0  # 0 / 90 / 180 / 270, clockwise
        self.locked = False
        self.name = ""
        self.uid = uuid.uuid4().hex
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)

    # ------------------------------------------------------------- geometry
    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self._w, self._h)

    def geometry(self) -> QRectF:
        return QRectF(self.pos(), QSizeF(self._w, self._h))

    def set_geometry(self, r: QRectF):
        self.prepareGeometryChange()
        self.setPos(r.topLeft())
        self._w = max(self.MIN_SIZE, r.width())
        self._h = max(self.MIN_SIZE, r.height())
        self.invalidate()

    def content_size(self) -> tuple[float, float]:
        return (self._h, self._w) if self.rot in (90, 270) else (self._w, self._h)

    def rotate_by(self, deg: int):
        g = self.geometry()
        c = g.center()
        self.rot = (self.rot + deg) % 360
        if deg % 180:
            g = QRectF(0, 0, g.height(), g.width())
            g.moveCenter(c)
        self.set_geometry(g)

    def invalidate(self):
        self.update()

    # ------------------------------------------------------------- painting
    def paint(self, p: QPainter, option, widget=None):
        cw, ch = self.content_size()
        p.save()
        p.translate(self._w / 2, self._h / 2)
        p.rotate(self.rot)
        p.translate(-cw / 2, -ch / 2)
        self.paint_content(p, cw, ch)
        p.restore()

    def paint_content(self, p: QPainter, w: float, h: float):
        raise NotImplementedError

    def display_name(self) -> str:
        return self.name or self.KIND.title()

    def uses_sequence(self) -> bool:
        return False

    # ------------------------------------------------------------- serialization
    def props(self) -> dict:
        return {}

    def apply_props(self, d: dict):
        pass

    def to_dict(self) -> dict:
        return {"kind": self.KIND, "uid": self.uid, "x": self.x(), "y": self.y(),
                "w": self._w, "h": self._h, "rot": self.rot, "locked": self.locked,
                "name": self.name, **self.props()}

    def load_dict(self, d: dict):
        self.uid = d.get("uid", self.uid)
        self.rot = int(d.get("rot", 0))
        self.locked = bool(d.get("locked", False))
        self.name = d.get("name", "")
        self.apply_props(d)
        self.set_geometry(QRectF(d["x"], d["y"], d["w"], d["h"]))


# =========================================================================== text
class TextItem(LabelItem):
    KIND = "text"

    def __init__(self, text="Text", w=160, h=40):
        super().__init__(w, h)
        self.text = text
        self.family = "Segoe UI"
        self.size_pt = 14.0
        self.bold = True
        self.italic = False
        self.underline = False
        self.halign = "center"   # left / center / right
        self.valign = "middle"   # top / middle / bottom
        self.autofit = True      # shrink text to fit the box
        self.invert = False      # white text on black
        self.letter_spacing = 0.0  # percent
        self._fit_cache = None

    def display_name(self):
        if self.name:
            return self.name
        first = self.text.strip().splitlines()[0] if self.text.strip() else "Text"
        return f"“{first[:24]}”"

    def uses_sequence(self):
        return has_sequence(self.text)

    def props(self):
        return {k: getattr(self, k) for k in (
            "text", "family", "size_pt", "bold", "italic", "underline", "halign", "valign",
            "autofit", "invert", "letter_spacing")}

    def apply_props(self, d):
        for k, v in self.props().items():
            setattr(self, k, type(v)(d.get(k, v)))

    def invalidate(self):
        self._fit_cache = None
        self.update()

    def _flags(self):
        h = {"left": Qt.AlignLeft, "center": Qt.AlignHCenter, "right": Qt.AlignRight}[self.halign]
        v = {"top": Qt.AlignTop, "middle": Qt.AlignVCenter, "bottom": Qt.AlignBottom}[self.valign]
        return h | v | Qt.TextWordWrap

    def make_font(self, px: float) -> QFont:
        f = QFont(self.family)
        f.setPixelSize(max(1, round(px)))
        f.setBold(self.bold)
        f.setItalic(self.italic)
        f.setUnderline(self.underline)
        if self.letter_spacing:
            f.setLetterSpacing(QFont.PercentageSpacing, 100 + self.letter_spacing)
        f.setHintingPreference(QFont.PreferFullHinting)
        return f

    def fitted_px(self, text: str, w: float, h: float) -> float:
        want = self.size_pt * PT_TO_DOTS
        if not self.autofit:
            return want
        key = (text, w, h, want, self.family, self.bold, self.italic, self.letter_spacing)
        if self._fit_cache and self._fit_cache[0] == key:
            return self._fit_cache[1]
        lo, hi = 4.0, want
        if not self._fits(text, w, h, hi):
            for _ in range(10):
                mid = (lo + hi) / 2
                if self._fits(text, w, h, mid):
                    lo = mid
                else:
                    hi = mid
            want = lo
        self._fit_cache = (key, want)
        return want

    def _fits(self, text, w, h, px):
        fm = QFontMetricsF(self.make_font(px))
        r = fm.boundingRect(QRectF(0, 0, w, 100000), self._flags(), text)
        # Words wider than the box would be broken: also check the widest word.
        widest = max((fm.horizontalAdvance(word) for word in text.split()), default=0)
        return r.height() <= h + 0.5 and widest <= w + 0.5

    def paint_content(self, p, w, h):
        text = expand(self.text)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        p.setRenderHint(QPainter.Antialiasing, True)
        if self.invert:
            p.fillRect(QRectF(0, 0, w, h), BLACK)
        pad = 1 if self.invert else 0
        box = QRectF(pad, 0, w - 2 * pad, h)
        p.setFont(self.make_font(self.fitted_px(text, box.width(), box.height())))
        p.setPen(WHITE if self.invert else BLACK)
        p.drawText(box, self._flags(), text)


# =========================================================================== image
class ImageItem(LabelItem):
    KIND = "image"
    KEEP_ASPECT = True
    DITHER_MODES = ["Floyd–Steinberg", "Atkinson", "Ordered (Bayer)", "Threshold"]

    def __init__(self, data: bytes = b"", w=96, h=96):
        super().__init__(w, h)
        self.data = data
        self.dither = "Floyd–Steinberg"
        self.threshold = 128
        self.brightness = 0   # -100..100
        self.contrast = 0     # -100..100
        self.invert = False
        self.flip_h = False
        self.flip_v = False
        self._src: Image.Image | None = None
        self._bitmap: QImage | None = None
        self._bitmap_key = None

    @classmethod
    def from_file_bytes(cls, data: bytes, max_w: int, max_h: int) -> "ImageItem":
        img = Image.open(io.BytesIO(data))
        img = _flatten(img)
        img = _autotrim(img)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        item = cls(buf.getvalue())
        scale = min(max_w / img.width, max_h / img.height)
        item._w, item._h = max(4, img.width * scale), max(4, img.height * scale)
        if _is_bilevel(img):
            item.dither = "Threshold"
        return item

    def props(self):
        return {"data": self.data, "dither": self.dither, "threshold": self.threshold,
                "brightness": self.brightness, "contrast": self.contrast, "invert": self.invert,
                "flip_h": self.flip_h, "flip_v": self.flip_v}

    def apply_props(self, d):
        if d.get("data") is not None and d["data"] is not self.data:
            self.data = d["data"]
            self._src = None
        for k in ("dither", "threshold", "brightness", "contrast", "invert", "flip_h", "flip_v"):
            if k in d:
                setattr(self, k, d[k])
        self.invalidate()

    def source(self) -> Image.Image:
        if self._src is None:
            self._src = _flatten(Image.open(io.BytesIO(self.data))).convert("L")
        return self._src

    def natural_aspect(self) -> float:
        s = self.source()
        return s.width / s.height

    def bitmap(self, w: int, h: int) -> QImage:
        key = (w, h, self.dither, self.threshold, self.brightness, self.contrast, self.invert,
               self.flip_h, self.flip_v, id(self.data))
        if self._bitmap is None or self._bitmap_key != key:
            img = self.source().resize((max(1, w), max(1, h)), Image.LANCZOS)
            if self.brightness:
                img = ImageEnhance.Brightness(img).enhance(1 + self.brightness / 100)
            if self.contrast:
                img = ImageEnhance.Contrast(img).enhance(1 + self.contrast / 100)
            if self.flip_h:
                img = ImageOps.mirror(img)
            if self.flip_v:
                img = ImageOps.flip(img)
            mono = to_1bit(img, self.dither, self.threshold)
            if self.invert:
                mono = ImageOps.invert(mono.convert("L")).convert("1")
            self._bitmap = pil_to_qimage(mono)
            self._bitmap_key = key
        return self._bitmap

    def paint_content(self, p, w, h):
        iw, ih = max(1, round(w)), max(1, round(h))
        p.setRenderHint(QPainter.SmoothPixmapTransform, False)
        p.drawImage(QRectF(0, 0, w, h), self.bitmap(iw, ih))


def _flatten(img: Image.Image) -> Image.Image:
    """Composite any transparency onto white and return RGB."""
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        bg.alpha_composite(img)
        return bg.convert("RGB")
    return img.convert("RGB")


def _autotrim(img: Image.Image) -> Image.Image:
    """Crop away uniform white margins."""
    gray = ImageOps.invert(img.convert("L"))
    bbox = gray.point(lambda v: 255 if v > 12 else 0).getbbox()
    return img.crop(bbox) if bbox else img


def _is_bilevel(img: Image.Image) -> bool:
    small = img.convert("L").resize((min(img.width, 128), min(img.height, 128)))
    hist = small.histogram()
    total = sum(hist)
    return sum(hist[:40]) + sum(hist[216:]) > 0.97 * total


BAYER4 = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]


def to_1bit(img: Image.Image, mode: str, threshold: int = 128) -> Image.Image:
    img = img.convert("L")
    if mode == "Threshold":
        return img.point(lambda v: 255 if v >= threshold else 0).convert("1", dither=Image.NONE)
    if mode == "Floyd–Steinberg":
        return img.convert("1", dither=Image.FLOYDSTEINBERG)
    w, h = img.size
    px = list(img.getdata())
    out = bytearray(w * h)
    if mode == "Ordered (Bayer)":
        for y in range(h):
            row = BAYER4[y & 3]
            for x in range(w):
                out[y * w + x] = 255 if px[y * w + x] > (row[x & 3] + 0.5) * 16 else 0
    else:  # Atkinson
        buf = [float(v) for v in px]
        for y in range(h):
            for x in range(w):
                i = y * w + x
                old = buf[i]
                new = 255 if old >= 128 else 0
                out[i] = new
                err = (old - new) / 8
                for dx, dy in ((1, 0), (2, 0), (-1, 1), (0, 1), (1, 1), (0, 2)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < w and ny < h:
                        buf[ny * w + nx] += err
    return Image.frombytes("L", (w, h), bytes(out)).convert("1", dither=Image.NONE)


def pil_to_qimage(img: Image.Image) -> QImage:
    g = img.convert("L")
    q = QImage(g.tobytes(), g.width, g.height, g.width, QImage.Format_Grayscale8)
    return q.copy()


# =========================================================================== shapes
class ShapeItem(LabelItem):
    KIND = "shape"
    SHAPES = ["rect", "ellipse", "line"]

    def __init__(self, shape="rect", w=80, h=48):
        super().__init__(w, h)
        self.shape = shape
        self.stroke = 2.0
        self.fill = False
        self.radius = 0.0
        self.MIN_SIZE = 1

    def display_name(self):
        return self.name or {"rect": "Rectangle", "ellipse": "Ellipse", "line": "Line"}[self.shape]

    def props(self):
        return {"shape": self.shape, "stroke": self.stroke, "fill": self.fill, "radius": self.radius}

    def apply_props(self, d):
        self.shape = d.get("shape", self.shape)
        self.stroke = float(d.get("stroke", self.stroke))
        self.fill = bool(d.get("fill", self.fill))
        self.radius = float(d.get("radius", self.radius))
        self.invalidate()

    def paint_content(self, p, w, h):
        s = self.stroke
        if self.shape == "line":
            p.setRenderHint(QPainter.Antialiasing, False)
            # A line runs along the box's long axis, centered.
            if w >= h:
                p.fillRect(QRectF(0, (h - s) / 2, w, s), BLACK)
            else:
                p.fillRect(QRectF((w - s) / 2, 0, s, h), BLACK)
            return
        if self.shape == "rect" and self.radius <= 0:
            p.setRenderHint(QPainter.Antialiasing, False)
            if self.fill:
                p.fillRect(QRectF(0, 0, w, h), BLACK)
            else:
                for r in (QRectF(0, 0, w, s), QRectF(0, h - s, w, s),
                          QRectF(0, 0, s, h), QRectF(w - s, 0, s, h)):
                    p.fillRect(r, BLACK)
            return
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(s / 2, s / 2, w - s, h - s)
        p.setPen(QPen(BLACK, s) if not self.fill else Qt.NoPen)
        p.setBrush(QBrush(BLACK) if self.fill else Qt.NoBrush)
        if self.fill:
            r = QRectF(0, 0, w, h)
        if self.shape == "ellipse":
            p.drawEllipse(r)
        else:
            p.drawRoundedRect(r, self.radius, self.radius)


# =========================================================================== barcodes
class BarcodeItem(LabelItem):
    KIND = "barcode"
    TYPES = {"qr": "QR code", "code128": "Code 128"}

    def __init__(self, btype="qr", data="BIN-{n:03}", w=88, h=88):
        super().__init__(w, h)
        self.btype = btype
        self.data = data
        self.show_text = True
        self.ecc = "M"
        self._cache = None

    @property
    def KEEP_ASPECT(self):  # noqa: N802 - QR codes stay square
        return self.btype == "qr"

    def display_name(self):
        return self.name or self.TYPES.get(self.btype, "Barcode")

    def uses_sequence(self):
        return has_sequence(self.data)

    def props(self):
        return {"btype": self.btype, "data": self.data, "show_text": self.show_text, "ecc": self.ecc}

    def apply_props(self, d):
        self.btype = d.get("btype", self.btype)
        self.data = d.get("data", self.data)
        self.show_text = bool(d.get("show_text", self.show_text))
        self.ecc = d.get("ecc", self.ecc)
        self.invalidate()

    def invalidate(self):
        self._cache = None
        self.update()

    def _encode(self, text):
        key = (self.btype, text, self.ecc)
        if self._cache and self._cache[0] == key:
            return self._cache[1]
        result = None
        try:
            if self.btype == "qr":
                import segno
                q = segno.make(text or " ", error=self.ecc.lower(), micro=False)
                result = [list(row) for row in q.matrix]
            else:
                from barcode import Code128
                result = Code128(text or " ").build()[0]
        except Exception:  # noqa: BLE001 - unencodable input shows a placeholder
            result = None
        self._cache = (key, result)
        return result

    def paint_content(self, p, w, h):
        text = expand(self.data)
        code = self._encode(text)
        p.setRenderHint(QPainter.Antialiasing, False)
        if code is None:
            self._paint_error(p, w, h, "Invalid data")
            return
        if self.btype == "qr":
            n = len(code)
            m = int(min(w, h) // n)
            if m < 1:
                self._paint_error(p, w, h, "Too small")
                return
            ox, oy = int((w - n * m) // 2), int((h - n * m) // 2)
            for y, row in enumerate(code):
                for x, bit in enumerate(row):
                    if bit:
                        p.fillRect(ox + x * m, oy + y * m, m, m, BLACK)
            return
        modules = len(code)
        m = int(w // modules)
        text_h = 0
        if self.show_text:
            f = QFont("Segoe UI")
            f.setPixelSize(max(8, min(14, round(h * 0.28))))
            p.setFont(f)
            text_h = QFontMetricsF(f).height()
        bar_h = h - text_h
        if m < 1 or bar_h < 4:
            self._paint_error(p, w, h, "Too small")
            return
        ox = int((w - modules * m) // 2)
        x = 0
        while x < modules:
            if code[x] == "1":
                run = 1
                while x + run < modules and code[x + run] == "1":
                    run += 1
                p.fillRect(QRectF(ox + x * m, 0, run * m, bar_h), BLACK)
                x += run
            else:
                x += 1
        if self.show_text:
            p.setRenderHint(QPainter.TextAntialiasing, True)
            p.setPen(BLACK)
            p.drawText(QRectF(0, bar_h, w, text_h), Qt.AlignCenter, text)

    def _paint_error(self, p, w, h, msg):
        if rendering_output:
            return
        p.fillRect(QRectF(0, 0, w, h), QColor(229, 83, 75, 60))
        p.setPen(QColor(229, 83, 75))
        f = QFont("Segoe UI")
        f.setPixelSize(10)
        p.setFont(f)
        p.drawText(QRectF(0, 0, w, h), Qt.AlignCenter | Qt.TextWordWrap, msg)


ITEM_TYPES = {cls.KIND: cls for cls in (TextItem, ImageItem, ShapeItem, BarcodeItem)}


def item_from_dict(d: dict) -> LabelItem:
    item = ITEM_TYPES[d["kind"]]()
    item.load_dict(d)
    return item
