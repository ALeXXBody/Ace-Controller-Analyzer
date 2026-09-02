"""Export a connected board's full diagnostic data for upstream analysis.

Collects whichever data sources the user opts into (device INFO, register
dump, OTP scan, SPI flash ROM, UART capture, diagnostic report) into a single
self-describing JSON bundle named after the MacBook model / board model, then
optionally pushes that file into the project's GitHub repository under the
top-level ``samples/`` folder on the default (``main``) branch using the
GitHub Contents API — easy to browse in the repo root.

Security notes
--------------
* The bundle is written locally to ``samples/<Name>.json`` first so the user
  always has a copy.
* Pushing to GitHub requires a Personal Access Token with ``contents:write``
  (or ``repo``) scope. The token is stored ONLY in a user-local file
  (permissions 0600) or read from the ``CD3217_GH_TOKEN`` environment
  variable — never embedded in the code, and never shipped in the build.
  The repository owner still controls whether the app account can write to
  that branch.
* Flash ROM / OTP dumps can contain board-specific firmware; export them
  only when you intend to share the data.
"""

import base64
import json
import os
import re
import ssl
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

from . import __version__ as APP_VERSION

GITHUB_REPO = "ALeXXBody/cd3217-analyzer"
DATA_BRANCH = "main"          # exports land in samples/ on the default branch
DATA_DIR = "samples"          # top-level folder, easy to browse in the repo

# Which data sources are available (used by both CLI and GUI checklists).
DATA_SOURCES = [
    ("info", "Device INFO frame (board model, firmware, pins)"),
    ("registers", "Full register dump (all documented registers)"),
    ("otp", "OTP register scan 0x00-0x7F"),
    ("flash", "SPI flash ROM dump (contains the ACE ROM firmware)"),
    ("uart", "Latest UART capture (if one was taken)"),
    ("report", "Full diagnostic report"),
]

DATA_DEFAULT = ["info", "registers", "report"]


def sanitize_name(name: str) -> str:
    """Turn a MacBook/board model into a safe file-name stem.

    Keeps letters, digits, spaces, dots and dashes; collapses whitespace;
    upper-cases the A-code (e.g. 'a2141' -> 'A2141'); strips path separators.
    """
    if not name:
        raise ValueError("name is required")
    stem = re.sub(r"[^\w. -]+", "", name).strip().replace(" ", "_")
    stem = re.sub(r"_{2,}", "_", stem)
    if not stem:
        raise ValueError("name contains no usable characters")
    m = re.match(r"^(a\d{3,5})$", stem, re.IGNORECASE)
    if m:
        stem = m.group(1).upper()
    return stem


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def token_path() -> str:
    """Location of the stored GitHub token (user-local, protected file)."""
    base = os.path.expanduser("~")
    d = os.path.join(base, ".cd3217_analyzer")
    return os.path.join(d, "gh_token")


def load_token() -> Optional[str]:
    """Return the stored GitHub token or the CD3217_GH_TOKEN env var."""
    t = os.environ.get("CD3217_GH_TOKEN")
    if t:
        return t.strip() or None
    try:
        with open(token_path(), "r") as f:
            t = f.read().strip()
        return t or None
    except OSError:
        return None


