"""Tests for UART RX sniffing over the USB bridge (adapter methods)."""

import unittest

from cd3217_analyzer.usb_bridge import (
    CMD_UART_AUTOBAUD,
    CMD_UART_READ,
    CMD_UART_SETUP,
    UsbBridgeAdapter,
    snap_baud,
)
from tests.test_usb_bridge import make_response

from tests.test_spi_bridge import SerialScript


def setup_resp(ok=True):
    return make_response(CMD_UART_SETUP, b"\x00" if ok else b"\xFF")


class TestSnapBaud(unittest.TestCase):
    def test_snaps_within_tolerance(self):
        self.assertEqual(snap_baud(115200), 115200)
        self.assertEqual(snap_baud(115000), 115200)     # -0.2%
        self.assertEqual(snap_baud(117000), 115200)     # +1.6%
        self.assertEqual(snap_baud(921600), 921600)

    def test_outside_tolerance_rounds(self):
        self.assertEqual(snap_baud(150000), 150000)     # no standard nearby
        self.assertEqual(snap_baud(200000), 200000)
        self.assertEqual(snap_baud(250), 300)           # floor at 300

    def test_exact_boundaries(self):
        # exactly 8% off 115200 -> 124416, within tolerance
        self.assertEqual(snap_baud(124416), 115200)


class TestUartMethods(unittest.TestCase):
    def make(self, responses):
        fake = SerialScript(responses)
        a = UsbBridgeAdapter(port="COM9")
        a._ser = fake
        return a, fake

    def test_uart_setup_frame(self):
        a, fake = self.make([setup_resp()])
        a.uart_setup(115200, pin=4)
        frame = fake.write_calls[0]
        # payload: baud LE32 + pin
        self.assertEqual(frame[3:8], bytes([0x00, 0xC2, 0x01, 0x00, 4]))
        # stop frame: baud 0, default pin 0xFF
        a2, fake2 = self.make([setup_resp()])
        a2.uart_setup(0)
        self.assertEqual(fake2.write_calls[0][3:8],
                         bytes([0, 0, 0, 0, 0xFF]))

    def test_uart_setup_error_raises(self):
        a, _ = self.make([setup_resp(ok=False)])
        with self.assertRaises(IOError):
            a.uart_setup(115200)

    def test_uart_read(self):
        data = bytes(range(8))
        a, _ = self.make([make_response(CMD_UART_READ,
                                        bytes([len(data)]) + data)])
        self.assertEqual(a.uart_read(), data)

    def test_uart_read_empty(self):
        a, _ = self.make([make_response(CMD_UART_READ, b"\x00")])
        self.assertEqual(a.uart_read(), b"")

    def test_uart_autobaud(self):
        # 9600 baud -> bit time 104.17us -> pulseIn sees 104
        resp = make_response(CMD_UART_AUTOBAUD,
                             b"\x00" + (104).to_bytes(4, "little"))
        a, _ = self.make([resp])
        self.assertEqual(a.uart_autobaud(), 9600)

    def test_uart_autobaud_high_baud_truncation(self):
        # 115200 baud -> bit time 8.68us -> pulseIn truncates to 8;
        # midpoint correction (8.5us -> 117647) must still snap to 115200
        resp = make_response(CMD_UART_AUTOBAUD,
                             b"\x00" + (8).to_bytes(4, "little"))
        a, _ = self.make([resp])
        self.assertEqual(a.uart_autobaud(), 115200)

    def test_uart_autobaud_silent_returns_none(self):
        resp = make_response(CMD_UART_AUTOBAUD,
                             b"\x00" + (0).to_bytes(4, "little"))
        a, _ = self.make([resp])
        self.assertIsNone(a.uart_autobaud())

    def test_uart_autobaud_error_returns_none(self):
        resp = make_response(CMD_UART_AUTOBAUD, b"\xFF")
        a, _ = self.make([resp])
        self.assertIsNone(a.uart_autobaud())


class TestInfoUartField(unittest.TestCase):
    def test_info_parses_uart_rx(self):
        board = b"pico1"
        payload = (bytes([len(board)]) + board +
                   bytes([4, 5, 14, 12, 15, 13, 0x01, 1]))
        a, _ = self.make([make_response(0x05, payload)])
        info = a.info()
        self.assertEqual(info["uart_rx"], 1)
        self.assertEqual(info["hw"], 0x01)

    def make(self, responses):
        fake = SerialScript(responses)
        a = UsbBridgeAdapter(port="COM9")
        a._ser = fake
        return a, fake

    def test_info_old_firmware_no_uart(self):
        board = b"pico1"
        payload = bytes([len(board)]) + board + bytes([4, 5])
        a, _ = self.make([make_response(0x05, payload)])
        info = a.info()
        self.assertIsNone(info["uart_rx"])


if __name__ == "__main__":
    unittest.main()
