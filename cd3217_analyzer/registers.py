"""CD3217B12 (Apple ACE2) register definitions and known-good values.

Based on:
- TPS65982 datasheet (TI public reference for ACE1/ACE2 architecture)
- TPS65987D Host Interface TRM (register map structure)
- Asahi Linux wiki ACE controller documentation
- Repair.wiki ACE2 controller documentation
- Community reverse-engineering (t8012dev, etc.)

NOTE: CD3217B12 is an Apple-custom TI part with no public datasheet.
      Register addresses and meanings are inferred from TPS65982/TPS65987D
      documentation and community research. Some registers may have
      Apple-specific extensions or differences.
"""

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional


class PortMode(IntEnum):
    """Device operational modes (register 0x03 Mode)."""
    APP = 0          # Application mode - fully functional
    BOOT = 1         # Boot mode - dead battery / bootloader
    PTCH = 2         # Patch mode - accepting firmware patches
    UNKNOWN = 0xFF


class I2CPort(IntEnum):
    """ACE2 I2C port identifiers."""
    PORT1 = 1   # Debug/Thunderbolt (B5=SDA, A4=SCL, D7=IRQ)
    PORT2 = 2   # SMC (B7=SDA, A6=SCL, C8=IRQ)


# Known I2C addresses for CD3217B12 variants
# These are 7-bit addresses. The ACE2 can appear at many addresses
# depending on OTP configuration and strap resistors.
KNOWN_ACE2_ADDRESSES = {
    # Common addresses from repair community / schematics
    0x38: "ACE2 Port1 (vanilla, ADDR=GND)",
    0x3F: "ACE2 Port1 (vanilla, ADDR=float)",
    0x3B: "ACE2 Port1 (OTP typical)",
    0x3A: "ACE2 Port1 (OTP typical)",
    0x3C: "ACE2 Port1 (OTP typical)",
    0x2F: "ACE2 Port2 (vanilla, ADDR=float)",
    0x28: "ACE2 Port2 (vanilla, ADDR=GND)",
    0x2B: "ACE2 Port2 (OTP typical)",
    0x2A: "ACE2 Port2 (OTP typical)",
    0x6B: "ACE2 All-call / Bank",
    # From Asahi Linux wiki / TPS65987D reference
    0x20: "TPS6598x Primary I2C1",
    0x22: "TPS6598x Primary I2C1 (alt)",
    0x3D: "TPS6598x Secondary",
    0x47: "TUSB320 (UFP addr)",
    0x67: "TUSB320 (DFP addr)",
}

# Broader scan range for unknown devices
I2C_SCAN_RANGE = list(range(0x08, 0x78))


@dataclass
class RegisterDef:
    """Definition of a single register."""
    offset: int
    length: int
    name: str
    description: str
    readable: bool = True
    writable: bool = False
    expected_values: Optional[dict] = None  # {value: description}
    expected_range: Optional[tuple] = None  # (min, max) for valid range
    mask: Optional[int] = None              # Bitmask for multi-field regs


