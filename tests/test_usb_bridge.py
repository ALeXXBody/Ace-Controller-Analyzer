"""Tests for the USB bridge adapter (protocol framing + I2CAdapter mapping)."""

import unittest
from unittest.mock import MagicMock

from cd3217_analyzer.usb_bridge import (
    UsbBridgeAdapter,
    _verify_ck,
    normalize_port,
    CMD_SCAN,
    CMD_READ,
    CMD_WRITE,
    CMD_PING,
    CMD_INFO,
    MAGIC,
    RESP_OK,
)


def make_response(cmd, payload):
    """Build a response frame byte string: [MAGIC][cmd][plen][pay...][ck]."""
    ck = cmd ^ (len(payload) & 0xFF)
    for b in payload:
        ck ^= b
    return bytes([MAGIC, cmd, len(payload) & 0xFF]) + bytes(payload) + bytes([ck])


class FakeSerial:
    """Scripted pyserial stand-in that returns responses in order."""

    def __init__(self, responses):
        self._queue = list(responses)
        self.write_calls = []
        self.in_waiting = 0

    def reset_input_buffer(self):
        # expose next response
        self._capture()
        return None

    def _capture(self):
        cur = [b for b in self._queue if b is not None]
        self._current = cur[0] if cur else b""
        self.in_waiting = len(self._current)

    def write(self, frame):
        self.write_calls.append(bytes(frame))

    def read(self, n):
        n = int(n)
        out = self._current[:n]
        self._current = self._current[n:]
        self.in_waiting = len(self._current)
        return out

    def close(self):
        pass


