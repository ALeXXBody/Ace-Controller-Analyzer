<p align="center">
  <a href="https://buymeacoffee.com/ALeXXBody" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/default-orange.png" alt="Buy Me A Coffee" height="41" width="174"></a>
</p>

# ACA — ACE Controller Analyzer

**ACE Controller Analyzer (ACA)** — I²C diagnostic tool for the Apple **ACE1/ACE2** USB-C Power Delivery controllers (CD3215, CD3217, CD3218 — TI TPS6598x-family) used in MacBook repair, 2016-present (Intel T2 + Apple Silicon).

ACA communicates over I²C to read device registers, validate chip health, decode identity/OTP data, and identify faults — for ACE1 (CD3215) and ACE2 (CD3217/CD3218) controllers across the whole MacBook lineup.

**Available as:** Windows GUI application, CLI tool, or Python library.

## What It Does

- **Scans I2C bus** for all connected devices, identifying ACE2 controllers
- **Reads key registers** (Vendor ID, Device ID, Mode, Type, etc.)
- **Validates chip health** against known-good values from TPS65982 reference
- **Classifies faults** (no response, wrong VID, stuck in boot, corrupted registers)
- **Calculates health scores** (0-100) for quick triage
- **Identifies chip type** (vanilla vs OTP-ed) based on I2C address
- **Decodes strap configuration** for ADDR/CNTL1/CNTL2 pin settings
- **Logs results** to JSON/CSV for batch tracking
- **GUI application** with dark theme, real-time register viewer, and batch testing
- **Board tab (MacBook reference)**: per-model guide for where to connect
  the adapter to each logic board's CD3217 I2C bus (Intel T2 + Apple Silicon)

## Requirements

### Hardware
- USB-to-I2C adapter (one of):
  - **FTDI FT232H** (recommended - Adafruit FT232H breakout, etc.)
  - **CH341A** (cheap but needs voltage shifter for 3.3V)
  - **Linux SMBus** (Raspberry Pi, BeagleBone, etc.)
  - **ACA bridge board** (RP2040/Pico or ESP32 running the firmware —
    acts as a USB-I2C bridge over its USB port; no FTDI needed)
- Test fixture with:
  - 3.3V power supply for VIN_3V3
  - I2C pullup resistors (2.2kΩ to 3.3V on SDA and SCL)
  - SDA/SCL connections to the chip's I2C Port 1 (B5/A4) or Port 2 (B7/A6)

### Software
- Python 3.8+
- `pip install customtkinter` (modern dark-themed GUI)
- `pip install smbus2` (for CH341/SMBus adapters)
- `pip install pyftdi` (for FTDI FT232H)
- `pip install pyserial` (for a ACA bridge board / USB bridge)
- `pip install pyinstaller` (optional, to build .exe)

## Installation

### Option 1: Download the installer (easiest)

