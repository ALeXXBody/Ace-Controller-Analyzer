# Task Queue (FIFO)

Queue cleared by owner (2026-09-05): previous item 12 (A2141 UB090 ROM
dumps / donor swap / UB300 20V thread) was for another purpose and its
tracking has been removed. Commit history retains the full text.

## Queue

### 13. [IN-PROGRESS] Full app audit (2026-09-05) — criticals 1-5 in order
Owner: "do them the correct way". Fixes as separate commits, each with
regression tests. Remaining audit rows (HIGH/MEDIUM/LOW) stay OPEN below
until the owner asks for them.
- [ ] 1. registers.py duplicate dict keys (0x30/0x35/0x35/0x36) — semantics
      of ActivePDO/ActiveRDO/SinkRequestRDO must match field-verified
      behavior (§4.9: 0x36 carries the live contract RDO)
- [ ] 2. GUI NameErrors: _save_debug_log stray textbox (gui.py:2107),
      _exit_for_installer undefined `action` (562)
- [ ] 3. Analyzer class-attribute state (truncation_seen/_slow_clock_engaged)
      → instance state, reset semantics
- [ ] 4. One shared 0xFF-merge + decode helper (3 drifting copies:
      analyzer.py:560, otp.py:162, otp_probe.py:53; merged decode drops
      0x36/0x3F/0x30)
- [ ] 5. Export double-read: report's full_diagnostic rescans bus +
      re-diagnoses every chip already collected (~2-3x traffic)
- OPEN (not scheduled): SMBus 0xFF-failure masking (adapters.py:236);
  unmasked VID compare (analyzer.py:1141); WRONG_VID retry contradiction
  (analyzer.py:1534); uart_autobaud timeout (usb_bridge.py:434); export
  token fchmod; connect/disconnect races + adapter leaks (gui.py:2450/3849);
  model '*' selection bug (gui.py:3575); cancel-event replacement (gui.py:
  2159); firmware no-reply + rlen-clamp + writePage:true (bridge.cpp);
  detect_adapter CH341/leak; duplicate validate_bundle identity checks.
- OBSERVATION: registers bug lived in an untested module — consider
  tests for adapters/registers/otp/models when touching them.