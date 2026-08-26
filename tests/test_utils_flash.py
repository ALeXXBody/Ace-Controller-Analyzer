"""Tests for utils, flash helpers, models, and OTP."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from cd3217_analyzer.flash import SPIFlash, FlashError, FlashInfo
from cd3217_analyzer.models import get_model, model_ids
from cd3217_analyzer.otp import OTPDump, diff_dumps, format_dump_table, save_dump_json, load_dump_json
from cd3217_analyzer.utils import parse_address_list, parse_hex_address, unique_sorted


class TestUtils(unittest.TestCase):
    def test_parse_hex_address(self):
        self.assertEqual(parse_hex_address("0x38"), 0x38)
        self.assertEqual(parse_hex_address("38"), 0x38)
        self.assertEqual(parse_hex_address("3F"), 0x3F)
        self.assertEqual(parse_hex_address("56"), 0x56)

    def test_parse_address_list(self):
        self.assertEqual(parse_address_list("0x38, 0x3F"), [0x38, 0x3F])

    def test_unique_sorted(self):
        self.assertEqual(unique_sorted([3, 1, 2, 1]), [1, 2, 3])


class TestModels(unittest.TestCase):
    def test_models_present(self):
        self.assertIn("A2442", model_ids())
        m = get_model("a2442")
        self.assertIsNotNone(m)
        self.assertEqual(m.chip_count, 4)


class TestOTP(unittest.TestCase):
    def test_diff_and_json_roundtrip(self):
        a = OTPDump(address=0x38, label="a", timestamp="t")
        b = OTPDump(address=0x38, label="b", timestamp="t")
        a.registers[0x00] = b"\x01\x02\x03\x04"
        a.registers[0x10] = b"\xaa\xbb\xcc\xdd"
        b.registers[0x00] = b"\x01\x02\x03\x04"
        b.registers[0x10] = b"\x11\x22\x33\x44"
        result = diff_dumps(a, b)
        self.assertEqual(result.match_count, 1)
        self.assertEqual(result.diff_count, 1)
        table = format_dump_table(a, show_zeros=True)
        self.assertIn("0x00", table)

        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td) / "dump.json")
            save_dump_json(a, path)
            loaded = load_dump_json(path)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.registers[0x00], a.registers[0x00])


class TestFlashReadOffset(unittest.TestCase):
    def test_read_strips_command_address_prefix(self):
        spi = MagicMock()
        # exchange returns cmd+addr garbage then payload
        payload = bytes(range(16))
        spi.transfer.return_value = b"\x00\x00\x00\x00" + payload
        flash = SPIFlash(spi)
        data = flash.read(0x100, 16)
        self.assertEqual(data, payload)
        sent = spi.transfer.call_args[0][0]
        self.assertEqual(sent[0], 0x03)
        self.assertEqual(len(sent), 4 + 16)

    def test_full_restore_unknown_size(self):
        spi = MagicMock()
        flash = SPIFlash(spi)
        flash.info = FlashInfo(0xEF, 0x40, 0x14, (0xEF, 0x40, 0x14), "x", 0)
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"\x00" * 16)
            path = f.name
        with self.assertRaises(FlashError):
            flash.full_restore(path)


if __name__ == "__main__":
    unittest.main()
