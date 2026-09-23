"""uart_autobaud deadline extension (audit fix F, §4.19).

The firmware blocks window_ms measuring the pulse width, then replies.
The default _transact deadline (~timeout*(retries+1)+0.5 ≈ 1.5 s) expired
mid-measurement: any window > ~1 s returned None forever. _transact now
takes a min_deadline_s floor that uart_autobaud raises to window +
response time + margin.
"""

import time
import unittest
from unittest.mock import MagicMock

from cd3217_analyzer.usb_bridge import UsbBridgeAdapter


class _SilentSerial:
    """A serial port that never returns data (board quiet)."""

    def reset_input_buffer(self):
        pass

    def write(self, data):
        return len(data)

    def read(self, n):
        return b""

    def in_waiting(self):
        return 0


def _mk_adapter(timeout_s: float = 0.2) -> UsbBridgeAdapter:
    a = UsbBridgeAdapter(port="COMTEST", timeout=timeout_s)
    a._ser = _SilentSerial()
    a._closing = False
    return a


class TestAutobaudDeadline(unittest.TestCase):
    def test_min_deadline_extends_the_wait(self):
        """With a silent board and min_deadline_s=1.2 (timeout 0.2), the
        transaction must wait ~1.2 s instead of ~0.7 s (0.2*1+0.5)."""
        a = _mk_adapter(timeout_s=0.2)
        t0 = time.monotonic()
        with self.assertRaises(IOError):
            a._transact(0x21, b"\x01", retries=0, min_deadline_s=1.2)
        wall = time.monotonic() - t0
        self.assertGreaterEqual(wall, 1.1)
        self.assertLess(wall, 2.3)

    def test_default_deadline_unchanged(self):
        """No override -> old deadline math (timeout*(retries+1)+0.5)."""
        a = _mk_adapter(timeout_s=0.2)
        t = time.monotonic()
        with self.assertRaises(IOError):
            a._transact(0x04, b"", retries=0)
        wall = time.monotonic() - t
        self.assertLess(wall, 1.1)

    def test_autobaud_passes_window_to_backend(self):
        """uart_autobaud wires window_ms into min_deadline via _transact."""
        a = _mk_adapter()
        seen = {}

        def spy(cmd, payload, retries=2, min_deadline_s=None):
            seen["retries"] = retries
            seen["min_deadline_s"] = min_deadline_s
            return None

        a._transact = spy
        self.assertIsNone(a.uart_autobaud(window_ms=15000, pin=5))
        self.assertEqual(seen["retries"], 0)
        # window 15 s + response time + margin
        self.assertGreaterEqual(seen["min_deadline_s"], 15.0 + 0.2)
        self.assertLessEqual(seen["min_deadline_s"], 18.0)


if __name__ == "__main__":
    unittest.main()
