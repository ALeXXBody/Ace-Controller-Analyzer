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
    decode_type_reg,
    decode_vid,
    is_ace2_address,
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

    def __init__(self, adapter: I2CAdapter, addresses: Optional[List[int]] = None):
        self.adapter = adapter
        self.addresses = addresses or list(KNOWN_ACE2_ADDRESSES.keys())

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
            return None

    def _ping_with_retry(self, address: int) -> bool:
        """Ping an address, retrying with a settle delay.

        On a board with a dead CD3217, transactions against the dead address
        leave the bus/bridge in a bad state for a short moment; the very
        next ping can NACK even on a healthy chip. Retrying after a short
        delay distinguishes 'flaky right after a NACK' from 'really dead'.
        """
        for attempt in range(1 + self.PING_RETRIES):
            if self.adapter.ping(address):
                return True
            if attempt < self.PING_RETRIES:
                time.sleep(self.PING_RETRY_DELAY)
        return False

    # Registers whose format is known well enough to detect a contaminated
    # read (undriven bytes read back as 0xFF right after bus contention or
    # a NACKed dead address).
    _IDENTITY_REGS = {0x00, 0x03, 0x04}

    @staticmethod
    def _register_suspicious(offset: int, read: RegisterRead) -> bool:
        """True when an identity-register read shows undriven-byte garbage."""
        v = read.raw_value
        if v == 0xFFFFFFFF:
            return True
        if offset == 0x00:
            # VID is a 16-bit field; any high-byte content is garbage.
            return (v & 0xFFFF0000) != 0
        if offset in (0x03, 0x04):
            # 4CC registers: every byte must be printable ASCII (or 0x00 pad).
            for i in range(4):
                b = (v >> (i * 8)) & 0xFF
                if b != 0x00 and not (0x20 <= b <= 0x7E):
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
        if read is None or offset not in self._IDENTITY_REGS:
            return read
        if not self._register_suspicious(offset, read):
            return read
        for _ in range(self.REG_RETRIES):
            time.sleep(self.REG_RETRY_DELAY)
            retry = self.read_register(address, offset, length)
            if retry is not None and not self._register_suspicious(offset, retry):
                return retry
            if retry is not None:
                read = retry
        return read

    def read_all_registers(self, address: int) -> Dict[int, RegisterRead]:
        """Read all important registers from a device."""
        results = {}
        for offset in self.DETAIL_REGS:
            reg_def = REGISTERS.get(offset)
            if reg_def:
                read = self._read_register_clean(address, offset, reg_def.length)
            else:
                read = self._read_register_clean(address, offset, 4)
            if read:
                results[offset] = read
        return results

    def diagnose_device(self, address: int) -> DeviceResult:
        """
        Run full diagnostics on a single ACE2 device at given address.
        Returns detailed DeviceResult.
        """
        result = DeviceResult(address=address, timestamp=datetime.now().isoformat())

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

        # Step 3: Validate Vendor ID
        vid_reg = result.registers.get(0x00)
        if vid_reg:
            # The VID is a 16-bit field in the low bytes of the 32-bit register.
            # Some read paths return the unused high bytes as 0xFF (e.g.
            # 0xFF002804 for Apple 0x2804), so mask to 16 bits before comparing.
            vid = vid_reg.raw_value & 0xFFFF
            result.vendor_id = vid_reg.decoded or f"0x{vid:04X}"
            if vid not in VALID_ACE2_VIDS:
                result.faults.append(FaultType.WRONG_VID)
                result.fault_details.append(
                    f"Vendor ID is 0x{vid_reg.raw_value:08X} (expected one of "
                    f"{', '.join(f'0x{v:04X}' for v in sorted(VALID_ACE2_VIDS))})"
                )
        else:
            result.faults.append(FaultType.I2C_ERROR)
            result.fault_details.append("Could not read Vendor ID register")

        # Step 4: Validate Device ID
        did_reg = result.registers.get(0x01)
        if did_reg:
            result.device_id = did_reg.decoded or f"0x{did_reg.raw_value:08X}"

        # Step 5: Check Mode
        mode_reg = result.registers.get(0x03)
        if mode_reg:
            result.mode = mode_reg.decoded
            result.mode_raw = mode_reg.raw_value
            mode_str = mode_reg.decoded.upper()
            if "0X" in mode_str:
                # Hex-escaped bytes survived decoding: the register read is
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
                result.faults.append(FaultType.BOOT_FAILED)
                result.fault_details.append(
                    "Device stuck in BOOT mode - may need ROM or VIN_3V3"
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

        return result

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
        corruption = sum(1 for r in result.registers.values()
                         if r.raw_value in (0xFFFFFFFF, 0x00000000))
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
        Attempt to identify if a chip is vanilla or OTP-ed.

        This is a heuristic based on:
        - OTP-ed chips typically have addresses like 0x3A, 0x3B, 0x3C
        - Vanilla chips can have any address based on strap resistors
        - Some OTP addresses are used exclusively by Apple
        """
        if not self.adapter.ping(address):
            return None

        # Known OTP-only addresses (Apple internal)
        OTP_ADDRESSES = {0x3A, 0x3B, 0x3C, 0x74, 0x76, 0x78, 0x79}

        # Known vanilla-friendly addresses
        VANILLA_ADDRESSES = {0x38, 0x3F, 0x2F, 0x28}

        if address in OTP_ADDRESSES:
            return "OTP-ed (Apple-programmed address)"
        elif address in VANILLA_ADDRESSES:
            return "Likely vanilla (strap-configured address)"
        else:
            return "Unknown type"

    def quick_health_check(self, address: int) -> Tuple[HealthStatus, int, str]:
        """
        Quick health check returning (status, score, message).
        Faster than full diagnosis for batch testing.
        """
        result = self.diagnose_device(address)
        msg = "; ".join(result.fault_details) if result.fault_details else "All checks passed"
        return result.health, result.health_score, msg
