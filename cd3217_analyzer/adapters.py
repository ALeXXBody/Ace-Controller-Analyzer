"""I2C adapter abstraction layer.

Supports multiple USB-to-I2C adapters:
- FTDI FT232H (via pyftdi)
- CH341 (via ch341-i2c or i2c-dev)
- Linux SMBus / i2c-dev (Raspberry Pi, etc.)
- Bus Pirate (via pyBusPirateLite)
"""

import time
from abc import ABC, abstractmethod
from typing import List, Optional, Tuple


class I2CAdapter(ABC):
    """Abstract base class for I2C adapters."""

    @abstractmethod
    def open(self) -> None:
        """Open the I2C adapter connection."""
        pass

    @abstractmethod
    def close(self) -> None:
        """Close the I2C adapter connection."""
        pass

    @abstractmethod
    def scan(self, start: int = 0x08, end: int = 0x77) -> List[int]:
        """
        Scan I2C bus and return list of responding addresses.
        Each address should ACK at least once.
        """
        pass

    @abstractmethod
    def read_byte(self, address: int, register: int) -> int:
        """Read a single byte from a device register."""
        pass

    @abstractmethod
    def read_bytes(self, address: int, register: int, length: int) -> bytes:
        """Read multiple bytes from a device register."""
        pass

    @abstractmethod
    def write_byte(self, address: int, register: int, value: int) -> bool:
        """Write a single byte to a device register. Returns True on success."""
        pass

    @abstractmethod
    def write_bytes(self, address: int, register: int, data: bytes) -> bool:
        """Write multiple bytes to a device register. Returns True on success."""
        pass

    def ping(self, address: int) -> bool:
        """Check if a device ACKs at the given address."""
        try:
            self.read_byte(address, 0x00)
            return True
        except Exception:
            return False

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *args):
        self.close()


class FTDIAdapter(I2CAdapter):
    """
    FTDI FT232H-based I2C adapter (e.g., Adafruit FT232H breakout).

    Requires: pyftdi
    Install: pip install pyftdi

    Wiring:
        FT232H ADBUS0 = SCL (use 1k pullup to 3.3V)
        FT232H ADBUS1 = SDA (use 1k pullup to 3.3V)
        FT232H GND = GND
    """

    def __init__(self, url: str = "ftdi://ftdi:232h/1", frequency: int = 100000):
        self.url = url
        self.frequency = frequency
        self._i2c = None

    def open(self) -> None:
        from pyftdi.i2c import I2cController
        self._i2c = I2cController()
        self._i2c.configure(self.url)

    def close(self) -> None:
        if self._i2c:
            self._i2c.terminate()
            self._i2c = None

    def scan(self, start: int = 0x08, end: int = 0x77) -> List[int]:
        found = []
        for addr in range(start, end + 1):
            try:
                port = self._i2c.get_port(addr)
                port.read(1)
                found.append(addr)
            except Exception:
                pass
        return found

    def read_byte(self, address: int, register: int) -> int:
        port = self._i2c.get_port(address)
        result = port.read_from(register, 1)
        return result[0]

    def read_bytes(self, address: int, register: int, length: int) -> bytes:
        port = self._i2c.get_port(address)
        return bytes(port.read_from(register, length))

    def write_byte(self, address: int, register: int, value: int) -> bool:
        try:
            port = self._i2c.get_port(address)
            port.write_to(register, bytes([value]))
            return True
        except Exception:
            return False

    def write_bytes(self, address: int, register: int, data: bytes) -> bool:
        try:
            port = self._i2c.get_port(address)
            port.write_to(register, data)
            return True
        except Exception:
            return False


