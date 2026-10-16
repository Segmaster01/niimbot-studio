"""Update check against GitHub Releases, plus download-and-install of the new Setup.exe.

The only network traffic the app makes: one anonymous request to the GitHub API
(at most twice a day, and only when "Check for updates" is enabled), and the
installer download when the user chooses to update.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from . import GITHUB_REPO, __version__

API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
TIMEOUT = 10


@dataclass
class Release:
    version: str
    notes: str
    page_url: str
    setup_url: str
    setup_name: str
    size: int


def parse_version(v: str) -> tuple:
    nums = re.findall(r"\d+", v)
    return tuple(int(n) for n in nums[:3]) + (0,) * (3 - len(nums[:3]))


def is_newer(remote: str, local: str = __version__) -> bool:
    return parse_version(remote) > parse_version(local)


def fetch_latest() -> Release | None:
    req = urllib.request.Request(API_URL, headers={"Accept": "application/vnd.github+json",
                                                   "User-Agent": f"NiimbotStudio/{__version__}"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # noqa: S310 - fixed https URL
        data = json.load(r)
    asset = next((a for a in data.get("assets", [])
                  if a["name"].lower().endswith(".exe") and "setup" in a["name"].lower()), None)
    if asset is None:
        return None
    return Release(version=data.get("tag_name", "").lstrip("v"), notes=data.get("body") or "",
                   page_url=data.get("html_url", ""), setup_url=asset["browser_download_url"],
                   setup_name=asset["name"], size=int(asset.get("size", 0)))


class Updater(QObject):
    """Runs network work on a worker thread and reports back through Qt signals."""

    checked = Signal(object, str)       # Release | None (None = up to date), error message
    download_progress = Signal(int, int)
    downloaded = Signal(str, str)       # installer path, error message

    def __init__(self):
        super().__init__()
        self._cancel = False

    def check(self):
        def work():
            try:
                rel = fetch_latest()
                self.checked.emit(rel if rel and is_newer(rel.version) else None, "")
            except Exception as e:  # noqa: BLE001 - offline, rate-limited, no releases yet...
                self.checked.emit(None, str(e) or e.__class__.__name__)
        threading.Thread(target=work, daemon=True).start()

    def download(self, rel: Release):
        self._cancel = False

        def work():
            dest = Path(tempfile.gettempdir()) / rel.setup_name
            try:
                req = urllib.request.Request(rel.setup_url, headers={"User-Agent": f"NiimbotStudio/{__version__}"})
                with urllib.request.urlopen(req, timeout=TIMEOUT) as r, open(dest, "wb") as f:  # noqa: S310
                    total = int(r.headers.get("Content-Length") or rel.size or 0)
                    done = 0
                    while chunk := r.read(256 * 1024):
                        if self._cancel:
                            raise RuntimeError("Cancelled")
                        f.write(chunk)
                        done += len(chunk)
                        self.download_progress.emit(done, total)
                self.downloaded.emit(str(dest), "")
            except Exception as e:  # noqa: BLE001
                dest.unlink(missing_ok=True)
                self.downloaded.emit("", str(e) or e.__class__.__name__)
        threading.Thread(target=work, daemon=True).start()

    def cancel(self):
        self._cancel = True


def can_self_update() -> bool:
    """Only the installed (frozen) build can replace itself."""
    return bool(getattr(sys, "frozen", False))


def launch_installer(path: str):
    """Run the installer silently; it closes this app, upgrades in place and relaunches it."""
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS",
                      "/RESTARTAPPLICATIONS"], creationflags=flags, close_fds=True,
                     cwd=os.path.dirname(path))
