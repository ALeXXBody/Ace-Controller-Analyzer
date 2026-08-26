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

For **A2442** (2021 M1 Pro 14"):

| Position | Port 1 | Port 2 | Type |
|----------|--------|--------|------|
| UF400 (UPC0) | 0x38 | 0x38 | Vanilla |
| UF500 (UPC1) | 0x3F | 0x3F | Vanilla |
| UG400 (UPC2) | 0x3B | 0x3B | OTP |
| U5500 (UPC5) | 0x3A | 0x3A | OTP |

For **A2337** (2020 M1):

| Position | Port 1 | Port 2 | Type |
|----------|--------|--------|------|
| UF400 | 0x70 | - | Vanilla |
| UF500 | 0x7E | - | Vanilla |

## References

- [TPS65982 Datasheet](https://www.ti.com/lit/ds/symlink/tps65982.pdf) - Public TI reference
- [TPS65987D Host Interface TRM](https://www.ti.com/lit/ug/slvubh2b/slvubh2b.pdf) - Register map
- [Repair.wiki ACE2 Controllers](https://repair.wiki/w/Apple_ACE2_Controllers) - Community docs
- [Asahi Linux ACE Wiki](https://leo3418.github.io/asahi-wiki-build/hwusb-pd/) - Reverse engineering
- [t8012dev ACE Part 1](https://blog.t8012.dev/ace-part-1/) - Technical deep dive

## License

MIT
