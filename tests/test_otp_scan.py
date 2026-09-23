"""Tests for otp.py: scan_otp behavior + dump roundtrips (§4.21).

test_otp_profile.py covers profiles; this covers the scan engine
(paced/retry/merge), NACK bucketing, and the JSON/binary persistence.
"""

import tempfile
import os
import unittest
from unittest.mock import MagicMock, patch

from cd3217_analyzer.otp import (
    OTPDump,
    load_dump_binary,
    load_dump_json,
    save_dump_binary,
    save_dump_json,
    scan_otp,
)


class _Adapter:
    def __init__(self, reader):
        self.reader = reader

    def read_bytes(self, address, register, length):
        return self.reader(address, register, length)


class TestScanOtp(unittest.TestCase):
    def test_healthy_chip_32_registers(self):
        def reader(addr, reg, length):
            return (0x04 + reg).to_bytes(1, "little") * length

        dump = scan_otp(_Adapter(reader), 0x38, label="t")
        self.assertEqual(32, len(dump.registers))
        self.assertEqual(0, len(dump.read_errors))
        self.assertEqual(dump.registers[0x00].hex(), "04040404")

    def test_nacked_register_bucketed_as_error(self):
        def reader(addr, reg, length):
            if reg >= 0x70:
                raise IOError("nack")
            return b"\xaa" * length

        dump = scan_otp(_Adapter(reader), 0x3B)
        self.assertIn(0x70, dump.read_errors)
        self.assertEqual(28, len(dump.registers))

    def test_truncated_read_is_merged(self):
        """First response has an 0xFF tail; the spaced re-read supplies
        the missing bytes — merged result must be complete (§4.17)."""
        flips = {"n": 0}

        def reader(addr, reg, length):
            if reg == 0x2C:   # a chunk whose read truncates first pass
                flips["n"] += 1
                if flips["n"] % 2:
                    return bytes(b"\x04AP \xff\xff")[:length]
                return bytes([0x04, 0x41, 0x50, 0x20])[:length]
            return (0x11).to_bytes(1, "little") * length

        with patch("cd3217_analyzer.otp.time.sleep") as _:
            dump = scan_otp(_Adapter(reader), 0x38)
        merged = dump.registers[0x2C]
        self.assertNotIn(0xFF, merged)
        self.assertEqual(merged, bytes([0x04]) + b"AP ")


class TestDumpRoundtrip(unittest.TestCase):
    def _sample(self):
        return OTPDump(
            address=0x3B, label="0x3B", timestamp="2026-01-01T00:00:00",
            registers={0x00: b"\x04\x28\x00\x00",
                       0x2F: b"@CD3217 "},
            read_errors=[0x7C], notes="unit",
        )

    def test_json_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "d.json")
            save_dump_json(self._sample(), path)
            loaded = load_dump_json(path)
        self.assertEqual(loaded.address, 0x3B)
        self.assertEqual(loaded.registers[0x00], b"\x04\x28\x00\x00")
        self.assertEqual(loaded.registers[0x2F], b"@CD3217 ")
        self.assertIn(0x7C, loaded.read_errors)

    def test_binary_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "d.otp.bin")
            save_dump_binary(self._sample(), path)
            loaded = load_dump_binary(path)
        self.assertEqual(loaded.registers[0x00], b"\x04\x28\x00\x00")

    def test_load_bad_file_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "missing.json")
            self.assertIsNone(load_dump_json(missing))


if __name__ == "__main__":
    unittest.main()