# TPS65982/TPS65987D register map (applied to CD3217B12)
# Offsets are 7-bit register addresses used in I2C transactions
REGISTERS = {
    # --- Identification Registers ---
    0x00: RegisterDef(
        offset=0x00, length=4, name="VID",
        description="Vendor ID (TI = 0x0451)",
        expected_values={0x0451: "Texas Instruments"},
    ),
    0x01: RegisterDef(
        offset=0x01, length=4, name="DID",
        description="Device ID (silicon revision specific)",
    ),
    0x02: RegisterDef(
        offset=0x02, length=4, name="ProtoVer",
        description="Protocol Version (Thunderbolt)",
    ),
    0x03: RegisterDef(
        offset=0x03, length=4, name="Mode",
        description="Operational mode (4CC: APP / BOOT / PTCH)",
        expected_values={
            # 4CC codes stored as MSB-first (big-endian)
            0x41505020: "APP - Application (normal)",
            0x424F4F54: "BOOT - Bootloader",
            0x50544348: "PTCH - Patch mode",
        },
    ),
    0x04: RegisterDef(
        offset=0x04, length=4, name="Type",
        description="Device type (should return 'I2C ')",
        expected_values={
            0x49324320: "I2C device",
        },
    ),
    0x05: RegisterDef(
        offset=0x05, length=16, name="UID",
        description="128-bit unique ID (per port)",
    ),
    0x06: RegisterDef(
        offset=0x06, length=8, name="CustomerUse",
        description="Customer-use bytes (initialized by app customization)",
    ),
    0x08: RegisterDef(
        offset=0x08, length=4, name="Cmd1",
        description="Command register 1 (write 4CC command, read status)",
    ),
    0x09: RegisterDef(
        offset=0x09, length=4, name="Data1",
        description="Data register 1 (input/output for Cmd1)",
    ),
    0x10: RegisterDef(
        offset=0x10, length=4, name="Cmd2",
        description="Command register 2",
    ),
    0x11: RegisterDef(
        offset=0x11, length=4, name="Data2",
        description="Data register 2",
    ),
    0x0F: RegisterDef(
        offset=0x0F, length=4, name="Version",
        description="Boot loader / firmware version",
    ),
    # --- Status Registers ---
    0x14: RegisterDef(
        offset=0x14, length=11, name="IntEvent1",
        description="Interrupt event register 1 (Port 1)",
    ),
    0x15: RegisterDef(
        offset=0x15, length=11, name="IntEvent2",
        description="Interrupt event register 2 (Port 2)",
    ),
    0x16: RegisterDef(
        offset=0x16, length=11, name="IntMask1",
        description="Interrupt mask register 1",
    ),
    0x17: RegisterDef(
        offset=0x17, length=11, name="IntMask2",
        description="Interrupt mask register 2",
    ),
    # --- PD Status Registers ---
    0x29: RegisterDef(
        offset=0x29, length=4, name="PowerStatus",
        description="Power status / VBUS state",
    ),
    0x2D: RegisterDef(
        offset=0x2D, length=12, name="BootFlags",
        description="Boot flags and silicon revision",
    ),
    0x2E: RegisterDef(
        offset=0x2E, length=4, name="BuildInfo",
        description="Build identifier (ASCII + date)",
    ),
    0x2F: RegisterDef(
        offset=0x2F, length=47, name="DeviceInfo",
        description="Hardware/firmware version string",
    ),
    # --- PD Capability Registers ---
    0x30: RegisterDef(
        offset=0x30, length=29, name="RxCapabilities",
        description="Latest Source Capabilities received over BMC",
    ),
    0x31: RegisterDef(
        offset=0x31, length=29, name="SinkCapabilities",
        description="Latest Sink Capabilities received over BMC",
    ),
    0x32: RegisterDef(
        offset=0x32, length=64, name="TxSourceCaps",
        description="Outgoing Source Capabilities (PD PDOs)",
    ),
    0x33: RegisterDef(
        offset=0x33, length=57, name="TxSinkCaps",
        description="Outgoing Sink Capabilities",
    ),
    0x34: RegisterDef(
        offset=0x34, length=6, name="ActivePDO",
        description="Current contract PDO",
    ),
    0x35: RegisterDef(
        offset=0x35, length=4, name="ActiveRDO",
        description="Current contract RDO",
    ),
    0x36: RegisterDef(
        offset=0x36, length=4, name="SinkRequestRDO",
        description="Most recent RDO sent by Sink",
    ),
    0x37: RegisterDef(
        offset=0x37, length=20, name="AutoSink",
        description="Auto-sink voltage range config",
    ),
    0x38: RegisterDef(
        offset=0x38, length=16, name="AltMode",
        description="Alternate mode selection and sequence",
    ),
    # --- PD Status/Config Registers ---
    0x40: RegisterDef(
        offset=0x40, length=4, name="PDStatus",
        description="PD status bit field (messages and state)",
    ),
    0x41: RegisterDef(
        offset=0x41, length=4, name="PD3Status",
        description="PD3.0 status bit field",
    ),
    0x43: RegisterDef(
        offset=0x43, length=4, name="PDConfig",
        description="PD configuration settings",
    ),
    0x44: RegisterDef(
        offset=0x44, length=4, name="PD3Config",
        description="PD3.0 configuration settings",
    ),
    # --- GPIO Registers ---
    0x58: RegisterDef(
        offset=0x58, length=4, name="GPIOControl",
        description="GPIO output control",
    ),
    0x59: RegisterDef(
        offset=0x59, length=4, name="GPIOStatus",
        description="GPIO input status",
    ),
    # --- I2C Master Configuration ---
    0x60: RegisterDef(
        offset=0x60, length=1, name="I2CMasterEnable",
        description="I2C master port enable",
    ),
    0x61: RegisterDef(
        offset=0x61, length=4, name="I2CMasterAddr",
        description="I2C master slave address config",
    ),
    # --- Debug/Manufacturer Registers ---
    0xA0: RegisterDef(
        offset=0xA0, length=4, name="ChipRevision",
        description="Silicon chip revision",
    ),
    0xA1: RegisterDef(
        offset=0xA1, length=4, name="RomVersion",
        description="ROM version",
    ),
}


