# CD3217-Analyzer: ESP32 / RP2040 Standalone Unit — Design & Roadmap

> Status: **Milestone 1 spike DONE** (firmware compiles on 9 boards; no hardware
> flash yet)
> Session date: 2026-08-27
>
> **M1 spike results (28 Aug 2026):**
> - `firmware_esp32/` builds with **Arduino** on the **ESP32** family
>   (**S3, C3, classic**) *and* the **Pico/RP2040/RP2350** family
>   (**RP2040-Zero, Pico 1, Pico 2, Pico W, Pico 2 W**) — verified via `pio run`.
> - **Web UI** runs on the WiFi boards: ESP32 S3/C3/classic + **Pico W, Pico 2 W**.
> - **Wired boards** (RP2040-Zero, Pico 1, Pico 2) have no WiFi → they serve as
>   the USB-I2C bridge in M2 (all RP2040/RP2350 have native USB).
> - The web UI uses the built-in sync `WebServer.h`, so no external web lib is
>   needed across ESP32 + Pico — this removed all cross-arch lib friction.
> - **ESP32-C6 does NOT build with Arduino** in espressif32 7.0.1 →
>   needs ESP-IDF. Tracked as a known limitation — C6 stays a secondary target.
> - Firmware: WiFi SoftAP `cd3217-analyzer` + mDNS `cd3217.local` + web UI
>   (`/`) + `/api/scan` (real I2C scan 0x08–0x77) + `/api/health`.

---

## 1. Vision

Turn the GUI/FTDI app into a **two-halves** system:

1. **Existing Windows app** (`gui.py`, `cd3217_analyzer/*`) — the full-featured analysis tool.
2. **NEW: ESP32 / RP2040 standalone unit** — a pocket I2C bench tool that:
   - hosts its own **Wi-Fi web UI** (phone/laptop), no PC needed *(WiFi boards)*,
   - **reads and writes OTP** on CD3217B12 (ACE2) chips via I2C,
   - keeps a **large-flash OTP library on-board** (survives power loss),
   - is **flashed/updated and its OTP library synced by the Windows app** over USB.

The Windows app gains a **4th hardware backend** (board-as-USB-I2C-bridge, any
ESP32 or Pico via native/ROM USB) that implements the *same* `I2CAdapter`
interface, so all existing scan/diagnose/analyze code runs against it with
**zero changes** to the logic layer.

---

## 2. Board Selection (target "all boards that can take it")

Goal: one firmware that runs across common **ESP32** and **RP2040/RP2350**
boards, degrading gracefully on smaller ones. Same Arduino core + `main.cpp`;
only the pins and (for WiFi) the connectivity differ per env.

| Board | Core | Flash | WiFi | Arduino build (M1) | Verdict |
|-------|------|-------|------|--------------------|-----|
| **ESP32-S3** 16/32MB | Xtensa | 16–32MB | ✅ | ✅ compiles | **⭐ primary (web UI)** |
| ESP32-C3 | RISC-V | 4MB | ✅ | ✅ compiles | Supported (web UI) |
| ESP32 (classic) | Xtensa | 4MB | ✅ | ✅ compiles | Supported (web UI) |
| ESP32-C6 | RISC-V | 4MB | ✅ | ❌ needs IDF | Secondary — see below |
| **RP2040-Zero** (Waveshare) | ARM M0+ | 2MB | ❌ | ✅ compiles | Supported (USB bridge M2) |
| **Pico 1** (RP2040) | ARM M0+ | 2MB | ❌ | ✅ compiles | Supported (USB bridge M2) |
| **Pico 2** (RP2350) | ARM M33 | 4MB | ❌ | ✅ compiles | Supported (USB bridge M2) |
| **Pico W** (RP2040) | ARM M0+ | 2MB | ✅ CYW43439 | ✅ compiles | Supported (web UI) |
| **Pico 2 W** (RP2350) | ARM M33 | 4MB | ✅ CYW43439 | ✅ compiles | Supported (web UI) |

**Split by capability:**

- **WiFi boards** (ESP32-S3/C3/classic, Pico W, Pico 2 W) → full embedded
  **web UI** (M1).
