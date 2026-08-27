# CD3217-Analyzer: ESP32 Standalone Unit — Design & Roadmap

> Status: **Milestone 1 spike DONE** (firmware compiles; no hardware flash yet)
> Session date: 2026-08-27
>
> **M1 spike results (28 Aug 2026):**
> - `firmware_esp32/` builds with **Arduino** framework on **ESP32-S3, C3, and
>   classic ESP32** (verified via `pio run`).
> - **ESP32-C6 does NOT build with Arduino** in espressif32 7.0.1 →
>   "This board doesn't support arduino framework!". C6 needs **ESP-IDF**
>   (as VoiceSentry's C6 firmware already uses) or a newer platform.
>   Tracked as a known limitation — C6 stays a secondary target.
> - Firmware: WiFi SoftAP `cd3217-analyzer` + mDNS `cd3217.local` + web UI
>   (`/`) + `/api/scan` (real I2C scan 0x08–0x77) + `/api/health`.

---

## 1. Vision

Turn the GUI/FTDI app into a **two-halves** system:

1. **Existing Windows app** (`gui.py`, `cd3217_analyzer/*`) — the full-featured analysis tool.
2. **NEW: ESP32 standalone unit** — a pocket I2C bench tool that:
   - hosts its own **Wi-Fi web UI** (phone/laptop), no PC needed,
   - **reads and writes OTP** on CD3217B12 (ACE2) chips via I2C,
   - keeps a **large-flash OTP library on-board** (survives power loss),
   - is **flashed/updated and its OTP library synced by the Windows app** over USB.

The Windows app gains a **4th hardware backend** (ESP32-as-USB-I2C-bridge) that
implements the *same* `I2CAdapter` interface, so all existing scan/diagnose/
analyze code runs against it with **zero changes** to the logic layer.

---

## 2. Board Selection (target "all boards that can take it")

Goal: one firmware that runs across common ESP32 variants, and degrades
gracefully on smaller boards.

| Board | Flash | USB-native | I2C | Arduino build (M1) | Verdict |
|-------|-------|-----------|-----|--------------------|---------|
| **ESP32-S3** 16/32MB | 16–32MB | ✅ | HW (2×) | ✅ compiles | **⭐ primary target** |
| ESP32-C6 | 4MB | ✅ | HW (1×) | ❌ needs IDF/newer platform | Secondary — see below |
| ESP32-C3 | 4MB | ✅ | HW | ✅ compiles | Supported |
| ESP32 (classic) | 4MB | ❌(w/ USB-UART) | HW | ✅ compiles | Supported |

**"Can take it" = has hardware I2C + ≥4MB flash + WiFi.** S3/C3/classic all build
with the shared Arduino firmware today. **C6** needs an espidf port or an
espressif32 platform bump; it was kept in `platformio.ini` but is not an
Arduino build target yet.

**"Extra space for OTP"** = a dedicated MTD/partition (`otpstore`) carved from
flash. Sizes:

- 32MB board → **16MB OTP partition** (easily tens of thousands of dumps)
- 16MB board → **~8MB OTP partition**
- 4MB board → **~1.5MB OTP partition** (still a few thousand dumps)

Because a single full OTP dump is **<4KB** (registers 0x00–0x7F × 4 bytes),
even the smallest partition holds far more dumps than a repair tech will ever
collect.

---

## 3. System Architecture

```text
                    ┌─────────────────────────────────────┐
                    │          WINDOWS APP (existing)     │
                    │  gui.py / cli.py / analyzer logic   │
                    └───────────────┬─────────────────────┘
                                    │  I2CAdapter ABC
                    ┌───────────────┴─────────────────────┐
                    │         Backends (swappable)        │
                    ├───────────────┐ ┌──────────────────┤
                    │ FTDI/CH341/   │ │ ESP32Adapter USB  │  <-- new
                    │ SMBus (now)   │ └────────┬─────────┘
                    └───────────────┘          │ USB / CDC / BLE?
                                               │
              ┌────────────────────────────────┴──────────────┐
              │                  ESP32 UNIT        (firmware) │
              │ ┌────────────┐ ┌────────────┐ ┌────────────┐  │
              │ │ I2C master │ │  USB bridge│ │ WiFi AP +  │  │
              │ │ (HW)  →    │ │  (CDC)     │ │ web server │  │
              │ │   CD3217   │ │            │ │  (AsyncTCP)│  │
              │ └────────────┘ └─────┬──────┘ └─────┬──────┘  │
              │               ┌──────┴──────────────┴───┐     │
              │               │  OTP library (partition) │     │
              │               │  + JSON index           │     │
              │               └─────────────────────────┘     │
              └────────────────────────────────────────────────┘
```

**Logical separation of IO:**

- **USB path** → used by the Windows app as a plain USB-I2C bridge
  (flash/update the ESP, and use it as an adapter).
- **Wi-Fi path** → the ESP's own web UI + OTP library management + sync.

Both paths talk through one **command transport** built on a tiny framed
protocol (same commands, two carriers).

---

## 4. Firmware design (Arduino / PlatformIO)

Chosen toolchain: **PlatformIO + arduino-esp32** (installed
`framework-arduinoespressif32`). The M1 spike builds on S3/C3/classic with a
single source tree; per-board envs differ only by GPIOs + partitions.
**C6** is the one board that needs ESP-IDF here (no Arduino support in
espressif32 7.0.1), so it stays a secondary target.

### 4.1 Modules

| Module | Responsibility |
|--------|----------------|
| `main.cpp` | setup, WiFi, task orchestration |
| `i2c_master` | Hardware I2C driver; scan/read/write/OTP helpers |
| `usb_bridge` | USB **CDC** serial implementing the framed protocol |
| `webui` | Async web server (SPIFFS/LittleFS) — scan, read, write, library browser |
| `otpstore` | LittleFS wrapper over the `otpstore` partition; JSON index + raw dumps |
| `proto` | Frame encode/decode shared between USB and web paths |
| `wifi_mgr` | SoftAP + STA, mDNS (`cd3217.local`), reconnect logic |

### 4.2 Protocol (framed)

Tiny length-prefixed binary frames, identical over USB and WebSocket:

```
[0xA5][len:1][cmd:1][addr:1][reg:1][datalen:1][data...][cksum:1]
```

Commands (`cmd`):
| cmd | name | req | resp |
|-----|------|-----|------|
| 0x01 | SCAN | – | list of ACK addresses |
| 0x02 | READ_BYTES | addr, reg, len | data |
| 0x03 | WRITE_BYTES | addr, reg, data | ack/err |
| 0x10 | OTP_READ | addr | full dump |
| 0x11 | OTP_WRITE | addr, dump | ack/err |
| 0x20 | STORE_LIST | – | index of stored dumps |
| 0x21 | STORE_GET | id | dump |
| 0x22 | STORE_PUT | id, dump | ack |
| 0x23 | STORE_DEL | id | ack |
| 0x30 | BOOT_ERASE / BOOT_FLASH | – | OTA update handle |

### 4.3 OTP writing caveat (IMPORTANT)

Writing OTP (cmd 0x11) programs **one-time fuse bits** on the CD3217. This is
**irreversible** on a given chip. The tool must:

- show a **hard confirm** before any write,
- refuse to write over a "locked/verified" chip without an override flag,
- keep a local JSON *manifest* of every chip it has written (serial, address,
  original + written hashes) so a mistake is at least documented.

This is a deliberate design decision for the "Read + write OTP" requirement —
the harness/fixture (power, straps) is the user's responsibility, same as the
FTDI rig.

---

## 5. Windows app integration (the part reusing existing code)

Add one backend file `cd3217_analyzer/esp32_adapter.py` implementing the
existing `I2CAdapter` ABC (`adapters.py:15`):

```python
class ESP32Adapter(I2CAdapter):
    def __init__(self, port="COM3", baud=115200): ...
    open/close/scan/read_bytes/write_bytes/...
```

Because the ABC is already the seam, **GUI and CLI logic need no changes** —
the user just picks "ESP32 (USB)" as the adapter in the GUI instead of "FTDI".

### 5.1 New Windows features (incremental)

1. **ESP32 Connect tab** — pick COM port, open bridge, test with a scan.
2. **Flash ESP32 for first time** — from the Windows app: download latest
   firmware binary, hold boot button, and flash via `esptool` (bundled). This is
   the "flash the board for the first time in using it as i2c tool" requirement.
3. **Sync OTP library** — pull dumps from the ESP's `otpstore` and/or push the
   user's GitHub-collected known-good dumps onto the board.
4. **Update firmware (OTA)** — upload new `.bin` over WiFi via web UI.

### 5.2 GitHub OTP library sync (the "sync to github" requirement)

The ESP32 **does not** push to GitHub itself (no creds). Instead:

- ESP32 = **capture** + **local library**.
- User hits "Export to GitHub repo" in the Windows app (already has `gh` /
  repo context) → pushes dumps as files into a `otp-library/<chip>/<serial>/`
  tree in **this repo** or a dedicated `cd3217-otp-library` repo.
- Windows app can also **pull** the repo library back onto any ESP32
  (`STORE_PUT`), turning a board into a curated known-good OTP pack.

---

## 6. Build / packaging

- Firmware: PlatformIO project (`firmware_esp32/`), `pio run`, per-board build
  envs; release artifacts = `.bin` files.
- Windows bundle: `build.bat`/PyInstaller already handles the app; add
  `esptool` + a bundled default firmware `.bin` so "Flash ESP32" is one click.
- CI: extend `.github/workflows/build.yml` (or a second workflow) to build the
  ESP32 firmware on tag, attach `.bin` to the same release.

---

## 7. Roadmap (ordered)

| # | Milestone | Deliverable | Out of scope / gate |
|---|-----------|-------------|---------------------|
| 1 | **Firmware skeleton** | ✅ **DONE (28 Aug)** — WiFi AP, mDNS, web UI, on-device I2C scan; builds on S3/C3/classic | C6 needs IDF |
| 2 | **USB bridge** | CDC protocol; `ESP32Adapter` in Windows app; full scan/read/write from GUI over USB | OTP write still gated |
| 3 | **OTP store** | `otpstore` part, read/write OTP on-device + via web UI, JSON index, library browser | — |
| 4 | **Windows admin** | Connect tab, **flash-for-first-time**, OTP library export to GitHub | — |
| 5 | **Multi-board** | C6 via IDF or platform bump, fallback 4MB partition tables, level-shift notes | — |
| 6 | **OTA** | Wireless firmware update from web UI | — |
| 7 | **Polish** | 3.3/5V tolerance notes, enclosure ref, README, docs | Release |

### M1 spike — done
`firmware_esp32/` (Arduino) on **ESP32-S3** (primary): WiFi SoftAP
`cd3217-analyzer` + mDNS `cd3217.local`, web UI at `/` with a "Scan I2C bus"
button hitting `/api/scan` (real hardware scan 0x08–0x77, marks known ACE2
addresses), plus `/api/health`. Builds: `pio run -e esp32s3`.

### Immediate next step (Milestone 2 — USB bridge)
Add the USB CDC framed-protocol (section 4.2) to the firmware and the
`ESP32Adapter` in the Windows app so the existing GUI/CLI drives the scan
over USB with no analyzer-logic changes.

---

## 8. Open questions

1. **Wi-Fi mode**: SoftAP (ESP creates its own network, works anywhere) vs STA
   (joins `192.168.50.0/24`, reachable from LAN) vs both. Proposal: **both**,
   STA-first with SoftAP fallback. *Likely go this route, confirm later.*
2. **USB transport**: ROM CDC (native on S3/C6) — recommended — vs legacy
   USB-UART for classic ESP32. 
3. **Gating OTP writes**: hard-confirm only, or also a physical jumper
   (`IO0`) that must be pulled to enable destructive writes?
4. **GitHub library location**: same repo (`otp-library/`) vs separate
   `cd3217-otp-library` repo.
5. **Level tolerance**: CD3217 breakout rails — is the chip 3.3V only, or does
   it need 5V-tolerant I2C + a level shifter on the adapter?

Questions 1–4 are design decisions I'll default to the proposals above unless
you say otherwise. Q5 is a hardware fact only you can confirm from the bench.

---

## 9. Files touched (once we start)

```
firmware_esp32/            NEW * — PlatformIO firmware (Arduino)
  src/main.cpp              * M1: WiFi AP + mDNS + web UI + I2C scan
  src/i2c_master, proto, usb_bridge, webui, otpstore, wifi_mgr  (M2+)
  platformio.ini           * envs: esp32s3 (primary), c3, classic; c6 planned/IDF
  partitions.csv           * app0 + otpstore (spiffs) + coredump
cd3217_analyzer/esp32_adapter.py   NEW  — I2CAdapter backend (M2)
gui.py                     EDIT — ESP32 connect/flash/sync UI (M4)
pyproject / requirements   EDIT — add esptool, pyserial (M4)
.github/workflows/build.yml EDIT — build firmware on tag (M6)
```
`*` = already created in the M1 spike.
