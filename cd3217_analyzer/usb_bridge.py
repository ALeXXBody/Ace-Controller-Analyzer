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

import sys
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
        if self._ser is not None:
            # Already open — never open a held CDC port twice. A second open on
            # Windows' usbser.sys wedges the driver / denies access.
            return
        self._ser = serial.Serial(self.port, self.baud, timeout=self.timeout,
                                  write_timeout=self.timeout)
        # Drain once. Opening asserts DTR/RTS which can reset the board; give it
        # a beat to (re)enumerate, then drop any boot banner so it can't pollute
        # frame parsing. We do NOT re-flush before every send.
        time.sleep(0.5)
        if self._ser:
            try:
                self._ser.reset_input_buffer()
            except Exception:
                pass

    def close(self) -> None:
        if self._ser:
            try:
                self._ser.close()
            except Exception:
                pass
            finally:
                self._ser = None

    @property
    def is_open(self) -> bool:
        return self._ser is not None

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
        """Send a frame and read the matching response payload (without status).

        Reads bytes one at a time and rescans forward past any stray bytes
        (e.g. the boot banner) until it finds a complete valid frame whose
        cmd matches. The resync loop always makes progress. We intentionally do
        NOT flush the input buffer before each send, so a response that landed
        between retries is not discarded.
        """
        self._require_open()
        frame = self._frame(cmd, payload)
        deadline_total = time.time() + self.timeout * (retries + 1) + 0.5
        attempt = 0
        while True:
            # Flush stale bytes (e.g. a partial banner) before sending, then
            # read our fresh response. Standard request/response pattern.
            try:
                self._ser.reset_input_buffer()
            except Exception:
                pass
            self._ser.write(frame)
            buf = bytearray()
            timeout_end = min(time.time() + self.timeout, deadline_total)
            while time.time() < timeout_end:
                b = self._ser.read(1)
                if not b:
                    continue
                buf.append(b[0])
                # scan forward for a [MAGIC][cmd][plen][payload][ck] frame
                i = 0
                while len(buf) - i >= 4:
                    if buf[i] != MAGIC:
                        i += 1
                        continue
                    body = bytes(buf[i + 1:])          # [cmd][plen][payload][ck]
                    if len(body) >= 2:
                        plen = body[1]
                        total = 2 + plen + 1
                        if len(body) >= total:
                            cand = body[:total]
                            if cand[0] == cmd and _verify_ck(cand, plen):
                                return bytes(cand[2:2 + plen])
                            i += 1  # not our target — keep scanning
                            continue
                    break  # need more bytes for this candidate
                if i:
                    del buf[:i]
            if attempt >= retries or time.time() >= deadline_total:
                break
            attempt += 1  # retry without flushing; let a late response arrive
        raise IOError(f"Bridge no response for cmd 0x{cmd:02X}")

    # ---- I2CAdapter interface ----------------------------------------------
    def scan(self, start: int = 0x08, end: int = 0x77) -> List[int]:
        # Firmware SCAN response payload is the list of found addresses
        # (the count is transmitted as the frame's plen, already stripped).
        resp = self._transact(CMD_SCAN)
        return list(resp)

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
        """Read the board INFO frame.

        Payload: [boardlen][board][sda][scl][spi_sck][spi_miso][spi_mosi]
                 [spi_cs][hw]  — fields after scl are optional (older
        firmware sends only board/sda/scl; missing fields are None).
        """
        resp = self._transact(CMD_INFO)
        if not resp:
            return {}
        out = {}
        blen = resp[0]
        out["board"] = bytes(resp[1:1 + blen]).decode("utf-8", "replace")
        fields = ("sda", "scl", "spi_sck", "spi_miso", "spi_mosi",
                  "spi_cs", "hw")
        for i, name in enumerate(fields):
            idx = 1 + blen + i
            out[name] = resp[idx] if len(resp) > idx else None
        return out

    def handshake(self) -> bool:
        """Confirm the device responds, retrying until it's ready.

        Opening a Pico's USB-CDC port asserts DTR and can cause a fresh
        enumeration / soft-reset, so the bridge may not be ready to answer for
        a moment. We loop PING attempts over a generous window so a
        slow-but-healthy board still connects instead of failing the first
        blind attempt.
        """
        deadline = time.time() + 5.0
        while time.time() < deadline:
            try:
                resp = self._transact(CMD_PING, retries=1)
                if resp and resp[0] == 0x51:
                    return True
            except Exception:
                pass
            time.sleep(0.3)
        return False

    def is_alive(self) -> bool:
        """One quick PING (no retries) — used by the removal watcher.

        False means "didn't answer right now": either the board is gone or
        it's busy. The watcher only reports removal after the port also
        disappears from enumeration, so a busy-but-present board is fine.
        """
        try:
            resp = self._transact(CMD_PING, retries=0)
            return bool(resp) and resp[0] == 0x51
        except Exception:
            return False


def _verify_ck(body: bytes, plen: int):
    """body = [cmd][plen][payload...][cksum]"""
    ck = body[0] ^ body[1]
    for i in range(plen):
        ck ^= body[2 + i]
    return ck == body[-1]


def normalize_port(port: str) -> str:
    """Return a real serial port name from user input.

    Accepts a bare COM number ("8" or "COM8") and passes real paths through
    unchanged. On non-Windows systems a bare number is interpreted as a
    CD-ROM/board index the same way.
    """
    p = (port or "").strip()
    if not p:
        return p
    # "COM8", "com8" -> "COM8"; bare "8" -> "COM8"
    if p.upper().startswith("COM") and p[3:].isdigit():
        n = str(int(p[3:]))
        return ("COM" + n) if n else ""
    if p.isdigit():
        return "COM" + str(int(p))
    return p


