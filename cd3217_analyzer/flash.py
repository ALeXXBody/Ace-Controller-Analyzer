"""SPI flash operations for reading/writing external ROM chips.

Supports standard SPI NOR flash commands used by chips found on
MacBook CD3217B12 boards:
- Winbond W25Q80, W25Q16, W25Q32
- ISSI IS25LP080, IS25WP080
- GD25Q80, GD25Q16
- Any JEDEC-compliant SPI flash

Typical flash on CD3217B12 boards: W25Q80 (1MB / 8Mbit)
"""

import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from .spi_adapter import SPIAdapter


# ─── SPI Flash Commands ──────────────────────────────────────────────────────

CMD_READ_JEDEC_ID    = 0x9F
CMD_READ_DATA        = 0x03
CMD_FAST_READ        = 0x0B
CMD_PAGE_PROGRAM     = 0x02
CMD_WRITE_ENABLE     = 0x06
CMD_WRITE_DISABLE    = 0x04
CMD_CHIP_ERASE       = 0xC7  # Also 0x60
CMD_SECTOR_ERASE     = 0x20  # 4KB
CMD_BLOCK_ERASE_32K  = 0x52
CMD_BLOCK_ERASE_64K  = 0xD8
CMD_READ_STATUS_REG1 = 0x05
CMD_READ_STATUS_REG2 = 0x35
CMD_WRITE_STATUS_REG = 0x01
CMD_READ_SFDP        = 0x5A
CMD_POWER_DOWN       = 0xB9
CMD_RELEASE_PD       = 0xAB
CMD_ENABLE_RESET     = 0x66
CMD_RESET            = 0x99

# Status register bits
STATUS_BUSY = 0x01
STATUS_WEL  = 0x02

# Common flash sizes
SECTOR_SIZE = 4096        # 4KB
BLOCK_SIZE_32K = 32768    # 32KB
BLOCK_SIZE_64K = 65536    # 64KB
PAGE_SIZE = 256           # 256 bytes

# Known flash chips (JEDEC ID → description)
KNOWN_FLASHES = {
    (0xEF, 0x40, 0x14): "Winbond W25Q80 (1MB)",
    (0xEF, 0x40, 0x15): "Winbond W25Q16 (2MB)",
    (0xEF, 0x40, 0x16): "Winbond W25Q32 (4MB)",
    (0xEF, 0x40, 0x17): "Winbond W25Q64 (8MB)",
    (0xEF, 0x40, 0x18): "Winbond W25Q128 (16MB)",
    (0x9D, 0x40, 0x14): "ISSI IS25LP080 (1MB)",
    (0x9D, 0x60, 0x14): "ISSI IS25WP080 (1MB)",
    (0xC8, 0x40, 0x14): "GD25Q80 (1MB)",
    (0xC8, 0x40, 0x15): "GD25Q16 (2MB)",
    (0x20, 0x40, 0x14): "Micron M25P80 (1MB)",
    (0x20, 0xBA, 0x14): "Micron MT25Q80 (1MB)",
}

# Flash size lookup from capacity byte (2^N bytes)
CAPACITY_TO_SIZE = {
    0x14: 1 * 1024 * 1024,     # 1MB (8Mbit)
    0x15: 2 * 1024 * 1024,     # 2MB
    0x16: 4 * 1024 * 1024,     # 4MB
    0x17: 8 * 1024 * 1024,     # 8MB
    0x18: 16 * 1024 * 1024,    # 16MB
}


@dataclass
class FlashInfo:
    """Information about a detected SPI flash chip."""
    manufacturer_id: int
    memory_type: int
    capacity: int
    jedec_id: Tuple[int, int, int]
    name: str
    size_bytes: int

    @property
    def size_mb(self) -> float:
        return self.size_bytes / (1024 * 1024)

    @property
    def sector_count(self) -> int:
        return self.size_bytes // SECTOR_SIZE


class FlashError(Exception):
    """Flash operation error."""
    pass