def store_token(token: str) -> None:
    """Persist the token to a user-local file with owner-only permissions."""
    d = os.path.dirname(token_path())
    os.makedirs(d, exist_ok=True)
    fd = os.open(token_path(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token.strip())


# ──────────────────────────────────────────────────────────────────────────
# Bundle collection (best-effort: a failed source does not kill the export)
# ──────────────────────────────────────────────────────────────────────────
def collect_bundle(adapter, selected: List[str], name: str,
                   scan_results: Optional[List[int]] = None,
                   devices: Optional[Dict[int, object]] = None,
                   uart_text: Optional[str] = None,
                   flash: Optional[object] = None,
                   mac_model: Optional[str] = None,
                   progress_cb: Optional[Callable[[str], None]] = None
                   ) -> Dict:
    """Collect the selected data sources into a self-describing bundle.

    ``adapter`` is any connected I2C adapter (UsbBridgeAdapter etc.). Each
    source is optional and guarded; failures are recorded, not fatal.
    """
    def log(msg):
        if progress_cb:
            progress_cb(msg)

    sel = set(selected or [])
    bundle = {
        "format": "cd3217-analyzer/board-export",
        "format_version": 1,
        "name": name,
        "generated_utc": _utcnow(),
        "app_version": APP_VERSION,
        "adapter_type": (type(adapter).__name__ if adapter else None),
        "mac_model": mac_model,
        "sources": sorted(sel),
        "data": {},
        "verification": {},
        "errors": [],
    }

    has_a2k = adapter is not None
    reg_pass_truncated = False   # set by the registers pass; read by OTP

    if "info" in sel:
        log("Reading device INFO frame...")
        try:
            bundle["data"]["info"] = adapter.info()
        except Exception as e:
            bundle["errors"].append(f"info: {e}")
            log(f"  info failed: {e}")

    if "registers" in sel and has_a2k:
        log("Reading registers...")
        from .analyzer import CD3217Analyzer
        try:
            analyzer = CD3217Analyzer(adapter)
            regs: Dict[str, dict] = {}
            addrs = list(scan_results or [])
            if not addrs and devices:
                addrs = sorted(devices.keys())
            if not addrs:
                addrs = analyzer.scan_bus()
            verification_regs = {}
            for idx, a in enumerate(addrs):
                if idx:
                    # Same medicine as Diagnose All: reading chip N+1
                    # immediately after chip N's burst NACKs identity
                    # registers on a probed bus. Settle between chips.
                    time.sleep(0.3)
                try:
                    rd = analyzer.read_all_registers(a)
                    regs_map = {
                        f"0x{o:02X}": {
                            "name": r.name, "raw": r.raw_bytes.hex(),
                            "value": f"0x{r.raw_value:X}",
                            "decoded": r.decoded,
                        }
                        for o, r in rd.items()
                    }
                    # accuracy gate: verify + recheck garbled identity data
                    regs_map, n_rechecks, v_status = _recheck_chip_registers(
                        analyzer, a, regs_map,
                        progress_cb=lambda m: log(m))
                    reg_pass_truncated = reg_pass_truncated or \
                        bool(getattr(analyzer, "truncation_seen", False))
                    regs[f"0x{a:02X}"] = regs_map
                    verification_regs[f"0x{a:02X}"] = {
                        "status": v_status, "rechecks": n_rechecks,
                    }
                except Exception as e:
                    bundle["errors"].append(f"registers 0x{a:02X}: {e}")
            bundle["data"]["register_dump"] = regs
            bundle["verification"]["register_dump"] = verification_regs

            # Golden identity profile per chip, derived from the VERIFIED
            # data (this is what makes the export reusable as a reference).
            from .registers import decode_silicon, parse_device_info
            golden = bundle.setdefault("golden", {})
            for a in addrs:
                regs_map = regs.get(f"0x{a:02X}") or {}
                try:
                    vid = int.from_bytes(
                        bytes.fromhex((regs_map.get("0x00") or {}).get(
                            "raw", "")), "little") & 0xFFFF
                    did = int.from_bytes(
                        bytes.fromhex((regs_map.get("0x01") or {}).get(
                            "raw", "")), "little")
                except Exception:
                    vid = did = None
                ident = parse_device_info(bytes.fromhex(
                    (regs_map.get("0x2F") or {}).get("raw", "") or "00"))
                golden[f"0x{a:02X}"] = {
                    "vid": f"0x{vid:04X}" if vid is not None else None,
                    "did": f"0x{did:08X}" if did is not None else None,
                    "silicon_did": decode_silicon(did) if did else "",
                    "identity_string": ident.raw,
                    "hw_version": ident.hw,
                    "fw_version": ident.fw,
                    "fw_variant": ident.variant,
                }
        except Exception as e:
            bundle["errors"].append(f"register_dump: {e}")
            log(f"  registers failed: {e}")

    if "otp" in sel and has_a2k:
        log("Scanning OTP registers...")
        from .otp import scan_otp
        try:
            addrs = list((scan_results or []) or (devices or {}).keys())
            if not addrs:
                addrs = CD3217Analyzer(adapter).scan_bus()
            otps = {}
            verification_otp = {}
            time.sleep(0.3)   # settle after the register pass
            # If the register pass hit truncation, the chip is slow — scan
            # OTP at half clock (I2CFREQ) and restore afterwards.
            slow_clock = reg_pass_truncated \
                and hasattr(adapter, "set_i2c_clock")
            if slow_clock:
                try:
                    adapter.set_i2c_clock(50_000)
                    log("  OTP pass at 50 kHz (chip truncating at 100 kHz)")
                except Exception:
                    slow_clock = False
            try:
              for idx, a in enumerate(addrs):
                if idx:
                    time.sleep(0.3)
                try:
                    dump = scan_otp(adapter, a, label=f"0x{a:02X}")
                    otp_entry = {
                        "address": a,
                        "registers": {
                            f"0x{o:02X}": d.hex()
                            for o, d in dump.registers.items()
                        },
                        "read_errors": dump.read_errors,
                    }
                    # accuracy gate: verify + rescan low-fill OTP dumps
                    otp_entry, n_rechecks, v_status = _recheck_chip_otp(
                        adapter, a, otp_entry,
                        progress_cb=lambda m: log(m))
                    otps[f"0x{a:02X}"] = otp_entry
                    verification_otp[f"0x{a:02X}"] = {
                        "status": v_status, "rechecks": n_rechecks,
                    }
                except Exception as e:
                    bundle["errors"].append(f"otp 0x{a:02X}: {e}")
            finally:
                if slow_clock:
                    try:
                        adapter.set_i2c_clock(100_000)
                        log("  clock restored to 100 kHz")
                    except Exception:
                        pass
            bundle["data"]["otp_dump"] = otps
            bundle["verification"]["otp_dump"] = verification_otp
            import hashlib
            golden = bundle.setdefault("golden", {})
            for a in addrs:
                otp_entry = otps.get(f"0x{a:02X}") or {}
                blob = "".join(otp_entry.get("registers", {}).get(
                    f"0x{o:02X}", "") for o in range(0x00, 0x80, 4))
                g = golden.setdefault(f"0x{a:02X}", {})
                g["otp_filled"] = len(otp_entry.get("registers", {}))
                g["otp_read_errors"] = len(otp_entry.get("read_errors", []))
                g["otp_sha256"] = hashlib.sha256(
                    blob.encode()).hexdigest()[:16] if blob else None
        except Exception as e:
            bundle["errors"].append(f"otp_dump: {e}")
            log(f"  otp failed: {e}")

    if "flash" in sel and flash is not None:
        log("Reading SPI flash ROM (can be large)...")
        try:
            data = flash.read_all()
            bundle["data"]["flash_dump"] = {
                "length": len(data),
                "hex": data.hex(),
            }
        except Exception as e:
            bundle["errors"].append(f"flash_dump: {e}")
            log(f"  flash failed: {e}")

    if "uart" in sel and uart_text:
        log("Capturing UART log...")
        try:
            bundle["data"]["uart_log"] = uart_text
        except Exception as e:
            bundle["errors"].append(f"uart: {e}")

    if "report" in sel and has_a2k:
        log("Serialising report...")
        from .analyzer import CD3217Analyzer, DiagnosticReport
        try:
            analyzer = CD3217Analyzer(adapter)
            report = analyzer.full_diagnostic()
            if scan_results:
                report.bus_scan_results = list(scan_results)
            if devices:
                report.devices = list(devices.values())
            bundle["data"]["report"] = _serialize_report(report)
        except Exception as e:
            bundle["errors"].append(f"report: {e}")
            log(f"  report failed: {e}")

    log(f"Bundle ready ({len(bundle['data'])} source(s)); "
        f"{len(bundle['errors'])} error(s).")
    return bundle


def _serialize_report(report) -> Dict:
    """Best-effort conversion of a DiagnosticReport to plain JSON-safe dicts."""
    devices = []
    for dev in getattr(report, "devices", []) or []:
        devices.append({
            "address": f"0x{dev.address:02X}",
            "responds": dev.responds,
            "vendor_id": dev.vendor_id,
            "device_id": dev.device_id,
            "mode": dev.mode,
            "device_type": dev.device_type,
            "health": getattr(dev.health, "value", None),
            "health_score": dev.health_score,
            "faults": [getattr(f, "value", str(f)) for f in dev.faults],
            "fault_details": list(dev.fault_details or []),
            "notes": dev.notes,
        })
    return {
        "timestamp": getattr(report, "timestamp", None),
        "adapter_type": getattr(report, "adapter_type", None),
        "bus_scan_results": [
            f"0x{a:02X}" for a in (getattr(report, "bus_scan_results", []) or [])
        ],
        "devices": devices,
        "summary": getattr(report, "summary", None),
        "notes": getattr(report, "notes", None),
    }


def _data_dir() -> str:
    """Default export directory, resolved to a deterministic location.

    Frozen (PyInstaller) builds save next to the executable so the folder is
    easy to find regardless of the process working directory. Source runs save
    to ./samples relative to the file (same as the repo layout).
    """
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, DATA_DIR)


