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
from typing import Callable, Dict, List, Optional

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
        "errors": [],
    }

    has_a2k = adapter is not None

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
            for a in addrs:
                try:
                    rd = analyzer.read_all_registers(a)
                    regs[f"0x{a:02X}"] = {
                        f"0x{o:02X}": {
                            "name": r.name, "raw": r.raw_bytes.hex(),
                            "value": f"0x{r.raw_value:X}",
                            "decoded": r.decoded,
                        }
                        for o, r in rd.items()
                    }
                except Exception as e:
                    bundle["errors"].append(f"registers 0x{a:02X}: {e}")
            bundle["data"]["register_dump"] = regs
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
            for a in addrs:
                try:
                    dump = scan_otp(adapter, a, label=f"0x{a:02X}")
                    otps[f"0x{a:02X}"] = {
                        "address": a,
                        "registers": {
                            f"0x{o:02X}": d.hex()
                            for o, d in dump.registers.items()
                        },
                        "read_errors": dump.read_errors,
                    }
                except Exception as e:
                    bundle["errors"].append(f"otp 0x{a:02X}: {e}")
            bundle["data"]["otp_dump"] = otps
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
    """Write the bundle to ``out_dir/<Name>.json`` and return the path."""
    if out_dir is None:
        out_dir = _data_dir()
    stem = sanitize_name(name)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{stem}.json")
    with open(path, "w") as f:
        json.dump(bundle, f, indent=2)
    return path


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