class SPIFlash:
    """SPI flash operations for external ROM chips."""

    def __init__(self, spi: SPIAdapter):
        self.spi = spi
        self.info: Optional[FlashInfo] = None

    def _cmd(self, cmd: int, data: bytes = b"") -> bytes:
        """Send a SPI command."""
        return self.spi.transfer(bytes([cmd]) + data)

    def _write_enable(self) -> None:
        """Enable write/erase operations."""
        self._cmd(CMD_WRITE_ENABLE)
        # Verify WEL bit is set
        status = self._cmd(CMD_READ_STATUS_REG1, b"\x00")
        if len(status) >= 2 and not (status[1] & STATUS_WEL):
            raise FlashError("Write enable failed — WEL bit not set")

    def _wait_busy(self, timeout: float = 10.0) -> None:
        """Wait for flash to finish internal operation."""
        start = time.time()
        while time.time() - start < timeout:
            status = self._cmd(CMD_READ_STATUS_REG1, b"\x00")
            if len(status) >= 2 and not (status[1] & STATUS_BUSY):
                return
            time.sleep(0.01)
        raise FlashError("Timeout waiting for flash (busy)")

    # ─── Identification ────────────────────────────────────────────────────

    def read_jedec_id(self) -> Tuple[int, int, int]:
        """Read JEDEC manufacturer/type/capacity ID."""
        resp = self._cmd(CMD_READ_JEDEC_ID, b"\x00\x00\x00")
        if len(resp) < 4:
            raise FlashError(f"Invalid JEDEC ID response: {resp.hex()}")
        return (resp[1], resp[2], resp[3])

    def detect(self) -> FlashInfo:
        """Detect and identify the connected SPI flash chip."""
        mfr, mtype, cap = self.read_jedec_id()

        name = KNOWN_FLASHES.get((mfr, mtype, cap),
                                  f"Unknown (0x{mfr:02X},0x{mtype:02X},0x{cap:02X})")
        size = CAPACITY_TO_SIZE.get(cap, 0)

        self.info = FlashInfo(
            manufacturer_id=mfr,
            memory_type=mtype,
            capacity=cap,
            jedec_id=(mfr, mtype, cap),
            name=name,
            size_bytes=size,
        )
        return self.info

    def power_up(self) -> None:
        """Release from power-down mode."""
        self._cmd(CMD_RELEASE_PD)
        time.sleep(0.01)

    def reset(self) -> None:
        """Software reset the flash chip."""
        self._cmd(CMD_ENABLE_RESET)
        self._cmd(CMD_RESET)
        time.sleep(0.01)

    # ─── Read ──────────────────────────────────────────────────────────────

    def read(self, address: int, length: int) -> bytes:
        """Read data from flash at given address."""
        addr_bytes = struct.pack(">I", address & 0xFFFFFF)[1:]  # 3-byte address
        # Send [CMD_READ_DATA, addr2, addr1, addr0] then clock out length bytes
        # exchange() sends cmd+addr+zeros, receives garbage+data
        resp = self.spi.transfer(
            bytes([CMD_READ_DATA]) + addr_bytes + b"\x00" * length
        )
        # First 4 bytes are command+address (received as garbage), data follows
        return resp[4:4 + length]

    def read_all(self, progress_cb: Callable = None) -> bytes:
        """Read entire flash contents."""
        if not self.info:
            self.detect()
        if self.info.size_bytes == 0:
            raise FlashError("Unknown flash size — cannot read all")

        data = bytearray()
        chunk_size = 4096  # Read in 4KB chunks
        total = self.info.size_bytes

        for offset in range(0, total, chunk_size):
            length = min(chunk_size, total - offset)
            chunk = self.read(offset, length)
            data.extend(chunk)
            if progress_cb:
                progress_cb(offset + length, total)

        return bytes(data)

    # ─── Erase ─────────────────────────────────────────────────────────────

    def erase_sector(self, address: int) -> None:
        """Erase a 4KB sector at the given address."""
        self._write_enable()
        addr_bytes = struct.pack(">I", address & 0xFFFFFF)[1:]
        self._cmd(CMD_SECTOR_ERASE, addr_bytes)
        self._wait_busy()

    def erase_block_32k(self, address: int) -> None:
        """Erase a 32KB block."""
        self._write_enable()
        addr_bytes = struct.pack(">I", address & 0xFFFFFF)[1:]
        self._cmd(CMD_BLOCK_ERASE_32K, addr_bytes)
        self._wait_busy(timeout=30)

    def erase_block_64k(self, address: int) -> None:
        """Erase a 64KB block."""
        self._write_enable()
        addr_bytes = struct.pack(">I", address & 0xFFFFFF)[1:]
        self._cmd(CMD_BLOCK_ERASE_64K, addr_bytes)
        self._wait_busy(timeout=30)

    def erase_chip(self) -> None:
        """Erase entire chip (may take several seconds)."""
        self._write_enable()
        self._cmd(CMD_CHIP_ERASE)
        self._wait_busy(timeout=60)

    def erase_range(self, start: int, end: int,
                    progress_cb: Callable = None) -> None:
        """Erase all sectors covering the address range [start, end)."""
        start = start & ~(SECTOR_SIZE - 1)  # Align to sector boundary
        total = end - start
        done = 0

        while start < end:
            self.erase_sector(start)
            start += SECTOR_SIZE
            done += SECTOR_SIZE
            if progress_cb:
                progress_cb(min(done, total), total)

    # ─── Write ─────────────────────────────────────────────────────────────

    def write_page(self, address: int, data: bytes) -> None:
        """Write up to 256 bytes to a page boundary."""
        if len(data) > PAGE_SIZE:
            raise FlashError(f"Page write too large: {len(data)} bytes (max {PAGE_SIZE})")

        self._write_enable()
        addr_bytes = struct.pack(">I", address & 0xFFFFFF)[1:]
        self._cmd(CMD_PAGE_PROGRAM, addr_bytes + data)
        self._wait_busy()

    def write(self, address: int, data: bytes,
              progress_cb: Callable = None) -> None:
        """Write data to flash, handling page boundaries automatically."""
        offset = 0
        total = len(data)

        while offset < total:
            # Calculate how much fits in current page
            page_start = (address + offset) % PAGE_SIZE
            bytes_left_in_page = PAGE_SIZE - page_start
            chunk_len = min(bytes_left_in_page, total - offset)

            chunk = data[offset:offset + chunk_len]
            self.write_page(address + offset, chunk)

            offset += chunk_len
            if progress_cb:
                progress_cb(offset, total)

    def write_verify(self, address: int, data: bytes,
                     progress_cb: Callable = None) -> Tuple[bool, int]:
        """Write data and verify by reading back. Returns (success, first_error_address)."""
        self.write(address, data, progress_cb)
        readback = self.read(address, len(data))
        if readback == data:
            return True, -1

        # Find first mismatch
        for i in range(len(data)):
            if readback[i] != data[i]:
                return False, address + i
        return True, -1

    # ─── Convenience ───────────────────────────────────────────────────────

    def full_restore(self, filepath: str,
                     progress_cb: Callable = None) -> None:
        """Erase chip and write firmware from file, with verify.

        progress_cb is called as progress_cb(current, total).
        """
        data = Path(filepath).read_bytes()
        if not self.info:
            self.detect()

        if self.info.size_bytes == 0:
            raise FlashError("Unknown flash size — cannot restore")

        if len(data) > self.info.size_bytes:
            raise FlashError(f"File too large: {len(data)} > {self.info.size_bytes}")

        def report(cur: int, total: int) -> None:
            if progress_cb:
                progress_cb(cur, total)

        # Erase (0-10%)
        report(0, 100)
        self.erase_chip()
        report(10, 100)

        # Write (10-90%)
        def write_progress(cur, total):
            if total:
                report(10 + int(cur / total * 80), 100)

        success, err_addr = self.write_verify(0, data, progress_cb=write_progress)
        report(100, 100)

        if not success:
            bad = self.read(err_addr, 1)
            got = bad[0] if bad else 0xFF
            expected = data[err_addr] if 0 <= err_addr < len(data) else 0xFF
            raise FlashError(
                f"Verify failed at 0x{err_addr:06X}: "
                f"expected 0x{expected:02X}, got 0x{got:02X}"
            )

    def dump_to_file(self, filepath: str,
                     progress_cb: Callable = None) -> int:
        """Read entire flash and save to file. Returns bytes read."""
        if not self.info:
            self.detect()

        data = self.read_all(progress_cb=progress_cb)
        Path(filepath).write_bytes(data)
        return len(data)
