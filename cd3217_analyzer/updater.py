"""Self-update mechanism for the ACA app.

Checks the latest GitHub release and updates whichever build is running:

- **Installed** (Windows Inno Setup): downloads the new ``ACA_Setup.exe``
  and launches it with Inno's silent flags — Setup closes the running app via
  the Restart Manager, replaces files and restarts the app.
- **Portable (Windows)**: downloads ``ACA_Portable.zip``, extracts it next to
  the current folder and spawns a PowerShell swapper that waits for the app
  to exit, swaps the folders, restarts the app and cleans up.
- **deb (Linux)**: downloads ``aca-analyzer_<ver>_amd64.deb`` and runs it with
  ``pkexec dpkg -i`` after the app exits (polkit password), then relaunches.
- **Linux portable**: ``ACA_Linux_Portable.tar.gz`` + POSIX (sh) swapper —
  same folder-swap dance as the Windows one.
- **macOS (.app)**: ``ACA_macOS.zip`` (unsigned; Gatekeeper note in the
  README) + the same POSIX swapper on the ``ACA.app`` bundle.
- **Source** (``python gui.py``): no in-place update — just opens the
  releases page.

Stdlib only (urllib / zipfile / tarfile / subprocess / winreg) — no new
dependencies.
"""

import json
import os
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from typing import Callable, Optional

GITHUB_REPO = "ALeXXBody/Ace-Controller-Analyzer"
API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases"
SETUP_ASSET = "ACA_Setup.exe"
PORTABLE_ASSET = "ACA_Portable.zip"
APP_EXE = "ACA.exe"
MACOS_ASSET = "ACA_macOS.zip"
MACOS_APP_BUNDLE = "ACA.app"
LINUX_PORTABLE_ASSET = "ACA_Linux_Portable.tar.gz"
# .deb asset: version goes into the filename, so match by prefix/suffix.
DEB_PREFIX = "aca-analyzer_"
DEB_SUFFIX = "_amd64.deb"
# .deb layout: binary dir + this marker file identifies a deb install.
DEB_INSTALL_ROOT = "/opt/aca-analyzer"
DEB_MARKER = ".deb-install"

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

