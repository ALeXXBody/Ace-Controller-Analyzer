"""SPI flash over the CD3217-Analyzer board USB bridge.

Lets any board (ESP32 / RP2040 family) act as the SPI adapter instead of an
FTDI FT232H dongle: the board's hardware SPI pins drive the target flash chip
(via the level-shifting shield) and this module tunnels the full-duplex SPI
exchange through the USB-CDC bridge protocol (cmd 0x10 SPIXFR).

Two pieces:

- ``BridgeSPIAdapter`` — drop-in replacement for the FTDI ``SPIAdapter``
  (same ``transfer()`` full-duplex semantics, just over the board).
- ``BridgeSPIFlash`` — ``SPIFlash`` subclass that chunks reads/writes into
  bridge-sized pieces (the bridge frame carries ≤240 payload bytes; multiple
  non-overlapping partial page programs per page are legal per SPI NOR spec).

The board's USB bridge must already be open (``UsbBridgeAdapter.open()``)
before ``transfer()`` is used; I2C and SPI share the same port.
"""

import struct
from typing import Optional

from .flash import (
    CMD_PAGE_PROGRAM,
    CMD_READ_DATA,
    SPIFlash,
)
from .usb_bridge import UsbBridgeAdapter

CMD_SPI_XFR = 0x10        # bridge command: full-duplex SPI exchange

# Bridge frames carry a 1-byte payload length; response = status + rx bytes,
# so tx payloads must stay ≤ 240. SPI commands add a 4-byte header (opcode +
# 3-byte address), leaving 236 data bytes per exchange.
BRIDGE_SPI_CHUNK = 240
SPI_DATA_CHUNK = BRIDGE_SPI_CHUNK - 4


class BridgeSPIAdapter:
    """FTDI-SPIAdapter-compatible SPI backend over the USB board bridge."""

    def __init__(self, bridge: UsbBridgeAdapter):
        if not isinstance(bridge, UsbBridgeAdapter):
            raise TypeError("BridgeSPIAdapter needs a UsbBridgeAdapter")
        self.bridge = bridge
        self.url = f"board:{bridge.port}"
        self.frequency = 2000000

    # SPIAdapter interface ----------------------------------------------------
    def open(self) -> None:
        # The underlying USB bridge owns the serial port; it must already be
        # open. Opening it here as well is a no-op (idempotent).
        self.bridge.open()

    def close(self) -> None:
        # Do NOT close the shared bridge — the I2C side may still use it.
        pass

    def transfer(self, data: bytes) -> bytes:
        """Full-duplex SPI exchange: send ``data``, return the same-length rx."""
        data = bytes(data)
        if len(data) > BRIDGE_SPI_CHUNK:
            raise ValueError(
                f"SPI transfer too large for bridge frame: {len(data)} "
                f"(max {BRIDGE_SPI_CHUNK}); use BridgeSPIFlash for chunked ops")
        resp = self.bridge._transact(CMD_SPI_XFR, data)
        if not resp or resp[0] != 0x00:
            raise IOError(f"Bridge SPI transfer failed (status "
                          f"0x{resp[0]:02X})" if resp else "Bridge SPI: no response")
        return resp[1:]

    def write(self, data: bytes) -> None:
        self.transfer(data)

    def read(self, length: int) -> bytes:
        return self.transfer(b"\x00" * length)

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *args):
        self.close()


class BridgeSPIFlash(SPIFlash):
    """SPIFlash with bridge-sized chunking (reads and page programs).

    ``read()`` re-issues the 0x03 READ command per chunk; ``write_page()``
    splits a page into non-overlapping partial page programs (legal per SPI
    NOR spec: any byte may only be programmed once between erases).
    """

    def read(self, address: int, length: int) -> bytes:
        out = bytearray()
        remaining = length
        addr = address
        while remaining > 0:
            n = min(SPI_DATA_CHUNK, remaining)
            addr_bytes = struct.pack(">I", addr & 0xFFFFFF)[1:]
            resp = self.spi.transfer(
                bytes([CMD_READ_DATA]) + addr_bytes + b"\x00" * n)
            if len(resp) < 4 + n:
                raise IOError(f"Short SPI read at 0x{addr:06X}: "
                              f"{len(resp)} < {4 + n}")
            out += resp[4:4 + n]
            addr += n
            remaining -= n
        return bytes(out)

    def write_page(self, address: int, data: bytes) -> None:
        if len(data) > 256:
            raise ValueError(f"Page write too large: {len(data)} bytes (max 256)")
        off = 0
        while off < len(data):
            n = min(SPI_DATA_CHUNK, len(data) - off)
            chunk = data[off:off + n]
            self._write_enable()
            addr_bytes = struct.pack(">I", (address + off) & 0xFFFFFF)[1:]
            self._cmd(CMD_PAGE_PROGRAM, addr_bytes + chunk)
            self._wait_busy()
            off += n


def make_bridge_flash(port: Optional[str] = None) -> tuple:
    """Open a USB bridge and return (bridge, flash) ready for SPI ops.

    Convenience for the CLI/GUI: connects the board, verifies it answers,
    and wraps a BridgeSPIFlash around it.
    """
    from .usb_bridge import normalize_port
    port = normalize_port(port) if port else None
    if not port:
        from .usb_bridge import list_bridge_ports
        ports = list_bridge_ports()
        if not ports:
            raise IOError("No USB serial port found. Plug in the board and "
                          "specify the port (e.g. --port COM5).")
        port = ports[0]
    bridge = UsbBridgeAdapter(port=port)
    bridge.open()
    if not bridge.handshake():
        bridge.close()
        raise IOError(f"Board on {port} did not answer PING — is it running "
                      "CD3217 firmware?")
    spi = BridgeSPIAdapter(bridge)
    return bridge, BridgeSPIFlash(spi)
