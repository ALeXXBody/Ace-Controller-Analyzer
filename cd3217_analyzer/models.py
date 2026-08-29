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
            CD3217Position("UF400", 0x70, "strap", "GND", 2),
            CD3217Position("UF500", 0x7E, "strap", "float", 2),
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
            CD3217Position("UF400", 0x70, "strap", "GND", 2),
            CD3217Position("UF500", 0x7E, "strap", "float", 2),
        ],
    ),
    "A2289": MacBookModel(
        model_id="A2289",
        name="MacBook Pro 13\" i5 (2020, Intel)",
        board_id="820-01987",
        chip_count=2,
        positions=[
            CD3217Position("UF400", 0x38, "strap", "GND", 2),
            CD3217Position("UF500", 0x3F, "strap", "float", 2),
        ],
    ),
    "A2251": MacBookModel(
        model_id="A2251",
        name="MacBook Pro 13\" i5 (2020, Intel, 4-port)",
        board_id="820-01958",
        chip_count=2,
        positions=[
            CD3217Position("UF400", 0x38, "strap", "GND", 2),
            CD3217Position("UF500", 0x3F, "strap", "float", 2),
        ],
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
    ),
    "A2485": MacBookModel(
        model_id="A2485",
        name="MacBook Pro 16\" M1 Pro/Max (2021)",
        board_id="820-02382",
        chip_count=4,
        positions=[
            CD3217Position("UF400", 0x38, "strap", "GND", 1,
                           notes="ACE2-0 — system power path (dead => stuck at 5V)"),
            CD3217Position("UF500", 0x3F, "strap", "float", 1,
                           notes="ACE2-1 — data port controller"),
            CD3217Position("UG400", 0x3B, "strap", "float", 1,
                           notes="ACE2-2 — data port controller"),
            CD3217Position("U5500", 0x3A, "strap", "GND", 0,
                           notes="ACE2-3 — CD3218B12 system/charge controller"),
        ],
        notes=("Verified schematic address map (820-02382): ACE2-0=UF400@0x38, "
               "ACE2-1=UF500@0x3F, ACE2-2=UG400@0x3B, ACE2-3=U5500@0x3A; "
               "all-call/broadcast @0x6B (not a device). Probe test point JF200."),
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
        notes="Same address map as A2442/A2485",
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
        notes="Same address map as A2442/A2485",
    ),

    # ── 2-port T2 models ──────────────────────────────────────────
    "A2141": MacBookModel(
        model_id="A2141",
        name="MacBook Pro 16\" i9 (2019, T2)",
        board_id="820-01700",
        chip_count=4,
        positions=[
            CD3217Position("U3100", 0x38, "strap", "GND", 1,
                           notes="ACE XA — left front port (I2C_UPC_X)"),
            CD3217Position("U3200", 0x3F, "strap", "float", 1,
                           notes="ACE XB — left rear port (I2C_UPC_X)"),
            CD3217Position("UB300", 0x3B, "strap", "float", 1,
                           notes="ACE TA — right front port (I2C_UPC_T)"),
            CD3217Position("UB400", 0x3C, "strap", "GND", 1,
                           notes="ACE TB — right rear port (I2C_UPC_T)"),
        ],
        notes=("Verified schematic + boardview (820-01700): CD3215A (ACE1) gen. "
               "U3100=XA@0x38, U3200=XB@0x3F, UB300=TA@0x3B, UB400=TB@0x3C "
               "(write 0x70/7E/76/78). No 0x6B broadcast on this gen."),
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