def write_bundle(bundle: Dict, name: str,
                 out_dir: Optional[str] = None) -> str:
    """Write the bundle to ``out_dir/<Name>.json`` and return the path.

    Also writes a ``<Name>.json.sha256`` sidecar so the file's integrity
    can be proven later (validate_bundle checks it; so can `sha256sum -c`).
    """
    if out_dir is None:
        out_dir = _data_dir()
    stem = sanitize_name(name)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{stem}.json")
    with open(path, "w") as f:
        json.dump(bundle, f, indent=2)
    try:
        digest = _sha256_file(path)
        with open(path + ".sha256", "w") as f:
            f.write(f"{digest}  {os.path.basename(path)}\n")
    except Exception:
        pass
    return path


def _sha256_file(path: str) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _identity_problem_count(regs_map: Dict[str, dict]) -> int:
    """How many identity fields in a collected chip register map are
    missing, garbled, or not SEMANTICALLY valid (golden-data grade).

    Semantic checks — the user's exported data must be usable as a golden
    reference, so "present but wrong" counts as bad:
      * VID must be TI (0x0451) or Apple (0x2804) — catches partial
        garbling like 0xFF04
      * DID must decode to a known silicon family (CD3215/17/18) —
        catches garbled top bytes like 0xFF321704
      * DeviceInfo (0x2F) must parse into silicon + FW version — catches
        truncated identity strings ("@CD")
    """
    from .registers import decode_silicon, parse_device_info

    n = 0
    for identity in ("0x00", "0x01", "0x03", "0x04", "0x2F"):
        entry = regs_map.get(identity)
        if not entry:
            n += 1
            continue
        raw = (entry.get("raw") or "").lower()
        if not raw or set(raw) == {"0"} or set(raw) == {"f"}:
            n += 1
            continue
        try:
            if identity == "0x00":
                vid = int.from_bytes(bytes.fromhex(raw), "little") & 0xFFFF
                if vid not in (0x0451, 0x2804):
                    n += 1
            elif identity == "0x01":
                did = int.from_bytes(bytes.fromhex(raw), "little")
                if not decode_silicon(did):
                    n += 1
            elif identity == "0x2F":
                ident = parse_device_info(bytes.fromhex(raw))
                if not (ident.silicon and ident.fw):
                    n += 1
        except Exception:
            n += 1
    return n


