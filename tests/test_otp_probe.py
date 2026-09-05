"""Tests for the sacrificial-chip OTP probe tools (otp_probe)."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from cd3217_analyzer.otp import OTPDump, diff_dumps
from cd3217_analyzer.otp_probe import (
    PageProbeResult,
    compare_to_golden,
    format_write_report,
    load_golden_dump,
    probe_extended_page,
    probe_writability,
    read_register_safe,
)


def _mk_adapter(regs: dict, writable: set = frozenset(),
                nacked: set = frozenset()):
    """Mock adapter: regs maps offset -> 4 bytes; only `writable`
    offsets accept writes (readback changes); `nacked` never answer."""
    mock = MagicMock()
    state = {k: bytes(v) for k, v in regs.items()}

    def read(a, r, l):
        if r in nacked:
            raise IOError("nack")
        return state.get(r, b"\xff" * l)[:l]

    def write(a, r, data):
        if r in nacked or r not in writable:
            return False
        state[r] = bytes(data)
        return True

    mock.read_bytes.side_effect = read
    mock.write_bytes.side_effect = write
    mock.ping.return_value = True
    return mock


class TestReadRegisterSafe(unittest.TestCase):
    def test_returns_merged_data(self):
        a = _mk_adapter({0x28: b"\xaa\xbb\xcc\xdd"})
        self.assertEqual(read_register_safe(a, 0x3B, 0x28),
                         b"\xaa\xbb\xcc\xdd")

    def test_nack_returns_none(self):
        a = _mk_adapter({}, nacked={0xF0})
        self.assertIsNone(read_register_safe(a, 0x3B, 0xF0))


class TestExtendedPage(unittest.TestCase):
    def test_buckets_data_ff_and_nack(self):
        regs = {0x80: b"\x01\x02\x03\x04",
                0x84: b"\xff" * 4}
        a = _mk_adapter(regs, nacked={0x88})
        res = probe_extended_page(a, 0x3B, start=0x80, end=0x88)
        self.assertIsInstance(res, PageProbeResult)
        self.assertEqual(res.with_data[0x80], b"\x01\x02\x03\x04")
        self.assertIn(0x84, res.all_ff)
        self.assertIn(0x88, res.nacked)
        self.assertIn("hidden OTP page", res.summary())

    def test_default_range_covers_to_fc(self):
        a = _mk_adapter({})
        res = probe_extended_page(a, 0x38)
        self.assertEqual(res.start, 0x80)
        self.assertEqual(res.end, 0xFC)


class TestWriteProbe(unittest.TestCase):
    def test_writable_register_flagged_and_restored(self):
        regs = {0x2C: b"\x02\x00\x0e\x00"}
        a = _mk_adapter(regs, writable={0x2C})
        results = probe_writability(a, 0x3B, start=0x2C, end=0x2C)
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r.verdict, "WRITABLE")
        self.assertTrue(r.restored)
        self.assertEqual(r.original, b"\x02\x00\x0e\x00")
        self.assertEqual(r.test_value, b"\x03\x00\x0e\x00")
        # restore verified by reading back through the mock
        self.assertEqual(read_register_safe(a, 0x3B, 0x2C),
                         b"\x02\x00\x0e\x00")

    def test_readonly_register_rejected_and_untouched(self):
        regs = {0x00: b"\x04\x28\x00\x00"}
        a = _mk_adapter(regs, writable=set())
        results = probe_writability(a, 0x3B, start=0x00, end=0x00)
        r = results[0]
        self.assertEqual(r.verdict, "REJECTED")
        self.assertTrue(r.restored)
        self.assertEqual(r.original, b"\x04\x28\x00\x00")

    def test_unreadable_register_reported(self):
        a = _mk_adapter({}, nacked={0x50})
        results = probe_writability(a, 0x38, start=0x50, end=0x50)
        self.assertEqual(results[0].verdict, "UNREADABLE")
        self.assertEqual(results[0].original, b"")

    def test_default_range_skips_vid(self):
        seen = []
        a = _mk_adapter({off: b"\x00" * 4 for off in
                         range(0x00, 0x7D, 4)},
                        writable=set(range(0x00, 0x7D, 4)))
        orig_write = a.write_bytes.side_effect

        def spy(a_, r_, d):
            seen.append(r_)
            return orig_write(a_, r_, d)

        a.write_bytes.side_effect = spy
        probe_writability(a, 0x38)
        self.assertNotIn(0x00, seen)
        self.assertNotIn(0x04, seen)
        self.assertIn(0x08, seen)
        self.assertIn(0x7C, seen)

    def test_report_groups_and_warns(self):
        from cd3217_analyzer.otp_probe import WriteProbeResult
        results = [
            WriteProbeResult(0x2C, b"\x00" * 4, b"\x01" * 4,
                             b"\x01" * 4, True, "WRITABLE"),
            WriteProbeResult(0x00, b"\x04" * 4, b"\x05" * 4,
                             b"\x04" * 4, True, "REJECTED"),
            WriteProbeResult(0x50, b"\x06" * 4, b"\x07" * 4,
                             None, False, "UNEXPECTED"),
        ]
        report = format_write_report(results)
        self.assertIn("WRITABLE (RAM-backed):  1", report)
        self.assertIn("REJECTED (read-only):   1", report)
        self.assertIn("RESTORE FAILED", report)


class TestGoldenDiff(unittest.TestCase):
    def test_load_from_saved_dump_json(self):
        dump = OTPDump(address=0x3B, label="0x3B",
                       timestamp="2026-01-01T00:00:00",
                       registers={0x00: b"\x04\x28\x00\x00"})
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "g.json")
            from cd3217_analyzer.otp import save_dump_json
            save_dump_json(dump, path)
            loaded = load_golden_dump(path)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.registers[0x00], b"\x04\x28\x00\x00")

    def test_load_from_export_bundle_with_chip(self):
        bundle = {
            "format": "cd3217-analyzer/board-export",
            "generated_utc": "2026-01-01T00:00:00Z",
            "data": {"otp_dump": {
                "0x3B": {"address": 59, "read_errors": [],
                         "registers": {"0x00": "04280000"}},
                "0x38": {"address": 56, "read_errors": [],
                         "registers": {"0x00": "04280000"}},
            }},
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "bundle.json")
            Path(path).write_text(json.dumps(bundle))
            loaded = load_golden_dump(path, "0x3B")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.address, 0x3B)
        self.assertEqual(loaded.registers[0x00], b"\x04\x28\x00\x00")

    def test_bundle_without_chip_selector_rejected(self):
        bundle = {"data": {"otp_dump": {
            "0x3B": {"registers": {}}, "0x38": {"registers": {}}}}}
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "b.json")
            Path(path).write_text(json.dumps(bundle))
            self.assertIsNone(load_golden_dump(path))

    def test_compare_to_golden_reuses_diff(self):
        golden = OTPDump(address=0x3B, label="golden",
                         timestamp="t",
                         registers={0x00: b"\x04\x28\x00\x00",
                                    0x2C: b"\x02\x00\x0e\x00"})
        live = OTPDump(address=0x3B, label="live", timestamp="t",
                       registers={0x00: b"\x04\x28\x00\x00",
                                  0x2C: b"\x02\x00\x0c\x00"})
        result = compare_to_golden(live, golden)
        self.assertEqual(result.different, [0x2C])
        self.assertEqual(result.identical, [0x00])


if __name__ == "__main__":
    unittest.main()
