# CD3217B12 (Apple ACE2) I2C Diagnostic Analyzer

A diagnostic tool for testing **CD3217B12** (Apple ACE2) USB-C Power Delivery controllers used in MacBook repair. The tool communicates over I2C to read device registers, validate chip health, and identify faults.

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

## Requirements

### Hardware
- USB-to-I2C adapter (one of):
  - **FTDI FT232H** (recommended - Adafruit FT232H breakout, etc.)
  - **CH341A** (cheap but needs voltage shifter for 3.3V)
  - **Linux SMBus** (Raspberry Pi, BeagleBone, etc.)
- Test fixture with:
  - 3.3V power supply for VIN_3V3
  - I2C pullup resistors (2.2kΩ to 3.3V on SDA and SCL)
  - SDA/SCL connections to the chip's I2C Port 1 (B5/A4) or Port 2 (B7/A6)

### Software
- Python 3.8+
- `pip install smbus2` (for CH341/SMBus adapters)
- `pip install pyftdi` (for FTDI FT232H)

## Installation

### Windows (GUI)

1. Install Python 3.8+ from https://www.python.org/downloads/
   - **Important:** Check "Add Python to PATH" during installation
2. Download/clone this project
3. Double-click `CD3217_Analyzer.bat` to launch the GUI
   - Or run: `python gui.py`

### Command Line

```bash
cd cd3217-analyzer
pip install -e .
# Or with FTDI support:
pip install -e ".[ftdi]"
```

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
```

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
