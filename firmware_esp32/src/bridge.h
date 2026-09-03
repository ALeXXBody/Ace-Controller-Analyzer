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
 *   0x06 BUSCHK req: -                resp: [status][sda][scl]
 *                                        [sda_blips][scl_blips]
 *                                     SDA/SCL idle levels (1 = HIGH = pulled
 *                                     up/healthy, 0 = held LOW by a stuck
 *                                     chip/wiring) + 100-sample low-blip
 *                                     counts (rail-stability check; 0 = clean
 *                                     rail). Temporarily detaches Wire.
 *   0x07 I2CFREQ req: [freq LE32 Hz]  resp: [status]
 *                                     Set the I2C clock for the bus-speed
 *                                     stress probe (clamped 10k..400k Hz,
 *                                     out-of-range resets to 100k).
 *   0x10 SPIXFR req: [tx bytes...] (≤240) resp: [status][rx bytes...]
 *                                       full-duplex SPI exchange with CS
 *                                       wrapped around it (SPI flash backend)
 *   0x20 UART_SETUP req: [baud LE32][pin] (pin 0xFF=default; baud 0=stop)
 *                       resp: [status]          — start/stop RX-only sniffing
 *   0x21 UART_READ  req: -                     resp: [n][n bytes]  (n≤240)
 *   0x24 UART_AUTOBAUD req: [pin]              resp: [status][width_us LE32]
 *                                       shortest start-bit pulse (0=silent)
 *   0x30 FW_UPDATE  req: [sub][...]            resp: [status]
 *     sub 0x00 BEGIN   [size LE32]  — ESP32: start OTA write
 *     sub 0x01 CHUNK   [data ≤200]  — ESP32: write chunk (Update.write)
 *     sub 0x02 END     []           — ESP32: finish+verify; board reboots
 *     sub 0x03 BOOTSEL []           — RP2040: reboot into UF2 bootloader
 *     sub 0x04 REBOOT  []           — normal reboot
 *   status: 0x00 = OK, 0xFF = error (e.g. NACK/timeout)
 */

#ifndef CD3217_BRIDGE_H
#define CD3217_BRIDGE_H

#include <Arduino.h>
#include <Wire.h>
#include <stdint.h>

#define BRIDGE_MAGIC 0xA5
#define BRIDGE_MAX_FRAME 16 + 512  // room for a 240-byte SPI xfer + slack

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
  void reattachWire_();
  void busIdleLevels_(uint8_t &sda, uint8_t &scl);
  void recoverBus_();
};

#endif // CD3217_BRIDGE_H