# backwards-compatible alias
_identity_garbled_count = _identity_problem_count


def _recheck_chip_registers(analyzer, addr: int, regs_map: Dict[str, dict],
                            progress_cb=None) -> Tuple[Dict[str, dict], int, str]:
    """Verify one chip's collected register data; re-read up to twice when
    identity registers are garbled/missing, keeping the cleanest snapshot.
    Returns (best_map, rechecks_used, status) with status in
    ok / recovered / degraded."""
    from .analyzer import CD3217Analyzer  # for the decode pipeline

    def rebuild(rd):
        return {f"0x{o:02X}": {
            "name": r.name, "raw": r.raw_bytes.hex(),
            "value": f"0x{r.raw_value:X}", "decoded": r.decoded,
        } for o, r in rd.items()}

    initial_bad = _identity_garbled_count(regs_map)
    best_map, best_bad = regs_map, initial_bad
    rechecks = 0
    while best_bad and rechecks < 2:
        rechecks += 1
        if progress_cb:
            progress_cb(f"  0x{addr:02X}: {best_bad} garbled identity "
                        f"register(s) — recheck {rechecks}/2")
        try:
            rd2 = analyzer.read_all_registers(addr)
        except Exception:
            break
        regs2 = rebuild(rd2)
        n2 = _identity_garbled_count(regs2)
        if n2 < best_bad:
            best_map, best_bad = regs2, n2
    if best_bad == 0:
        status = "ok"
    elif best_bad < initial_bad:
        status = "recovered"
    else:
        status = "degraded"
    return best_map, rechecks, status


