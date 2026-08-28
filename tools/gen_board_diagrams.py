"""Generate board pinout diagrams for the CD3217 Analyzer Board tab.

Draws each supported board as a clean schematic (transparent background so
the app's card color shows through) with:
  - board outline, USB connector, MCU chip, buttons, antenna
  - every edge pad drawn castellated-style with its silkscreen label
  - the pins this project uses HIGHLIGHTED:
        green = I2C  (SDA / SCL)
        cyan  = SPI  (SCK / MISO / MOSI / CS)
  - a legend

Sources for the physical layouts (verified per board):
  - Pico family: Raspberry Pi Pico datasheet (Rev3 pinout, pins 1-40)
  - RP2040-Zero: Waveshare / NuttX docs (23 pads anticlockwise from USB;
    8 left + 7 bottom + 8 right fits the 23.5x18mm footprint at 2.54mm pitch)
  - ESP32-S3-DevKitC-1: Espressif user guide (J1 / J3 header tables)
  - ESP32-C3 SuperMini: standard Nologo/sigmdel pinout (power + GPIO0-4 left,
    GPIO5-10/20/21 right)
  - ESP32 DevKit V1 (30-pin DOIT): classic pinout (EN..VIN / IO23..IO15)
  - ESP32-C6-Zero (Waveshare): official arduino-esp32 variant pins_arduino.h

Run:  python3 tools/gen_board_diagrams.py
Output: assets/boards/<key>.png   (+ prints a self-check of every highlight)
"""

import os
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT_DIR = os.path.join(ROOT, "assets", "boards")

# ─── palette ─────────────────────────────────────────────────────────────────
COL_TEXT = (229, 238, 251, 255)
COL_DIM = (139, 155, 180, 255)
COL_PAD = (176, 186, 200, 255)
COL_PAD_EDGE = (90, 100, 116, 255)
COL_USB = (150, 158, 170, 255)
COL_CHIP = (24, 30, 44, 255)
COL_CHIP_TXT = (150, 160, 180, 255)
COL_BTN = (60, 70, 90, 255)
COL_I2C = (34, 197, 94, 255)        # green
COL_SPI = (56, 189, 248, 255)       # cyan
COL_BOARD_EDGE = (255, 255, 255, 60)

I2C_TAGS = {"sda": "SDA", "scl": "SCL"}
SPI_TAGS = {"sck": "SCK", "miso": "MISO", "mosi": "MOSI", "cs": "CS"}


