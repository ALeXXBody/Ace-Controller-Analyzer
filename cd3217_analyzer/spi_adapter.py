"""SPI adapter for reading/writing external flash via FTDI FT232H.

The CD3217B12 loads firmware from an external SPI flash chip (typically
Winbond W25Q80 or similar). This module provides SPI communication
to read/write that flash.

Requires: pyftdi (same as I2C adapter)
FTDI FT232H pin mapping for SPI:
    ADBUS0 = SCK   (clock)
    ADBUS1 = MOSI  (master out, slave in)
    ADBUS2 = MISO  (master in, slave out)
    ADBUS3 = CS#   (chip select, active low)
"""

import time
from typing import Optional


class SPIAdapter:
    """SPI adapter using FTDI FT232H via pyftdi."""

    def __init__(self, url: str = "ftdi://ftdi:232h/1", frequency: int = 1000000):
        self.url = url
        self.frequency = frequency
        self._spi = None
        self._port = None

    def open(self) -> None:
        from pyftdi.spi import SpiController
        self._spi = SpiController(cs_count=1)
        self._spi.configure(self.url)
        self._port = self._spi.get_port(cs=0, freq=self.frequency,
                                         mode=0)  # SPI Mode 0 (CPOL=0, CPHA=0)

    def close(self) -> None:
        if self._spi:
            self._spi.terminate()
            self._spi = None
            self._port = None

    def transfer(self, data: bytes) -> bytes:
        """Full-duplex SPI transfer."""
        return self._port.exchange(data)

    def write(self, data: bytes) -> None:
        """SPI write (no readback)."""
        self._port.write(data)

    def read(self, length: int) -> bytes:
        """SPI read."""
        return self._port.read(length)

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *args):
        self.close()
