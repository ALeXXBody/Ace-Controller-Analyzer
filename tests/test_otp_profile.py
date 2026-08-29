"""Tests for golden OTP profiles (save/load/verify)."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cd3217_analyzer.otp import OTPDump
from cd3217_analyzer import otp_profile as op


def _dump(regs=None, errors=None):
    return OTPDump(
        address=0x3B, label="0x3B", timestamp="2026-01-01T00:00:00",
        registers=regs or {}, read_errors=errors or [],
    )


class TestOTPProfile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.object(op, "profile_dir",
                               return_value=Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_roundtrip_save_load(self):
        dump = _dump(regs={0x00: b"\x04\x28\x00\x00",
                           0x28: b"\xaa\xbb\xcc\xdd"})
        path = op.save_profile(dump, "A2485", "UG400",
                               silicon="CD3217B12", chip_class="otp",
                               source="test board")
        self.assertTrue(path.exists())
        p = op.load_profile("A2485", "UG400")
        self.assertIsNotNone(p)
        self.assertEqual(p.address, 0x3B)
        self.assertEqual(p.silicon, "CD3217B12")
        self.assertEqual(p.chip_class, "otp")
        self.assertEqual(p.registers[0x28], b"\xaa\xbb\xcc\xdd")

    def test_load_missing_returns_none(self):
        self.assertIsNone(op.load_profile("A2485", "NOPE"))

    def test_verify_match(self):
        regs = {0x00: b"\x04\x28\x00\x00", 0x28: b"\xaa\xbb\xcc\xdd"}
        dump = _dump(regs=dict(regs))
        profile = op.OTPProfile(model_id="A2485", ref="UG400", address=0x3B,
                                registers=dict(regs))
        lines = op.verify_dump(dump, profile)
        self.assertIn("MATCH", lines[0])
        self.assertNotIn("MISMATCH", lines[0])

    def test_verify_mismatch_reports_registers(self):
        profile = op.OTPProfile(
            model_id="A2485", ref="UG400", address=0x3B,
            registers={0x00: b"\x04\x28\x00\x00", 0x28: b"\xaa\xbb\xcc\xdd"})
        dump = _dump(regs={0x00: b"\x04\x28\x00\x00",     # same
                           0x28: b"\x00\x00\x00\x00"})    # different
        lines = op.verify_dump(dump, profile)
        self.assertIn("MISMATCH", lines[0])
        joined = "\n".join(lines)
        self.assertIn("0x28", joined)
        self.assertIn("aabbccdd", joined)

    def test_verify_missing_register_counts(self):
        profile = op.OTPProfile(
            model_id="A2485", ref="UG400", address=0x3B,
            registers={0x00: b"\x04\x28\x00\x00", 0x10: b"\x01\x02\x03\x04"})
        dump = _dump(regs={0x00: b"\x04\x28\x00\x00"})  # 0x10 missing
        lines = op.verify_dump(dump, profile)
        self.assertIn("MISMATCH", lines[0])
        self.assertIn("0x10", "\n".join(lines))


if __name__ == "__main__":
    unittest.main()
