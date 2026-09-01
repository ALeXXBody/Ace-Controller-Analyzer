from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass(frozen=True)
class CD3217Position:
    """A single CD3217B12 chip position on a MacBook logic board."""
    ref: str
    address: int
    addressing: str  # "strap" or "otp"
    addr_pin: Optional[str] = None  # "GND", "VCC", "float", etc.
    i2c_port: int = 1
    notes: str = ""
    # Verified socket expectations (schematic/boardview + repair data):
    chip_class: str = ""   # "otp" = needs OTP-ed Apple donor, "vanilla" = strap-addressed TI part OK
    silicon: str = ""      # e.g. "CD3217B12", "CD3218B12"


@dataclass(frozen=True)
class MacBookModel:
    """I2C address map for a specific MacBook model."""
    model_id: str
    name: str
    board_id: str
    chip_count: int
    positions: List[CD3217Position]
    notes: str = ""


MACBOOK_MODELS: Dict[str, MacBookModel] = {
    # ── 2-port models (M1/M2) ──────────────────────────────────────
    "A2337": MacBookModel(
        model_id="A2337",
        name="MacBook Air M1 (2020)",
        board_id="820-02016",
        chip_count=2,
        positions=[
            # The schematic lists 0x70/0x7E — those are the 8-BIT WRITE
            # forms (0x38<<1, 0x3F<<1). The chips ANSWER at the 7-bit
            # addresses 0x38/0x3F, same straps as A2338 (GND / float).
            # Verified from strap analysis (badcaps 2026, BoardRev video).
            CD3217Position("UF400", 0x38, "strap", "GND", 2),
            CD3217Position("UF500", 0x3F, "strap", "float", 2),
        ],
    ),
    "A2338": MacBookModel(
        model_id="A2338",
        name="MacBook Pro 13\" M1 (2020)",
        board_id="820-02020",
        chip_count=2,
        positions=[
            CD3217Position("UF400", 0x38, "strap", "GND", 2),
            CD3217Position("UF500", 0x3F, "strap", "float", 2),
        ],
    ),
    "A2179": MacBookModel(
        model_id="A2179",
        name="MacBook Air 13\" i5 (2020, Intel)",
        board_id="820-01996",
        chip_count=2,
        positions=[
            # Same strap pattern as A2337; schematic 0x70/0x7E are 8-bit
            # write forms — the real 7-bit addresses are 0x38/0x3F.
            CD3217Position("UF400", 0x38, "strap", "GND", 2),
            CD3217Position("UF500", 0x3F, "strap", "float", 2),
        ],
    ),
    "A2289": MacBookModel(
        model_id="A2289",
        name="MacBook Pro 13\" i5 (2020, Intel, 2-port)",
        board_id="820-01987",
        chip_count=2,
        positions=[
            CD3217Position("U3100", 0x38, "strap", "GND", 1,
                           chip_class="vanilla", silicon="CD3217B12",
                           notes="ACE XA, CD3217B12 — left-front port; "
                                 "I2C_ADDR pin 111 to GND (pair primary)"),
            CD3217Position("U3200", 0x3F, "strap", "float", 1,
                           chip_class="vanilla", silicon="CD3217B12",
                           notes="ACE XB, CD3217B12 — left-rear port; "
                                 "I2C_ADDR pin 111 floating (pair secondary)"),
        ],
        notes=("Verified from 820-01987 schematic + boardview (X1782, 2-port "
               "Touch Bar): only 2 ACE controllers — U3100=XA@0x38 (write "
               "0x70), U3200=XB@0x3F (write 0x7E), both CD3217B12 (BOM "
               "353S02158). Both ports are on the LEFT side and connect "
               "through one USB-C tongue flex (J3300 / 821-01646) — a common "
               "failure point; check the flex before blaming the chips. "
               "BOARD_ID=111011."),
    ),
    "A2251": MacBookModel(
        model_id="A2251",
        name="MacBook Pro 13\" i5 (2020, Intel, 4-port)",
        board_id="820-01949",
        chip_count=4,
        positions=[
            CD3217Position("U3100_X", 0x38, "strap", "GND", 1,
                           chip_class="vanilla", silicon="CD3217B12",
                           notes="ACE2, CD3217B12 — LEFT port (pair with T); "
                                 "I2C_ADDR=GND -> 0x38; no own SPI (writes T's)"),
            CD3217Position("U3100_T", 0x3F, "strap", "float", 1,
                           chip_class="vanilla", silicon="CD3217B12",
                           notes="ACE2, CD3217B12 — LEFT port (pair with X); "
                                 "I2C_ADDR=FLOAT -> 0x3F; owns left-pair SPI"),
            CD3217Position("U3100_W", 0x3B, "otp", "float", 1,
                           chip_class="otp", silicon="CD3217B12",
                           notes="ACE2, CD3217B12 — RIGHT port (pair with R); "
                                 "OVERRIDE OTP -> 0x3B; owns right-pair SPI; "
                                 "needs OTP-ed donor"),
            CD3217Position("U3100_R", 0x3C, "otp", "float", 1,
                           chip_class="otp", silicon="CD3217B12",
                           notes="ACE2, CD3217B12 — RIGHT port (pair with W); "
                                 "OVERRIDE OTP -> 0x3C; no own SPI (writes W's); "
                                 "needs OTP-ed donor"),
        ],
        notes=("Verified from 820-01949 (X1795) schematic I2C table + "
               "boardview (BVRAW_FORMAT_3). Four ACE2 (CD3217B12) controllers "
               "U3100_X/T/W/R. Addresses: X=GND->0x38, T=FLOAT->0x3F "
               "(both vanilla strap); W=0x3B and R=0x3C via OVERRIDE OTP "
               "(need OTP-ed Apple donors). Alternate/all-call = 0x6B (over "
               "I2C1 and I2C2). Right side (J3300_CWR) = ports R,W; left "
               "side (J3300_CXT) = ports T,X. Left-pair SPI on U3100_T, "
               "right-pair SPI on U3100_W."),
    ),

    # ── 4-port models (M1 Pro/Max) ────────────────────────────────
    "A2442": MacBookModel(
        model_id="A2442",
        name="MacBook Pro 14\" M1 Pro/Max (2021)",
        board_id="820-02100",
        chip_count=4,
        positions=[
            CD3217Position("UB300", 0x20, "otp", notes="Port 1 — Debug/TBT (SDA=B5, SCL=A4)"),
            CD3217Position("UB400", 0x74, "otp", notes="Port 1 — Debug/TBT"),
            CD3217Position("UF500", 0x39, "strap", "GND", 2, "Port 2 — SMC (SDA=B7, SCL=A6)"),
            CD3217Position("UF600", 0x10, "strap", "GND", 2, "Port 2 — SMC"),
        ],
        notes=("UNVERIFIED map: repair.wiki quotes the A2442 schematic I2C0 "
               "table as 7-bit 0x38/0x3F/0x3B/0x3A (8-bit 0x70/0x7E/0x76/0x74) "
               "+ all-call 0x6B — which contradicts the addresses here. Do "
               "not trust placement verdicts on this model until verified "
               "against the 820-02100 schematic + boardview."),
    ),
    "A2485": MacBookModel(
        model_id="A2485",
        name="MacBook Pro 16\" M1 Pro/Max (2021)",
        board_id="820-02382",
        chip_count=4,
        positions=[
            CD3217Position("UF400", 0x38, "strap", "GND", 1,
                           chip_class="vanilla", silicon="CD3217B12",
                           notes="ACE2-0, CD3217B12 — system power path "
                                 "(dead => stuck at 5V); I2C_ADDR pin 111 to GND"),
            CD3217Position("UF500", 0x3F, "strap", "float", 1,
                           chip_class="vanilla", silicon="CD3217B12",
                           notes="ACE2-1, CD3217B12 — data port controller; "
                                 "I2C_ADDR pin 111 floating (NC)"),
            CD3217Position("UG400", 0x3B, "strap", "GND", 1,
                           chip_class="otp", silicon="CD3217B12",
                           notes="ACE2-2, CD3217B12 — data port controller; "
                                 "I2C_ADDR pin 111 to GND via RG201"),
            CD3217Position("U5500", 0x3A, "strap", "float", 0,
                           chip_class="otp", silicon="CD3218B12",
                           notes="ACE2-5, CD3218B12 — system/charge controller; "
                                 "I2C_ADDR pin 111 floating (NC)"),
        ],
        notes=("Verified from 820-02382 schematic + boardview: ACE2-0=UF400@0x38, "
               "ACE2-1=UF500@0x3F, ACE2-2=UG400@0x3B, ACE2-5=U5500@0x3A; "
               "BANK ALL CALL (broadcast, not a device) @0x6B on both the UPC "
               "and SMC_UPC buses. Probe test point JF200: SDA=pin 4 "
               "(I2C_UPC_SDA), SCL=pin 6 (I2C_UPC_SCL), GND=pins 13-16."),
    ),

    # ── 4-port models (M2 Pro/Max) ────────────────────────────────
    "A2779": MacBookModel(
        model_id="A2779",
        name="MacBook Pro 14\" M2 Pro/Max (2023)",
        board_id="820-02230",
        chip_count=4,
        positions=[
            CD3217Position("UB300", 0x20, "otp", notes="Port 1 — Debug/TBT"),
            CD3217Position("UB400", 0x74, "otp", notes="Port 1 — Debug/TBT"),
            CD3217Position("UF500", 0x39, "strap", "GND", 2, "Port 2 — SMC"),
            CD3217Position("UF600", 0x10, "strap", "GND", 2, "Port 2 — SMC"),
        ],
        notes=("Same address map as A2442/A2485 — UNVERIFIED for 820-02230; "
               "repair.wiki's A2442-family schematic table (7-bit "
               "0x38/0x3F/0x3B/0x3A) contradicts it. Verify against the "
               "820-02230 schematic + boardview before trusting verdicts."),
    ),
    "A2780": MacBookModel(
        model_id="A2780",
        name="MacBook Pro 16\" M2 Pro/Max (2023)",
        board_id="820-02230",
        chip_count=4,
        positions=[
            CD3217Position("UB300", 0x20, "otp", notes="Port 1 — Debug/TBT"),
            CD3217Position("UB400", 0x74, "otp", notes="Port 1 — Debug/TBT"),
            CD3217Position("UF500", 0x39, "strap", "GND", 2, "Port 2 — SMC"),
            CD3217Position("UF600", 0x10, "strap", "GND", 2, "Port 2 — SMC"),
        ],
        notes=("Same address map as A2442/A2485 — UNVERIFIED for 820-02230; "
               "repair.wiki's A2442-family schematic table (7-bit "
               "0x38/0x3F/0x3B/0x3A) contradicts it. Verify against the "
               "820-02230 schematic + boardview before trusting verdicts."),
    ),

    # ── 2-port T2 models ──────────────────────────────────────────
    "A2141": MacBookModel(
        model_id="A2141",
        name="MacBook Pro 16\" i9 (2019, T2)",
        board_id="820-01700",
        chip_count=4,
        positions=[
            CD3217Position("U3100", 0x38, "strap", "GND", 1,
                           chip_class="vanilla", silicon="CD3217B12",
                           notes="ACE XA, CD3217B12 — left pair primary; "
                                 "I2C_ADDR pin 111 to GND"),
            CD3217Position("U3200", 0x3F, "strap", "float", 1,
                           chip_class="vanilla", silicon="CD3217B12",
                           notes="ACE XB, CD3217B12 — left pair secondary; "
                                 "I2C_ADDR pin 111 floating (NC)"),
            CD3217Position("UB300", 0x3B, "strap", "GND", 1,
                           chip_class="otp", silicon="CD3217B12",
                           notes="ACE TA, CD3217B12 — right pair primary; "
                                 "I2C_ADDR pin 111 to GND"),
            CD3217Position("UB400", 0x3C, "strap", "float", 1,
                           chip_class="otp", silicon="CD3217B12",
                           notes="ACE TB, CD3217B12 — right pair secondary; "
                                 "I2C_ADDR pin 111 floating (NC)"),
        ],
        notes=("Verified schematic + boardview (820-01700): all four are "
               "CD3217B12BCE (BOM 353S01960; the 'CD3215A' page title is a "
               "stale Apple artifact). U3100=XA@0x38, U3200=XB@0x3F, "
               "UB300=TA@0x3B, UB400=TB@0x3C (write 0x70/7E/76/78). "
               "'GND I2C_ADDR on primary only': XA/TA strapped, XB/TB float."),
    ),
    "A2159": MacBookModel(
        model_id="A2159",
        name="MacBook Pro 13\" i5 (2019, T2)",
        board_id="820-01843",
        chip_count=2,
        positions=[
            CD3217Position("UB300", 0x50, "otp", notes="Port 1"),
            CD3217Position("UB400", 0x28, "otp", notes="Port 1"),
        ],
        notes="OTP — addresses burned at factory.",
    ),
}