def _recheck_chip_otp(adapter, addr: int, otp_entry: Dict,
                      progress_cb=None) -> Tuple[Dict, int, str]:
    """Same idea for one chip's OTP scan: if the fill is suspiciously low,
    rescan up to twice and keep the more complete dump."""
    from .otp import scan_otp

    def filled(d):
        return len(d.get("registers") or {})

    def content_ok(d):
        """Fill alone is not enough: a NACK-free but corrupted read
        returns 32/32 'filled' registers full of 0xFF-pattern junk (real
        case: VID bytes reading 0xFF04). Verify the VID bytes."""
        try:
            raw = bytes.fromhex(d.get("registers", {}).get("0x00", ""))
            vid = int.from_bytes(raw[:2], "little")
            return vid in (0x0451, 0x2804)
        except Exception:
            return False

    best, best_filled = otp_entry, filled(otp_entry)
    rechecks = 0
    while (best_filled < 30 or not content_ok(best)) and rechecks < 2:
        rechecks += 1
        if progress_cb:
            progress_cb(f"  0x{addr:02X}: OTP {best_filled}/32 — recheck "
                        f"{rechecks}/2")
        try:
            dump = scan_otp(adapter, addr, label=f"0x{addr:02X}")
        except Exception:
            break
        cand = {
            "address": addr,
            "registers": {f"0x{o:02X}": d.hex()
                          for o, d in dump.registers.items()},
            "read_errors": dump.read_errors,
        }
        if filled(cand) > best_filled:
            best, best_filled = cand, filled(cand)
    if best_filled >= 30 and content_ok(best):
        status = "ok" if rechecks == 0 else "recovered"
    elif rechecks and (best_filled > filled(otp_entry)
                       or content_ok(best)):
        status = "recovered"
    else:
        status = "degraded"
    return best, rechecks, status


