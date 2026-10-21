"""Connect dialog: scan errors, an empty result, and devices as they are found.

Bleak's scanner is mocked, so these tests never open a radio.
"""
import asyncio
import threading
import time
from types import SimpleNamespace

import pytest
from bleak import BleakError
from bleak.exc import BleakBluetoothNotAvailableError, BleakBluetoothNotAvailableReason
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox

from niimbot_studio import printer_service as printer_service_mod
from niimbot_studio.dialogs import ConnectDialog
from niimbot_studio.driver import (DISCOVER_SECONDS, NOT_PRINTER_MESSAGE, SERVICE_UUID, DeviceInfo,
                                  PrinterError, PrinterStatus)
from niimbot_studio.printer_service import PrinterService


NO_PRINTERS = "No printers found. Make sure it's on and nearby, then search again."
RADIO_HINT = "Settings > Privacy & security > Radios allows apps to control radios."


class _Dev:
    def __init__(self, address, name):
        self.address = address
        self.name = name


class _Adv:
    def __init__(self, local_name=None, service_uuids=None, rssi=-60):
        self.local_name = local_name
        self.service_uuids = [] if service_uuids is None else service_uuids
        self.rssi = rssi


class ScanScript:
    """Stand-in for BleakScanner.discover."""

    def __init__(self):
        self.mode = "empty"
        self.calls = []
        self.release = threading.Event()

    def _packets(self):
        if self.mode in ("mixed", "live"):
            packets = [(_Dev("AA:AA:AA:AA:AA:A1", "D11-TEST"), _Adv(rssi=-48))]
            if self.mode == "mixed":
                packets.append((_Dev("BB:BB:BB:BB:BB:B2", "Headphones"),
                                _Adv(rssi=-70, service_uuids=["0000180F-0000-1000-8000-00805F9B34FB"])))
            return packets
        return []

    async def __call__(self, *args, **kwargs):
        self.calls.append(kwargs.get("timeout"))
        assert kwargs.get("return_adv") is True
        callback = kwargs["detection_callback"]
        if self.mode == "error":
            raise BleakBluetoothNotAvailableError(
                "No Bluetooth adapter found",
                BleakBluetoothNotAvailableReason.NO_BLUETOOTH,
            )
        if self.mode == "off":
            raise BleakError("Bluetooth radio is not powered on. Turn on Bluetooth and try again.")
        found = {}
        for dev, adv in self._packets():
            callback(dev, adv)
            found[dev.address] = (dev, adv)
        if self.mode == "live":
            await asyncio.get_running_loop().run_in_executor(None, self.release.wait)
        return found


def wait_until(pred, timeout=3, detail="timed out"):
    app = QApplication.instance()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if pred():
            return
        time.sleep(0.02)
    raise AssertionError(detail)


@pytest.fixture
def ble(qapp, monkeypatch, tmp_path):
    script = ScanScript()
    script.log_dir = tmp_path
    monkeypatch.setattr(printer_service_mod, "APP_DIR", tmp_path)
    monkeypatch.setattr("niimbot_studio.driver.BleakScanner.discover", script)
    service = PrinterService()
    try:
        yield service, script
    finally:
        script.release.set()
        service.shutdown()


def _texts(dlg):
    return [dlg.list.item(i).text() for i in range(dlg.list.count())]