- **Wired boards** (RP2040-Zero, Pico 1, Pico 2) → no WiFi; they shine as the
  **USB-I2C bridge** in M2 (native USB on every RP2040/RP2350 board).
- **ESP32-C6** needs an espidf port or an espressif32 platform bump; it stays
  in `platformio.ini` but is not an Arduino build target yet.

All verified to compile (`pio run`) in the M1 spike. The main.cpp uses the
built-in sync `WebServer.h` (present on ESP32 and Pico cores), so no external
web-server lib is needed — eliminating cross-arch lib friction.

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

Chosen toolchain: **PlatformIO + Arduino**, spanning two platforms in one
`platformio.ini`:

- `espressif32` (installed) — ESP32-S3/C3/classic/C6
- `maxgerhardt/platform-raspberrypi` — Pico family (RP2040/RP2350), custom
  `boards/waveshare_rp2040_zero.json` for the RP2040-Zero

One `main.cpp` builds for both cores; `#ifdef ARDUINO_ARCH_RP2040` handles the
only API difference (I2C pin setup via `Wire.setSDA/setSCL`). The web UI uses
the **built-in sync `WebServer.h`** (present on ESP32 + Pico), so no external
web lib is required. Per-board envs differ only by GPIOs, platform, and
`-DCD3217_HAS_WIFI` (WiFi boards) vs none (wired boards, which get the USB
bridge in M2). **ESP32-C6** still needs ESP-IDF here (no Arduino in
espressif32 7.0.1).

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

Add one backend file `cd3217_analyzer/usb_bridge_adapter.py` implementing the
existing `I2CAdapter` ABC (`adapters.py:15`). It talks the framed CDC protocol
to any board (ESP32 or Pico — both present as a serial port):

```python
class UsbBridgeAdapter(I2CAdapter):
    def __init__(self, port="COM3", baud=115200): ...
    open/close/scan/read_bytes/write_bytes/...
```

Because the ABC is already the seam, **GUI and CLI logic need no changes** —
the user just picks "Board (USB bridge)" as the adapter in the GUI instead of
"FTDI".

### 5.1 New Windows features (incremental)

