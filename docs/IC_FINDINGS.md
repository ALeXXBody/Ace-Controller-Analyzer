# CD3217 / ACE2 — Findings Ledger (evidence log)

Living document. Every conclusion gets a verdict and the evidence behind
it. Update it whenever new hardware evidence arrives; never re-litigate a
VERIFIED item without new evidence, never resurrect a WRONG one without a
rebuttal.

Verdict legend:
- **VERIFIED** — confirmed by multiple independent observations
  (different boards/sessions/samples) or by primary documentation.
- **PROVISIONAL** — fits current evidence but seen on limited data.
- **WRONG** — disproven; kept here so we don't repeat it, with what
  replaced it.
- **OPEN** — unknown, needs evidence.

Evidence index: session logs (user-provided), exported bundles in
`samples/` (sha256-verified), `samples/820-02382.json` (A2485 capture),
schematic/boardview extracts, TI docs (SLVA689, SCPA069, SLVUBH2B,
SLVAE21A), Asahi/t8012dev notes, repair.wiki, badcaps threads.

---

## 1. The chip family

| # | Finding | Verdict | Evidence |
|---|---------|---------|----------|
| 1.1 | CD3215/CD3217/CD3218 are the same "Burnside" ACE2 core (TI TPS6598x-derived USB-C PD controller); retail CD3217 boards can report the CD3218 die. Chip *type* must not be judged from the die string. | VERIFIED | repair.wiki; A2251 healthy board reports CD3218 die on some sockets; A2485 sample mixes CD3217/CD3218 |
| 1.2 | ACE2 I²C buses run at 3.3 V (not 1.8 V). | VERIFIED | Asahi/t8012dev electrical capture; app guidance unchanged |
| 1.3 | Vanilla (strap-addressed) chips report VID **0x0451** (TI); Apple OTP-ed chips report **0x2804**. A CD3217-family chip always reports one of these — an unexpected VID is wire corruption, not a different chip. | VERIFIED | every capture to date; export validator flags others; "2 faulty / all good per-chip" case (v0.8.4) |
| 1.4 | OTP-ed chips ignore board straps and answer at their burned address. Vanilla chips strap per ADDR/CNTL resistors. Strap→address decode table (0/38k3/84k5/140k/205k/280k/374k/float) matches the app's calculator. | VERIFIED | badcaps strap analysis; BoardRev video; A2251 boardview |
| 1.5 | All-call address **0x6B**: every chip ACKs it simultaneously; never a device; transactions to it garble the bus. Filtered from all scan paths since v0.7.1. | VERIFIED | analyzer/OTP scans; firmware |
| 1.6 | Socket maps: A2251 (X@38 GND, T@3F float, W@3B OTP, R@3C OTP), A2338/A2337/A2179 (UF400@38 GND, UF500@3F float) — verified. A2442/A2779/A2780 maps are **UNVERIFIED** (repair.wiki's schematic table contradicts them) and flagged as such in the app. | VERIFIED / OPEN | boardview 820-01949; repair.wiki A2442 table |
| 1.7 | Each USB-C port pair shares one SPI ROM; the ACE2 loads its app firmware (patch bundle) from it. Persistent BOOT mode ⇒ check the ROM path, not just the chip. | VERIFIED | TI SLVAE21A boot flow; 2025-26 repair cases (shorted ROM ≙ bad chip symptoms) |
| 1.8 | Firmware variant tags in DeviceInfo (`ZACE2-J213`, `RACE2-J316P5U`, …): project code (J316 = A2485 family) + per-ROLE build suffix. Roles (system-power / data-port / SMC) carry different builds; variant is the donor-matching signal. | VERIFIED | A2485 real capture: 0x3A RACE2-J316P5U, 0x3B ZACE2-J316P2P, 0x3F ZACE2-J316P01P; rickmark ZACE2-J213 |

## 2. I²C register protocol (the big picture)

| # | Finding | Verdict | Evidence |
|---|---------|---------|----------|
| 2.1 | **Every register read response starts with a length/prefix byte**, then data: `[0x04]['A']['P']['P']` = mode "APP"; `[0x40]['C']['D']…` = DeviceInfo; `0x0B`/`0x12` seen on 0x14/0x2D; `0x3F` seen on 0x29. The prefix is NOT register data. | VERIFIED | A2485 sample (0x2F starts 0x40 '@' + "CD3218"); user dumps (0x14→0b, 0x2D→12, 0x29→3f); A2251 exports |
| 2.2 | 4CC registers (Mode 0x03, Type 0x04): response = `[0x04][3 chars]`. The app's 4-byte read folds the prefix into the value; decoders tolerate it. | VERIFIED | all captures |
| 2.3 | VID (reg 0x00): response `[data_lo data_hi 00 00]` — e.g. Apple `04 28 00 00`, TI `51 04 00 00`. **No prefix on 0x00** (the TI low byte 0x51 disproves a universal 0x04 prefix). | VERIFIED | all captures |
| 2.4 | DID (reg 0x01): response `[0x04][0x17 0x32 0xCD]`-style — the 0x04 is the prefix (or rev), DID = 3 data bytes "CD3217" LE. The app's 4-byte read yields 0xCD321704; `decode_silicon` handles it. Exact semantic split (prefix vs revision) is OPEN. | PROVISIONAL | stable across sessions; exact framing unproven |
| 2.5 | DeviceInfo (reg 0x2F, 47 B): `[0x40]['C']['D']['3']['2']['1'][...]` — identity string "CD3217   HW0022 FW002.170.00 ZACE2-…" with a leading marker byte (0x40='@' or length). Parses via `parse_device_info`. | VERIFIED | A2485 sample (full 47 B string); TI E2E format |
| 2.6 | **Truncation**: a slow / partially-powered chip delivers prefix + K data bytes then floats SDA — the master reads 0xFF for the rest. K varies per attempt. Retrying at the same length never fixes it. | VERIFIED | user exports: OTP chunks `04 FF FF FF`, DeviceInfo truncated to `@CD`, DID tails 0xFF; A2485 sample proves full 47 B IS deliverable when the chip is fully awake |
| 2.7 | **Repair = merge repeated full-length reads byte-wise** (first non-0xFF byte per position wins). Different attempts truncate at different points; the union assembles the complete response. Byte-wise sub-reads are meaningless (every sub-read restarts at the prefix). | VERIFIED | v0.9.1 merge + regression test simulating progressive truncation; replaced the WRONG byte-wise repair (v0.9.0) |
| 2.8 | Long reads (≥22 B: 0x14/0x15/0x2D/0x2F) truncate first; 4-byte reads usually complete. Detection: ≥40% trailing-0xFF fraction. | VERIFIED | all A2251 exports; 820-02382 counter-example (full strings on a healthy-powered board) |
| 2.9 | Event registers 0x14/0x15 legitimately contain 0x00 and 0xFF regions — excluded from the zero-corruption rule. Values parsed from them include the prefix byte — treat parsed conclusions from 0x14/0x15/0x29/0x2D values as PROVISIONAL. | VERIFIED / OPEN | all exports |

## 3. Bus & probing

| # | Finding | Verdict | Evidence |
|---|---------|---------|----------|
| 3.1 | Probing adds capacitance; margins degrade; NACK/garble rates rise. Inter-transaction spacing (20 ms) + retries cut NACK rates from ~80% to 1.5–20%. | VERIFIED | user logs v0.7.x → v0.8.x; TI SLVA689 |
| 3.2 | Stuck-SDA (slave held mid-byte) is real and recoverable: 9–18 SCL pulses + STOP clears it. Firmware `recoverBus_` (v0.8.0) no-ops on a healthy bus. | VERIFIED | TI SCPA069; compiled+shipped; no regressions |
| 3.3 | **0-bits reading as 1s** (`0xCD`→`0xFF`, `0x28`→`0xFF`) = SDA low-drive failure at the probe (contact / pull-up drive), NOT chip damage. Cross-checking two independent datasets (register dump vs OTP dump) surfaces it. | VERIFIED | A2251 exports: same-position 0xFF patterns; chip 0x38 reads clean at times |
| 3.4 | "Diagnose All shows 2 faulty, per-chip all good" = recoverable corruption (garbled VID→WRONG_VID) on later chips in the sequence. WRONG_VID is now retryable in the ladder (v0.8.4). | VERIFIED | user logs; fix shipped and confirmed ("All 4 read successfully") |
| 3.5 | Idle-settle between chips matters: reading chip N+1 right after chip N's burst NACKs identity registers. 0.3 s inter-chip settle in exports; adaptive settle after flaky diagnoses. | VERIFIED | v0.8.3 log deltas |
| 3.6 | A board right after OTA/reboot can: enumerate-delay (false "board removed"), refuse reopen (PermissionError 13/31), and answer its first full scan empty. All three now handled (grace, 2×absence, open-retry, empty-scan retry). | VERIFIED | user logs 19:09 / 20:43 / 19:58 |

## 4. Tooling lessons (app architecture)

| # | Finding | Verdict | Evidence |
|---|---------|---------|----------|
| 4.1 | Cross-thread tkinter (`after()` from workers) raises AND deadlocks → "Not responding". Workers must never touch tkinter: queue-only `_ui()` + main-thread drain + busy watchdog (v0.7.3). | VERIFIED | Xvfb rig stack traces; the v0.7.3/v0.7.5 freeze reports |
| 4.2 | Multi-line `self.after(\n 0, …)` call sites are easy to miss in sweeps — the rig/grep sweep must cover them (v0.7.5 caught 6 more). | VERIFIED | sweep history |
| 4.3 | Unclean mid-read serial close wedges the Windows driver (port un-reopenable until replug). close() must take the transaction lock, defer if busy. | VERIFIED | `'NoneType' object has no attribute 'hEvent'` + subsequent PermissionError cluster (v0.8.0) |
| 4.4 | threading.Lock is not reentrant — the first v0.8.0 close() re-acquired and self-deadlocked; caught by the suite before release. | VERIFIED | suite hang; faulthandler stack |
| 4.5 | Firmware auto-update must fire only when firmware code changed (LAST_FIRMWARE_CHANGE gate, currently v0.7.1). App-only releases never reflash. | VERIFIED | user request + logs showing pointless 0.7.4→0.7.9 reflash |
| 4.6 | The debug trace (v0.7.6) is the decisive diagnostic: TX/RX with status bytes distinguishes serial-level vs I²C-level vs app-level failures. Keep instrumenting new paths. | VERIFIED | user's first trace session identified the stale-update-worker port contention |
| 4.7 | Export pipeline must verify-and-recheck at collection time (identity semantic checks + OTP content check + response merging), then file-level validation (sha256 sidecar, completeness, model coverage). The validator caught real data holes (partially-garbled VID 0xFF04; truncated DeviceInfo) that "32/32 filled" missed. | VERIFIED | v0.7.9–v0.9.1; user's popups |
| 4.8 | A unit-test rig for the GUI (Xvfb + fake hardware + stall watchdog) is mandatory before every release — it caught the popup TypeError, the log_text drop, the open() deadlock, and proved freeze fixes. | VERIFIED | tours since v0.7.3 |
| 3.7 | **Bus quality is machine-dependent**: the same board+probe gave 1.5–20% NACK on one PC (COM10) and 84% + 4/32 OTP fills on another PC (COM2). The bridge's USB power and ground path differ per machine — degraded 3.3 V rail on the bridge degrades the I²C levels it drives. Advice when a session is catastrophic: different USB port (direct, no hub), shorter/better USB cable, verify probe GND. | VERIFIED | user logs 22:14 (Lenovo, good) vs 09:11 (Bogdan, 84% fail) — same board, different machine |
| 3.9 | **v0.10.0 field result — the corruption story is CLOSED**: after the Wire.setTimeout firmware + 50 kHz merged repair, the SAME machine that showed 84% NACK now measures 0.0% NACK, 0 garbled reads, all golden identities valid (`CD3217 FW002.099.00 ZACE2-J214XT` ×2 / `ZACE2-J214WR` ×2), OTP 32/32, model coverage 4/4. Variant suffixes encode socket PAIRS (XT = X/T pair, WR = W/R pair) — the donor-matching rule. | VERIFIED | user's v0.10.0 log + pushed A2251.json (sha256-verified, 0 warnings) |
| 3.8 | **Interface hardware root cause identified and being fixed**: the user's shield powers the I²C pull-ups from an AMS1117 (no minimum load, ceramic-output-cap-sensitive → oscillation lands on SDA/SCL). The user is building a new shield using the bridge board's own 3V3 rail (recommendation #1, docs/HARDWARE.md). Expect machine-dependent corruption to disappear. | VERIFIED (user statement) / field confirmation pending |
| 4.11 | BUSCHK v2 (fw ≥ 0.10.0): 100-sample low-blip counters on SDA/SCL idle levels detect an oscillating/weak pull-up rail; backward compatible (old fw = blips None). | VERIFIED (tests + all 10 envs compile); field validation pending |
| 4.9a | StringVar.get()/set() from worker threads is the SAME hazard class as widget calls: it blocks on the Tcl lock for as long as the main thread isn't servicing (observed: 19 s stall between "Connecting..." and the first worker log). Rule extended: workers receive UI state as plain ARGUMENTS, captured on the UI thread. | VERIFIED | v0.9.7 user log (19 s connect gap); fixed v0.9.8 |
| 4.9 | **RELEASE VERSIONS WERE STALE v0.9.2–v0.9.6**: the manual `sed` version bumps silently no-opped, so five releases shipped binaries labeled "0.9.1" (code was current, label was not). Consequences: the updater nagged forever, builds were indistinguishable, and reported symptoms couldn't be mapped to versions. Fixed: CI now stamps `__version__` from the git tag (build.yml "Stamp app version from tag") + `release_bump.py` fails hard on a bad bump. Verified by extracting the PYZ from the published Portable (contained "0.9.1" — proof) . | VERIFIED | PYZ extraction of the v0.9.6 asset; `git show v0.9.6:cd3217_analyzer/__init__.py` |
| 4.10 | Truncation persisted in the user's latest export EVEN with the slow-clock merged repair (v0.9.2+ code was in their binary despite the stale label) and board fw 0.9.3 (1 s stretch timeout). Next hypotheses: board power state during probing (asleep SMC), or the stretch fix needs field verification with a KNOWN-good labeled build. | OPEN | v0.9.6-binary export still flagged identity truncation |

## 5. WRONG (disproven — do not repeat)

| # | Disproven idea | What replaced it |
|---|----------------|------------------|
| 5.1 | "Chip-type from address" heuristic (0x74/0x76/0x78 = OTP) — those are 8-bit write forms of strap addresses; A2337's 0x7E chips are strap vanilla. | VID-based identification (v0.8.1 L2); corrected 7-bit maps (v0.8.1 L1) |
| 5.2 | "Any WRONG_VID = genuine wrong chip, not retryable." | Retryable (v0.8.4) — unexpected VID is nearly always wire corruption |
| 5.3 | "Byte-wise chunked reads can repair truncation" (v0.9.0). | Protocol-aware response MERGING (v0.9.1) — every sub-read restarts at the prefix |
| 5.4 | "OTP stride-4 chunks reconstruct a byte-addressable register space" — **VERIFIED-WRONG with clean data**: in the v0.10.0 all-clean export, chunk@0x00 == read@0x00 and chunk@0x04 == read@0x04 exactly, yet the DID read@0x01 (041732cd) does NOT match space[1..4] (28000004). Conclusion: the register pointer selects a 4-byte register; the stride-4 OTP scan never reads pointer 0x01, so a byte-offset DID reconstruction is invalid. Cross-check rewritten (v0.10.1) to compare same-pointer reads only (VID/Type/events, length-aware prefix compare) — the user's real bundle now validates with 0 warnings. | VERIFIED-WRONG → replaced | user's clean A2251 bundle (app 0.10.0, fw 0.10.0) |
| 6.2 | True block addressing: RESOLVED as far as the tool needs — same-pointer reads are byte-identical across datasets; pointer 0x01 returns the DID register (041732cd) which does not compose with the flat space. The exact internal decode (why pointer 0x01 ≠ flat bytes) remains chip-internal and does not affect the tool. | CLOSED (empirically) | clean v0.10.0 bundle |
| 5.5 | "bus_stats.marginal on any NACK" (v0.7.2 first cut) — a dead chip's NACKs flagged the bus. | Recovered-flakiness-only semantics (v0.7.2 final) |
| 5.6 | "Diagnose All must follow a scan" / "targets = scan results" (v0.7.1 bug). | Whole-board target merge (merge_diagnose_targets, v0.7.1) + second-chance pings (v0.7.1/v0.8.3) |
| 5.7 | "is_alive() misses = board removed" (false removals post-OTA). | 8 s grace + 2×absence confirmation + clean close (v0.8.0) |
| 5.8 | "Export name from the interface board" (user-visible bug). | MacBook model first, date-stamped fallback (v0.7.2) |

## 6. OPEN questions (need evidence)

| # | Question | How to answer |
|---|----------|---------------|
| 6.1 | Exact DID framing: is reg 0x01's response `[rev][DID lo-mid-hi]` or `[prefix][3 DID bytes]`? Is the trailing 0x04 in 0xCD321704 a revision? | Capture DID + 0x04-region from a chip with known revision; compare across CD3217B12/B13 |
| 6.2 | True block addressing: do multi-byte reads round the register pointer to 4-byte blocks? (register@0x01 appears to return the block at 0x04 on A2251.) | Single-byte reads at 0x01..0x04 with debug trace on a fully-powered board; compare with 4-byte read content |
| 6.3 | A2442/A2779/A2780 true 7-bit socket addresses (app maps flagged UNVERIFIED). | Schematic + boardview for 820-02100 / 820-02230, or live capture cross-check with strap logic |
| 6.4 | Prefix bytes for 0x14/0x15/0x29/0x2D (0x0B/0x12/0x3F observed once each) — length semantics? | Long trace campaign on a healthy board; correlate prefix vs response length |
| 6.5 | Why the chip truncates on the user's A2251 setup: power state of the board during probing (asleep SMC? VIN_3V3 only?), vs the A2485 capture setup. | Repeat export with the Mac fully awake vs asleep; compare truncation rates (trace has per-read detail) |
| 6.6 | **Truncation is (at least partly) a CLOCK-STRETCH TIMEOUT at the bridge**: RP2040 Wire default timeout is 25 ms; a stretching CD3217 exceeding it gets its remaining response clocked in as 0xFF — exactly the observed signature. v0.9.2 raises the bridge timeout to 1000 ms (both cores) and halves the clock (50 kHz via I2CFREQ) for truncation repair passes. | VERIFIED mechanism (core source: `TwoWire::setTimeout(25)` default), field confirmation pending |
| 6.8 | Wire lengths <10 cm are NOT the truncation cause; the stretch-timeout interface behavior is the primary suspect, wire contact still matters for the 0-bits-as-1s signature. | PROVISIONAL |
| 6.7 | OTP write path: no public method for Apple OTP address programming; TI's documented flash-update-over-I²C is the model. Keep the write stub. | Research only; never experimental-write on a donor |

## 7. Maintenance rules

1. New hardware evidence → add/upgrade a row here in the same commit as
   the code change it justifies.
2. A release that changes interpretation of the protocol must cite a row
   in §2 and update the regression tests that encode it.
3. Never delete WRONG rows — they are the guardrails.
4. The debug trace (Log tab → Debug trace) is the primary evidence
   source; ask users for `cd3217_debug.log` before theorizing.
