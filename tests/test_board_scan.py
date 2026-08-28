"""Tests for board auto-detection (scan_for_boards / port_exists)."""

import unittest
from unittest.mock import patch

import cd3217_analyzer.usb_bridge as ub


class FakePort:
    def __init__(self, device, vid=None, pid=None, desc="", hwid="",
                 serial_number=""):
        self.device = device
        self.vid = vid
        self.pid = pid
        self.description = desc
        self.hwid = hwid
        self.serial_number = serial_number


class FakeAdapter:
    """Stands in for UsbBridgeAdapter during scans."""
    instances = []

    def __init__(self, port="COMx", timeout=1.0):
        self.port = port
        self.opened = False
        self._info = FakeAdapter.responses.get(port, {})
        FakeAdapter.instances.append(self)

    def open(self):
        self.opened = True

    def close(self):
        self.opened = False

    def handshake(self):
        return bool(self._info)

    def info(self):
        return self._info


class TestScanForBoards(unittest.TestCase):
    def setUp(self):
        FakeAdapter.instances = []
        FakeAdapter.responses = {}

    def scan(self, ports, current=None):
        with patch.object(ub, "_comports", return_value=ports), \
             patch.object(ub, "UsbBridgeAdapter", FakeAdapter):
            return ub.scan_for_boards(current_port=current)

    def test_finds_board_and_reports_info(self):
        FakeAdapter.responses = {
            "COM8": {"board": "pico1", "sda": 4, "scl": 5},
            "COM9": {},  # some other serial device — no PING answer
        }
        ports = [FakePort("COM9", vid=0x10C4, pid=0xEA60, desc="CP210x"),
                 FakePort("COM8", vid=0x2E8A, pid=0x000A, desc="Pico")]
        found = self.scan(ports)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["port"], "COM8")
        self.assertEqual(found[0]["board"], "pico1")

    def test_known_vid_pid_boards_rank_first(self):
        FakeAdapter.responses = {
            "COM3": {"board": "esp32-c3"},
            "COM7": {"board": "pico1"},
        }
        ports = [FakePort("COM3", vid=0x1234, pid=0x5678),   # unknown device
                 FakePort("COM7", vid=0x2E8A, pid=0x000A)]   # known board
        found = self.scan(ports)
        self.assertEqual(found[0]["port"], "COM7")   # priority despite number

    def test_current_port_is_skipped(self):
        FakeAdapter.responses = {
            "COM8": {"board": "pico1"},
            "COM9": {"board": "pico-w"},
        }
        ports = [FakePort("COM8", vid=0x2E8A, pid=0x000A),
                 FakePort("COM9", vid=0x2E8A, pid=0x000C)]
        found = self.scan(ports, current="COM8")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["port"], "COM9")

    def test_duplicate_usb_device_deduped(self):
        FakeAdapter.responses = {"COM8": {"board": "pico1"}}
        ports = [FakePort("COM8", vid=0x2E8A, pid=0x000A, serial_number="A1"),
                 FakePort("COM8", vid=0x2E8A, pid=0x000A, serial_number="A1")]
        found = self.scan(ports)
        self.assertEqual(len(found), 1)

    def test_no_ports(self):
        self.assertEqual(self.scan([]), [])

    def test_open_failure_is_skipped(self):
        class Boom(FakeAdapter):
            def open(self):
                raise OSError("denied")
        with patch.object(ub, "_comports",
                          return_value=[FakePort("COM8", vid=0x2E8A,
                                                 pid=0x000A)]), \
             patch.object(ub, "UsbBridgeAdapter", Boom):
            self.assertEqual(ub.scan_for_boards(), [])


class TestPortExists(unittest.TestCase):
    def test_present(self):
        with patch.object(ub, "_comports",
                          return_value=[FakePort("COM8")]):
            self.assertTrue(ub.port_exists("COM8"))
            self.assertTrue(ub.port_exists("com8"))   # normalized
            self.assertTrue(ub.port_exists("8"))      # -> COM8

    def test_absent(self):
        with patch.object(ub, "_comports",
                          return_value=[FakePort("COM9")]):
            self.assertFalse(ub.port_exists("COM8"))
            self.assertFalse(ub.port_exists(""))


class TestIsAlive(unittest.TestCase):
    def test_alive(self):
        from tests.test_usb_bridge import FakeSerial, make_response
        fake = FakeSerial([make_response(ub.CMD_PING, b"\x51")])
        a = ub.UsbBridgeAdapter(port="COM9")
        a._ser = fake
        self.assertTrue(a.is_alive())

    def test_dead(self):
        from tests.test_usb_bridge import FakeSerial
        fake = FakeSerial([])          # no response ever queued
        a = ub.UsbBridgeAdapter(port="COM9")
        a._ser = fake
        self.assertFalse(a.is_alive())


if __name__ == "__main__":
    unittest.main()
