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

### 3. [PENDING] Verify A2289/A2338 (820-02098) socket map
The schematic's ACE2 table body was found (ACE2-0/1/2/5 + BANK 1 ALL
CALL) but the address column is layout-scrambled. Options: re-extract
with a column-aware PDF parser, or capture live addresses on the board
(now easy: JG200 test point, user-verified). Models currently hold the
old placeholder map.

### 4. [PENDING] New shield rail-stability validation
> Waiting on the user's new shield (no AMS1117, pull-ups from the
> bridge's internal 3V3).

Run Bus Check on the OLD shield (capture blip counts) and on the NEW
shield — the contrast proves the AMS1117 fix (findings §3.8). Ledger
§3.8 field-confirmation pending.

### 5. [PENDING] Truncation follow-up (ledger §4.10)
The v0.10.0/0.10.1 exports were clean (0 warnings), but §4.10 notes the
truncation-repair path was exercised on the old shield. With the new
shield, confirm truncation stays gone at full 100 kHz (no 50 kHz
degraded-mode activations in the debug trace).
