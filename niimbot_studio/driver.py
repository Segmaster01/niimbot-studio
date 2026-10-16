"""Niimbot D11 Bluetooth LE driver.

Implements the Niimbot packet protocol (55 55 CMD LEN DATA XOR AA AA) over the
printer's BLE serial characteristic, plus the D11 print task sequence.
"""
from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass
from enum import IntEnum

from bleak import BleakClient, BleakScanner
from PIL import Image

SERVICE_UUID = "e7810a71-73ae-499d-8c15-faa9aef0c3f2"
CHAR_UUID = "bef8d6c9-9c21-4c9e-b632-bd58c1009f9f"

PRINTHEAD_PX = 96  # D11: 12 mm @ 203 dpi
DPI = 203
PX_PER_MM = 8  # 203 dpi ~= 8 dots/mm
ROW_PACKET_DELAY = 0.01  # seconds between bitmap row packets


class Cmd(IntEnum):
    PRINT_START = 0x01
    PAGE_START = 0x03
    SET_PAGE_SIZE = 0x13
    PRINT_QUANTITY = 0x15
    RFID_INFO = 0x1A
    PRINT_CLEAR = 0x20
    SET_DENSITY = 0x21
    SET_LABEL_TYPE = 0x23
    SET_AUTO_SHUTDOWN = 0x27
    PRINTER_INFO = 0x40
    SOUND_SETTINGS = 0x58
    PRINT_EMPTY_ROW = 0x84
    PRINT_BITMAP_ROW = 0x85
    PRINT_STATUS = 0xA3
    HEARTBEAT = 0xDC
    PAGE_END = 0xE3
    PRINT_END = 0xF3


class Info(IntEnum):
    DENSITY = 1
    PRINT_SPEED = 2
    LABEL_TYPE = 3
    LANGUAGE = 6
    AUTO_SHUTDOWN = 7
    DEVICE_TYPE = 8
    SOFTWARE_VERSION = 9
    BATTERY = 10
    SERIAL = 11
    HARDWARE_VERSION = 12


class LabelType(IntEnum):
    WITH_GAPS = 1
    BLACK = 2
    CONTINUOUS = 3
    PERFORATED = 4
    TRANSPARENT = 5


PRINT_ERRORS = {
    0x01: "Cover open", 0x02: "Out of labels", 0x03: "Low battery",
    0x04: "Battery fault", 0x05: "Cancelled", 0x06: "Data error",
    0x07: "Overheated", 0x08: "Paper feed fault", 0x09: "Printer busy",
    0x0A: "No printhead", 0x0B: "Temperature too low", 0x0C: "Printhead loose",
    0x10: "Wrong label type", 0x11: "Could not set paper", 0x34: "Data timeout",
}


class PrinterError(Exception):
    pass


def make_packet(cmd: int, data: bytes = b"") -> bytes:
    checksum = cmd ^ len(data)
    for b in data:
        checksum ^= b
    return bytes([0x55, 0x55, cmd, len(data)]) + data + bytes([checksum, 0xAA, 0xAA])


@dataclass
class Packet:
    cmd: int
    data: bytes


class PacketParser:
    """Reassembles packets from a BLE notification byte stream."""

    def __init__(self):
        self.buf = bytearray()

    def feed(self, chunk: bytes) -> list[Packet]:
        self.buf += chunk
        out = []
        while True:
            start = self.buf.find(b"\x55\x55")
            if start < 0:
                self.buf.clear()
                return out
            del self.buf[:start]
            if len(self.buf) < 4:
                return out
            length = self.buf[3]
            total = 4 + length + 3
            if len(self.buf) < total:
                return out
            raw = bytes(self.buf[:total])
            if raw[-2:] != b"\xAA\xAA":
                del self.buf[:2]  # bad frame, resync
                continue
            del self.buf[:total]
            out.append(Packet(raw[2], raw[4:4 + length]))


