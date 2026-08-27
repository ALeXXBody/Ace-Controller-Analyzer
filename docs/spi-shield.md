# SPI Flash Shield — Standalone Flashing from Any Board

Every CD3217-Analyzer board (ESP32 and RP2040/Pico family) has hardware SPI
and now acts as a **standalone SPI flasher**: no PC-side FT232H dongle needed.
The board drives the target flash chip through a simple level-shifting
shield + SOIC-8 clip.

```
Board SPI pins ──► level shifter ──► SOIC-8 clip ──► flash chip
                                              └──► 3.3V / 1.8V target supply
```

> **Voltage warning:** M1/M2-era Mac flash chips run at **1.8V** — wiring
> 3.3V GPIOs directly can destroy the chip. Pre-T2 Macs are usually 3.3V.
> Measure first, and always level-shift when in doubt. A bidirectional
> shifter (TXB0108 / BSS138-based) works at the 2 MHz SPI clock used here.

## Board SPI pin map

The pins are compile-time defaults (`PIN_SPI_*` in `platformio.ini`) — change
and rebuild if your wiring differs.

| Board            | SCK | MISO | MOSI | CS  | Notes |
|------------------|-----|------|------|-----|-------|
| Pico 1 / Pico 2  | GP14 | GP12 | GP15 | GP13 | SPI1, phys pins 19/16/20/17 — one wiring serves the whole family |
| Pico W / Pico 2 W| GP14 | GP12 | GP15 | GP13 | same as above |
| RP2040-Zero      | GP12 | GP12→GP12 | GP15 | GP13 | GP12–GP15 block on the top edge |
| ESP32-S3 DevKit  | 12  | 13   | 11   | 10  | FSPI |
| ESP32-C3 SuperMini | 4 | 6    | 5    | 7   | I2C stays on 8/9 |
| ESP32 classic    | 18  | 19   | 23   | 5   | VSPI |
| ESP32-C6-Zero    | 12  | 13   | 11   | 10  | |

I2C (SDA/SCL) is unchanged — SPI is additional wiring, and both can be used
through the same USB connection.

## Clip wiring (SOIC-8 flash)

| Clip pin | Signal | Board pin |
|----------|--------|-----------|
| 1        | CS#    | CS        |
| 2        | MISO   | MISO      |
| 3        | WP#    | (leave, pulled up on target) |
| 4        | GND    | GND       |
| 5        | MOSI   | MOSI      |
| 6        | SCK    | SCK       |
| 7        | HOLD#  | (leave, pulled up on target) |
| 8        | VCC    | target voltage (via shifter supply) |

The chip must be powered at **its** voltage (3.3V or 1.8V) — power it from
the target board or a suitable regulator, not blindly from the analyzer
board's 3.3V.

## Using it

### WiFi boards (ESP32-S3/C3/classic, Pico W / Pico 2 W) — fully standalone

1. Flash the board firmware (`cd3217_<board>.bin` / `.uf2`)
2. Join the `cd3217-analyzer` WiFi AP (password `cd3217analyzer`)
3. Open `http://192.168.4.1` → **SPI Flash** tab
4. **Detect chip** → **Read whole chip** (downloads a `.bin`) or
   **Write** a `.bin` (erase + program + verify, with progress bars) or
   **Erase whole chip**

### Wired boards (Pico 1/2, RP2040-Zero) — via the Windows app / CLI

Connect the board over USB, then use the app's flash tools — when the app is
connected through **USB Bridge (board)**, the Flash tab automatically uses
the board instead of FTDI.

CLI examples (board on COM5):

```
CD3217B12_Analyzer.exe --adapter usb --port COM5 --flash-detect
CD3217B12_Analyzer.exe --adapter usb --port COM5 --flash-read dump.bin
CD3217B12_Analyzer.exe --adapter usb --port COM5 --flash-write firmware.bin
CD3217B12_Analyzer.exe --adapter usb --port COM5 --flash-erase
```

### Protocol (bridge cmd 0x10)

Over the USB bridge, SPI is a single generic command:

```
0x10 SPIXFR  req: [tx bytes...] (≤240)  resp: [status][rx bytes...]
```

A full-duplex exchange with CS wrapped around it. The host-side
`cd3217_analyzer/spi_bridge.py` uses it to run the *entire* existing
`SPIFlash` driver (JEDEC detect, read, erase, page program, verify) on the
board — one command, no duplicated logic. SPI runs at 2 MHz / mode 0.