def validate_bundle(path: str) -> Dict:
    """Verify an exported bundle was written correctly and is complete.

    Returns a dict:
        {"path":..., "valid": bool, "summary": str,
         "checks": [{"name","level","detail"}...]}
    level: "ok" | "warn" | "critical". valid=False only on criticals
    (unparseable file, wrong format, integrity mismatch, zero chips).
    """
    checks: List[Dict] = []

    def add(name, level, detail=""):
        checks.append({"name": name, "level": level, "detail": detail})

    # 1. file + JSON parse
    if not os.path.exists(path):
        return {"path": path, "valid": False,
                "summary": "file does not exist", "checks": checks}
    try:
        with open(path, "r", encoding="utf-8") as f:
            bundle = json.load(f)
        add("JSON parses", "ok", f"{os.path.getsize(path):,} bytes")
    except Exception as e:
        add("JSON parses", "critical", f"corrupt file: {e}")
        return {"path": path, "valid": False,
                "summary": "file is corrupt (not valid JSON)",
                "checks": checks}

    # 2. format identity
    fmt = bundle.get("format")
    if fmt == "cd3217-analyzer/board-export":
        add("format", "ok", f"version {bundle.get('format_version')}")
    else:
        add("format", "critical", f"unexpected: {fmt!r}")

    # 3. integrity sidecar (proves the file is exactly what was written)
    sidecar = path + ".sha256"
    if os.path.exists(sidecar):
        try:
            with open(sidecar) as f:
                expected = f.read().split()[0].strip().lower()
            actual = _sha256_file(path)
            if actual == expected:
                add("integrity (sha256)", "ok", expected[:16] + "…")
            else:
                add("integrity (sha256)", "critical",
                    "file changed after export (hash mismatch)")
        except Exception as e:
            add("integrity (sha256)", "warn", f"sidecar unreadable: {e}")
    else:
        add("integrity (sha256)", "warn",
            "no .sha256 sidecar (bundle from an older version)")

    # 4. metadata completeness
    for key in ("name", "generated_utc", "app_version", "adapter_type"):
        if bundle.get(key):
            add(f"metadata.{key}", "ok", str(bundle[key])[:40])
        else:
            add(f"metadata.{key}", "warn", "missing")
    if bundle.get("mac_model"):
        add("metadata.mac_model", "ok", str(bundle["mac_model"]))
    else:
        add("metadata.mac_model", "warn",
            "not recorded — export was made without a model selected")

    # 5. per-source completeness
    data = bundle.get("data") or {}
    sources = bundle.get("sources") or []
    chip_count = None
    if "registers" in sources:
        regs = data.get("register_dump") or {}
        chip_count = len(regs)
        if not regs:
            add("register_dump", "critical", "no chips captured")
        else:
            for addr, regs_map in sorted(regs.items()):
                problems = []
                for identity, what in (("0x00", "VID"), ("0x01", "DID"),
                                       ("0x03", "Mode")):
                    entry = regs_map.get(identity)
                    if not entry:
                        problems.append(f"{what} missing")
                        continue
                    raw = (entry.get("raw") or "").lower()
                    if not raw or set(raw) == {"0"} or set(raw) == {"f"}:
                        problems.append(f"{what} garbled (all-0x00/0xFF)")
                # semantic (golden-grade) validation of identity fields
                from .registers import decode_silicon, parse_device_info
                try:
                    did = int.from_bytes(bytes.fromhex(
                        (regs_map.get("0x01") or {}).get("raw", "")),
                        "little")
                    if not decode_silicon(did):
                        problems.append("DID does not decode to a known "
                                        "silicon (garbled)")
                except Exception:
                    problems.append("DID unreadable")
                try:
                    ident = parse_device_info(bytes.fromhex(
                        (regs_map.get("0x2F") or {}).get("raw", "") or "00"))
                    if not (ident.silicon and ident.fw):
                        problems.append(
                            "identity string incomplete/truncated")
                except Exception:
                    problems.append("DeviceInfo unreadable")
                if problems:
                    add(f"chip {addr}", "warn", "; ".join(problems))
                else:
                    add(f"chip {addr}", "ok",
                        f"{len(regs_map)} registers, golden identity valid")

            # golden section present and complete?
            golden = bundle.get("golden") or {}
            for addr in sorted(regs.keys()):
                g = golden.get(addr)
                if not g:
                    add(f"golden {addr}", "warn", "missing golden profile")
                    continue
                missing = [k for k in ("vid", "did", "silicon_did",
                                       "fw_version", "otp_sha256")
                           if not g.get(k)]
                if missing:
                    add(f"golden {addr}", "warn",
                        "incomplete: " + ", ".join(missing))
                else:
                    add(f"golden {addr}", "ok",
                        f"{g.get('silicon_did')} FW{g.get('fw_version')} "
                        f"{g.get('fw_variant', '')}".strip())
    if "otp" in sources:
        otps = data.get("otp_dump") or {}
        for addr, dump in sorted(otps.items()):
            filled = len(dump.get("registers") or {})
            errs = len(dump.get("read_errors") or [])
            if filled >= 30:
                add(f"otp {addr}", "ok", f"{filled}/32 registers")
            else:
                add(f"otp {addr}", "warn",
                    f"only {filled}/32 registers readable "
                    f"({errs} read errors) — rescan recommended")
    if "flash" in sources:
        fd = data.get("flash_dump") or {}
        length = fd.get("length") or 0
        hexlen = len(fd.get("hex") or "")
        if length and hexlen == length * 2:
            add("flash_dump", "ok", f"{length:,} bytes")
        else:
            add("flash_dump", "critical",
                f"size mismatch: length={length}, hex={hexlen // 2} bytes")
    if "info" in sources and not data.get("info"):
        add("info", "warn", "source selected but empty")

    # 5b. collection-time verification (recheck) results
    verif = bundle.get("verification") or {}
    all_v = []
    for section in ("register_dump", "otp_dump"):
        all_v.extend((verif.get(section) or {}).values())
    if all_v:
        bad = [v for v in all_v if v.get("status") not in ("ok", None)]
        rechecked = sum(1 for v in all_v if (v.get("rechecks") or 0) > 0)
        if not bad:
            add("collection recheck", "ok",
                f"{len(all_v)} dataset(s) verified clean"
                + (f", {rechecked} auto-recovered" if rechecked else ""))
        else:
            add("collection recheck", "warn",
                f"{len(bad)}/{len(all_v)} dataset(s) imperfect after "
                f"recheck — consider re-exporting")

    # 5c. data accuracy: unexpected VID / generation mismatch per chip
    if "registers" in sources:
        for addr, regs_map in sorted((data.get("register_dump") or {}).items()):
            vid_entry = (regs_map.get("0x00") or {})
            try:
                vid_raw = vid_entry.get("raw") or ""
                if vid_raw and set(vid_raw) not in ({"0"}, {"f"}):
                    vid = int.from_bytes(bytes.fromhex(vid_raw), "little") & 0xFFFF
                    if vid not in (0x0451, 0x2804):
                        add(f"chip {addr} VID", "warn",
                            f"0x{vid:04X} is neither TI (0x0451) nor Apple "
                            "(0x2804) — wrong chip or garbled read")
            except Exception:
                pass

    # 5d. cross-dataset consistency: DID/VID from the register dump must
    # match the same bytes reconstructed from the independent OTP dump.
    # A mismatch (or 0xFF-heavy data) is the signature of a probe/pull-up
    # drive problem: 0-bits reading as 1s (e.g. 0xCD -> 0xFF).
    regs_data = data.get("register_dump") or {}
    otp_data = data.get("otp_dump") or {}
    for addr in sorted(set(regs_data) & set(otp_data),
                       key=lambda k: int(k, 16)):
        try:
            space = {}
            for o, h in (otp_data[addr].get("registers") or {}).items():
                for i, byte in enumerate(bytes.fromhex(h)):
                    space[int(o, 16) + i] = byte
            otp_vid = int.from_bytes(
                bytes(space.get(o, 0xFF) for o in (0x00, 0x01)), "little")
            otp_did = int.from_bytes(
                bytes(space.get(o, 0xFF) for o in range(0x01, 0x05)),
                "little")
            rd_vid = int.from_bytes(bytes.fromhex(
                (regs_data[addr].get("0x00") or {}).get("raw", "ff" * 4)),
                "little") & 0xFFFF
            rd_did = int.from_bytes(bytes.fromhex(
                (regs_data[addr].get("0x01") or {}).get("raw", "ff" * 4)),
                "little")
            ff_bytes = sum(1 for o in (0x00, 0x01, 0x02, 0x03, 0x04)
                           if space.get(o, 0xFF) == 0xFF)
            if otp_vid != rd_vid or otp_did != rd_did:
                add(f"cross-check {addr}", "warn",
                    f"register dump (VID 0x{rd_vid:04X}, DID 0x{rd_did:08X}) "
                    f"disagrees with OTP dump (VID 0x{otp_vid:04X}, DID "
                    f"0x{otp_did:08X}) — the two independent reads must "
                    "match; they don't, so the wire corrupted data. "
                    "Re-seat the probe, shorten leads, verify 3.3V and "
                    "pull-up strength (0-bits reading as 0xFF = SDA low-"
                    "drive failure at the probe, not chip damage)")
            elif ff_bytes >= 3:
                add(f"cross-check {addr}", "warn",
                    f"{ff_bytes}/5 identity bytes read as 0xFF — probe/"
                    "pull-up low-drive problem: re-seat probe, shorten "
                    "leads, check 3.3V")
        except Exception:
            pass

    # 6. collection errors recorded during export
    errors = bundle.get("errors") or []
    if errors:
        for e in errors:
            add("collection error", "warn", str(e)[:80])

    # 7. model expectation: chips captured vs chips on the board
    mac_model = bundle.get("mac_model")
    if mac_model and chip_count is not None:
        try:
            from .models import get_model
            m = get_model(str(mac_model))
            if m:
                expected = len(m.positions)
                if chip_count >= expected:
                    add("model coverage", "ok",
                        f"{chip_count}/{expected} sockets captured")
                else:
                    add("model coverage", "warn",
                        f"only {chip_count}/{expected} sockets captured — "
                        "run Diagnose All before exporting")
        except Exception:
            pass

    criticals = [c for c in checks if c["level"] == "critical"]
    warns = [c for c in checks if c["level"] == "warn"]
    valid = not criticals
    if not valid:
        summary = f"INVALID — {len(criticals)} critical problem(s)"
    elif warns:
        summary = f"complete with {len(warns)} warning(s)"
    else:
        summary = "complete"
    return {"path": path, "valid": valid, "summary": summary,
            "checks": checks}