1. Go to **[Releases](https://github.com/ALeXXBody/cd3217-analyzer/releases)** and download `CD3217B12_Analyzer_Setup.exe`
2. Run it — installs to your user Programs folder (no admin prompt), adds a
   Start Menu entry and optional Desktop shortcut, and registers an uninstaller.
3. Launch **CD3217B12 Analyzer** from the Start Menu. No Python needed.

### Option 2: Portable (no install)

1. Download `CD3217B12_Portable.zip` from **[Releases](https://github.com/ALeXXBody/cd3217-analyzer/releases)**
2. Unzip anywhere and run `CD3217B12_Analyzer.exe` directly — no console
   window, no install, leaves nothing on the machine.

### Option 3: Run from source (first time only)

1. Install **Python 3.8+** from https://www.python.org/downloads/
   - **CHECK THE BOX "Add Python to PATH"** (very important!)
2. Download this repo
3. Double-click **`setup.bat`** — installs everything + creates Desktop shortcut
4. From then on, just double-click the Desktop shortcut

### Option 4: Build the .exe yourself

Double-click `build.bat`. Takes ~1 minute. Output goes to `dist\CD3217B12_Analyzer\`
(windowed, no console) plus `CD3217B12_Portable.zip`. With
[Inno Setup 6](https://jrsoftware.org/isinfo.php) installed you can also
compile the proper installer: `ISCC.exe installer.iss`.

## UART Sniffing (ACE2 firmware bus)

Every board can passively listen to a UART line — RX-only, never driving the
target. Main use: the ACE2 Master→Slave **firmware-download bus** on MacBook
boards (`UPC_TA_UART_TX/RX` test points): no traffic at power-on = master
side problem; traffic but dead port = slave chip. Also captures the firmware
payload slaves receive.

- **App**: connect the board → **UART** tab → baud or **Auto-detect** →
  Start; save the log to file
- **CLI**: `--uart-autobaud` / `--uart-sniff auto|BAUD`
- **WiFi boards**: web UI → **UART Sniff** tab — fully standalone
- **RX pins**: Pico family GP1, ESP32-S3 GPIO4, C3/C6 GPIO1, classic GPIO16
  (amber in the Adapter tab diagram)

⚠ The ACE2 UART bus is expected to be 1.8V — sniff through a level shifter.
See **[docs/uart-sniff.md](docs/uart-sniff.md)** for test points, wiring and
the diagnosis table.

## Updating

### App

The app checks GitHub for new releases automatically at startup (silent) —
or use the **Check updates** button in the top bar. When a new version is
found, one click downloads and applies it:

- **Installed**: the new installer runs silently, closes the app, replaces
  it and restarts it (per-user install, no admin prompt).
- **Portable**: the app downloads the new package, replaces its own folder
  and restarts itself.
- **From source**: the releases page opens in your browser.

### Board firmware

Boards report their firmware version on connect (Adapter tab shows it). When a
board runs an older firmware than the current release, the app pops up and
offers to update it — the right image is downloaded automatically and
flashed with no wiring changes:

- **Pico/RP2040 boards**: the app reboots the board into BOOTSEL and copies
  the UF2 (flash-verified), then the board restarts.
- **ESP32 boards**: the firmware streams over USB and is written to the OTA
  partition, verified, then the board restarts itself.

CLI equivalent: `python -m cd3217_analyzer --board-update [--port COMx]`.
Note: first-generation firmware (pre-0.6.1) doesn't report a version — it
shows as "unknown" and is offered the update.

### Export board data (for upstream analysis)

The **Export data** button (top bar) collects the connected board's data into a
single self-describing JSON bundle named after the MacBook/board model, saves
it locally to `samples/<Name>.json`, and optionally pushes it to the project's
GitHub repository under the top-level `samples/` folder (easy to browse) so we
can study these chips and improve the app.

- **What it captures** (pick a checklist per export): device INFO frame,
  full register dump, OTP scan, SPI flash ROM, UART capture, and the full
  diagnostic report.
- **GitHub push**: requires a Personal Access Token with `contents:write`
  scope. The token is stored only locally (owner-only file, or the
  `CD3217_GH_TOKEN` env var) — never embedded in the app. Pushing writes
  `samples/<Name>.json` on the repo's main branch (in `samples/`).

CLI equivalent:

```
python -m cd3217_analyzer --export A2141 --with info,registers,otp,report
python -m cd3217_analyzer --export A2141 --with info,registers --push
python -m cd3217_analyzer --set-token ghp_xxx
```

> ⚠ Flash ROM / OTP dumps can contain board-specific firmware — export those
> only when you intend to share the data.

## GUI Usage

Double-click `CD3217_Analyzer.bat` or run `python gui.py`:

```
+================================================================+
|  CD3217B12 (Apple ACE2) I2C Diagnostic Analyzer                |
+================================================================+
| [Adapter: FTDI FT232H v] [Bus: 1] [Connect] ● Connected       |
+================================================================+
| Devices (3)            |  Overview   Registers  Batch  Straps  |
| +---------+------+----+| +-------------------------------+    |
| | Addr    |Health|Score||| HEALTHY              95/100   |    |
| | 0x38    |PASS  | 95  ||| Device is responding correctly |    |
| | 0x3F    |PASS  | 92  |||-------------------------------|    |
| | 0x3B    |FAIL  |  0  ||| Address:  0x38                |    |
| +---------+------+----+||| VID:      0x0451 (TI)          |    |
| [Diagnose] [Dump] [Batch]||| Mode:    APP                  |    |
| Address: [0x38] [Diag] ||| Type:    I2C                   |    |
|                         ||+-------------------------------+    |
+================================================================+
| Ready                                         v1.0.0           |
+================================================================+
```

### GUI Features

- **Connection Panel** - Select adapter (auto-detect, FTDI, SMBus, CH341), connect/disconnect
- **Device List** - Color-coded scan results (green=pass, yellow=warn, red=fail)
- **Overview Tab** - Big health score, device info, fault details
- **Registers Tab** - Live register dump with hex/decoded values, copy to clipboard
- **Batch Tab** - Multi-iteration testing with progress bar, CSV export
- **Strap Decoder Tab** - Calculate ADDR/CNTL1/CNTL2 resistor values from addresses
- **Log Tab** - Real-time operation log with timestamps
- **File Menu** - Save JSON reports, export CSV, keyboard shortcuts

## Usage

### Interactive Mode
```bash
python -m cd3217_analyzer
# Or:
cd3217-analyzer
```

### Scan I2C Bus
```bash
python -m cd3217_analyzer --scan
```

### Diagnose a Single Chip
```bash
python -m cd3217_analyzer --diagnose 0x38
```

### Full Diagnostic Report
```bash
python -m cd3217_analyzer --full
python -m cd3217_analyzer --full -o report.json
```

### Register Dump
```bash
python -m cd3217_analyzer --dump 0x38
```

### Batch Testing
```bash
python -m cd3217_analyzer --batch -n 5
python -m cd3217_analyzer --batch -n 10 -o results.csv
```

### Decode Strap Configuration
```bash
python -m cd3217_analyzer --strap 0x38 0x2F
```

### Specify Adapter
```bash
python -m cd3217_analyzer --adapter ftdi
python -m cd3217_analyzer --adapter smbus --bus 1
python -m cd3217_analyzer --adapter usb --port COM5   # ACA bridge board (USB bridge)
```

### Flash a board (RP2040/Pico or ESP32)
Works even when the app is otherwise disconnected — it just needs the board on USB.
```bash
# Pico-family — hold BOOTSEL, plug in, then:
python -m cd3217_analyzer --flash-board firmware_esp32/dist/cd3217_pico.uf2

# ESP32 — needs a COM port:
python -m cd3217_analyzer --flash-board firmware_esp32/dist/cd3217_esp32s3.bin --port COM5
```
Or use the **"Flash board"** button in the GUI (pick the `.uf2` / `.bin`).

### Custom Address List
```bash
python -m cd3217_analyzer --addresses 0x38,0x3F,0x3B,0x2F --scan
```

## I2C Wiring

### Pin Connections (CD3217B12 BGA)

| Function | Port 1 (Debug/TBT) | Port 2 (SMC) |
|----------|-------------------|---------------|
| SDA      | B5                | B7            |
| SCL      | A4                | A6            |
| IRQ      | D7                | C8            |
| VIN_3V3  | Power input       | Power input   |
| GND      | Ground            | Ground        |

### Test Fixture Setup

For testing a loose CD3217B12 chip (not on a board):

```
CD3217B12 Test Fixture:
├── VIN_3V3 (pin per datasheet) → 3.3V supply
├── GND → Ground
├── SDA (B5 for Port1) → I2C SDA with 2.2kΩ pullup to 3.3V
├── SCL (A4 for Port1) → I2C SCL with 2.2kΩ pullup to 3.3V
└── CNTL1 (B15) → Pull to 3.3V or GND for address config
```

## SPI Flash (External ROM)

The CD3217B12 loads firmware from an external SPI flash chip. Every CD3217-Analyzer
board (ESP32 and RP2040/Pico family) can act as a **standalone SPI flasher** through
its hardware SPI pins — no FTDI FT232H needed. WiFi boards do it entirely from the
browser; wired boards do it through the Windows app/CLI over USB.

> **Voltage:** M1/M2-era Mac flash runs at 1.8V — use a level-shifting shield.
> See **[docs/spi-shield.md](docs/spi-shield.md)** for the pin map, clip wiring
> and shield design.

### Flash Chip Details

| Board Position | Typical Flash | Size | Notes |
|----------------|---------------|------|-------|
| Near CD3217B12 | Winbond W25Q80 | 1MB | PD firmware + config |
| Some boards | GD25Q80 / IS25LP080 | 1MB | Same pinout, compatible |

### Option A: Standalone with a board (recommended)

- **WiFi boards** (ESP32-S3/C3/classic, Pico W / Pico 2 W): join the
  `cd3217-analyzer` AP → `http://192.168.4.1` → **SPI Flash** tab →
  detect / read to file / write with verify / erase — all from the browser.
- **Wired boards** (Pico 1/2, RP2040-Zero): connect via USB, then in the app
  connect through **USB Bridge (board)** first — the Flash tab automatically
  uses the board instead of FTDI.

### Option B: FTDI FT232H dongle

```
FT232H ADBUS0 = SCK   → Flash CLK
FT232H ADBUS1 = MOSI  → Flash DI (SI)
FT232H ADBUS2 = MISO  → Flash DO (SO)
FT232H ADBUS3 = CS#   → Flash CS#
FT232H GND             → Flash GND
FT232H 3.3V (if needed) → Flash VCC
```

### CLI Usage (board on COM5, or omit --adapter for FTDI)

```bash
# Detect flash chip via the board
python -m cd3217_analyzer --adapter usb --port COM5 --flash-detect

# Dump flash to file
python -m cd3217_analyzer --adapter usb --port COM5 --flash-read dump.bin

# Write firmware to flash (erases first!)
python -m cd3217_analyzer --adapter usb --port COM5 --flash-write firmware.bin

# Erase entire flash
python -m cd3217_analyzer --adapter usb --port COM5 --flash-erase

# Full restore: erase + write + verify
python -m cd3217_analyzer --adapter usb --port COM5 --flash-restore firmware.bin
```

### GUI Usage

Go to the **Flash** tab:
1. Click **Connect SPI** to open FTDI in SPI mode
2. Click **Detect Chip** to identify the flash
3. **Read Flash** — dump full contents to .bin file
4. **Write File** — erase + write a firmware file
5. **Erase Chip** — wipe the flash
6. **Restore** — erase + write + verify

### Workflow: Clone Firmware from Working Board

```bash
# 1. Connect FTDI to flash on WORKING board
python -m cd3217_analyzer --flash-read good_board.bin

# 2. Connect FTDI to flash on BROKEN/locked board
python -m cd3217_analyzer --flash-restore good_board.bin
```

### Notes

- **I2C and SPI cannot run simultaneously** on the same FT232H
- Disconnect I2C before connecting SPI (close the app, rewire, reopen)
- The flash chip is typically in SOIC-8 or WSON-8 package
- Use a SOIC-8 test clip for in-circuit reading without desoldering
- Reading is non-destructive — safe to dump from any board

## Known I2C Addresses

| Address | Description |
|---------|-------------|
| 0x38    | ACE2 vanilla (ADDR=GND, CNTL1=0, CNTL2=1) |
| 0x3F    | ACE2 vanilla (ADDR=float, CNTL1=1, CNTL2=1) |
| 0x3A    | ACE2 OTP-ed (Apple typical) |
| 0x3B    | ACE2 OTP-ed (Apple typical) |
| 0x3C    | ACE2 OTP-ed (Apple typical) |
| 0x2F    | ACE2 Port2 (ADDR=float) |
| 0x6B    | ACE2 All-call / Bank |

## Health Scoring

| Score | Meaning |
|-------|---------|
| 90-100 | Excellent - all checks pass |
| 70-89  | Good - minor issues detected |
| 50-69  | Warning - some registers unexpected |
| 25-49  | Poor - significant issues |
| 0-24   | Critical - device likely faulty |

## Fault Types

| Fault | Description |
|-------|-------------|
| NO_RESPONSE | Device does not ACK on I2C |
| WRONG_VID | Vendor ID != 0x0451 (TI) |
| WRONG_MODE | Device not in expected mode |
| BOOT_FAILED | Stuck in BOOT mode |
| CORRUPTED_REGISTERS | Multiple registers return 0x00 or 0xFF |
| I2C_ERROR | Communication error during register read |
| ROM_MISSING | May need external SPI ROM |

## MacBook Board Reference

### 2-Port Models (M1 / M2 / Intel)

**A2337** — MacBook Air M1 (2020) — 820-02016

| Position | Address | Type | Port |
|----------|---------|------|------|
| UF400 | 0x70 | Vanilla (strap) | 2 |
| UF500 | 0x7E | Vanilla (strap) | 2 |

**A2338** — MacBook Pro 13" M1 (2020) — 820-02020

| Position | Address | Type | Port |
|----------|---------|------|------|
| UF400 | 0x38 | Vanilla (strap) | 2 |
| UF500 | 0x3F | Vanilla (strap) | 2 |

**A2179** — MacBook Air 13" i5 (2020, Intel) — 820-01996

| Position | Address | Type | Port |
|----------|---------|------|------|
| UF400 | 0x70 | Vanilla (strap) | 2 |
| UF500 | 0x7E | Vanilla (strap) | 2 |

**A2289** — MacBook Pro 13" i5 (2020, Intel) — 820-01987

| Position | Address | Type | Port |
|----------|---------|------|------|
| UF400 | 0x38 | Vanilla (strap) | 2 |
| UF500 | 0x3F | Vanilla (strap) | 2 |

**A2251** — MacBook Pro 13" i5 (2020, Intel, 4-port) — 820-01958

| Position | Address | Type | Port |
|----------|---------|------|------|
| UF400 | 0x38 | Vanilla (strap) | 2 |
| UF500 | 0x3F | Vanilla (strap) | 2 |

### 4-Port Models (M1 Pro/Max)

**A2442** — MacBook Pro 14" M1 Pro/Max (2021) — 820-02100

| Position | Address | Type | Port | Notes |
|----------|---------|------|------|-------|
| UB300 | 0x20 | OTP | 1 | Debug/TBT |
| UB400 | 0x74 | OTP | 1 | Debug/TBT |
| UF500 | 0x39 | Strap (GND) | 2 | SMC |
| UF600 | 0x10 | Strap (GND) | 2 | SMC |

**A2485** — MacBook Pro 16" M1 Pro/Max (2021) — 820-02100 (same board)

| Position | Address | Type | Port | Notes |
|----------|---------|------|------|-------|
| UB300 | 0x20 | OTP | 1 | Debug/TBT |
| UB400 | 0x74 | OTP | 1 | Debug/TBT |
| UF500 | 0x39 | Strap (GND) | 2 | SMC |
| UF600 | 0x10 | Strap (GND) | 2 | SMC |

### 4-Port Models (M2 Pro/Max)

**A2779** — MacBook Pro 14" M2 Pro/Max (2023) — 820-02230

| Position | Address | Type | Port | Notes |
|----------|---------|------|------|-------|
| UB300 | 0x20 | OTP | 1 | Debug/TBT |
| UB400 | 0x74 | OTP | 1 | Debug/TBT |
| UF500 | 0x39 | Strap (GND) | 2 | SMC |
| UF600 | 0x10 | Strap (GND) | 2 | SMC |

**A2780** — MacBook Pro 16" M2 Pro/Max (2023) — 820-02230

| Position | Address | Type | Port | Notes |
|----------|---------|------|------|-------|
| UB300 | 0x20 | OTP | 1 | Debug/TBT |
| UB400 | 0x74 | OTP | 1 | Debug/TBT |
| UF500 | 0x39 | Strap (GND) | 2 | SMC |
| UF600 | 0x10 | Strap (GND) | 2 | SMC |

### T2 Models

**A2141** — MacBook Pro 16" i9 (2019, T2) — 820-01997

| Position | Address | Type | Notes |
|----------|---------|------|-------|
| UB300 | 0x50 | OTP | Port 1 — Left |
| UB400 | 0x28 | OTP | Port 1 — Left |
| UB700 | 0x3C | OTP | Port 2 — Right |
| UB800 | 0x30 | OTP | Port 2 — Right |

> All OTP — addresses burned at factory. Cannot use strap-only scan.

**A2159** — MacBook Pro 13" i5 (2019, T2) — 820-01843

| Position | Address | Type | Notes |
|----------|---------|------|-------|
| UB300 | 0x50 | OTP | Port 1 |
| UB400 | 0x28 | OTP | Port 1 |

### Using Model Selection

**CLI:**
```bash
# List all supported models
python -m cd3217_analyzer --list-models

# Diagnose with model-specific addresses
python -m cd3217_analyzer --model A2442 --scan
python -m cd3217_analyzer --model A2337 --diagnose 0x70
```

**GUI:** Select your MacBook model from the dropdown in the top bar. The Strap Decoder reference table and Batch Test addresses will update automatically.

## OTP Reverse Engineering

The OTP (One-Time Programmable) fuse map on CD3217B12 is undocumented. This tool provides scanner/diff capabilities to help reverse-engineer it.

### How It Works

1. **Scan a vanilla chip** (never programmed — all OTP fuses intact) → save as `vanilla.json`
2. **Scan an OTP-ed chip** (pulled from a working/locked board) → save as `otp_ed.json`
3. **Diff the two dumps** — registers that differ are likely OTP-backed
4. **Correlate** — the OTP bits that changed should explain the I2C address, device ID, etc.

### CLI Usage

```bash
# Scan full OTP register space (0x00-0x7F)
python -m cd3217_analyzer --otp-scan 0x38

# Export scan to file
python -m cd3217_analyzer --otp-scan 0x38 -o vanilla.json
python -m cd3217_analyzer --otp-scan 0x20 -o otp_ed.json

# Compare two dumps
python -m cd3217_analyzer --otp-diff vanilla.json otp_ed.json

# Import and view a saved dump
python -m cd3217_analyzer --otp-import dump.json
```

### GUI Usage

Go to the **OTP Scanner** tab:
1. Enter the I2C address and click **Scan OTP**
2. The full register dump appears in the left panel
3. Click **Import Dump** to load a previously saved dump
4. Click **Diff Two Dumps** to select two files and compare them
5. Different registers (likely OTP-backed) are highlighted in red

### What to Expect

| Register Range | Likely Purpose |
|----------------|---------------|
| 0x00-0x0F | Identification (VID, DID, Mode) — may be OTP-backed |
| 0x10-0x1F | Configuration registers — some OTP-backed |
| 0x20-0x3F | PD capabilities — likely OTP-backed |
| 0x40-0x6F | OTP/fuse region — highest density of OTP content |
| 0x70-0x7F | Extended/Apple-specific registers |

### Tips

- **Use iCloud-locked boards** as donors — they're already bricked
- **Label your dumps** — e.g., "A2442_UB300_vanilla" vs "A2442_UB300_otp"
- **Compare same-position chips** across boards — OTP for UB300 should be identical across A2442 boards
- **Power cycle after diff** — some OTP registers only take effect after reboot

## References

- [TPS65982 Datasheet](https://www.ti.com/lit/ds/symlink/tps65982.pdf) - Public TI reference
- [TPS65987D Host Interface TRM](https://www.ti.com/lit/ug/slvubh2b/slvubh2b.pdf) - Register map
- [Repair.wiki ACE2 Controllers](https://repair.wiki/w/Apple_ACE2_Controllers) - Community docs
- [Asahi Linux ACE Wiki](https://leo3418.github.io/asahi-wiki-build/hwusb-pd/) - Reverse engineering
- [t8012dev ACE Part 1](https://blog.t8012.dev/ace-part-1/) - Technical deep dive

## Support

If this tool helps you with board repair, consider buying me a coffee:

[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-ffdd00?style=flat&logo=buy-me-a-coffee&logoColor=black)](https://buymeacoffee.com/alexxbody)

## License

MIT
