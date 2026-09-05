# Task Queue (FIFO)

Every user message that carries a task/finding is appended here and
processed **oldest-first**. Rules:
1. Append verbatim; never delete entries — mark with a status.
2. Statuses: `PENDING` → `IN-PROGRESS` → `DONE` (with result notes) or
   `WONTFIX` (with reason).
3. The oldest PENDING item is always the next one worked on.
4. Hardware-dependent items stay PENDING until the hardware arrives.

---

## Queue

### 1. [DONE] Test points: JG200 (820-02098), JF200 (820-02443), JF200 (820-02890)
> "820-02098 - I2c test point - JG200 / 820-02443 - JF200 / 820-02890 - JF200"

**Result (v0.10.5):** boards.py connect instructions updated for all
three boards. Schematic verification: 820-02890 JF200 carries the UPC
I²C nets (I2C_UPC_SDA/SCL + I2C_UPC01_3V3_SDA/SCL + SMC mirror +
DFU/UART pins — the CD3217s UF400/UF500/UG400 sit on the 3V3-side
I2C_UPC01 bus). 820-02443 JF200 and 820-02098 JG200 encoded as
user-verified I²C test connectors.

### 2. [DONE] Apply the VERIFIED A2442 map (from the 820-02443 schematic table)
> From the schematic's own address table: ACE2-0=0x38, ACE2-1=0x3F,
> ACE2-2=0x3B, ACE2-5=0x3A, all-call 0x6B; chips UF400/UF500/UG400/U5500
> (CD3217B12BCE).

**Result (v0.10.5):** models.py A2442 rewritten to
UF400@0x38 / UF500@0x3F / UG400@0x3B / U5500@0x3A (strap notes per the
schematic: GND / NC / GND+OTP / Float+OTP), UNVERIFIED warning removed,
regression test added. Ledger §6.3 updated.

### 6. [DONE] Process the new schematics (A2681/A2992/A3113/A3114)
> "added some new schematics on mega"

**Result (v0.11.5):** A2681 map VERIFIED (UF400@0x38, UF500@0x3F,
U5500@0x3A; new silicon CD3217B13HACE) — '*' cleared. A2992/A3113/
A3114: M3-era ACE3-generation controllers, part numbers don't extract
from the PDFs and straps are NC — remain '*' pending a live capture
(protocol compatibility with the TI register map is unknown).

### 9. [DONE] Auto-baud window configurable (user: "auto-baud time is too short 1.5 sec")
**Result (v0.11.10):** CMD 0x24 v2 [pin][window LE32 ms]; GUI auto-baud
measures 15 s with a power-cycle hint. Recapture the UF400/UF500
SER_DBG lines with the 15 s window + power-on to catch the boot logs.

### 11. [UPDATED] A2141 UB pair — the shared W25Q80 ROM is the unified suspect
Loader-default theory disproven (no extra device on rescan). The pair
runs the TI slave-flash scheme: UB400 (primary) loads from the shared
ROM then programs UB300 over I2C. Corrupt/blank ROM = both symptoms.
NEXT: Flash tab → Detect (W25Q80 JEDEC EF4014) → Dump → blank/corrupt/
valid decides. Repair: re-program from a same-model (A2141) golden
dump — the ROM carries the A2141-specific port config.

### 11. [PENDING] A2141 UB300 (0x3B) — the no-answer chip
The v0.12.6 session: 3/4 sockets read (50 kHz fallback ✓), UB300
missing from scan + all passes. Next: check UB300's I2C_ADDR strap
component (820-01700 boardview) — a secondary chip's address comes
from that net ("ADDR bits 3,2,1"); a broken strap = never answers.
Then the chip itself.

### 7. [PENDING] Live captures for the starred boards (user hardware)
> "A1932 A1989 A1990 A2992 A3..." — schematic table audit showed none of
> these publish port-controller addresses (all ACE/CD32 rows absent).

Live-capture procedure per board: connect ACA via the known test
points, Scan Bus, Diagnose All, export, send the JSON. From the capture
I fill the model map and clear the '*'. Boards: A1932, A1989, A1990
(CD3215 ACE1 — protocol-compatible), A2992/A3113/A3114 (ACE3 — capture
also answers whether the TI register protocol applies at all).

