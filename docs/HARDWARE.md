# Hardware notes — probe / bridge interface design

Evidence-based guidance for the interface hardware that powers the I²C
pull-ups and connects to the MacBook. Every item traces to observed
field behavior (see docs/IC_FINDINGS.md §3/§5).

## 1. The 3.3 V pull-up rail — the AMS1117 problem (observed)

Interface boards using an **AMS1117** LDO as the dedicated 3.3 V source
for SDA/SCL pull-ups (instead of the bridge board's own 3V3 rail) show
elevated read corruption that varies **from computer to computer**:

* Same board + probe: 1.5–20 % NACK rate on one PC, 84 % on another.
* Signature: response prefix arrives, data bytes garble/truncate to 0xFF.
* Root suspects, in order of likelihood:

  1. **Output-capacitor ESR**: the AMS1117 is a bipolar LDO that requires
     output capacitance with *some* ESR (solid tantalum ≥22 µF per
     datasheet). A small or ceramic (very low ESR) output cap lets the
     regulator **oscillate** — that oscillation lands directly on
     SDA/SCL because the pull-ups reference it.
  2. **Minimum load**: the AMS1117 family wants ~5 mA of load for solid
     regulation. Pull-up resistors to idle-high I²C lines draw
     ~0 mA — a nearly unloaded AMS1117 drifts/oscillates.
  3. **Second reference rail**: pull-ups on a different 3.3 V rail than
     the bridge MCU shifts the receiver's effective thresholds.

## 2. Recommended fixes (any ONE of these, in order of preference)

1. **Power the pull-ups from the bridge board's own 3V3 pin.** Same rail
   as the MCU, already regulated, one ground reference, no extra LDO.
   (Leave the AMS1117 unpopulated.)
2. **Replace the AMS1117 with a ceramic-stable LDO** that has no minimum
   load: MCP1700/MCP1702 (250 mA), HT7333, ME6211, LP5907. Keep 1 µF
   ceramic on in and out. Pin-compatible-ish footprints differ — check.
3. **Keep the AMS1117 but stabilize it**:
   * Output: ≥22 µF **tantalum** (or ceramic + 0.5–1 Ω series ESR).
   * Input: ≥10 µF close to the regulator.
   * Bleeder resistor: 1 kΩ from 3V3 to GND (≈3.3 mA minimum load).
   * Short, thick traces; the LDO close to the pull-up network.

## 3. I²C signal integrity

* **Series resistors 100 Ω** on SDA and SCL at the bridge end — damps
  ringing, limits current spikes during bus faults.
* **Lead length**: keep probe wires <10 cm, ideally twisted
  SDA+GND and SCL+GND.
* **Common ground**: the probe GND MUST connect to the MacBook's ground
  (any exposed shield/screw). Missing common ground produces
  catastrophic failure rates (>30 % transactions) — the app flags this
  as SEVERE in the bus-health summary.
* Pull-up value: 4.7 kΩ to the clean 3.3 V rail (the MacBook's own
  pull-ups may already be present on the SMC bus — the probe adds
  parallel strength).

## 4. Firmware/soft diagnostics

* **BUSCHK (Log tab → Bus Check, or CLI --bus-check)** now samples the
  idle levels 100× over ~50 ms and reports low-blip counts. A stable
  rail reports `0 blips`; an oscillating/weak rail blips — that is the
  AMS1117 signature above.
* The analyzer auto-drops to 50 kHz after consecutive failures and the
  truncation-repair merged reads run at 50 kHz — these mask a marginal
  rail but do not fix it. Fix the rail for full-rate captures.
