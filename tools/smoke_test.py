"""CI smoke test: build a label with every element type, render it to 1-bit, round-trip the file format.

Runs headless (QT_QPA_PLATFORM=offscreen). Exits non-zero on failure.
"""
import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402
from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from niimbot_studio import __version__, theme  # noqa: E402
from niimbot_studio.config import LabelSize  # noqa: E402
from niimbot_studio.document import Document, render_label  # noqa: E402
from niimbot_studio.driver import NiimbotPrinter, make_packet  # noqa: E402
from niimbot_studio.items import BarcodeItem, ImageItem, ShapeItem, TextItem  # noqa: E402
from niimbot_studio.updater import is_newer  # noqa: E402


def main():
    app = QApplication(sys.argv)
    theme.apply(app)
    doc = Document(LabelSize(40, 12))
    text = TextItem("BIN {n:03}")
    text.set_geometry(QRectF(96, 8, 216, 80))
    doc.add_item(text)
    qr = BarcodeItem("qr", "BIN-{n:03}", 80, 80)
    qr.set_geometry(QRectF(8, 8, 80, 80))
    doc.add_item(qr)
    doc.add_item(ShapeItem("rect", 316, 92))
    buf = io.BytesIO()
    Image.new("RGB", (64, 32), (200, 40, 40)).save(buf, "PNG")
    doc.add_item(ImageItem.from_file_bytes(buf.getvalue(), 64, 32))

    one, two = render_label(doc.scene, 1), render_label(doc.scene, 2)
    assert one.mode == "1" and one.size == (320, 96), one
    assert one.tobytes() != two.tobytes(), "sequence numbers should change the output"

    path = Path(tempfile.mkdtemp()) / "smoke.nlbl"
    doc.save(path)
    doc2 = Document()
    doc2.open(path)
    assert render_label(doc2.scene, 1).tobytes() == one.tobytes(), "file round-trip changed output"

    rows = list(NiimbotPrinter.encode_rows(NiimbotPrinter.prepare_image(one, 7, -4)))
    assert rows, "no print packets"
    assert make_packet(0x40, b"\x01") == bytes.fromhex("555540010140aaaa"), "packet format"
    assert is_newer("99.0.0") and not is_newer(__version__)
    print(f"smoke test OK (v{__version__}, {len(rows)} row packets)")


if __name__ == "__main__":
    main()
