/*
 * CD3217-Analyzer UART RX sniffing backend — implementation (see uart_sniff.h).
 */

#include "uart_sniff.h"

// UART peripheral used for sniffing. RP2040: Serial1 = UART0 (GP0/GP1).
// ESP32: Serial1 with explicit RX pin, TX unused (-1).
#ifdef ARDUINO_ARCH_RP2040
#define CD_UART Serial1
#else
#define CD_UART Serial1
#endif

bool UartSniff::s_active = false;

static uint8_t s_buf[UART_SNIFF_BUF];
static volatile size_t s_head = 0;   // write index
static volatile size_t s_tail = 0;   // read index
static uint8_t s_pin = PIN_UART_RX;

bool UartSniff::begin(uint32_t baud, uint8_t pin) {
  if (baud == 0) {
    stop();
    return true;
  }
  if (pin != 0xFF) s_pin = pin;
  if (s_active) {
    CD_UART.end();
    s_active = false;
  }
  s_head = s_tail = 0;   // drop stale bytes on re-start
#ifdef ARDUINO_ARCH_RP2040
  CD_UART.setRX(s_pin);
  CD_UART.begin(baud);
#else
  CD_UART.begin(baud, SERIAL_8N1, s_pin, -1);   // RX only, no TX
#endif
  s_active = true;
  return true;
}

void UartSniff::stop() {
  if (s_active) {
    CD_UART.end();
    s_active = false;
  }
  s_head = s_tail = 0;
}

bool UartSniff::active() { return s_active; }

void UartSniff::poll() {
  if (!s_active) return;
  while (CD_UART.available() > 0) {
    size_t next = (s_head + 1) % UART_SNIFF_BUF;
    if (next == s_tail) break;            // buffer full: drop newest byte
    s_buf[s_head] = (uint8_t)CD_UART.read();
    s_head = next;
  }
}

size_t UartSniff::read(uint8_t *buf, size_t max) {
  size_t n = 0;
  while (n < max && s_tail != s_head) {
    buf[n++] = s_buf[s_tail];
    s_tail = (s_tail + 1) % UART_SNIFF_BUF;
  }
  return n;
}

uint32_t UartSniff::autoBaud(uint8_t pin) {
  if (pin != 0xFF) {
    if (s_active) stop();                 // free the pin for pulseIn
    s_pin = pin;
  } else if (s_active) {
    stop();
  }
  // UART idles HIGH; every start bit is a LOW pulse one bit-time wide, so
  // the SHORTEST LOW pulse seen approximates the bit time -> baud.
  pinMode(s_pin, INPUT_PULLUP);
  uint32_t min_us = 0;
  uint32_t t0 = millis();
  while (millis() - t0 < 1500) {
    uint32_t w = pulseIn(s_pin, LOW, 20000);   // wait up to 20ms per pulse
    if (w > 0 && w < 10000) {                  // sane bit-time range
      if (min_us == 0 || w < min_us) min_us = w;
      if (w <= 2) break;                        // faster than we can measure
    }
  }
  return min_us;   // 0 = no activity
}