UA = f"aca-updater/{getattr(__import__('cd3217_analyzer'), '__version__', '?')}"


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

    def _asset(prefix: str, suffix: str) -> Optional[str]:
        """Version-suffixed asset (deb) or exact-name lookup."""
        for name, url in assets.items():
            if name.startswith(prefix) and name.endswith(suffix):
                return url
        return None

    return {
        "tag": tag,
        "version": tag.lstrip("vV"),
        "setup_url": assets.get(SETUP_ASSET),
        "portable_url": assets.get(PORTABLE_ASSET),
        "deb_url": _asset(DEB_PREFIX, DEB_SUFFIX),
        "macos_url": assets.get(MACOS_ASSET),
        "linux_portable_url": assets.get(LINUX_PORTABLE_ASSET),
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
    """"installed" (Windows Inno) | "deb" (Linux .deb) | "portable"
    | "source"."""
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
    if sys.platform.startswith("linux"):
        if os.path.isfile(os.path.join(DEB_INSTALL_ROOT, DEB_MARKER)) and \
                sys.executable.startswith(DEB_INSTALL_ROOT):
            return "deb"
    # macOS .app bundles and Linux portable tarballs update in place.
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


def _find_macos_app(extract_root: str) -> Optional[str]:
    """Locate ACA.app inside an extracted macOS zip."""
    direct = os.path.join(extract_root, MACOS_APP_BUNDLE)
    if os.path.isdir(os.path.join(direct, "Contents", "MacOS")):
        return direct
    for name in os.listdir(extract_root):
        cand = os.path.join(extract_root, name)
        if name == MACOS_APP_BUNDLE and \
                os.path.isdir(os.path.join(cand, "Contents", "MacOS")):
            return cand
    return None


def _linux_app_binary(app_dir: str) -> Optional[str]:
    """The executable inside an extracted Linux portable dir."""
    cand = os.path.join(app_dir, "ACA")
    return cand if os.path.isfile(cand) else None


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


_SWAP_SH = r"""#!/bin/sh
# POSIX swap: waits for the app to exit, swaps the bundle/dir in place,
# relaunches, cleans up. Args: oldPid appDir newDir [LAUNCHER]
set -u
OLD_PID=$1; APP_DIR=$2; NEW_DIR=$3; LAUNCHER=${4:-}
LOG="$(dirname "$APP_DIR")/update.log"
L(){ echo "$(date '+%Y-%m-%dT%H:%M:%S')  $*" >> "$LOG"; }
L "updater started (pid=$OLD_PID appDir=$APP_DIR newDir=$NEW_DIR)"
# wait for the app (bundle binary or dir binary) to exit
i=0
while [ $i -lt 90 ]; do
  if [ -n "$OLD_PID" ] && ! kill -0 "$OLD_PID" 2>/dev/null; then break; fi
  sleep 1; i=$((i+1))
done
sleep 1
OLD="$APP_DIR.old"
rm -rf "$OLD"
mv "$APP_DIR" "$OLD"
mv "$NEW_DIR" "$APP_DIR"
L "folders swapped; starting app"
# LAUNCHER: explicit executable (Linux portable binary), or on macOS
# open the bundle; fall back to the bundle binary.
if [ -n "$LAUNCHER" ] && [ -x "$LAUNCHER" ]; then
  for i in 1 2 3 4 5 6 7 8 9 10; do
    "$LAUNCHER" >/dev/null 2>&1 &
    case $? in 0) break;; esac
    sleep 1
  done
elif [ -d "$APP_DIR/Contents/MacOS" ]; then
  open "$APP_DIR" || L "open failed"
else
  BIN="$APP_DIR/ACA"
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if [ -x "$BIN" ]; then "$BIN" >/dev/null 2>&1 & break; fi
    sleep 1
  done
fi
sleep 2
rm -rf "$OLD"
rm -rf "$(dirname "$NEW_DIR")"
L "update complete"
"""


def _swap_sh_launch(app_dir: str, new_dir: str, launcher: str = "") -> None:
    """Write + spawn the POSIX swap script (detached)."""
    tmp = os.path.dirname(new_dir)
    sh = os.path.join(tmp, "swap.sh")
    with open(sh, "w") as f:
        f.write(_SWAP_SH)
    os.chmod(sh, 0o755)
    subprocess.Popen(
        ["/bin/sh", sh, str(os.getpid()), app_dir, new_dir, launcher],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True)


def apply_update(release: dict, progress_cb: Optional[Callable] = None
                 ) -> dict:
    """Download and launch the update for the running install mode.

    Modes:
      Windows "installed"  -> ACA_Setup.exe (Inno silent)
      Windows "portable"   -> ACA_Portable.zip + PowerShell swapper
      Linux "deb"          -> aca-analyzer_<ver>_amd64.deb via pkexec dpkg,
                              then relaunch from /opt
      Linux "portable"     -> ACA_Linux_Portable.tar.gz + sh swapper
      macOS "portable"     -> ACA_macOS.zip + sh swapper (ACA.app swap)
      "source" anywhere    -> open the releases page
    Returns {"mode", "action": "setup-launched"|"swap-launched"|"browser"};
    the caller exits the app on "swap-launched" (the swapper waits for the
    process, swaps in place and restarts it).
    Raises IOError/ValueError on failure.
    """
    mode = install_mode()

    if mode == "source":
        import webbrowser
        webbrowser.open(release.get("url") or RELEASES_URL)
        return {"mode": mode, "action": "browser"}

    if os.name == "nt":
        return _apply_update_windows(release, mode, progress_cb)
    return _apply_update_posix(release, mode, progress_cb)


def _apply_update_windows(release: dict, mode: str,
                          progress_cb: Optional[Callable] = None) -> dict:
    """Windows: existing Inno (installed) / zip+PowerShell (portable)."""
    if mode == "installed":
        url = release.get("setup_url")
        if not url:
            raise IOError("Installer asset not found on the latest release")
        tmp = tempfile.mkdtemp(prefix="cd3217_setup_")
        setup = os.path.join(tmp, SETUP_ASSET)
        download_file(url, setup, progress_cb)
        subprocess.Popen([setup, "/SILENT", "/CLOSEAPPLICATIONS"],
                         creationflags=0x08000000)
        return {"mode": mode, "action": "setup-launched", "path": setup}

    # portable zip (Windows layout, ACA.exe)
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
    subprocess.Popen(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", ps1, "-oldPid", str(os.getpid()),
         "-appDir", app_dir, "-newDir", new_dir],
        creationflags=0x08000000)
    return {"mode": mode, "action": "swap-launched", "path": new_dir}