def test_error_is_not_an_empty_result_and_search_again_works(ble):
    service, script = ble
    script.mode = "error"
    dlg = ConnectDialog(None, service)
    try:
        wait_until(lambda: dlg.rescan.isEnabled(), detail=dlg.status.text())
        note = " ".join(w.text() for w in dlg.findChildren(QLabel))
        assert "Close the Niimbot app on your phone first" in note
        assert dlg.rescan.text() == "Search again"
        assert dlg.status.text() == (
            "Bluetooth unavailable: No Bluetooth adapter found. "
            "Check that Bluetooth is on and Settings > Privacy & security > Radios "
            "allows apps to control radios."
        )
        assert RADIO_HINT in dlg.status.text()
        assert "No printers found" not in dlg.status.text()
        assert "BleakBluetoothNotAvailableReason" not in dlg.status.text()
        assert dlg.list.count() == 0
        assert not dlg.ok.isEnabled()
        dlg.show_all.setChecked(True)
        assert "Bluetooth unavailable" in dlg.status.text()
        assert "No printers found" not in dlg.status.text()
        dlg.show_all.setChecked(False)

        script.mode = "empty"
        dlg.rescan.click()
        wait_until(lambda: dlg.rescan.isEnabled() and dlg.status.text() == NO_PRINTERS,
                   detail=dlg.status.text())
        assert "Bluetooth unavailable" not in dlg.status.text()
        assert dlg.list.count() == 0
        dlg.show_all.setChecked(True)
        assert dlg.status.text().startswith("No Bluetooth devices found")
        assert "No printers found" not in dlg.status.text()
        dlg.show_all.setChecked(False)

        script.mode = "off"
        dlg.rescan.click()
        wait_until(lambda: dlg.rescan.isEnabled() and dlg.status.text().startswith("Bluetooth unavailable:"),
                   detail=dlg.status.text())
        assert "Bluetooth radio is not powered on" in dlg.status.text()
        assert RADIO_HINT in dlg.status.text()
        assert "No printers found" not in dlg.status.text()

        script.mode = "mixed"
        dlg.rescan.click()
        wait_until(lambda: dlg.rescan.isEnabled() and dlg.list.count() == 1,
                   detail=f"{dlg.status.text()} / {_texts(dlg)}")
        assert dlg.status.text() == "Found 1 printer."
        assert _texts(dlg) == ["D11-TEST    AA:AA:AA:AA:AA:A1"]
        assert "Headphones" not in dlg.status.text()
        calls_before = len(script.calls)
        dlg.show_all.setChecked(True)
        assert len(script.calls) == calls_before  # checkbox filters; it does not scan again
        assert dlg.status.text() == "Found 2 devices."
        assert _texts(dlg) == [
            "D11-TEST    AA:AA:AA:AA:AA:A1    -48 dBm    no services",
            "Headphones    BB:BB:BB:BB:BB:B2    -70 dBm    0000180f-0000-1000-8000-00805f9b34fb",
        ]
        log = (script.log_dir / "niimbot.log").read_text(encoding="utf-8")
        d11_lines = [line for line in log.splitlines() if "D11-TEST" in line]
        assert d11_lines
        assert "AA:AA:AA:AA:AA:A1" in d11_lines[0]
        assert "rssi=-48" in d11_lines[0]
        assert "services=-" in d11_lines[0]
        assert SERVICE_UUID not in d11_lines[0]
        assert any("Headphones" in line and "0000180f-0000-1000-8000-00805f9b34fb" in line
                   for line in log.splitlines())
        dlg.list.setCurrentRow(1)
        addr, name = dlg.selected()
        assert (addr, name) == ("BB:BB:BB:BB:BB:B2", "Headphones")
        dlg.show_all.setChecked(False)
        assert dlg.list.count() == 1
        assert all(timeout == DISCOVER_SECONDS for timeout in script.calls)
    finally:
        dlg.reject()


def test_devices_show_up_before_the_scan_finishes(ble):
    service, script = ble
    script.mode = "live"
    dlg = ConnectDialog(None, service)
    try:
        wait_until(
            lambda: dlg.list.count() == 1 and not dlg.rescan.isEnabled()
            and dlg.status.text().startswith("Searching"),
            detail=f"enabled={dlg.rescan.isEnabled()} text={dlg.status.text()!r} n={dlg.list.count()}",
        )
        assert "D11-TEST" in dlg.list.item(0).text()
        assert dlg.ok.isEnabled()
        script.release.set()
        wait_until(lambda: dlg.rescan.isEnabled() and dlg.status.text() == "Found 1 printer.",
                   detail=dlg.status.text())
        assert dlg.rescan.text() == "Search again"
    finally:
        script.release.set()
        dlg.reject()


