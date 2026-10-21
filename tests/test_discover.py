"""Discovery matching and connect-time GATT checks. Bleak is mocked; no radio is used."""
import asyncio

import pytest
from bleak import BleakError
from bleak.exc import BleakBluetoothNotAvailableError, BleakBluetoothNotAvailableReason

from niimbot_studio import driver
from niimbot_studio.driver import (CHAR_UUID, DISCOVER_SECONDS, NOT_PRINTER_MESSAGE, SERVICE_UUID,
                                  NiimbotPrinter, PrinterError, bluetooth_unavailable_message,
                                  discover, exception_text, matches_printer, scan_error_reason)


class _Dev:
    def __init__(self, address, name):
        self.address = address
        self.name = name


class _Adv:
    def __init__(self, local_name=None, service_uuids=None, rssi=-60):
        self.local_name = local_name
        self.service_uuids = [] if service_uuids is None else service_uuids
        self.rssi = rssi


def test_matches_service_uuid():
    assert matches_printer("Phone", service_uuids=[SERVICE_UUID])
    assert matches_printer(service_uuids=["{E7810A71-73AE-499D-8C15-FAA9AEF0C3F2}"])
    assert matches_printer("Phone", service_uuids=["0000180f-0000-1000-8000-00805f9b34fb", SERVICE_UUID])


def test_matches_name_prefix_only():
    assert matches_printer("D11_ABCD", service_uuids=[])
    assert matches_printer(None, "D11-LOCAL")
    assert matches_printer("D110")  # covered by the D11 prefix
    assert "D11" in driver.PRINTER_NAME_PREFIXES


def test_rejects_unrelated_device():
    battery = "0000180f-0000-1000-8000-00805f9b34fb"
    assert not matches_printer("Headphones", service_uuids=[battery])
    assert not matches_printer("xd11")
    assert not matches_printer("")
    assert not matches_printer(None)


def test_name_prefix_is_case_insensitive():
    assert matches_printer("d11")
    assert matches_printer("D11-22B2")
    assert matches_printer("  d11_aabb ")
    assert not matches_printer("not a d11")


def test_discover_keeps_uuid_or_name_and_reports_the_rest(monkeypatch):
    devices = [
        (_Dev("1", "Phone"), _Adv(service_uuids=["{E7810A71-73AE-499D-8C15-FAA9AEF0C3F2}"], rssi=-40)),
        (_Dev("2", "d11_aabb"), _Adv(rssi=-50)),
        (_Dev("3", "Headphones"), _Adv(service_uuids=["0000180f-0000-1000-8000-00805f9b34fb"], rssi=-70)),
        (_Dev("4", None), _Adv(local_name="D11-LOCAL", rssi=-55)),
        (_Dev("5", "xd11"), _Adv(rssi=-65)),
    ]
    seen = []

    async def fake_discover(**kwargs):
        assert kwargs["timeout"] == 3
        assert kwargs["return_adv"] is True
        callback = kwargs["detection_callback"]
        found = {}
        for dev, adv in devices:
            callback(dev, adv)
            callback(dev, adv)  # repeat advertisements should not notify twice
            found[dev.address] = (dev, adv)
        return found

    monkeypatch.setattr(driver.BleakScanner, "discover", fake_discover)
    result = asyncio.run(discover(3, lambda *row: seen.append(row)))

    assert result == [("1", "Phone"), ("2", "d11_aabb"), ("4", "D11-LOCAL")]
    assert seen == [
        ("1", "Phone", -40, True, (SERVICE_UUID,)),
        ("2", "d11_aabb", -50, True, ()),
        ("3", "Headphones", -70, False, ("0000180f-0000-1000-8000-00805f9b34fb",)),
        ("4", "D11-LOCAL", -55, True, ()),
        ("5", "xd11", -65, False, ()),
    ]


def test_discover_defaults_to_twelve_seconds(monkeypatch):
    seen = {}

    async def fake_discover(**kwargs):
        seen["timeout"] = kwargs["timeout"]
        return {}

    monkeypatch.setattr(driver.BleakScanner, "discover", fake_discover)
    assert asyncio.run(discover()) == []
    assert seen["timeout"] == DISCOVER_SECONDS == 12


