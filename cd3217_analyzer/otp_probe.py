"""Sacrificial-chip OTP probe tools (bench fixture stage).

Hunting the CD3217/CD3218 OTP burn mechanism on a chip connected ALONE to
the adapter (the owner's fixture plan). Three probes, ordered by risk:

1. Extended-page read (READ-ONLY, safe): the standard OTP window is
   0x00-0x7C. The burned address override is not visible there (§3.10b),
   so probe 0x80-0xFC for a hidden page and report which offsets NACK,
   return all-FF, or carry data.

2. Golden diff (READ-ONLY, safe): scan the live chip and diff against a
   golden dump (a saved --otp-scan json, or a chip inside an export
   bundle) to see exactly which registers differ between the sacrificial
   chip and a known Apple chip.

3. Write-probe (mutates RAM state, restores after each step): write a
   one-bit-changed value into each register, read back, and classify the
   register WRITABLE / REJECTED / UNEXPECTED, then restore the original.
   This maps which register space is RAM (config) vs hardwired/OTP — the
   prerequisite for finding the burn command. Plain register writes are
   NOT known to trigger a fuse burn; the probe never attempts an
   undocumented burn sequence.

Safety: OTP is one-time programmable. A wrong burn kills the donor chip.
Nothing in this module writes a value that persists: every write probe
restores the original bytes. The write probe is opt-in per command and
confirmed interactively (or --yes for scripting).
"""

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from .adapters import I2CAdapter
from .otp import OTPDump, diff_dumps
from . import debuglog

# The standard window ends at 0x7C (32 x 4-byte regs). The extended probe
# covers the rest of the single-byte register space.
STANDARD_END = 0x7C
EXTENDED_START = 0x80
EXTENDED_END = 0xFC
REG_WIDTH = 4

# Pacing for the marginal-bus reality (TI SLVA689) — same recipe scan_otp
# uses so probe reads behave like production reads.
READ_SPACING = 0.02
READ_RETRIES = 2
RETRY_DELAY = 0.12


def read_register_safe(adapter: I2CAdapter, address: int, offset: int,
                       width: int = REG_WIDTH) -> Optional[bytes]:
    """One paced 4-byte read with retries and 0xFF-fill merge.

    Returns None when every attempt NACKs. Returns the merged bytes when
    the chip answered (possibly all-FF — the caller distinguishes).
    """
    for attempt in range(1 + READ_RETRIES):
        if attempt:
            time.sleep(RETRY_DELAY)
        try:
            data = adapter.read_bytes(address, offset, width)
        except Exception:
            data = None
        if data is None:
            continue
        data = bytearray(data)
        if 0xFF in data:
            for _ in range(3):
                time.sleep(READ_SPACING)
                try:
                    again = adapter.read_bytes(address, offset, width)
                except Exception:
                    continue
                for i, byte in enumerate(again):
                    if data[i] == 0xFF and byte != 0xFF:
                        data[i] = byte
                if 0xFF not in data:
                    break
        return bytes(data)
    return None


@dataclass
class PageProbeResult:
    """Which parts of a register page answer, and with what."""
    start: int
    end: int
    with_data: Dict[int, bytes] = field(default_factory=dict)
    all_ff: List[int] = field(default_factory=list)
    nacked: List[int] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"Page probe 0x{self.start:02X}-0x{self.end:02X}: "
            f"{len(self.with_data)} with data, "
            f"{len(self.all_ff)} all-FF, {len(self.nacked)} NACK",
        ]
        if self.with_data:
            lines.append("Offsets carrying data (candidates for the "
                         "hidden OTP page):")
            for off in sorted(self.with_data):
                lines.append(f"  0x{off:02X}: {self.with_data[off].hex()}")
        return "\n".join(lines)


def probe_extended_page(adapter: I2CAdapter, address: int,
                        start: int = EXTENDED_START,
                        end: int = EXTENDED_END) -> PageProbeResult:
    """Read-only probe of the register space beyond the standard window.

    Buckets every 4-byte offset into NACK (chip refused / no register),
    all-FF (answered but empty — unwritten OTP reads like this), or
    with-data (something lives there).
    """
    res = PageProbeResult(start=start, end=end)
    offset = start
    while offset <= end:
        data = read_register_safe(adapter, address, offset)
        if data is None:
            res.nacked.append(offset)
        elif set(data) == {0xFF}:
            res.all_ff.append(offset)
        else:
            res.with_data[offset] = data
        offset += REG_WIDTH
        time.sleep(READ_SPACING)
    return res


@dataclass
class WriteProbeResult:
    """Outcome of one register's write-probe (original always restored)."""
    offset: int
    original: bytes
    test_value: bytes
    readback: Optional[bytes]
    restored: bool
    verdict: str        # WRITABLE / REJECTED / UNEXPECTED / UNREADABLE


