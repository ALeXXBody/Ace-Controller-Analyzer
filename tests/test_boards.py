"""Tests for the extended INFO frame + board knowledge base."""

import unittest

from cd3217_analyzer.boards import (
    BOARDS,
    HW_ESP32,
    HW_RP2040,
    board_from_info,
    get_board_info,
)
from cd3217_analyzer.usb_bridge import UsbBridgeAdapter
from tests.test_usb_bridge import make_response

CMD_INFO = 0x05


def info_resp(board: bytes, sda, scl, sck=None, miso=None, mosi=None,
              cs=None, hw=None):
    payload = bytes([len(board)]) + board + bytes(
        x if x is not None else 0 for x in (sda, scl, sck, miso, mosi, cs, hw))
    return make_response(CMD_INFO, payload)


class TestInfoParsing(unittest.TestCase):
    def make(self, responses):
        from tests.test_usb_bridge import FakeSerial
        fake = FakeSerial(responses)
        adapter = UsbBridgeAdapter(port="COM9")
        adapter._ser = fake
        return adapter

    def test_full_info(self):
        a = self.make([info_resp(b"pico1", 4, 5, 14, 12, 15, 13, HW_RP2040)])
        info = a.info()
        self.assertEqual(info["board"], "pico1")
        self.assertEqual(info["sda"], 4)
        self.assertEqual(info["scl"], 5)
        self.assertEqual(info["spi_sck"], 14)
        self.assertEqual(info["spi_miso"], 12)
        self.assertEqual(info["spi_mosi"], 15)
        self.assertEqual(info["spi_cs"], 13)
        self.assertEqual(info["hw"], HW_RP2040)

    def test_old_firmware_short_info(self):
        # v0.3.x firmware sends only [boardlen][board][sda][scl]
        payload = bytes([5]) + b"pico1" + bytes([4, 5])
        a = self.make([make_response(CMD_INFO, payload)])
        info = a.info()
        self.assertEqual(info["board"], "pico1")
        self.assertEqual(info["sda"], 4)
        self.assertEqual(info["scl"], 5)
        self.assertEqual(info["spi_sck"], None)
        self.assertEqual(info["hw"], None)


class TestBoardTable(unittest.TestCase):
    def test_all_boards_have_both_pin_sets(self):
        for key, b in BOARDS.items():
            self.assertIn("sda", b.i2c, key)
            self.assertIn("scl", b.i2c, key)
            for role in ("sck", "miso", "mosi", "cs"):
                self.assertIn(role, b.spi, f"{key}.{role}")

    def test_lookup_by_firmware_name(self):
        for key in ("pico1", "pico2", "pico-w", "pico2-w", "rp2040-zero",
                    "esp32-s3-devkitc-1", "esp32-c3-supermini",
                    "esp32-devkit"):
            self.assertIsNotNone(get_board_info(key), key)

    def test_lookup_case_insensitive(self):
        self.assertEqual(get_board_info("PICO1").key, "pico1")
        self.assertIsNone(get_board_info("nonexistent-board"))
        self.assertIsNone(get_board_info(None))

    def test_board_from_info_known(self):
        b = board_from_info({"board": "pico2", "sda": 4, "scl": 5,
                             "spi_sck": 14, "spi_miso": 12, "spi_mosi": 15,
                             "spi_cs": 13, "hw": HW_RP2040})
        self.assertEqual(b.key, "pico2")
        self.assertEqual(b.i2c["sda"][0], 4)
        self.assertEqual(b.spi["cs"][0], 13)

    def test_board_from_info_unknown_synthesizes(self):
        b = board_from_info({"board": "my-custom-board", "sda": 21,
                             "scl": 22, "spi_sck": 18, "spi_miso": 19,
                             "spi_mosi": 23, "spi_cs": 5, "hw": HW_ESP32})
        self.assertEqual(b.name, "my-custom-board")
        self.assertEqual(b.family, "ESP32")
        self.assertEqual(b.i2c["sda"], (21, "GPIO21"))
        self.assertEqual(b.spi["sck"], (18, "GPIO18"))

    def test_board_from_info_empty(self):
        self.assertIsNone(board_from_info({}))
        self.assertIsNone(board_from_info(None))


if __name__ == "__main__":
    unittest.main()
