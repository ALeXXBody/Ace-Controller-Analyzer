/*
 * CD3217-Analyzer USB bridge — implementation (see bridge.h for protocol).
 */

#include "bridge.h"
#include "spi_flash.h"
#include "uart_sniff.h"
#include "fw_version.h"

#ifdef ARDUINO_ARCH_RP2040
#include "pico/bootrom.h"      // reset_usb_boot() -> UF2 BOOTSEL mode
#else
#include <Update.h>            // ESP32 OTA self-update
#endif

// Max SPI full-duplex payload: response = status + rx, must fit 1-byte plen.
#define BRIDGE_SPI_MAX 240

void UsbBridge::begin() {
  // Serial is initialised in main setup(); nothing extra needed here.
}

// Read one byte from Serial, returning false if none available.
bool UsbBridge::readByte_(uint8_t &b) {
  if (!Serial.available()) return false;
  b = (uint8_t)Serial.read();
  return true;
}

// Try to read a complete frame into buf_. Returns true when a full, valid
// frame has been parsed and should be dispatched.
bool UsbBridge::readFrame_() {
  uint8_t b;
  if (!readByte_(b)) return false;

  if (!got_magic_) {
    if (b == BRIDGE_MAGIC) {
      // All 0xA5 bytes could be noise; hold the magic and wait for a cmd.
      got_magic_ = true;
      buf_[0] = b;
      len_ = 1;
    }
    return false;
  }

  buf_[len_++] = b;

  // Frame structure: magic, cmd, len, payload..., cksum
  // We know structure once we have at least 3 bytes.
  if (len_ >= 3) {
    uint8_t plen = buf_[2];
    size_t total = 3 + plen + 1;  // magic + cmd + plen + payload + cksum
    if (len_ == total) {
      // Validate cksum
      uint8_t ck = buf_[1] ^ buf_[2];
      for (size_t i = 0; i < plen; i++) ck ^= buf_[3 + i];
      if (ck == buf_[total - 1]) {
        frame_len_ = total;      // saved before len_ is cleared
        got_magic_ = false;
        len_ = 0;
        return true;
      }
      // bad checksum — discard and resync
      got_magic_ = false;
      len_ = 0;
      return false;
    }
    if (len_ > total || len_ > BRIDGE_MAX_FRAME) {
      // Oversized / malformed — resync
      got_magic_ = false;
      len_ = 0;
    }
  }
  return false;
}

// Send a response frame: [0xA5][cmd][plen][payload...][cksum]
void UsbBridge::sendResp_(uint8_t cmd, const uint8_t *payload, size_t plen) {
  uint8_t ck = cmd ^ (uint8_t)plen;
  Serial.write(BRIDGE_MAGIC);
  Serial.write(cmd);
  Serial.write((uint8_t)plen);
  for (size_t i = 0; i < plen; i++) {
    Serial.write(payload[i]);
    ck ^= payload[i];
  }
  Serial.write(ck);
}

void UsbBridge::runScan_() {
  uint8_t found[0x77 - 0x08 + 1];
  size_t  n = 0;
  for (int a = 0x08; a <= 0x77; a++) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) found[n++] = (uint8_t)a;
  }
  sendResp_(0x01, found, n);
}

void UsbBridge::runRead_(const uint8_t *f, size_t flen) {
  // payload: [addr][reg][len]
  if (flen < 3) return;
  uint8_t addr = f[0];
  uint8_t reg  = f[1];
  uint8_t rlen = f[2];

  uint8_t resp[1 + 64];
  if (rlen > 64) rlen = 64;

  Wire.beginTransmission(addr);
  Wire.write(reg);
  uint8_t wstatus = Wire.endTransmission(false);

  if (wstatus != 0) {
    resp[0] = 0xFF;                       // error (device NACK)
    sendResp_(0x02, resp, 1);
    return;
  }

  size_t got = Wire.requestFrom((int)addr, (int)rlen);
  if (got < rlen) {
    resp[0] = 0xFF;
    sendResp_(0x02, resp, 1);
    return;
  }
  resp[0] = 0x00;
  for (int i = 0; i < rlen; i++) resp[1 + i] = (uint8_t)Wire.read();
  sendResp_(0x02, resp, 1 + rlen);
}

