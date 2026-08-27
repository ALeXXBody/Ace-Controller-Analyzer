"""
USB bridge adapter.

Drives a CD3217-Analyzer ESP32 / RP2040 board over its native USB-CDC serial
port as an I2C adapter. The board runs a tiny framed binary protocol
(see firmware_esp32/src/bridge.h) that proxies I2C reads/writes to the SDA/SCL
pins, so this adapter implements the same I2CAdapter interface as FTDI/CH341.

Wire the board's SDA/SCL to the CD3217 (pull-ups to 3.3V required), then pick
this adapter in the GUI instead of FTDI.

Requires: pyserial
Install:  pip install pyserial
"""

import time
from typing import List, Optional

from .adapters import I2CAdapter

try:
    import serial
except ImportError:  # pragma: no cover
    serial = None

# ---- protocol constants (match firmware bridge.h) ---------------------------
MAGIC = 0xA5
CMD_SCAN = 0x01
CMD_READ = 0x02
CMD_WRITE = 0x03
CMD_PING = 0x04
CMD_INFO = 0x05
RESP_OK = 0x00


class UsbBridgeAdapter(I2CAdapter):
    """I2C adapter that proxies over a board's USB-CDC serial bridge."""

    def __init__(self, port: str = "COM3", baud: int = 115200, timeout: float = 1.0):
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self._ser = None

    # ---- connection ---------------------------------------------------------
    def open(self) -> None:
        if serial is None:
            raise RuntimeError("pyserial is not installed: pip install pyserial")
        self._ser = serial.Serial(self.port, self.baud, timeout=self.timeout,
                                  write_timeout=self.timeout)
        self._ser.reset_input_buffer()
        # Drain any boot banner lines before sending frames.
        time.sleep(0.3)
        self._ser.reset_input_buffer()

    def close(self) -> None:
        if self._ser:
            try:
                self._ser.close()
            finally:
                self._ser = None

    def _require_open(self):
        if self._ser is None:
            raise RuntimeError("Bridge not open: call open() first")

    # ---- framing -----------------------------------------------------------
    def _frame(self, cmd: int, payload: bytes = b"") -> bytes:
        ck = cmd ^ (len(payload) & 0xFF)
        for b in payload:
            ck ^= b
        return bytes([MAGIC, cmd, len(payload) & 0xFF]) + payload + bytes([ck])

    def _transact(self, cmd: int, payload: bytes = b"", retries: int = 2) -> bytes:
        """Send a frame and read the matching response payload (without status)."""
        self._require_open()
        frame = self._frame(cmd, payload)
        for attempt in range(retries + 1):
            self._ser.reset_input_buffer()
            self._ser.write(frame)
            # response: [MAGIC][cmd][plen][payload...][cksum]
            # read until we have a full valid frame (or timeout).
            data = bytearray()
            timeout_end = time.time() + self.timeout
            while time.time() < timeout_end:
                if self._ser.in_waiting:
                    data += self._ser.read(self._ser.in_waiting)
                    if len(data) >= 3:
                        plen = data[2]
                        total = 3 + plen + 1
                        if len(data) >= total:
                            body = bytes(data[1:total])
                            if body[0] == cmd:
                                resp = bytes(data[3:3 + plen])
                                if _verify_ck(body, plen):
                                    return resp
                            # wrong cmd or bad cksum -> resync by dropping a byte
                            data.pop(0)
            if attempt < retries:
                continue
            raise IOError(f"Bridge no response for cmd 0x{cmd:02X}")
        raise IOError(f"Bridge no response for cmd 0x{cmd:02X}")

    # ---- I2CAdapter interface ----------------------------------------------
    def scan(self, start: int = 0x08, end: int = 0x77) -> List[int]:
        resp = self._transact(CMD_SCAN)
        if not resp:
            return []
        n = resp[0]
        found = []
        for i in range(min(n, len(resp) - 1)):
            found.append(resp[i + 1])
        return found

    def read_byte(self, address: int, register: int) -> int:
        payload = bytes([address, register, 1])
        resp = self._transact(CMD_READ, payload)
        if not resp or resp[0] != RESP_OK:
            raise OSError(f"Read failed at 0x{address:02X} reg 0x{register:02X}")
        return resp[1]

    def read_bytes(self, address: int, register: int, length: int) -> bytes:
        length = min(length, 64)
        payload = bytes([address, register, length])
        resp = self._transact(CMD_READ, payload)
        if not resp or resp[0] != RESP_OK:
            raise OSError(f"Read failed at 0x{address:02X} reg 0x{register:02X}")
        return resp[1:]

    def write_byte(self, address: int, register: int, value: int) -> bool:
        return self.write_bytes(address, register, bytes([value]))

    def write_bytes(self, address: int, register: int, data: bytes) -> bool:
        data = bytes(data)
        payload = bytes([address, register, len(data)]) + data
        resp = self._transact(CMD_WRITE, payload)
        return bool(resp) and resp[0] == RESP_OK

    def ping(self, address: int) -> bool:
        try:
            self.read_byte(address, 0x00)
            return True
        except Exception:
            return False

    # ---- extra (not in ABC, used by GUI) -----------------------------------
    def info(self) -> dict:
        resp = self._transact(CMD_INFO)
        if not resp:
            return {}
        blen = resp[0]
        board = bytes(resp[1:1 + blen]).decode("utf-8", "replace")
        sda = resp[1 + blen] if len(resp) > 1 + blen else None
        scl = resp[2 + blen] if len(resp) > 2 + blen else None
        return {"board": board, "sda": sda, "scl": scl}

    def handshake(self) -> bool:
        """Send PING and confirm the device responds (banner already drained)."""
        try:
            resp = self._transact(CMD_PING)
            return bool(resp) and resp[0] == 0x51
        except Exception:
            return False


def _verify_ck(body: bytes, plen: int):
    """body = [cmd][plen][payload...][cksum]"""
    ck = body[0] ^ body[1]
    for i in range(plen):
        ck ^= body[2 + i]
    return ck == body[-1]


def list_bridge_ports() -> List[str]:
    """Return candidate serial port names (debug helper)."""
    if serial is None:
        return []
    ports = []
    try:
        import serial.tools.list_ports as lp
        for p in lp.comports():
            ports.append(p.device)
    except Exception:
        pass
    return ports
