"""Shared helpers used by CLI and GUI."""

from __future__ import annotations

import threading
from typing import Callable, Iterable, List, Optional


def parse_hex_address(value: str) -> int:
    """Parse an I2C address string into an int.

    Accepts ``0x38``, ``38``, or ``56``. Bare values are treated as hex
    (standard for I2C tooling).
    """
    text = (value or "").strip().lower()
    if not text:
        raise ValueError("Empty address")
    if text.startswith("0x"):
        text = text[2:]
    if not text or any(c not in "0123456789abcdef" for c in text):
        raise ValueError(f"Invalid address: {value}")
    return int(text, 16)


def parse_address_list(value: str) -> List[int]:
    """Parse comma-separated addresses."""
    parts = [p.strip() for p in (value or "").split(",") if p.strip()]
    return [parse_hex_address(p) for p in parts]


def format_hex_addr(addr: int) -> str:
    return f"0x{addr:02X}"


def run_bg(target: Callable, *, daemon: bool = True) -> threading.Thread:
    """Run a callable on a daemon background thread."""
    thread = threading.Thread(target=target, daemon=daemon)
    thread.start()
    return thread


def clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def unique_sorted(addrs: Iterable[int]) -> List[int]:
    return sorted(set(int(a) for a in addrs))


def merge_ff_reads(snapshots: "Iterable[bytes]") -> bytes:
    """Byte-wise merge of repeated register reads against truncation.

    CD3217 register responses carry a length-prefix byte (§2.7), so
    byte-wise sub-reads are meaningless; instead, N spaced attempts of
    the same register truncate at DIFFERENT points. Merging takes the
    first non-0xFF byte at each position and assembles the complete
    response. Result length = the longest snapshot's length.

    This is the single implementation the analyzer (merged truncation
    repair), otp.scan_otp and otp_probe (paced reads) all use.
    """
    out = bytearray()
    for snap in snapshots:
        if snap is None:
            continue
        if len(snap) > len(out):
            out.extend(b"\xFF" * (len(snap) - len(out)))
        for i, b in enumerate(snap):
            if out[i] == 0xFF and b != 0xFF:
                out[i] = b
    return bytes(out)