# ──────────────────────────────────────────────────────────────────────────
# GitHub push (Contents API) on the data branch
# ──────────────────────────────────────────────────────────────────────────
class GitHubPushError(Exception):
    pass


def _helpful_error(code: int, detail: str = "") -> str:
    """Map common GitHub API error codes to actionable guidance."""
    low = detail.lower()
    if code == 401:
        return ("token is invalid or expired. Generate a new Personal Access "
                "Token (repo scope / fine-grained with contents:write).")
    if code == 403:
        if "rate" in low or "abuse" in low:
            return "rate limit hit; wait a minute and retry."
        return ("token lacks write permission to this repo "
                "(docs: contents:write, and classic PAT needs repo scope).")
    if code == 404:
        return ("token has no access to this repo, or you selected the wrong "
                "repository. GitHub returns 404 when the token can't see/write "
                "the repo. For a fine-grained token, select "
                f"{GITHUB_REPO!r} with Contents: Read and write.")
    if code == 422:
        return f"unprocessable entity (conflicting update): {detail}"
    return detail or ""



def _api(url: str, token: str, data: Optional[dict] = None,
         timeout: float = 30.0) -> dict:
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", f"cd3217-analyzer/{APP_VERSION}")
    if data is not None:
        body = json.dumps(data).encode()
        req.add_header("Content-Type", "application/json")
        req.data = body
    try:
        context = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=timeout, context=context) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode()[:300]
        except Exception:
            pass
        msg = _helpful_error(e.code, detail)
        raise GitHubPushError(
            f"GitHub API {e.code} {e.reason}: {msg}".strip())


