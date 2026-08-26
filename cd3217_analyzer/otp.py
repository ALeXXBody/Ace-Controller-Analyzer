"""CD3217B12 OTP (One-Time Programmable) memory scanning and analysis tools.

The OTP fuse map is undocumented for CD3217B12. This module provides tools
to read the full register space, compare vanilla vs OTP-ed chips, and
identify which addresses are OTP-backed by diffing dumps.

Approach:
1. Read full register range (0x00-0x7F) from a vanilla chip (OTP empty)
2. Read same range from an OTP-ed chip (pulled from working board)
3. Diff the two dumps — differing bytes = OTP content
4. Correlate with known changes (I2C address, mode, etc.)
"""

import json
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Dict, List, Optional, Tuple

from .adapters import I2CAdapter
from .registers import KNOWN_ACE2_ADDRESSES, REGISTERS, is_ace2_address


# Full register space to scan — covers TPS65982/TPS65987D + Apple extensions
OTP_SCAN_START = 0x00
OTP_SCAN_END = 0x7F
OTP_CHUNK_SIZE = 4  # Read 4 bytes at a time (standard I2C register width)


@dataclass
class OTPDump:
    """A complete register dump from a CD3217B12 chip."""
    address: int
    label: str
    timestamp: str
    registers: Dict[int, bytes] = field(default_factory=dict)
    read_errors: List[int] = field(default_factory=list)
    notes: str = ""

    @property
    def filled_count(self) -> int:
        return len(self.registers)

    @property
    def error_count(self) -> int:
        return len(self.read_errors)


@dataclass
class OTPDiffResult:
    """Result of comparing two OTP dumps."""
    dump_a: OTPDump
    dump_b: OTPDump
    identical: List[int] = field(default_factory=list)
    different: List[int] = field(default_factory=list)
    only_a: List[int] = field(default_factory=list)
    only_b: List[int] = field(default_factory=list)

    @property
    def match_count(self) -> int:
        return len(self.identical)

    @property
    def diff_count(self) -> int:
        return len(self.different)

    def summary(self) -> str:
        lines = [
            f"OTP Diff: {self.dump_a.label} vs {self.dump_b.label}",
            f"  Identical registers: {self.match_count}",
            f"  Different registers: {self.diff_count}",
            f"  Only in A: {len(self.only_a)}",
            f"  Only in B: {len(self.only_b)}",
            "",
        ]

        if self.different:
            lines.append("Different registers (likely OTP-backed):")
            lines.append(f"  {'Offset':<10} {'A (hex)':<20} {'B (hex)':<20} {'Notes'}")
            lines.append(f"  {'-'*10} {'-'*20} {'-'*20} {'-'*30}")
            for offset in sorted(self.different):
                a_bytes = self.dump_a.registers[offset]
                b_bytes = self.dump_b.registers[offset]
                a_hex = a_bytes.hex()
                b_hex = b_bytes.hex()

                # Check if this register is in our known map
                reg_def = REGISTERS.get(offset)
                note = reg_def.name if reg_def else "Unknown"

                lines.append(f"  0x{offset:02X}     {a_hex:<20} {b_hex:<20} {note}")

        if self.only_a:
            lines.append(f"\nOnly in {self.dump_a.label}: {', '.join(f'0x{o:02X}' for o in self.only_a)}")
        if self.only_b:
            lines.append(f"\nOnly in {self.dump_b.label}: {', '.join(f'0x{o:02X}' for o in self.only_b)}")

        return "\n".join(lines)


def scan_otp(adapter: I2CAdapter, address: int, label: str = "",
             start: int = OTP_SCAN_START, end: int = OTP_SCAN_END,
             chunk_size: int = OTP_CHUNK_SIZE,
             progress_cb=None) -> OTPDump:
    """Read the full register space from a CD3217B12 chip.

    Args:
        adapter: Open I2C adapter
        address: 7-bit I2C address of the chip
        label: Human-readable label for this dump
        start: Start register offset
        end: End register offset (inclusive)
        chunk_size: Bytes to read per transaction
        progress_cb: Optional callback(current, total) for progress updates

    Returns:
        OTPDump with all readable registers
    """
    from datetime import datetime

    dump = OTPDump(
        address=address,
        label=label or f"0x{address:02X}",
        timestamp=datetime.now().isoformat(),
    )

    total = end - start + 1
    offset = start

    while offset <= end:
        remaining = end - offset + 1
        read_len = min(chunk_size, remaining)

        try:
            data = adapter.read_bytes(address, offset, read_len)
            dump.registers[offset] = data
        except Exception:
            dump.read_errors.append(offset)

        offset += read_len

        if progress_cb:
            progress_cb(offset - start, total)

    return dump


