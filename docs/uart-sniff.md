# UART RX Sniffing — Listening to the ACE2 Firmware Bus

Every CD3217-Analyzer board can passively **sniff a UART line** (RX-only —
the board never drives the target's TX). The main use: the ACE2
**Master → Slave firmware-download bus** on MacBook logic boards.

## Why this matters

On boards like the 820-01700 (MacBook Pro A2141), the CD3217 (ACE2) USB-C
controllers boot in a Master/Slave arrangement: only Master chips have a
SPI flash ROM; **Slave chips download their application firmware from a
Master over UART at every power-on** (see repair.wiki "Apple ACE2
Controllers"). That bus is exposed on test points:

- `UPC_TA_UART_RX` / `UPC_TA_UART_TX` (per-controller; e.g. TA = left-top
  port on 820-01700)

Sniffing it gives you a decisive dead-port diagnosis:

| Observation | Meaning |
|---|---|
| No UART traffic at power-on | Master (or its ROM/flash) never sends firmware → check the Master side |
| Traffic flows, port still dead | Slave chip received firmware but fails → replace the Slave ACE2 |
| Garbage at any baud | Clock/wiring issue — check level shifting and ground |

You can also **capture the firmware payload** a Slave receives (complements
the SPI flash dump, which is the Master's ROM).

## ⚠ Voltage — measure first

The ACE2 UART bus is expected to be **1.8V logic**; analyzer boards are
3.3V. For RX-only sniffing, either:

- use a level shifter (same shield as SPI), **or**
- a series resistor (10k) from the target TX into the board RX, with a
  pull-up to the **target's** rail — verify thresholds before trusting data.

Never connect a 3.3V GPIO directly to a 1.8V line you intend to drive
(TX support, if added later, would be write-side and needs a proper shifter).

## Board RX pins (highlighted amber in the app's Board tab)

| Board | UART RX pin | UART peripheral |
|---|---|---|
| Pico 1 / Pico 2 / Pico W / Pico 2 W | GP1 (physical pin 2) | UART0 |
| RP2040-Zero | GP1 (right edge) | UART0 |
| ESP32-S3 DevKitC | GPIO4 | Serial1 |
| ESP32-C3 SuperMini | GPIO1 | Serial1 |
| ESP32 DevKit (classic) | GPIO16 (labeled RX2) | Serial1 |
| ESP32-C6-Zero (Waveshare) | GPIO1 (left edge) | Serial1 |

I2C and SPI pins are untouched — you can diagnose over I2C and sniff UART
at the same time, through one USB connection.

## Using it

### Windows app

1. Connect the board (USB Bridge), wire the target's UART TX (→ our RX
   pin) through the level shifter
2. Open the **UART** tab
3. Baud: pick a rate or **Auto-detect** (measures the shortest start-bit
   pulse, ~1.5s; the line must be transmitting during the measurement —
   power-cycle/replug the board to make the Master talk)
4. **Start** — data streams into the console (non-printable bytes shown as
   `<XX>`); **Save log** exports the capture

### CLI

```
# measure the line's baud
python -m cd3217_analyzer --uart-autobaud --port COM5

# stream a capture (fixed baud or auto)
python -m cd3217_analyzer --uart-sniff 115200 --port COM5
python -m cd3217_analyzer --uart-sniff auto --port COM5
```

### WiFi boards — fully standalone

Join the `cd3217-analyzer` AP → `http://192.168.4.1` → **UART Sniff** tab →
same Start/Stop/Clear/Save flow, straight from the browser.

## Bridge protocol

```
0x20 UART_SETUP   req [baud LE32][pin]   resp [status]   (baud 0 = stop)
0x21 UART_READ    req -                  resp [n][bytes] (n ≤ 240)
0x24 UART_AUTOBAUD req [pin]             resp [status][width_us LE32]
```

The firmware buffers up to 4 KB while the host polls (150 ms app cadence,
250 ms web cadence). TX is deliberately not implemented — it will be added
once the bus is better understood.