@dataclass
class RfidInfo:
    uuid: str
    barcode: str
    serial: str
    total: int
    used: int
    label_type: int

    @property
    def remaining(self) -> int:
        return max(0, self.total - self.used)


@dataclass
class PrinterStatus:
    battery: int | None = None  # 1..4
    lid_closed: bool | None = None


@dataclass
class DeviceInfo:
    serial: str = ""
    software_version: str = ""
    hardware_version: str = ""
    device_type: int = 0
    density: int = 0
    label_type: int = 0
    auto_shutdown: int = 0
    battery: int = 0


async def discover(timeout: float = 6.0) -> list[tuple[str, str]]:
    """Return [(address, name)] of nearby Niimbot printers."""
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    result = []
    for addr, (dev, adv) in found.items():
        name = dev.name or adv.local_name or ""
        uuids = [u.lower() for u in adv.service_uuids]
        if SERVICE_UUID in uuids:
            result.append((addr, name))
    return result


class NiimbotPrinter:
    def __init__(self, address: str):
        self.address = address
        self.client: BleakClient | None = None
        self._parser = PacketParser()
        self._queue: asyncio.Queue[Packet] = asyncio.Queue()
        self._lock = asyncio.Lock()
        self.on_disconnect = None
        self.last_error: int | None = None
        self.page_index = 0  # updated by unsolicited In_PrinterPageIndex (0xE0)

    # ---------------------------------------------------------------- transport
    async def connect(self):
        self.client = BleakClient(self.address, timeout=20,
                                  disconnected_callback=self._disconnected)
        await self.client.connect()
        await self.client.start_notify(CHAR_UUID, self._notify)

    def _disconnected(self, _client):
        if self.on_disconnect:
            self.on_disconnect()

    @property
    def connected(self) -> bool:
        return bool(self.client and self.client.is_connected)

    async def disconnect(self):
        if self.client:
            try:
                await self.client.disconnect()
            finally:
                self.client = None

    def _notify(self, _h, data: bytearray):
        for p in self._parser.feed(bytes(data)):
            if p.cmd == 0xDB and p.data:  # unsolicited print error
                self.last_error = p.data[0]
            elif p.cmd == 0xE0 and len(p.data) >= 2:  # D11 reports finished copies this way
                self.page_index = struct.unpack(">H", p.data[:2])[0]
            self._queue.put_nowait(p)

    async def send(self, cmd: int, data: bytes = b""):
        await self.client.write_gatt_char(CHAR_UUID, make_packet(cmd, data), response=False)

    async def request(self, cmd: int, data: bytes = b"\x01", expect: int | None = None,
                      timeout: float = 2.0, retries: int = 2) -> Packet | None:
        """Send a command and wait for the response packet with code `expect`."""
        async with self._lock:
            loop = asyncio.get_running_loop()
            for _ in range(retries + 1):
                while not self._queue.empty():
                    self._queue.get_nowait()
                await self.send(cmd, data)
                deadline = loop.time() + timeout
                while (left := deadline - loop.time()) > 0:
                    try:
                        p = await asyncio.wait_for(self._queue.get(), left)
                    except asyncio.TimeoutError:
                        break
                    if p.cmd == 0xDB and p.data:
                        raise PrinterError(PRINT_ERRORS.get(p.data[0], f"Printer error 0x{p.data[0]:02x}"))
                    if expect is None or p.cmd == expect:
                        return p
            return None

    # ---------------------------------------------------------------- queries
    async def get_info(self, key: Info) -> bytes | None:
        p = await self.request(Cmd.PRINTER_INFO, bytes([key]), expect=0x40 + key)
        return p.data if p else None

    async def get_device_info(self) -> DeviceInfo:
        di = DeviceInfo()

        def num(b):
            return int.from_bytes(b, "big") if b else 0

        def ver(b):
            return f"{num(b) / 100:.2f}" if b else ""

        if b := await self.get_info(Info.SERIAL):
            # D11 firmware returns the serial as raw BCD-ish bytes (CA 30 50 14 00)
            di.serial = b.decode() if all(32 <= c < 127 for c in b) else b.rstrip(b"\x00").hex().upper()
        di.software_version = ver(await self.get_info(Info.SOFTWARE_VERSION))
        di.hardware_version = ver(await self.get_info(Info.HARDWARE_VERSION))
        di.device_type = num(await self.get_info(Info.DEVICE_TYPE))
        di.density = num(await self.get_info(Info.DENSITY))
        di.label_type = num(await self.get_info(Info.LABEL_TYPE))
        di.auto_shutdown = num(await self.get_info(Info.AUTO_SHUTDOWN))
        di.battery = num(await self.get_info(Info.BATTERY))
        return di

    async def get_rfid(self) -> RfidInfo | None:
        p = await self.request(Cmd.RFID_INFO, b"\x01", expect=0x1B)
        if not p or len(p.data) < 2 or p.data[0] == 0:
            return None
        d = p.data
        i = 0
        uuid = d[i:i + 8].hex(":").upper(); i += 8
        n = d[i]; i += 1
        barcode = d[i:i + n].decode(errors="replace"); i += n
        n = d[i]; i += 1
        serial = d[i:i + n].decode(errors="replace"); i += n
        total, used = struct.unpack(">HH", d[i:i + 4]); i += 4
        ltype = d[i] if i < len(d) else 0
        return RfidInfo(uuid, barcode, serial, total, used, ltype)

    async def heartbeat(self) -> PrinterStatus:
        st = PrinterStatus()
        p = await self.request(Cmd.HEARTBEAT, b"\x01", timeout=1.5, retries=1)
        if not p:
            return st
        d = p.data
        if p.cmd == 0xDD and len(d) == 10:
            st.lid_closed = d[8] == 1  # verified on D11: 1 = closed
            st.battery = d[9]
        elif p.cmd == 0xDD and len(d) >= 13:
            st.lid_closed = d[9] == 0
            st.battery = d[10]
        elif p.cmd == 0xD9 and len(d) >= 10:
            st.lid_closed = d[7] == 0
            st.battery = d[9]
        return st

    # ---------------------------------------------------------------- settings
    async def set_auto_shutdown(self, value: int) -> bool:
        """value: 1=15 min, 2=30 min, 3=45 min, 4=60 min."""
        p = await self.request(Cmd.SET_AUTO_SHUTDOWN, bytes([value]), expect=0x37)
        return bool(p and p.data[:1] == b"\x01")

    async def set_sound(self, item: int, on: bool) -> bool:
        """item: 1 = Bluetooth connect sound, 2 = power sound."""
        p = await self.request(Cmd.SOUND_SETTINGS, bytes([1, item, int(on)]), expect=0x68)
        return bool(p and p.data[:1] == b"\x01")

    # ---------------------------------------------------------------- printing
    @staticmethod
    def encode_rows(img: Image.Image):
        """Yield (cmd, payload) packets for a 1-bit image (black = print)."""
        img = img.convert("1")
        w, h = img.size
        bpr = (w + 7) // 8
        # Pillow mode "1": 0 = black. Invert so set bits mean "burn".
        raw = bytes(b ^ 0xFF for b in img.tobytes())
        rows = [raw[y * bpr:(y + 1) * bpr] for y in range(h)]
        y = 0
        while y < h:
            row = rows[y]
            repeat = 1
            while y + repeat < h and rows[y + repeat] == row and repeat < 255:
                repeat += 1
            if not any(row):
                yield Cmd.PRINT_EMPTY_ROW, struct.pack(">HB", y, repeat)
            else:
                third = bpr // 3
                bounds = [(0, third), (third, 2 * third), (2 * third, bpr)]
                counts = [min(255, sum(bin(b).count("1") for b in row[a:z])) for a, z in bounds]
                yield Cmd.PRINT_BITMAP_ROW, struct.pack(">HBBBB", y, *counts, repeat) + row
            y += repeat

    @staticmethod
    def prepare_image(label_img: Image.Image, offset_x: int = 0, offset_y: int = 0) -> Image.Image:
        """Rotate a landscape label image (length x height) into printhead orientation (96 wide).

        offset_x/offset_y shift the content in label (landscape) coordinates, in dots;
        positive = right/down. Used to compensate for printer/label alignment.
        """
        img = label_img.convert("1")
        if offset_x or offset_y:
            shifted = Image.new("1", img.size, 1)
            shifted.paste(img, (offset_x, offset_y))
            img = shifted
        if img.width > img.height:
            img = img.rotate(-90, expand=True)  # D11 prints 'left': label's left edge feeds first
        if img.width < PRINTHEAD_PX:
            canvas = Image.new("1", (PRINTHEAD_PX, img.height), 1)
            canvas.paste(img, ((PRINTHEAD_PX - img.width) // 2, 0))
            img = canvas
        elif img.width > PRINTHEAD_PX:
            x0 = (img.width - PRINTHEAD_PX) // 2
            img = img.crop((x0, 0, x0 + PRINTHEAD_PX, img.height))
        return img

    async def print_image(self, label_img: Image.Image, quantity: int = 1, density: int = 2,
                          label_type: int = LabelType.WITH_GAPS, progress=None,
                          offset_x: int = 0, offset_y: int = 0):
        """Print a label image. progress(copies_done, total) is called as copies finish."""
        img = self.prepare_image(label_img, offset_x, offset_y)
        self.last_error = None
        self.page_index = 0

        async def step(cmd, data, expect, what):
            p = await self.request(cmd, data, expect=expect)
            if p is None:
                raise PrinterError(f"No response to {what}")
            return p

        await step(Cmd.SET_DENSITY, bytes([density]), 0x31, "set density")
        await step(Cmd.SET_LABEL_TYPE, bytes([label_type]), 0x33, "set label type")
        await step(Cmd.PRINT_START, b"\x01", 0x02, "print start")
        await self.request(Cmd.PRINT_CLEAR, b"\x01", expect=0x30, retries=0)
        await step(Cmd.PAGE_START, b"\x01", 0x04, "page start")
        await step(Cmd.SET_PAGE_SIZE, struct.pack(">HH", img.height, img.width), 0x14, "set page size")
        await step(Cmd.PRINT_QUANTITY, struct.pack(">H", quantity), 0x16, "set quantity")

        for i, (cmd, payload) in enumerate(self.encode_rows(img)):
            await self.send(cmd, payload)
            # The D11 has a tiny receive buffer: bursting rows gives "Data error" (0x06).
            await asyncio.sleep(ROW_PACKET_DELAY)
            if self.last_error:
                raise PrinterError(PRINT_ERRORS.get(self.last_error, f"Printer error 0x{self.last_error:02x}"))

        await step(Cmd.PAGE_END, b"\x01", 0xE4, "page end")

        # Wait until the printer reports every copy done. The D11 pushes
        # In_PrinterPageIndex (0xE0); newer firmware answers PrintStatus (0xA3).
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 20 + 5 * quantity
        last_reported = -1
        while loop.time() < deadline:
            if self.last_error:
                raise PrinterError(PRINT_ERRORS.get(self.last_error, f"Printer error 0x{self.last_error:02x}"))
            p = await self.request(Cmd.PRINT_STATUS, b"", expect=0xB3, timeout=0.4, retries=0)
            if p and len(p.data) >= 4:
                self.page_index = max(self.page_index, struct.unpack(">H", p.data[:2])[0])
            if progress and self.page_index != last_reported:
                last_reported = self.page_index
                progress(self.page_index, quantity)
            if self.page_index >= quantity:
                break
        else:
            raise PrinterError("Timed out waiting for the print to finish")

        for _ in range(20):
            p = await self.request(Cmd.PRINT_END, b"\x01", expect=0xF4)
            if p and p.data[:1] == b"\x01":
                return
            await asyncio.sleep(0.1)