### 3. [PENDING] Verify A2289/A2338 (820-02098) socket map
The schematic's ACE2 table body was found (ACE2-0/1/2/5 + BANK 1 ALL
CALL) but the address column is layout-scrambled. Options: re-extract
with a column-aware PDF parser, or capture live addresses on the board
(now easy: JG200 test point, user-verified). Models currently hold the
old placeholder map.

### 8. [DONE] Cold-bus warm-up + bridge self-reset (v0.11.8)
> Logs showed empty scans / 100% failures after connect that turned
> clean after minutes of activity, plus "Write timeout" bridge stalls.

**Result:** first scan auto-warms (6 passes / ~20 s); firmware uses
Wire.setTimeout(1000, true) — the I2C peripheral self-resets after a
stuck-bus timeout instead of stalling the USB-serial writer.

### 10. [PENDING] T824 pin-test integration (measurements deferred)
Owner has a Mechanic T824 (Nuvoton ML51TC0AE controller, NuLink header
R/C/D/G/3V3, Lightning + USB-C heads, LCD pin-connect verdicts). C/D
measure 0 V at idle. Deferred pending more owner measurements:
  - boot-time UART sniff on C/D with the ACA bridge (one try)
  - NuLink programmer for the ML51 flash (definitive data tap)
  - ACA integration: per-model reference diagrams + pin measurement
    log in the golden bundle (A2681 reference JPGs already uploaded)

### 4. [PENDING] New shield rail-stability validation
> Waiting on the user's new shield (no AMS1117, pull-ups from the
> bridge's internal 3V3).

Run Bus Check on the OLD shield (capture blip counts) and on the NEW
shield — the contrast proves the AMS1117 fix (findings §3.8). Ledger
§3.8 field-confirmation pending.

### 12. [IN-PROGRESS] A2141 UB090 W25Q80 ROM dumps — repair vs donor vs archive
> "Here you have 3 rom's: https://mega.nz/folder/LPYTDYBI#AE5DAmf9g_fKeuFYfGUk8A
> the one ending with "repair" is the one from the board that i need to
> repair, the one ending with "donor" is the one from donor board
> (UB300 -20v) and the 3rd one is just a dump I had in the computer"

Dumps stored at samples/roms_a2141/. Initial analysis:
  - All three populated (15–19% FF), none blank/garbage.
  - repair ≈ archive (pc): byte-identical 0x0000–0x082000; pc is the
    same-variant golden candidate for the repair board.
  - donor (-20v) diverges from pc from 0x007000 on — different sub-variant
    firmware, NOT a same-model golden for the repair board.
  - repair's own divergent regions (0x085000–0x0a2000 ~92% differ and
    0x0e9000–0x0fb000 ~97% differ against BOTH donor and pc) = per-device
    data, not a copy of either → repair chip content is NOT provably corrupt.
  - UPDATED (owner field session): after the donor ROM swap, UB300 (0x3B)
    is "missing" on I2C but is the ONLY port negotiating 20 V. The three
    ROM images are NOT byte-identical — the donor carries a different
    sub-variant config, which is the likely cause (its address-table/config
    only lines up with that one slot). OTP-fixed addresses mean the donor
    ROM cannot re-address a chip.
  - UPDATED (clean export): all four chips answered after rework (bus was
    marginal: 17% failed attempts), golden identities valid (ZACE2-J152FX
    @0x38/0x3F, ZACE2-J152FT @0x3B/0x3C), OTP 32/32 everywhere. One false
    cross-check WARN @0x14 (live register) — fixed in export_data.py.
  - NEXT: decide reflash target (pc image) + confirm tool, per owner.
  - NEXT: owner to test 20 V on all four ports (acceptance test).

### 5. [PENDING] Truncation follow-up (ledger §4.10)
The v0.10.0/0.10.1 exports were clean (0 warnings), but §4.10 notes the
truncation-repair path was exercised on the old shield. With the new
shield, confirm truncation stays gone at full 100 kHz (no 50 kHz
degraded-mode activations in the debug trace).