def list_bridge_ports() -> List[str]:
    """Return candidate serial port names (debug helper).

    Uses only pyserial's comports() — never brute-force opens COM1..255. A
    naive per-port open (which by default asserts DTR/RTS) can reset a Pico's
    USB-CDC device or wedge Windows' usbser.sys driver, so we must not do that
    just to discover ports. Ask the user to type the port if none are listed.
    """
    if serial is None:
        return []
    ports = []
    try:
        import serial.tools.list_ports as lp
        for p in lp.comports():
            name = getattr(p, "device", None)
            if name:
                ports.append(name)
    except Exception:
        ports = []
    seen = set()
    return [p for p in ports if not (p in seen or seen.add(p))]


def list_ports_with_desc() -> List[tuple]:
    """Return [(port, description, hwid), ...] sorted by port name.

    The friendly name / VID:PID lets the user tell boards apart (e.g. a Pico 2
    vs an RP2040-Zero are separate COM ports) instead of guessing the number.
    Pure enumeration via comports(); no open-close probing (see
    list_bridge_ports for why that is unsafe on Windows CDC).
    """
    out = []
    try:
        import serial.tools.list_ports as lp
        for p in lp.comports():
            out.append((getattr(p, "device", ""),
                        getattr(p, "description", ""),
                        getattr(p, "hwid", "")))
    except Exception:
        pass
    out.sort(key=lambda x: _port_sort_key(x[0]))
    return out


def _port_sort_key(port: str) -> tuple:
    """Sort COMxx numerically: COM2 before COM10."""
    if port.upper().startswith("COM") and port[3:].isdigit():
        return (0, int(port[3:]))
    return (1, 0)


# USB VID/PID of the boards this firmware runs on (native USB CDC).
_BOARD_USB_IDS = {
    (0x2E8A, 0x000A): "pico",   # Raspberry Pi Pico (RP2040, std USB)
    (0x2E8A, 0x000F): "pico",   # Pico running Picoprobe... (reserved)
    (0x2E8A, 0x000C): "pico_w",
    (0x2E8A, 0x0009): "rp2350",  # Pico 2 (RP2350) / Pico 2 W std CDC
    (0x2E8A, 0x000B): "esp32-s3",  # ESP32-S3 native USB JTAG/serial
    (0x303A, 0x1001): "esp32",  # Espressif ESP32-S2/S3/C3 CDC
    (0x303A, 0x0002): "esp32-c3",
    (0x1A86, 0x55D4): "esp32-c6",  # WCH CH34x bridge on some C6 boards
    (0x10C4, 0xEA60): "esp32-bridge",  # CP210x bridge (ESP32 classic)
    (0x1A86, 0x7523): "esp32-bridge",  # CH340 bridge (ESP32 classic clones)
}


def _comports():
    """pyserial comports() with graceful fallback."""
    try:
        import serial.tools.list_ports as lp
        return list(lp.comports())
    except Exception:
        return []


def scan_for_boards(current_port: Optional[str] = None,
                    timeout: float = 0.8) -> List[dict]:
    """Find CD3217 analyzer boards on the USB serial ports.

    A "board" is a serial port where our bridge firmware answers PING and
    reports its INFO (board name). Candidate ports are prioritized by USB
    VID/PID (known dev-board IDs first), then everything else — so a board
    behind a generic USB-serial bridge is still found.

    ``current_port`` (if given) is skipped — it's the port this app already
    holds open (opening it again would fail with Access denied).

    Returns a list of dicts sorted best-first:
        {"port": "COM8", "board": "pico1", "desc": "Raspberry Pi Pico",
         "hwid": "USB VID:PID=2E8A:000A ..."}
    """
    if serial is None:
        return []
    current = normalize_port(current_port) if current_port else None

    found = []
    seen_usb = set()          # (vid, pid, serial_number) dedup
    entries = []              # (priority, port, desc, hwid, vid, pid)

    for p in _comports():
        name = getattr(p, "device", None)
        if not name:
            continue
        if current and name == current:
            continue
        vid = getattr(p, "vid", None)
        pid = getattr(p, "pid", None)
        sn = getattr(p, "serial_number", None) or ""
        if vid is not None and pid is not None:
            key = (vid, pid, sn)
            if key in seen_usb:
                continue       # same physical device listed twice
            seen_usb.add(key)
            prio = 0 if (vid, pid) in _BOARD_USB_IDS else 1
        else:
            prio = 1
        entries.append((prio, name, getattr(p, "description", "") or "",
                        getattr(p, "hwid", "") or "", vid, pid))

    entries.sort(key=lambda e: (e[0], _port_sort_key(e[1])))

    for _prio, name, desc, hwid, _vid, _pid in entries:
        try:
            a = UsbBridgeAdapter(port=name, timeout=timeout)
            a.open()
            try:
                if not a.handshake():
                    continue
                info = a.info()
            finally:
                a.close()
        except Exception:
            continue
        if info and info.get("board"):
            found.append({"port": name,
                          "board": info["board"],
                          "desc": desc,
                          "hwid": hwid})
    return found


def port_exists(port: str) -> bool:
    """True when ``port`` is still present in the OS enumeration."""
    if not port:
        return False
    want = normalize_port(port)
    for p in _comports():
        if getattr(p, "device", "") == want:
            return True
    return False
