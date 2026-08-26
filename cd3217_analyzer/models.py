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
        board_id="820-02100",
        chip_count=4,
        positions=[
            CD3217Position("UB300", 0x20, "otp", notes="Port 1 — Debug/TBT"),
            CD3217Position("UB400", 0x74, "otp", notes="Port 1 — Debug/TBT"),
            CD3217Position("UF500", 0x39, "strap", "GND", 2, "Port 2 — SMC"),
            CD3217Position("UF600", 0x10, "strap", "GND", 2, "Port 2 — SMC"),
        ],
        notes="Same board design as A2442",
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
        board_id="820-01997",
        chip_count=4,
        positions=[
            CD3217Position("UB300", 0x50, "otp", notes="Port 1 — Left"),
            CD3217Position("UB400", 0x28, "otp", notes="Port 1 — Left"),
            CD3217Position("UB700", 0x3C, "otp", notes="Port 2 — Right"),
            CD3217Position("UB800", 0x30, "otp", notes="Port 2 — Right"),
        ],
        notes="All OTP — addresses burned at factory. Cannot use strap-only scan.",
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