def _apply_update_posix(release: dict, mode: str,
                        progress_cb: Optional[Callable] = None) -> dict:
    """Linux/macOS update application (full in-place flow, §4.24)."""
    mac = sys.platform == "darwin"
    app_dir = os.path.dirname(os.path.abspath(sys.executable))
    # macOS frozen: sys.executable = ACA.app/Contents/MacOS/ACA — the
    # swappable unit is the BUNDLE, not the MacOS dir.
    if mac and "/Contents/MacOS/" in app_dir:
        app_dir = app_dir.split("/Contents/MacOS/")[0]
    base = os.path.dirname(app_dir) or os.getcwd()
    tmp = tempfile.mkdtemp(prefix=".aca_update_", dir=base)

    if mode == "deb":
        url = release.get("deb_url")
        if not url:
            raise IOError("No .deb asset for Linux on the latest release")
        deb = os.path.join(tmp, "aca-analyzer.deb")
        download_file(url, deb, progress_cb)
        script = os.path.join(tmp, "swap_deb.sh")
        with open(script, "w") as f:
            f.write(_SWAP_DEB_SH)
        os.chmod(script, 0o755)
        # pkexec pops the desktop polkit agent for the admin password
        # (needed because dpkg -i writes /opt + /usr) — the script then
        # waits for this process to exit, installs, relaunches.
        subprocess.Popen(
            ["/bin/sh", script, str(os.getpid()), deb],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
        return {"mode": mode, "action": "swap-launched", "path": deb}

    if mac:
        url = release.get("macos_url")
        if not url:
            raise IOError("No macOS asset on the latest release")
        archive = os.path.join(tmp, MACOS_ASSET)
        download_file(url, archive, progress_cb)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(tmp)
        bundle = _find_macos_app(tmp)
        if not bundle:
            raise IOError(f"Downloaded archive does not contain "
                          f"{MACOS_APP_BUNDLE}")
        _swap_sh_launch(app_dir, bundle)
        return {"mode": mode, "action": "swap-launched", "path": bundle}

    if not sys.platform.startswith("linux"):
        raise IOError(f"No update flow for platform {sys.platform!r} yet")

    # Linux portable tar.gz
    url = release.get("linux_portable_url")
    if not url:
        raise IOError("No Linux portable asset on the latest release")
    archive = os.path.join(tmp, LINUX_PORTABLE_ASSET)
    download_file(url, archive, progress_cb)
    import tarfile
    with tarfile.open(archive) as tf:
        tf.extractall(tmp)
    # tar members may be prefixed (e.g. ACA_Linux/ACA); find the dir with
    # the executable.
    new_dir = None
    for root, _dirs, files in os.walk(tmp):
        if not os.path.isdir(root):
            continue
        cand = os.path.join(root, "ACA")
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            new_dir = root
            break
    if not new_dir:
        raise IOError("Downloaded archive does not contain the ACA binary")
    _swap_sh_launch(app_dir, new_dir, launcher=os.path.join(
        new_dir, "ACA"))
    return {"mode": mode, "action": "swap-launched", "path": new_dir}


_SWAP_DEB_SH = r"""#!/bin/sh
# .deb swap: waits for the running app to exit, installs via pkexec
# (polkit GUI password — dpkg writes /opt + /usr), relaunches.
set -u
OLD_PID=$1; DEB=$2; LOG=/tmp/aca_update_deb.log; INSTALL_ROOT=/opt/aca-analyzer
L(){ echo "$(date '+%Y-%m-%dT%H:%M:%S')  $*" >> "$LOG"; }
L "deb updater started (pid=$OLD_PID deb=$DEB)"
i=0
while [ $i -lt 90 ]; do
  if [ -n "$OLD_PID" ] && ! kill -0 "$OLD_PID" 2>/dev/null; then break; fi
  sleep 1; i=$((i+1))
done
sleep 1
if command -v pkexec >/dev/null 2>&1 && [ -f "$DEB" ]; then
  pkexec dpkg -i "$DEB" && L "dpkg -i ok" || L "dpkg -i FAILED"
else
  L "no pkexec or deb missing"
fi
for i in 1 2 3 4 5 6 7 8 9 10; do
  if [ -x "$INSTALL_ROOT/ACA" ]; then
    "$INSTALL_ROOT/ACA" >/dev/null 2>&1 & break
  fi
  sleep 1
done
rm -rf "$(dirname "$DEB")"
L "update complete"
"""