void UsbBridge::runWrite_(const uint8_t *f, size_t flen) {
  // payload: [addr][reg][dlen][data...]
  if (flen < 3) return;
  uint8_t addr = f[0];
  uint8_t reg  = f[1];
  uint8_t dlen = f[2];
  if (flen < (size_t)(3 + dlen)) return;

  uint8_t resp[1];
  Wire.beginTransmission(addr);
  Wire.write(reg);
  for (int i = 0; i < dlen; i++) Wire.write(f[3 + i]);
  uint8_t st = Wire.endTransmission();
  resp[0] = (st == 0) ? 0x00 : 0xFF;
  sendResp_(0x03, resp, 1);
}

void UsbBridge::handleFrame_(const uint8_t *f, size_t flen) {
  if (flen < 2) return;
  uint8_t cmd = f[1];
  uint8_t plen = f[2];
  const uint8_t *pl = f + 3;

  switch (cmd) {
    case 0x01: runScan_(); break;
    case 0x02: runRead_(pl, plen); break;
    case 0x03: runWrite_(pl, plen); break;
    case 0x04: { uint8_t r = 0x51; sendResp_(0x04, &r, 1); break; }
    case 0x05: {
      // INFO: [boardlen][board][sda][scl][spi_sck][spi_miso][spi_mosi]
      //       [spi_cs][hw][uart_rx][verlen][version]
      // (older firmware sends fewer fields; host tolerates all)
      const char *name = CD3217_BOARD;
      uint8_t blen = (uint8_t)strlen(name);
      const char *ver = CD3217_FW_VERSION;
      uint8_t vlen = (uint8_t)strlen(ver);
      uint8_t resp[24 + 16];
      resp[0] = blen;
      memcpy(resp + 1, name, blen);
      resp[1 + blen] = I2C_SDA_GPIO;
      resp[2 + blen] = I2C_SCL_GPIO;
      resp[3 + blen] = PIN_SPI_SCK;
      resp[4 + blen] = PIN_SPI_MISO;
      resp[5 + blen] = PIN_SPI_MOSI;
      resp[6 + blen] = PIN_SPI_CS;
#ifdef ARDUINO_ARCH_RP2040
      resp[7 + blen] = 0x01;   // hw: RP2040/RP2350 (SPI1 block)
#else
      resp[7 + blen] = 0x02;   // hw: ESP32 family
#endif
      resp[8 + blen] = PIN_UART_RX;
      resp[9 + blen] = vlen;
      memcpy(resp + 10 + blen, ver, vlen);
      sendResp_(0x05, resp, 10 + blen + vlen);
      break;
    }
    case 0x10: {
      // SPI_XFR: full-duplex exchange, CS wrapped. resp = [status][rx...]
      if (plen > BRIDGE_SPI_MAX) {
        uint8_t r = 0xFF;
        sendResp_(0x10, &r, 1);
        break;
      }
      static uint8_t rxbuf[BRIDGE_SPI_MAX];
      SpiFlash::xfer(pl, rxbuf, plen);
      uint8_t resp[1 + BRIDGE_SPI_MAX];
      resp[0] = 0x00;
      memcpy(resp + 1, rxbuf, plen);
      sendResp_(0x10, resp, (size_t)plen + 1);
      break;
    }
    case 0x20: {
      // UART_SETUP: [baud LE32][pin] — baud 0 stops sniffing. resp [status]
      if (plen < 5) {
        uint8_t r = 0xFF;
        sendResp_(0x20, &r, 1);
        break;
      }
      uint32_t baud = (uint32_t)pl[0] | ((uint32_t)pl[1] << 8) |
                      ((uint32_t)pl[2] << 16) | ((uint32_t)pl[3] << 24);
      uint8_t pin = pl[4];
      bool ok = UartSniff::begin(baud, pin);
      uint8_t r = ok ? 0x00 : 0xFF;
      sendResp_(0x20, &r, 1);
      break;
    }
    case 0x21: {
      // UART_READ: pop sniffed bytes. resp = [n][bytes...]
      static uint8_t rbuf[BRIDGE_SPI_MAX];
      size_t n = UartSniff::read(rbuf, BRIDGE_SPI_MAX);
      uint8_t resp[1 + BRIDGE_SPI_MAX];
      resp[0] = (uint8_t)n;
      memcpy(resp + 1, rbuf, n);
      sendResp_(0x21, resp, n + 1);
      break;
    }
    case 0x24: {
      // UART_AUTOBAUD: [pin] -> [status][width_us LE32] (0 = no activity)
      if (plen < 1) {
        uint8_t r = 0xFF;
        sendResp_(0x24, &r, 1);
        break;
      }
      uint32_t w = UartSniff::autoBaud(pl[0]);
      uint8_t resp[5];
      resp[0] = 0x00;
      resp[1] = w & 0xFF;
      resp[2] = (w >> 8) & 0xFF;
      resp[3] = (w >> 16) & 0xFF;
      resp[4] = (w >> 24) & 0xFF;
      sendResp_(0x24, resp, 5);
      break;
    }
    case 0x30: {
      // FW_UPDATE: [sub][...]
      if (plen < 1) {
        uint8_t r = 0xFF;
        sendResp_(0x30, &r, 1);
        break;
      }
      uint8_t sub = pl[0];
      const uint8_t *data = pl + 1;
      size_t dlen = plen - 1;
      uint8_t ok = 0xFF;

      switch (sub) {
        case 0x00: {   // BEGIN [size LE32] (ESP32 OTA)
#ifdef ARDUINO_ARCH_RP2040
          break;       // not supported — use BOOTSEL flow
#else
          if (dlen >= 4) {
            uint32_t size = (uint32_t)data[0] | ((uint32_t)data[1] << 8) |
                            ((uint32_t)data[2] << 16) |
                            ((uint32_t)data[3] << 24);
            ok = Update.begin(size, U_FLASH) ? 0x00 : 0xFF;
          }
          break;
#endif
        }
        case 0x01: {   // CHUNK [data]
#ifdef ARDUINO_ARCH_RP2040
          break;
#else
          if (dlen > 0 && Update.write(data, dlen) == dlen) ok = 0x00;
          break;
#endif
        }
        case 0x02: {   // END: verify; reply then reboot into the new fw
#ifdef ARDUINO_ARCH_RP2040
          break;
#else
          if (Update.end(true)) {
            ok = 0x00;
            sendResp_(0x30, &ok, 1);
            delay(150);              // let the reply reach the host
            ESP.restart();
            return;                  // never reached
          }
          break;
#endif
        }
        case 0x03: {   // REBOOT TO BOOTSEL (RP2040 UF2 flow)
#ifdef ARDUINO_ARCH_RP2040
          ok = 0x00;
          sendResp_(0x30, &ok, 1);
          delay(150);                // let the reply reach the host
          reset_usb_boot(0, 0);      // mounts as RPI-RP2 drive
          return;
#else
          break;
#endif
        }
        case 0x04: {   // REBOOT
          ok = 0x00;
          sendResp_(0x30, &ok, 1);
          delay(150);
#ifdef ARDUINO_ARCH_RP2040
          watchdog_reboot(0, 0, 0);
#else
          ESP.restart();
#endif
          return;
        }
        default: break;
      }
      sendResp_(0x30, &ok, 1);
      break;
    }
    default: break;
  }
}

void UsbBridge::poll() {
  if (readFrame_()) {
    handleFrame_(buf_, frame_len_);
    // readFrame_ already reset len_/got_magic_ on success.
  }
}
