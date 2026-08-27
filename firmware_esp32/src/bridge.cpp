/*
 * CD3217-Analyzer USB bridge — implementation (see bridge.h for protocol).
 */

#include "bridge.h"

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
      // INFO: [boardlen][board][sda][scl]
      const char *name = CD3217_BOARD;
      uint8_t blen = (uint8_t)strlen(name);
      uint8_t resp[2 + 16];
      resp[0] = blen;
      memcpy(resp + 1, name, blen);
      resp[1 + blen] = I2C_SDA_GPIO;
      resp[2 + blen] = I2C_SCL_GPIO;
      sendResp_(0x05, resp, 3 + blen);
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
