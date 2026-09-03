"""Core diagnostic engine for CD3217B12 (Apple ACE2) I2C analysis.

Performs comprehensive testing of ACE2 controllers:
- I2C connectivity and address detection
- Register validation against known-good values
- OTP vs vanilla chip identification
- Mode/status assessment
- PD negotiation capability check
- Health scoring and fault classification
"""

import json
import time

from . import debuglog
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Tuple

from .adapters import I2CAdapter
from .registers import (
    ACE2_BROADCAST_ADDRESS,
    KNOWN_ACE2_ADDRESSES,
    REGISTERS,
    VALID_ACE2_VIDS,
    PortMode,
    decode_mode_reg,
    decode_silicon,
    decode_silicon_from_str,
    decode_type_reg,
    decode_vid,
    is_ace2_address,
    parse_device_info,
)


class HealthStatus(Enum):
    """Overall health assessment."""
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class FaultType(Enum):
    """Types of faults detected."""
    NO_RESPONSE = "NO_RESPONSE"
    WRONG_VID = "WRONG_VID"
    WRONG_MODE = "WRONG_MODE"
    CORRUPTED_REGISTERS = "CORRUPTED_REGISTERS"
    STUCK_BUS = "STUCK_BUS"
    UNEXPECTED_DEVICE = "UNEXPECTED_DEVICE"
    I2C_ERROR = "I2C_ERROR"
    WRONG_ADDRESS = "WRONG_ADDRESS"
    BOOT_FAILED = "BOOT_FAILED"
    ROM_MISSING = "ROM_MISSING"
    CHIP_MISMATCH = "CHIP_MISMATCH"


@dataclass
class RegisterRead:
    """Result of reading a single register."""
    offset: int
    name: str
    raw_bytes: bytes
    raw_value: int
    decoded: str = ""
    is_expected: Optional[bool] = None
    notes: str = ""


@dataclass
class DeviceResult:
    """Complete diagnostic result for a single ACE2 device."""
    address: int
    timestamp: str = ""
    # Connectivity
    responds: bool = False
    i2c_errors: int = 0
    scan_time_ms: float = 0.0
    # Identification
    vendor_id: Optional[str] = None
    device_id: Optional[str] = None
    did_raw: Optional[int] = None       # numeric DID register (0x01) value
    silicon: str = ""                   # decoded family: CD3217/CD3218/CD3215
    device_info: str = ""               # register 0x2F identity string
    hw_version: str = ""                # parsed from identity ("22")
    fw_version: str = ""                # parsed from identity ("002.170.00")
    fw_variant: str = ""                # role/firmware build ("ZACE2-J316P01P")
    mode: Optional[str] = None
    mode_raw: Optional[int] = None
    device_type: Optional[str] = None
    # Register reads
    registers: Dict[int, RegisterRead] = field(default_factory=dict)
    # Assessment
    health: HealthStatus = HealthStatus.UNKNOWN
    faults: List[FaultType] = field(default_factory=list)
    fault_details: List[str] = field(default_factory=list)
    health_score: int = 0  # 0-100
    # Metadata
    is_vanilla: Optional[bool] = None  # True=vanilla, False=OTP, None=unknown
    notes: str = ""


@dataclass
class DiagnosticReport:
    """Full diagnostic report for a scan session."""
    timestamp: str = ""
    adapter_type: str = ""
    adapter_info: str = ""
    bus_scan_results: List[int] = field(default_factory=list)
    devices: List[DeviceResult] = field(default_factory=list)
    summary: str = ""
    notes: str = ""


@dataclass
class BusStats:
    """Aggregated I2C bus integrity counters for a scan session.

    Two different signals live here and must not be conflated:

    * *Recovered flakiness* (pings that only ACKed after a retry, garbled
      identity reads that came back clean on a re-read) — a *bus margin*
      signal: probing a USB-C board adds capacitance to the I2C rails and
      shares the board's pull-ups, which eats edge-timing margin (TI
      SLVA689). This is what "the tap is marginal, the chips may be fine"
      means.
    * *Hard failures* (an address that never ACKed, registers that never
      read) — already reported per-chip as NO_RESPONSE / I2C_ERROR; a
      genuinely dead chip produces them on every scan, so by themselves
      they are NOT evidence of a bad probe.
    """
    pings: int = 0           # ping transactions issued
    ping_failures: int = 0   # ping attempts that did not ACK
    ping_recovered: int = 0  # addresses that ACKed only after a retry
    reads: int = 0           # register read transactions issued
    read_failures: int = 0   # reads that raised / returned no data
    contaminated_rereads: int = 0  # identity reads that looked garbled & re-read

    def add_ping(self, ok: bool) -> None:
        self.pings += 1
        if not ok:
            self.ping_failures += 1

    def add_recovered_ping(self) -> None:
        """Count an address that answered only after >=1 NACKed attempt."""
        self.ping_recovered += 1

    def add_read(self, ok: bool) -> None:
        self.reads += 1
        if not ok:
            self.read_failures += 1

    @property
    def nack_rate(self) -> float:
        """Fraction of transaction attempts that did not complete cleanly."""
        total = self.pings + self.reads
        if not total:
            return 0.0
        return (self.ping_failures + self.read_failures) / total

    @property
    def marginal(self) -> bool:
        """True when transactions flake but recover — the bus-margin signal.

        Deliberately excludes hard failures: a dead chip NACKs every ping on
        every scan (that is a chip fault, reported per-chip), whereas a
        recovered-after-retry ping or a garbled-then-clean register read
        only happens when the bus itself is marginal.
        """
        return self.ping_recovered > 0 or self.contaminated_rereads > 0