class CH341Adapter(I2CAdapter):
    """
    CH341A-based I2C adapter.

    Requires: ch341-i2c (Linux) or i2c-tools with ch341 kernel module
    Alternative: Uses /dev/i2c-* with i2c-dev

    Install: pip install smbus2
    """

    def __init__(self, bus_number: int = 1):
        self.bus_number = bus_number
        self._bus = None

    def open(self) -> None:
        from smbus2 import SMBus
        self._bus = SMBus(self.bus_number)

    def close(self) -> None:
        if self._bus:
            self._bus.close()
            self._bus = None

    def scan(self, start: int = 0x08, end: int = 0x77) -> List[int]:
        found = []
        for addr in range(start, end + 1):
            try:
                self._bus.read_byte_data(addr, 0x00)
                found.append(addr)
            except Exception:
                pass
        return found

    def read_byte(self, address: int, register: int) -> int:
        return self._bus.read_byte_data(address, register)

    def read_bytes(self, address: int, register: int, length: int) -> bytes:
        return bytes(self._bus.read_i2c_block_data(address, register, length))

    def write_byte(self, address: int, register: int, value: int) -> bool:
        try:
            self._bus.write_byte_data(address, register, value)
            return True
        except Exception:
            return False

    def write_bytes(self, address: int, register: int, data: bytes) -> bool:
        try:
            self._bus.write_i2c_block_data(address, register, list(data))
            return True
        except Exception:
            return False


class SMBusAdapter(I2CAdapter):
    """
    Generic Linux SMBus adapter via i2c-dev (/dev/i2c-*).

    Works with any Linux I2C adapter:
    - Raspberry Pi (built-in I2C)
    - BeagleBone
    - Any Linux SBC with I2C enabled

    Requires: smbus2
    Install: pip install smbus2

    Enable I2C on Raspberry Pi:
        sudo raspi-config -> Interface -> I2C -> Enable
        sudo modprobe i2c-dev
    """

    def __init__(self, bus_number: int = 1):
        self.bus_number = bus_number
        self._bus = None

    def open(self) -> None:
        from smbus2 import SMBus
        self._bus = SMBus(self.bus_number)

    def close(self) -> None:
        if self._bus:
            self._bus.close()
            self._bus = None

    def scan(self, start: int = 0x08, end: int = 0x77) -> List[int]:
        found = []
        for addr in range(start, end + 1):
            try:
                self._bus.read_byte(addr)
                found.append(addr)
            except Exception:
                pass
        return found

    def read_byte(self, address: int, register: int) -> int:
        return self._bus.read_byte_data(address, register)

    def read_bytes(self, address: int, register: int, length: int) -> bytes:
        # Read byte by byte for reliability with unknown devices
        data = bytearray()
        for i in range(length):
            try:
                b = self._bus.read_byte_data(address, register + i)
                data.append(b)
            except Exception:
                data.append(0xFF)
        return bytes(data)

    def write_byte(self, address: int, register: int, value: int) -> bool:
        try:
            self._bus.write_byte_data(address, register, value)
            return True
        except Exception:
            return False

    def write_bytes(self, address: int, register: int, data: bytes) -> bool:
        try:
            self._bus.write_i2c_block_data(address, register, list(data))
            return True
        except Exception:
            return False


def detect_adapter() -> Optional[I2CAdapter]:
    """
    Auto-detect available I2C adapter.

    Priority:
    1. FTDI FT232H (pyftdi)
    2. Linux SMBus / i2c-dev (smbus2)
    3. None found
    """
    # Try FTDI first
    try:
        from pyftdi.i2c import I2cController
        adapter = FTDIAdapter()
        adapter.open()
        # Quick test — scan valid range
        adapter.scan(0x08, 0x77)
        return adapter
    except Exception:
        pass

    # Try Linux SMBus
    try:
        import os
        import glob
        i2c_devices = glob.glob("/dev/i2c-*")
        if i2c_devices:
            bus_num = int(i2c_devices[0].split("-")[-1])
            adapter = SMBusAdapter(bus_num)
            adapter.open()
            return adapter
    except Exception:
        pass

    return None


ADAPTER_TYPES = {
    "ftdi": FTDIAdapter,
    "ch341": CH341Adapter,
    "smbus": SMBusAdapter,
    "linux": SMBusAdapter,
}
