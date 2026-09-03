/*
 * CD3217-Analyzer UART RX sniffing backend (listen-only).
 *
 * Taps a UART RX line (e.g. the ACE2 Master->Slave firmware-download bus on
 * MacBook boards: UPC_TA_UART_TX from the board feeds our RX pin) without
 * ever driving the line — RX-only by design, TX support may come later.
 *
 * Used by the USB bridge (cmds 0x20/0x21/0x24, all boards) and the WiFi
 * web UI (/api/uart/*).
 *
 * Default RX pin comes from platformio.ini: -DPIN_UART_RX=n
 *   RP2040 family : GP1  (UART0 RX; I2C=GP4/5, SPI=GP12-15 untouched)
 *   ESP32-S3      : GPIO4
 *   ESP32-C3      : GPIO1
 *   ESP32 classic : GPIO16 (RX2)
 *   ESP32-C6-Zero : GPIO1
 *
 * WARNING: measure the target's UART voltage first (Mac ACE2 buses are
 * expected to be 1.8V; boards are 3.3V) — sniff through a level shifter or
 * a series resistor + pull-up to the TARGET's rail.
 */

#ifndef CD3217_UART_SNIFF_H
#define CD3217_UART_SNIFF_H

#include <Arduino.h>
#include <stdint.h>

#ifndef PIN_UART_RX
#define PIN_UART_RX 1
#endif

#define UART_SNIFF_BUF 4096

class UartSniff {
 public:
  // baud == 0 -> stop sniffing (release the UART). pin == 0xFF -> default.
  // Returns true on success.
  static bool begin(uint32_t baud, uint8_t pin);
  static void stop();
  static bool active();

  // Move hardware FIFO bytes into the ring buffer (call from loop()).
  static void poll();

  // Pop up to max sniffed bytes into buf; returns count.
  static size_t read(uint8_t *buf, size_t max);

  // Measure the shortest LOW pulse (~start-bit width) on pin for ~1.5s.
  // Returns pulse width in microseconds (0 = no UART activity seen).
  static uint32_t autoBaud(uint8_t pin, uint32_t window_ms = 1500);

 private:
  static bool s_active;
};

#endif  // CD3217_UART_SNIFF_H
