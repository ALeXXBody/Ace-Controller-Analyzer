# CD3217-Analyzer — ESP32 firmware (M1 spike)

Standalone bench tool for the CD3217B12 (Apple ACE2). Currently Milestone 1:
WiFi web UI + on-device I2C scan. See `../docs/esp32-unit-design.md` for the
full roadmap.

## Build

```sh
pio run -e esp32s3      # primary target (ESP32-S3)
pio run -e esp32c3      # C3 SuperMini
pio run -e esp32        # classic ESP32 DevKit
# ESP32-C6 is NOT supported by Arduino in espressif32 7.0.1 — needs IDF.
```

Each `pio run -e <env> -t upload` flashes that board.

## M1 web UI

1. Upload firmware to an ESP32-S3.
2. Power it; it creates an access point:
   - SSID: `cd3217-analyzer`
   - pass: `cd3217analyzer`
3. Connect to that AP, open **http://cd3217.local** (or `192.168.4.1`).
4. Press **Scan I2C bus** — lists ACK-ing addresses 0x08–0x77 and flags known
   ACE2 ones.

Endpoints:
- `GET /` — web UI
- `GET /api/scan` — JSON `{addresses:[...], known:{...}, ms:n}`
- `GET /api/health` — JSON board/I2C/IP status

## Wiring (I2C to CD3217 breakout)

| Signal | S3 (GPIO) | C3 | classic | Note |
|--------|-----------|----|---------|------|
| SDA    | 8         | 8  | 21      | + pull-up to 3.3V |
| SCL    | 9         | 9  | 22      | + pull-up to 3.3V |
| GND    | —         | —  | —       | common ground |

The I2C pins are set per-env via `-DI2C_SDA_GPIO=x` / `-DI2C_SCL_GPIO=y`
in `platformio.ini`.
