"""
Firmware flashing for CD3217-Analyzer boards.

Two families, two methods:

* Pico / RP2040 (Pico 1, Pico 2, Pico W, RP2040-Zero) — UF2 drag-and-drop.
  Hold BOOTSEL, plug in USB → the board shows up as a mass-storage drive
  ("RPI-RP2"). Copying a valid .uf2 file onto it flashes automatically.
  This needs no external tools and works on Windows/Linux/macOS.

* ESP32 (S3 / C3 / classic) — esptool.py. When esptool.py is on PATH the app
  can flash a combined .bin directly to a board in download mode.

Designed to be driven from the GUI (a "Flash firmware" button) and the CLI.
"""

import os
import shutil
import subprocess
import sys
from typing import List, Optional


# ---- Pico / RP2040: UF2 mass-storage drag-and-drop --------------------------

def find_bootsel_drives() -> List[str]:
    """Return drive/volume paths that look like a Pico in BOOTSEL mode.

    On Windows the board shows as a drive with label 'RPI-RP2' (or RP2350).
    On Linux/macOS it mounts under /media or /Volumes with that label.
    """
    drives = []
    if sys.platform.startswith("win"):
        try:
            import string
            for letter in string.ascii_uppercase:
                root = letter + ":\\"
                if os.path.exists(root):
                    try:
                        label = _win_volume_label(letter)
                    except Exception:
                        label = None
                    if label in ("RPI-RP2", "RP2350", "RP2350A", "RP2350B", "RPI-RP1"):
                        drives.append(root)
        except Exception:
            pass
    else:
        import glob
        bases = ["/media/*/", "/Volumes/", "/mnt/*/"]
        for base in bases:
            for path in glob.glob(base):
                name = os.path.basename(path.rstrip("/"))
                if name.upper().startswith(("RPI-RP", "RP2350")):
                    drives.append(path)
    return drives


def _win_volume_label(letter: str) -> Optional[str]:
    """Best-effort Windows volume label via PowerShell (no extra deps)."""
    cmd = [
        "powershell", "-NoProfile", "-Command",
        f"(Get-Volume -DriveLetter '{letter}').FileSystemLabel",
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return out.stdout.strip()
    except Exception:
        return None


def flash_pico_uf2(uf2_path: str, bootsel_drive: Optional[str] = None,
                   timeout: float = 60.0) -> str:
    """Flash a Pico-family board with a .uf2 via BOOTSEL mass-storage.

    Copies the .uf2 onto the bootsel volume. The RP2040/RP2350 ROM flashes it
    and reboots automatically.

    Returns a human-readable status message.
    """
    if not os.path.isfile(uf2_path):
        raise FileNotFoundError(f"UF2 not found: {uf2_path}")
    if not uf2_path.lower().endswith(".uf2"):
        raise ValueError("Expected a .uf2 firmware file")

    drive = bootsel_drive or (find_bootsel_drives() or [None])[0]
    if not drive:
        raise RuntimeError(
            "No Pico in BOOTSEL mode found. Hold the BOOTSEL button and plug "
            "the board into USB, then retry."
        )

    dest = os.path.join(drive, os.path.basename(uf2_path))
    shutil.copyfile(uf2_path, dest)
    return f"Flashed {os.path.basename(uf2_path)} to {drive}. Board will reboot."


# ---- ESP32: esptool ----------------------------------------------------------

def find_esptool() -> Optional[str]:
    """Locate esptool.py / esptool on PATH."""
    for cand in ("esptool.py", "esptool"):
        p = shutil.which(cand)
        if p:
            return p
    return None


def flash_esptool(bin_path: str, port: str,
                  chip: str = "esp32s3",
                  flash_mode: str = "dio",
                  erase: bool = False) -> str:
    """Flash an ESP32-family board via esptool (needs esptool on PATH)."""
    tool = find_esptool()
    if not tool:
        raise RuntimeError("esptool not found on PATH. Install it or use the "
                           "Espressif IDE / esptool.py.")

    cmd = [
        tool, "--chip", chip, "--port", port, "--baud", "460800",
        "write_flash", "--flash_mode", flash_mode,
    ]
    if erase:
        erase_cmd = [tool, "--chip", chip, "--port", port, "erase_flash"]
        subprocess.run(erase_cmd, check=True, timeout=120)
    # Combined binary goes at offset 0x0.
    cmd += ["0x0", bin_path]
    subprocess.run(cmd, check=True, timeout=180)
    return f"Flashed {os.path.basename(bin_path)} to ESP32 on {port}."


def flash_file(path: str, port: Optional[str] = None,
               bootsel_drive: Optional[str] = None, erase: bool = False) -> str:
    """Top-level: pick the flash method based on the file extension."""
    p = path.lower()
    if p.endswith(".uf2"):
        return flash_pico_uf2(path, bootsel_drive)
    if p.endswith((".bin", ".merged.bin")):
        if not port:
            raise ValueError("--port required to flash an ESP32 .bin")
        return flash_esptool(path, port, erase=erase)
    raise ValueError("Unsupported firmware type (expected .uf2 or .bin)")