def _base_url(repo: str) -> str:
    return f"https://api.github.com/repos/{repo}"


def ensure_data_branch(token: str, repo: str = GITHUB_REPO) -> str:
    """Resolve the branch to push exports to (the repo default branch).

    Exports now land in the top-level ``samples/`` folder on the default
    branch so they're easy to browse. The default branch name is read live
    (falling back to ``main``).
    """
    try:
        rep = _api(f"{_base_url(repo)}", token)
        return rep.get("default_branch", DATA_BRANCH)
    except GitHubPushError:
        return DATA_BRANCH


def push_bundle(bundle: Dict, name: str,
                token: Optional[str] = None,
                repo: str = GITHUB_REPO,
                branch: str = DATA_BRANCH,
                message: Optional[str] = None,
                progress_cb: Optional[Callable[[str], None]] = None,
                timeout: float = 60.0) -> str:
    """Push a bundle to ``repo:samples/<Name>.json`` on the default branch.

    Returns the URL of the committed file. Raises GitHubPushError on failure.
    """
    token = token or load_token()
    if not token:
        raise GitHubPushError(
            "No GitHub token. Set CD3217_GH_TOKEN or store one via the "
            "Export dialog / '--set-token' before pushing.")

    stem = sanitize_name(name)
    path = f"{DATA_DIR}/{stem}.json"
    content = json.dumps(bundle, indent=2).encode()

    def log(m):
        if progress_cb:
            progress_cb(m)

    log("Verifying/creating data branch...")
    branch = ensure_data_branch(token, repo)

    log(f"Checking for existing file {path}...")
    blob = None
    try:
        existing = _api(
            f"{_base_url(repo)}/contents/{path}?ref={branch}", token)
        blob = existing.get("sha")
    except GitHubPushError as e:
        if "404" not in str(e):
            raise

    body = {
        "message": message or f"Add {stem} board data export",
        "content": base64.b64encode(content).decode(),
        "branch": branch,
    }
    if blob:
        body["sha"] = blob

    log(f"Uploading {stem}.json to {repo} ({branch})...")
    created = _api(
        f"{_base_url(repo)}/contents/{path}", token, body, timeout=timeout)
    return created.get("html_url", f"{_base_url(repo)}/blob/{branch}/{path}")
