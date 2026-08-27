/*
 * CD3217-Analyzer USB bridge
 *
 * Framed binary protocol over CDC serial (native USB on RP2040 / ESP32-S3).
 * Lets the Windows app drive the board as a USB-I2C adapter. Runs on ALL
 * board builds (wired boards have no web UI, so this is their control path;
 * WiFi boards can also use it).
 *
 * Frame:  [0xA5][cmd:1][len:1][payload...][cksum:1]
 *   cksum = XOR over cmd, len, and payload bytes.
 *
 * Commands (cmd):
 *   0x01 SCAN   req: -                resp: [n][addr...]
 *   0x02 READ   req: [addr][reg][len] resp: [status][data...]
 *   0x03 WRITE  req: [addr][reg][dlen][data...] resp: [status]
 *   0x04 PING   req: -                resp: [0x51]
 *   0x05 INFO   req: -                resp: [boardlen][board][sda][scl]
 *   status: 0x00 = OK, 0xFF = error (e.g. NACK/timeout)
 */

#ifndef CD3217_BRIDGE_H
#define CD3217_BRIDGE_H

#include <Arduino.h>
#include <Wire.h>
#include <stdint.h>

#define BRIDGE_MAGIC 0xA5
#define BRIDGE_MAX_FRAME 16 + 64   // enough for one write op

class UsbBridge {
 public:
  void begin();
  void poll();

 private:
  uint8_t buf_[BRIDGE_MAX_FRAME];
  size_t  len_ = 0;
  size_t  frame_len_ = 0;   // length of the last fully-received frame
  bool    got_magic_ = false;

  bool readByte_(uint8_t &b);
  bool readFrame_();
  void handleFrame_(const uint8_t *f, size_t flen);
  void sendResp_(uint8_t cmd, const uint8_t *payload, size_t plen);
  void runScan_();
  void runRead_(const uint8_t *f, size_t flen);
  void runWrite_(const uint8_t *f, size_t flen);
};

#endif // CD3217_BRIDGE_H
