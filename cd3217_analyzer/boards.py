"""Board knowledge base: pin maps and wiring notes for all supported boards.

Used by the GUI Board tab to show the user exactly which pins to connect.
The live INFO frame (bridge cmd 0x05) reports the flashed firmware's actual
pin numbers; this table adds human context (GPIO label, physical pin, power
notes) and serves as a fallback when no board is connected.
"""

from typing import Dict, Optional

# hw type codes reported by the INFO frame (see firmware bridge.cpp)
HW_RP2040 = 0x01
HW_ESP32 = 0x02


class BoardInfo:
    def __init__(self, key, name, family, hw, i2c, spi, notes,
                 spi_label="SPI", i2c_label="I2C", image=None):
        self.key = key
        self.name = name            # human name
        self.family = family        # "RP2040/RP2350" / "ESP32"
        self.hw = hw                # HW_* code
        self.i2c = i2c              # {"sda": (gpio, "GP4"), "scl": (gpio, "GP5")}
        self.spi = spi              # {"sck": (gpio, "GP14"), ...}
        self.notes = notes          # list of wiring strings
        self.spi_label = spi_label
        self.i2c_label = i2c_label
        self.image = image          # pinout diagram (assets/boards/<file>)


BOARDS: Dict[str, BoardInfo] = {
    "pico1": BoardInfo(
        "pico1", "Raspberry Pi Pico 1", "RP2040/RP2350", HW_RP2040,
        {"sda": (4, "GP4 (pin 6)"), "scl": (5, "GP5 (pin 7)")},
        {"sck": (14, "GP14 (pin 19)"), "miso": (12, "GP12 (pin 16)"),
         "mosi": (15, "GP15 (pin 20)"), "cs": (13, "GP13 (pin 17)")},
        ["Power: VSYS/3V3(OUT) — 3.3V logic (no 1.8V without a shifter)",
         "SPI uses the SPI1 block — SPI0 stays free (not used on Pico 1)",
         "Same SPI1 pin block as the whole Pico family: one shield fits all"],
        image="pico.png",
    ),
    "pico2": BoardInfo(
        "pico2", "Raspberry Pi Pico 2 (RP2350)", "RP2040/RP2350", HW_RP2040,
        {"sda": (4, "GP4 (pin 6)"), "scl": (5, "GP5 (pin 7)")},
        {"sck": (14, "GP14 (pin 19)"), "miso": (12, "GP12 (pin 16)"),
         "mosi": (15, "GP15 (pin 20)"), "cs": (13, "GP13 (pin 17)")},
        ["Power: VSYS/3V3(OUT) — 3.3V logic (no 1.8V without a shifter)",
         "SPI uses the SPI1 block — SPI0 stays free",
         "Same SPI1 pin block as the whole Pico family: one shield fits all"],
        image="pico.png",
    ),
    "pico-w": BoardInfo(
        "pico-w", "Raspberry Pi Pico W", "RP2040/RP2350", HW_RP2040,
        {"sda": (4, "GP4 (pin 6)"), "scl": (5, "GP5 (pin 7)")},
        {"sck": (14, "GP14 (pin 19)"), "miso": (12, "GP12 (pin 16)"),
         "mosi": (15, "GP15 (pin 20)"), "cs": (13, "GP13 (pin 17)")},
        ["Power: VSYS/3V3(OUT) — 3.3V logic",
         "WiFi radio occupies SPI0 — flash backend uses SPI1 (no conflict)",
         "Web UI: join 'cd3217-analyzer' AP → http://192.168.4.1"],
        image="pico.png",
    ),
    "pico2-w": BoardInfo(
        "pico2-w", "Raspberry Pi Pico 2 W (RP2350)", "RP2040/RP2350", HW_RP2040,
        {"sda": (4, "GP4 (pin 6)"), "scl": (5, "GP5 (pin 7)")},
        {"sck": (14, "GP14 (pin 19)"), "miso": (12, "GP12 (pin 16)"),
         "mosi": (15, "GP15 (pin 20)"), "cs": (13, "GP13 (pin 17)")},
        ["Power: VSYS/3V3(OUT) — 3.3V logic",
         "WiFi radio occupies SPI0 — flash backend uses SPI1 (no conflict)",
         "Web UI: join 'cd3217-analyzer' AP → http://192.168.4.1"],
        image="pico.png",
    ),
    "rp2040-zero": BoardInfo(
        "rp2040-zero", "Waveshare RP2040-Zero", "RP2040/RP2350", HW_RP2040,
        {"sda": (4, "GP4"), "scl": (5, "GP5")},
        {"sck": (14, "GP14"), "miso": (12, "GP12"),
         "mosi": (15, "GP15"), "cs": (13, "GP13")},
        ["Power: 5V pin or USB — 3.3V logic",
         "I2C (GP4/GP5) on the right edge; SPI (GP12-GP15) wraps the "
         "bottom-left corner and bottom edge — see diagram",
         "Same SPI1 pin block as the Pico family: one shield fits all"],
        image="rp2040_zero.png",
    ),
    "esp32-s3-devkitc-1": BoardInfo(
        "esp32-s3-devkitc-1", "ESP32-S3 DevKitC", "ESP32", HW_ESP32,
        {"sda": (8, "GPIO8"), "scl": (9, "GPIO9")},
        {"sck": (12, "GPIO12"), "miso": (13, "GPIO13"),
         "mosi": (11, "GPIO11"), "cs": (10, "GPIO10")},
        ["Power: 5V pin or USB — 3.3V logic",
         "SPI on the FSPI peripheral (default SPI pins)",
         "Web UI: join 'cd3217-analyzer' AP → http://192.168.4.1"],
        image="esp32s3.png",
    ),
    "esp32-c3-supermini": BoardInfo(
        "esp32-c3-supermini", "ESP32-C3 SuperMini", "ESP32", HW_ESP32,
        {"sda": (8, "GPIO8"), "scl": (9, "GPIO9")},
        {"sck": (4, "GPIO4"), "miso": (6, "GPIO6"),
         "mosi": (5, "GPIO5"), "cs": (7, "GPIO7")},
        ["Power: 5V pin or USB — 3.3V logic",
         "I2C stays on GPIO8/9; SPI on GPIO4-7",
         "Web UI: join 'cd3217-analyzer' AP → http://192.168.4.1"],
        image="esp32c3_supermini.png",
    ),
    "esp32-devkit": BoardInfo(
        "esp32-devkit", "ESP32 DevKit (classic)", "ESP32", HW_ESP32,
        {"sda": (21, "GPIO21"), "scl": (22, "GPIO22")},
        {"sck": (18, "GPIO18 (VSPI SCK)"), "miso": (19, "GPIO19 (VSPI MISO)"),
         "mosi": (23, "GPIO23 (VSPI MOSI)"), "cs": (5, "GPIO5 (VSPI CS)")},
        ["Power: 5V (VIN) or USB — 3.3V logic",
         "SPI on the VSPI peripheral (classic ESP32 default SPI pins)",
         "Web UI: join 'cd3217-analyzer' AP → http://192.168.4.1"],
        image="esp32_devkit.png",
    ),
    "esp32-c6-zero": BoardInfo(
        "esp32-c6-zero", "ESP32-C6-Zero (Waveshare)", "ESP32", HW_ESP32,
        {"sda": (14, "GPIO14"), "scl": (15, "GPIO15")},
        {"sck": (21, "GPIO21"), "miso": (20, "GPIO20"),
         "mosi": (19, "GPIO19"), "cs": (18, "GPIO18")},
        ["Power: 5V pin or USB — 3.3V logic",
         "Pins follow the official Waveshare C6-Zero map (SDA=14 SCL=15, "
         "SPI 18-21) — all on the right edge of the board",
         "Web UI: join 'cd3217-analyzer' AP → http://192.168.4.1"],
        image="esp32c6_zero.png",
    ),
}


