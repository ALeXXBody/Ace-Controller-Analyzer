"""CD3217B12 (Apple ACE2) I2C Diagnostic Analyzer - Modern GUI."""

from __future__ import annotations

import os
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

import customtkinter as ctk
from tkinter import filedialog, messagebox

# Windowed (console-less) PyInstaller builds have sys.stdout/stderr = None;
# any stray print() would then crash the app. Route them to devnull.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cd3217_analyzer import __version__
from cd3217_analyzer.adapters import FTDIAdapter, SMBusAdapter, detect_adapter
from cd3217_analyzer.usb_bridge import UsbBridgeAdapter, list_bridge_ports, normalize_port
from cd3217_analyzer.analyzer import (
    CD3217Analyzer,
    DeviceResult,
    DiagnosticReport,
    FaultType,
    HealthStatus,
)
from cd3217_analyzer.flash import FlashInfo, SPIFlash
from cd3217_analyzer.models import get_model, list_models
from cd3217_analyzer.otp import (
    OTPDump,
    diff_dumps,
    format_dump_table,
    load_dump_binary,
    load_dump_json,
    save_diff_report,
    scan_otp,
)
from cd3217_analyzer.registers import (
    ACE2_BROADCAST_ADDRESS,
    KNOWN_ACE2_ADDRESSES,
    REGISTERS,
    decode_i2c_address_straps,
    is_ace2_address,
)
from cd3217_analyzer.report import save_csv_log, save_json_report
from cd3217_analyzer.spi_adapter import SPIAdapter
from cd3217_analyzer.theme import COLORS as C
from cd3217_analyzer.theme import FONTS as F
from cd3217_analyzer.utils import (
    format_hex_addr,
    parse_address_list,
    parse_hex_address,
    unique_sorted,
)