def probe_writability(adapter: I2CAdapter, address: int,
                      start: int = 0x08, end: int = STANDARD_END,
                      progress_cb=None) -> List[WriteProbeResult]:
    """Map which registers accept a RAM write (and restore them).

    For each 4-byte offset: read original, write original with bit 0 of
    byte 0 flipped, read back, restore original, verify the restore.
    Never attempts an undocumented burn sequence; nothing persists.
    """
    results: List[WriteProbeResult] = []
    offsets = list(range(start, end + 1, REG_WIDTH))
    total = len(offsets)

    for idx, offset in enumerate(offsets):
        original = read_register_safe(adapter, address, offset)
        if original is None:
            results.append(WriteProbeResult(
                offset=offset, original=b"", test_value=b"",
                readback=None, restored=False, verdict="UNREADABLE"))
            if progress_cb:
                progress_cb(idx + 1, total)
            continue

        probe = bytearray(original)
        probe[0] ^= 0x01
        probe = bytes(probe)

        verdict = "REJECTED"
        readback = None
        try:
            ok = adapter.write_bytes(address, offset, probe)
        except Exception:
            ok = False
        if ok:
            time.sleep(READ_SPACING)
            readback = read_register_safe(adapter, address, offset)
            if readback == probe:
                verdict = "WRITABLE"
            elif readback == original:
                verdict = "REJECTED"
            else:
                verdict = "UNEXPECTED"

        # Restore/verify unconditionally — the chip must end as it
        # started. A REJECTED register was never altered, so verify by
        # reading first and only write when the readback differs.
        restored = False
        final = read_register_safe(adapter, address, offset)
        if final == original:
            restored = True
        else:
            try:
                if adapter.write_bytes(address, offset, original):
                    time.sleep(READ_SPACING)
                    restored = read_register_safe(
                        adapter, address, offset) == original
            except Exception:
                restored = False
        if debuglog.is_enabled():
            debuglog.log("OTP probe 0x%02X@0x%02X %s (restored=%s)",
                         address, offset, verdict, restored)

        results.append(WriteProbeResult(
            offset=offset, original=original, test_value=probe,
            readback=readback, restored=restored, verdict=verdict))
        if progress_cb:
            progress_cb(idx + 1, total)
        time.sleep(READ_SPACING)

    return results


def format_write_report(results: List[WriteProbeResult]) -> str:
    """Human-readable write-probe report grouped by verdict."""
    by_verdict: Dict[str, List[WriteProbeResult]] = {}
    for r in results:
        by_verdict.setdefault(r.verdict, []).append(r)
    counts = {v: len(rs) for v, rs in by_verdict.items()}
    lines = [
        "Write-probe verdicts (nothing persisted — originals restored):",
        f"  WRITABLE (RAM-backed):  {counts.get('WRITABLE', 0)}",
        f"  REJECTED (read-only):   {counts.get('REJECTED', 0)}",
        f"  UNEXPECTED:             {counts.get('UNEXPECTED', 0)}",
        f"  UNREADABLE:             {counts.get('UNREADABLE', 0)}",
    ]
    for verdict in ("WRITABLE", "REJECTED", "UNEXPECTED"):
        rs = by_verdict.get(verdict)
        if not rs:
            continue
        lines.append(f"\n{verdict}:")
        for r in rs:
            rb = r.readback.hex() if r.readback else "nack"
            flag = "" if r.restored else "  *** RESTORE FAILED ***"
            lines.append(
                f"  0x{r.offset:02X}: orig {r.original.hex()} -> "
                f"wrote {r.test_value.hex()}, readback {rb}{flag}")
    broken = [r for r in results if not r.restored
              and r.verdict != "UNREADABLE"]
    if broken:
        lines.append(
            "\nWARNING: " + ", ".join(f"0x{r.offset:02X}" for r in broken)
            + " could not be restored — re-read them before powering down.")
    return "\n".join(lines)


def load_golden_dump(path: str, chip: Optional[str] = None) -> Optional[OTPDump]:
    """Load a golden reference for the diff probe.

    Accepts an OTP dump JSON (as saved by --otp-scan -o) or an export
    bundle (which holds one otp_dump per chip; `chip` selects it, e.g.
    "0x3B"). Returns None when the file/chip doesn't fit.
    """
    import json as _json
    from .otp import load_dump_json, load_dump_binary

    dump = load_dump_json(path) or load_dump_binary(path)
    if dump is not None:
        return dump
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            bundle = _json.loads(f.read())
    except (OSError, ValueError):
        return None
    otp = (bundle.get("data") or {}).get("otp_dump") or {}
    if not isinstance(otp, dict) or not otp:
        return None
    if chip is None:
        if len(otp) == 1:
            chip = next(iter(otp))
        else:
            return None
    entry = otp.get(chip)
    if not isinstance(entry, dict):
        return None
    registers: Dict[int, bytes] = {}
    for k, v in (entry.get("registers") or {}).items():
        try:
            registers[int(k, 16)] = bytes.fromhex(v)
        except (ValueError, TypeError):
            continue
    from datetime import datetime as _dt
    return OTPDump(
        address=int(str(chip), 16), label=f"golden {chip}",
        timestamp=(bundle.get("generated_utc") or _dt.now().isoformat()),
        registers=registers,
        read_errors=list(entry.get("read_errors") or []),
    )


def compare_to_golden(live: OTPDump, golden: OTPDump):
    """Diff a live sacrificial-chip dump against the golden reference."""
    return diff_dumps(golden, live)


def new_dump(address: int, label: str, registers: Dict[int, bytes],
             read_errors: List[int]) -> OTPDump:
    return OTPDump(address=address, label=label,
                   timestamp=datetime.now().isoformat(),
                   registers=registers, read_errors=read_errors)
