/*
 * CD3217-Analyzer SPI flash backend — implementation (see spi_flash.h).
 */

#include "spi_flash.h"

// Max chunk for a single CS-wrapped read stream (keeps buffers small).
#define SF_READ_CHUNK 512
#define SF_MAX_WRITE  256

static uint8_t sf_tx[4 + SF_READ_CHUNK];   // cmd+addr+padding
static uint8_t sf_rx[4 + SF_READ_CHUNK];

void SpiFlash::begin() {
  pinMode(PIN_SPI_CS, OUTPUT);
  digitalWrite(PIN_SPI_CS, HIGH);
  CD_SPI.begin(PIN_SPI_SCK, PIN_SPI_MISO, PIN_SPI_MOSI, PIN_SPI_CS);
}

void SpiFlash::xfer(const uint8_t *tx, uint8_t *rx, size_t n) {
  digitalWrite(PIN_SPI_CS, LOW);
  CD_SPI.beginTransaction(SPISettings(2000000, MSBFIRST, SPI_MODE0));
#ifdef ARDUINO_ARCH_RP2040
  // arduino-pico: in-place full-duplex transfer
  if (rx != tx) memcpy(rx, tx, n);
  CD_SPI.transfer(rx, n);
#else
  CD_SPI.transferBytes(tx, rx, n);
#endif
  CD_SPI.endTransaction();
  digitalWrite(PIN_SPI_CS, HIGH);
}

static void sfWaitIdle(uint32_t timeoutMs) {
  uint32_t t0 = millis();
  while (millis() - t0 < timeoutMs) {
    if (!SpiFlash::busy()) return;
    delay(1);
  }
}

uint8_t SpiFlash::readStatus() {
  uint8_t tx[2] = {SF_CMD_READ_STATUS, 0x00};
  uint8_t rx[2];
  xfer(tx, rx, 2);
  return rx[1];
}

bool SpiFlash::busy() { return readStatus() & SF_STATUS_BUSY; }

void SpiFlash::jedec(uint8_t out[3]) {
  // Release power-down first — chips reached via a clip may have been left
  // powered down (0xAB, then ~3 dummy clocks before it wakes).
  uint8_t pd[1] = {SF_CMD_RELEASE_PD};
  uint8_t pdR[1];
  xfer(pd, pdR, 1);
  delayMicroseconds(50);
  uint8_t tx[4] = {SF_CMD_JEDEC_ID, 0x00, 0x00, 0x00};
  uint8_t rx[4];
  xfer(tx, rx, 4);
  out[0] = rx[1];
  out[1] = rx[2];
  out[2] = rx[3];
}

void SpiFlash::read(uint32_t addr, uint8_t *buf, size_t n) {
  sfWaitIdle(50);
  size_t off = 0;
  while (off < n) {
    size_t chunk = n - off;
    if (chunk > SF_READ_CHUNK) chunk = SF_READ_CHUNK;
    uint32_t a = addr + off;
    sf_tx[0] = SF_CMD_READ_DATA;
    sf_tx[1] = (a >> 16) & 0xFF;
    sf_tx[2] = (a >> 8) & 0xFF;
    sf_tx[3] = a & 0xFF;
    memset(sf_tx + 4, 0, chunk);
    xfer(sf_tx, sf_rx, 4 + chunk);
    memcpy(buf + off, sf_rx + 4, chunk);
    off += chunk;
  }
}

bool SpiFlash::writePage(uint32_t addr, const uint8_t *data, size_t n) {
  if (n > SF_MAX_WRITE) return false;
  sfWaitIdle(50);
  uint8_t wren[1] = {SF_CMD_WRITE_ENABLE};
  uint8_t wrenR[1];
  xfer(wren, wrenR, 1);
  uint8_t tx[4 + SF_MAX_WRITE];
  tx[0] = SF_CMD_PAGE_PROGRAM;
  tx[1] = (addr >> 16) & 0xFF;
  tx[2] = (addr >> 8) & 0xFF;
  tx[3] = addr & 0xFF;
  memcpy(tx + 4, data, n);
  uint8_t rx[4 + SF_MAX_WRITE];
  xfer(tx, rx, 4 + n);
  sfWaitIdle(20);          // page program typ 0.4ms, max 3ms
  return true;
}

void SpiFlash::eraseSector(uint32_t addr) {
  sfWaitIdle(50);
  uint8_t wren[1] = {SF_CMD_WRITE_ENABLE};
  uint8_t wrenR[1];
  xfer(wren, wrenR, 1);
  uint8_t tx[4] = {SF_CMD_SECTOR_ERASE,
                   (addr >> 16) & 0xFF, (addr >> 8) & 0xFF, addr & 0xFF};
  uint8_t rx[4];
  xfer(tx, rx, 4);          // returns immediately; caller polls busy
}

void SpiFlash::eraseChip() {
  sfWaitIdle(50);
  uint8_t wren[1] = {SF_CMD_WRITE_ENABLE};
  uint8_t wrenR[1];
  xfer(wren, wrenR, 1);
  uint8_t tx[1] = {SF_CMD_CHIP_ERASE};
  uint8_t rx[1];
  xfer(tx, rx, 1);          // returns immediately; caller polls busy
}
