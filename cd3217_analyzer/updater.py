"""Self-update mechanism for the CD3217 Analyzer app.

Checks the latest GitHub release and updates whichever build is running:

- **Installed** (Inno Setup): downloads the new ``ACA_Setup.exe``
  and launches it with Inno's silent flags — Setup closes the running app via
  the Restart Manager, replaces files and restarts the app.
- **Portable**: downloads ``ACA_Portable.zip``, extracts it next to the
  current folder and spawns a small PowerShell swapper that waits for the app
  to exit, swaps the folders, restarts the app and cleans up.
- **Source** (``python gui.py``): no in-place update — just opens the
  releases page.

Stdlib only (urllib / zipfile / subprocess / winreg) — no new dependencies.
"""

import json
import os
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from typing import Callable, Optional

GITHUB_REPO = "ALeXXBody/cd3217-analyzer"
API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases"
SETUP_ASSET = "ACA_Setup.exe"
PORTABLE_ASSET = "ACA_Portable.zip"
APP_EXE = "ACA.exe"

# Board name (from the bridge INFO frame) -> release firmware asset.
BOARD_FIRMWARE_ASSETS = {
    "pico1": "aca_pico.uf2",
    "pico2": "aca_pico2.uf2",
    "pico-w": "aca_pico_w.uf2",
    "pico2-w": "aca_pico2w.uf2",
    "rp2040-zero": "aca_rp2040_zero.uf2",
    "esp32-s3-devkitc-1": "aca_esp32s3.bin",
    "esp32-c3-supermini": "aca_esp32c3.bin",
    "esp32-devkit": "aca_esp32.bin",
}
# Inno Setup writes the uninstall key "<AppId>_is1" (per-user install).
_UNINSTALL_KEY = (r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
                  r"\{8E4C3D2A-9F6B-4E7A-B1C5-CD3217B12ANLZ}_is1")

UA = f"cd3217-analyzer-updater/{getattr(__import__('cd3217_analyzer'), '__version__', '?')}"


# ─── version compare ─────────────────────────────────────────────────────────

def parse_version(v: str) -> tuple:
    """'v0.4.10' -> (0, 4, 10); malformed parts are ignored."""
    parts = (v or "").strip().lstrip("vV").split(".")
    out = []
    for p in parts:
        digits = ""
        for ch in p:
            if ch.isdigit():
                digits += ch
            else:
                break
        out.append(int(digits) if digits else 0)
    return tuple(out) if out else (0,)


def is_newer(remote: str, local: str) -> bool:
    """True when ``remote`` is a strictly newer dotted version than ``local``."""
    r, l = parse_version(remote), parse_version(local)
    # pad to equal length so 0.4 == 0.4.0
    n = max(len(r), len(l))
    r += (0,) * (n - len(r))
    l += (0,) * (n - len(l))
    return r > l


# ─── release discovery ───────────────────────────────────────────────────────