class TestUsbBridgeAdapter(unittest.TestCase):
    def make_adapter(self, responses):
        fake = FakeSerial(responses)
        adapter = UsbBridgeAdapter(port="COM9")
        adapter._ser = fake
        return adapter, fake

    def test_verify_ck(self):
        # body = [cmd][plen][payload...][cksum]
        payload = b"\x51"
        body = bytes([CMD_PING, 1]) + payload + bytes([CMD_PING ^ 1 ^ payload[0]])
        self.assertTrue(_verify_ck(body, 1))
        # corrupted cksum should fail
        bad = body[:-1] + bytes([body[-1] ^ 0xFF])
        self.assertFalse(_verify_ck(bad, 1))

    def test_frame_ping(self):
        adapter, fake = self.make_adapter([make_response(CMD_PING, b"\x51")])
        self.assertTrue(adapter.handshake())
        # frame sent = [MAGIC][CMD_PING][0][ck]
        frame = fake.write_calls[0]
        self.assertEqual(frame[0], MAGIC)
        self.assertEqual(frame[1], CMD_PING)

    def test_resync_past_boot_banner(self):
        # Board boots, prints a text banner, THEN responds to PING. The host
        # must scan past the stray ASCII bytes and still find the frame.
        banner = b"[boot] CD3217-Analyzer M1 spike, board=pico\n"
        resp = banner + make_response(CMD_PING, b"\x51")
        adapter, fake = self.make_adapter([resp])
        self.assertTrue(adapter.handshake())

    def test_ping_wrong_cmd_ignored(self):
        # A frame for a different cmd must not satisfy the PING.
        resp = make_response(CMD_INFO, b"\x04pico\x04\x05") + make_response(CMD_PING, b"\x51")
        adapter, fake = self.make_adapter([resp])
        self.assertTrue(adapter.handshake())

    def test_scan(self):
        adapter, fake = self.make_adapter([make_response(CMD_SCAN, bytes([0x38, 0x3F]))])
        found = adapter.scan()
        self.assertEqual(found, [0x38, 0x3F])

    def test_read_bytes(self):
        adapter, fake = self.make_adapter([make_response(CMD_READ, bytes([RESP_OK, 1, 2, 3, 4]))])
        data = adapter.read_bytes(0x38, 0x00, 4)
        self.assertEqual(data, bytes([1, 2, 3, 4]))

    def test_read_byte(self):
        adapter, fake = self.make_adapter([make_response(CMD_READ, bytes([RESP_OK, 0xAB]))])
        self.assertEqual(adapter.read_byte(0x2F, 0x00), 0xAB)

    def test_write_bytes(self):
        adapter, fake = self.make_adapter([make_response(CMD_WRITE, bytes([RESP_OK]))])
        self.assertTrue(adapter.write_bytes(0x38, 0x10, b"\xde\xad"))
        frame = fake.write_calls[0]
        self.assertEqual(frame[1], CMD_WRITE)
        # payload starts [addr][reg][dlen]
        self.assertEqual(frame[3], 0x38)
        self.assertEqual(frame[4], 0x10)
        self.assertEqual(frame[5], 2)
        self.assertEqual(frame[6:8], b"\xde\xad")

    def test_write_fail(self):
        adapter, fake = self.make_adapter([make_response(CMD_WRITE, bytes([0xFF]))])
        self.assertFalse(adapter.write_bytes(0x38, 0x10, b"\x00"))

    def test_info(self):
        adapter, fake = self.make_adapter(
            [make_response(CMD_INFO, bytes([4]) + b"pico" + bytes([4, 5]))]
        )
        info = adapter.info()
        self.assertEqual(info["board"], "pico")
        self.assertEqual(info["sda"], 4)
        self.assertEqual(info["scl"], 5)

    def test_bus_check_healthy(self):
        """F4: BUSCHK reports both lines idle HIGH."""
        from cd3217_analyzer.usb_bridge import CMD_BUS_CHECK
        adapter, fake = self.make_adapter(
            [make_response(CMD_BUS_CHECK, bytes([0x00, 0x01, 0x01]))]
        )
        res = adapter.bus_check()
        self.assertEqual(res, {"sda": 1, "scl": 1})

    def test_bus_check_stuck_sda(self):
        """F4: SDA held LOW is reported so the tool can blame the bus."""
        from cd3217_analyzer.usb_bridge import CMD_BUS_CHECK
        adapter, fake = self.make_adapter(
            [make_response(CMD_BUS_CHECK, bytes([0x00, 0x00, 0x01]))]
        )
        res = adapter.bus_check()
        self.assertEqual(res["sda"], 0)
        self.assertEqual(res["scl"], 1)

    def test_bus_check_old_firmware_raises(self):
        """F4: firmware without BUSCHK support yields no matching frame —
        the adapter must surface an actionable error."""
        from cd3217_analyzer.usb_bridge import CMD_BUS_CHECK
        adapter, fake = self.make_adapter([])
        with self.assertRaises(IOError):
            adapter.bus_check()

    def test_set_i2c_clock_ok(self):
        """S1: I2CFREQ payload is the frequency as LE32."""
        import struct
        from cd3217_analyzer.usb_bridge import CMD_I2C_FREQ
        adapter, fake = self.make_adapter(
            [make_response(CMD_I2C_FREQ, b"\x00")]
        )
        adapter.set_i2c_clock(400_000)
        frame = fake.write_calls[-1]
        self.assertEqual(frame[3:7], struct.pack("<I", 400_000))

    def test_set_i2c_clock_old_firmware_raises(self):
        """S1: firmware without I2CFREQ support errors with guidance."""
        adapter, fake = self.make_adapter([])
        with self.assertRaises(IOError):
            adapter.set_i2c_clock(400_000)

    def test_read_error_raises(self):
        adapter, fake = self.make_adapter([make_response(CMD_READ, bytes([0xFF]))])
        with self.assertRaises(OSError):
            adapter.read_bytes(0x38, 0x00, 4)

    def test_open_is_idempotent(self):
        # Opening twice must not create a second serial handle — a second
        # serial.Serial() on a held Windows CDC port wedges usbser.sys and
        # throws "Access is denied" (PermissionError 13).
        from unittest.mock import patch

        constructed = {"n": 0}

        class FakeSerial:
            def __init__(self, *a, **k):
                constructed["n"] += 1

            def reset_input_buffer(self):
                pass

            def write(self, frame):
                pass

            def read(self, n):
                return b""

            def close(self):
                pass

        fake_module = type("serial", (), {"Serial": FakeSerial})()
        with patch("cd3217_analyzer.usb_bridge.serial", fake_module):
            with patch("cd3217_analyzer.usb_bridge.time.sleep"):
                adapter = UsbBridgeAdapter(port="COM9")
                adapter.open()
                adapter.open()
        self.assertEqual(constructed["n"], 1)
        self.assertTrue(adapter.is_open)
        adapter.close()
        self.assertFalse(adapter.is_open)

    def test_concurrent_transact_is_serialized(self):
        """Regression: the removal watcher calls is_alive() every 2 s on the
        same CDC port while Diagnose All runs it from a background thread.
        Without a lock these interleave and corrupt frames (is_alive() reports
        false -> watcher wrongly disconnects). Every concurrent call here must
        return a correct, non-corrupted response."""
        import threading

        sentinel = make_response(CMD_PING, b"\x51")

        class ThreadSafeSerial:
            def __init__(self):
                self._guard = threading.Lock()
                self._current = sentinel

            def reset_input_buffer(self):
                with self._guard:
                    self._current = sentinel
                return None

            def write(self, frame):
                pass

            def read(self, n):
                with self._guard:
                    out = self._current[:n]
                    self._current = self._current[n:]
                return out

            def close(self):
                pass

        adapter = UsbBridgeAdapter(port="COM9")
        adapter._ser = ThreadSafeSerial()

        results = []
        errors = []

        def hammer():
            for _ in range(50):
                try:
                    results.append(adapter.is_alive())
                except Exception as e:  # noqa: BLE001
                    errors.append(repr(e))

        threads = [threading.Thread(target=hammer) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(results), 8 * 50)
        self.assertTrue(all(results), "every PING must return alive=True")


class TestNormalizePort(unittest.TestCase):
    def test_bare_number(self):
        self.assertEqual(normalize_port("8"), "COM8")
        self.assertEqual(normalize_port("26"), "COM26")

    def test_com_variants(self):
        self.assertEqual(normalize_port("COM8"), "COM8")
        self.assertEqual(normalize_port("com8"), "COM8")

    def test_real_paths_pass_through(self):
        self.assertEqual(normalize_port("/dev/ttyACM0"), "/dev/ttyACM0")

    def test_empty_and_spaces(self):
        self.assertEqual(normalize_port(""), "")
        self.assertEqual(normalize_port("   "), "")
        self.assertEqual(normalize_port("COM"), "COM")


if __name__ == "__main__":
    unittest.main()
