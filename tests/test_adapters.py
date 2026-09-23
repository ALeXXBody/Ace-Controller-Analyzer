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


class TestDetectAdapter(unittest.TestCase):
    def test_ftdi_scan_failure_closes_adapter(self):
        """A half-opened FTDI (open ok, scan fails) must be closed, not
        leaked holding the USB port."""
        from cd3217_analyzer import adapters as A
        closed = []
        with unittest.mock.patch.dict(
                "sys.modules", {"pyftdi": unittest.mock.MagicMock(),
                                "pyftdi.i2c": unittest.mock.MagicMock()}):
            with unittest.mock.patch.object(A.FTDIAdapter, "open",
                                            autospec=True), \
                unittest.mock.patch.object(
                    A.FTDIAdapter, "close",
                    autospec=True,
                    side_effect=lambda self: closed.append(True)), \
                unittest.mock.patch.object(A.FTDIAdapter, "scan",
                                           side_effect=OSError("no bus")), \
                unittest.mock.patch("glob.glob", return_value=[]):
                self.assertIsNone(A.detect_adapter())
        self.assertEqual(closed, [True])

    def test_smbus_path_used_when_no_ftdi(self):
        from cd3217_analyzer import adapters as A

        class FakeSMBus(SMBusAdapter):
            def __init__(self, bus_number=1):
                self.bus_number = bus_number
                self.opened = False

            def open(self):
                self.opened = True

        with unittest.mock.patch.object(A, "SMBusAdapter", FakeSMBus), \
            unittest.mock.patch("glob.glob",
                                return_value=["/dev/i2c-1"]):
            ad = A.detect_adapter()
            self.assertIsInstance(ad, FakeSMBus)
            self.assertTrue(ad.opened)