def get_board_info(board_name: Optional[str]) -> Optional[BoardInfo]:
    """Look up a board by its firmware CD3217_BOARD string."""
    if not board_name:
        return None
    return BOARDS.get(board_name.strip().lower())


def board_from_info(info: dict) -> Optional[BoardInfo]:
    """Best-effort BoardInfo from a live INFO-frame dict.

    Returns the static table entry when the board is known; otherwise builds
    a dynamic entry from the reported pins so the user still gets a wiring
    guide.
    """
    name = (info or {}).get("board")
    known = get_board_info(name)
    if known:
        return known
    if not info:
        return None
    # Unknown board: synthesize from the live pin numbers
    fam = "ESP32" if info.get("hw") == HW_ESP32 else "RP2040/RP2350"
    i2c = {}
    for role, key in (("sda", "sda"), ("scl", "scl")):
        v = info.get(key)
        if v is not None:
            i2c[role] = (v, f"GPIO{v}")
    spi = {}
    for role in ("sck", "miso", "mosi", "cs"):
        v = info.get(f"spi_{role}")
        if v is not None:
            spi[role] = (v, f"GPIO{v}")
    if not i2c and not spi:
        return None
    return BoardInfo(
        name or "unknown", name or "Unknown board", fam,
        info.get("hw") or 0, i2c, spi,
        ["Pins reported live by the board firmware (unknown board type)"],
    )
