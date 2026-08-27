/*
 * CD3217-Analyzer SPI flash backend
 *
 * Drives a standard SPI NOR flash chip (Winbond/GD/ISSI/Micron...) on the
 * board's hardware SPI pins so ANY board can act as a standalone SPI flasher
 * for CD3217/Mac flash chips. Used by both the USB bridge (cmd 0x10, all
 * boards) and the WiFi web UI (/api/spi/*, WiFi boards).
 *
 * Pin map comes from platformio.ini build flags:
 *   -DPIN_SPI_SCK=n -DPIN_SPI_MISO=n -DPIN_SPI_MOSI=n -DPIN_SPI_CS=n
 * (defaults below = RP2040 family SPI1: GP12-15, works on Pico 1/2/W/2W and
 *  RP2040-Zero alike so one shield wiring serves the whole family).
 *
 * Wiring to the target chip goes through a level shifter on the shield:
 *   board SPI pins -> level shifter -> SOIC-8 clip -> flash chip
 * M1/M2-era Mac flash runs at 1.8V — never wire 3.3V GPIOs directly.
 */

#ifndef CD3217_SPI_FLASH_H
#define CD3217_SPI_FLASH_H

#include <Arduino.h>
#include <SPI.h>

// Board families: RP2040/RP2350 use SPI1 (GP12-15 block), ESP32 uses the
// default SPI peripheral with pins set in platformio.ini.
#ifdef ARDUINO_ARCH_RP2040
#define CD_SPI SPI1
#else
#define CD_SPI SPI
#endif

#ifndef PIN_SPI_SCK
#define PIN_SPI_SCK 14
#endif
#ifndef PIN_SPI_MISO
#define PIN_SPI_MISO 12
#endif
#ifndef PIN_SPI_MOSI
#define PIN_SPI_MOSI 15
#endif
#ifndef PIN_SPI_CS
#define PIN_SPI_CS 13
#endif

// SPI NOR flash opcodes (same set as the host cd3217_analyzer/flash.py)
#define SF_CMD_WRITE_STATUS   0x01
#define SF_CMD_PAGE_PROGRAM   0x02
#define SF_CMD_READ_DATA      0x03
#define SF_CMD_READ_STATUS    0x05
#define SF_CMD_WRITE_ENABLE   0x06
#define SF_CMD_SECTOR_ERASE   0x20
#define SF_CMD_CHIP_ERASE     0xC7
#define SF_CMD_RELEASE_PD     0xAB
#define SF_CMD_JEDEC_ID       0x9F

#define SF_STATUS_BUSY 0x01

class SpiFlash {
 public:
  static void begin();

  // Full-duplex transfer with CS wrapped around it (the bridge 0x10 cmd).
  static void xfer(const uint8_t *tx, uint8_t *rx, size_t n);

  // ── semantic helpers (web UI) ─────────────────────────────────────────
  static void jedec(uint8_t out[3]);          // releases power-down first
  static uint8_t readStatus();
  static bool busy();
  static void read(uint32_t addr, uint8_t *buf, size_t n);   // waits idle
  static bool writePage(uint32_t addr, const uint8_t *data, size_t n);  // ≤256
  static void eraseSector(uint32_t addr);     // issue + return (poll busy)
  static void eraseChip();                    // issue + return (poll busy)
};

#endif  // CD3217_SPI_FLASH_H
