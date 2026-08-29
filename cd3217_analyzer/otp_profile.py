"""Golden OTP profiles per board socket.

Collects known-good register dumps for a verified socket (model + refdes)
so a later dump can be verified against them. This is the data foundation
for OTP verification and (eventually) programming vanilla chips:

- `save_profile` — store the dump of a healthy, verified chip as the
  golden reference for its socket.
- `load_profile` / `verify_dump` — compare a live dump against the golden
  reference and report which registers differ.

Writing OTP to a vanilla chip is NOT implemented: the CD3217/CD3218 OTP
burn procedure is not publicly documented. Profiles collected here (and
via the Export feature) build the dataset needed to understand it.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .otp import OTPDump
from .registers import REGISTERS


@dataclass
class OTPProfile:
    """Golden register image for one board socket."""
    model_id: str            # e.g. "A2485"
    ref: str                 # e.g. "UG400"
    address: int             # 7-bit I2C address
    silicon: str = ""        # e.g. "CD3217B12" (from models.py)
    chip_class: str = ""     # "otp" / "vanilla"
    timestamp: str = ""
    source: str = ""         # board / donor description
    registers: Dict[int, bytes] = field(default_factory=dict)
    notes: str = ""


def profile_dir() -> Path:
    """Directory holding golden profiles (bundled app dir or repo root)."""
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))
    # Frozen exe: keep profiles next to the app (writable), not in _MEIPASS
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    d = Path(base) / "otp_profiles"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _profile_path(model_id: str, ref: str) -> Path:
    return profile_dir() / f"{model_id.upper()}_{ref.upper()}.json"


def save_profile(dump: OTPDump, model_id: str, ref: str,
                 silicon: str = "", chip_class: str = "",
                 source: str = "", notes: str = "") -> Path:
    """Write the dump as the golden profile for this socket."""
    p = OTPProfile(
        model_id=model_id.upper(), ref=ref.upper(), address=dump.address,
        silicon=silicon, chip_class=chip_class,
        timestamp=datetime.now().isoformat(),
        source=source or dump.label,
        registers={int(k): bytes(v) for k, v in dump.registers.items()},
        notes=notes,
    )
    path = _profile_path(p.model_id, p.ref)
    path.write_text(json.dumps({
        "model_id": p.model_id, "ref": p.ref, "address": p.address,
        "silicon": p.silicon, "chip_class": p.chip_class,
        "timestamp": p.timestamp, "source": p.source,
        "notes": p.notes,
        "registers": {f"0x{k:02X}": v.hex() for k, v in p.registers.items()},
    }, indent=1))
    return path


def load_profile(model_id: str, ref: str) -> Optional[OTPProfile]:
    """Load the golden profile for a socket, if one exists."""
    path = _profile_path(model_id, ref)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    regs = {}
    for k, v in data.get("registers", {}).items():
        try:
            regs[int(k, 16)] = bytes.fromhex(v)
        except ValueError:
            continue
    return OTPProfile(
        model_id=data.get("model_id", model_id),
        ref=data.get("ref", ref),
        address=int(data.get("address", 0), 16)
        if isinstance(data.get("address"), str) else data.get("address", 0),
        silicon=data.get("silicon", ""), chip_class=data.get("chip_class", ""),
        timestamp=data.get("timestamp", ""), source=data.get("source", ""),
        registers=regs, notes=data.get("notes", ""),
    )


def verify_dump(dump: OTPDump, profile: OTPProfile) -> List[str]:
    """Compare a live dump against the golden profile.

    Returns human-readable summary lines (first line is the verdict).
    Read errors and registers missing from the live dump count as
    mismatches; registers absent from the profile are ignored (the
    profile may be partial).
    """
    lines = []
    diffs: List[int] = []
    missing: List[int] = []
    errors = sorted(dump.read_errors)

    for off in sorted(profile.registers):
        if off not in dump.registers:
            missing.append(off)
            continue
        if dump.registers[off] != profile.registers[off]:
            diffs.append(off)

    total = len(profile.registers)
    ok = total - len(diffs) - len(missing)
    verdict = "MATCH" if not diffs and not missing else "MISMATCH"
    lines.append(
        f"OTP verify {dump.label} vs golden {profile.model_id}/{profile.ref}: "
        f"{verdict} ({ok}/{total} registers match)")
    if profile.silicon or profile.chip_class:
        lines.append(
            f"Golden socket: {profile.ref} @0x{profile.address:02X}"
            + (f", {profile.silicon}" if profile.silicon else "")
            + (f", {profile.chip_class} class" if profile.chip_class else "")
            + (f", source: {profile.source}" if profile.source else ""))
    if diffs:
        lines.append(f"Different registers ({len(diffs)}):")
        for off in diffs:
            reg_name = REGISTERS.get(off).name if REGISTERS.get(off) else "?"
            lines.append(
                f"  0x{off:02X} {reg_name}: golden "
                f"{profile.registers[off].hex()} vs live {dump.registers[off].hex()}")
    if missing:
        lines.append(
            f"Registers missing from live dump ({len(missing)}): "
            + ", ".join(f"0x{o:02X}" for o in missing))
    if dump.read_errors:
        lines.append(
            f"Read errors ({len(dump.read_errors)}): "
            + ", ".join(f"0x{o:02X}" for o in errors))
    return lines


OTP_WRITE_STATUS = (
    "Writing OTP to a vanilla chip is not implemented yet: the CD3217/CD3218 "
    "factory OTP burn procedure is not publicly documented. Collect dumps "
    "from healthy boards (OTP scan + Export) to build the golden dataset."
)
