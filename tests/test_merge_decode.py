"""Shared truncation-merge helper + canonical decode dispatch (fix 4).

Three copies of the 0xFF-merge existed (analyzer merged repair, otp.scan_otp,
otp_probe.read_register_safe) and the analyzer's merged path dropped
decoding for 0x36/0x3F/0x30 — merged DeviceInfo repairs decoded, merged
contract repairs didn't. One helper + one dispatch now.
"""

import unittest
from unittest.mock import MagicMock

from cd3217_analyzer.utils import merge_ff_reads
from cd3217_analyzer.analyzer import CD3217Analyzer, decode_register_value


class TestMergeFFReads(unittest.TestCase):
    def test_longest_read_defines_length(self):
        out = merge_ff_reads([b"\x04\x28", b"\x04\x28\x00\x00"])
        self.assertEqual(out, b"\x04\x28\x00\x00")

    def test_first_non_ff_wins_per_position(self):
        out = merge_ff_reads([b"\x01\xff\x03\xff",
                              b"\xff\x02\xff\x04"])
        self.assertEqual(out, b"\x01\x02\x03\x04")

    def test_all_ff_positions_stay_ff(self):
        out = merge_ff_reads([b"\xff" * 4, b"\x04\xff\xff\xff"])
        self.assertEqual(out, b"\x04\xff\xff\xff")

    def test_none_snapshots_ignored(self):
        self.assertEqual(merge_ff_reads([None, b"\x01\x02"]), b"\x01\x02")

    def test_empty(self):
        self.assertEqual(merge_ff_reads([]), b"")
        self.assertEqual(merge_ff_reads([None]), b"")


_NEEDS_DECODE = [
    (0x00, 0x2804),
    (0x03, 1),
    (0x04, 0x3249),
    (0x36, 0x00000000),
    (0x35, (200 << 10) | 100),
    (0x3F, 1),
]
_OFFSETS, _RAWS = zip(*_NEEDS_DECODE)


class TestDecodeDispatch(unittest.TestCase):
    def _mk_read(self, offset, raw):
        n = len(raw)
        from cd3217_analyzer.registers import REGISTERS
        reg_def = REGISTERS.get(offset)
        from cd3217_analyzer.analyzer import RegisterRead
        return RegisterRead(
            offset=offset,
            name=reg_def.name if reg_def else f"0x{offset:02X}",
            raw_bytes=raw,
            raw_value=int.from_bytes(raw, "little"),
            decoded=decode_register_value(
                offset, raw, int.from_bytes(raw, "little")),
        )

    def test_canonical_offsets_decode_nonempty(self):
        for offset, raw in _NEEDS_DECODE:
            with self.subTest(offset=offset):
                read = self._mk_read(offset, raw.to_bytes(4, "little"))
                self.assertTrue(read.decoded, f"no decode for 0x{offset:02X}")

    def _fake_analyzer(self, reads, offset):
        """A fake self for _read_register_merged whose read_register
        consumes `reads` in order (repeating the last when exhausted)."""
        from cd3217_analyzer.registers import REGISTERS
        from cd3217_analyzer.analyzer import RegisterRead
        seq = iter(reads)

        def read_register(address, off, length):
            try:
                data = next(seq)
            except StopIteration:
                data = reads[-1]
            reg_def = REGISTERS.get(off)
            return RegisterRead(
                offset=off,
                name=reg_def.name if reg_def else f"0x{off:02X}",
                raw_bytes=data,
                raw_value=int.from_bytes(data, "little"),
                decoded=decode_register_value(
                    off, data, int.from_bytes(data, "little")),
            )

        fake_self = MagicMock()
        fake_self.read_register.side_effect = read_register
        fake_self.REG_FAIL_RETRY_DELAY = 0.0
        fake_self.bus_stats = MagicMock()
        return fake_self

    def test_merged_repair_decodes_rdo(self):
        """Regression for the decode gap: a merged truncation repair of a
        0x36 read must carry the decode_rdo string, not be empty."""
        fake_self = self._fake_analyzer(
            [b"\x04\xff\xff\xff",     # truncated: 0xFF tail
             b"\x04\x00\x00\x00"],    # clean retry: no contract RDO
            0x36)
        merged = CD3217Analyzer._read_register_merged(
            fake_self, 0x3C, 0x36, 4, attempts=2)
        self.assertEqual(merged.raw_bytes, b"\x04\x00\x00\x00")
        self.assertIn("no contract", merged.decoded)

    def test_merged_repair_decodes_vid(self):
        fake_self = self._fake_analyzer(
            [b"\xff\x28\x00\x00",     # truncated
             b"\x04\x28\x00\x00"],    # Apple VID
            0x00)
        merged = CD3217Analyzer._read_register_merged(
            fake_self, 0x38, 0x00, 4, attempts=2)
        self.assertEqual(merged.raw_bytes, b"\x04\x28\x00\x00")
        self.assertTrue(merged.decoded)


if __name__ == "__main__":
    unittest.main()