def test_connect_uses_the_device_found_by_address(qapp, monkeypatch):
    """A name-matched printer has no service UUID filter on the way back to connect."""
    dev = SimpleNamespace(name="D11-TEST", address="AA:AA:AA:AA:AA:A1")
    seen = {}

    async def find_device_by_address(address, timeout=10.0, **kwargs):
        seen["address"] = address
        seen["timeout"] = timeout
        seen["kwargs"] = kwargs
        return dev

    class FakePrinter:
        def __init__(self, address):
            seen["printer_address"] = address
            self.address = address
            self.on_disconnect = None
            self.connected = True

        async def connect(self, device=None):
            seen["device"] = device

        async def get_device_info(self):
            return DeviceInfo()

        async def heartbeat(self):
            return PrinterStatus()

        async def get_rfid(self):
            return None

    monkeypatch.setattr("niimbot_studio.printer_service.BleakScanner.find_device_by_address",
                        find_device_by_address)
    monkeypatch.setattr("niimbot_studio.printer_service.NiimbotPrinter", FakePrinter)
    service = PrinterService()
    try:
        service.connect_to("AA:AA:AA:AA:AA:A1", "D11-TEST")
        wait_until(lambda: service.state == "connected", detail=f"state={service.state}")
        assert seen["address"] == "AA:AA:AA:AA:AA:A1"
        assert "service_uuids" not in seen["kwargs"]
        assert seen["device"] is dev
        assert seen["printer_address"] == "AA:AA:AA:AA:AA:A1"
    finally:
        service.shutdown()


def test_rejected_device_is_not_retried(qapp, monkeypatch):
    async def find_device_by_address(address, timeout=10.0, **kwargs):
        return SimpleNamespace(name="Headphones", address=address)

    class FakePrinter:
        def __init__(self, address):
            self.connected = False
            self.on_disconnect = None

        async def connect(self, device=None):
            raise PrinterError(NOT_PRINTER_MESSAGE)

    monkeypatch.setattr("niimbot_studio.printer_service.BleakScanner.find_device_by_address",
                        find_device_by_address)
    monkeypatch.setattr("niimbot_studio.printer_service.NiimbotPrinter", FakePrinter)
    service = PrinterService()
    messages = []
    service.state_changed.connect(lambda state, msg: messages.append((state, msg)))
    try:
        service.connect_to("BB:BB:BB:BB:BB:B2", "Headphones")
        wait_until(lambda: any(NOT_PRINTER_MESSAGE in msg for _state, msg in messages)
                   and not service._want_connected,
                   detail=repr(messages))
        assert any(msg.startswith("PrinterError:") and NOT_PRINTER_MESSAGE in msg
                   for _state, msg in messages)
        assert service.state == "disconnected"
    finally:
        service.shutdown()


def test_connect_failure_dialog_shows_bleak_exception(ble, monkeypatch):
    service, script = ble
    script.mode = "live"
    shown = []

    def warning(_parent, title, text, *args, **kwargs):
        shown.append((title, text))
        return QMessageBox.Ok

    async def find_device_by_address(address, timeout=10.0, **kwargs):
        return SimpleNamespace(name="D11-TEST", address=address)

    class FakePrinter:
        def __init__(self, address):
            self.connected = False
            self.on_disconnect = None

        async def connect(self, device=None):
            try:
                raise OSError(5, "Access is denied")
            except OSError as cause:
                raise BleakError("Could not connect") from cause

    monkeypatch.setattr("niimbot_studio.dialogs.QMessageBox.warning", warning)
    monkeypatch.setattr("niimbot_studio.printer_service.BleakScanner.find_device_by_address",
                        find_device_by_address)
    monkeypatch.setattr("niimbot_studio.printer_service.NiimbotPrinter", FakePrinter)
    dlg = ConnectDialog(None, service)
    try:
        wait_until(lambda: dlg.list.count() == 1 and dlg.ok.isEnabled(), detail=dlg.status.text())
        dlg.ok.click()
        wait_until(lambda: shown, detail=dlg.status.text())
        title, text = shown[0]
        assert title == "Couldn't connect"
        assert "BleakError: Could not connect" in text
        assert "OSError:" in text and "Access is denied" in text
        assert "Printer not found" not in text
        assert NOT_PRINTER_MESSAGE not in text
        assert dlg.status.text() == text
        assert dlg.result() != QDialog.Accepted
    finally:
        script.release.set()
        dlg.reject()
