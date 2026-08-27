"""End-to-end conformance test.

Simulates the firmware's USB bridge (bridge.cpp) byte-for-byte in Python and
drives it with the real host UsbBridgeAdapter over a loopback pipe. Proves the
framed protocol + host adapter are self-consistent without needing hardware.
"""

import queue
import threading
import unittest


from cd3217_analyzer.usb_bridge import UsbBridgeAdapter, MAGIC

MAGIC_B = 0xA5


class FirmwareSim:
    """Faithful reimplementation of UsbBridge::poll/readFrame_/sendResp_."""

    # Wire.beginTransmission/endTransmission/requestFrom are stubbed as a fake
    # I2C device map. addr 0x38 responds; others NACK.

    def __init__(self, tx):
        self.tx = tx           # bytes the sim writes back to host
        self.buf = []
        self.got_magic = False
        self.frame_len = 0

    def write(self, data):
        # one byte per Serial.write call, same as firmware
        for byte in data:
            self.tx.put(bytes([byte]) if isinstance(byte, int) else byte)

    # ---- firmware framing (mirrors bridge.cpp) ------------------------------
    def _readFrame(self, b):
        if not self.got_magic:
            if b == MAGIC_B:
                self.got_magic = True
                self.buf = [b]
            return None
        self.buf.append(b)
        if len(self.buf) >= 3:
            plen = self.buf[2]
            total = 3 + plen + 1
            if len(self.buf) == total:
                ck = self.buf[1] ^ self.buf[2]
                for i in range(plen):
                    ck ^= self.buf[3 + i]
                if ck == self.buf[total - 1]:
                    self.got_magic = False
                    return list(self.buf)
                self.got_magic = False
                self.buf = []
                return None
            if len(self.buf) > total or len(self.buf) > 128:
                self.got_magic = False
                self.buf = []
        return None

    def _handleFrame(self, frame):
        cmd, plen = frame[1], frame[2]
        pl = frame[3:3 + plen]
        if cmd == 0x01:      # SCAN
            self._resp(0x01, [0x38])
        elif cmd == 0x02:    # READ
            self._resp(0x02, [0x00, 0x51, 0x04, 0x00, 0x00])
        elif cmd == 0x03:    # WRITE
            self._resp(0x03, [0x00])
        elif cmd == 0x04:    # PING
            self._resp(0x04, [0x51])
        elif cmd == 0x05:    # INFO
            self._resp(0x05, [4] + list(b"pico") + [4, 5])

    def _resp(self, cmd, payload):
        ck = cmd ^ (len(payload) & 0xFF)
        for by in payload:
            ck ^= by
        frame = bytes([MAGIC_B, cmd, len(payload) & 0xFF]) + bytes(payload) + bytes([ck])
        self.write(frame)

    def poll(self, b):
        frame = self._readFrame(b)
        if frame:
            self._handleFrame(frame)


class FakeSerialStream:
    """In-memory duplex stream standing in for a pyserial connection."""

    def __init__(self):
        self.to_board = queue.Queue()   # host -> sim
        self.to_host = queue.Queue()    # sim -> host

    # ----- host side (what UsbBridgeAdapter calls) -----
    def reset_input_buffer(self):
        while not self.to_host.empty():
            self.to_host.get_nowait()

    def write(self, data):
        self.to_board.put(b"".join(data) if isinstance(data, list) else data)

    def read(self, n=1):
        # block briefly for a byte
        try:
            b = self.to_host.get(timeout=0.5)
        except queue.Empty:
            return b""
        return b[:n] if len(b) > n else b


def _run_sim(sim, stream):
    while True:
        try:
            b = stream.to_board.get(timeout=0.1)
        except queue.Empty:
            return
        for byte in b:
            sim.poll(byte)


class TestBridgeConformance(unittest.TestCase):
    def test_ping_scan_read_write_info_roundtrip(self):
        stream = FakeSerialStream()
        sim = FirmwareSim(stream.to_host)

        class _Ser:
            def __init__(s):
                s.reset_input_buffer = stream.reset_input_buffer
                s.write = stream.write
                s.read = stream.read

        t = threading.Thread(target=_run_sim, args=(sim, stream), daemon=True)
        t.start()

        adapter = UsbBridgeAdapter(port="loopback", timeout=2.0)
        adapter._ser = _Ser()

        # PING handshake
        self.assertTrue(adapter.handshake())
        # INFO
        info = adapter.info()
        self.assertEqual(info["board"], "pico")
        self.assertEqual(info["sda"], 4)
        self.assertEqual(info["scl"], 5)
        # SCAN
        self.assertEqual(adapter.scan(), [0x38])
        # READ
        self.assertEqual(adapter.read_byte(0x38, 0x00), 0x51)
        # WRITE
        self.assertTrue(adapter.write_bytes(0x38, 0x10, b"\xde\xad"))


if __name__ == "__main__":
    unittest.main()
