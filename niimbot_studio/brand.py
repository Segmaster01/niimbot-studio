"""Optional brand packs (.nsbrand): swap in an organization's logo, app icon and accent names.

A brand pack is a zip file containing brand.json plus the images it names:

    {
      "name": "Example Co",
      "logo": {"blue": "logo-blue.png", "red": "logo-red.png"},   # or one file for all accents
      "icon": {"blue": "icon-blue.png", "red": "icon-red.png"},
      "accent_names": {"blue": "Example Blue", "red": "Example Red"}
    }

Every field is optional. Packs are looked up, in order, at:
  1. %APPDATA%\\NiimbotStudio\\brand.nsbrand   (installed from Settings → Brand pack)
  2. brand.nsbrand next to the installed app   (dropped beside Setup.exe at install time)
Without a pack the app uses its built-in, unbranded look.
"""
from __future__ import annotations

import json
import shutil
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .config import APP_DIR

BRAND_EXT = ".nsbrand"
USER_BRAND = APP_DIR / f"brand{BRAND_EXT}"


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[1]


@dataclass
class Brand:
    name: str = ""
    path: Path | None = None
    logos: dict = field(default_factory=dict)        # accent -> PNG bytes ("*" = any accent)
    icons: dict = field(default_factory=dict)
    accent_names: dict = field(default_factory=dict)

    def logo(self, accent: str) -> bytes | None:
        return self.logos.get(accent) or self.logos.get("*")

    def icon(self, accent: str) -> bytes | None:
        return self.icons.get(accent) or self.icons.get("*")


def read_pack(path: Path) -> Brand:
    """Parse and validate a brand pack. Raises ValueError if it is unusable."""
    try:
        with zipfile.ZipFile(path) as z:
            meta = json.loads(z.read("brand.json").decode("utf-8"))

            def files(key):
                spec = meta.get(key) or {}
                if isinstance(spec, str):
                    spec = {"*": spec}
                return {accent: z.read(name) for accent, name in spec.items()}

            return Brand(name=str(meta.get("name", "")), path=Path(path), logos=files("logo"),
                         icons=files("icon"), accent_names=dict(meta.get("accent_names") or {}))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile) as e:
        raise ValueError(f"Not a valid brand pack: {e}") from e


def load() -> Brand:
    for candidate in (USER_BRAND, app_dir() / f"brand{BRAND_EXT}"):
        if candidate.exists():
            try:
                return read_pack(candidate)
            except ValueError:
                continue
    return Brand()


def install(path: Path) -> Brand:
    brand = read_pack(path)
    APP_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, USER_BRAND)
    brand.path = USER_BRAND
    return brand


def remove():
    USER_BRAND.unlink(missing_ok=True)