def fetch_latest_release(timeout: float = 6.0) -> Optional[dict]:
    """Return the latest release info dict, or None on any network error.

    Dict: {tag, version, setup_url, portable_url, url, notes}
    """
    req = urllib.request.Request(
        API_URL, headers={"User-Agent": UA,
                          "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except Exception:
        return None
    assets = {a["name"]: a["browser_download_url"]
              for a in data.get("assets", []) if a.get("name")}
    tag = data.get("tag_name") or ""
    return {
        "tag": tag,
        "version": tag.lstrip("vV"),
        "setup_url": assets.get(SETUP_ASSET),
        "portable_url": assets.get(PORTABLE_ASSET),
        "url": data.get("html_url") or RELEASES_URL,
        "notes": (data.get("body") or "")[:2000],
        "assets": assets,          # every asset name -> download url
    }


def check_for_update(current_version: str,
                     timeout: float = 6.0) -> Optional[dict]:
    """Return the release dict when it is newer than current, else None."""
    rel = fetch_latest_release(timeout)
    if not rel or not rel["version"]:
        return None
    return rel if is_newer(rel["version"], current_version) else None


# ─── install-mode detection ──────────────────────────────────────────────────

def install_mode() -> str:
    """"installed" | "portable" | "source"."""
    if not getattr(sys, "frozen", False):
        return "source"
    if os.name == "nt":
        try:
            import winreg
            app_dir = os.path.dirname(os.path.abspath(sys.executable))
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _UNINSTALL_KEY) as k:
                loc, _ = winreg.QueryValueEx(k, "InstallLocation")
            if os.path.normcase(os.path.abspath(loc)) == \
                    os.path.normcase(app_dir):
                return "installed"
        except OSError:
            pass
        except ImportError:
            pass
    return "portable"


# ─── download ────────────────────────────────────────────────────────────────

def download_file(url: str, dest: str,
                  progress_cb: Optional[Callable] = None,
                  timeout: float = 30.0) -> str:
    """Stream ``url`` to ``dest``; progress_cb(done, total) if given."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress_cb:
                    progress_cb(done, total)
    # Strip the Mark-of-the-Web: a downloaded Setup.exe with MOTW can trip
    # SmartScreen during the silent self-update and the install never runs.
    if os.name == "nt":
        try:
            os.remove(dest + ":Zone.Identifier")
        except OSError:
            pass
    return dest


def board_firmware_asset(board: str) -> Optional[str]:
    """Release asset filename for a board INFO name (None if unsupported)."""
    return BOARD_FIRMWARE_ASSETS.get((board or "").strip().lower())


def download_board_firmware(board: str, release: dict,
                            progress_cb: Optional[Callable] = None) -> str:
    """Download the firmware asset for ``board`` into a temp file.

    Returns the local path. Raises IOError when the board has no asset on
    the release or the download fails.
    """
    name = board_firmware_asset(board)
    if not name:
        raise IOError(f"No firmware image available for board "
                      f"'{board}' on GitHub releases")
    rel = release or {}
    assets = rel.get("assets") or {}
    if not assets:
        rel = fetch_latest_release()
        assets = (rel or {}).get("assets") or {}
    url = assets.get(name)
    if not url:
        raise IOError(f"Firmware '{name}' is missing from the latest "
                      "release")
    tmp = tempfile.mkdtemp(prefix="cd3217_fw_")
    return download_file(url, os.path.join(tmp, name), progress_cb)


# ─── update application ──────────────────────────────────────────────────────

def _find_app_dir(extract_root: str) -> Optional[str]:
    """Locate the folder containing the exe inside an extracted zip."""
    if os.path.isfile(os.path.join(extract_root, APP_EXE)):
        return extract_root
    for name in os.listdir(extract_root):
        cand = os.path.join(extract_root, name)
        if os.path.isdir(cand) and \
                os.path.isfile(os.path.join(cand, APP_EXE)):
            return cand
    return None


_SWAP_PS1 = r"""
param([int]$oldPid, [string]$appDir, [string]$newDir)
$ErrorActionPreference = 'Stop'
$log = Join-Path (Split-Path $appDir -Parent) 'update.log'
function L($m){ Add-Content -Path $log -Value "$(Get-Date -Format s)  $m" }
try {
  L "updater started (pid=$oldPid appDir=$appDir newDir=$newDir)"
  $p = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
  if ($p) { try { $p | Wait-Process -Timeout 90 } catch { L "wait timeout" } }
  Start-Sleep -Milliseconds 800
  $old = "$appDir.old"
  if (Test-Path $old) { Remove-Item -Recurse -Force $old }
  Rename-Item -Path $appDir -NewName ((Split-Path $appDir -Leaf) + '.old')
  Move-Item -Path $newDir -Destination $appDir
  L "folders swapped; starting app"
  $exe = Join-Path $appDir 'ACA.exe'
  for ($i = 0; $i -lt 10; $i++) {
    try { Start-Process -FilePath $exe -ErrorAction Stop; break } catch {
      L "start attempt $($i+1) failed: $_"; Start-Sleep -Milliseconds 1000
    }
  }
  for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 500
    try { Remove-Item -Recurse -Force $old -ErrorAction Stop; break } catch {}
  }
  Remove-Item -Recurse -Force (Split-Path $newDir -Parent) -ErrorAction SilentlyContinue
  L "update complete"
} catch {
  L "ERROR: $_"
}
"""


def apply_update(release: dict, progress_cb: Optional[Callable] = None
                 ) -> dict:
    """Download and launch the update for the running install mode.

    Returns a dict describing what was done:
        {"mode": ..., "action": "setup-launched" | "swap-launched" | "browser"}
    The caller should exit the app afterwards when action == "swap-launched"
    (the swapper waits for the process to die). For "setup-launched" Inno
    Setup closes/restarts the app itself.
    Raises IOError/ValueError on failure.
    """
    mode = install_mode()

    if mode == "source":
        import webbrowser
        webbrowser.open(release.get("url") or RELEASES_URL)
        return {"mode": mode, "action": "browser"}

    if mode == "installed":
        url = release.get("setup_url")
        if not url:
            raise IOError("Installer asset not found on the latest release")
        tmp = tempfile.mkdtemp(prefix="cd3217_setup_")
        setup = os.path.join(tmp, SETUP_ASSET)
        download_file(url, setup, progress_cb)
        # /SILENT: progress only, no questions.
        # /CLOSEAPPLICATIONS: Restart Manager closes this app so Setup can
        # replace it. The app is relaunched afterwards by the installer's
        # [Run] entry (Check: WizardSilent) — deterministic, unlike
        # /RESTARTAPPLICATIONS.
        subprocess.Popen([setup, "/SILENT", "/CLOSEAPPLICATIONS"],
                         creationflags=0x08000000 if os.name == "nt" else 0)
        return {"mode": mode, "action": "setup-launched", "path": setup}

    # portable
    url = release.get("portable_url")
    if not url:
        raise IOError("Portable asset not found on the latest release")
    app_dir = os.path.dirname(os.path.abspath(sys.executable))
    base = os.path.dirname(app_dir)
    tmp = tempfile.mkdtemp(prefix=".cd_update_", dir=base)
    archive = os.path.join(tmp, PORTABLE_ASSET)
    download_file(url, archive, progress_cb)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(tmp)
    new_dir = _find_app_dir(tmp)
    if not new_dir:
        raise IOError("Downloaded archive does not contain "
                      f"{APP_EXE}")
    ps1 = os.path.join(tmp, "swap.ps1")
    with open(ps1, "w", encoding="utf-8-sig") as f:
        f.write(_SWAP_PS1)
    flags = 0x08000000 if os.name == "nt" else 0   # CREATE_NO_WINDOW
    subprocess.Popen(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", ps1, "-oldPid", str(os.getpid()),
         "-appDir", app_dir, "-newDir", new_dir],
        creationflags=flags)
    return {"mode": mode, "action": "swap-launched", "path": new_dir}
