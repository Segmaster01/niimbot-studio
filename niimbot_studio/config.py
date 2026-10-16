"""Local settings, label catalog and learned RFID barcode -> label size map.

Everything is stored in %APPDATA%\\NiimbotStudio\\settings.json. Nothing leaves this machine.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "Niimbot Studio"
APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "NiimbotStudio"
SETTINGS_FILE = APP_DIR / "settings.json"
LIBRARY_DIR = Path.home() / "Documents" / "Niimbot Labels"
FILE_EXT = ".nlbl"

DOTS_PER_MM = 8


@dataclass(frozen=True)
class LabelSize:
    """A label as it sits on the canvas: `length` along the feed, `height` across the printhead."""
    length_mm: float
    height_mm: float

    @property
    def width_px(self) -> int:
        return round(self.length_mm * DOTS_PER_MM)

    @property
    def height_px(self) -> int:
        return round(self.height_mm * DOTS_PER_MM)

    @property
    def name(self) -> str:
        return f"{self.height_mm:g} × {self.length_mm:g} mm"

    def to_dict(self):
        return {"length_mm": self.length_mm, "height_mm": self.height_mm}

    @classmethod
    def from_dict(cls, d):
        return cls(float(d["length_mm"]), float(d["height_mm"]))


# Common D11 / D110 / D101 rolls (height × length, as Niimbot names them).
PRESETS = [LabelSize(l, h) for h, l in [
    (12, 22), (12, 26), (12, 30), (12, 40), (12, 50), (12, 75), (12, 109),
    (14, 22), (14, 28), (14, 30), (14, 40), (14, 50), (14, 75),
    (15, 26), (15, 30), (15, 50),
]]
DEFAULT_LABEL = LabelSize(40, 12)

# RFID barcodes confirmed by the user. More are learned at runtime and saved to settings.
KNOWN_BARCODES = {
    "6977031211887": {"length_mm": 40, "height_mm": 12, "name": "White 12×40 (192)"},
}

DEFAULTS = {
    "printer_address": "",
    "printer_name": "",
    "auto_connect": True,
    "density": 2,
    "offset_x": 7,   # dots; measured on a D11 with 12×40 labels (recalibrate per printer)
    "offset_y": -4,
    "learned_barcodes": {},
    "recent_files": [],
    "library_dir": str(LIBRARY_DIR),
    "show_grid": False,
    "snap": True,
    "grid_mm": 1.0,
    "pixel_preview": False,
    "accent": "blue",
    "check_updates": True,
    "last_update_check": 0,
    "skipped_version": "",
    "window_geometry": "",
    "window_state": "",
    "last_label": DEFAULT_LABEL.to_dict(),
}


def _store_python_settings() -> list[Path]:
    """Settings written while running from source under Microsoft Store Python.

    Store Python silently redirects %APPDATA% into its package folder, so an installed
    build would otherwise start from scratch on a PC that already ran the source version.
    """
    packages = Path(os.environ.get("LOCALAPPDATA", "")) / "Packages"
    return sorted(packages.glob("PythonSoftwareFoundation.Python.*/LocalCache/Roaming/NiimbotStudio/settings.json"))


class Settings:
    def __init__(self):
        self.data = dict(DEFAULTS)
        for path in (SETTINGS_FILE, *_store_python_settings()):
            try:
                self.data.update(json.loads(path.read_text(encoding="utf-8")))
                break
            except (OSError, ValueError):
                continue

    def __getitem__(self, key):
        return self.data.get(key, DEFAULTS.get(key))

    def __setitem__(self, key, value):
        self.data[key] = value
        self.save()

    def save(self):
        APP_DIR.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        tmp.replace(SETTINGS_FILE)

    # ----------------------------------------------------------- label lookup
    def label_for_barcode(self, barcode: str) -> LabelSize | None:
        entry = self["learned_barcodes"].get(barcode) or KNOWN_BARCODES.get(barcode)
        return LabelSize.from_dict(entry) if entry else None

    def learn_barcode(self, barcode: str, size: LabelSize):
        learned = dict(self["learned_barcodes"])
        learned[barcode] = size.to_dict()
        self["learned_barcodes"] = learned

    def add_recent(self, path: str):
        recent = [p for p in self["recent_files"] if p != path]
        self["recent_files"] = [path] + recent[:9]

    @property
    def library_dir(self) -> Path:
        p = Path(self["library_dir"])
        p.mkdir(parents=True, exist_ok=True)
        return p