def diff_dumps(dump_a: OTPDump, dump_b: OTPDump) -> OTPDiffResult:
    """Compare two OTP dumps register-by-register.

    Returns a diff result identifying which registers are identical
    (RAM/config) vs different (likely OTP-backed).
    """
    result = OTPDiffResult(dump_a=dump_a, dump_b=dump_b)

    all_offsets = set(dump_a.registers.keys()) | set(dump_b.registers.keys())

    for offset in sorted(all_offsets):
        in_a = offset in dump_a.registers
        in_b = offset in dump_b.registers

        if in_a and in_b:
            if dump_a.registers[offset] == dump_b.registers[offset]:
                result.identical.append(offset)
            else:
                result.different.append(offset)
        elif in_a:
            result.only_a.append(offset)
        else:
            result.only_b.append(offset)

    return result


def save_dump_binary(dump: OTPDump, filepath: str) -> None:
    """Save an OTP dump as a binary file (raw register contents).

    Format: 16 bytes per line, offset-prefixed hex dump.
    File extension: .otp.bin
    """
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "wb") as f:
        # Header: magic + address + label length + label
        f.write(b"CD3217OTP")
        f.write(struct.pack("B", dump.address))
        label_bytes = dump.label.encode("utf-8")[:255]
        f.write(struct.pack("B", len(label_bytes)))
        f.write(label_bytes)

        # Register data: 2-byte offset + 4-byte length + data
        for offset in sorted(dump.registers.keys()):
            data = dump.registers[offset]
            f.write(struct.pack(">H", offset))
            f.write(struct.pack(">H", len(data)))
            f.write(data)

        # Terminator
        f.write(struct.pack(">H", 0xFFFF))


def load_dump_binary(filepath: str) -> Optional[OTPDump]:
    """Load an OTP dump from a binary file."""
    from datetime import datetime

    try:
        with open(filepath, "rb") as f:
            magic = f.read(9)
            if magic != b"CD3217OTP":
                return None

            addr = struct.unpack("B", f.read(1))[0]
            label_len = struct.unpack("B", f.read(1))[0]
            label = f.read(label_len).decode("utf-8")

            dump = OTPDump(
                address=addr,
                label=label,
                timestamp=datetime.now().isoformat(),
            )

            while True:
                offset_bytes = f.read(2)
                if len(offset_bytes) < 2:
                    break
                offset = struct.unpack(">H", offset_bytes)[0]
                if offset == 0xFFFF:
                    break

                length = struct.unpack(">H", f.read(2))[0]
                data = f.read(length)
                if len(data) == length:
                    dump.registers[offset] = data

            return dump
    except Exception:
        return None


def save_dump_json(dump: OTPDump, filepath: str) -> None:
    """Save an OTP dump as JSON for easy inspection."""
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "address": dump.address,
        "label": dump.label,
        "timestamp": dump.timestamp,
        "notes": dump.notes,
        "registers": {
            f"0x{offset:02X}": bytes.hex(raw)
            for offset, raw in sorted(dump.registers.items())
        },
        "read_errors": [f"0x{e:02X}" for e in dump.read_errors],
    }

    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def load_dump_json(filepath: str) -> Optional[OTPDump]:
    """Load an OTP dump from a JSON file."""
    from datetime import datetime

    try:
        with open(filepath) as f:
            data = json.load(f)

        dump = OTPDump(
            address=data["address"],
            label=data.get("label", ""),
            timestamp=data.get("timestamp", datetime.now().isoformat()),
            notes=data.get("notes", ""),
        )

        for offset_hex, hex_str in data.get("registers", {}).items():
            offset = int(offset_hex, 16)
            dump.registers[offset] = bytes.fromhex(hex_str)

        for err_hex in data.get("read_errors", []):
            dump.read_errors.append(int(err_hex, 16))

        return dump
    except Exception:
        return None


def save_diff_report(result: OTPDiffResult, filepath: str) -> None:
    """Save a diff result as a text report."""
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w") as f:
        f.write(result.summary())
        f.write("\n\n")
        f.write(f"Dump A: {result.dump_a.label} @ 0x{result.dump_a.address:02X}\n")
        f.write(f"Dump B: {result.dump_b.label} @ 0x{result.dump_b.address:02X}\n")


def format_dump_table(dump: OTPDump, show_zeros: bool = False) -> str:
    """Format an OTP dump as a readable hex table."""
    lines = [
        f"OTP Dump: {dump.label} (0x{dump.address:02X})",
        f"Timestamp: {dump.timestamp}",
        f"Registers read: {dump.filled_count} | Errors: {dump.error_count}",
        "",
        f"{'Offset':<8} {'Hex':<14} {'Dec':<14} {'Register'}",
        f"{'-'*8} {'-'*14} {'-'*14} {'-'*30}",
    ]

    for offset in sorted(dump.registers.keys()):
        raw = dump.registers[offset]
        hex_str = raw.hex()
        val = int.from_bytes(raw, "little")

        reg_def = REGISTERS.get(offset)
        name = reg_def.name if reg_def else f"REG_0x{offset:02X}"

        if not show_zeros and val == 0 and not reg_def:
            continue

        lines.append(f"0x{offset:02X}    {hex_str:<14} {val:<14} {name}")

    if dump.read_errors:
        lines.append(f"\nRead errors at: {', '.join(f'0x{e:02X}' for e in dump.read_errors)}")

    return "\n".join(lines)