# ─── fonts ───────────────────────────────────────────────────────────────────
def load_font(size, bold=True):
    cands = []
    if bold:
        cands += [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
            "C:/Windows/Fonts/segoeuib.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
        ]
    cands += [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for p in cands:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


# ─── board definitions ───────────────────────────────────────────────────────
PICO_LEFT = ["GP0", "GP1", "GND", "GP2", "GP3", "GP4", "GP5", "GND", "GP6",
             "GP7", "GP8", "GP9", "GND", "GP10", "GP11", "GP12", "GP13",
             "GND", "GP14", "GP15"]
PICO_RIGHT = ["VBUS", "VSYS", "GND", "3V3EN", "3V3", "VREF", "AGND", "GP28",
              "GP27", "GP26", "RUN", "GP22", "GND", "GP21", "GP20", "GP19",
              "GP18", "GND", "GP17", "GP16"]

BOARDS = [
    dict(
        key="pico",
        title="Raspberry Pi Pico family — Pico 1 / Pico 2 / Pico W / Pico 2 W",
        subtitle="USB at top · SPI1 on GP12-GP15 · I2C on GP4/GP5",
        pcb=(26, 62, 46, 255),          # dark green
        chip_label="RP2040 / RP2350",
        usb="top",
        left=PICO_LEFT, right=PICO_RIGHT,
        board_w=300, pitch=64,
        highlights={
            "GP4": ("i2c", "sda"), "GP5": ("i2c", "scl"),
            "GP12": ("spi", "miso"), "GP13": ("spi", "cs"),
            "GP14": ("spi", "sck"), "GP15": ("spi", "mosi"),
        },
        buttons=["BOOTSel", "RESET"],
    ),
    dict(
        key="rp2040_zero",
        title="Waveshare RP2040-Zero",
        subtitle="USB-C at top · SPI1 on GP12-GP15 · I2C on GP4/GP5",
        pcb=(18, 20, 28, 255),          # black
        chip_label="RP2040",
        usb="top",
        left=["5V", "GND", "3V3", "GP29", "GP28", "GP27", "GP26", "GP15"],
        bottom=["GP14", "GP13", "GP12", "GP11", "GP10", "GP9", "GP8"],
        right=["GP0", "GP1", "GP2", "GP3", "GP4", "GP5", "GP6", "GP7"],
        board_w=340, pitch=64,
        highlights={
            "GP4": ("i2c", "sda"), "GP5": ("i2c", "scl"),
            "GP12": ("spi", "miso"), "GP13": ("spi", "cs"),
            "GP14": ("spi", "sck"), "GP15": ("spi", "mosi"),
        },
        buttons=["BOOT", "RESET"],
    ),
    dict(
        key="esp32s3",
        title="ESP32-S3-DevKitC-1",
        subtitle="USB at bottom · J1 left / J3 right · I2C GPIO8/9 · SPI GPIO10-13",
        pcb=(52, 32, 36, 255),          # dark red
        chip_label="ESP32-S3",
        usb="bottom",
        left=["3V3", "3V3", "RST", "4", "5", "6", "7", "15", "16", "17",
              "18", "8", "3", "46", "9", "10", "11", "12", "13", "14",
              "5V", "G"],
        right=["G", "TX", "RX", "1", "2", "42", "41", "40", "39", "38",
               "37", "36", "35", "0", "45", "48", "47", "21", "20", "19",
               "G", "G"],
        board_w=380, pitch=58,
        highlights={
            "8": ("i2c", "sda"), "9": ("i2c", "scl"),
            "10": ("spi", "cs"), "11": ("spi", "mosi"),
            "12": ("spi", "sck"), "13": ("spi", "miso"),
        },
        buttons=["BOOT", "RST"],
        antenna=True,
    ),
    dict(
        key="esp32c3_supermini",
        title="ESP32-C3 SuperMini",
        subtitle="USB-C at top · I2C GPIO8/9 · SPI GPIO4-7",
        pcb=(20, 24, 34, 255),          # near-black blue
        chip_label="ESP32-C3",
        usb="top",
        left=["5V", "G", "3V3", "0", "1", "2", "3", "4"],
        right=["5", "6", "7", "8", "9", "10", "20", "21"],
        board_w=300, pitch=64,
        highlights={
            "8": ("i2c", "sda"), "9": ("i2c", "scl"),
            "4": ("spi", "sck"), "5": ("spi", "mosi"),
            "6": ("spi", "miso"), "7": ("spi", "cs"),
        },
        buttons=["BOOT", "RST"],
        antenna=True,
    ),
    dict(
        key="esp32_devkit",
        title="ESP32 DevKit V1 (30-pin DOIT layout)",
        subtitle="USB at bottom · I2C GPIO21/22 · VSPI GPIO5/18/19/23",
        pcb=(16, 26, 44, 255),          # dark navy
        chip_label="ESP32-WROOM",
        usb="bottom",
        left=["EN", "VP", "VN", "34", "35", "32", "33", "25", "26", "27",
              "14", "12", "13", "GND", "VIN"],
        right=["23", "22", "TX0", "RX0", "21", "GND", "19", "18", "5",
               "TX2", "RX2", "4", "0", "2", "15"],
        board_w=340, pitch=64,
        highlights={
            "21": ("i2c", "sda"), "22": ("i2c", "scl"),
            "18": ("spi", "sck"), "19": ("spi", "miso"),
            "23": ("spi", "mosi"), "5": ("spi", "cs"),
        },
        buttons=["EN", "BOOT"],
        antenna=True,
        footnote="30-pin DOIT DevKit V1 layout — some clones/38-pin variants "
                 "differ. Always cross-check your board's silkscreen.",
    ),
    dict(
        key="esp32c6_zero",
        title="Waveshare ESP32-C6-Zero",
        subtitle="USB at top · I2C GPIO14/15 · SPI GPIO18-21",
        pcb=(22, 20, 40, 255),          # dark purple-blue
        chip_label="ESP32-C6",
        usb="top",
        left=["5V", "GND", "3V3", "0", "1", "2", "3", "4", "5"],
        right=["16", "17", "14", "15", "18", "19", "20", "21", "22"],
        bottom=["13", "12", "23", "9", "8", "7", "6"],
        board_w=360, pitch=64,
        highlights={
            "14": ("i2c", "sda"), "15": ("i2c", "scl"),
            "18": ("spi", "cs"), "19": ("spi", "mosi"),
            "20": ("spi", "miso"), "21": ("spi", "sck"),
        },
        buttons=["BOOT", "RST"],
        antenna=True,
    ),
]


# ─── drawing ─────────────────────────────────────────────────────────────────
PAD_W, PAD_H = 46, 30          # pad size (side pads: w x h)
LABEL_MARGIN = 300             # room for labels on each side
HEADER_H = 170
FOOTER_H = 150


def text_wh(draw, txt, font):
    b = draw.textbbox((0, 0), txt, font=font)
    return b[2] - b[0], b[3] - b[1]


def draw_diagram(bd):
    pitch = bd["pitch"]
    left = bd.get("left", [])
    right = bd.get("right", [])
    bottom = bd.get("bottom", [])

    n_side = max(len(left), len(right), 1)
    board_w = bd["board_w"]
    board_h = n_side * pitch + (60 if bottom else 40)
    if bottom:
        board_h = max(board_h, len(bottom) * pitch + 60)

    W = LABEL_MARGIN * 2 + board_w + PAD_W * 2 + 60
    H = HEADER_H + board_h + PAD_H * 2 + FOOTER_H + \
        (40 if bd.get("footnote") else 0)

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    f_title = load_font(40)
    f_sub = load_font(26, bold=False)
    f_label = load_font(26)
    f_tag = load_font(24)
    f_chip = load_font(28)
    f_btn = load_font(20)
    f_note = load_font(22, bold=False)

    # title / subtitle
    d.text((W // 2, 30), bd["title"], font=f_title, fill=COL_TEXT, anchor="ma")
    d.text((W // 2, 92), bd["subtitle"], font=f_sub, fill=COL_DIM, anchor="ma")

    # board rect
    bx0 = (W - board_w) // 2
    by0 = HEADER_H + PAD_H + 10
    bx1, by1 = bx0 + board_w, by0 + board_h
    d.rounded_rectangle([bx0, by0, bx1, by1], radius=22, fill=bd["pcb"],
                        outline=COL_BOARD_EDGE, width=3)

    # USB connector
    uw, uh = 120, 46
    if bd["usb"] == "top":
        d.rounded_rectangle([(W - uw) // 2, by0 - uh // 2,
                             (W + uw) // 2, by0 + uh // 2],
                            radius=8, fill=COL_USB,
                            outline=COL_PAD_EDGE, width=2)
    else:
        d.rounded_rectangle([(W - uw) // 2, by1 - uh // 2,
                             (W + uw) // 2, by1 + uh // 2],
                            radius=8, fill=COL_USB,
                            outline=COL_PAD_EDGE, width=2)

    # antenna (ESP32 boards)
    if bd.get("antenna"):
        aw, ah = 150, 34
        ay = by0 + 26 if bd["usb"] == "bottom" else by0 + board_h - 60
        d.rounded_rectangle([(W - aw) // 2, ay, (W + aw) // 2, ay + ah],
                            radius=6, fill=(206, 168, 110, 255))
        d.text((W // 2, ay + ah // 2), "ANT", font=f_btn, fill=(40, 34, 20, 255),
               anchor="mm")

    # MCU chip
    cw, ch = min(board_w - 120, 260), 110
    d.rounded_rectangle([(W - cw) // 2, (by0 + by1) // 2 - ch // 2,
                         (W + cw) // 2, (by0 + by1) // 2 + ch // 2],
                        radius=10, fill=COL_CHIP, outline=COL_PAD_EDGE, width=2)
    d.text((W // 2, (by0 + by1) // 2), bd["chip_label"], font=f_chip,
           fill=COL_CHIP_TXT, anchor="mm")

    # buttons
    for i, name in enumerate(bd.get("buttons", [])):
        bw, bh = 92, 40
        bx = W // 2 - len(bd["buttons"]) * (bw + 20) // 2 + i * (bw + 20) + 10
        by = by0 + board_h - 66
        d.rounded_rectangle([bx, by, bx + bw, by + bh], radius=8,
                            fill=COL_BTN, outline=COL_PAD_EDGE, width=2)
        d.text((bx + bw // 2, by + bh // 2), name, font=f_btn,
               fill=COL_TEXT, anchor="mm")

    pads = {}  # label -> (cx, cy, role)

    def pad_rect(cx, cy, horizontal=False):
        if horizontal:
            return [cx - PAD_H // 2, cy - PAD_W // 2,
                    cx + PAD_H // 2, cy + PAD_W // 2]
        return [cx - PAD_W // 2, cy - PAD_H // 2,
                cx + PAD_W // 2, cy + PAD_H // 2]

    def draw_pad(label, cx, cy, side):
        role, tag = bd["highlights"].get(label, (None, None))
        pads[label] = (cx, cy, role)
        r = pad_rect(cx, cy, horizontal=(side == "bottom"))
        col = {"i2c": COL_I2C, "spi": COL_SPI}.get(role)
        if col:
            # glow ring
            glow = [r[0] - 10, r[1] - 10, r[2] + 10, r[3] + 10]
            d.rounded_rectangle(glow, radius=12,
                                outline=(col[0], col[1], col[2], 140),
                                width=6)
        d.rounded_rectangle(r, radius=7, fill=col or COL_PAD,
                            outline=COL_PAD_EDGE, width=2)

        # label + tag outside the board
        if side == "left":
            tx, ty = r[0] - 16, cy
            anchor = "rm"
        elif side == "right":
            tx, ty = r[2] + 16, cy
            anchor = "lm"
        else:  # bottom
            tx, ty = cx, r[3] + 14
            anchor = "ma"
        if col:
            d.text((tx, ty), label, font=f_label, fill=col, anchor=anchor)
            tag_txt = " " + (I2C_TAGS if role == "i2c" else SPI_TAGS)[tag]
            if side == "left":
                d.text((tx, ty), tag_txt[1:], font=f_tag, fill=col,
                       anchor="rm")
                # move label above, tag below for legibility
                d.text((tx, ty - 20), label, font=f_label, fill=col,
                       anchor="rm")
                d.text((tx, ty + 18), (I2C_TAGS if role == "i2c" else
                                       SPI_TAGS)[tag], font=f_tag, fill=col,
                       anchor="rm")
            elif side == "right":
                d.text((tx, ty - 20), label, font=f_label, fill=col,
                       anchor="lm")
                d.text((tx, ty + 18), (I2C_TAGS if role == "i2c" else
                                       SPI_TAGS)[tag], font=f_tag, fill=col,
                       anchor="lm")
            else:
                d.text((tx, ty + 18), label + " " +
                       (I2C_TAGS if role == "i2c" else SPI_TAGS)[tag],
                       font=f_tag, fill=col, anchor="ma")
        else:
            d.text((tx, ty), label, font=f_label, fill=COL_DIM, anchor=anchor)

    # side pads
    for i, label in enumerate(left):
        cy = by0 + 30 + i * pitch + (board_h - (n_side * pitch + 40)) // 2
        draw_pad(label, bx0, cy, "left")
    for i, label in enumerate(right):
        cy = by0 + 30 + i * pitch + (board_h - (n_side * pitch + 40)) // 2
        draw_pad(label, bx1, cy, "right")
    # bottom pads
    for i, label in enumerate(bottom):
        step = board_w / max(len(bottom), 1)
        cx = bx0 + step * (i + 0.5)
        draw_pad(label, cx, by1, "bottom")

    # legend
    ly = H - FOOTER_H + 30
    d.ellipse([W // 2 - 330, ly, W // 2 - 296, ly + 34], fill=COL_I2C)
    d.text((W // 2 - 284, ly + 17), "I2C  (SDA / SCL)", font=f_label,
           fill=COL_TEXT, anchor="lm")
    d.ellipse([W // 2 + 40, ly, W // 2 + 74, ly + 34], fill=COL_SPI)
    d.text((W // 2 + 86, ly + 17), "SPI  (SCK / MISO / MOSI / CS)",
           font=f_label, fill=COL_TEXT, anchor="lm")

    if bd.get("footnote"):
        d.text((W // 2, ly + 64), bd["footnote"], font=f_note,
               fill=COL_DIM, anchor="ma")

    return img, pads


def self_check(bd, img, pads):
    """Verify every highlighted pad actually rendered in its color."""
    px = img.load()
    ok = True
    for label, (role, _tag) in bd["highlights"].items():
        cx, cy, got_role = pads[label]
        r, g, b, a = px[cx, cy]
        want = COL_I2C if role == "i2c" else COL_SPI
        if got_role != role or abs(r - want[0]) > 12 or \
           abs(g - want[1]) > 12 or abs(b - want[2]) > 12:
            print(f"  !! {bd['key']}: {label} highlight wrong "
                  f"(got rgba{r,g,b,a} role={got_role}, want {want})")
            ok = False
    return ok


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    all_ok = True
    for bd in BOARDS:
        img, pads = draw_diagram(bd)
        path = os.path.join(OUT_DIR, bd["key"] + ".png")
        img.save(path)
        ok = self_check(bd, img, pads)
        all_ok = all_ok and ok
        n_hl = len(bd["highlights"])
        print(f"{bd['key']:20s} {img.size[0]}x{img.size[1]}  "
              f"{n_hl} highlights {'OK' if ok else 'FAILED'}")
    if not all_ok:
        sys.exit(1)
    print(f"\n{len(BOARDS)} diagrams written to {OUT_DIR}")


if __name__ == "__main__":
    main()
