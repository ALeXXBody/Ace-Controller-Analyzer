"""Tests for SPI flash over the USB board bridge (spi_bridge.py)."""

import unittest

from cd3217_analyzer.spi_bridge import (
    BRIDGE_SPI_CHUNK,
    SPI_DATA_CHUNK,
    BridgeSPIAdapter,
    BridgeSPIFlash,
)
from cd3217_analyzer.usb_bridge import MAGIC, UsbBridgeAdapter
from tests.test_usb_bridge import make_response

CMD_SPI_XFR = 0x10
CMD_READ_DATA = 0x03
CMD_PAGE_PROGRAM = 0x02
CMD_WRITE_ENABLE = 0x06
CMD_READ_STATUS = 0x05


def xfr_resp(rx: bytes) -> bytes:
    """Bridge response frame for a SPI_XFR: [status=0][rx...]."""
    return make_response(CMD_SPI_XFR, bytes([0x00]) + bytes(rx))


class SerialScript:
    """pyserial stand-in that serves one scripted response per write.

    Unlike FakeSerial (which replays queue[0] forever), this pops a response
    for every write — the right model for multi-transaction SPI flows.
    """

    def __init__(self, responses):
        self._queue = list(responses)
        self.write_calls = []
        self._current = b""

    def reset_input_buffer(self):
        pass

    def write(self, frame):
        self.write_calls.append(bytes(frame))
        self._current = self._queue.pop(0) if self._queue else b""

    def read(self, n):
        n = int(n)
        out = self._current[:n]
        self._current = self._current[n:]
        return out

    def close(self):
        pass


class TestBridgeSPIAdapter(unittest.TestCase):
    def make(self, responses):
        fake = SerialScript(responses)
        adapter = UsbBridgeAdapter(port="COM9")
        adapter._ser = fake
        return BridgeSPIAdapter(adapter), fake

    def test_transfer_round_trip(self):
        adapter, fake = self.make([xfr_resp(b"\xAA\xBB\xCC")])
        rx = adapter.transfer(b"\x9F\x00\x00\x00")
        self.assertEqual(rx, b"\xAA\xBB\xCC")
        # frame sent: [MAGIC][0x10][4][9F 00 00 00][ck]
        frame = fake.write_calls[0]
        self.assertEqual(frame[0], MAGIC)
        self.assertEqual(frame[1], CMD_SPI_XFR)
        self.assertEqual(frame[3:7], b"\x9F\x00\x00\x00")

    def test_transfer_too_large_raises(self):
        adapter, _ = self.make([])
        with self.assertRaises(ValueError):
            adapter.transfer(b"\x00" * (BRIDGE_SPI_CHUNK + 1))

    def test_bridge_status_error_raises(self):
        adapter, _ = self.make([make_response(CMD_SPI_XFR, b"\xFF")])
        with self.assertRaises(IOError):
            adapter.transfer(b"\x9F")

    def test_chunk_constants(self):
        # SPI command header (opcode + 3-byte addr) must fit with the data.
        self.assertEqual(BRIDGE_SPI_CHUNK, 240)
        self.assertEqual(SPI_DATA_CHUNK, 236)


class TestBridgeSPIFlash(unittest.TestCase):
    def make(self, responses):
        fake = SerialScript(responses)
        usb = UsbBridgeAdapter(port="COM9")
        usb._ser = fake
        spi = BridgeSPIAdapter(usb)
        return BridgeSPIFlash(spi), fake

    def test_read_chunking(self):
        # read(500) -> chunks of 236 + 236 + 28
        d1 = bytes(i & 0xFF for i in range(236))
        d2 = bytes((i + 1) & 0xFF for i in range(236))
        d3 = bytes((i + 2) & 0xFF for i in range(28))
        flash, fake = self.make([
            xfr_resp(b"\x00" * 4 + d1),   # chunk 1: garbage hdr + data
            xfr_resp(b"\x00" * 4 + d2),   # chunk 2
            xfr_resp(b"\x00" * 4 + d3),   # chunk 3
        ])
        data = flash.read(0x100, 500)
        self.assertEqual(data, d1 + d2 + d3)
        self.assertEqual(len(fake.write_calls), 3)
        # each chunk re-issues 0x03 with the running address
        c1, c2, c3 = fake.write_calls
        self.assertEqual(c1[3], CMD_READ_DATA)
        self.assertEqual(c1[4:7], b"\x00\x01\x00")       # addr 0x100
        self.assertEqual(c2[4:7], b"\x00\x01\xEC")       # 0x100+236
        self.assertEqual(c3[4:7], b"\x00\x02\xD8")       # 0x100+472
        self.assertEqual(len(c1), 3 + 4 + 236 + 1)       # frame incl. magic/plen/ck

    def test_read_single_chunk(self):
        payload = bytes(range(16))
        flash, fake = self.make([xfr_resp(b"\x00" * 4 + payload)])
        self.assertEqual(flash.read(0, 16), payload)
        self.assertEqual(len(fake.write_calls), 1)

    def test_write_page_splitting(self):
        # 256-byte page -> 236 + 20 partial page programs, each:
        # WREN xfr, WEL status check, PP xfr, busy poll
        wel_set = xfr_resp(b"\x00\x02")      # status read: WEL set
        not_busy = xfr_resp(b"\x00\x00")     # status read: idle
        wren = xfr_resp(b"\x00")             # WREN opcode echo
        pp_ok = xfr_resp(b"\x00" * 240)      # PP (4+236 bytes clocked)
        pp_ok2 = xfr_resp(b"\x00" * 24)      # PP (4+20 bytes clocked)
        flash, fake = self.make([
            wren, wel_set, pp_ok, not_busy,    # chunk 1 (236 B)
            wren, wel_set, pp_ok2, not_busy,   # chunk 2 (20 B)
        ])
        page = bytes((i * 7) & 0xFF for i in range(256))
        flash.write_page(0x40, page)
        # 8 transfers total (2 per phase x 2 chunks)
        self.assertEqual(len(fake.write_calls), 8)
        # verify PP frames carry the page data, split at 236
        pp_frames = [f for f in fake.write_calls if f[3] == CMD_PAGE_PROGRAM]
        self.assertEqual(len(pp_frames), 2)
        self.assertEqual(pp_frames[0][7:7 + 236], page[:236])
        self.assertEqual(pp_frames[0][4:7], b"\x00\x00\x40")
        self.assertEqual(pp_frames[1][7:7 + 20], page[236:])
        self.assertEqual(pp_frames[1][4:7], b"\x00\x01\x2C")  # 0x40+236

    def test_jedec_detect(self):
        # JEDEC read: transfer(9F 00 00 00) -> rx = [x, mfr, type, cap]
        flash, fake = self.make([xfr_resp(b"\x00\xEF\x40\x14")])
        info = flash.detect()
        self.assertEqual(info.jedec_id, (0xEF, 0x40, 0x14))
        self.assertEqual(info.size_mb, 1.0)
        self.assertIn("W25Q80", info.name)


if __name__ == "__main__":
    unittest.main()