def test_exception_text_includes_type_and_cause():
    powered_off = BleakBluetoothNotAvailableError(
        "Bluetooth radio is not powered on. Turn on Bluetooth and try again.",
        BleakBluetoothNotAvailableReason.POWERED_OFF,
    )
    text = exception_text(powered_off)
    assert text == (
        "BleakBluetoothNotAvailableError: "
        "Bluetooth radio is not powered on. Turn on Bluetooth and try again."
    )
    assert "BleakBluetoothNotAvailableReason" not in text

    try:
        raise BleakError("Could not connect") from OSError(5, "Access is denied")
    except BleakError as err:
        chained = exception_text(err)
    assert chained.splitlines()[0] == "BleakError: Could not connect"
    assert chained.splitlines()[1].startswith("OSError:")
    assert "Access is denied" in chained.splitlines()[1]


def test_scan_error_reason_is_plain_text():
    powered_off = BleakBluetoothNotAvailableError(
        "Bluetooth radio is not powered on. Turn on Bluetooth and try again.",
        BleakBluetoothNotAvailableReason.POWERED_OFF,
    )
    denied = BleakBluetoothNotAvailableError(
        "Bluetooth access was denied",
        BleakBluetoothNotAvailableReason.DENIED_BY_SYSTEM,
    )
    assert "BleakBluetoothNotAvailableReason" not in str(scan_error_reason(powered_off))
    assert scan_error_reason(powered_off) == (
        "Bluetooth radio is not powered on. Turn on Bluetooth and try again."
    )
    assert scan_error_reason(denied) == "Bluetooth access was denied"
    assert scan_error_reason(BleakError("Failed to start scanner. Is Bluetooth turned on?")) == (
        "Failed to start scanner. Is Bluetooth turned on?"
    )
    assert bluetooth_unavailable_message(scan_error_reason(powered_off)) == (
        "Bluetooth unavailable: Bluetooth radio is not powered on. Turn on Bluetooth and try again. "
        "Check that Bluetooth is on and Settings > Privacy & security > Radios "
        "allows apps to control radios."
    )


class _Services:
    def __init__(self, service=True, characteristic=True):
        self._service = service
        self._characteristic = characteristic

    def get_service(self, uuid):
        return object() if self._service and uuid == SERVICE_UUID else None

    def get_characteristic(self, uuid):
        return object() if self._characteristic and uuid == CHAR_UUID else None


def _install_client(monkeypatch, services):
    box = {}

    class Client:
        def __init__(self, target, **kwargs):
            box["target"] = target
            box["kwargs"] = kwargs
            self.services = services

        async def connect(self):
            return None

        async def disconnect(self):
            box["disconnected"] = True

        async def start_notify(self, uuid, _callback):
            box["notified"] = uuid

    monkeypatch.setattr(driver, "BleakClient", Client)
    return box


def test_connect_rejects_device_without_printer_gatt(monkeypatch):
    box = _install_client(monkeypatch, _Services(service=False, characteristic=True))
    printer = NiimbotPrinter("AA:BB:CC:DD:EE:FF")
    with pytest.raises(PrinterError, match="not a Niimbot printer"):
        asyncio.run(printer.connect())
    assert box["disconnected"] is True
    assert "notified" not in box
    assert box["kwargs"].get("pair", False) is False
    assert str(PrinterError(NOT_PRINTER_MESSAGE)) == NOT_PRINTER_MESSAGE


def test_connect_uses_scanned_device_when_gatt_matches(monkeypatch):
    box = _install_client(monkeypatch, _Services())
    scanned = object()
    printer = NiimbotPrinter("AA:BB:CC:DD:EE:FF")
    asyncio.run(printer.connect(scanned))
    assert box["target"] is scanned
    assert box["notified"] == CHAR_UUID
    assert "disconnected" not in box
    assert box["kwargs"].get("pair", False) is False