def resource_path(rel: str) -> str:
    """Locate a bundled asset in dev mode and inside a PyInstaller exe."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


_PIN_LABELS = {
    "sda": "SDA (data)",
    "scl": "SCL (clock)",
    "sck": "SCK (clock)",
    "miso": "MISO (board reads)",
    "mosi": "MOSI (board writes)",
    "cs": "CS (chip select)",
}
# kept for backwards compatibility with saved UI-state code paths; the Board
# tab now uses compact summary lines instead of per-pin rows.

# Chip socket classes (repair-community classification): sockets at these
# addresses need OTP-programmed donor chips ("Apple address") or accept
# vanilla (unprogrammed) TI parts. Shown alongside the board-specific
# refdes/strap data from models.py.
OTP_SOCKET_ADDRS = {0x3A, 0x3B, 0x3C, 0x74, 0x76, 0x78, 0x79}
VANILLA_SOCKET_ADDRS = {0x38, 0x3F, 0x2F, 0x28}


def chip_class(addr: int) -> str:
    """OTP vs vanilla socket classification for an address ('' if unknown)."""
    if addr in OTP_SOCKET_ADDRS:
        return "OTP-ed (Apple address)"
    if addr in VANILLA_SOCKET_ADDRS:
        return "Likely vanilla"
    return ""


class Application(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(f"CD3217B12 Analyzer v{__version__}")
        self.geometry("1360x900")
        self.minsize(1024, 720)
        self.configure(fg_color=C["bg"])
        self._set_window_icon()

        self.adapter = None
        self.connected = False
        self.busy = False
        self.scan_results: List[int] = []
        self.devices: Dict[int, DeviceResult] = {}
        self.selected_address: Optional[int] = None
        self.current_model = None
        self.batch_results: List[DeviceResult] = []
        self.otp_current_dump: Optional[OTPDump] = None
        self.spi_adapter: Optional[SPIAdapter] = None
        self.flash: Optional[SPIFlash] = None
        self.flash_info: Optional[FlashInfo] = None
        self.device_rows: Dict[int, ctk.CTkFrame] = {}
        self._busy_buttons: List[ctk.CTkButton] = []

        self._build_ui()
        self.after(400, self._auto_detect)
        # silent background update check (fails silently when offline)
        self.after(3000, self._auto_update_check)

    def _set_window_icon(self):
        """Set the taskbar / title-bar icon (skip silently if unavailable)."""
        try:
            from tkinter import PhotoImage
            icon = resource_path(os.path.join("assets", "icon.png"))
            if os.path.exists(icon):
                self._icon_img = PhotoImage(file=icon)  # keep a reference
                self.iconphoto(False, self._icon_img)
            # Windows: show a proper icon (not the python.exe icon) in the
            # taskbar for windowed PyInstaller builds.
            if sys.platform.startswith("win"):
                try:
                    import ctypes
                    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                        f"cd3217.analyzer.{__version__}")
                except Exception:
                    pass
        except Exception:
            pass

    # ─── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self._build_topbar()
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=12, pady=6)
        body.grid_columnconfigure(1, weight=3)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(body, fg_color=C["panel"], corner_radius=12)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self._build_device_panel(left)

        right = ctk.CTkFrame(body, fg_color=C["panel"], corner_radius=12)
        right.grid(row=0, column=1, sticky="nsew")
        self._build_tabs(right)

        status = ctk.CTkFrame(self, fg_color=C["panel"], corner_radius=8, height=34)
        status.pack(fill="x", padx=12, pady=(0, 10))
        self.status_left = ctk.CTkLabel(
            status, text="Ready", text_color=C["dim"], font=F["small"]
        )
        self.status_left.pack(side="left", padx=12, pady=6)
        self.busy_label = ctk.CTkLabel(
            status, text="", text_color=C["yellow"], font=F["small"]
        )
        self.busy_label.pack(side="left", padx=8)
        ctk.CTkLabel(
            status, text=f"v{__version__}", text_color=C["dim"], font=F["small"]
        ).pack(side="right", padx=12, pady=6)

    def _build_topbar(self):
        top = ctk.CTkFrame(self, fg_color=C["panel"], corner_radius=12)
        top.pack(fill="x", padx=12, pady=(12, 4))

        brand = ctk.CTkFrame(top, fg_color="transparent")
        brand.pack(side="left", padx=12, pady=10)
        ctk.CTkLabel(
            brand, text="CD3217 Analyzer", font=F["title"], text_color=C["accent"]
        ).pack(anchor="w")
        ctk.CTkLabel(
            brand,
            text="Apple ACE2 · I2C diagnostics · SPI flash · OTP tools",
            font=F["small"],
            text_color=C["dim"],
        ).pack(anchor="w")

        controls = ctk.CTkFrame(top, fg_color="transparent")
        controls.pack(side="right", padx=12, pady=10)

        ctk.CTkLabel(controls, text="Model", text_color=C["dim"], font=F["small"]).grid(
            row=0, column=0, sticky="w", padx=4
        )
        self.model_var = ctk.StringVar(value="Auto-detect")
        model_names = ["Auto-detect"] + [
            f"{m.model_id} — {m.name}" for m in list_models()
        ]
        self.model_menu = ctk.CTkOptionMenu(
            controls,
            variable=self.model_var,
            values=model_names,
            width=300,
            command=self._on_model_change,
            fg_color=C["entry"],
            button_color=C["btn"],
            button_hover_color=C["btn_hover"],
        )
        self.model_menu.grid(row=1, column=0, padx=4)

        ctk.CTkLabel(
            controls, text="Adapter", text_color=C["dim"], font=F["small"]
        ).grid(row=0, column=1, sticky="w", padx=4)
        self.adapter_var = ctk.StringVar(value="Auto-detect (FTDI / board)")
        self.adapter_menu = ctk.CTkOptionMenu(
            controls,
            variable=self.adapter_var,
            values=["Auto-detect (FTDI / board)", "FTDI FT232H",
                    "SMBus (Linux)", "CH341", "USB Bridge (board)"],
            width=150,
            fg_color=C["entry"],
            button_color=C["btn"],
            button_hover_color=C["btn_hover"],
        )
        self.adapter_menu.grid(row=1, column=1, padx=4)

        ctk.CTkLabel(controls, text="Bus/Port", text_color=C["dim"], font=F["small"]).grid(
            row=0, column=2, sticky="w", padx=4
        )
        self.bus_var = ctk.StringVar(value="1")
        ctk.CTkEntry(
            controls, textvariable=self.bus_var, width=70, fg_color=C["entry"]
        ).grid(row=1, column=2, padx=4)

        self.connect_btn = ctk.CTkButton(
            controls,
            text="Connect",
            width=110,
            height=32,
            fg_color=C["green"],
            hover_color="#16a34a",
            text_color="#04120a",
            command=self._toggle_connection,
        )
        self.connect_btn.grid(row=1, column=3, padx=(10, 4))

        self.conn_status = ctk.CTkLabel(
            controls, text="● Disconnected", text_color=C["red"], font=F["body"]
        )
        self.conn_status.grid(row=1, column=4, padx=6)

        self.flash_btn = ctk.CTkButton(
            controls,
            text="Flash board",
            width=100,
            height=32,
            fg_color=C["btn"],
            hover_color=C["btn_hover"],
            text_color=C["text"],
            command=self._flash_board,
        )
        self.flash_btn.grid(row=1, column=5, padx=(6, 4))

        self.update_btn = ctk.CTkButton(
            controls,
            text="Check updates",
            width=120,
            height=32,
            fg_color=C["btn"],
            hover_color=C["btn_hover"],
            text_color=C["text"],
            command=self._manual_update_check,
        )
        self.update_btn.grid(row=1, column=6, padx=(6, 4))

        self.export_btn = ctk.CTkButton(
            controls,
            text="Export data",
            width=110,
            height=32,
            fg_color=C["btn"],
            hover_color=C["btn_hover"],
            text_color=C["text"],
            command=self._export_data,
        )
        self.export_btn.grid(row=1, column=7, padx=(6, 4))

    # ─── Self-update ──────────────────────────────────────────────────────

    def _ui(self, fn):
        """Run fn on the UI thread (safe to call from workers)."""
        try:
            self.after(0, fn)
        except Exception:
            pass

    def _manual_update_check(self):
        self.log("Checking for updates...")
        self.update_btn.configure(state="disabled")

        def work():
            from cd3217_analyzer.updater import check_for_update
            rel = check_for_update(__version__)
            self._ui(lambda: self._on_update_checked(rel, manual=True))

        threading.Thread(target=work, daemon=True).start()

    def _auto_update_check(self):
        """Silent startup check: log + re-style the button if newer exists."""
        def work():
            from cd3217_analyzer.updater import check_for_update
            rel = check_for_update(__version__)
            self._ui(lambda: self._on_update_checked(rel, manual=False))

        threading.Thread(target=work, daemon=True).start()

    def _on_update_checked(self, rel, manual):
        self.update_btn.configure(state="normal")
        if rel:
            self.update_btn.configure(
                text=f"Update to {rel['version']}",
                fg_color=C["accent"], hover_color=C["accent_dim"],
                text_color="#06121e",
                command=lambda: self._confirm_update(rel))
            self.log(f"Update available: v{rel['version']} "
                     f"(current v{__version__})", "ok")
            if manual:
                self._confirm_update(rel)
        else:
            self.log("Up to date" if manual else
                     "Up to date (no newer release found)")
            if manual:
                messagebox.showinfo(
                    "No update",
                    f"You are running the latest version (v{__version__}).")

    def _confirm_update(self, rel):
        from cd3217_analyzer.updater import install_mode
        mode = install_mode()
        mode_txt = {
            "installed": "The installer will run and restart the app.",
            "portable": "The app will download and replace itself, "
                        "then restart.",
            "source": "Running from source — the releases page will open.",
        }.get(mode, "")
        if not messagebox.askyesno(
                "Update available",
                f"A new version is available: v{rel['version']} "
                f"(current v{__version__}).\n\n{mode_txt}\n\n"
                "Download and update now?"):
            return
        self._run_update(rel)

    def _run_update(self, rel):
        from cd3217_analyzer.updater import apply_update
        self._updating = True

        dlg = ctk.CTkToplevel(self)
        dlg.title("Updating")
        dlg.geometry("420x150")
        dlg.resizable(False, False)
        dlg.attributes("-topmost", True)
        dlg.grab_set()
        ctk.CTkLabel(dlg, text=f"Downloading v{rel['version']}...",
                     font=F["heading"]).pack(pady=(22, 8))
        bar = ctk.CTkProgressBar(dlg, width=360, height=16)
        bar.pack(pady=4)
        bar.set(0)
        info = ctk.CTkLabel(dlg, text="", font=F["small"], text_color=C["dim"])
        info.pack(pady=4)

        def progress(done, total):
            def upd():
                if total:
                    bar.set(min(1.0, done / total))
                    info.configure(text=f"{done/1048576:.1f} / "
                                         f"{total/1048576:.1f} MB")
                else:
                    info.configure(text=f"{done/1048576:.1f} MB")
            self._ui(upd)

        def work():
            try:
                result = apply_update(rel, progress)
                self._ui(lambda: self._on_update_applied(dlg, result))
            except Exception as e:
                err = str(e)
                self._ui(lambda: self._on_update_failed(dlg, err))

        threading.Thread(target=work, daemon=True).start()

    def _on_update_applied(self, dlg, result):
        action = result.get("action")
        if action == "swap-launched":
            self.log("Update staged — restarting to finish...", "ok")
            self.destroy()
            return
        # setup-launched / browser: Inno (or the browser) takes over
        dlg.destroy()
        if action == "setup-launched":
            self.log("Installer launched — follow the Setup window. "
                     "The app will be closed and restarted by Setup.", "ok")

    def _on_update_failed(self, dlg, err):
        dlg.destroy()
        self.log(f"Update failed: {err}", "err")
        messagebox.showerror("Update failed", str(err))

    # ─── Board firmware update ────────────────────────────────────────────

    def _maybe_offer_board_update(self, adapter: UsbBridgeAdapter):
        """Popup when the connected board runs an older firmware.

        Comparison is against the latest GitHub release (the app's own
        version as fallback when offline). Runs the check on a worker
        thread; the dialog opens on the UI thread.
        """
        if getattr(self, "_board_update_declined", False):
            return
        try:
            info = adapter.info()
        except Exception:
            return
        board = (info or {}).get("board")
        fw = (info or {}).get("version")
        if not board:
            return

        def work():
            from cd3217_analyzer.updater import (fetch_latest_release,
                                                 is_newer)
            rel = None
            try:
                rel = fetch_latest_release()
            except Exception:
                rel = None
            latest = rel["version"] if rel else __version__
            # unknown version (pre-0.6.1 firmware) always counts as outdated
            outdated = (fw is None) or is_newer(latest, fw)
            if not outdated:
                return
            if rel is None:
                self.log(f"Board firmware update available (board runs "
                         f"{fw or 'an unknown version'}, latest is "
                         f"{latest}) — connect to the internet to update.",
                         "warn")
                return
            self._ui(lambda: self._ask_board_update(adapter, board, fw,
                                                    rel))

        threading.Thread(target=work, daemon=True).start()

    def _ask_board_update(self, adapter, board, fw, rel):
        if not (self.connected and self.adapter is adapter):
            return          # disconnected meanwhile
        from cd3217_analyzer.updater import board_firmware_asset
        asset = board_firmware_asset(board)
        cur = fw or "older than 0.6.1"
        if not asset:
            self.log(f"Board '{board}' runs fw {cur} — a newer firmware "
                     f"({rel['version']}) exists but this board has no "
                     "release image; flash manually.", "warn")
            return
        if not messagebox.askyesno(
                "Board firmware update",
                f"A firmware update is available for this board.\n\n"
                f"Board:  {board}\n"
                f"Installed firmware:  {cur}\n"
                f"Latest firmware:  {rel['version']}\n\n"
                "Update now? The board will restart automatically\n"
                "(about a minute — no wiring changes needed)."):
            self._board_update_declined = True
            self.log("Board firmware update declined (this session)")
            return
        self._perform_board_update(adapter, board, rel)

    def _perform_board_update(self, adapter, board, rel):
        """Download the board firmware and apply it (UF2 or OTA flow)."""
        self._stop_board_watcher()
        self.log(f"Updating board firmware: {board} -> {rel['version']}",
                 "ok")

        dlg = ctk.CTkToplevel(self)
        dlg.title("Board firmware update")
        dlg.geometry("440x160")
        dlg.resizable(False, False)
        dlg.attributes("-topmost", True)
        dlg.grab_set()
        ctk.CTkLabel(dlg, text=f"Updating {board} to fw {rel['version']}...",
                     font=F["heading"]).pack(pady=(22, 8))
        bar = ctk.CTkProgressBar(dlg, width=380, height=16)
        bar.pack(pady=4)
        bar.set(0)
        info = ctk.CTkLabel(dlg, text="Downloading...", font=F["small"],
                            text_color=C["dim"])
        info.pack(pady=4)

        def set_phase(txt, frac):
            info.configure(text=txt)
            bar.set(frac)

        def dl_progress(done, total):
            def upd():
                if total:
                    set_phase(f"Downloading firmware... "
                              f"{done/1048576:.1f}/{total/1048576:.1f} MB",
                              min(0.4, 0.4 * done / total))
                else:
                    set_phase(f"Downloading... {done/1048576:.1f} MB", 0.2)
            self._ui(upd)

        def work():
            try:
                from cd3217_analyzer.updater import download_board_firmware
                path = download_board_firmware(board, rel, dl_progress)
                self._ui(lambda: set_phase("Flashing board...", 0.45))
                if path.lower().endswith(".uf2"):
                    self._flash_board_uf2(adapter, path)
                else:
                    with open(path, "rb") as f:
                        data = f.read()

                    def fw_progress(done, total):
                        self._ui(lambda: set_phase(
                            f"Writing firmware... {done/1024:.0f}/"
                            f"{total/1024:.0f} KB",
                            0.45 + 0.5 * done / total))
                    adapter.fw_update_image(data, fw_progress)
                    # board reboots itself after fw_update_end
                self._ui(lambda: self._board_update_stage2(dlg, board))
            except Exception as e:
                err = str(e)
                self._ui(lambda: self._board_update_failed(dlg, err))

        threading.Thread(target=work, daemon=True).start()

    def _flash_board_uf2(self, adapter, uf2_path):
        """RP2040 flow: reboot to BOOTSEL, copy the UF2 (verified)."""
        import time as _t
        from cd3217_analyzer.flash_board import (find_bootsel_drives,
                                                 flash_pico_uf2)
        adapter.fw_reboot_bootsel()
        adapter.close()
        deadline = _t.time() + 20
        drive = None
        while _t.time() < deadline:
            drives = find_bootsel_drives()
            if drives:
                drive = drives[0]
                break
            _t.sleep(0.5)
        if not drive:
            raise IOError("Board did not enter BOOTSEL mode — unplug it, "
                          "hold BOOTSEL, plug back in and retry")
        flash_pico_uf2(uf2_path, bootsel_drive=drive)

    def _board_update_stage2(self, dlg, board):
        """Firmware written — wait for the board to come back, reconnect."""
        dlg.destroy()
        self.log("Firmware written — waiting for the board to restart...",
                 "ok")
        self._disconnect()          # clean state; port may re-enumerate

        def work():
            import time as _t
            from cd3217_analyzer.usb_bridge import scan_for_boards
            deadline = _t.time() + 30
            found = []
            while _t.time() < deadline:
                found = scan_for_boards()
                if found:
                    break
                _t.sleep(2)
            def report():
                if not found:
                    self.log("Board did not come back after the update — "
                             "unplug/replug it, then Connect.", "warn")
                    return
                b = found[0]
                self.log(f"Board back: {b['board']} on {b['port']} — "
                         "reconnecting", "ok")
                self.adapter_var.set("USB Bridge (board)")
                self.bus_var.set(b["port"])
                self._connect()
            self._ui(report)

        threading.Thread(target=work, daemon=True).start()

    def _board_update_failed(self, dlg, err):
        dlg.destroy()
        self.log(f"Board firmware update failed: {err}", "err")
        messagebox.showerror("Board update failed", str(err))
        # the port may be gone (mid-reboot) — let the watcher/disconnect clean
        if not (self.connected and self.adapter and
                getattr(self.adapter, "is_alive", lambda: True)()):
            self._disconnect()

    def _build_device_panel(self, parent):
        header = ctk.CTkFrame(parent, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(12, 6))
        ctk.CTkLabel(
            header, text="Devices", font=F["heading"], text_color=C["text"]
        ).pack(side="left")
        self.device_count_var = ctk.StringVar(value="0 found")
        ctk.CTkLabel(
            header, textvariable=self.device_count_var, text_color=C["dim"], font=F["small"]
        ).pack(side="right")

        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=(0, 8))
        self.btn_scan = ctk.CTkButton(
            row,
            text="Scan Bus",
            width=90,
            height=32,
            fg_color=C["accent"],
            hover_color=C["accent_dim"],
            text_color="#041018",
            command=self._scan_bus,
        )
        self.btn_scan.pack(side="left", padx=(0, 4))
        self.btn_quick = ctk.CTkButton(
            row, text="Quick", width=70, height=32, fg_color=C["btn"],
            hover_color=C["btn_hover"], command=self._quick_scan
        )
        self.btn_quick.pack(side="left", padx=(0, 4))
        self.btn_model_scan = ctk.CTkButton(
            row, text="Model", width=70, height=32, fg_color=C["btn"],
            hover_color=C["btn_hover"], command=self._model_scan
        )
        self.btn_model_scan.pack(side="left", padx=(0, 4))
        self.btn_refresh = ctk.CTkButton(
            row, text="Diagnose All", width=100, height=32, fg_color=C["btn"],
            hover_color=C["btn_hover"], command=self._refresh_devices
        )
        self.btn_refresh.pack(side="left", padx=(0, 4))
        self.btn_bus_check = ctk.CTkButton(
            row, text="Bus Check", width=84, height=32, fg_color=C["btn"],
            hover_color=C["btn_hover"], command=self._bus_check
        )
        self.btn_bus_check.pack(side="left")

        self.device_frame = ctk.CTkScrollableFrame(
            parent, fg_color=C["entry"], corner_radius=10
        )
        self.device_frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        act = ctk.CTkFrame(parent, fg_color="transparent")
        act.pack(fill="x", padx=12, pady=(0, 6))
        self.btn_diagnose = ctk.CTkButton(
            act, text="Diagnose", width=90, height=30, fg_color=C["btn"],
            hover_color=C["btn_hover"], command=self._diagnose_selected
        )
        self.btn_diagnose.pack(side="left", padx=(0, 4))
        self.btn_dump = ctk.CTkButton(
            act, text="Dump", width=70, height=30, fg_color=C["btn"],
            hover_color=C["btn_hover"], command=self._dump_selected
        )
        self.btn_dump.pack(side="left", padx=(0, 4))
        self.btn_stress = ctk.CTkButton(
            act, text="Stress", width=74, height=30, fg_color=C["btn"],
            hover_color=C["btn_hover"], command=self._stress_selected
        )
        self.btn_stress.pack(side="left", padx=(0, 4))
        self.btn_export = ctk.CTkButton(
            act, text="Export JSON", width=100, height=30, fg_color=C["btn"],
            hover_color=C["btn_hover"], command=self._save_json
        )
        self.btn_export.pack(side="left")

        qa = ctk.CTkFrame(parent, fg_color=C["card"], corner_radius=10)
        qa.pack(fill="x", padx=12, pady=(0, 12))
        ctk.CTkLabel(qa, text="Manual address", text_color=C["dim"], font=F["small"]).pack(
            side="left", padx=10, pady=8
        )
        self.quick_addr_var = ctk.StringVar(value="0x38")
        ctk.CTkEntry(
            qa, textvariable=self.quick_addr_var, width=80, fg_color=C["entry"]
        ).pack(side="left", padx=4, pady=8)
        ctk.CTkButton(
            qa, text="Go", width=50, height=28, fg_color=C["accent"],
            text_color="#041018", hover_color=C["accent_dim"],
            command=self._diagnose_quick
        ).pack(side="left", padx=8, pady=8)

        self._busy_buttons.extend(
            [self.btn_scan, self.btn_quick, self.btn_model_scan, self.btn_refresh,
             self.btn_diagnose, self.btn_dump]
        )

    def _build_tabs(self, parent):
        self.tabs = ctk.CTkTabview(
            parent,
            fg_color=C["bg"],
            segmented_button_fg_color=C["panel"],
            segmented_button_selected_color=C["accent"],
            segmented_button_selected_hover_color=C["accent_dim"],
            segmented_button_unselected_color=C["card"],
            segmented_button_unselected_hover_color=C["card_hover"],
            text_color=C["text"],
            text_color_disabled=C["dim"],
        )
        self.tabs.pack(fill="both", expand=True, padx=10, pady=10)

        self.tab_adapter = self.tabs.add("Adapter")
        self.tab_board = self.tabs.add("Board")
        self.tab_overview = self.tabs.add("Overview")
        self.tab_registers = self.tabs.add("Registers")
        self.tab_batch = self.tabs.add("Batch")
        self.tab_straps = self.tabs.add("Straps")
        self.tab_otp = self.tabs.add("OTP")
        self.tab_flash = self.tabs.add("Flash")
        self.tab_uart = self.tabs.add("UART")
        self.tab_log = self.tabs.add("Log")

        self._build_adapter_tab()
        self._build_board_tab()
        self._build_overview_tab()
        self._build_register_tab()
        self._build_batch_tab()
        self._build_strap_tab()
        self._build_otp_tab()
        self._build_flash_tab()
        self._build_uart_tab()
        self._build_log_tab()

    # ─── Adapter tab (your Pico/ESP32 analyzer board) ────────────────────

    def _build_adapter_tab(self):
        tab = self.tab_adapter
        from cd3217_analyzer.boards import BOARDS, get_board_info

        # ── layout: left column (stacked cards) + right column (diagram) ──
        body = ctk.CTkFrame(tab, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=12, pady=12)
        body.grid_columnconfigure(0, weight=0, minsize=330)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(body, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        # ── connected board card (top of the stack) ────────────────────────
        card = ctk.CTkFrame(left, fg_color=C["card"], corner_radius=12)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=12)

        self.board_status_dot = ctk.CTkLabel(
            inner, text="●", font=F["heading"], text_color=C["dim"], width=22)
        self.board_status_dot.pack(side="left", padx=(0, 10))

        vbox = ctk.CTkFrame(inner, fg_color="transparent")
        vbox.pack(side="left", fill="x", expand=True)
        self.board_name_label = ctk.CTkLabel(
            vbox, text="No board connected", font=F["heading"],
            wraplength=260)
        self.board_name_label.pack(anchor="w")
        self.board_sub_label = ctk.CTkLabel(
            vbox, text="Connect via USB Bridge (board) to see its pinout, "
                       "or pick a board below.",
            text_color=C["dim"], font=F["body"], justify="left",
            wraplength=270)
        self.board_sub_label.pack(anchor="w", pady=(2, 0))

        # ── I2C / SPI summary cards (single compact line each — the diagram
        #    next to them is the visual pin reference, so no pin-row lists) ─
        self.i2c_card = ctk.CTkFrame(left, fg_color=C["card"], corner_radius=12)
        self.i2c_card.pack(fill="x", pady=(8, 0))
        self._build_pin_summary(
            self.i2c_card, "I2C — CD3217", C["green"],
            [("SDA", "sda"), ("SCL", "scl")])

        self.spi_card = ctk.CTkFrame(left, fg_color=C["card"], corner_radius=12)
        self.spi_card.pack(fill="x", pady=(8, 0))
        self._build_pin_summary(
            self.spi_card, "SPI — flash (via level shifter)", C["accent"],
            [("SCK", "sck"), ("MISO", "miso"), ("MOSI", "mosi"),
             ("CS", "cs")])

        self.uart_card = ctk.CTkFrame(left, fg_color=C["card"],
                                      corner_radius=12)
        self.uart_card.pack(fill="x", pady=(8, 0))
        self._build_pin_summary(
            self.uart_card, "UART — sniff bus (RX only)", C["yellow"],
            [("RX (listen)", "uart_rx")])

        # ── pinout diagram (right column, spans the full height of the stack)
        diag_card = ctk.CTkFrame(body, fg_color=C["card"], corner_radius=12)
        diag_card.grid(row=0, column=1, sticky="nsew")
        diag_card.grid_rowconfigure(1, weight=1)
        diag_card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            diag_card, text="Board pinout — connect the highlighted pins",
            font=F["heading"], text_color=C["accent"]
        ).grid(row=0, column=0, sticky="w", padx=18, pady=(14, 2))
        self.board_diagram_label = ctk.CTkLabel(diag_card, text="")
        self.board_diagram_label.grid(row=1, column=0, sticky="nsew",
                                      padx=18, pady=(4, 16))
        self._board_diagram_img = None   # keep a reference (GC safety)

        # ── wiring notes ───────────────────────────────────────────────────
        notes_card = ctk.CTkFrame(left, fg_color=C["card"], corner_radius=12)
        notes_card.pack(fill="x", pady=(8, 0))
        self.board_notes_label = ctk.CTkLabel(
            notes_card, text="", font=F["small"], text_color=C["dim"],
            justify="left", wraplength=300)
        self.board_notes_label.pack(anchor="w", padx=14, pady=12)
        self._board_notes_frame = notes_card

        # ── board picker (works without a board connected) ─────────────────
        picker_card = ctk.CTkFrame(left, fg_color=C["card"], corner_radius=12)
        picker_card.pack(fill="x", pady=(8, 0))
        box = ctk.CTkFrame(picker_card, fg_color="transparent")
        box.pack(fill="x", padx=14, pady=12)
        ctk.CTkLabel(box, text="Browse a board's pinout:",
                     font=F["body"], text_color=C["dim"]).pack(side="left")
        self.board_picker_var = ctk.StringVar(value="")
        picker = ctk.CTkOptionMenu(
            box, variable=self.board_picker_var, values=[""] + sorted(
                b.name for b in BOARDS.values()),
            command=self._on_board_picked, width=170,
            fg_color=C["entry"], button_color=C["btn"],
            button_hover_color=C["btn_hover"], text_color=C["text"],
            dropdown_fg_color=C["panel"])
        picker.pack(side="left", padx=(8, 0))

        # initialize with the empty state
        self._show_board_info(None)

    def _build_pin_summary(self, parent, title, color, roles):
        """Vertical card: one row per pin — description left, pin right.

        The pin values are filled in by _show_board_info(); the diagram next
        to the card shows where each highlighted pin physically is.
        """
        ctk.CTkLabel(parent, text=title, font=F["heading"],
                     text_color=color).pack(anchor="w", padx=14, pady=(12, 6))
        for label, key in roles:
            row = ctk.CTkFrame(parent, fg_color="transparent")
            row.pack(fill="x", padx=14, pady=(0, 7))
            dot = ctk.CTkLabel(row, text="●", text_color=color,
                               font=F["small"], width=18)
            dot.pack(side="left", padx=(0, 8))
            ctk.CTkLabel(row, text=label, font=F["body"],
                         text_color=C["dim"]).pack(side="left")
            val = ctk.CTkLabel(row, text="—", font=F["mono"],
                               text_color=C["text"])
            val.pack(side="right")
            setattr(self, f"_pin_lbl_{key}", val)

    def _on_board_picked(self, name):
        from cd3217_analyzer.boards import BOARDS
        for b in BOARDS.values():
            if b.name == name:
                self._show_board_info(b)
                return
        self._show_board_info(None)

    def _show_board_diagram(self, board):
        """Load and display the board's pinout diagram (if any)."""
        self._board_diagram_img = None
        path = None
        if board is not None and board.image:
            cand = resource_path(os.path.join("assets", "boards", board.image))
            if os.path.exists(cand):
                path = cand
        if not path:
            self.board_diagram_label.configure(
                image=None, text="No diagram available for this board")
            return
        try:
            from PIL import Image
        except ImportError:
            self.board_diagram_label.configure(
                image=None,
                text=f"Diagram: assets/boards/{board.image} (install "
                     f"Pillow to display)")
            return
        try:
            img = Image.open(path)
            # Fit into the right column: cap both width and height so tall
            # boards (Pico) use the full stack height, wide ones the width.
            max_w, max_h = 620, 660
            scale = min(max_w / img.width, max_h / img.height)
            disp_w = max(1, int(img.width * scale))
            disp_h = max(1, int(img.height * scale))
            self._board_diagram_img = ctk.CTkImage(
                light_image=img, dark_image=img, size=(disp_w, disp_h))
            self.board_diagram_label.configure(
                image=self._board_diagram_img, text="")
        except Exception:
            self.board_diagram_label.configure(
                image=None, text="Could not load diagram")

    def _show_board_info(self, board):
        """Render a BoardInfo (or None) into the Board tab."""
        from cd3217_analyzer.boards import BoardInfo
        self._show_board_diagram(board)
        if board is None:
            self.board_status_dot.configure(text_color=C["dim"])
            self.board_name_label.configure(text="No board selected")
            self.board_sub_label.configure(
                text="Connect via USB Bridge (board) to see the live pinout, "
                     "or pick a board above.")
            for key in ("sda", "scl", "sck", "miso", "mosi", "cs",
                        "uart_rx"):
                getattr(self, f"_pin_lbl_{key}").configure(text="—")
            self.board_notes_label.configure(
                text="Wiring basics: I2C needs SDA+SCL (+2.2kΩ pull-ups to "
                     "3.3V, GND). SPI flash needs SCK/MISO/MOSI/CS + GND, "
                     "and the target chip powered at its own voltage — "
                     "level-shift for 1.8V targets.")
            return

        self.board_name_label.configure(text=board.name)
        self.board_notes_label.configure(text="\n".join(
            f"•  {n}" for n in board.notes))
        self.board_sub_label.configure(text=(
            f"{board.family}  ·  {'SPI1' if board.hw == 1 else 'hw SPI'}  ·  "
            f"highlighted in the diagram"))
        for key in ("sda", "scl"):
            v = board.i2c.get(key)
            getattr(self, f"_pin_lbl_{key}").configure(
                text=v[1] if v else "—")
        for key in ("sck", "miso", "mosi", "cs"):
            v = board.spi.get(key)
            getattr(self, f"_pin_lbl_{key}").configure(
                text=v[1] if v else "—")
        v = board.uart_rx.get("rx")
        getattr(self, "_pin_lbl_uart_rx").configure(text=v[1] if v else "—")

    def _refresh_board_tab_live(self, adapter=None):
        """Update the Board tab from the connected board's INFO frame.

        Recognizes the board by its INFO name, auto-selects it in the board
        picker, and renders its pinout from the boards table (live pin
        numbers when the firmware reports them).
        """
        from cd3217_analyzer.boards import board_from_info
        adapter = adapter or self.adapter
        if adapter is None:
            return
        try:
            info = adapter.info()
        except Exception:
            info = {}
        board = board_from_info(info)
        if board:
            self.board_status_dot.configure(text_color=C["green"])
            self._show_board_info(board)
            # auto-select the recognized board in the picker dropdown
            try:
                self.board_picker_var.set(board.name)
            except Exception:
                pass
            live = info.get("spi_sck") is not None
            fw = info.get("version")
            self.board_sub_label.configure(text=(
                f"Connected  ·  fw {fw or 'unknown'}  ·  pins reported "
                f"{'live by firmware' if live else 'from the board table'}"))
        else:
            self.board_status_dot.configure(text_color=C["red"])
            self.board_name_label.configure(text="Board did not report pins")
            self.board_sub_label.configure(
                text="Connected, but the firmware did not answer INFO — "
                     "re-flash with the latest firmware for pin reporting.")

    # ─── Board tab (MacBook logic boards: where to connect) ──────────────

    def _build_board_tab(self):
        tab = self.tab_board
        from cd3217_analyzer.boards import MAC_BOARDS

        head = ctk.CTkFrame(tab, fg_color=C["card"], corner_radius=12)
        head.pack(fill="x", padx=12, pady=(12, 6))
        box = ctk.CTkFrame(head, fg_color="transparent")
        box.pack(fill="x", padx=16, pady=12)
        ctk.CTkLabel(
            box, text="MacBook logic board — where to connect the "
                      "adapter to its CD3217 bus",
            font=F["heading"], text_color=C["accent"]
        ).pack(anchor="w")
        ctk.CTkLabel(
            box,
            text="Each USB-C power port is run by one CD3217 (ACE) "
                 "controller. The analyzer talks to it on an onboard 3.3 V "
                 "I2C bus (not the USB-C connector CC pins — those carry "
                 "PD/SPI boot traffic). Pick a model for the recommended tap "
                 "point.",
            font=F["small"], text_color=C["dim"], justify="left",
            wraplength=680,
        ).pack(anchor="w", pady=(4, 0))

        row = ctk.CTkFrame(head, fg_color="transparent")
        row.pack(fill="x", pady=(8, 2))
        ctk.CTkLabel(row, text="Model:",
                     font=F["body"], text_color=C["dim"]).pack(side="left")
        self.mac_picker_var = ctk.StringVar(value="")
        picker = ctk.CTkOptionMenu(
            row, variable=self.mac_picker_var,
            values=[""] + sorted((b.model for b in MAC_BOARDS.values()),
                                 key=str.lower),
            command=self._on_mac_picked, width=380,
            fg_color=C["entry"], button_color=C["btn"],
            button_hover_color=C["btn_hover"], text_color=C["text"],
            dropdown_fg_color=C["panel"])
        picker.pack(side="left", padx=(8, 0))

        # detail panel (scrollable so long notes stay readable)
        bodyc = ctk.CTkFrame(tab, fg_color="transparent")
        bodyc.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        bodyc.grid_columnconfigure(0, weight=1)
        bodyc.grid_rowconfigure(0, weight=1)
        sc = ctk.CTkScrollableFrame(
            bodyc, fg_color=C["card"], corner_radius=12)
        sc.grid(row=0, column=0, sticky="nsew")
        sc.grid_columnconfigure(0, weight=1)
        self.mac_detail = sc
        self._show_mac_info(None)

    def _on_mac_picked(self, name):
        from cd3217_analyzer.boards import MAC_BOARDS
        for key, b in MAC_BOARDS.items():
            if b.model == name:
                self._show_mac_info(b)
                mac_key = key.upper()  # e.g. "a2485" -> "A2485"
                self.current_model = get_model(mac_key)
                if self.current_model:
                    self.log(
                        f"Board: {self.current_model.name} — "
                        + ", ".join(
                            f"{p.ref}@0x{p.address:02X}"
                            for p in self.current_model.positions))
                    addrs = [format_hex_addr(p.address)
                             for p in self.current_model.positions]
                    self.batch_addr_var.set(",".join(addrs))
                    if self.current_model.positions:
                        self.quick_addr_var.set(
                            format_hex_addr(self.current_model.positions[0].address))
                else:
                    self.log(f"No I2C address map for {mac_key} in models.py",
                             "warn")
                return
        self._show_mac_info(None)

    def _show_mac_info(self, mac):
        """Render a MacBookInfo (or None) into the Board tab."""
        sc = self.mac_detail
        for w in sc.winfo_children():
            w.destroy()
        from cd3217_analyzer.boards import MAC_BOARDS
        if mac is None:
            ctk.CTkLabel(
                sc, text="Select a MacBook model above to see where to "
                         "connect the adapter.",
                font=F["body"], text_color=C["dim"],
                wraplength=680, justify="left",
            ).grid(row=0, column=0, sticky="w", padx=8, pady=8)
            return

        r = 0
        ctk.CTkLabel(sc, text=mac.model,
                     font=F["title"], text_color=C["text"]
                     ).grid(row=r, column=0, sticky="w", padx=8, pady=(4, 2)); r += 1
        ctk.CTkLabel(
            sc, text="Logic board " + " / ".join(mac.board_nos) +
                     "   ·   " + str(mac.ports) + " USB-C power port(s)"
                     "   ·   " + mac.ace + "    ·    " + mac.bus,
            font=F["body"], text_color=C["dim"],
        ).grid(row=r, column=0, sticky="w", padx=8, pady=(0, 8)); r += 1

        self._mac_section(sc, r, "Where to connect (tap the I2C bus)",
                          mac.connect, C["green"]); r += len(mac.connect) + 2
        self._mac_section(sc, r, "Observed CD3217 addresses",
                          [mac.addresses], C["yellow"]); r += 3
        self._mac_section(sc, r, "Wiring notes", mac.notes, C["dim"])
        r += len(mac.notes)

        ctk.CTkLabel(
            sc,
            text="Apple does not publish test-point designators for these "
                 "buses; the practical tap is a pull-up/series resistor on "
                 "the named net, or the CD3217 BGA pins (Port1 SDA=B5 / "
                 "SCL=A4, Port2 SDA=B7 / SCL=A6). Verify exact positions in "
                 "the board's boardview (openboarddata / FlexBV) before "
                 "soldering. Bus is 3.3 V open-drain.",
            font=F["small"], text_color=C["dim"], justify="left",
            wraplength=680,
        ).grid(row=r + 1, column=0, sticky="w", padx=8, pady=(12, 4))

    def _mac_section(self, sc, row, title, lines, color):
        ctk.CTkLabel(sc, text=title, font=F["heading"],
                     text_color=color).grid(
            row=row, column=0, sticky="w", padx=8, pady=(10, 2))
        for i, ln in enumerate(lines):
            ctk.CTkLabel(
                sc, text="•  " + ln, font=F["body"], text_color=C["text"],
                justify="left", wraplength=680,
            ).grid(row=row + 1 + i, column=0, sticky="w", padx=18, pady=1)

    def _build_overview_tab(self):
        tab = self.tab_overview
        card = ctk.CTkFrame(tab, fg_color=C["card"], corner_radius=12)
        card.pack(fill="x", padx=12, pady=12)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=18, pady=16)

        self.health_label = ctk.CTkLabel(
            inner, text="--", font=F["score"], text_color=C["dim"], width=90
        )
        self.health_label.pack(side="left", padx=(0, 18))

        info = ctk.CTkFrame(inner, fg_color="transparent")
        info.pack(side="left", fill="x", expand=True)
        self.health_status = ctk.CTkLabel(
            info, text="No device selected", font=F["heading"]
        )
        self.health_status.pack(anchor="w")
        self.health_detail = ctk.CTkLabel(
            info,
            text="Scan the bus, select a device, then diagnose.",
            text_color=C["dim"],
            font=F["body"],
        )
        self.health_detail.pack(anchor="w", pady=(2, 0))

        grid = ctk.CTkFrame(tab, fg_color=C["card"], corner_radius=12)
        grid.pack(fill="x", padx=12, pady=(0, 10))
        self.info_labels = {}
        fields = [
            ("Address", "address"),
            ("Vendor ID", "vid"),
            ("Device ID", "did"),
            ("Mode", "mode"),
            ("Type", "type"),
            ("Identity", "identity"),
            ("Response", "time"),
            ("Chip Type", "chip_type"),
        ]
        for i, (label, key) in enumerate(fields):
            ctk.CTkLabel(
                grid, text=f"{label}", text_color=C["dim"], font=F["small"]
            ).grid(row=i, column=0, sticky="w", padx=14, pady=4)
            lbl = ctk.CTkLabel(grid, text="—", font=F["mono"], text_color=C["text"])
            lbl.grid(row=i, column=1, sticky="w", padx=14, pady=4)
            self.info_labels[key] = lbl
        grid.columnconfigure(1, weight=1)

        faults = ctk.CTkFrame(tab, fg_color=C["card"], corner_radius=12)
        faults.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        ctk.CTkLabel(
            faults, text="Faults & Diagnostics", font=F["heading"], text_color=C["accent"]
        ).pack(anchor="w", padx=14, pady=(12, 4))
        self.faults_text = ctk.CTkTextbox(
            faults, fg_color=C["entry"], font=F["mono_small"], state="disabled"
        )
        self.faults_text.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    def _build_register_tab(self):
        tab = self.tab_registers
        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=12)
        ctk.CTkButton(
            row, text="Read All", width=90, fg_color=C["green"], text_color="#04120a",
            hover_color="#16a34a", command=self._read_registers
        ).pack(side="left")
        ctk.CTkButton(
            row, text="Copy", width=70, fg_color=C["btn"], hover_color=C["btn_hover"],
            command=self._copy_registers
        ).pack(side="left", padx=6)
        ctk.CTkLabel(row, text="Device", text_color=C["dim"]).pack(side="left", padx=(12, 4))
        self.reg_addr_var = ctk.StringVar(value="0x38")
        ctk.CTkEntry(row, textvariable=self.reg_addr_var, width=80, fg_color=C["entry"]).pack(
            side="left"
        )

        self.reg_frame = ctk.CTkScrollableFrame(tab, fg_color=C["entry"], corner_radius=10)
        self.reg_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        hdr = ctk.CTkFrame(self.reg_frame, fg_color=C["panel"], corner_radius=6)
        hdr.pack(fill="x", pady=(0, 2))
        for text, w in [
            ("Offset", 70), ("Name", 150), ("Raw", 180), ("Value", 100), ("Decoded", 200)
        ]:
            ctk.CTkLabel(
                hdr, text=text, font=("Segoe UI", 12, "bold"), text_color=C["accent"],
                width=w, anchor="w"
            ).pack(side="left", padx=4)

    def _build_batch_tab(self):
        tab = self.tab_batch
        ctrl = ctk.CTkFrame(tab, fg_color=C["card"], corner_radius=10)
        ctrl.pack(fill="x", padx=12, pady=12)

        ctk.CTkLabel(ctrl, text="Iterations", text_color=C["dim"]).pack(
            side="left", padx=(12, 4), pady=10
        )
        self.batch_count_var = ctk.StringVar(value="5")
        ctk.CTkEntry(ctrl, textvariable=self.batch_count_var, width=50, fg_color=C["entry"]).pack(
            side="left", pady=10
        )
        ctk.CTkLabel(ctrl, text="Devices", text_color=C["dim"]).pack(
            side="left", padx=(12, 4)
        )
        self.batch_addr_var = ctk.StringVar(value="0x38,0x3F")
        ctk.CTkEntry(
            ctrl, textvariable=self.batch_addr_var, width=220, fg_color=C["entry"]
        ).pack(side="left", pady=10)
        self.batch_start_btn = ctk.CTkButton(
            ctrl, text="Start Batch", width=100, fg_color=C["green"], text_color="#04120a",
            hover_color="#16a34a", command=self._start_batch
        )
        self.batch_start_btn.pack(side="left", padx=10, pady=10)
        ctk.CTkButton(
            ctrl, text="Export CSV", width=90, fg_color=C["btn"],
            hover_color=C["btn_hover"], command=self._save_csv
        ).pack(side="left", pady=10)

        self.batch_progress = ctk.CTkProgressBar(
            tab, fg_color=C["panel"], progress_color=C["accent"]
        )
        self.batch_progress.pack(fill="x", padx=12, pady=(0, 4))
        self.batch_progress.set(0)
        self.batch_status_var = ctk.StringVar(value="Ready")
        ctk.CTkLabel(
            tab, textvariable=self.batch_status_var, text_color=C["dim"], font=F["small"]
        ).pack(anchor="w", padx=14)
        self.batch_frame = ctk.CTkScrollableFrame(tab, fg_color=C["entry"], corner_radius=10)
        self.batch_frame.pack(fill="both", expand=True, padx=12, pady=12)

    def _build_strap_tab(self):
        tab = self.tab_straps
        ctk.CTkLabel(
            tab, text="I2C Strap Calculator", font=F["heading"], text_color=C["accent"]
        ).pack(anchor="w", padx=16, pady=(16, 4))
        ctk.CTkLabel(
            tab,
            text="Decode ADDR / CNTL1 / CNTL2 resistor configuration from Port1 + Port2 addresses.",
            text_color=C["dim"], font=F["body"]
        ).pack(anchor="w", padx=16)

        inp = ctk.CTkFrame(tab, fg_color=C["card"], corner_radius=10)
        inp.pack(fill="x", padx=16, pady=12)
        ctk.CTkLabel(inp, text="Port 1", text_color=C["dim"]).grid(
            row=0, column=0, sticky="w", padx=12, pady=8
        )
        self.strap_p1_var = ctk.StringVar(value="0x38")
        ctk.CTkEntry(inp, textvariable=self.strap_p1_var, width=90, fg_color=C["entry"]).grid(
            row=0, column=1, padx=8, pady=8
        )
        ctk.CTkLabel(inp, text="Port 2", text_color=C["dim"]).grid(
            row=1, column=0, sticky="w", padx=12, pady=8
        )
        self.strap_p2_var = ctk.StringVar(value="0x2F")
        ctk.CTkEntry(inp, textvariable=self.strap_p2_var, width=90, fg_color=C["entry"]).grid(
            row=1, column=1, padx=8, pady=8
        )
        ctk.CTkButton(
            inp, text="Calculate", width=100, fg_color=C["accent"], text_color="#041018",
            hover_color=C["accent_dim"], command=self._calc_straps
        ).grid(row=0, column=2, rowspan=2, padx=16, pady=8)
        ctk.CTkButton(
            inp, text="Placement guide", width=110, fg_color=C["btn"],
            hover_color=C["btn_hover"], command=self._placement_guide
        ).grid(row=0, column=3, rowspan=2, padx=(0, 8), pady=8)

        res = ctk.CTkFrame(tab, fg_color=C["card"], corner_radius=10)
        res.pack(fill="x", padx=16, pady=4)
        self.strap_result_labels = {}
        fields = [
            ("ADDR bits", "addr_bits"), ("Resistor", "addr_resistor"),
            ("CNTL1", "cntl1"), ("CNTL1 source", "cntl1_source"),
            ("CNTL2", "cntl2"), ("CNTL2 source", "cntl2_source"),
            ("Port 1", "port1_addr"), ("Port 2", "port2_addr"),
        ]
        for i, (label, key) in enumerate(fields):
            ctk.CTkLabel(res, text=label, text_color=C["dim"], font=F["small"]).grid(
                row=i, column=0, sticky="w", padx=14, pady=3
            )
            lbl = ctk.CTkLabel(res, text="—", font=F["mono"])
            lbl.grid(row=i, column=1, sticky="w", padx=14, pady=3)
            self.strap_result_labels[key] = lbl

        ref = ctk.CTkFrame(tab, fg_color=C["card"], corner_radius=10)
        ref.pack(fill="both", expand=True, padx=16, pady=12)
        ctk.CTkLabel(
            ref, text="Reference map", font=F["heading"], text_color=C["accent"]
        ).pack(anchor="w", padx=12, pady=(10, 4))
        self.strap_ref_frame = ctk.CTkScrollableFrame(ref, fg_color=C["entry"], corner_radius=8)
        self.strap_ref_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self._update_strap_reference()

    def _build_otp_tab(self):
        tab = self.tab_otp
        ctk.CTkLabel(
            tab, text="OTP Scanner & Diff", font=F["heading"], text_color=C["accent"]
        ).pack(anchor="w", padx=16, pady=(14, 2))
        ctk.CTkLabel(
            tab,
            text="Dump 0x00–0x7F and compare vanilla vs OTP-ed chips.",
            text_color=C["dim"], font=F["body"]
        ).pack(anchor="w", padx=16)

        ctrl = ctk.CTkFrame(tab, fg_color=C["card"], corner_radius=10)
        ctrl.pack(fill="x", padx=16, pady=10)
        ctk.CTkLabel(ctrl, text="Device", text_color=C["dim"]).pack(side="left", padx=(12, 4), pady=10)
        self.otp_addr_var = ctk.StringVar(value="0x38")
        ctk.CTkEntry(ctrl, textvariable=self.otp_addr_var, width=80, fg_color=C["entry"]).pack(
            side="left", pady=10
        )
        self.otp_scan_btn = ctk.CTkButton(
            ctrl, text="Scan OTP", width=100, fg_color=C["green"], text_color="#04120a",
            hover_color="#16a34a", command=self._otp_scan_device
        )
        self.otp_scan_btn.pack(side="left", padx=8, pady=10)
        ctk.CTkButton(
            ctrl, text="Save Golden", width=110, fg_color=C["btn"],
            hover_color=C["btn_hover"], command=self._otp_save_golden
        ).pack(side="left", padx=4, pady=10)
        ctk.CTkButton(
            ctrl, text="Verify vs Golden", width=130, fg_color=C["btn"],
            hover_color=C["btn_hover"], command=self._otp_verify_golden
        ).pack(side="left", padx=4, pady=10)
        ctk.CTkButton(
            ctrl, text="Write OTP", width=100, fg_color=C["btn"],
            hover_color=C["btn_hover"], command=self._otp_write_stub
        ).pack(side="left", padx=4, pady=10)
        ctk.CTkButton(
            ctrl, text="Import", width=70, fg_color=C["btn"], hover_color=C["btn_hover"],
            command=self._otp_import_file
        ).pack(side="left", padx=4, pady=10)
        ctk.CTkButton(
            ctrl, text="Diff Files", width=90, fg_color=C["btn"], hover_color=C["btn_hover"],
            command=self._otp_diff_dialog
        ).pack(side="left", padx=4, pady=10)

        self.otp_progress = ctk.CTkProgressBar(tab, fg_color=C["panel"], progress_color=C["accent"])
        self.otp_progress.pack(fill="x", padx=16, pady=(0, 4))
        self.otp_progress.set(0)
        self.otp_status_var = ctk.StringVar(value="Ready")
        ctk.CTkLabel(
            tab, textvariable=self.otp_status_var, text_color=C["dim"], font=F["small"]
        ).pack(anchor="w", padx=18)

        split = ctk.CTkFrame(tab, fg_color="transparent")
        split.pack(fill="both", expand=True, padx=12, pady=10)
        split.grid_columnconfigure(0, weight=1)
        split.grid_columnconfigure(1, weight=1)
        split.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(split, fg_color=C["card"], corner_radius=10)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        ctk.CTkLabel(left, text="Current Dump", font=F["heading"], text_color=C["accent"]).pack(
            anchor="w", padx=12, pady=(10, 4)
        )
        self.otp_dump_text = ctk.CTkTextbox(
            left, fg_color=C["entry"], font=F["mono_small"], state="disabled"
        )
        self.otp_dump_text.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        right = ctk.CTkFrame(split, fg_color=C["card"], corner_radius=10)
        right.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        ctk.CTkLabel(right, text="Diff Result", font=F["heading"], text_color=C["accent"]).pack(
            anchor="w", padx=12, pady=(10, 4)
        )
        self.otp_diff_text = ctk.CTkTextbox(
            right, fg_color=C["entry"], font=F["mono_small"], state="disabled"
        )
        self.otp_diff_text.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def _build_flash_tab(self):
        tab = self.tab_flash
        ctk.CTkLabel(
            tab, text="SPI Flash Manager", font=F["heading"], text_color=C["accent"]
        ).pack(anchor="w", padx=16, pady=(14, 2))
        ctk.CTkLabel(
            tab,
            text="FTDI FT232H SPI mode. I2C is disconnected automatically while SPI is active.",
            text_color=C["dim"], font=F["body"]
        ).pack(anchor="w", padx=16)

        conn = ctk.CTkFrame(tab, fg_color=C["card"], corner_radius=10)
        conn.pack(fill="x", padx=16, pady=10)
        ctk.CTkButton(
            conn, text="Connect SPI", width=110, fg_color=C["green"], text_color="#04120a",
            hover_color="#16a34a", command=self._flash_connect
        ).pack(side="left", padx=12, pady=10)
        ctk.CTkButton(
            conn, text="Disconnect SPI", width=120, fg_color=C["btn"],
            hover_color=C["btn_hover"], command=self._flash_disconnect
        ).pack(side="left", padx=4, pady=10)
        self.flash_conn_status = ctk.CTkLabel(
            conn, text="● SPI disconnected", text_color=C["red"]
        )
        self.flash_conn_status.pack(side="left", padx=12)

        info = ctk.CTkFrame(tab, fg_color=C["card"], corner_radius=10)
        info.pack(fill="x", padx=16, pady=4)
        ctk.CTkButton(
            info, text="Detect", width=80, fg_color=C["btn"], hover_color=C["btn_hover"],
            command=self._flash_detect
        ).pack(side="left", padx=12, pady=10)
        ctk.CTkButton(
            info, text="Power Up", width=80, fg_color=C["btn"], hover_color=C["btn_hover"],
            command=self._flash_power_up
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            info, text="Reset", width=70, fg_color=C["btn"], hover_color=C["btn_hover"],
            command=self._flash_reset
        ).pack(side="left", padx=4)
        self.flash_info_var = ctk.StringVar(value="No chip detected")
        ctk.CTkLabel(
            info, textvariable=self.flash_info_var, text_color=C["text"], font=F["mono_small"]
        ).pack(side="left", padx=12)

        act = ctk.CTkFrame(tab, fg_color=C["card"], corner_radius=10)
        act.pack(fill="x", padx=16, pady=8)
        ctk.CTkButton(
            act, text="Read Flash", width=100, fg_color=C["accent"], text_color="#041018",
            hover_color=C["accent_dim"], command=self._flash_read
        ).pack(side="left", padx=12, pady=10)
        ctk.CTkButton(
            act, text="Write File", width=100, fg_color=C["red"], hover_color="#e11d48",
            command=self._flash_write
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            act, text="Erase Chip", width=100, fg_color=C["red"], hover_color="#e11d48",
            command=self._flash_erase
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            act, text="Restore", width=90, fg_color=C["orange"], hover_color="#ea580c",
            command=self._flash_restore
        ).pack(side="left", padx=4)

        self.flash_progress = ctk.CTkProgressBar(
            tab, fg_color=C["panel"], progress_color=C["accent"]
        )
        self.flash_progress.pack(fill="x", padx=16, pady=(0, 4))
        self.flash_progress.set(0)
        self.flash_status_var = ctk.StringVar(value="Ready")
        ctk.CTkLabel(
            tab, textvariable=self.flash_status_var, text_color=C["dim"], font=F["small"]
        ).pack(anchor="w", padx=18)

        hv = ctk.CTkFrame(tab, fg_color=C["card"], corner_radius=10)
        hv.pack(fill="both", expand=True, padx=16, pady=12)
        ctk.CTkLabel(
            hv, text="Hex preview", font=F["heading"], text_color=C["accent"]
        ).pack(anchor="w", padx=12, pady=(10, 4))
        self.flash_hex_text = ctk.CTkTextbox(
            hv, fg_color=C["entry"], font=F["mono_small"], state="disabled"
        )
        self.flash_hex_text.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    # ─── UART sniff tab ───────────────────────────────────────────────────

    def _build_uart_tab(self):
        tab = self.tab_uart
        self._uart_active = False
        self._uart_bytes = 0

        card = ctk.CTkFrame(tab, fg_color=C["card"], corner_radius=12)
        card.pack(fill="x", padx=12, pady=12)
        box = ctk.CTkFrame(card, fg_color="transparent")
        box.pack(fill="x", padx=14, pady=12)

        ctk.CTkLabel(box, text="Baud:", text_color=C["dim"],
                     font=F["body"]).pack(side="left", padx=(0, 6))
        self.uart_baud_var = ctk.StringVar(value="Auto-detect")
        baud_menu = ctk.CTkOptionMenu(
            box, variable=self.uart_baud_var, width=130,
            values=["Auto-detect"] + [str(b) for b in (
                9600, 19200, 38400, 57600, 115200, 230400, 460800,
                921600, 1000000, 1500000, 2000000, 3000000)],
            fg_color=C["entry"], button_color=C["btn"],
            button_hover_color=C["btn_hover"], text_color=C["text"],
            dropdown_fg_color=C["panel"])
        baud_menu.pack(side="left", padx=(0, 10))

        self.uart_start_btn = ctk.CTkButton(
            box, text="Start", width=80, height=30, fg_color=C["green"],
            hover_color="#16a34a", text_color="#04120a",
            command=self._uart_start)
        self.uart_start_btn.pack(side="left", padx=4)
        self.uart_stop_btn = ctk.CTkButton(
            box, text="Stop", width=70, height=30, fg_color=C["btn"],
            hover_color=C["btn_hover"], command=self._uart_stop,
            state="disabled")
        self.uart_stop_btn.pack(side="left", padx=4)
        self.uart_clear_btn = ctk.CTkButton(
            box, text="Clear", width=70, height=30, fg_color=C["btn"],
            hover_color=C["btn_hover"], command=self._uart_clear)
        self.uart_clear_btn.pack(side="left", padx=4)
        self.uart_save_btn = ctk.CTkButton(
            box, text="Save log", width=90, height=30, fg_color=C["btn"],
            hover_color=C["btn_hover"], command=self._uart_save)
        self.uart_save_btn.pack(side="left", padx=4)

        self.uart_status = ctk.CTkLabel(
            box, text="Idle — connect a board first", text_color=C["dim"],
            font=F["small"])
        self.uart_status.pack(side="right")

        self.uart_output = ctk.CTkTextbox(
            tab, fg_color=C["entry"], corner_radius=10, font=F["mono"],
            wrap="word", height=460)
        self.uart_output.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    def _uart_adapter(self):
        """The connected USB bridge adapter, or None with a log message."""
        if not (self.connected and isinstance(self.adapter, UsbBridgeAdapter)):
            self.log("UART sniffing needs a board connected via "
                     "USB Bridge (board)", "warn")
            return None
        return self.adapter

    def _uart_start(self):
        adapter = self._uart_adapter()
        if not adapter:
            return
        baud = self.uart_baud_var.get()
        self.uart_start_btn.configure(state="disabled")
        self._uart_bytes = 0

        def work():
            if baud == "Auto-detect":
                self._ui(lambda: self.uart_status.configure(
                    text="Auto-detecting baud (~1.5s)..."))
                try:
                    detected = adapter.uart_autobaud()
                except Exception as e:
                    detected = None
                    self.log(f"UART auto-baud failed: {e}", "err")
                if not detected:
                    self._ui(lambda: (
                        self.uart_status.configure(
                            text="No UART activity detected", ),
                        self.uart_start_btn.configure(state="normal")))
                    self.log("UART auto-baud: no activity on the RX pin — "
                             "check wiring/pull-up and that the target is "
                             "transmitting", "warn")
                    return
                use_baud = detected
                self.log(f"UART auto-baud: detected {detected} baud", "ok")
            else:
                use_baud = int(baud)
            try:
                adapter.uart_setup(use_baud)
            except Exception as e:
                self.log(f"UART setup failed: {e}", "err")
                self._ui(lambda: self.uart_start_btn.configure(
                    state="normal"))
                return
            def go():
                self._uart_active = True
                self.uart_stop_btn.configure(state="normal")
                self.uart_status.configure(
                    text=f"Sniffing at {use_baud} baud (RX only)")
                self.after(150, self._uart_poll)
            self._ui(go)

        threading.Thread(target=work, daemon=True).start()

    def _uart_stop(self, silent=False):
        self._uart_active = False
        adapter = (self.adapter if isinstance(self.adapter, UsbBridgeAdapter)
                   else None)
        if adapter:
            try:
                adapter.uart_setup(0)
            except Exception:
                pass
        self.uart_stop_btn.configure(state="disabled")
        self.uart_start_btn.configure(state="normal")
        self.uart_status.configure(
            text=f"Stopped — {self._uart_bytes} bytes captured")
        if not silent:
            self.log(f"UART sniffing stopped ({self._uart_bytes} bytes)")

    def _uart_poll(self):
        if not self._uart_active:
            return
        if not (self.connected and
                isinstance(self.adapter, UsbBridgeAdapter)):
            self._uart_stop(silent=True)
            return
        try:
            data = self.adapter.uart_read()
        except Exception:
            self._uart_stop(silent=True)
            return
        if data:
            self._uart_bytes += len(data)
            text = "".join(
                "\n" if c == 10 else
                "" if c == 13 else
                (f"<{c:02X}>" if c < 32 or c > 126 else chr(c))
                for c in data)
            self.uart_output.insert("end", text)
            self.uart_output.see("end")
            self.uart_status.configure(
                text=f"Sniffing — {self._uart_bytes} bytes")
        self.after(150, self._uart_poll)

    def _uart_clear(self):
        self.uart_output.delete("1.0", "end")
        self._uart_bytes = 0

    def _uart_save(self):
        content = self.uart_output.get("1.0", "end")
        if not content.strip():
            self.log("UART log is empty", "warn")
            return
        path = filedialog.asksaveasfilename(
            title="Save UART log", defaultextension=".txt",
            filetypes=[("Text log", "*.txt"), ("All files", "*.*")],
            initialfile=f"uart_sniff_{time.strftime('%Y%m%d_%H%M%S')}.txt")
        if not path:
            return
        with open(path, "w", encoding="utf-8", errors="replace") as f:
            f.write(content)
        self.log(f"UART log saved: {path}", "ok")

    def _build_log_tab(self):
        tab = self.tab_log
        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=12)
        ctk.CTkButton(
            row, text="Clear", width=70, fg_color=C["btn"], hover_color=C["btn_hover"],
            command=self._clear_log
        ).pack(side="left")
        ctk.CTkButton(
            row, text="Copy All", width=80, fg_color=C["btn"], hover_color=C["btn_hover"],
            command=self._copy_log
        ).pack(side="left", padx=6)
        self.log_text = ctk.CTkTextbox(tab, fg_color=C["entry"], font=F["mono_small"])
        self.log_text.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    # ─── Helpers ───────────────────────────────────────────────────────────

    def log(self, msg: str, level: str = "info"):
        # Workers call log() directly in several places; tkinter widgets are
        # not thread-safe, so marshal to the UI thread when off-thread.
        if threading.current_thread() is not threading.main_thread():
            try:
                self.after(0, self.log, msg, level)
            except Exception:
                pass
            return
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{ts}] {msg}\n")
        self.log_text.see("end")

    def _set_busy(self, busy: bool, message: str = ""):
        self.busy = busy
        state = "disabled" if busy else "normal"
        for btn in self._busy_buttons:
            try:
                btn.configure(state=state)
            except Exception:
                pass
        self.busy_label.configure(text=message if busy else "")
        if message:
            self.status_left.configure(text=message)

    def _run_bg(self, work: Callable, done_msg: str = "Ready"):
        def runner():
            try:
                work()
            except Exception as e:
                self.after(0, lambda err=e: self.log(f"Error: {err}", "err"))
            finally:
                self.after(0, lambda: self._set_busy(False, ""))
                self.after(0, lambda: self.status_left.configure(text=done_msg))

        threading.Thread(target=runner, daemon=True).start()

    def _check_conn(self) -> bool:
        if self.busy:
            self.log("Busy — wait for current operation", "warn")
            return False
        if not self.connected or not self.adapter:
            self.log("Not connected. Click Connect first.", "warn")
            return False
        return True

    def _check_flash(self) -> bool:
        if self.busy:
            self.log("Busy — wait for current operation", "warn")
            return False
        if not self.flash:
            self.log("Connect SPI first", "warn")
            return False
        return True

    def _parse_addr_field(self, value: str) -> Optional[int]:
        try:
            return parse_hex_address(value)
        except ValueError:
            self.log(f"Invalid address: {value}", "err")
            return None

    def _select_address(self, addr: int):
        self.selected_address = addr
        self.quick_addr_var.set(format_hex_addr(addr))
        self.reg_addr_var.set(format_hex_addr(addr))
        self.otp_addr_var.set(format_hex_addr(addr))
        self._highlight_selection()
        if addr in self.devices:
            self._show_result(self.devices[addr])

    def _highlight_selection(self):
        for addr, row in self.device_rows.items():
            selected = addr == self.selected_address
            row.configure(fg_color=C["card_selected"] if selected else C["card"])

    def _known_scan_addresses(self) -> List[int]:
        addrs = [a for a in KNOWN_ACE2_ADDRESSES.keys()
                 if a != ACE2_BROADCAST_ADDRESS]
        if self.current_model:
            addrs.extend(p.address for p in self.current_model.positions)
        return unique_sorted(addrs)

    # ─── Connection ────────────────────────────────────────────────────────

    def _auto_detect(self):
        self.log("Scanning for I2C adapters...")
        adapter = None
        try:
            adapter = detect_adapter()
        except Exception as e:
            self.log(f"Auto-detect error: {e}", "err")
        if adapter:
            self.adapter = adapter
            self.connected = True
            self._update_conn_status(True)
            self.log(f"Auto-detected: {type(adapter).__name__}", "ok")
            return
        # No FTDI/SMBus adapter: look for a CD3217 board on USB serial.
        def board_scan():
            from cd3217_analyzer.usb_bridge import scan_for_boards
            try:
                boards = scan_for_boards()
            except Exception:
                boards = []
            def report():
                if self.connected:      # user connected meanwhile
                    return
                if not boards:
                    self.log("No adapter or board found. Select adapter and "
                             "click Connect.", "warn")
                    return
                b = boards[0]
                self.log(f"Board found: {b['board']} "
                         f"({b['desc'] or 'USB serial'}) on {b['port']} — "
                         "connecting...", "ok")
                self.adapter_var.set("USB Bridge (board)")
                self.bus_var.set(b["port"])
                self._connect()
            self._ui(report)
        threading.Thread(target=board_scan, daemon=True).start()

    def _toggle_connection(self):
        if self.connected:
            self._disconnect()
        else:
            self._connect()

    def _connect_board_bridge(self) -> Optional[UsbBridgeAdapter]:
        """Connect to a CD3217 board over USB; returns None on failure.

        Uses the Bus/Port field when set; otherwise scans serial ports for a
        board answering the bridge protocol (best match first). Handles the
        handshake, firmware logging, Board-tab refresh and the firmware
        update offer.
        """
        port = normalize_port(self.bus_var.get())
        if not port:
            # No port given: scan USB serial ports for a board that
            # answers our bridge protocol (best match first).
            self.log("No port given — scanning for boards...")
            from cd3217_analyzer.usb_bridge import scan_for_boards
            try:
                boards = scan_for_boards()
            except Exception as e:
                boards = []
                self.log(f"Board scan error: {e}", "warn")
            if not boards:
                self.log("No CD3217 board found on any serial port. "
                         "Plug it in (or set the COM port in "
                         "Bus/Port) and retry.", "warn")
                return None
            if len(boards) > 1:
                names = ", ".join(f"{b['board']} on {b['port']}"
                                  for b in boards)
                self.log(f"Multiple boards found ({names}); "
                         f"using {boards[0]['board']} on "
                         f"{boards[0]['port']}", "warn")
            port = boards[0]["port"]
            self.bus_var.set(port)
            self.log(f"Board found: {boards[0]['board']} "
                     f"({boards[0]['desc'] or 'USB serial'}) on "
                     f"{port}", "ok")
        adapter = UsbBridgeAdapter(port=port)
        adapter.open()
        ok = False
        try:
            ok = adapter.handshake()
        except Exception:
            ok = False
        if not ok:
            adapter.close()
            self.log(f"USB bridge on {port} did not respond to PING. "
                     "Is the board running CD3217 firmware?", "err")
            return None
        # Log which firmware the board is actually running so a wrong
        # flash (e.g. pico2 firmware on a Pico 1) is obvious, and
        # populate the Board tab with its live pinout.
        try:
            b = adapter.info()
            if b and b.get("board"):
                fw = b.get("version")
                self.log(f"Board firmware: {b['board']} "
                         f"(fw {fw or 'unknown'})", "ok")
        except Exception:
            pass
        try:
            self._refresh_board_tab_live(adapter)
        except Exception:
            pass
        # offer a firmware update when the board is outdated
        try:
            self._maybe_offer_board_update(adapter)
        except Exception:
            pass
        return adapter

    def _connect(self):
        if self.spi_adapter:
            self._flash_disconnect()
        selection = self.adapter_var.get()
        self.log(f"Connecting to {selection}...")
        try:
            if selection.startswith("Auto-detect"):
                adapter = detect_adapter()
                if adapter is None:
                    # No FTDI/SMBus hardware: fall back to scanning for a
                    # CD3217 board on USB serial (board-first UX — the user
                    # shouldn't have to pick the adapter type manually).
                    self.log("No FTDI/SMBus adapter found — "
                             "scanning for a CD3217 board...")
                    adapter = self._connect_board_bridge()
                    if adapter is None:
                        self.log("Auto-detect found nothing: no FTDI, no "
                                 "SMBus, no CD3217 board. Plug a board in "
                                 "and retry.", "warn")
                        return
            elif selection == "FTDI FT232H":
                adapter = FTDIAdapter()
                adapter.open()
            elif selection in ("SMBus (Linux)", "CH341"):
                adapter = SMBusAdapter(bus_number=int(self.bus_var.get() or "1"))
                adapter.open()
            elif selection == "USB Bridge (board)":
                adapter = self._connect_board_bridge()
                if adapter is None:
                    return
            else:
                return

            if adapter is None:
                self.log("Could not connect", "err")
                return

            # All branches above already opened the adapter (including
            # detect_adapter(), which opens internally). Only open if something
            # still needs it, and never open an already-open CDC port twice —
            # a second serial.Serial() on a held RPi/ESP32 CDC port wedges
            # Windows' usbser.sys and throws "Access is denied" (errno 13).
            if not getattr(adapter, "is_open", False) \
               and not getattr(adapter, "_i2c", None) \
               and not getattr(adapter, "_bus", None):
                adapter.open()

            self.adapter = adapter
            self.connected = True
            self._update_conn_status(True)
            self.log(f"Connected: {type(adapter).__name__}", "ok")
            if isinstance(adapter, UsbBridgeAdapter):
                self._start_board_watcher(adapter)
        except Exception as e:
            self.log(f"Connection failed: {e}", "err")

    # ─── Board presence watcher ───────────────────────────────────────────

    def _start_board_watcher(self, adapter: UsbBridgeAdapter):
        """Watch the connected board; auto-disconnect when it's removed.

        A board is considered removed when its COM port vanishes from the
        OS enumeration (USB unplug). PING health is used as a secondary
        signal: if the port still exists but the board hasn't answered for
        several checks in a row, we also let go (wedged CDC driver state).
        Runs on a daemon thread; calls back into the UI thread via after().
        """
        self._stop_board_watcher()
        port = adapter.port
        state = {"misses": 0}
        self._board_watch_stop = threading.Event()

        def tick():
            if self._board_watch_stop.is_set():
                return
            if not self.connected or self.adapter is not adapter:
                return
            try:
                from cd3217_analyzer.usb_bridge import port_exists
                present = port_exists(port)
            except Exception:
                present = True
            alive = False
            if present:
                # While a scan/diagnose/batch is running, the same CDC port is
                # in use by the background worker. PINGing it from this thread
                # only adds bus traffic and (before the v0.6.20 lock) raced
                # frames. Skip the health ping during busy windows and just
                # keep the port-presence check (pure OS enumeration, no serial
                # I2C traffic); count no misses so a skipped check can't look
                # like the board vanished.
                if self.busy:
                    state["misses"] = 0
                    self.after(2000, lambda: threading.Thread(
                        target=tick, daemon=True).start())
                    return
                alive = adapter.is_alive()
                state["misses"] = 0 if alive else state["misses"] + 1
            if (not present) or state["misses"] >= 5:
                def gone():
                    if self.connected and self.adapter is adapter:
                        self.log("Board removed — disconnecting.", "warn")
                        self._disconnect()
                self._ui(gone)
                return
            self.after(2000, lambda: threading.Thread(
                target=tick, daemon=True).start())

        self.log(f"Watching {port} for board removal")
        self.after(2000, lambda: threading.Thread(
            target=tick, daemon=True).start())

    def _stop_board_watcher(self):
        evt = getattr(self, "_board_watch_stop", None)
        if evt:
            evt.set()
        self._board_watch_stop = None

    def _flash_board(self):
        """Flash firmware (.uf2 / .bin) to a connected board."""
        fpath = filedialog.askopenfilename(
            title="Select firmware to flash",
            filetypes=[
                ("Firmware", "*.uf2 *.bin"),
                ("UF2 (Pico/RP2040)", "*.uf2"),
                ("Binary (ESP32)", "*.bin"),
                ("All files", "*.*"),
            ],
        )
        if not fpath:
            return

        # Read UI state on the UI thread — StringVar/widget access from a
        # worker thread is not thread-safe.
        port = self.bus_var.get().strip() if self.connected else None

        def worker():
            try:
                from cd3217_analyzer.flash_board import find_bootsel_drives, flash_file
                if fpath.lower().endswith(".uf2"):
                    drives = find_bootsel_drives()
                    if not drives:
                        self.log("No Pico in BOOTSEL mode. Hold BOOTSEL and "
                                 "plug in the board, then retry.", "warn")
                        return
                    msg = flash_file(fpath, bootsel_drive=drives[0])
                else:
                    if not port:
                        self.log("Connect to the board first (enter COM port in "
                                 "Bus/Port), then Flash.", "warn")
                        return
                    msg = flash_file(fpath, port=port)
                self.log(msg, "ok")
            except Exception as e:
                self.log(f"Flash failed: {e}", "err")

        threading.Thread(target=worker, daemon=True).start()

    def _disconnect(self):
        self._stop_board_watcher()
        if self.adapter:
            try:
                self.adapter.close()
            except Exception:
                pass
            self.adapter = None
        self.connected = False
        self.devices.clear()
        self.scan_results.clear()
        self.selected_address = None
        self._update_conn_status(False)
        self._clear_devices()
        try:
            self.board_status_dot.configure(text_color=C["dim"])
        except Exception:
            pass
        self.log("Disconnected")

    def _update_conn_status(self, connected: bool):
        if connected:
            self.conn_status.configure(text="● Connected", text_color=C["green"])
            self.connect_btn.configure(
                text="Disconnect", fg_color=C["red"], hover_color="#e11d48",
                text_color=C["bright"]
            )
        else:
            self.conn_status.configure(text="● Disconnected", text_color=C["red"])
            self.connect_btn.configure(
                text="Connect", fg_color=C["green"], hover_color="#16a34a",
                text_color="#04120a"
            )

    # ─── Scanning ──────────────────────────────────────────────────────────

    def _scan_bus(self):
        if not self._check_conn():
            return
        self._set_busy(True, "Scanning bus...")
        self.log("Scanning I2C bus (0x08–0x77)...")

        def work():
            devices = [a for a in self.adapter.scan(0x08, 0x77)
                       if a != ACE2_BROADCAST_ADDRESS]
            # Second chance: a chip can NACK the sequential scan while it
            # is busy or still booting — retry those model sockets with a
            # stable ping before declaring them MISSING.
            recovered = []
            if self.current_model:
                found = set(devices)
                for p in self.current_model.positions:
                    if p.address in found:
                        continue
                    if self._ping_stable(p.address):
                        devices.append(p.address)
                        recovered.append(p.address)
            devices = sorted(set(devices))
            self.scan_results = devices
            if recovered:
                self.after(0, self.log, "Second-chance ping recovered: "
                           + ", ".join(format_hex_addr(a) for a in recovered),
                           "ok")
            if self.current_model:
                expected = self._merge_expected(devices)
                self.after(0, self._show_devices, expected, set(devices), True)
                self.after(0, self._update_strap_reference)
            else:
                self.after(0, self._show_devices, devices)

        self._run_bg(work, "Scan complete")

    def _quick_scan(self):
        if not self._check_conn():
            return
        addrs = self._known_scan_addresses()
        self._set_busy(True, "Quick scan...")
        self.log(f"Quick scanning {len(addrs)} known addresses...")

        def work():
            found = [a for a in addrs if self._ping_stable(a)]
            self.scan_results = found
            if self.current_model:
                expected = self._merge_expected(found)
                self.after(0, self._show_devices, expected, set(found), True)
                self.after(0, self._update_strap_reference)
            else:
                self.after(0, self._show_devices, found)

        self._run_bg(work, "Quick scan complete")

    def _merge_expected(self, found: List[int]) -> List[int]:
        """Display list when a model is selected: the board's full address
        map in board order, then any extra found addresses."""
        model_addrs = [p.address for p in self.current_model.positions]
        extras = [a for a in found if a not in model_addrs]
        return model_addrs + extras

    def _stress_selected(self):
        """S1: bus-speed stress probe on the selected device (100k vs 400k)."""
        addr = self.selected_address
        if addr is None:
            self.log("Select a device first (click a row).", "warn")
            return
        if not self._check_conn():
            return
        self._set_busy(True, f"Stress test {format_hex_addr(addr)}...")
        self.log(f"Stress test {format_hex_addr(addr)}: identity reads at "
                 "100 kHz vs 400 kHz...")

        def work():
            analyzer = CD3217Analyzer(self.adapter, addresses=[addr])
            res = analyzer.stress_test_margin(addr)
            lvl = {"ample-margin": "ok", "marginal": "warn",
                   "bus-problem": "err", "no-response": "warn",
                   "unavailable": "warn"}.get(res["verdict"], "info")
            self.log(f"Stress test {format_hex_addr(addr)} "
                     f"[{res['verdict']}]: {res['detail']}", lvl)

        self._run_bg(work, "Stress test complete")

    def _bus_check(self):
        """F4: measure SDA/SCL idle levels through the board bridge."""
        if not self._check_conn():
            return
        self._set_busy(True, "Bus check...")
        self.log("Bus check: measuring SDA/SCL idle levels...")

        def work():
            try:
                res = self.adapter.bus_check()
            except Exception as e:
                self.log(f"Bus check failed: {e}", "err")
                return
            self.log(
                "SDA idle HIGH ✓ (pulled up, healthy)" if res["sda"] else
                "SDA held LOW ✗ — a chip or wiring is stuck on the bus "
                "(the bridge clears it on the next failed transaction)",
                "ok" if res["sda"] else "err")
            self.log(
                "SCL idle HIGH ✓" if res["scl"] else
                "SCL held LOW ✗ — line shorted/absent, or a clock-stretching "
                "slave is wedged (power-cycle the board/chip)",
                "ok" if res["scl"] else "err")

        self._run_bg(work, "Bus check complete")

    def _placement_guide(self):
        """F1: donor placement guidance from a quick scan + model map."""
        if not self.current_model:
            self.log("Select a MacBook model first (Board tab).", "warn")
            return
        if not self._check_conn():
            return
        addrs = self._known_scan_addresses()
        self._set_busy(True, "Placement guide scan...")
        self.log(f"Placement guide: scanning {len(addrs)} addresses...")

        def work():
            found = [a for a in addrs if self._ping_stable(a)]
            self.scan_results = found
            from cd3217_analyzer.models import build_placement_guide
            lines = build_placement_guide(self.current_model, found)
            self.after(0, self._show_placement_guide, lines)

        self._run_bg(work, "Placement guide ready")

    def _show_placement_guide(self, lines):
        dlg = ctk.CTkToplevel(self)
        dlg.title("Donor placement guide")
        dlg.geometry("640x460")
        txt = ctk.CTkTextbox(dlg, font=F["mono"], fg_color=C["entry"],
                             text_color=C["text"], wrap="word")
        txt.pack(fill="both", expand=True, padx=12, pady=(12, 6))
        txt.insert("1.0", "\n".join(lines))
        txt.configure(state="disabled")
        btns = ctk.CTkFrame(dlg, fg_color="transparent")
        btns.pack(pady=(0, 10))

        def copy():
            self.clipboard_clear()
            self.clipboard_append("\n".join(lines))
            self.log("Placement guide copied to clipboard", "ok")

        ctk.CTkButton(btns, text="Copy", width=80, fg_color=C["btn"],
                      hover_color=C["btn_hover"], command=copy).pack(
            side="left", padx=4)
        ctk.CTkButton(btns, text="Close", width=80, fg_color=C["btn"],
                      hover_color=C["btn_hover"], command=dlg.destroy).pack(
            side="left", padx=4)
        for ln in lines:
            self.log(ln, "info" if "answers —" in ln or "empty — a" in ln
                     else "warn")

    def _ping_stable(self, addr: int) -> bool:
        """Ping with one retry — the first ping after a NACKed dead address
        can fail on a healthy chip."""
        if self.adapter.ping(addr):
            return True
        time.sleep(0.05)
        return self.adapter.ping(addr)

    def _model_scan(self):
        if not self._check_conn():
            return
        if not self.current_model:
            self.log("Select a MacBook model first", "warn")
            return
        addrs = [p.address for p in self.current_model.positions]
        self._set_busy(True, f"Scanning {self.current_model.model_id}...")
        self.log(f"Model scan {self.current_model.model_id}: {', '.join(format_hex_addr(a) for a in addrs)}")

        def work():
            found = [a for a in addrs if self._ping_stable(a)]
            self.scan_results = found
            self.after(0, self._show_devices, addrs, set(found), True)

        self._run_bg(work, "Model scan complete")

    def _show_devices(self, devices: List[int], found: Optional[set] = None,
                      expected: bool = False):
        self._clear_devices()
        if found is None:
            found = set(devices)
        missing = [a for a in devices if a not in found]
        if expected:
            self.device_count_var.set(
                f"{len(found)} found / {len(devices)} expected")
        else:
            self.device_count_var.set(f"{len(found)} found")
        for addr in devices:
            desc = self._device_label(addr)
            if addr in found:
                self._add_device_row(addr, "?", "—", desc, is_ace2_address(addr))
            else:
                self._add_device_row(addr, "MISSING", "—",
                                     f"{desc} · NOT FOUND", is_ace2_address(addr))
        if found:
            self.log(f"Found {len(found)} device(s)", "ok")
        if missing:
            self.log(
                f"{len(missing)} of {len(devices)} expected address(es) missing: "
                + ", ".join(format_hex_addr(a) for a in missing), "warn")
        if not found and not devices:
            self.log("No devices found", "warn")

    def _device_label(self, addr: int) -> str:
        label = None
        if self.current_model:
            for p in self.current_model.positions:
                if p.address == addr:
                    label = f"{p.ref} · {p.addressing}"
                    break
        cls = chip_class(addr)
        if label:
            return f"{label} · {cls}" if cls else label
        return KNOWN_ACE2_ADDRESSES.get(addr, "Unknown")[:44]

    def _add_device_row(self, addr, health, score, desc, is_ace2=True):
        row = ctk.CTkFrame(self.device_frame, fg_color=C["card"], corner_radius=8, height=42)
        row.pack(fill="x", pady=3, padx=2)
        row.pack_propagate(False)

        color = C["accent"] if is_ace2 else C["dim"]
        ctk.CTkLabel(
            row, text=format_hex_addr(addr), font=("Consolas", 13, "bold"),
            text_color=color, width=70, anchor="w"
        ).pack(side="left", padx=10)
        health_color = (
            C["green"] if health == "PASS" else
            C["yellow"] if health == "WARN" else
            C["red"] if health in ("FAIL", "MISSING") else C["dim"]
        )
        ctk.CTkLabel(
            row, text=str(health), font=("Segoe UI", 13, "bold"),
            text_color=health_color, width=55
        ).pack(side="left")
        ctk.CTkLabel(
            row, text=str(score), font=F["body"], text_color=C["text"], width=40
        ).pack(side="left")
        ctk.CTkLabel(
            row, text=desc, text_color=C["dim"], font=F["small"], anchor="w"
        ).pack(side="left", padx=8, fill="x", expand=True)

        ctk.CTkButton(
            row, text="Diag", width=56, height=26, fg_color=C["btn"],
            hover_color=C["btn_hover"],
            command=lambda a=addr: self._diagnose_address(a)
        ).pack(side="right", padx=8)

        def on_click(_event=None, a=addr):
            self._select_address(a)

        row.bind("<Button-1>", on_click)
        for child in row.winfo_children():
            child.bind("<Button-1>", on_click)

        self.device_rows[addr] = row
        if self.selected_address is None:
            self._select_address(addr)
        else:
            self._highlight_selection()

    def _clear_devices(self):
        for row in self.device_rows.values():
            row.destroy()
        self.device_rows.clear()

    def _refresh_devices(self):
        if not self._check_conn():
            return
        if self.current_model:
            # Diagnose the whole board map even if the last scan missed
            # chips: the sequential bus scan can transiently NACK a chip
            # that is busy/booting (ACE2 emits bus junk of its own), and a
            # per-chip diagnose with ping retries recovers it. Diagnosing
            # only the scan-found set produced "one good IC + the rest
            # MISSING" even on healthy boards.
            from cd3217_analyzer.models import merge_diagnose_targets
            addrs = merge_diagnose_targets(self.current_model,
                                           self.scan_results)
        else:
            if not self.scan_results:
                self.log("Scan first", "warn")
                return
            addrs = list(self.scan_results)
        self._set_busy(True, "Diagnosing all...")
        self.log(f"Diagnose All: {len(addrs)} target(s)")

        def work():
            analyzer = CD3217Analyzer(self.adapter)
            # v0.7.2: a chip that NACKed right after the previous chip's
            # read burst usually answers fine seconds later — exactly what
            # the manual per-chip clicks show. Re-diagnose transport
            # failures after a settle (keeps the best verdict, never
            # downgrades a PASS).
            retry_settles = (0.0, 0.8, 1.6)
            for addr in addrs:
                best = None
                for attempt, settle in enumerate(retry_settles, 1):
                    if settle:
                        time.sleep(settle)
                    try:
                        result = analyzer.diagnose_device(addr)
                        self._apply_socket_expectations(analyzer, result)
                    except Exception as e:
                        self.after(0, self.log,
                                   f"Error {format_hex_addr(addr)}: {e}", "err")
                        continue
                    rank = CD3217Analyzer._HEALTH_RANK.get(result.health, 0)
                    if best is None or rank > CD3217Analyzer._HEALTH_RANK.get(
                            best.health, 0):
                        best = result
                    if not CD3217Analyzer.is_retryable_failure(result):
                        break
                    if attempt < len(retry_settles):
                        self.after(0, self.log,
                                   f"{format_hex_addr(addr)}: no answer on "
                                   f"pass {attempt} — settling bus, retrying...",
                                   "warn")
                if best is not None:
                    self.devices[addr] = best
                # brief pause between devices so a dead chip's NACK does not
                # contaminate the reads of the next healthy chip
                time.sleep(0.08)
            # Guidance: if MOST model sockets stayed silent while extras
            # answered elsewhere, the selected model's address map may not
            # match this board (some maps are flagged UNVERIFIED).
            try:
                model_addrs = ([p.address for p in self.current_model.positions]
                               if self.current_model else [])
                silent = [a for a in model_addrs if self.devices.get(a)
                          and self.devices[a].health == HealthStatus.FAIL
                          and not self.devices[a].faults]
                live_extra = [a for a in self.devices
                              if a not in model_addrs
                              and self.devices[a].responds]
                if len(silent) >= max(2, len(model_addrs) // 2) and live_extra:
                    self.after(0, self.log,
                               "Most model sockets silent while chips answer "
                               "at other addresses — the selected model's "
                               "address map may not match this board (some "
                               "maps are UNVERIFIED). Run Scan Bus and check "
                               "the Straps placement guide.", "warn")
            except Exception:
                pass
            self._report_bus_health(analyzer)
            self.after(0, self._refresh_display)

        self._run_bg(work, "Diagnose all complete")

    def _refresh_display(self):
        self._clear_devices()
        shown = list(self.devices.keys())
        if self.current_model:
            model_addrs = [p.address for p in self.current_model.positions]
            shown = model_addrs + [a for a in shown if a not in model_addrs]
        for addr in shown:
            dev = self.devices.get(addr)
            if dev is not None:
                desc = self._device_label(addr)
                if dev.device_info:
                    desc += (f"  ·  {dev.device_info.split()[0]}"
                             f" FW{dev.fw_version} {dev.fw_variant}").rstrip()
                self._add_device_row(
                    addr, dev.health.value, dev.health_score,
                    desc, is_ace2_address(addr)
                )
            elif addr in self.scan_results:
                self._add_device_row(
                    addr, "?", "—", self._device_label(addr),
                    is_ace2_address(addr)
                )
            else:
                self._add_device_row(
                    addr, "MISSING", "—",
                    f"{self._device_label(addr)} · NOT FOUND",
                    is_ace2_address(addr)
                )

    # ─── Diagnosis ─────────────────────────────────────────────────────────

    def _diagnose_selected(self):
        if self.selected_address is not None:
            self._diagnose_address(self.selected_address)
        else:
            self._diagnose_quick()

    def _diagnose_quick(self):
        if not self._check_conn():
            return
        addr = self._parse_addr_field(self.quick_addr_var.get())
        if addr is None:
            return
        self._diagnose_address(addr)

    def _diagnose_address(self, address: int):
        if not self._check_conn():
            return
        self._select_address(address)
        self._set_busy(True, f"Diagnosing {format_hex_addr(address)}...")
        self.log(f"Diagnosing {format_hex_addr(address)}...")

        def work():
            analyzer = CD3217Analyzer(self.adapter)
            result = analyzer.diagnose_device(address)
            self._apply_socket_expectations(analyzer, result)
            self.devices[address] = result
            self.after(0, self._show_result, result)
            self.after(0, self._refresh_display)

        self._run_bg(work, f"{format_hex_addr(address)} done")

    def _apply_socket_expectations(self, analyzer: CD3217Analyzer,
                                   result: DeviceResult) -> None:
        """Validate the chip against the selected board's socket data."""
        if not self.current_model:
            return
        for p in self.current_model.positions:
            if p.address == result.address:
                analyzer.apply_socket_expectations(result, p)
                break

    def _report_bus_health(self, analyzer: CD3217Analyzer) -> None:
        """Surface the session's bus-integrity counters to the UI.

        A flaky probe tap (extra capacitance / shared pull-ups, TI SLVA689)
        can NACK healthy chips. When the analyzer saw NACKs/garbled reads we
        say so explicitly instead of letting the chips take the blame.
        """
        try:
            summary = analyzer.bus_health_summary()
            level = "warn" if analyzer.bus_stats.marginal else "info"
            first, _, rest = summary.partition("\n")
            self.last_bus_stats = analyzer.bus_stats
            self.after(0, self.log, first, level)
            if rest:
                for ln in rest.split("\n"):
                    if ln.strip():
                        self.after(0, self.log, ln.strip(), level)
        except Exception as e:
            self.after(0, self.log, f"Bus health: {e}", "err")

    def _show_result(self, result: DeviceResult):
        score = result.health_score
        if result.health == HealthStatus.PASS:
            color, status = C["green"], "HEALTHY"
        elif result.health == HealthStatus.WARN:
            color, status = C["yellow"], "WARNING"
        elif result.health == HealthStatus.FAIL:
            color, status = C["red"], "FAULTY"
        else:
            color, status = C["dim"], "UNKNOWN"

        self.health_label.configure(text=str(score), text_color=color)
        self.health_status.configure(text=status, text_color=color)
        parts = []
        if result.faults:
            parts.append(f"{len(result.faults)} fault(s)")
        if result.scan_time_ms:
            parts.append(f"{result.scan_time_ms:.0f} ms")
        if result.mode:
            parts.append(result.mode.strip())
        self.health_detail.configure(text=" · ".join(parts) if parts else "All checks passed")

        self.info_labels["address"].configure(text=format_hex_addr(result.address))
        self.info_labels["vid"].configure(text=result.vendor_id or "N/A")
        self.info_labels["did"].configure(text=result.device_id or "N/A")
        self.info_labels["mode"].configure(text=result.mode or "N/A")
        self.info_labels["type"].configure(text=result.device_type or "N/A")
        # Register 0x2F identity string: silicon + FW + variant tag
        # (the ZACEx/RACEx-xxxxx variant is the role build — the donor-
        # matching signal). Prefer DID-decoded silicon for the prefix.
        identity = "N/A"
        if result.device_info:
            first = result.silicon or result.device_info.split()[0]
            bits = [first.lstrip("@#*")]
            if result.fw_version:
                bits.append(f"FW{result.fw_version}")
            if result.fw_variant:
                bits.append(result.fw_variant)
            identity = "  ".join(b for b in bits if b)
        self.info_labels["identity"].configure(text=identity)
        self.info_labels["time"].configure(text=f"{result.scan_time_ms:.1f} ms")

        cls = chip_class(result.address)
        measured = ("Vanilla TI (VID 0x0451)" if result.is_vanilla
                    else "Apple OTP-ed (VID 0x2804)"
                    if result.is_vanilla is False else "")
        if measured:
            chip = measured
        elif cls:
            chip = cls
        else:
            chip = "Unknown type"
        if self.current_model:
            for p in self.current_model.positions:
                if p.address == result.address:
                    want = f"needs {p.chip_class.upper()} chip" if p.chip_class else ""
                    chip = f"{p.ref} · {p.addressing.upper()}" + \
                           (f" · {want}" if want else "") + \
                           (f" · installed: {measured}" if measured else "")
                    break
        self.info_labels["chip_type"].configure(text=chip)

        self.faults_text.configure(state="normal")
        self.faults_text.delete("1.0", "end")
        if not result.faults:
            self.faults_text.insert("end", "✓ All checks passed\n")
            self.faults_text.insert("end", "Device responding on I2C.\n")
            if result.mode and "APP" in result.mode.upper():
                self.faults_text.insert("end", "Mode: Application (normal).\n")
        else:
            self.faults_text.insert("end", f"FAULTS: {len(result.faults)}\n\n")
            for fault in result.faults:
                self.faults_text.insert("end", f"  • {fault.value}\n")
            if result.fault_details:
                self.faults_text.insert("end", "\nDetails:\n")
                for d in result.fault_details:
                    self.faults_text.insert("end", f"  - {d}\n")
            if any(f == FaultType.BOOT_FAILED for f in result.faults):
                # F3: persistent BOOT usually means the chip can't load its
                # patch bundle from the shared SPI ROM — route the user to
                # the ROM check the app already has.
                self.faults_text.insert(
                    "end",
                    "\n→ Persistent BOOT: the chip can't load its firmware "
                    "from the ACE ROM. Check it in the Flash tab (Detect → "
                    "Dump) and re-program from a golden dump if needed — a "
                    "shorted/blank ROM shows the same symptoms as a bad "
                    "chip (repair case reports, 2025–2026).\n")
                self.log("Persistent BOOT — check the ACE ROM (Flash tab: "
                         "Detect + Dump)", "warn")
        self.faults_text.configure(state="disabled")

        try:
            self.tabs.set("Overview")
        except Exception:
            pass
        level = "ok" if result.health == HealthStatus.PASS else "warn"
        self.log(
            f"{format_hex_addr(result.address)}: {result.health.value} ({score})", level
        )
        self.status_left.configure(
            text=f"{format_hex_addr(result.address)}: {result.health.value} ({score}/100)"
        )

    # ─── Registers ─────────────────────────────────────────────────────────

    def _dump_selected(self):
        if self.selected_address is not None:
            self.reg_addr_var.set(format_hex_addr(self.selected_address))
        self._read_registers()

    def _read_registers(self):
        if not self._check_conn():
            return
        addr = self._parse_addr_field(self.reg_addr_var.get())
        if addr is None:
            return
        self._set_busy(True, f"Reading registers {format_hex_addr(addr)}...")
        self.log(f"Reading registers from {format_hex_addr(addr)}...")
        for widget in self.reg_frame.winfo_children()[1:]:
            widget.destroy()

        def work():
            analyzer = CD3217Analyzer(self.adapter)
            for offset in sorted(REGISTERS.keys()):
                reg_def = REGISTERS[offset]
                read = analyzer.read_register(addr, offset, reg_def.length)
                if read:
                    self.after(
                        0, self._add_reg_row, f"0x{offset:02X}", read.name,
                        read.raw_bytes.hex(), f"0x{read.raw_value:X}",
                        read.decoded or f"0x{read.raw_value:X}"
                    )
                else:
                    self.after(
                        0, self._add_reg_row, f"0x{offset:02X}", reg_def.name,
                        "ERROR", "—", "Read failed"
                    )
            self.after(0, self.log, f"Register dump: {format_hex_addr(addr)}", "ok")
            self.after(0, lambda: self.tabs.set("Registers"))

        self._run_bg(work, "Register dump complete")

    def _add_reg_row(self, offset, name, hex_val, value, decoded):
        row = ctk.CTkFrame(self.reg_frame, fg_color=C["card"], corner_radius=4, height=28)
        row.pack(fill="x", pady=1)
        row.pack_propagate(False)
        ctk.CTkLabel(row, text=offset, font=F["mono_small"], text_color=C["accent"],
                     width=65, anchor="w").pack(side="left", padx=6)
        ctk.CTkLabel(row, text=name, font=F["mono_small"], width=150, anchor="w").pack(side="left")
        ctk.CTkLabel(row, text=hex_val, font=F["mono_small"], text_color=C["dim"],
                     width=180, anchor="w").pack(side="left")
        ctk.CTkLabel(row, text=value, font=F["mono_small"], width=100, anchor="w").pack(side="left")
        ctk.CTkLabel(row, text=decoded, font=F["mono_small"], anchor="w").pack(
            side="left", padx=4, fill="x", expand=True
        )

    def _copy_registers(self):
        lines = []
        for widget in self.reg_frame.winfo_children()[1:]:
            labels = widget.winfo_children()
            if len(labels) >= 5:
                parts = [labels[i].cget("text") for i in range(5)]
                lines.append(f"[{parts[0]}] {parts[1]:20s} = {parts[2]:32s} ({parts[4]})")
        self.clipboard_clear()
        self.clipboard_append("\n".join(lines))
        self.log("Registers copied", "ok")

    # ─── Batch ─────────────────────────────────────────────────────────────

    def _start_batch(self):
        if not self._check_conn():
            return
        try:
            count = int(self.batch_count_var.get())
            addrs = parse_address_list(self.batch_addr_var.get())
        except ValueError:
            self.log("Invalid batch settings", "err")
            return

        for widget in self.batch_frame.winfo_children():
            widget.destroy()
        self.batch_results.clear()
        self.batch_progress.set(0)
        self.batch_start_btn.configure(state="disabled")
        self._set_busy(True, "Batch running...")
        self.log(f"Batch: {count} × {len(addrs)} device(s)")

        def work():
            analyzer = CD3217Analyzer(self.adapter, addresses=addrs)
            total = max(count * len(addrs), 1)
            done = 0
            for i in range(count):
                for addr in addrs:
                    try:
                        result = analyzer.diagnose_device(addr)
                        self._apply_socket_expectations(analyzer, result)
                        self.batch_results.append(result)
                        faults = "; ".join(f.value for f in result.faults)
                        self.after(
                            0, self._add_batch_row, i + 1, addr, result.health.value,
                            result.health_score, result.mode or "—", faults,
                            f"{result.scan_time_ms:.0f}"
                        )
                    except Exception as e:
                        self.after(0, self.log, f"Batch error: {e}", "err")
                    # let the bus settle between devices (dead-chip NACKs)
                    time.sleep(0.08)
                    done += 1
                    self.after(0, self.batch_progress.set, done / total)
                    self.after(0, self.batch_status_var.set, f"{done}/{total}")

            total_r = len(self.batch_results)
            passed = sum(1 for r in self.batch_results if r.health == HealthStatus.PASS)
            warned = sum(1 for r in self.batch_results if r.health == HealthStatus.WARN)
            failed = sum(1 for r in self.batch_results if r.health == HealthStatus.FAIL)
            self._report_bus_health(analyzer)
            self.after(
                0, self.batch_status_var.set,
                f"Done: {total_r} | {passed} pass | {warned} warn | {failed} fail"
            )
            self.after(0, lambda: self.batch_start_btn.configure(state="normal"))

        self._run_bg(work, "Batch complete")

    def _add_batch_row(self, iteration, addr, health, score, mode, faults, time_ms):
        color = (
            C["green"] if health == "PASS" else
            C["yellow"] if health == "WARN" else C["red"]
        )
        row = ctk.CTkFrame(self.batch_frame, fg_color=C["card"], corner_radius=4, height=28)
        row.pack(fill="x", pady=1)
        row.pack_propagate(False)
        ctk.CTkLabel(row, text=str(iteration), font=F["mono_small"], width=30,
                     text_color=C["dim"]).pack(side="left", padx=6)
        ctk.CTkLabel(row, text=format_hex_addr(addr), font=F["mono_small"], width=60,
                     text_color=C["accent"]).pack(side="left")
        ctk.CTkLabel(row, text=health, font=("Segoe UI", 12, "bold"), width=50,
                     text_color=color).pack(side="left")
        ctk.CTkLabel(row, text=str(score), font=F["mono_small"], width=40).pack(side="left")
        ctk.CTkLabel(row, text=mode, font=F["mono_small"], width=50, text_color=C["dim"]).pack(
            side="left"
        )
        ctk.CTkLabel(row, text=faults, font=F["mono_small"], text_color=C["dim"],
                     anchor="w").pack(side="left", padx=6, fill="x", expand=True)
        ctk.CTkLabel(row, text=f"{time_ms}ms", font=F["mono_small"], text_color=C["dim"],
                     width=50).pack(side="left")

    # ─── Straps ────────────────────────────────────────────────────────────

    def _calc_straps(self):
        try:
            p1 = parse_hex_address(self.strap_p1_var.get())
            p2 = parse_hex_address(self.strap_p2_var.get())
        except ValueError:
            self.log("Invalid hex addresses", "err")
            return
        info = decode_i2c_address_straps(p1, p2)
        for key, value in info.items():
            if key in self.strap_result_labels:
                self.strap_result_labels[key].configure(text=str(value))
        self.log(f"Strap decode: P1={format_hex_addr(p1)} P2={format_hex_addr(p2)}")

    def _update_strap_reference(self):
        for widget in self.strap_ref_frame.winfo_children():
            widget.destroy()
        if self.current_model:
            from cd3217_analyzer.models import check_model_placement
            live = list(getattr(self, "scan_results", None) or [])
            placement = check_model_placement(self.current_model, live)
            items = []
            for p in self.current_model.positions:
                info = placement.get(p.address, {})
                verdict = info.get("verdict", "")
                color = (C["green"] if verdict == "OK" else
                         C["red"] if verdict == "MISSING" else
                         C["yellow"])
                strap = p.addr_pin or "—"
                cls = p.chip_class or p.addressing
                items.append((p.ref, format_hex_addr(p.address),
                              p.addressing.upper(), strap, cls, verdict, color))
            for pos, addr, typ, strap, cls, verdict, color in items:
                row = ctk.CTkFrame(self.strap_ref_frame, fg_color=C["card"],
                                   corner_radius=4, height=28)
                row.pack(fill="x", pady=1)
                row.pack_propagate(False)
                ctk.CTkLabel(row, text=pos, font=F["mono_small"], width=100,
                             anchor="w").pack(side="left", padx=8)
                ctk.CTkLabel(row, text=addr, font=F["mono_small"],
                             text_color=C["accent"], width=60).pack(side="left")
                ctk.CTkLabel(row, text=typ, font=F["mono_small"],
                             text_color=C["yellow"], width=52).pack(side="left")
                ctk.CTkLabel(row, text=strap, font=F["mono_small"],
                             text_color=C["dim"], width=52).pack(side="left")
                ctk.CTkLabel(row, text=cls, font=F["mono_small"],
                             text_color=C["accent"], width=44).pack(side="left")
                ctk.CTkLabel(row, text=verdict, font=F["mono_small"],
                             text_color=color, width=88).pack(side="left")
        else:
            items = [
                ("UF400", "0x38", "STRAP", "GND"),
                ("UF500", "0x3F", "STRAP", "float"),
                ("UB300", "0x20", "OTP", "—"),
                ("UB400", "0x74", "OTP", "—"),
            ]
            for pos, addr, typ, strap in items:
                row = ctk.CTkFrame(self.strap_ref_frame, fg_color=C["card"],
                                   corner_radius=4, height=28)
                row.pack(fill="x", pady=1)
                row.pack_propagate(False)
                ctk.CTkLabel(row, text=pos, font=F["mono_small"], width=100,
                             anchor="w").pack(side="left", padx=8)
                ctk.CTkLabel(row, text=addr, font=F["mono_small"],
                             text_color=C["accent"], width=60).pack(side="left")
                ctk.CTkLabel(row, text=typ, font=F["mono_small"],
                             text_color=C["yellow"], width=52).pack(side="left")
                ctk.CTkLabel(row, text=strap, font=F["mono_small"],
                             text_color=C["dim"], width=52).pack(side="left")

    def _on_model_change(self, selection: str):
        if selection == "Auto-detect":
            self.current_model = None
            self.log("Model: Auto-detect")
        else:
            model_id = selection.split("—")[0].split("-")[0].strip()
            self.current_model = get_model(model_id)
            if self.current_model:
                self.log(f"Model: {self.current_model.name}")
                addrs = [format_hex_addr(p.address) for p in self.current_model.positions]
                self.batch_addr_var.set(",".join(addrs))
                if self.current_model.positions:
                    self.quick_addr_var.set(format_hex_addr(self.current_model.positions[0].address))
        self._update_strap_reference()

    # ─── OTP ───────────────────────────────────────────────────────────────

    def _otp_scan_device(self):
        if not self._check_conn():
            return
        addr = self._parse_addr_field(self.otp_addr_var.get())
        if addr is None:
            return
        self._set_busy(True, f"OTP scan {format_hex_addr(addr)}...")
        self.otp_scan_btn.configure(state="disabled")
        self.otp_progress.set(0)

        def work():
            def progress(cur, total):
                self.after(0, self.otp_progress.set, cur / total if total else 0)

            dump = scan_otp(
                self.adapter, addr, label=format_hex_addr(addr), progress_cb=progress
            )
            self.otp_current_dump = dump
            self.after(0, self._show_otp_dump, dump)
            self.after(
                0, self.otp_status_var.set,
                f"Done: {dump.filled_count} regs, {dump.error_count} errors"
            )
            self.after(0, self.log, f"OTP scan: {dump.filled_count} regs", "ok")
            self.after(0, lambda: self.otp_scan_btn.configure(state="normal"))

        self._run_bg(work, "OTP scan complete")

    def _show_otp_dump(self, dump: OTPDump):
        self.otp_dump_text.configure(state="normal")
        self.otp_dump_text.delete("1.0", "end")
        self.otp_dump_text.insert("end", format_dump_table(dump, show_zeros=True))
        self.otp_dump_text.configure(state="disabled")
        try:
            self.tabs.set("OTP")
        except Exception:
            pass

    def _otp_import_file(self):
        filepath = filedialog.askopenfilename(
            filetypes=[("OTP dumps", "*.json *.otp.bin"), ("All", "*.*")]
        )
        if not filepath:
            return
        dump = load_dump_json(filepath) or load_dump_binary(filepath)
        if dump is None:
            self.log("Could not load file", "err")
            return
        self.otp_current_dump = dump
        self._show_otp_dump(dump)
        self.log(f"Imported: {dump.label} ({dump.filled_count} regs)", "ok")

    def _otp_socket_context(self):
        """(model, position) for the current OTP address, if a model with
        verified socket data is selected."""
        addr = self._parse_addr_field(self.otp_addr_var.get())
        if addr is None or not self.current_model:
            return None, None
        for p in self.current_model.positions:
            if p.address == addr:
                return self.current_model, p
        return self.current_model, None

    def _otp_save_golden(self):
        from cd3217_analyzer.otp_profile import save_profile
        if self.otp_current_dump is None:
            self.log("Scan OTP first — nothing to save", "warn")
            return
        model, pos = self._otp_socket_context()
        if not model:
            self.log("Select the board model first (Adapter/Board tab)", "warn")
            return
        if pos is None:
            self.log(
                f"0x{self.otp_current_dump.address:02X} is not a known socket "
                f"of {model.model_id} — cannot save a golden profile", "warn")
            return
        try:
            path = save_profile(
                self.otp_current_dump, model.model_id, pos.ref,
                silicon=pos.silicon, chip_class=pos.chip_class,
                source=model.name)
        except OSError as e:
            self.log(f"Could not save golden profile: {e}", "err")
            return
        self.log(f"Golden profile saved: {path.name} "
                 f"({pos.ref} @0x{pos.address:02X})", "ok")
        self.otp_status_var.set(f"Golden saved: {model.model_id}/{pos.ref}")

    def _otp_verify_golden(self):
        from cd3217_analyzer.otp_profile import load_profile, verify_dump
        if self.otp_current_dump is None:
            self.log("Scan OTP first — nothing to verify", "warn")
            return
        model, pos = self._otp_socket_context()
        if not model or pos is None:
            self.log("Select the board model and a known socket address", "warn")
            return
        profile = load_profile(model.model_id, pos.ref)
        if profile is None:
            self.log(
                f"No golden profile for {model.model_id}/{pos.ref} yet — "
                "scan a healthy chip and 'Save Golden'", "warn")
            return
        lines = verify_dump(self.otp_current_dump, profile)
        self.otp_diff_text.configure(state="normal")
        self.otp_diff_text.delete("1.0", "end")
        self.otp_diff_text.insert("end", "\n".join(lines))
        self.otp_diff_text.configure(state="disabled")
        verdict_ok = lines[0].split(":")[-1].strip().startswith("MATCH") \
            and "MISMATCH" not in lines[0]
        self.otp_status_var.set(
            f"Verify {model.model_id}/{pos.ref}: "
            + ("OK" if verdict_ok else "MISMATCH"))
        self.log(lines[0], "ok" if verdict_ok else "warn")

    def _otp_write_stub(self):
        from cd3217_analyzer.otp_profile import OTP_WRITE_STATUS
        messagebox.showinfo("Write OTP — not yet available", OTP_WRITE_STATUS)
        self.log(OTP_WRITE_STATUS, "warn")

    def _otp_diff_dialog(self):
        file_a = filedialog.askopenfilename(
            title="Select Dump A (vanilla)",
            filetypes=[("OTP dumps", "*.json *.otp.bin"), ("All", "*.*")],
        )
        if not file_a:
            return
        file_b = filedialog.askopenfilename(
            title="Select Dump B (OTP-ed)",
            filetypes=[("OTP dumps", "*.json *.otp.bin"), ("All", "*.*")],
        )
        if not file_b:
            return
        dump_a = load_dump_json(file_a) or load_dump_binary(file_a)
        dump_b = load_dump_json(file_b) or load_dump_binary(file_b)
        if not dump_a or not dump_b:
            self.log("Could not load dumps", "err")
            return
        result = diff_dumps(dump_a, dump_b)
        self.otp_diff_text.configure(state="normal")
        self.otp_diff_text.delete("1.0", "end")
        self.otp_diff_text.insert("end", result.summary())
        self.otp_diff_text.configure(state="disabled")
        self.log(f"Diff: {result.match_count} same, {result.diff_count} different")
        if result.diff_count > 0 and messagebox.askyesno(
            "Save", f"{result.diff_count} different registers. Save report?"
        ):
            filepath = filedialog.asksaveasfilename(defaultextension=".txt")
            if filepath:
                save_diff_report(result, filepath)
                self.log(f"Saved: {filepath}", "ok")

    # ─── Flash ─────────────────────────────────────────────────────────────

    def _flash_connect(self):
        from cd3217_analyzer.spi_bridge import BridgeSPIAdapter, BridgeSPIFlash

        if self.connected:
            if isinstance(self.adapter, UsbBridgeAdapter):
                # The board bridge does I2C and SPI on the same port — keep it.
                pass
            else:
                self.log("Disconnecting I2C before SPI (FT232H cannot do both)",
                         "warn")
                self._disconnect()
        try:
            if isinstance(self.adapter, UsbBridgeAdapter) and self.connected:
                # SPI over the already-connected board (no FT232H needed).
                self.spi_adapter = BridgeSPIAdapter(self.adapter)
                self.flash = BridgeSPIFlash(self.spi_adapter)
                self.flash_conn_status.configure(
                    text="● SPI via board", text_color=C["green"])
                self.log("SPI flash via board USB bridge", "ok")
            else:
                self.spi_adapter = SPIAdapter()
                self.spi_adapter.open()
                self.flash = SPIFlash(self.spi_adapter)
                self.flash_conn_status.configure(
                    text="● SPI connected", text_color=C["green"])
                self.log("SPI flash connected (FTDI)", "ok")
        except Exception as e:
            self.log(f"SPI connect error: {e}", "err")
            self.flash_conn_status.configure(text="● SPI error", text_color=C["red"])

    def _flash_disconnect(self):
        if self.spi_adapter:
            try:
                self.spi_adapter.close()
            except Exception:
                pass
        self.spi_adapter = None
        self.flash = None
        self.flash_info = None
        self.flash_conn_status.configure(text="● SPI disconnected", text_color=C["red"])
        self.flash_info_var.set("No chip detected")
        self.log("SPI disconnected")

    def _flash_detect(self):
        if not self._check_flash():
            return
        try:
            info = self.flash.detect()
            self.flash_info = info
            self.flash_info_var.set(
                f"{info.name} — {info.size_mb:.1f}MB ({info.sector_count} sectors) | "
                f"ID {info.jedec_id[0]:02X}{info.jedec_id[1]:02X}{info.jedec_id[2]:02X}"
            )
            self.log(f"Flash detected: {info.name}", "ok")
        except Exception as e:
            self.log(f"Flash detect error: {e}", "err")
            self.flash_info_var.set("Detection failed")

    def _flash_power_up(self):
        if not self._check_flash():
            return
        try:
            self.flash.power_up()
            self.log("Flash powered up", "ok")
        except Exception as e:
            self.log(f"Power up error: {e}", "err")

    def _flash_reset(self):
        if not self._check_flash():
            return
        try:
            self.flash.reset()
            self.log("Flash reset", "ok")
        except Exception as e:
            self.log(f"Reset error: {e}", "err")

    def _flash_read(self):
        if not self._check_flash():
            return
        filepath = filedialog.asksaveasfilename(
            defaultextension=".bin",
            filetypes=[("Binary", "*.bin"), ("All", "*.*")],
            title="Save Flash Dump",
        )
        if not filepath:
            return
        if not self.flash_info:
            self._flash_detect()
        if not self.flash_info or self.flash_info.size_bytes == 0:
            self.log("Cannot read — unknown flash size", "err")
            return

        self._set_busy(True, "Reading flash...")
        self.flash_status_var.set("Reading flash...")
        self.flash_progress.set(0)

        def work():
            def progress(cur, total):
                self.after(0, self.flash_progress.set, cur / total if total else 0)

            size = self.flash.dump_to_file(filepath, progress_cb=progress)
            self.after(0, self.flash_status_var.set, f"Read {size:,} bytes → {filepath}")
            self.after(0, self.log, f"Flash dumped: {filepath} ({size:,} bytes)", "ok")
            self.after(0, self._show_flash_hex, filepath, 256)

        self._run_bg(work, "Flash read complete")

    def _flash_write(self):
        if not self._check_flash():
            return
        filepath = filedialog.askopenfilename(
            filetypes=[("Binary", "*.bin"), ("All", "*.*")],
            title="Select firmware file to write",
        )
        if not filepath:
            return
        data = Path(filepath).read_bytes()
        if self.flash_info and len(data) > self.flash_info.size_bytes:
            self.log(f"File too large: {len(data)} bytes", "err")
            return
        if not messagebox.askyesno(
            "Confirm Write",
            f"ERASE and write {len(data):,} bytes to flash?\n\n{filepath}",
        ):
            return

        self._set_busy(True, "Writing flash...")
        self.flash_status_var.set("Writing flash...")
        self.flash_progress.set(0)

        def work():
            def progress(cur, total):
                self.after(0, self.flash_progress.set, cur / total if total else 0)

            self.after(0, self.flash_status_var.set, "Erasing chip...")
            self.flash.erase_chip()
            self.after(0, self.flash_status_var.set, "Writing data...")
            self.flash.write(0, data, progress_cb=progress)
            self.after(0, self.flash_status_var.set, "Verifying...")
            readback = self.flash.read(0, len(data))
            if readback == data:
                self.after(
                    0, self.flash_status_var.set,
                    f"Write complete — {len(data):,} bytes verified"
                )
                self.after(0, self.log, f"Flash write verified: {len(data):,} bytes", "ok")
            else:
                for i, (a, b) in enumerate(zip(data, readback)):
                    if a != b:
                        self.after(0, self.log, f"Verify mismatch at 0x{i:06X}", "err")
                        break
                self.after(0, self.flash_status_var.set, "Write complete — VERIFY FAILED")

        self._run_bg(work, "Flash write complete")

    def _flash_erase(self):
        if not self._check_flash():
            return
        if not messagebox.askyesno(
            "Confirm Erase", "ERASE entire flash chip?\n\nThis destroys all contents."
        ):
            return
        self._set_busy(True, "Erasing flash...")
        self.flash_status_var.set("Erasing chip...")

        def work():
            self.flash.erase_chip()
            self.after(0, self.flash_status_var.set, "Erase complete")
            self.after(0, self.log, "Flash erased", "ok")

        self._run_bg(work, "Flash erase complete")

    def _flash_restore(self):
        if not self._check_flash():
            return
        filepath = filedialog.askopenfilename(
            filetypes=[("Binary", "*.bin"), ("All", "*.*")],
            title="Select firmware file to restore",
        )
        if not filepath:
            return
        if not messagebox.askyesno(
            "Confirm Restore", f"Erase and restore flash from:\n{filepath}\n\nContinue?"
        ):
            return

        self._set_busy(True, "Restoring flash...")
        self.flash_status_var.set("Restoring flash...")
        self.flash_progress.set(0)

        def work():
            def progress(cur, total):
                self.after(0, self.flash_progress.set, cur / total if total else 0)
                self.after(0, self.flash_status_var.set, f"Restore {cur}/{total}")

            self.flash.full_restore(filepath, progress_cb=progress)
            self.after(0, self.flash_status_var.set, "Restore complete — verified")
            self.after(0, self.log, f"Flash restored: {filepath}", "ok")

        self._run_bg(work, "Flash restore complete")

    def _show_flash_hex(self, filepath: str, max_bytes: int = 256):
        try:
            data = Path(filepath).read_bytes()[:max_bytes]
            lines = [f"{'Addr':<8} {'Hex':<48} ASCII", f"{'-'*8} {'-'*48} {'-'*16}"]
            for i in range(0, len(data), 16):
                chunk = data[i:i + 16]
                hex_part = " ".join(f"{b:02X}" for b in chunk)
                ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
                lines.append(f"0x{i:06X}  {hex_part:<48} {ascii_part}")
            self.flash_hex_text.configure(state="normal")
            self.flash_hex_text.delete("1.0", "end")
            self.flash_hex_text.insert("end", "\n".join(lines))
            self.flash_hex_text.configure(state="disabled")
        except Exception as e:
            self.log(f"Hex preview error: {e}", "err")

    # ─── Export / log ──────────────────────────────────────────────────────

    def _save_json(self):
        if not self.devices and not self.scan_results:
            self.log("Nothing to export — scan/diagnose first", "warn")
            return
        filepath = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("All", "*.*")],
        )
        if not filepath:
            return
        report = DiagnosticReport(
            timestamp=datetime.now().isoformat(),
            adapter_type=type(self.adapter).__name__ if self.adapter else "None",
        )
        report.bus_scan_results = self.scan_results
        report.devices = list(self.devices.values())
        report.summary = f"GUI session — {len(self.devices)} device(s)"
        from cd3217_analyzer.report import bus_stats_to_dict
        bus_stats = bus_stats_to_dict(getattr(self, "last_bus_stats", None))
        try:
            save_json_report(report, filepath, bus_stats=bus_stats)
            self.log(f"Saved: {filepath}", "ok")
        except Exception as e:
            self.log(f"Save error: {e}", "err")

    # ─── Export data to GitHub (for upstream analysis) ────────────────────

    def _export_default_name(self) -> str:
        """Best name for the export: the selected MacBook model.

        The interface board (Pico/ESP32 analyzer) is NOT the device under
        test — it must never be the bundle name. Without a selected model
        we use a date-stamped generic name the user can overwrite.
        """
        if self.current_model:
            return self.current_model.model_id          # e.g. "A2251"
        mac = self.mac_picker_var.get()
        if mac:
            m = re.match(r"^(A\d{3,5})", mac)
            if m:
                return m.group(1)
        return datetime.now().strftime("Board_%Y%m%d_%H%M%S")

    def _export_data(self):
        """Open the export dialog (checklist + optional GitHub push)."""
        from cd3217_analyzer.export_data import (
            DATA_SOURCES, DATA_DEFAULT, load_token)

        dlg = ctk.CTkToplevel(self)
        dlg.title("Export board data")
        dlg.transient(self)
        dlg.grab_set()
        try:
            dlg.attributes("-topmost", True)
        except Exception:
            pass
        dlg.geometry("460x560")
        dlg.configure(fg_color=C["bg"])

        ctk.CTkLabel(
            dlg, text="Export board data — for upstream analysis",
            font=F["heading"], text_color=C["accent"]
        ).pack(anchor="w", padx=18, pady=(16, 4))
        ctk.CTkLabel(
            dlg, text="Collect the board's data into a JSON bundle and "
                      "optionally push it to the project's GitHub repo.",
            font=F["small"], text_color=C["dim"], justify="left",
            wraplength=420,
        ).pack(anchor="w", padx=18, pady=(0, 8))

        # name
        namebox = ctk.CTkFrame(dlg, fg_color="transparent")
        namebox.pack(fill="x", padx=18, pady=(8, 2))
        ctk.CTkLabel(namebox, text="Name (MacBook/board model):",
                     font=F["body"], text_color=C["dim"]).pack(side="left")
        self.export_name_var = ctk.StringVar(value=self._export_default_name())
        ctk.CTkEntry(
            namebox, textvariable=self.export_name_var, width=200,
            fg_color=C["entry"], text_color=C["text"],
        ).pack(side="right")

        # checklist
        ctk.CTkLabel(dlg, text="Include:", font=F["body"],
                     text_color=C["dim"]).pack(anchor="w", padx=18, pady=(8, 2))
        self.export_checks: dict = {}
        for key, desc in DATA_SOURCES:
            var = ctk.BooleanVar(value=(key in DATA_DEFAULT) and
                                 self._export_source_available(key))
            self.export_checks[key] = var
            row = ctk.CTkFrame(dlg, fg_color="transparent")
            row.pack(fill="x", padx=24, pady=1)
            ctk.CTkCheckBox(
                row, text="", variable=var, width=22,
                fg_color=C["accent"], hover_color=C["accent_dim"],
            ).pack(side="left")
            ctk.CTkLabel(row, text=desc, font=F["body"],
                         text_color=C["text"], justify="left",
                         wraplength=360).pack(side="left", padx=(4, 0))

        # GitHub push
        ctk.CTkLabel(dlg, text="GitHub:", font=F["body"],
                     text_color=C["accent"]).pack(anchor="w", padx=18, pady=(12, 2))
        self.export_push_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            dlg, text="Push to GitHub samples/ folder (samples/NAME.json)",
            variable=self.export_push_var, fg_color=C["accent"],
            hover_color=C["accent_dim"], text_color=C["text"],
        ).pack(anchor="w", padx=24, pady=2)
        tokbox = ctk.CTkFrame(dlg, fg_color="transparent")
        tokbox.pack(fill="x", padx=18, pady=(6, 2))
        ctk.CTkLabel(tokbox, text="Token:", font=F["body"],
                     text_color=C["dim"]).pack(side="left")
        self.export_token_var = ctk.StringVar(value=load_token() or "")
        ctk.CTkEntry(
            tokbox, textvariable=self.export_token_var, show="*", width=260,
            fg_color=C["entry"], text_color=C["text"],
        ).pack(side="right")
        ctk.CTkLabel(
            dlg, text="Token needs contents:write scope on the repo. It is "
                      "stored locally (owner-only).",
            font=F["small"], text_color=C["dim"], justify="left",
            wraplength=420,
        ).pack(anchor="w", padx=18, pady=(0, 2))

        # actions
        act = ctk.CTkFrame(dlg, fg_color="transparent")
        act.pack(fill="x", padx=18, pady=(14, 16))
        self.export_progress = ctk.CTkLabel(
            act, text="", font=F["small"], text_color=C["dim"])
        self.export_progress.pack(anchor="w", pady=(0, 6))
        ctk.CTkButton(
            act, text="Export", width=110, height=32, fg_color=C["green"],
            hover_color="#16a34a", text_color="#04120a",
            command=lambda: self._run_export(dlg),
        ).pack(side="right", padx=(6, 0))
        ctk.CTkButton(
            act, text="Cancel", width=90, height=32, fg_color=C["btn"],
            hover_color=C["btn_hover"], text_color=C["text"],
            command=dlg.destroy,
        ).pack(side="right")

    def _export_source_available(self, key: str) -> bool:
        """Whether a source can actually be collected right now."""
        if key in ("info", "registers", "otp", "report"):
            return self.adapter is not None and self.connected
        if key == "flash":
            return self.flash is not None
        if key == "uart":
            try:
                return bool(self.uart_output.get("1.0", "end").strip())
            except Exception:
                return False
        return True

    def _run_export(self, dlg):
        """Collect the bundle, write locally, optionally push to GitHub."""
        def ui(fn):
            try:
                self.after(0, fn)
            except Exception:
                pass

        name = self.export_name_var.get().strip()
        if not name:
            self._ui(lambda: self.export_progress.configure(
                text="Please enter a name."))
            return
        selected = [k for k, v in self.export_checks.items() if v.get()]
        if not selected:
            self._ui(lambda: self.export_progress.configure(
                text="Select at least one data source."))
            return

        token = self.export_token_var.get().strip()
        push = self.export_push_var.get()
        # Read widget/var state on the UI thread (Text.get / StringVar.get
        # are not thread-safe from a worker).
        uart_text = None
        if "uart" in selected:
            try:
                uart_text = self.uart_output.get("1.0", "end").strip()
            except Exception:
                uart_text = None
        mac_model = (self.current_model.model_id if self.current_model
                     else (self.mac_picker_var.get() or None))
        from cd3217_analyzer.export_data import (
            GitHubPushError, collect_bundle, store_token, write_bundle)

        def work():
            def prog(msg):
                ui(lambda: self.export_progress.configure(text=msg))
            try:
                selected_lower = selected
                prog("Collecting data...")
                bundle = collect_bundle(
                    self.adapter, selected_lower, name,
                    scan_results=list(self.scan_results or []),
                    devices=self.devices,
                    uart_text=uart_text,
                    flash=self.flash,
                    mac_model=mac_model,
                    progress_cb=lambda m: prog(m))
                local_path = write_bundle(bundle, name)
                if push and token:
                    store_token(token)
                    prog("Pushing to GitHub...")
                    from cd3217_analyzer.export_data import push_bundle
                    url = push_bundle(bundle, name, token=token,
                                      progress_cb=lambda m: prog(m))
                    ui(lambda: self.log(
                        f"Exported {name} — local: {local_path}; pushed: "
                        f"{url}", "ok"))
                    ui(lambda: dlg.destroy())
                else:
                    ui(lambda: self.log(
                        f"Exported {name} — saved to {local_path}", "ok"))
                    ui(lambda: dlg.destroy())
            except GitHubPushError as e:
                ui(lambda: self.export_progress.configure(
                    text=f"Push failed: {e}"))
                self.log(f"GitHub push failed: {e}", "err")
            except Exception as e:
                self.log(f"Export error: {e}", "err")
                ui(lambda: self.export_progress.configure(text=str(e)))

        t = threading.Thread(target=work, daemon=True)
        t.start()
    def _save_csv(self):
        if not self.batch_results:
            self.log("No batch data", "warn")
            return
        filepath = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("All", "*.*")],
        )
        if filepath:
            save_csv_log(self.batch_results, filepath, append=False)
            self.log(f"CSV saved: {filepath}", "ok")

    def _clear_log(self):
        self.log_text.delete("1.0", "end")

    def _copy_log(self):
        self.clipboard_clear()
        self.clipboard_append(self.log_text.get("1.0", "end"))
        self.log("Log copied", "ok")


def main():
    # High-DPI: mark the process DPI-aware so Windows doesn't bitmap-scale the
    # UI (crisp text on 125%/150% displays). Must run before the Tk window is
    # created; CustomTkinter then picks up the system scaling factor itself.
    if sys.platform.startswith("win"):
        try:
            from ctypes import windll
            try:
                windll.user32.SetProcessDpiAwarenessContext(-4)  # per-monitor v2
            except Exception:
                windll.shcore.SetProcessDpiAwareness(1)           # system aware
        except Exception:
            pass
    app = Application()
    app.mainloop()


if __name__ == "__main__":
    main()