def list_models() -> List[MacBookModel]:
    return list(MACBOOK_MODELS.values())


def get_model(model_id: str) -> Optional[MacBookModel]:
    return MACBOOK_MODELS.get(model_id.upper())


def model_ids() -> List[str]:
    return sorted(MACBOOK_MODELS.keys())


def check_model_placement(model: MacBookModel,
                          responding_addresses) -> Dict[int, dict]:
    """Compare live bus scan results against a model's expected sockets.

    Returns a dict keyed by address with one entry per relevant position:
      * every expected socket address that had no responding chip -> MISSING
      * every responding address that no socket expects -> UNEXPECTED

    This catches the classic donor mistakes from repair.wiki before a chip
    takes down the USB-C system:
      - an OTP-ed Apple donor whose burned address does not match the socket
        (responds somewhere the board doesn't use) -> UNEXPECTED
      - a socket left empty / dead / unpowered chip -> MISSING
    """
    broadcast = 0x6B  # ACE2 all-call address — not a device
    pos_by_addr = {p.address: p for p in model.positions}
    out: Dict[int, dict] = {}
    for addr in sorted(responding_addresses):
        if addr == broadcast:
            continue
        if addr in pos_by_addr:
            out[addr] = {
                "ref": pos_by_addr[addr].ref,
                "address": addr,
                "verdict": "OK",
                "message": "chip responds at this socket's expected address",
            }
        else:
            out[addr] = {
                "ref": None,
                "address": addr,
                "verdict": "UNEXPECTED",
                "message": ("no socket on this board uses 0x{addr:02X} — wrong "
                            "OTP donor address or misplaced chip").format(
                                addr=addr),
            }
    for p in model.positions:
        if p.address not in out:
            out[p.address] = {
                "ref": p.ref,
                "address": p.address,
                "verdict": "MISSING",
                "message": (f"{p.ref} expected at 0x{p.address:02X} but nothing "
                            "accepts this address — dead / unpowered / absent, "
                            "or donor with the wrong burned address"),
            }
    return out


