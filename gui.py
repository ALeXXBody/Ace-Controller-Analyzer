"""CD3217B12 (Apple ACE2) I2C Diagnostic Analyzer - Modern GUI."""

from __future__ import annotations

import os
import sys
import threading
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
        self.adapter_var = ctk.StringVar(value="Auto-detect")
        self.adapter_menu = ctk.CTkOptionMenu(
            controls,
            variable=self.adapter_var,
            values=["Auto-detect", "FTDI FT232H", "SMBus (Linux)", "CH341", "USB Bridge (board)"],
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
        self.btn_refresh.pack(side="left")

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

        self.tab_board = self.tabs.add("Board")
        self.tab_overview = self.tabs.add("Overview")
        self.tab_registers = self.tabs.add("Registers")
        self.tab_batch = self.tabs.add("Batch")
        self.tab_straps = self.tabs.add("Straps")
        self.tab_otp = self.tabs.add("OTP")
        self.tab_flash = self.tabs.add("Flash")
        self.tab_log = self.tabs.add("Log")

        self._build_board_tab()
        self._build_overview_tab()
        self._build_register_tab()
        self._build_batch_tab()
        self._build_strap_tab()
        self._build_otp_tab()
        self._build_flash_tab()
        self._build_log_tab()

    # ─── Board tab ────────────────────────────────────────────────────────

    def _build_board_tab(self):
        tab = self.tab_board
        from cd3217_analyzer.boards import BOARDS, get_board_info

        # ── connected board card ────────────────────────────────────────────
        card = ctk.CTkFrame(tab, fg_color=C["card"], corner_radius=12)
        card.pack(fill="x", padx=12, pady=12)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=18, pady=16)

        self.board_status_dot = ctk.CTkLabel(
            inner, text="●", font=F["score"], text_color=C["dim"], width=30)
        self.board_status_dot.pack(side="left", padx=(0, 14))

        vbox = ctk.CTkFrame(inner, fg_color="transparent")
        vbox.pack(side="left", fill="x", expand=True)
        self.board_name_label = ctk.CTkLabel(
            vbox, text="No board connected", font=F["heading"])
        self.board_name_label.pack(anchor="w")
        self.board_sub_label = ctk.CTkLabel(
            vbox, text="Connect via USB Bridge (board) to see its pinout, "
                       "or pick a board below.",
            text_color=C["dim"], font=F["body"], justify="left", wraplength=640)
        self.board_sub_label.pack(anchor="w", pady=(2, 0))

        # ── pinout cards ───────────────────────────────────────────────────
        grid = ctk.CTkFrame(tab, fg_color="transparent")
        grid.pack(fill="x", padx=12, pady=(0, 6))
        grid.grid_columnconfigure((0, 1), weight=1)

        self.i2c_card = ctk.CTkFrame(grid, fg_color=C["card"], corner_radius=12)
        self.i2c_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.spi_card = ctk.CTkFrame(grid, fg_color=C["card"], corner_radius=12)
        self.spi_card.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        self._build_pin_card(
            self.i2c_card, "I2C — connect to the CD3217",
            [("SDA (data)", "sda"), ("SCL (clock)", "scl")])
        self._build_pin_card(
            self.spi_card, "SPI — flash chip (via level shifter)",
            [("SCK (clock)", "sck"), ("MISO (board reads)", "miso"),
             ("MOSI (board writes)", "mosi"), ("CS (chip select)", "cs")])

        # ── wiring notes ───────────────────────────────────────────────────
        notes_card = ctk.CTkFrame(tab, fg_color=C["card"], corner_radius=12)
        notes_card.pack(fill="x", padx=12, pady=6)
        self.board_notes_label = ctk.CTkLabel(
            notes_card, text="", font=F["small"], text_color=C["dim"],
            justify="left", wraplength=700)
        self.board_notes_label.pack(anchor="w", padx=18, pady=12)
        self._board_notes_frame = notes_card

        # ── board picker (works without a board connected) ─────────────────
        picker_card = ctk.CTkFrame(tab, fg_color=C["card"], corner_radius=12)
        picker_card.pack(fill="x", padx=12, pady=6)
        box = ctk.CTkFrame(picker_card, fg_color="transparent")
        box.pack(fill="x", padx=18, pady=12)
        ctk.CTkLabel(box, text="Browse a board's pinout:",
                     font=F["body"], text_color=C["dim"]).pack(side="left")
        self.board_picker_var = ctk.StringVar(value="")
        picker = ctk.CTkOptionMenu(
            box, variable=self.board_picker_var, values=[""] + sorted(
                b.name for b in BOARDS.values()),
            command=self._on_board_picked, width=260,
            fg_color=C["entry"], button_color=C["btn"],
            button_hover_color=C["btn_hover"], text_color=C["text"],
            dropdown_fg_color=C["panel"])
        picker.pack(side="left", padx=12)

        # initialize with the empty state
        self._show_board_info(None)

    def _build_pin_card(self, parent, title, roles):
        ctk.CTkLabel(
            parent, text=title, font=F["heading"],
            text_color=C["accent"]).pack(anchor="w", padx=18, pady=(14, 4))
        for _, key in roles:
            row = ctk.CTkFrame(parent, fg_color="transparent")
            row.pack(fill="x", padx=18, pady=3)
            ctk.CTkLabel(row, text=_PIN_LABELS[key], font=F["body"],
                         text_color=C["dim"], width=150, anchor="w"
                         ).pack(side="left")
            lbl = ctk.CTkLabel(row, text="—", font=F["mono"],
                               text_color=C["text"])
            lbl.pack(side="left")
            setattr(self, f"_pin_lbl_{key}", lbl)

    def _on_board_picked(self, name):
        from cd3217_analyzer.boards import BOARDS
        for b in BOARDS.values():
            if b.name == name:
                self._show_board_info(b)
                return
        self._show_board_info(None)

    def _show_board_info(self, board):
        """Render a BoardInfo (or None) into the Board tab."""
        from cd3217_analyzer.boards import BoardInfo
        if board is None:
            self.board_status_dot.configure(text_color=C["dim"])
            self.board_name_label.configure(text="No board selected")
            self.board_sub_label.configure(
                text="Connect via USB Bridge (board) to see the live pinout, "
                     "or pick a board above.")
            for key in _PIN_LABELS:
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
            f"{board.family} family  ·  {board.i2c_label} on dedicated pins  "
            f"·  {board.spi_label} on "
            f"{'SPI1' if board.hw == 1 else 'its SPI peripheral'}"))
        for role, key in (("sda", "sda"), ("scl", "scl")):
            v = board.i2c.get(role)
            getattr(self, f"_pin_lbl_{key}").configure(
                text=v[1] if v else "—")
        for key in ("sck", "miso", "mosi", "cs"):
            v = board.spi.get(key)
            getattr(self, f"_pin_lbl_{key}").configure(
                text=v[1] if v else "—")

    def _refresh_board_tab_live(self):
        """Update the Board tab from the connected board's INFO frame."""
        from cd3217_analyzer.boards import board_from_info
        try:
            info = self.adapter.info()
        except Exception:
            info = {}
        board = board_from_info(info)
        if board:
            self.board_status_dot.configure(text_color=C["green"])
            self._show_board_info(board)
            live = info.get("spi_sck") is not None
            self.board_sub_label.configure(text=(
                f"Connected  ·  pins reported "
                f"{'live by firmware' if live else 'from the board table'}"))
        else:
            self.board_status_dot.configure(text_color=C["red"])
            self.board_name_label.configure(text="Board did not report pins")
            self.board_sub_label.configure(
                text="Connected, but the firmware did not answer INFO — "
                     "re-flash with the latest firmware for pin reporting.")

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
                hdr, text=text, font=("Segoe UI", 10, "bold"), text_color=C["accent"],
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
        addrs = list(KNOWN_ACE2_ADDRESSES.keys())
        if self.current_model:
            addrs.extend(p.address for p in self.current_model.positions)
        return unique_sorted(addrs)

    # ─── Connection ────────────────────────────────────────────────────────

    def _auto_detect(self):
        self.log("Scanning for I2C adapters...")
        try:
            adapter = detect_adapter()
        except Exception as e:
            self.log(f"Auto-detect error: {e}", "err")
            return
        if adapter:
            self.adapter = adapter
            self.connected = True
            self._update_conn_status(True)
            self.log(f"Auto-detected: {type(adapter).__name__}", "ok")
        else:
            self.log("No adapter found. Select adapter and click Connect.", "warn")

    def _toggle_connection(self):
        if self.connected:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        if self.spi_adapter:
            self._flash_disconnect()
        selection = self.adapter_var.get()
        self.log(f"Connecting to {selection}...")
        try:
            if selection == "Auto-detect":
                adapter = detect_adapter()
            elif selection == "FTDI FT232H":
                adapter = FTDIAdapter()
                adapter.open()
            elif selection in ("SMBus (Linux)", "CH341"):
                adapter = SMBusAdapter(bus_number=int(self.bus_var.get() or "1"))
                adapter.open()
            elif selection == "USB Bridge (board)":
                port = normalize_port(self.bus_var.get())
                if not port:
                    ports = list_bridge_ports()
                    if not ports:
                        self.log("No USB serial port found. Plug in the board "
                                 "and enter the COM port (e.g. COM5) in Bus/Port.", "warn")
                        return
                    port = ports[0]
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
                    return
                # Log which firmware the board is actually running so a wrong
                # flash (e.g. pico2 firmware on a Pico 1) is obvious, and
                # populate the Board tab with its live pinout.
                try:
                    b = adapter.info()
                    if b and b.get("board"):
                        self.log(f"Board firmware: {b['board']} "
                                 f"(SDA={b.get('sda')} SCL={b.get('scl')})",
                                 "ok")
                except Exception:
                    pass
                try:
                    self._refresh_board_tab_live()
                except Exception:
                    pass
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
        except Exception as e:
            self.log(f"Connection failed: {e}", "err")

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
                    port = self.bus_var.get().strip() if self.connected else None
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
            devices = self.adapter.scan(0x08, 0x77)
            self.scan_results = devices
            self.after(0, self._show_devices, devices)

        self._run_bg(work, "Scan complete")

    def _quick_scan(self):
        if not self._check_conn():
            return
        addrs = self._known_scan_addresses()
        self._set_busy(True, "Quick scan...")
        self.log(f"Quick scanning {len(addrs)} known addresses...")

        def work():
            found = [a for a in addrs if self.adapter.ping(a)]
            self.scan_results = found
            self.after(0, self._show_devices, found)

        self._run_bg(work, "Quick scan complete")

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
            found = [a for a in addrs if self.adapter.ping(a)]
            self.scan_results = found
            self.after(0, self._show_devices, found)

        self._run_bg(work, "Model scan complete")

    def _show_devices(self, devices: List[int]):
        self._clear_devices()
        self.device_count_var.set(f"{len(devices)} found")
        if not devices:
            self.log("No devices found", "warn")
            return
        self.log(f"Found {len(devices)} device(s)", "ok")
        for addr in devices:
            desc = self._device_label(addr)
            self._add_device_row(addr, "?", "—", desc, is_ace2_address(addr))

    def _device_label(self, addr: int) -> str:
        if self.current_model:
            for p in self.current_model.positions:
                if p.address == addr:
                    return f"{p.ref} · {p.addressing}"
        return KNOWN_ACE2_ADDRESSES.get(addr, "Unknown")[:28]

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
            C["red"] if health == "FAIL" else C["dim"]
        )
        ctk.CTkLabel(
            row, text=str(health), font=("Segoe UI", 11, "bold"),
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
        if not self._check_conn() or not self.scan_results:
            if not self.scan_results:
                self.log("Scan first", "warn")
            return
        self._set_busy(True, "Diagnosing all...")
        addrs = list(self.scan_results)

        def work():
            analyzer = CD3217Analyzer(self.adapter)
            for addr in addrs:
                try:
                    self.devices[addr] = analyzer.diagnose_device(addr)
                except Exception as e:
                    self.after(0, self.log, f"Error {format_hex_addr(addr)}: {e}", "err")
            self.after(0, self._refresh_display)

        self._run_bg(work, "Diagnose all complete")

    def _refresh_display(self):
        self._clear_devices()
        for addr, dev in sorted(self.devices.items()):
            self._add_device_row(
                addr, dev.health.value, dev.health_score,
                self._device_label(addr), is_ace2_address(addr)
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
            result = CD3217Analyzer(self.adapter).diagnose_device(address)
            self.devices[address] = result
            self.after(0, self._show_result, result)
            self.after(0, self._refresh_display)

        self._run_bg(work, f"{format_hex_addr(address)} done")

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
        self.info_labels["time"].configure(text=f"{result.scan_time_ms:.1f} ms")

        otp = {0x3A, 0x3B, 0x3C, 0x74, 0x76, 0x78, 0x79}
        van = {0x38, 0x3F, 0x2F, 0x28}
        if result.address in otp:
            chip = "OTP-ed (Apple address)"
        elif result.address in van:
            chip = "Likely vanilla"
        else:
            chip = "Unknown type"
        if self.current_model:
            for p in self.current_model.positions:
                if p.address == result.address:
                    chip = f"{p.ref} · {p.addressing.upper()}"
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
                        self.batch_results.append(result)
                        faults = "; ".join(f.value for f in result.faults)
                        self.after(
                            0, self._add_batch_row, i + 1, addr, result.health.value,
                            result.health_score, result.mode or "—", faults,
                            f"{result.scan_time_ms:.0f}"
                        )
                    except Exception as e:
                        self.after(0, self.log, f"Batch error: {e}", "err")
                    done += 1
                    self.after(0, self.batch_progress.set, done / total)
                    self.after(0, self.batch_status_var.set, f"{done}/{total}")

            total_r = len(self.batch_results)
            passed = sum(1 for r in self.batch_results if r.health == HealthStatus.PASS)
            warned = sum(1 for r in self.batch_results if r.health == HealthStatus.WARN)
            failed = sum(1 for r in self.batch_results if r.health == HealthStatus.FAIL)
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
        ctk.CTkLabel(row, text=health, font=("Segoe UI", 10, "bold"), width=50,
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
            items = [
                (p.ref, format_hex_addr(p.address), p.addressing.upper(), f"Port {p.i2c_port}")
                for p in self.current_model.positions
            ]
        else:
            items = [
                ("UF400", "0x38", "STRAP", "P1"),
                ("UF500", "0x3F", "STRAP", "P1"),
                ("UB300", "0x20", "OTP", "P1"),
                ("UB400", "0x74", "OTP", "P1"),
            ]
        for pos, addr, typ, port in items:
            row = ctk.CTkFrame(self.strap_ref_frame, fg_color=C["card"], corner_radius=4, height=28)
            row.pack(fill="x", pady=1)
            row.pack_propagate(False)
            ctk.CTkLabel(row, text=pos, font=F["mono_small"], width=120, anchor="w").pack(
                side="left", padx=8
            )
            ctk.CTkLabel(row, text=addr, font=F["mono_small"], text_color=C["accent"],
                         width=70).pack(side="left")
            ctk.CTkLabel(row, text=typ, font=F["mono_small"], text_color=C["yellow"],
                         width=70).pack(side="left")
            ctk.CTkLabel(row, text=port, font=F["mono_small"], text_color=C["dim"],
                         width=60).pack(side="left")

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
        try:
            save_json_report(report, filepath)
            self.log(f"Saved: {filepath}", "ok")
        except Exception as e:
            self.log(f"Save error: {e}", "err")

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
