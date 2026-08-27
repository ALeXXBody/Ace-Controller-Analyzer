# CD3217-Analyzer — ESP32 / RP2040 firmware (M1 spike)

Standalone bench tool for the CD3217B12 (Apple ACE2). Currently Milestone 1:
WiFi web UI (WiFi-capable boards) + on-device I2C scan. See
`../docs/esp32-unit-design.md` for the full roadmap.

Supports the **ESP32** and **Pico/RP2040** families from one Arduino `main.cpp`.

## Build targets

| env            | Board            | WiFi | I2C SDA/SCL | Web UI |
|----------------|------------------|------|-------------|--------|
| `esp32s3`      | ESP32-S3 (primary) | ✅ | 8 / 9        | ✅ |
| `esp32c3`      | ESP32-C3 SuperMini | ✅ | 8 / 9        | ✅ |
| `esp32`        | ESP32 DevKit     | ✅ | 21 / 22      | ✅ |
| `rp2040_zero`  | RP2040-Zero (Waveshare) | ❌ | 4 / 5 | (M2) |
| `pico`          | Pico 1 (RP2040)  | ❌ | 4 / 5        | (M2) |
| `pico_w`        | Pico W (RP2040)  | ✅ | 4 / 5        | ✅ |
| `pico2`        | Pico 2 (RP2350)  | ❌ | 4 / 5        | (M2) |
| `pico2w`       | Pico 2 W (RP2350) | ✅ | 4 / 5        | ✅ |

> ESP32-C6 is NOT supported by Arduino in espressif32 7.0.1 — needs ESP-IDF.

Build / flash:

```sh
pio run -e esp32s3            # compile
pio run -e rp2040_zero        # compile (wired)
pio run -e pico_w -t upload   # build + flash over USB (drag UF2 or picotool)
```

Wired RP2040 boards have no WiFi, so the M1 build runs I2C + serial only; they
become USB-I2C bridges in M2 (every RP2040/RP2350 has native USB).

## M1 web UI (WiFi boards)

1. Upload firmware to a WiFi board (e.g. `esp32s3`, `pico_w`, `pico2w`).
2. Power it; it creates an access point:
   - SSID: `cd3217-analyzer`
   - pass: `cd3217analyzer`
3. Connect to that AP, open **http://cd3217.local** (ESP32; Pico uses the AP IP
   until mDNS lands — check the serial log).
4. Press **Scan I2C bus** — lists ACK-ing addresses 0x08–0x77 and flags known
   ACE2 ones.

Endpoints:
- `GET /` — web UI
- `GET /api/scan` — JSON `{addresses:[...], known:{...}, ms:n}`
- `GET /api/health` — JSON board/I2C/IP status

## Wiring (I2C to CD3217 breakout)

| Signal | S3 | C3 | classic | Pico family | Note |
|--------|----|----|---------|-------------|------|
| SDA    | 8  | 8  | 21      | 4           | + pull-up to 3.3V |
| SCL    | 9  | 9  | 22      | 5           | + pull-up to 3.3V |
| GND    | —  | —  | —       | —           | common ground |

Pins are set per-env via `-DI2C_SDA_GPIO=x` / `-DI2C_SCL_GPIO=y` in
`platformio.ini`. On RP2040 the code calls `Wire.setSDA/setSCL` then
`Wire.begin()` (API differs from ESP32).
