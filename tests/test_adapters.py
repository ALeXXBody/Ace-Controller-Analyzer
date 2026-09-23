"""Adapters: SMBus read failure raises, not 0xFF fabrication (audit fix A).

SMBusAdapter.read_bytes swallowed per-byte NACKs and appended 0xFF —
indistinguishable from the chip's real truncation fill. read_bytes now
uses read_i2c_block_data and raises like every other adapter.
"""

import unittest
from unittest.mock import MagicMock

from cd3217_analyzer.adapters import SMBusAdapter


class TestSMBusReadFailure(unittest.TestCase):
    def _mk(self, block=None, exc=None):
        a = SMBusAdapter(bus_number=1)
        a._bus = MagicMock()
        if exc is not None:
            a._bus.read_i2c_block_data.side_effect = exc
        else:
            a._bus.read_i2c_block_data.return_value = [0x04, 0x28, 0x00, 0x00]
        return a

    def test_failure_raises_not_ff_fabricated(self):
        a = self._mk(exc=IOError("nack"))
        with self.assertRaises(IOError):
            a.read_bytes(0x3B, 0x00, 4)

    def test_success_roundtrip(self):
        a = self._mk()
        self.assertEqual(a.read_bytes(0x38, 0x00, 4), b"\x04\x28\x00\x00")

    def test_block_data_called_with_expected_args(self):
        a = self._mk()
        a.read_bytes(0x3B, 0x00, 4)
        a._bus.read_i2c_block_data.assert_called_once_with(0x3B, 0x00, 4)


if __name__ == "__main__":
    unittest.main()