def build_placement_guide(model: MacBookModel,
                          responding_addresses) -> List[str]:
    """Step-by-step donor placement guidance from a live scan + model map.

    The repair-shop workflow this automates (repair.wiki donor procedure +
    badcaps A2251 thread): an OTP donor ALWAYS answers at its burned address
    no matter which socket it sits in, so a scan reveals which socket it
    belongs to — the one whose expected address matches. Vanilla (strap)
    chips instead strap to the socket's address once powered from VIN_3V3.

    Returns human-readable instruction lines; empty ``model`` yields [].
    """
    if not model:
        return []
    broadcast = 0x6B  # ACE2 all-call — not a device
    pos_by_addr = {p.address: p for p in model.positions}
    responding = sorted(set(int(a) for a in responding_addresses))
    lines = [
        f"Placement guide — {model.name} ({model.board_id})",
        "Power the board from VBUS (charger) or VIN_3V3 so the chips can "
        "boot and answer the bus.",
    ]
    for addr in responding:
        if addr == broadcast:
            continue
        p = pos_by_addr.get(addr)
        if p is None:
            lines.append(
                f"0x{addr:02X}: answers, but no socket on this board wants "
                "this address — wrong donor, or an OTP chip burned for a "
                "different board/model.")
        else:
            kind = (f"strap {p.addr_pin}" if p.addressing == "strap"
                    else "OTP — burned address")
            lines.append(
                f"0x{addr:02X}: answers — socket {p.ref} wants exactly this "
                f"address ({kind}). If this chip is not physically in "
                f"{p.ref}, move it there: it answers at its strapped/burned "
                "address regardless of which socket it sits in.")
    for p in model.positions:
        if p.address in responding or p.address == broadcast:
            continue
        if p.addressing == "otp":
            lines.append(
                f"{p.ref} (OTP, expects 0x{p.address:02X}): still empty. "
                "Place any donor chip and re-scan: if it answers at "
                f"0x{p.address:02X} it belongs here; if it answers at a "
                "different address, move it to the socket that wants that "
                "address instead.")
        else:
            lines.append(
                f"{p.ref} (strap {p.addr_pin or '—'}): still empty — a "
                f"vanilla chip will strap to 0x{p.address:02X} once it is "
                "powered from VIN_3V3.")
    return lines