def decode_mode_reg(value: int) -> str:
    """Decode the Mode register (0x03) from raw bytes (big-endian reading)."""
    if value == 0:
        return "Unknown/Zero"
    chars = []
    # Read bytes from MSB to LSB (big-endian order for 4CC codes)
    for i in range(3, -1, -1):
        b = (value >> (i * 8)) & 0xFF
        if 0x20 <= b <= 0x7E:
            chars.append(chr(b))
        elif b == 0:
            continue  # Skip null terminators
        else:
            chars.append(f"0x{b:02X}")
    return "".join(chars) if chars else "Empty"


def decode_type_reg(value: int) -> str:
    """Decode the Type register (0x04) from raw bytes."""
    return decode_mode_reg(value)


def decode_4cc(value: int) -> str:
    """Decode a 4-byte 4CC (Four Character Code) value."""
    return decode_mode_reg(value)


def decode_vid(value: int) -> str:
    """Decode Vendor ID register."""
    known_vids = {
        0x0451: "Texas Instruments",
        0x0483: "STMicroelectronics",
    }
    name = known_vids.get(value, "Unknown")
    return f"0x{value:04X} ({name})"


def decode_did(value: int) -> str:
    """Decode Device ID register."""
    return f"0x{value:08X}"


def is_ace2_address(addr: int) -> bool:
    """Check if an I2C address is in the known ACE2 address range."""
    return addr in KNOWN_ACE2_ADDRESSES


def get_addr_description(addr: int) -> str:
    """Get human-readable description for an ACE2 I2C address."""
    return KNOWN_ACE2_ADDRESSES.get(addr, "Unknown device")


def decode_i2c_address_straps(addr_port1: int, addr_port2: int) -> dict:
    """
    Given Port 1 and Port 2 addresses, decode the strap configuration.

    Port 1 address: 111ADDR (ADDR is 3 bits from resistor value)
    Port 2 address: 1CNTL2CNTL1ADDR (CNTL1/2 from pullup/pulldown)
    """
    # Extract ADDR bits (lower 3 bits of Port 1 address)
    addr_bits = addr_port1 & 0x07

    # Extract CNTL1 and CNTL2 from Port 2 address
    cntl1 = (addr_port2 >> 2) & 0x01
    cntl2 = (addr_port2 >> 3) & 0x01

    # Reverse lookup for ADDR resistor value
    addr_resistors = {
        0b000: "0Ω (GND)",
        0b001: "38.3kΩ",
        0b010: "84.5kΩ",
        0b011: "140kΩ",
        0b100: "205kΩ",
        0b101: "280kΩ",
        0b110: "374kΩ",
        0b111: "Infinite (floating)",
    }

    return {
        "port1_addr": f"0x{addr_port1:02X}",
        "port2_addr": f"0x{addr_port2:02X}",
        "addr_bits": f"{addr_bits:03b}",
        "addr_resistor": addr_resistors.get(addr_bits, "Unknown"),
        "cntl1": cntl1,
        "cntl1_source": "LDO_3V3" if cntl1 else "GND",
        "cntl2": cntl2,
        "cntl2_source": "LDO_3V3" if cntl2 else "GND",
    }
