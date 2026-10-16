"""Render the built-in (unbranded) app icon in each accent color.

Run: python tools/make_icons.py  -> niimbot_studio/resources/icon-<accent>.png/.ico
"""
import sys
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "niimbot_studio" / "resources"
ACCENTS = {"blue": ("#2A8FD6", "#193B6A"), "red": ("#E2363C", "#77111B")}


def render(light, dark, size=512) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 100
    grad = QLinearGradient(QPointF(0, 0), QPointF(size, size))
    grad.setColorAt(0, QColor(light))
    grad.setColorAt(1, QColor(dark))
    p.setPen(Qt.NoPen)
    p.setBrush(grad)
    p.drawRoundedRect(QRectF(4 * s, 4 * s, 92 * s, 92 * s), 22 * s, 22 * s)

    # A label tag, tilted slightly, with a punched hole and barcode bars.
    p.translate(50 * s, 50 * s)
    p.rotate(-12)
    tag = QPainterPath()
    tag.addRoundedRect(QRectF(-34 * s, -19 * s, 68 * s, 38 * s), 7 * s, 7 * s)
    hole = QPainterPath()
    hole.addEllipse(QPointF(-24 * s, 0), 4.2 * s, 4.2 * s)
    p.setBrush(QColor("white"))
    p.drawPath(tag.subtracted(hole))
    x = -14.0
    for w in (2.2, 1.0, 1.0, 3.0, 1.0, 2.2, 1.0, 3.0, 1.0, 1.0, 2.2, 1.0):
        p.fillRect(QRectF(x * s, -10 * s, w * s, 20 * s), QColor(dark))
        x += w + 1.6
    p.end()
    return img


def main():
    QGuiApplication(sys.argv)
    for name, (light, dark) in ACCENTS.items():
        img = render(light, dark)
        png = OUT / f"icon-{name}.png"
        img.save(str(png))
        Image.open(png).save(OUT / f"icon-{name}.ico",
                             sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
        print("wrote", png)


if __name__ == "__main__":
    main()
