"""Tests for the comprehensive debug trace (v0.7.6)."""

import os
import tempfile
import unittest
from unittest.mock import MagicMock

from cd3217_analyzer import debuglog
from cd3217_analyzer.usb_bridge import (
    UsbBridgeAdapter, CMD_READ, CMD_I2C_FREQ,
)
from tests.test_usb_bridge import FakeSerial, make_response


class TestDebugLog(unittest.TestCase):
    def tearDown(self):
        debuglog.disable()
        debuglog.clear()

    def test_disabled_is_noop(self):
        debuglog.clear()
        debuglog.log("should not appear")
        self.assertEqual(debuglog.entries(), [])

    def test_enable_log_disable(self):
        debuglog.enable()
        debuglog.log("hello %s", "world")
        debuglog.disable()
        entries = debuglog.entries()
        self.assertTrue(any("hello world" in e for e in entries))
        self.assertTrue(any("ENABLED" in e for e in entries))
        self.assertTrue(any("disabled" in e for e in entries))
        self.assertFalse(debuglog.is_enabled())

    def test_entries_have_thread_and_timestamp(self):
        debuglog.enable()
        debuglog.log("tick")
        e = debuglog.entries()[-1]
        self.assertIn("[MainThread]", e)
        self.assertIn(":", e)  # timestamp contains clock separator

    def test_file_sink(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "trace.log")
            used = debuglog.enable(path)
            self.assertEqual(used, path)
            debuglog.log("file line %d", 1)
            debuglog.disable()
            with open(path) as f:
                content = f.read()
        self.assertIn("file line 1", content)
        self.assertIn("ENABLED", content)

    def test_bad_file_path_falls_back_to_ring(self):
        # a path THROUGH an existing file can never be opened
        with tempfile.TemporaryDirectory() as td:
            blocker = os.path.join(td, "blocker")
            open(blocker, "w").close()
            used = debuglog.enable(os.path.join(blocker, "sub", "t.log"))
        self.assertIsNone(used)
        debuglog.log("still collected")
        self.assertTrue(any("still collected" in e
                            for e in debuglog.entries()))

    def test_bridge_transactions_are_traced(self):
        adapter = UsbBridgeAdapter(port="COM9")
        adapter._ser = FakeSerial([
            make_response(CMD_READ, b"\x00\x51"),
        ])
        debuglog.enable()
        adapter.read_bytes(0x38, 0x00, 1)
        entries = debuglog.entries()
        self.assertTrue(any("TX READ" in e for e in entries))
        self.assertTrue(any("RX READ ok" in e and "status=0x00" in e
                            for e in entries))
        self.assertTrue(any("I2C" not in e or "NACK" not in e
                            for e in entries))

    def test_i2c_nack_is_traced(self):
        adapter = UsbBridgeAdapter(port="COM9")
        adapter._ser = FakeSerial([
            make_response(CMD_READ, b"\xff"),
        ])
        debuglog.enable()
        with self.assertRaises(OSError):
            adapter.read_bytes(0x3C, 0x00, 4)
        self.assertTrue(any("I2C READ NACK 0x3C" in e
                            for e in debuglog.entries()))

    def test_bridge_timeout_is_traced(self):
        adapter = UsbBridgeAdapter(port="COM9")
        adapter._ser = FakeSerial([])
        debuglog.enable()
        with self.assertRaises(IOError):
            adapter.bus_check()
        self.assertTrue(any("FAILED" in e and "BUSCHK" in e
                            for e in debuglog.entries()))


if __name__ == "__main__":
    unittest.main()
