# Task Queue (FIFO)

Queue cleared by owner (2026-09-05): previous item 12 (A2141 UB090 ROM
dumps / donor swap / UB300 20V thread) was for another purpose and its
tracking has been removed. Commit history retains the full text.

## Queue

### 13. [DONE] Full app audit (2026-09-05) — criticals 1-5 in order
Owner: "do them the correct way". Fixed as separate commits, each with
regression tests:
- DONE 1. registers.py duplicate keys (0x30/0x35/0x36) — canonical defs,
  shipped lengths, field-anchored on §4.9. Commit e909289, §4.15.
- DONE 2. GUI NameErrors removed (_save_debug_log stray textbox,
  _exit_for_installer dead branch). Commit c4176cd.
- DONE 3. Analyzer repair state → per-instance (§4.16). Commit 7846fe3.
- DONE 4. One shared merge helper + one decode dispatch (§4.17);
  merged repairs now decode 0x36/0x3F/0x30. Commit 2a3393e.
- DONE 5. Export report pass reuses collected data, no double-read (§4.18);
  step-4 VID mask fixed in BOTH paths. Commit 6a8dabe.
- OPEN (owner to schedule): duplicate validate_bundle identity checks;
  tests for otp (incl scan_otp) + models modules; firmware 1-byte-per-5ms
  ingest ceiling + no watchdog (bigger C++ work); plaintext AP password.
- DONE tier 3 (owner: "please do", §4.20): (G) board-tab worker widget
  calls marshaled (822d80e); (H) connect-gen guard + disconnect busy-gate
  + per-run cancel events + connect-failure leak close (b80a991);
  (I) board-flash/export workers on registered machinery (3181a37);
  (J) firmware: every input answered, rlen clamp fixed, flash WEN/busy
  verify (3171871; LAST_FIRMWARE_CHANGE → 0.12.13). Released v0.12.13 —
  CI green, 20 assets. NOTE: boards in the field pick up fix J via the
  app's Board-update flow (board fw < 0.12.13 triggers the offer).
- DONE in tier 2 (commits 97ca39b, e1e052b, 071f061, f76303f, 33e1928,
  b1786d8): SMBus 0xFF-masking → raise; WRONG_VID docstring corrected
  (code was VERIFIED-right, §3.4/§5.2); token chmod unconditional;
  model '*' selection; detect_adapter FTDI-leak + CH341 path;
  uart_autobaud deadline. Ledger §4.19.
- OBSERVATION: registers bug lived in an untested module — tests for
  adapters/otp/models worth adding when touching them.