1. **Board Connect tab** — pick COM port, open bridge, test with a scan.
2. **Flash the board for the first time** — from the Windows app: download the
   latest firmware binary and flash it. ESP32 → `esptool` (bundled); Pico →
   drop-in UF2 (it's just a USB drive). This is the "flash the board for the
   first time in using it as i2c tool" requirement.
3. **Sync OTP library** — pull dumps from the board's `otpstore` and/or push
   the user's GitHub-collected known-good dumps onto the board.
4. **Update firmware (OTA)** — upload new `.bin` over WiFi via web UI (WiFi
   boards) or over USB.

### 5.2 GitHub OTP library sync (the "sync to github" requirement)

The board **does not** push to GitHub itself (no creds). Instead:

- Board = **capture** + **local library**.
- User hits "Export to GitHub repo" in the Windows app (already has `gh` /
  repo context) → pushes dumps as files into a `otp-library/<chip>/<serial>/`
  tree in **this repo** or a dedicated `cd3217-otp-library` repo.
- Windows app can also **pull** the repo library back onto any board
  (`STORE_PUT`), turning it into a curated known-good OTP pack.

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
| 1 | **Firmware skeleton** | ✅ **DONE (28 Aug)** — WiFi AP, mDNS, web UI, on-device I2C scan; builds on **9 boards**: ESP32 S3/C3/classic (web UI) + RP2040-Zero/Pico1/2/W/2W | C6 needs IDF |
| 2 | **USB bridge** | ✅ **DONE** — CDC protocol on firmware (all boards); `UsbBridgeAdapter` in Windows app; scan/read/write from GUI/CLI over USB | OTP write still gated |
| 3 | **OTP store** | `otpstore` part, read/write OTP on-device + via web UI, JSON index, library browser | — |
| 4 | **Windows admin** | 🔶 **PARTIAL** — flashing (`flash_board.py`: UF2 bootsel + esptool) done; OTP library export to GitHub pending | — |
| 5 | **Multi-board** | C6 via IDF or platform bump, fallback 4MB partition tables, level-shift notes | — |
| 6 | **OTA** | Wireless firmware update from web UI | — |
| 7 | **Polish** | 3.3/5V tolerance notes, enclosure ref, README, docs | Release |

### M1 spike — done
`firmware_esp32/` (Arduino) on **ESP32-S3** (primary) + the full Pico family:
WiFi SoftAP `cd3217-analyzer` + mDNS `cd3217.local`, web UI at `/` with a
"Scan I2C bus" button hitting `/api/scan` (real hardware scan 0x08–0x77, marks
known ACE2 addresses), plus `/api/health`.
- **Web UI**: ESP32-S3/C3/classic, Pico W, Pico 2 W
- **Wired (I2C + serial, USB bridge in M2)**: RP2040-Zero, Pico 1, Pico 2
- Builds: `pio run -e <env>`, e.g. `esp32s3`, `rp2040_zero`, `pico_w`.

### Immediate next step (Milestone 2 — USB bridge)
Add the USB CDC framed-protocol (section 4.2) to the firmware and an
`Adapter` in the Windows app so the existing GUI/CLI drives the scan
over USB on **any** of these boards (ESP32 or Pico, both have native/ROM USB)
with no analyzer-logic changes.

#### M2 status — done
- **Firmware**: `src/bridge.h` / `src/bridge.cpp` — framed binary protocol
  `[0xA5][cmd][plen][payload][cksum]` over CDC serial (`Serial`), on **all**
  boards. Commands: `0x01 SCAN`, `0x02 READ`, `0x03 WRITE`, `0x04 PING`,
  `0x05 INFO` (reports board, SDA, SCL). Built & verified across all 8 boards.
- **Adapter**: `cd3217_analyzer/usb_bridge.py` → `UsbBridgeAdapter` implements
  the `I2CAdapter` ABC (adapters.py). User picks **"USB Bridge (board)"** in the
  GUI (or `--adapter usb --port COMx` in the CLI) instead of FTDI; the existing
  scan/diagnose/dump/OTP flows work unchanged.
- **Flashing**: `cd3217_analyzer/flash_board.py` — Pico-family via UF2
  drag-and-drop to the BOOTSEL mass-storage volume (zero deps, works on
  Windows); ESP32 via `esptool`. GUI "Flash board" button + `--flash-board`.
- Tests: `tests/test_usb_bridge.py`, `tests/test_flash_board.py` (15 new).

---

## 8. Open questions

1. **Wi-Fi mode**: SoftAP (ESP creates its own network, works anywhere) vs STA
   (joins `192.168.50.0/24`, reachable from LAN) vs both. Proposal: **both**,
   STA-first with SoftAP fallback. *Likely go this route, confirm later.*
2. **USB transport**: ROM CDC native USB (S3/C6, all Pico/RP2040) — recommended
   — vs legacy USB-UART for classic ESP32.
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
  src/main.cpp              * M1: WiFi AP + mDNS + web UI + I2C scan (multi-arch)
  src/bridge.h/.cpp         * M2: USB-CDC framed bridge (all boards) — DONE
  src/i2c_master, proto, usb_bridge, webui, otpstore, wifi_mgr  (M2+)
  platformio.ini           * 9 envs: esp32s3/c3/classic + rp2040_zero/pico/pico_w/pico2/pico2w
                           *      (esp32c6 flagged: needs IDF)
  boards/waveshare_rp2040_zero.json * RP2040-Zero custom board def
  partitions.csv           * app0 + otpstore (spiffs) + coredump
cd3217_analyzer/usb_bridge.py   NEW — I2CAdapter backend (USB bridge, M2) — DONE
cd3217_analyzer/flash_board.py  NEW — UF2 bootsel + esptool flashing (M4) — DONE
gui.py                     EDIT — USB connect + flash UI — DONE
requirements.txt           EDIT — add pyserial — DONE
tests/test_usb_bridge.py, test_flash_board.py  NEW — DONE
```
`*` = already created in the M1 spike. `DONE` = implemented in this pass.
