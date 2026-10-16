"""Runs the BLE driver on a background asyncio thread and exposes it to Qt via signals."""
from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future

from bleak import BleakScanner
from PIL import Image
from PySide6.QtCore import QObject, Signal

from .driver import DeviceInfo, NiimbotPrinter, PrinterError, PrinterStatus, RfidInfo, discover

POLL_SECONDS = 4


class PrinterService(QObject):
    state_changed = Signal(str, str)        # state, message. state: disconnected/scanning/connecting/connected
    device_info = Signal(object)            # DeviceInfo
    status = Signal(object)                 # PrinterStatus
    rfid = Signal(object)                   # RfidInfo | None
    devices_found = Signal(list)            # [(address, name)]
    print_progress = Signal(int, int)       # labels done, total
    print_finished = Signal(bool, str)      # ok, message

    def __init__(self):
        super().__init__()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="niimbot-ble", daemon=True)
        self._thread.start()
        self.printer: NiimbotPrinter | None = None
        self.state = "disconnected"
        self.address = ""
        self.name = ""
        self._want_connected = False
        self._printing = False
        self._cancel = False
        self._poll_task = None
        self._last_rfid_key = object()

    def _run(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _submit(self, coro) -> Future:
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def _set_state(self, state, msg=""):
        self.state = state
        self.state_changed.emit(state, msg)

    @property
    def connected(self) -> bool:
        return self.state == "connected"

    @property
    def printing(self) -> bool:
        return self._printing

    # ------------------------------------------------------------------ public API
    def scan(self):
        self._submit(self._scan())

    def connect_to(self, address: str, name: str = ""):
        self.address, self.name = address, name
        self._want_connected = True
        self._submit(self._connect())

    def disconnect(self):
        self._want_connected = False
        self._submit(self._disconnect())

    def refresh(self):
        self._last_rfid_key = object()
        self._submit(self._poll_once(full=True))

    def print_labels(self, images: list[Image.Image], copies: int, density: int,
                     offset_x: int, offset_y: int):
        """Print each image `copies` times. Several images = a numbered run."""
        self._cancel = False
        self._submit(self._print(images, copies, density, offset_x, offset_y))

    def cancel_print(self):
        self._cancel = True

    def set_auto_shutdown(self, value: int):
        return self._submit(self._call(lambda p: p.set_auto_shutdown(value)))

    def set_sound(self, item: int, on: bool):
        return self._submit(self._call(lambda p: p.set_sound(item, on)))

    def shutdown(self):
        self._want_connected = False
        fut = self._submit(self._disconnect())
        try:
            fut.result(timeout=4)
        except Exception:  # noqa: BLE001
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)

    # ------------------------------------------------------------------ coroutines
    async def _call(self, fn):
        if not self.printer or not self.printer.connected:
            return False
        try:
            return await fn(self.printer)
        except Exception:  # noqa: BLE001
            return False

    async def _scan(self):
        self._set_state("scanning", "Searching for printers…")
        try:
            found = await discover(6)
        except Exception as e:  # noqa: BLE001
            self.devices_found.emit([])
            self._set_state("disconnected", f"Bluetooth unavailable: {e}")
            return
        self.devices_found.emit(found)
        if self.state == "scanning":
            self._set_state("disconnected", "")

    async def _connect(self):
        if self.printer and self.printer.connected:
            return
        delay = 2
        while self._want_connected:
            self._set_state("connecting", f"Connecting to {self.name or self.address}…")
            try:
                dev = await BleakScanner.find_device_by_address(self.address, timeout=8)
                if dev is None:
                    raise PrinterError("Printer not found. Is it switched on and in range?")
                self.name = dev.name or self.name
                printer = NiimbotPrinter(self.address)
                printer.on_disconnect = lambda: self._loop.call_soon_threadsafe(self._on_lost)
                await printer.connect()
                self.printer = printer
                info = await printer.get_device_info()
                self.device_info.emit(info)
                self._set_state("connected", self.name)
                self._last_rfid_key = object()
                await self._poll_once(full=True)
                if self._poll_task is None or self._poll_task.done():
                    self._poll_task = asyncio.ensure_future(self._poll_loop())
                return
            except Exception as e:  # noqa: BLE001
                self.printer = None
                self._set_state("disconnected", str(e) or e.__class__.__name__)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)

    def _on_lost(self):
        if self.printer is None:
            return
        self.printer = None
        self.rfid.emit(None)
        if self._want_connected:
            self._set_state("disconnected", "Connection lost. Reconnecting…")
            asyncio.ensure_future(self._connect())
        else:
            self._set_state("disconnected", "")

    async def _disconnect(self):
        if self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None
        p, self.printer = self.printer, None
        if p:
            try:
                await p.disconnect()
            except Exception:  # noqa: BLE001
                pass
        self.rfid.emit(None)
        self._set_state("disconnected", "")

    async def _poll_loop(self):
        while self.printer and self.printer.connected:
            await asyncio.sleep(POLL_SECONDS)
            if not self._printing:
                await self._poll_once()

    async def _poll_once(self, full=False):
        p = self.printer
        if not p or not p.connected or self._printing:
            return
        try:
            st: PrinterStatus = await p.heartbeat()
            self.status.emit(st)
            r: RfidInfo | None = await p.get_rfid()
            key = (r.serial, r.used, r.barcode) if r else None
            if full or key != self._last_rfid_key:
                self._last_rfid_key = key
                self.rfid.emit(r)
            if full:
                info: DeviceInfo = await p.get_device_info()
                self.device_info.emit(info)
        except Exception:  # noqa: BLE001 - transient BLE hiccups are retried next poll
            pass

    async def _print(self, images, copies, density, ox, oy):
        p = self.printer
        if not p or not p.connected:
            self.print_finished.emit(False, "Printer is not connected.")
            return
        self._printing = True
        total = len(images) * copies
        done = 0
        try:
            for img in images:
                if self._cancel:
                    raise PrinterError("Cancelled")
                base = done
                await p.print_image(img, quantity=copies, density=density, offset_x=ox, offset_y=oy,
                                    progress=lambda page, _q, b=base: self.print_progress.emit(b + page, total))
                done += copies
                self.print_progress.emit(done, total)
            self.print_finished.emit(True, f"Printed {total} label{'s' if total != 1 else ''}.")
        except Exception as e:  # noqa: BLE001
            # Make sure the printer is not left mid-job.
            try:
                await p.request(0xF3, b"\x01", expect=0xF4, retries=0)
            except Exception:  # noqa: BLE001
                pass
            msg = str(e) or e.__class__.__name__
            self.print_finished.emit(False, f"{msg}. {done} of {total} printed." if done else msg)
        finally:
            self._printing = False
            self._last_rfid_key = object()
            await self._poll_once()
