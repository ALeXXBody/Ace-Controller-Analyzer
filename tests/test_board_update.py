"""Tests for board firmware versioning + update protocol (fw 0x30 cmd)."""

import unittest

from cd3217_analyzer.updater import (
    BOARD_FIRMWARE_ASSETS,
    board_firmware_asset,
    is_newer,
)
from cd3217_analyzer.usb_bridge import (
    CMD_FW_UPDATE,
    UsbBridgeAdapter,
)
from tests.test_usb_bridge import make_response
from tests.test_spi_bridge import SerialScript


def info_payload(board=b"pico1", pins=(4, 5, 14, 12, 15, 13, 0x01, 1),
                 version=b"0.6.1"):
    out = bytes([len(board)]) + board + bytes(pins)
    if version is not None:
        out += bytes([len(version)]) + version
    return make_response(0x05, out)


def fw_resp(ok=True):
    return make_response(CMD_FW_UPDATE, b"\x00" if ok else b"\xFF")


class TestInfoVersion(unittest.TestCase):
    def make(self, responses):
        fake = SerialScript(responses)
        a = UsbBridgeAdapter(port="COM9")
        a._ser = fake
        return a

    def test_version_parsed(self):
        a = self.make([info_payload(version=b"0.6.1")])
        info = a.info()
        self.assertEqual(info["version"], "0.6.1")

    def test_no_version_field(self):
        a = self.make([info_payload(version=None)])
        info = a.info()
        self.assertIsNone(info["version"])
        self.assertEqual(info["uart_rx"], 1)   # preceding fields intact

    def test_empty_version_string(self):
        a = self.make([info_payload(version=b"")])
        info = a.info()
        self.assertIsNone(info["version"])     # empty -> None


class TestFwUpdateFrames(unittest.TestCase):
    def make(self, responses):
        fake = SerialScript(responses)
        a = UsbBridgeAdapter(port="COM9")
        a._ser = fake
        return a, fake

    def test_begin_frame(self):
        a, fake = self.make([fw_resp()])
        a.fw_update_begin(123456)
        frame = fake.write_calls[0]
        self.assertEqual(frame[1], CMD_FW_UPDATE)
        self.assertEqual(frame[3], 0x00)                    # sub BEGIN
        self.assertEqual(frame[4:8], (123456).to_bytes(4, "little"))

    def test_chunk_frames(self):
        data = bytes(range(256))
        a, fake = self.make([fw_resp()] * 2)
        a.fw_update_chunk(data[:200])
        a.fw_update_chunk(data[200:])
        f1, f2 = fake.write_calls
        self.assertEqual(f1[3], 0x01)                       # sub CHUNK
        self.assertEqual(bytes(f1[4:204]), data[:200])
        self.assertEqual(bytes(f2[4:60]), data[200:])       # ck at f2[60]

    def test_chunk_size_limits(self):
        a, _ = self.make([])
        with self.assertRaises(ValueError):
            a.fw_update_chunk(b"")
        with self.assertRaises(ValueError):
            a.fw_update_chunk(b"\x00" * 201)

    def test_end_frame(self):
        a, fake = self.make([fw_resp()])
        a.fw_update_end()
        self.assertEqual(fake.write_calls[0][3], 0x02)

    def test_reboot_bootsel_frame(self):
        a, fake = self.make([fw_resp()])
        a.fw_reboot_bootsel()
        self.assertEqual(fake.write_calls[0][3], 0x03)

    def test_reboot_frame(self):
        a, fake = self.make([fw_resp()])
        a.fw_reboot()
        self.assertEqual(fake.write_calls[0][3], 0x04)

    def test_error_raises(self):
        a, _ = self.make([fw_resp(ok=False)])
        with self.assertRaises(IOError):
            a.fw_update_begin(100)

    def test_image_stream(self):
        data = bytes((i * 3) & 0xFF for i in range(450))    # 3 chunks
        progress = []
        a, fake = self.make([fw_resp()] * 5)                # begin+3+end
        a.fw_update_image(data, lambda d, t: progress.append((d, t)))
        self.assertEqual(len(fake.write_calls), 5)
        self.assertEqual(progress, [(200, 450), (400, 450), (450, 450)])


class TestBoardAssets(unittest.TestCase):
    def test_every_released_board_has_asset(self):
        # all boards the app can connect to, except the not-yet-released C6
        for board in ("pico1", "pico2", "pico-w", "pico2-w",
                      "rp2040-zero", "esp32-s3-devkitc-1",
                      "esp32-c3-supermini", "esp32-devkit"):
            self.assertIn(board, BOARD_FIRMWARE_ASSETS)

    def test_asset_lookup(self):
        self.assertEqual(board_firmware_asset("pico1"),
                         "cd3217_pico.uf2")
        self.assertEqual(board_firmware_asset("RP2040-Zero"),
                         "cd3217_rp2040_zero.uf2")
        self.assertIsNone(board_firmware_asset("esp32-c6-zero"))
        self.assertIsNone(board_firmware_asset(None))

    def test_update_needed_logic(self):
        # unknown firmware -> update; older -> update; same/newer -> no
        self.assertTrue(is_newer("0.6.1", "0.5.0"))
        self.assertFalse(is_newer("0.6.1", "0.6.1"))
        self.assertFalse(is_newer("0.6.1", "0.7.0"))


if __name__ == "__main__":
    unittest.main()