class CD3217Analyzer:
    """
    Main diagnostic analyzer for CD3217B12 (Apple ACE2) controllers.

    Usage:
        adapter = FTDIAdapter()
        adapter.open()
        analyzer = CD3217Analyzer(adapter)
        report = analyzer.full_diagnostic()
        analyzer.print_report(report)
    """

    # Key registers for health assessment
    HEALTH_CHECK_REGS = [0x00, 0x01, 0x03, 0x04, 0x0F]
    # Registers to read for detailed analysis
    DETAIL_REGS = [0x00, 0x01, 0x03, 0x04, 0x0F, 0x06, 0x14, 0x15, 0x29, 0x2D, 0x2F]

    # Bus settling: right after a NACKed address (dead chip) or bus
    # contention, the next transactions can return garbage or fail. The
    # adapters/bridges need a moment before data is trustworthy again.
    PING_RETRIES = 2            # extra ping attempts when the first NACKs
    PING_RETRY_DELAY = 0.05     # s between ping attempts
    BUS_SETTLE_AFTER_NACK = 0.1  # s to let the bus settle after NO_RESPONSE
    REG_RETRY_DELAY = 0.05      # s before re-reading a suspicious register
    REG_RETRIES = 2             # re-read attempts for identity registers
    REG_SPACING = 0.02          # s between consecutive register reads
    REG_FAIL_RETRY_DELAY = 0.15  # s before re-reading a register that
                                 # hard-failed (NACK) — longer than the
                                 # garble-recheck delay because a NACKed
                                 # chip/bus needs real settle time
    BOOT_SETTLE_DELAY = 0.5     # s before re-checking a BOOT mode read
    # S3 adaptive settle: when a diagnosis itself hit flakiness (recovered
    # ping / garbled read), let the bus settle before the next device so
    # the tail of the garble cannot contaminate it — instead of a fixed
    # inter-device delay for every scan.
    ADAPTIVE_PING_SETTLE = 0.05  # s after a ping that only ACKed on retry
    ADAPTIVE_SETTLE = 0.25       # s after a diagnosis that saw flakiness

    def __init__(self, adapter: I2CAdapter, addresses: Optional[List[int]] = None):
        self.adapter = adapter
        # Default target list excludes the all-call address (0x6B): every
        # chip ACKs it simultaneously, so a "device" there is never real and
        # the transaction garbles the bus for the next target.
        self.addresses = [a for a in (addresses or KNOWN_ACE2_ADDRESSES.keys())
                          if a != ACE2_BROADCAST_ADDRESS]
        # Cumulative bus-integrity counters; reset per scan session.
        self.bus_stats = BusStats()

    def reset_bus_stats(self) -> None:
        """Start a fresh bus-integrity accounting window (e.g. per scan)."""
        self.bus_stats = BusStats()

    def scan_bus(self, start: int = 0x08, end: int = 0x77) -> List[int]:
        """Scan the entire I2C bus and return all responding addresses.

        The ACE2 broadcast address (0x6B) is excluded: all chips answer it
        simultaneously, so it always reads garbled and is not a device.
        """
        results = [a for a in self.adapter.scan(start, end)
                   if a != ACE2_BROADCAST_ADDRESS]
        return results

    def quick_scan(self, addresses: Optional[List[int]] = None) -> List[int]:
        """
        Quick scan of known ACE2 addresses only.
        Returns list of addresses that ACK.
        """
        addrs = addresses or self.addresses
        found = []
        for addr in addrs:
            if self.adapter.ping(addr):
                found.append(addr)
        return found

    def read_register(self, address: int, offset: int, length: int = 4) -> Optional[RegisterRead]:
        """Read a register and return decoded result."""
        reg_def = REGISTERS.get(offset)
        if not reg_def:
            reg_def = type('RegDef', (), {
                'name': f'REG_0x{offset:02X}',
                'description': 'Unknown register',
                'expected_values': None,
            })()

        try:
            raw = self.adapter.read_bytes(address, offset, length)
            self.bus_stats.add_read(True)
            raw_int = int.from_bytes(raw, 'little')

            decoded = ""
            if offset == 0x00:
                decoded = decode_vid(raw_int)
            elif offset == 0x03:
                decoded = decode_mode_reg(raw_int)
            elif offset == 0x04:
                decoded = decode_type_reg(raw_int)

            return RegisterRead(
                offset=offset,
                name=reg_def.name if hasattr(reg_def, 'name') else f"0x{offset:02X}",
                raw_bytes=raw,
                raw_value=raw_int,
                decoded=decoded,
            )
        except Exception as e:
            self.bus_stats.add_read(False)
            return None

    def _ping_with_retry(self, address: int) -> bool:
        """Ping an address, retrying with a settle delay.

        On a board with a dead CD3217, transactions against the dead address
        leave the bus/bridge in a bad state for a short moment; the very
        next ping can NACK even on a healthy chip. Retrying after a short
        delay distinguishes 'flaky right after a NACK' from 'really dead'.
        """
        for attempt in range(1 + self.PING_RETRIES):
            ok = self.adapter.ping(address)
            self.bus_stats.add_ping(ok)
            if debuglog.is_enabled():
                debuglog.log("PING 0x%02X attempt %d: %s", address,
                             attempt + 1, "ok" if ok else "NACK")
            if ok:
                if attempt:
                    self.bus_stats.add_recovered_ping()
                    # S3: the recovered NACK may leave the bus mid-settle —
                    # give it a beat before the first register read.
                    time.sleep(self.ADAPTIVE_PING_SETTLE)
                return True
            if attempt < self.PING_RETRIES:
                time.sleep(self.PING_RETRY_DELAY)
        return False

    # Registers that get a garble-recheck on suspicious reads: VID, DID,
    # Mode and Type 4CCs (identity-critical fields) plus the ASCII string
    # registers (identity/build info).
    _IDENTITY_REGS = {0x00, 0x01, 0x03, 0x04}
    _STRING_REGS = {0x2E, 0x2F, 0xA1}
    _RECHECK_REGS = _IDENTITY_REGS | _STRING_REGS

    def _register_suspicious(self, offset: int, read: RegisterRead) -> bool:
        """True when an identity-register read shows undriven-byte garbage."""
        v = read.raw_value
        if v == 0xFFFFFFFF:
            return True
        if offset == 0x00:
            # VID is a 16-bit field; any high-byte content is garbage.
            return (v & 0xFFFF0000) != 0
        if offset in (0x03, 0x04):
            # 4CC registers: every byte must be printable ASCII, 0x00 padding,
            # or a 0x04 length prefix (verified wire format: [0x04,'A','P','P']
            # = "APP"). Anything else (0xFF undriven, byte-shifted junk) is
            # contaminated.
            for i in range(4):
                b = (v >> (i * 8)) & 0xFF
                if b == 0x00 or b == 0x04:
                    continue
                if not (0x20 <= b <= 0x7E):
                    return True
            return False
        if offset == 0x01:
            # DID: real ACE2 silicon reads 0xCD32xxxx; undriven-bus garbage
            # shows as all-0x00, all-0xFF, or a 0xFF top byte. Deliberately
            # NOT flagging other shapes — non-Apple silicon (ACE1 donors)
            # has its own DID and must not trigger pointless re-reads.
            return (v == 0x00000000 or v == 0xFFFFFFFF
                    or (v & 0xFF000000) == 0xFF000000)
        if offset in self._STRING_REGS:
            # Identity strings (0x2F DeviceInfo, 0x2E BuildInfo, 0xA1
            # RomVersion) are printable ASCII with NUL padding; allow at
            # most one stray byte before calling the read garbled.
            bad = 0
            for b in read.raw_bytes:
                if b == 0x00:
                    continue
                if not (0x20 <= b <= 0x7E):
                    bad += 1
                    if bad > 1:
                        return True
            return False
        return False

    def _read_register_clean(self, address: int, offset: int,
                             length: int = 4) -> Optional[RegisterRead]:
        """Read a register, retrying (with settle delays) if contaminated.

        Consecutive I2C transactions right after a NACKed address (e.g. a
        dead CD3217 on the same bus) can return undriven bytes as 0xFF or
        byte-shifted garbage — e.g. VID 0xFF002804 instead of 0x00002804,
        or a 4CC like '0x04'/'I0x04'. Re-reading after a short delay gives
        the bus time to settle and returns clean data.
        """
        read = self.read_register(address, offset, length)
        # Hard failures (NACK/exception -> None) are retried too: an
        # identity register that never answers is exactly the "missing
        # VID/DID" data hole the export validator kept flagging.
        none_retries = 0
        while read is None and offset in self._RECHECK_REGS \
                and none_retries < self.REG_RETRIES:
            none_retries += 1
            if debuglog.is_enabled():
                debuglog.log("REG 0x%02X@0x%02X read failed — retry "
                             "%d/%d after %.2fs settle", address, offset,
                             none_retries, self.REG_RETRIES,
                             self.REG_FAIL_RETRY_DELAY)
            time.sleep(self.REG_FAIL_RETRY_DELAY)
            read = self.read_register(address, offset, length)
        if read is None or offset not in self._RECHECK_REGS:
            return read
        if not self._register_suspicious(offset, read):
            return read
        self.bus_stats.contaminated_rereads += 1
        if debuglog.is_enabled():
            debuglog.log("REG 0x%02X@0x%02X garbled (%s) — re-reading",
                         address, offset, read.raw_bytes[:8].hex(" "))
        for _ in range(self.REG_RETRIES):
            time.sleep(self.REG_RETRY_DELAY)
            retry = self.read_register(address, offset, length)
            if retry is not None and not self._register_suspicious(offset, retry):
                return retry
            if retry is not None:
                read = retry
        return read

    # Truncation repair: a chip that is slow / partially powered delivers
    # the first 1-2 bytes of a read then floats SDA — the master clocks in
    # 0xFF for the rest. Merged reads recover most of it; the rest needs
    # HALF CLOCK (50 kHz doubles the chip's per-byte fetch time).
    CHUNK_FAIL_DELAY = 0.12
    SLOW_CLOCK_HZ = 50_000
    CATASTROPHIC_NACK_RATE = 0.30   # >30% failed transactions
    CONSECUTIVE_FAIL_LIMIT = 6      # drop to half clock after this many
    truncation_seen = False

    @staticmethod
    def _tail_ff_fraction(raw_bytes: bytes) -> float:
        """Fraction of TRAILING bytes equal to 0xFF — the truncation
        signature (valid data prefix, then the chip floats the bus)."""
        n = 0
        for b in reversed(raw_bytes):
            if b == 0xFF:
                n += 1
            else:
                break
        return n / len(raw_bytes) if raw_bytes else 0.0

    def _read_register_merged(self, address: int, offset: int,
                              length: int,
                              attempts: int = 4) -> Optional[RegisterRead]:
        """Defeat truncation by MERGING repeated full-length reads.

        The chip's I2C protocol prefixes every register response with a
        length byte (0x04 for 4CC registers, 0x40 for DeviceInfo, ...) —
        so byte-wise sub-reads are meaningless (each one restarts at the
        prefix). When a slow/partially-powered chip truncates, it floats
        the bus mid-response (0xFF tail), but DIFFERENT attempts truncate
        at different points. Merging N spaced attempts byte-wise — taking
        the first non-0xFF byte at each position — assembles the complete
        response.
        """
        merged = bytearray([0xFF]) * 0  # start empty
        merged = bytearray(b"\xFF" * length)
        for attempt in range(max(1, attempts)):
            try:
                read = self.read_register(address, offset, length)
            except Exception:
                read = None
            if read is None:
                time.sleep(self.REG_FAIL_RETRY_DELAY)
                continue
            for i, b in enumerate(read.raw_bytes[:length]):
                if merged[i] == 0xFF and b != 0xFF:
                    merged[i] = b
            if 0xFF not in merged:
                break
            time.sleep(self.REG_FAIL_RETRY_DELAY)
        raw_int = int.from_bytes(bytes(merged), "little")
        decoded = ""
        if offset == 0x00:
            decoded = decode_vid(raw_int)
        elif offset == 0x03:
            decoded = decode_mode_reg(raw_int)
        elif offset == 0x04:
            decoded = decode_type_reg(raw_int)
        reg_def = REGISTERS.get(offset)
        return RegisterRead(
            offset=offset,
            name=reg_def.name if reg_def else f"0x{offset:02X}",
            raw_bytes=bytes(merged),
            raw_value=raw_int,
            decoded=decoded,
        )

    def _read_register_merged_slow(self, address: int, offset: int,
                                   length: int) -> Optional[RegisterRead]:
        """Merged re-read at HALF CLOCK: a truncating chip cannot fetch
        bytes fast enough at 100 kHz; 50 kHz doubles its per-byte time.
        Clock is always restored."""
        has_clock = hasattr(self.adapter, "set_i2c_clock")
        if has_clock:
            try:
                self.adapter.set_i2c_clock(self.SLOW_CLOCK_HZ)
                if debuglog.is_enabled():
                    debuglog.log("REG 0x%02X@0x%02X merged re-read at %d Hz",
                                 address, offset, self.SLOW_CLOCK_HZ)
            except Exception:
                has_clock = False
        try:
            return self._read_register_merged(address, offset, length,
                                              attempts=5)
        finally:
            if has_clock:
                try:
                    self.adapter.set_i2c_clock(100_000)
                except Exception:
                    pass

    def read_all_registers(self, address: int) -> Dict[int, RegisterRead]:
        """Read all important registers from a device."""
        results = {}
        first = True
        consecutive_fails = 0
        slow_clock = False
        try:
          for offset in self.DETAIL_REGS:
            if not first:
                # Back-to-back register bursts on a probed bus are the worst
                # case for timing margin (TI SLVA689) — and the ACE2's port-1
                # bus is shared with the live SMC. A tiny spacing between
                # register reads dramatically cuts NACK/garble rates without
                # meaningfully slowing a diagnosis.
                time.sleep(self.REG_SPACING)
            first = False
            reg_def = REGISTERS.get(offset)
            length = reg_def.length if reg_def else 4
            read = self._read_register_clean(address, offset, length)
            # Degraded mode: when transactions keep failing outright, drop
            # to half clock for the REST of the pass — the chip gets double
            # the inter-byte time. Restored after the pass.
            if read is None:
                consecutive_fails += 1
                if (not slow_clock and consecutive_fails
                        >= self.CONSECUTIVE_FAIL_LIMIT
                        and hasattr(self.adapter, "set_i2c_clock")):
                    try:
                        self.adapter.set_i2c_clock(self.SLOW_CLOCK_HZ)
                        slow_clock = True
                        if debuglog.is_enabled():
                            debuglog.log("REG pass 0x%02X: %d consecutive "
                                         "failures — dropping to %d Hz for "
                                         "the rest of the pass",
                                         address, consecutive_fails,
                                         self.SLOW_CLOCK_HZ)
                    except Exception:
                        pass
            else:
                consecutive_fails = 0
            # Truncation repair: when a read ends in a 0xFF tail (chip
            # floated the bus mid-transaction), re-read byte-by-byte and
            # keep whichever snapshot carries more real data.
            if read is not None and length > 4 \
                    and self._tail_ff_fraction(read.raw_bytes) >= 0.4:
                self.truncation_seen = True
                chunked = self._read_register_merged_slow(address, offset,
                                                          length)
                if chunked is not None and chunked.raw_bytes:
                    if self._tail_ff_fraction(chunked.raw_bytes) \
                            < self._tail_ff_fraction(read.raw_bytes):
                        if debuglog.is_enabled():
                            debuglog.log(
                                "REG 0x%02X@0x%02X truncated (%d%% tail "
                                "0xFF) — chunked re-read recovered (%d%%)",
                                address, offset,
                                int(self._tail_ff_fraction(
                                    read.raw_bytes) * 100),
                                int(self._tail_ff_fraction(
                                    chunked.raw_bytes) * 100))
                        read = chunked
            if read:
                results[offset] = read
        finally:
            if slow_clock and hasattr(self.adapter, "set_i2c_clock"):
                try:
                    self.adapter.set_i2c_clock(100_000)
                except Exception:
                    pass
        return results

    def diagnose_device(self, address: int) -> DeviceResult:
        """
        Run full diagnostics on a single ACE2 device at given address.
        Returns detailed DeviceResult.
        """
        result = DeviceResult(address=address, timestamp=datetime.now().isoformat())
        if debuglog.is_enabled():
            debuglog.log("DIAG 0x%02X start", address)
        # S3: snapshot the flakiness counters so we can tell at the end
        # whether THIS diagnosis itself hit recovered flakiness.
        flaky_before = (self.bus_stats.ping_recovered,
                        self.bus_stats.contaminated_rereads)

        # Step 1: Connectivity check (retried: a single NACK right after
        # another device failed does NOT mean the chip is dead)
        t0 = time.time()
        result.responds = self._ping_with_retry(address)
        result.scan_time_ms = (time.time() - t0) * 1000

        if not result.responds:
            result.health = HealthStatus.FAIL
            result.faults.append(FaultType.NO_RESPONSE)
            result.fault_details.append(
                f"Device at 0x{address:02X} does not respond to I2C ping "
                f"({1 + self.PING_RETRIES} attempts)"
            )
            # Let the bus settle so the NEXT device is not affected by the
            # dead address NACK storm.
            time.sleep(self.BUS_SETTLE_AFTER_NACK)
            return result

        # Step 2: Read all registers
        result.registers = self.read_all_registers(address)

        # Step 2b: Corruption re-pass. On a marginal probed bus, a burst of
        # register reads can come back largely garbled (all-0x00/all-0xFF);
        # a single-shot verdict on that snapshot wrongly FAILs a healthy
        # chip. If more than a few registers look corrupt, re-read the full
        # set once after a short settle and keep the better snapshot —
        # genuine chip faults garble consistently, bus noise does not.
        def _suspicious_count(regs: Dict[int, RegisterRead]) -> int:
            n = 0
            for off, rd in regs.items():
                if rd.raw_value == 0xFFFFFFFF:
                    n += 1
                elif rd.raw_value == 0x00000000 and off not in (0x06, 0x14, 0x15):
                    n += 1
            return n

        corrupt = _suspicious_count(result.registers)
        if corrupt > 3:
            if debuglog.is_enabled():
                debuglog.log("DIAG 0x%02X %d/%d registers corrupt — full "
                             "re-read pass", address, corrupt,
                             len(result.registers))
            time.sleep(self.REG_RETRY_DELAY * 3)
            re_read = self.read_all_registers(address)
            if _suspicious_count(re_read) < corrupt:
                if debuglog.is_enabled():
                    debuglog.log("DIAG 0x%02X re-read clean (%d suspicious) "
                                 "— keeping second snapshot", address,
                                 _suspicious_count(re_read))
                result.registers = re_read

        # Step 3: Validate Vendor ID
        vid_reg = result.registers.get(0x00)
        if vid_reg:
            # The VID is a 16-bit field in the low bytes of the 32-bit register.
            # Some read paths return the unused high bytes as 0xFF (e.g.
            # 0xFF002804 for Apple 0x2804), so mask to 16 bits before comparing.
            vid = vid_reg.raw_value & 0xFFFF
            result.vendor_id = vid_reg.decoded or f"0x{vid:04X}"
            # VID 0x0451 = stock TI silicon (vanilla); 0x2804 = Apple-programmed
            result.is_vanilla = vid == 0x0451
            if vid not in VALID_ACE2_VIDS:
                result.faults.append(FaultType.WRONG_VID)
                result.fault_details.append(
                    f"Vendor ID is 0x{vid_reg.raw_value:08X} (expected one of "
                    f"{', '.join(f'0x{v:04X}' for v in sorted(VALID_ACE2_VIDS))})."
                    " A real CD3217-family chip always reports one of those — "
                    "an unexpected VID is nearly always a garbled read on a "
                    "marginal probe, and the Diagnose-All ladder will retry it"
                )
        else:
            result.faults.append(FaultType.I2C_ERROR)
            result.fault_details.append("Could not read Vendor ID register")

        # Step 4: Validate Device ID
        did_reg = result.registers.get(0x01)
        if did_reg and decode_silicon(did_reg.raw_value) == "" \
                and did_reg.raw_value not in (0x00000000, 0xFFFFFFFF):
            # Truncation signature: the 4-byte DID read lost its tail
            # (e.g. 0xFF321704). Re-read byte-by-byte and re-derive.
            if debuglog.is_enabled():
                debuglog.log("DIAG 0x%02X DID 0x%08X does not decode — "
                             "chunked re-read", address, did_reg.raw_value)
            fixed = self._read_register_merged_slow(address, 0x01, 4)
            if fixed is not None and decode_silicon(fixed.raw_value):
                result.registers[0x01] = fixed
                did_reg = fixed
        if did_reg:
            result.did_raw = did_reg.raw_value
            result.device_id = did_reg.decoded or f"0x{did_reg.raw_value:08X}"
            result.silicon = decode_silicon(did_reg.raw_value) \
                or decode_silicon_from_str(result.device_id)

        # Step 4b: Parse the DeviceInfo identity string (register 0x2F).
        # Format (verified): "CD3217   HW0022 FW002.170.00 ZACE2-J316P01P".
        # The trailing ZACEx-xxxx tag is the firmware variant / role build —
        # key signal when matching donors to sockets (roles differ).
        di_reg = result.registers.get(0x2F)
        if di_reg and di_reg.raw_bytes:
            ident = parse_device_info(di_reg.raw_bytes)
            if ident.silicon:
                result.device_info = ident.raw
                result.hw_version = ident.hw
                result.fw_version = ident.fw
                result.fw_variant = ident.variant

        # Step 5: Check Mode
        mode_reg = result.registers.get(0x03)
        if mode_reg:
            result.mode = mode_reg.decoded
            result.mode_raw = mode_reg.raw_value
            mode_str = mode_reg.decoded.upper()
            if mode_str in ("EMPTY", "UNKNOWN/ZERO") or "0X" in mode_str:
                # No printable 4CC survived decoding: the register read is
                # still garbage after retries (bus contention). This is an
                # I2C-level symptom, NOT a chip mode fault — faulting the
                # chip here produced false WRONG_MODE failures.
                result.fault_details.append(
                    "Mode register unreadable (bus noise after NACK) — "
                    "re-run diagnosis on this device alone"
                )
            # Known-good operating modes: APP (application), PPA (power-path
            # active), PPS (programmable power supply), and their combinations.
            elif any(k in mode_str for k in ("APP", "PPA", "PPS", "PSU")):
                pass  # Normal operating mode
            elif "BOOT" in mode_str:
                # BOOT is NORMAL during power-on (boot-in-progress — TI
                # SLVAE21A boot flow: the chip runs boot code, then loads
                # the patch bundle from its SPI ROM before reaching APP).
                # Only a PERSISTENT BOOT is a fault. Re-read after a settle
                # to separate "still booting" from "boot stuck".
                time.sleep(self.BOOT_SETTLE_DELAY)
                boot_recheck = self._read_register_clean(address, 0x03, 4)
                if boot_recheck and "BOOT" not in \
                        (boot_recheck.decoded or "").upper():
                    result.fault_details.append(
                        f"Mode was BOOT at first read; re-check shows "
                        f"{boot_recheck.decoded} — healthy boot transition, "
                        "not a fault"
                    )
                    result.mode = boot_recheck.decoded
                    result.mode_raw = boot_recheck.raw_value
                else:
                    result.faults.append(FaultType.BOOT_FAILED)
                    result.fault_details.append(
                        "Device still in BOOT mode after re-check — firmware "
                        "did not load. Check the controller's SPI flash ROM "
                        "path (each port PAIR shares its own ROM) and "
                        "VIN_3V3, then re-ball/replace the chip. Note: a "
                        "chip in BOOT mode may answer at a loader default "
                        "address instead of its strapped one — if another "
                        "socket shows MISSING, this may be that chip, not "
                        "the socket's own part"
                    )
            elif "PTCH" in mode_str:
                result.fault_details.append(
                    "Device in PATCH mode - waiting for firmware download"
                )
            else:
                result.faults.append(FaultType.WRONG_MODE)
                result.fault_details.append(
                    f"Unexpected mode: {mode_reg.decoded}"
                )

        # Step 6: Check Type
        type_reg = result.registers.get(0x04)
        if type_reg:
            result.device_type = type_reg.decoded
            if "I2C" not in type_reg.decoded.upper():
                result.fault_details.append(
                    f"Unexpected device type: {type_reg.decoded}"
                )

        # Step 7: Check for register corruption
        corruption_count = 0
        for offset, read in result.registers.items():
            if read.raw_value == 0xFFFFFFFF:
                corruption_count += 1
            elif read.raw_value == 0x00000000 and offset not in (0x06, 0x14, 0x15):
                corruption_count += 1

        if corruption_count > 3:
            if debuglog.is_enabled():
                debuglog.log("DIAG 0x%02X CORRUPTED_REGISTERS (%d) — still "
                             "garbled after re-read: chip fault or hard bus "
                             "problem", address, corruption_count)
            result.faults.append(FaultType.CORRUPTED_REGISTERS)
            result.fault_details.append(
                f"{corruption_count} registers returned suspicious values "
                "(all 0x00 or all 0xFF)"
            )

        # Step 8: Calculate health score
        result.health_score = self._calculate_health_score(result)

        # Step 9: Determine overall health
        if not result.faults:
            result.health = HealthStatus.PASS
        elif any(f in (FaultType.NO_RESPONSE, FaultType.I2C_ERROR,
                       FaultType.WRONG_VID) for f in result.faults):
            result.health = HealthStatus.FAIL
        else:
            result.health = HealthStatus.WARN

        flaky_now = (self.bus_stats.ping_recovered,
                     self.bus_stats.contaminated_rereads)
        if flaky_now != flaky_before:
            # S3: this diagnosis hit recovered flakiness — let the bus
            # settle before the next device starts reading.
            time.sleep(self.ADAPTIVE_SETTLE)
            if debuglog.is_enabled():
                debuglog.log("DIAG 0x%02X flaky (recovered ping=%d, garbled "
                             "re-read=%d) — adaptive settle %.2fs",
                             address, flaky_now[0] - flaky_before[0],
                             flaky_now[1] - flaky_before[1],
                             self.ADAPTIVE_SETTLE)

        if debuglog.is_enabled():
            debuglog.log("DIAG 0x%02X done: %s score=%d faults=%s %.0fms",
                         address, result.health.value, result.health_score,
                         [f.value for f in result.faults],
                         result.scan_time_ms)
        return result

    # ── Socket expectations (chip-placement validation) ────────────────
    # Verified board data (models.py) says what belongs in each socket:
    # donor-chip class (OTP-ed Apple vs vanilla TI) and silicon family
    # (CD3217B12 vs CD3218B12). VID/DID reveal what is actually installed.

    @staticmethod
    def parse_silicon(device_id: Optional[str],
                      did_raw: Optional[int] = None) -> str:
        """Extract the silicon family from a Device ID.

        Prefers the raw numeric DID register (did_raw): Apple ACE2 parts spell
        the family in the DID value itself (0xCD321804 -> "CD3218"). Falls
        back to scanning a device_id display string for the family markers.
        Returns '' when the DID doesn't carry a recognizable family.
        """
        if did_raw is not None:
            fam = decode_silicon(did_raw)
            if fam:
                return fam
        return decode_silicon_from_str(device_id or "")

    def apply_socket_expectations(self, result: DeviceResult, position) -> None:
        """Validate the diagnosed chip against the board's socket data.

        `position` is a cd3217_analyzer.models.CD3217Position with verified
        chip_class ('otp' / 'vanilla') and silicon fields. Appends
        CHIP_MISMATCH faults when the installed chip is the wrong class
        (e.g. a vanilla TI part in an OTP socket) or a genuinely wrong
        silicon GENERATION (e.g. an ACE1 CD3215 in an ACE2 socket). The
        CD3217B12/CD3218B12 part-number distinction is informational only —
        they share the same Burnside-bridge core and a retail CD3217-marked
        board reports the CD3218 die, so it must never fault a working chip.
        """
        if not result.responds or position is None:
            return

        vid_reg = result.registers.get(0x00)
        vid = (vid_reg.raw_value & 0xFFFF) if vid_reg else None
        # VID 0x0451 = stock TI silicon (vanilla); 0x2804 = Apple-programmed
        if vid is not None:
            result.is_vanilla = vid == 0x0451

        installed_silicon = self.parse_silicon(result.device_id,
                                               result.did_raw)

        if getattr(position, "chip_class", "") == "otp":
            if vid is not None and vid == 0x0451:
                result.faults.append(FaultType.CHIP_MISMATCH)
                result.fault_details.append(
                    f"Vanilla TI chip (VID 0x0451) in OTP socket {position.ref} "
                    f"@0x{position.address:02X} — this socket needs an OTP-ed "
                    "Apple CD3217 donor; a vanilla chip will not take the "
                    "board-specific address/config"
                )
            elif vid is not None and vid == 0x2804:
                result.fault_details.append(
                    f"{position.ref}: Apple OTP-ed chip (VID 0x2804) as expected"
                )
        elif getattr(position, "chip_class", "") == "vanilla":
            if vid is not None and vid == 0x0451:
                result.fault_details.append(
                    f"{position.ref}: vanilla TI chip in vanilla socket "
                    "(OK — strap supplies the address)"
                )
            elif vid is not None and vid == 0x2804:
                result.fault_details.append(
                    f"{position.ref}: Apple OTP-ed chip in vanilla socket "
                    "(works only if its burned address matches this socket)"
                )

        expected_silicon = getattr(position, "silicon", "") or ""
        if expected_silicon and installed_silicon:
            want = expected_silicon[:6]  # 'CD3217B12' -> 'CD3217'
            if want != installed_silicon:
                # ACE2 family (CD3217B12 / CD3217B13 / CD3218B12) shares ONE
                # "Burnside bridge" silicon core (repair.wiki, ASCII strings
                # 'CD3217'/'CD3218' both appear in the same firmware). The DID
                # family does NOT reliably distinguish these part numbers — a
                # retail CD3217-marked board (e.g. A2251) reports the CD3218
                # Burnside die, so treating CD3217<->CD3218 as a mismatch false-
                # faults every healthy chip. Only a genuinely different
                # GENERATION (ACE1 = CD3215, or an unknown part where we cannot
                # tell) is a hard mismatch.
                same_ace2 = {"CD3217", "CD3218", "CD3215"}.issuperset(
                    {want, installed_silicon}) and want.startswith("CD32") \
                    and installed_silicon.startswith("CD32")
                if want == "CD3215" or installed_silicon == "CD3215" \
                        or not same_ace2:
                    # Definitely across generations (ACE1 vs ACE2) — real fault.
                    result.faults.append(FaultType.CHIP_MISMATCH)
                    result.fault_details.append(
                        f"{position.ref}: wrong silicon generation — board "
                        f"expects {expected_silicon}, installed chip reports "
                        f"{installed_silicon} (DID {result.device_id})"
                    )
                else:
                    # Same ACE2 Burnside core; part-number ambiguity. This is a
                    # donor-revision note, NOT a board fault — a working board
                    # (correct VID, correct class) must not be flagged.
                    result.fault_details.append(
                        f"{position.ref}: silicon part number noted "
                        f"{installed_silicon} vs expected {expected_silicon} "
                        f"(same ACE2 Burnside core; donor-revision only — "
                        f"not a fault)"
                    )

        if FaultType.CHIP_MISMATCH in result.faults:
            if result.health == HealthStatus.PASS:
                result.health = HealthStatus.WARN

    def _calculate_health_score(self, result: DeviceResult) -> int:
        """Calculate a 0-100 health score for a device."""
        score = 0

        if not result.responds:
            return 0

        # Connectivity (30 points)
        score += 30

        # Vendor ID correct (15 points)
        vid_reg = result.registers.get(0x00)
        if vid_reg and (vid_reg.raw_value & 0xFFFF) in VALID_ACE2_VIDS:
            score += 15

        # Mode is APP / PPA (20 points)
        if result.mode and any(k in result.mode.upper()
                               for k in ("APP", "PPA", "PPS", "PSU")):
            score += 20
        elif result.mode and "BOOT" in result.mode.upper():
            score += 5  # Some credit for being alive

        # Type is I2C (10 points)
        if result.device_type and "I2C" in result.device_type.upper():
            score += 10

        # Device ID readable (10 points)
        did_reg = result.registers.get(0x01)
        if did_reg and did_reg.raw_value != 0 and did_reg.raw_value != 0xFFFFFFFF:
            score += 10

        # No register corruption (15 points)
        # Match the CORRUPTED_REGISTERS exemptions: 0x06/0x14/0x15 and other
        # status registers legitimately read 0x00 or 0xFF on a healthy idle
        # chip, so they must not count as corruption.
        corruption = sum(
            1 for off, r in result.registers.items()
            if r.raw_value in (0xFFFFFFFF, 0x00000000)
            and not (r.raw_value == 0x00000000 and off in (0x06, 0x14, 0x15))
        )
        if corruption == 0:
            score += 15
        elif corruption < 3:
            score += 10

        return min(score, 100)

    def full_diagnostic(self) -> DiagnosticReport:
        """
        Run full diagnostic scan of the I2C bus and all ACE2 devices.
        Returns a comprehensive DiagnosticReport.
        """
        report = DiagnosticReport(
            timestamp=datetime.now().isoformat(),
            adapter_type=type(self.adapter).__name__,
        )

        # Step 1: Full bus scan
        report.bus_scan_results = self.scan_bus()

        # Step 2: Find ACE2 devices
        ace2_addrs = [a for a in report.bus_scan_results if is_ace2_address(a)]
        other_addrs = [a for a in report.bus_scan_results if not is_ace2_address(a)]

        # Step 3: Diagnose each ACE2
        for addr in ace2_addrs:
            result = self.diagnose_device(addr)
            report.devices.append(result)

        # Step 4: Also check any unexpected addresses that might be ACE2s
        for addr in other_addrs:
            # Try reading VID to see if it's a TI/Apple ACE2 device
            vid_read = self.read_register(addr, 0x00, 4)
            if vid_read and vid_read.raw_value in VALID_ACE2_VIDS:
                result = self.diagnose_device(addr)
                result.notes = "Found ACE2 device at non-standard address"
                report.devices.append(result)

        # Step 5: Generate summary
        report.summary = self._generate_summary(report)

        return report

    def _generate_summary(self, report: DiagnosticReport) -> str:
        """Generate human-readable summary of diagnostic results."""
        lines = []
        lines.append(f"Scan Time: {report.timestamp}")
        lines.append(f"Adapter: {report.adapter_type}")
        lines.append(f"I2C Devices Found: {len(report.bus_scan_results)}")
        lines.append(f"ACE2 Devices Found: {len(report.devices)}")
        lines.append("")

        if not report.devices:
            lines.append("NO ACE2 DEVICES DETECTED")
            lines.append("Possible causes:")
            lines.append("  - Device not powered (need VIN_3V3 or VBUS)")
            lines.append("  - I2C pullups missing or wrong value")
            lines.append("  - SDA/SCL swapped")
            lines.append("  - Device physically damaged")
            return "\n".join(lines)

        for dev in report.devices:
            lines.append(f"--- Device at 0x{dev.address:02X} ---")
            lines.append(f"  Health: {dev.health.value} (score: {dev.health_score}/100)")
            lines.append(f"  Responds: {'Yes' if dev.responds else 'No'}")
            lines.append(f"  VID: {dev.vendor_id or 'N/A'}")
            lines.append(f"  DID: {dev.device_id or 'N/A'}")
            lines.append(f"  Mode: {dev.mode or 'N/A'}")
            lines.append(f"  Type: {dev.device_type or 'N/A'}")

            if dev.faults:
                lines.append(f"  FAULTS ({len(dev.faults)}):")
                for fault in dev.faults:
                    lines.append(f"    - {fault.value}")
            if dev.fault_details:
                lines.append("  DETAILS:")
                for detail in dev.fault_details:
                    lines.append(f"    - {detail}")
            if dev.notes:
                lines.append(f"  Notes: {dev.notes}")
            lines.append("")

        return "\n".join(lines)

    def print_report(self, report: DiagnosticReport) -> None:
        """Print formatted diagnostic report to stdout."""
        print("=" * 70)
        print("  CD3217B12 (Apple ACE2) I2C Diagnostic Report")
        print("=" * 70)
        print(report.summary)

        if report.bus_scan_results:
            print("All I2C devices on bus:")
            for addr in report.bus_scan_results:
                marker = " <-- ACE2" if is_ace2_address(addr) else ""
                desc = KNOWN_ACE2_ADDRESSES.get(addr, "")
                if desc:
                    marker += f" ({desc})"
                print(f"  0x{addr:02X}{marker}")

        print("=" * 70)

    def bus_health_summary(self) -> str:
        """Human-readable bus-integrity line for the current session.

        Distinguishes recovered flakiness (bus margin — probe loading,
        pull-ups, leads) from hard failures (dead chips, already reported
        per-chip), so a scan that poked a dead chip is not mistaken for a
        bad probe and vice versa.
        """
        s = self.bus_stats
        if s.pings + s.reads == 0:
            return "Bus statistics: no transactions recorded."
        nack_n = s.ping_failures + s.read_failures
        rate = s.nack_rate * 100
        msg = (f"Bus statistics: {s.pings} pings, {s.reads} register reads, "
               f"{nack_n} NACK/error attempts ({rate:.1f}% failed), "
               f"{s.contaminated_rereads} garbled-read rechecks.")
        total_tx = s.pings + s.reads
        if total_tx >= 20 and s.nack_rate >= self.CATASTROPHIC_NACK_RATE:
            # Beyond probe-margin noise: at this failure rate the two
            # independent datasets won't agree and verdicts are
            # provisional. Escalate with the concrete physical checklist.
            msg += (f"\n  SEVERE: {s.nack_rate * 100:.0f}% of transactions "
                    f"failed ({nack_n}/{total_tx}) — far beyond probe-"
                    "margin noise. Check, in order: (1) probe GROUND is "
                    "actually connected to the MacBook's ground (no "
                    "common reference = exactly this failure pattern), "
                    "(2) no probe wire has detached or is touching the "
                    "wrong pad, (3) the board is fully powered (charger "
                    "connected). Verdicts from this session are "
                    "provisional until one of these is corrected.")
            return msg
        if s.marginal:
            if s.ping_recovered and s.contaminated_rereads:
                what = (f"{s.ping_recovered} address(es) answered only after "
                        f"retries and {s.contaminated_rereads} garbled "
                        "read(s) recovered on re-check")
            elif s.contaminated_rereads:
                what = (f"{s.contaminated_rereads} garbled read(s) recovered "
                        "on re-check")
            else:
                what = (f"{s.ping_recovered} address(es) answered only after "
                        "retries")
            msg += (f"\n  WARN: {what} — the bus is marginal. This is a "
                    "probe/cable/pull-up margin issue (TI SLVA689: added "
                    "probe capacitance eats rise-time margin). Re-check "
                    "SDA/SCL probe contact, lead length, and 3.3V pull-ups "
                    "before trusting per-chip verdicts.")
        elif nack_n:
            msg += ("\n  Failures were hard (never recovered) — consistent "
                    "with dead/absent chips, which are faulted per-device "
                    "above; not by itself a probe problem.")
        else:
            msg += "\n  Bus clean: no NACKs or garbled reads recorded."
        return msg

    # Identity registers used for the bus-speed stress comparison.
    STRESS_IDENTITY_REGS = (0x00, 0x01, 0x03, 0x04)

    def _stress_snapshot(self, address: int) -> Dict[int, bool]:
        """One pass of clean/garbled verdicts over the identity registers."""
        snaps = {}
        for reg in self.STRESS_IDENTITY_REGS:
            read = self._read_register_clean(address, reg, 4)
            snaps[reg] = (read is not None
                          and not self._register_suspicious(reg, read))
        return snaps

    def stress_test_margin(self, address: int, iterations: int = 3) -> dict:
        """S1 bus-speed stress probe: identity reads at 100 kHz vs 400 kHz.

        A chip that reads clean at BOTH speeds has ample timing margin
        (headroom within the TI TPS6598x 400 kHz / 400 pF bus spec). One
        that garbles at 400 kHz while staying clean at 100 kHz has a
        marginal bus — probe capacitance eats rise-time margin (TI
        SLVA689): shorten the probe leads, add ~100 Ω series resistors,
        verify the pull-ups.

        The clock is ALWAYS restored to 100 kHz (finally), and the baseline
        must be clean before the stress phase — a garbled 100 kHz baseline
        is a bus/chip problem, not a margin signal.

        Returns {"supported": bool, "verdict": str, "detail": str}.
        Verdicts: ample-margin | marginal | bus-problem | no-response |
        unavailable (FTDI/CH341/SMBus adapters can't change clock).
        """
        if not hasattr(self.adapter, "set_i2c_clock"):
            return {
                "supported": False, "verdict": "unavailable",
                "detail": ("needs the analyzer board (USB bridge); FTDI/"
                           "CH341/SMBus adapters cannot change the I2C "
                           "clock"),
            }
        if not self._ping_with_retry(address):
            return {
                "supported": True, "verdict": "no-response",
                "detail": "chip does not respond — stress test not "
                          "applicable; check power (VIN_3V3/VBUS) first",
            }
        base = self._stress_snapshot(address)
        if not all(base.values()):
            return {
                "supported": True, "verdict": "bus-problem",
                "detail": ("identity reads already fail/garble at 100 kHz — "
                           "fix the bus or chip first (see per-chip faults); "
                           "no clock change was performed"),
            }
        fast_clean = True
        garbled: List[int] = []
        try:
            self.adapter.set_i2c_clock(400_000)
            for _ in range(max(1, iterations)):
                for reg, ok in self._stress_snapshot(address).items():
                    if not ok:
                        fast_clean = False
                        if reg not in garbled:
                            garbled.append(reg)
        finally:
            try:
                self.adapter.set_i2c_clock(100_000)
            except Exception:
                pass
        if fast_clean:
            return {
                "supported": True, "verdict": "ample-margin",
                "detail": ("identity reads clean at BOTH 100 kHz and "
                           "400 kHz — ample timing margin (headroom within "
                           "the TI 400 kHz / 400 pF bus spec)"),
            }
        return {
            "supported": True, "verdict": "marginal",
            "detail": ("clean at 100 kHz but garbled at 400 kHz ("
                       + ", ".join(f"0x{r:02X}" for r in sorted(garbled))
                       + ") — bus capacitance eats rise-time margin (TI "
                       "SLVA689): shorten probe leads, add ~100 Ω series "
                       "resistors, verify pull-ups"),
        }

    def register_dump(self, address: int) -> str:
        """Dump all readable registers from a device as formatted text."""
        lines = []
        lines.append(f"Register dump for device at 0x{address:02X}")
        lines.append("-" * 60)

        for offset, reg_def in sorted(REGISTERS.items()):
            read = self.read_register(address, offset, reg_def.length)
            if read:
                hex_str = read.raw_bytes.hex()
                lines.append(
                    f"  [{offset:02X}] {reg_def.name:20s} "
                    f"= {hex_str:32s} ({read.decoded or f'0x{read.raw_value:X}'})"
                )
            else:
                lines.append(
                    f"  [{offset:02X}] {reg_def.name:20s} = ** READ ERROR **"
                )

        return "\n".join(lines)

    def identify_chip_type(self, address: int) -> Optional[str]:
        """
        Identify if a chip is vanilla or OTP-ed from its Vendor ID.

        VID 0x0451 = stock TI silicon (vanilla — strap-configured address),
        VID 0x2804 = Apple OTP-ed part. This replaces the old address-based
        heuristic, which mislabeled strap addresses (0x74/0x76/0x78 are the
        8-bit write forms of the OTP-socket 7-bit addresses 0x3A/0x3B/0x3C,
        while e.g. A2337's strap vanilla chips answer at 0x38/0x3F).

        Returns None when the chip doesn't respond or the VID is unreadable.
        """
        if not self.adapter.ping(address):
            return None
        try:
            read = self.read_register(address, 0x00, 4)
        except Exception:
            return None
        if read is None:
            return None
        vid = read.raw_value & 0xFFFF
        if vid == 0x0451:
            return "Vanilla (VID 0x0451 — stock TI, strap-configured address)"
        if vid == 0x2804:
            return "OTP-ed (VID 0x2804 — Apple-programmed)"
        return f"Unknown VID 0x{vid:04X}"

    # Verdict ranking for keep-best logic across retry passes.
    _HEALTH_RANK = {HealthStatus.PASS: 3, HealthStatus.WARN: 2,
                    HealthStatus.FAIL: 1, HealthStatus.UNKNOWN: 0}

    @staticmethod
    def is_retryable_failure(result: Optional["DeviceResult"]) -> bool:
        """True when a batch pass should re-diagnose a chip after a settle.

        Only TRANSPORT failures are worth retrying: a chip that NACKed or
        returned I2C errors right after the previous chip's read burst very
        often answers fine seconds later (probe capacitance + bus settle;
        the user's manual per-chip clicks prove it). Genuine verdicts —
        WRONG_VID, CHIP_MISMATCH, NO_RESPONSE-only-with-a-real-fault — are
        NOT retried away.
        """
        if result is None:
            return True
        if result.health == HealthStatus.FAIL:
            transport = {FaultType.NO_RESPONSE, FaultType.I2C_ERROR,
                         FaultType.CORRUPTED_REGISTERS,
                         FaultType.WRONG_VID}
            return any(f in transport for f in result.faults)
        return False

    def quick_health_check(self, address: int) -> Tuple[HealthStatus, int, str]:
        """
        Quick health check returning (status, score, message).
        Faster than full diagnosis for batch testing.
        """
        result = self.diagnose_device(address)
        msg = "; ".join(result.fault_details) if result.fault_details else "All checks passed"
        return result.health, result.health_score, msg
