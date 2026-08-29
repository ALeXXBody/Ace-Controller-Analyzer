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
                 spi_label="SPI", i2c_label="I2C", image=None, uart_rx=None):
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
        self.uart_rx = uart_rx or {}  # {"rx": (gpio, "GP1 (pin 2)")}


BOARDS: Dict[str, BoardInfo] = {
    "pico1": BoardInfo(
        "pico1", "Raspberry Pi Pico 1", "RP2040/RP2350", HW_RP2040,
        {"sda": (4, "GP4 (pin 6)"), "scl": (5, "GP5 (pin 7)")},
        {"sck": (14, "GP14 (pin 19)"), "miso": (12, "GP12 (pin 16)"),
         "mosi": (15, "GP15 (pin 20)"), "cs": (13, "GP13 (pin 17)")},
        ["Power: VSYS/3V3(OUT) — 3.3V logic (no 1.8V without a shifter)",
         "SPI uses the SPI1 block — SPI0 stays free (not used on Pico 1)",
         "Same SPI1 pin block as the whole Pico family: one shield fits all",
         "UART sniff: RX on GP1 (pin 2)"],
        uart_rx={"rx": (1, "GP1 (pin 2)")},
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
        uart_rx={"rx": (1, "GP1 (pin 2)")},
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
        uart_rx={"rx": (1, "GP1 (pin 2)")},
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
        uart_rx={"rx": (1, "GP1 (pin 2)")},
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
         "Same SPI1 pin block as the Pico family: one shield fits all",
         "UART sniff: RX on GP1 (right edge)"],
        uart_rx={"rx": (1, "GP1")},
        image="rp2040_zero.png",
    ),
    "esp32-s3-devkitc-1": BoardInfo(
        "esp32-s3-devkitc-1", "ESP32-S3 DevKitC", "ESP32", HW_ESP32,
        {"sda": (8, "GPIO8"), "scl": (9, "GPIO9")},
        {"sck": (12, "GPIO12"), "miso": (13, "GPIO13"),
         "mosi": (11, "GPIO11"), "cs": (10, "GPIO10")},
        ["Power: 5V pin or USB — 3.3V logic",
         "SPI on the FSPI peripheral (default SPI pins)",
         "UART sniff: RX on GPIO4",
         "Web UI: join 'cd3217-analyzer' AP → http://192.168.4.1"],
        uart_rx={"rx": (4, "GPIO4")},
        image="esp32s3.png",
    ),
    "esp32-c3-supermini": BoardInfo(
        "esp32-c3-supermini", "ESP32-C3 SuperMini", "ESP32", HW_ESP32,
        {"sda": (8, "GPIO8"), "scl": (9, "GPIO9")},
        {"sck": (4, "GPIO4"), "miso": (6, "GPIO6"),
         "mosi": (5, "GPIO5"), "cs": (7, "GPIO7")},
        ["Power: 5V pin or USB — 3.3V logic",
         "I2C stays on GPIO8/9; SPI on GPIO4-7",
         "UART sniff: RX on GPIO1",
         "Web UI: join 'cd3217-analyzer' AP → http://192.168.4.1"],
        uart_rx={"rx": (1, "GPIO1")},
        image="esp32c3_supermini.png",
    ),
    "esp32-devkit": BoardInfo(
        "esp32-devkit", "ESP32 DevKit (classic)", "ESP32", HW_ESP32,
        {"sda": (21, "GPIO21"), "scl": (22, "GPIO22")},
        {"sck": (18, "GPIO18 (VSPI SCK)"), "miso": (19, "GPIO19 (VSPI MISO)"),
         "mosi": (23, "GPIO23 (VSPI MOSI)"), "cs": (5, "GPIO5 (VSPI CS)")},
        ["Power: 5V (VIN) or USB — 3.3V logic",
         "SPI on the VSPI peripheral (classic ESP32 default SPI pins)",
         "UART sniff: RX on GPIO16 (labeled RX2)",
         "Web UI: join 'cd3217-analyzer' AP → http://192.168.4.1"],
        uart_rx={"rx": (16, "GPIO16 (RX2)")},
        image="esp32_devkit.png",
    ),
    "esp32-c6-zero": BoardInfo(
        "esp32-c6-zero", "ESP32-C6-Zero (Waveshare)", "ESP32", HW_ESP32,
        {"sda": (14, "GPIO14"), "scl": (15, "GPIO15")},
        {"sck": (21, "GPIO21"), "miso": (20, "GPIO20"),
         "mosi": (19, "GPIO19"), "cs": (18, "GPIO18")},
        ["Power: 5V pin or USB — 3.3V logic",
         "UART sniff: RX on GPIO1 (left edge)",
         "Pins follow the official Waveshare C6-Zero map (SDA=14 SCL=15, "
         "SPI 18-21) — all on the right edge of the board",
         "Web UI: join 'cd3217-analyzer' AP → http://192.168.4.1"],
        uart_rx={"rx": (1, "GPIO1")},
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
    uart = {}
    if info.get("uart_rx") is not None:
        uart = {"rx": (info["uart_rx"], f"GPIO{info['uart_rx']}")}
    return BoardInfo(
        name or "unknown", name or "Unknown board", fam,
        info.get("hw") or 0, i2c, spi,
        ["Pins reported live by the board firmware (unknown board type)"],
        uart_rx=uart,
    )

class MacBookInfo:
    """A MacBook logic board and where to connect the analyzer to its
    CD3217 (ACE2) USB-C power-delivery controller I2C bus.

    'connect' is guidance for the hardware tap point: because Apple does not
    publish test-point designators for these buses, the practical connection
    is at the CD3217 chip pins or the pull-up/series resistors on the named
    I2C nets (see docs/).
    """
    def __init__(self, model, board_nos, ports, ace, bus, addresses,
                 connect, notes):
        self.model = model            # Apple model (A1706 ...)
        self.board_nos = board_nos    # list of 820-XXXXX logic board numbers
        self.ports = ports            # number of USB-C/Thunderbolt charge ports
        self.ace = ace                # "CD3217 (ACE2)" or "CD3215 (ACE1)"
        self.bus = bus                # host bus name(s), e.g. "SMC_I2C1 / AP_I2C0"
        self.addresses = addresses    # CD3217 device addresses observed
        self.connect = connect        # where / how to tap the bus (list)
        self.notes = notes            # wiring notes (list)


MAC_BOARDS: Dict[str, MacBookInfo] = {}


def _mac(k, **kw):
    MAC_BOARDS[k] = MacBookInfo(**kw)


# ────────────────────────────────────────────────────────────────────────
# Intel T2 / pre-T2 (ACE1 = CD3215) and T2 (ACE2 = CD3217)
# ────────────────────────────────────────────────────────────────────────
_mac("a1706", model="MacBook Pro 13\" Touch Bar 2016/17", board_nos=["820-00923"],
     ports=4, ace="CD3215 (ACE1)", bus="SMC I2C (SMC_SMBUS) / AP I2C0",
     addresses="no pub. address map",
     connect=[
         "USB-C/Thunderbolt ports: 4 (one CD3215 per port, Master on the "
         "DFU-capable port, rest Slave)",
         "No named test point is published. Tap SDA/SCL + GND on a "
         "pull-up/series resistor of the SMC / AP I2C0 net near a CD3215, "
         "or directly on the CD3215 BGA pins (Port1 SDA=B5 SCL=A4, "
         "Port2 SDA=B7 SCL=A6)",
         "The USB-C connector CC1/CC2 pins carry PD + SPI-ROM boot traffic, "
         "NOT the host I2C register bus — do not expect addresses here"],
     notes=["3.3 V open-drain bus; do not add pull-ups (already on board)",
            "Ground to an exposed shield / screw boss, high-Z probe"])

_mac("a1708", model="MacBook Pro 13\" 2016/17 (no Touch Bar)", board_nos=["820-00840", "820-00875"],
     ports=2, ace="CD3215 (ACE1)", bus="SMC I2C (SMBUS_SMC_*) / AP I2C0",
     addresses="no pub. address map",
     connect=[
         "USB-C ports: 2 (one CD3215 per port)",
         "Tap SDA/SCL + GND on a SMBUS_SMC_* / AP I2C0 pull-up/series "
         "resistor near a CD3215, or the CD3215 BGA pins (B5/A4, B7/A6)",
         "2-port board: controllers are the strap-configurable type (no OTP "
         "address lock), so both are interchangeable"],
     notes=["3.3 V open-drain bus; high-Z probe, no extra pull-ups",
            "Ground to shield / screw boss"])

_mac("a1707", model="MacBook Pro 15\" 2016/17", board_nos=["820-00928", "820-00281"],
     ports=4, ace="CD3215 (ACE1)", bus="SMC I2C / AP I2C0",
     addresses="no pub. address map",
     connect=[
         "USB-C/Thunderbolt ports: 4 (one CD3215 per port)",
         "Tap SDA/SCL + GND on a pull-up/series resistor of the SMC / "
         "AP I2C0 net near a CD3215, or the CD3215 BGA pins (B5/A4, B7/A6)",
         "USB-C connector CC pins carry PD/SPI, not the host I2C bus"],
     notes=["3.3 V open-drain bus; no extra pull-ups; high-Z probe",
            "Ground via shield / screw boss"])

_mac("a1989", model="MacBook Pro 13\" 2018/19 Touch Bar", board_nos=["820-00850"],
     ports=4, ace="CD3215 (ACE1)", bus="SMC I2C (SMBUS_SMC_*) / AP I2C0",
     addresses="no pub. address map",
     connect=[
         "USB-C/Thunderbolt ports: 4 (one CD3215 per port)",
         "Tap SDA/SCL + GND on a SMBUS_SMC_* / AP I2C0 pull-up/series "
         "resistor near a CD3215, or the CD3215 BGA pins (B5/A4, B7/A6)"],
     notes=["3.3 V open-drain bus; high-Z probe, no extra pull-ups",
            "2018 model uses CD3215; the ACE2 (CD3217) starts with 2019"])

_mac("a1990", model="MacBook Pro 15\" 2018/19", board_nos=["820-01041", "820-01326"],
     ports=4, ace="CD3215 (ACE1) / CD3217 (ACE2, 2019)", bus="SMC I2C / AP I2C0",
     addresses="no pub. address map",
     connect=[
         "USB-C/Thunderbolt ports: 4 (one controller per port)",
         "Tap SDA/SCL + GND on a pull-up/series resistor of the SMC / "
         "AP I2C0 net near a controller, or the BGA pins (B5/A4, B7/A6)",
         "2018 board (820-01041) = CD3215; 2019 board (820-01326) = CD3217"],
     notes=["3.3 V open-drain bus; high-Z probe, no extra pull-ups"])

_mac("a1932", model="MacBook Air 2018/19", board_nos=["820-01521"],
     ports=2, ace="CD3215 (ACE1)", bus="SMC I2C / AP I2C0",
     addresses="no pub. address map",
     connect=[
         "USB-C ports: 2 (one CD3215 per port)",
         "Tap SDA/SCL + GND on a pull-up/series resistor near a CD3215, or "
         "the CD3215 BGA pins (B5/A4, B7/A6)"],
     notes=["3.3 V open-drain bus; high-Z probe, no extra pull-ups"])

_mac("a2159", model="MacBook Pro 13\" 2019/20 (2-port)", board_nos=["820-01598"],
     ports=2, ace="CD3217 (ACE2)", bus="SMC I2C1 / AP I2C0",
     addresses="no pub. address map",
     connect=[
         "USB-C ports: 2 (one CD3217 per port)",
         "Tap SDA/SCL + GND on a pull-up/series resistor of SMC I2C1 / "
         "AP I2C0 near a CD3217, or the CD3217 BGA pins (B5/A4, B7/A6)",
         "2-port = strap-configured ACE2 (interchangeable)"],
     notes=["3.3 V open-drain bus; high-Z probe, no extra pull-ups",
            "ACE2 first appears 2019 (with A2141/A2159)"])

_mac("a2141", model="MacBook Pro 16\" 2019", board_nos=["820-01700"],
     ports=4, ace="CD3217 (ACE2)", bus="SMC I2C1 / AP I2C0",
     addresses="verified schematic + boardview (820-01700): "
               "U3100=XA @0x38; U3200=XB @0x3F; UB300=TA @0x3B; UB400=TB @0x3C",
     connect=[
         "USB-C/Thunderbolt ports: 4 (one CD3217B12 ACE2 controller per port)",
         "Pair labels: XA/XB = left (I2C_UPC_X), TA/TB = right (I2C_UPC_T); "
         "all four hang on the shared AP I2C0 (I2C_UPC) bus",
         "Tap SDA/SCL + GND on a pull-up/series resistor of the I2C_UPC "
         "nets near a controller, or the CD3217 BGA pins (B5/A4, B7/A6)"],
     notes=["3.3 V open-drain bus; high-Z probe, no extra pull-ups",
            "Addresses verified from 820-01700 schematic I2C table "
            "(WRITE 0x70/7E/76/78) + boardview pin nets"])

_mac("a2251", model="MacBook Pro 13\" 2020 Intel (4-port)", board_nos=["820-01949"],
     ports=4, ace="CD3217 (ACE2)", bus="SMC I2C1 / AP I2C0",
     addresses="no pub. address map",
     connect=[
         "USB-C/Thunderbolt ports: 4 (one CD3217 per port)",
         "Tap SDA/SCL + GND on a pull-up/series resistor of SMC I2C1 / "
         "AP I2C0 near a CD3217, or the CD3217 BGA pins (B5/A4, B7/A6)",
         "Installation order 1/2/3/4 matters for OTP-addressed positions"],
     notes=["3.3 V open-drain bus; high-Z probe, no extra pull-ups"])

_mac("a2289", model="MacBook Pro 13\" 2020 Intel (2-port)", board_nos=["820-01987"],
     ports=2, ace="CD3217 (ACE2)", bus="SMC I2C1 / AP I2C0",
     addresses="no pub. address map",
     connect=[
         "USB-C ports: 2 (one CD3217 per port)",
         "Tap SDA/SCL + GND on a pull-up/series resistor of SMC I2C1 / "
         "AP I2C0 near a CD3217, or the CD3217 BGA pins (B5/A4, B7/A6)"],
     notes=["3.3 V open-drain bus; high-Z probe, no extra pull-ups"])

# ────────────────────────────────────────────────────────────────────────
# Apple Silicon (all ACE2 = CD3217)
# ────────────────────────────────────────────────────────────────────────
_mac("a2337", model="MacBook Air M1 2020", board_nos=["820-02016"],
     ports=2, ace="CD3217 (ACE2)", bus="I2C0 (SoC AP) / I2C1 (SMC side)",
     addresses="0x38, 0x3F (I2C0); 0x6B (I2C1 bank)",
     connect=[
         "USB-C ports: 2 (two CD3217: UF400, UF500)",
         "Tap SDA/SCL + GND on a pull-up/series resistor of the I2C0 / "
         "I2C1 nets near UF400/UF500, or the CD3217 BGA pins (B5/A4, B7/A6)",
         "Low-risk alternative: software readout via Asahi Linux "
         "/dev/i2c nodes instead of soldering"],
     notes=["3.3 V open-drain bus; high-Z probe, no extra pull-ups",
            "HEEP/UF260 SPI ROM near the controllers (not the I2C bus)",
            "Chip ids + addresses verified (Asahi / repair.wiki)"])

_mac("a2338", model="MacBook Pro 13\" M1/M2 2020-22", board_nos=["820-02020"],
     ports=2, ace="CD3217 (ACE2)", bus="I2C0 (SoC AP) / I2C1 (SMC side)",
     addresses="0x38, 0x3F (I2C0); 0x6B (I2C1 bank)",
     connect=[
         "USB-C ports: 2 (two CD3217 controllers)",
         "Tap SDA/SCL + GND on a pull-up/series resistor of the I2C0 / "
         "I2C1 nets near a CD3217, or the CD3217 BGA pins (B5/A4, B7/A6)",
         "Low-risk alternative: software readout via Asahi Linux i2c "
         "(/dev/i2c nodes)"],
     notes=["3.3 V open-drain bus; high-Z probe, no extra pull-ups",
            "Addresses verified (Asahi / repair.wiki)"])

_mac("a2442", model="MacBook Pro 14\" M1 Pro/Max 2021", board_nos=["820-02098", "820-02443"],
     ports=3, ace="CD3217 (ACE2)", bus="AP I2C0 / SMC I2C1",
     addresses="AP_I2C0: 0x38, 0x3F, 0x3B, 0x3A; SMC_I2C1: 0x38, 0x3F, 0x6B",
     connect=[
         "Thunderbolt ports: 3 (three CD3217); the 4th power path is "
         "MagSafe3, not a controller",
         "Tap SDA/SCL + GND on a pull-up/series resistor of the AP I2C0 / "
         "SMC I2C1 nets near a CD3217, or the CD3217 BGA pins (B5/A4, B7/A6)",
         "I2C address map documented in repair.wiki (AP_I2C0 p.56, "
         "SMC_I2C1 p.57)"],
     notes=["3.3 V open-drain bus; high-Z probe, no extra pull-ups",
            "OTP strap resistors R5650 / R5508 / R5610 near the controllers"])

_mac("a2485", model="MacBook Pro 16\" M1 Pro/Max 2021", board_nos=["820-02100", "820-02382"],
     ports=4, ace="CD3217/CD3218 (ACE2)", bus="AP I2C0 / SMC I2C1",
     addresses=(
         "verified schematic map (820-02382): ACE2-0=UF400 @0x38; "
         "ACE2-1=UF500 @0x3F; ACE2-2=UG400 @0x3B; ACE2-3=U5500 @0x3A; "
         "BANK ALL CALL (broadcast, all four listen) @0x6B"),
     connect=[
         "Thunderbolt ports: 4 (four ACE2 controllers; U5500 is the CD3218B12 "
         "system/charge controller, the rest are CD3217B12)",
         "Tap SDA/SCL + GND on a pull-up/series resistor of the AP I2C0 / "
         "SMC I2C1 nets near a controller, or the CD3217 BGA pins (B5/A4, B7/A6)",
         "820-02382: SDA/SCL/GND easily accessed at test point JF200"],
     notes=[
         "3.3 V open-drain bus; high-Z probe, no extra pull-ups",
         "0x6B is the all-call broadcast address (garbage/all-FF reads expected "
         "there) -- not a fault; probe 0x38/0x3A/0x3B/0x3F for the 4 real chips",
         "2 OTP-strapped (system/charge path) + 2 non-OTP (data port) "
         "controllers; a non-OTP IC only replaces a non-OTP socket"])

_mac("a2779", model="MacBook Air M2 2022", board_nos=["820-02167"],
     ports=2, ace="CD3217 (ACE2)", bus="I2C0 / I2C1",
     addresses="no pub. address map",
     connect=[
         "USB-C ports: 2 (two CD3217)",
         "Tap SDA/SCL + GND on a pull-up/series resistor near a CD3217, or "
         "the CD3217 BGA pins (B5/A4, B7/A6)"],
     notes=["3.3 V open-drain bus; high-Z probe, no extra pull-ups"])


def mac_from_model_key(key: Optional[str]) -> Optional[MacBookInfo]:
    """Look up a MacBook by its Apple model key (A1706 etc., case-insensitive)."""
    if not key:
        return None
    return MAC_BOARDS.get(key.strip().lower())


def mac_from_model_name(name: str) -> Optional[MacBookInfo]:
    """Look up a MacBook by a model string like 'MacBook Pro 13\" 2019'."""
    if not name:
        return None
    n = name.strip().lower()
    for b in MAC_BOARDS.values():
        if n in b.model.lower():
            return b
    return None
