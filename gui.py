"""CD3217B12 (Apple ACE2) I2C Diagnostic Analyzer - Modern GUI.

CustomTkinter-based GUI with dark theme, modern widgets, and .exe support.
"""

import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from datetime import datetime
from typing import Dict, List, Optional

import customtkinter as ctk

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cd3217_analyzer.registers import (
    KNOWN_ACE2_ADDRESSES, REGISTERS, decode_i2c_address_straps, is_ace2_address,
)
from cd3217_analyzer.models import get_model, list_models
from cd3217_analyzer.analyzer import (
    CD3217Analyzer, DeviceResult, DiagnosticReport, HealthStatus,
)
from cd3217_analyzer.adapters import FTDIAdapter, SMBusAdapter, detect_adapter
from cd3217_analyzer.report import save_json_report, save_csv_log
from cd3217_analyzer.otp import (
    OTPDump, diff_dumps, format_dump_table, load_dump_binary, load_dump_json,
    save_diff_report, scan_otp,
)
from cd3217_analyzer.spi_adapter import SPIAdapter
from cd3217_analyzer.flash import SPIFlash, FlashInfo, FlashError


# ─── Theme Colors ─────────────────────────────────────────────────────────────
C = {
    "bg":      "#1a1a2e",
    "panel":   "#16213e",
    "card":    "#0f3460",
    "accent":  "#00b4d8",
    "red":     "#e94560",
    "green":   "#06d6a0",
    "yellow":  "#ffd166",
    "orange":  "#f4845f",
    "text":    "#e0e0e0",
    "dim":     "#8892a0",
    "bright":  "#ffffff",
    "entry":   "#0d1b2a",
    "btn":     "#533483",
}


class Application(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("CD3217B12 (Apple ACE2) I2C Diagnostic Analyzer")
        self.geometry("1280x860")
        self.minsize(960, 700)
        self.configure(fg_color=C["bg"])

        self.adapter = None
        self.connected = False
        self.scan_results: List[int] = []
        self.devices: Dict[int, DeviceResult] = {}
        self.selected_address = None
        self.current_model = None
        self.batch_results: List[DeviceResult] = []
        self.otp_current_dump: Optional[OTPDump] = None
        self.spi_adapter: Optional[SPIAdapter] = None
        self.flash: Optional[SPIFlash] = None
        self.flash_info: Optional[FlashInfo] = None

        self._build_ui()
        self.after(500, self._auto_detect)

    # ─── UI Build ──────────────────────────────────────────────────────────

    def _build_ui(self):
        # Top bar
        top = ctk.CTkFrame(self, fg_color=C["panel"], corner_radius=8)
        top.pack(fill="x", padx=10, pady=(10, 5))

        ctk.CTkLabel(top, text="CD3217B12 Analyzer", font=("Segoe UI", 20, "bold"),
                     text_color=C["accent"]).pack(side="left", padx=12, pady=8)

        # Model selector
        ctk.CTkLabel(top, text="Model:", text_color=C["dim"]).pack(side="left", padx=(20, 4))
        self.model_var = ctk.StringVar(value="Auto-detect")
        model_names = ["Auto-detect"] + [f"{m.model_id} - {m.name}" for m in list_models()]
        self.model_menu = ctk.CTkOptionMenu(top, variable=self.model_var, values=model_names,
                                             width=320, command=self._on_model_change)
        self.model_menu.pack(side="left", padx=4)

        # Adapter selector
        ctk.CTkLabel(top, text="Adapter:", text_color=C["dim"]).pack(side="left", padx=(20, 4))
        self.adapter_var = ctk.StringVar(value="Auto-detect")
        self.adapter_menu = ctk.CTkOptionMenu(
            top, variable=self.adapter_var,
            values=["Auto-detect", "FTDI FT232H", "SMBus (Linux)", "CH341"],
            width=160)
        self.adapter_menu.pack(side="left", padx=4)

        ctk.CTkLabel(top, text="Bus:", text_color=C["dim"]).pack(side="left", padx=(12, 4))
        self.bus_var = ctk.StringVar(value="1")
        ctk.CTkEntry(top, textvariable=self.bus_var, width=40).pack(side="left")

        self.connect_btn = ctk.CTkButton(top, text="Connect", width=100,
                                          fg_color=C["red"], hover_color="#ff5a7a",
                                          command=self._toggle_connection)
        self.connect_btn.pack(side="left", padx=12)

        self.conn_status = ctk.CTkLabel(top, text="Disconnected", text_color=C["red"])
        self.conn_status.pack(side="left", padx=4)

        # Main paned area
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=10, pady=5)
        body.grid_columnconfigure(1, weight=3)
        body.grid_rowconfigure(0, weight=1)

        # Left panel — device list
        left = ctk.CTkFrame(body, fg_color=C["panel"], corner_radius=8, width=380)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        left.grid_propagate(False)
        self._build_device_panel(left)

        # Right panel — tabs
        right = ctk.CTkFrame(body, fg_color=C["panel"], corner_radius=8)
        right.grid(row=0, column=1, sticky="nsew")
        self._build_tabs(right)

        # Status bar
        status = ctk.CTkFrame(self, fg_color=C["panel"], corner_radius=6, height=30)
        status.pack(fill="x", padx=10, pady=(0, 8))
        self.status_left = ctk.CTkLabel(status, text="Ready", text_color=C["dim"],
                                         font=("Segoe UI", 10))
        self.status_left.pack(side="left", padx=10, pady=4)
        ctk.CTkLabel(status, text="v2.0.0", text_color=C["dim"],
                     font=("Segoe UI", 10)).pack(side="right", padx=10, pady=4)

    def _build_device_panel(self, parent):
        # Scan buttons row
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=(10, 5))

        ctk.CTkButton(row, text="Scan Bus", width=80, height=30, fg_color=C["green"],
                      text_color="#000", hover_color="#04b888",
                      command=self._scan_bus).pack(side="left", padx=(0, 4))
        ctk.CTkButton(row, text="Quick", width=55, height=30, fg_color=C["btn"],
                      command=self._quick_scan).pack(side="left", padx=(0, 4))
        ctk.CTkButton(row, text="Refresh", width=60, height=30, fg_color=C["btn"],
                      command=self._refresh_devices).pack(side="left")

        self.device_count_var = ctk.StringVar(value="0 devices")
        ctk.CTkLabel(row, textvariable=self.device_count_var, text_color=C["dim"],
                     font=("Segoe UI", 10)).pack(side="right")

        # Device list — use CTkScrollableFrame with card rows
        self.device_frame = ctk.CTkScrollableFrame(parent, fg_color=C["entry"],
                                                    corner_radius=6)
        self.device_frame.pack(fill="both", expand=True, padx=10, pady=(0, 5))

        self.device_rows: Dict[int, ctk.CTkFrame] = {}

        # Action buttons
        act = ctk.CTkFrame(parent, fg_color="transparent")
        act.pack(fill="x", padx=10, pady=(0, 5))
        ctk.CTkButton(act, text="Diagnose", width=80, height=28, fg_color=C["btn"],
                      command=self._diagnose_selected).pack(side="left", padx=(0, 4))
        ctk.CTkButton(act, text="Dump", width=55, height=28, fg_color=C["btn"],
                      command=self._dump_selected).pack(side="left", padx=(0, 4))
        ctk.CTkButton(act, text="Batch", width=55, height=28, fg_color=C["btn"],
                      command=self._switch_to_batch).pack(side="left")

        # Quick address entry
        qa = ctk.CTkFrame(parent, fg_color="transparent")
        qa.pack(fill="x", padx=10, pady=(0, 10))
        ctk.CTkLabel(qa, text="Address:", text_color=C["dim"]).pack(side="left")
        self.quick_addr_var = ctk.StringVar(value="0x38")
        ctk.CTkEntry(qa, textvariable=self.quick_addr_var, width=70).pack(side="left", padx=6)
        ctk.CTkButton(qa, text="Diagnose", width=70, height=26, fg_color=C["btn"],
                      command=self._diagnose_quick).pack(side="left")

    def _build_tabs(self, parent):
        self.tabs = ctk.CTkTabview(parent, fg_color=C["bg"],
                                   segmented_button_fg_color=C["panel"],
                                   segmented_button_selected_color=C["accent"],
                                   segmented_button_selected_hover_color="#0096b7")
        self.tabs.pack(fill="both", expand=True, padx=10, pady=10)

        self.tab_overview = self.tabs.add("  Overview  ")
        self.tab_registers = self.tabs.add("  Registers  ")
        self.tab_batch = self.tabs.add("  Batch  ")
        self.tab_straps = self.tabs.add("  Straps  ")
        self.tab_log = self.tabs.add("  Log  ")
        self.tab_otp = self.tabs.add("  OTP  ")
        self.tab_flash = self.tabs.add("  Flash  ")

        self._build_overview_tab()
        self._build_register_tab()
        self._build_batch_tab()
        self._build_strap_tab()
        self._build_log_tab()
        self._build_otp_tab()
        self._build_flash_tab()

    # ─── Overview Tab ──────────────────────────────────────────────────────

    def _build_overview_tab(self):
        tab = self.tab_overview

        # Health score card
        card = ctk.CTkFrame(tab, fg_color=C["card"], corner_radius=10)
        card.pack(fill="x", padx=10, pady=10)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=15)

        self.health_label = ctk.CTkLabel(inner, text="--", font=("Segoe UI", 40, "bold"),
                                          text_color=C["dim"], width=80)
        self.health_label.pack(side="left", padx=(0, 20))

        info = ctk.CTkFrame(inner, fg_color="transparent")
        info.pack(side="left", fill="x", expand=True)
        self.health_status = ctk.CTkLabel(info, text="No device selected",
                                           font=("Segoe UI", 14, "bold"))
        self.health_status.pack(anchor="w")
        self.health_detail = ctk.CTkLabel(info, text="Select a device and run diagnosis",
                                           text_color=C["dim"])
        self.health_detail.pack(anchor="w")

        # Device info grid
        info_frame = ctk.CTkFrame(tab, fg_color=C["card"], corner_radius=10)
        info_frame.pack(fill="x", padx=10, pady=(0, 10))

        self.info_labels = {}
        fields = [("Address", "address"), ("Vendor ID", "vid"), ("Device ID", "did"),
                  ("Mode", "mode"), ("Type", "type"), ("Response", "time"), ("Chip Type", "chip_type")]
        for i, (label, key) in enumerate(fields):
            ctk.CTkLabel(info_frame, text=f"{label}:", text_color=C["dim"],
                         font=("Segoe UI", 11)).grid(row=i, column=0, sticky="w", padx=12, pady=3)
            lbl = ctk.CTkLabel(info_frame, text="--", font=("Consolas", 11))
            lbl.grid(row=i, column=1, sticky="w", padx=12, pady=3)
            self.info_labels[key] = lbl
        info_frame.columnconfigure(1, weight=1)

        # Faults
        faults_frame = ctk.CTkFrame(tab, fg_color=C["card"], corner_radius=10)
        faults_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        ctk.CTkLabel(faults_frame, text="Faults & Diagnostics", font=("Segoe UI", 12, "bold"),
                     text_color=C["accent"]).pack(anchor="w", padx=12, pady=(8, 4))

        self.faults_text = ctk.CTkTextbox(faults_frame, fg_color=C["entry"],
                                           font=("Consolas", 11), state="disabled")
        self.faults_text.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    # ─── Registers Tab ─────────────────────────────────────────────────────

    def _build_register_tab(self):
        tab = self.tab_registers

        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=10)

        ctk.CTkButton(row, text="Read All", width=80, fg_color=C["green"],
                      text_color="#000", command=self._read_registers).pack(side="left")
        ctk.CTkButton(row, text="Copy", width=55, fg_color=C["btn"],
                      command=self._copy_registers).pack(side="left", padx=6)
        ctk.CTkLabel(row, text="Device:", text_color=C["dim"]).pack(side="left", padx=(12, 4))
        self.reg_addr_var = ctk.StringVar(value="0x38")
        ctk.CTkEntry(row, textvariable=self.reg_addr_var, width=70).pack(side="left")

        # Register list — scrollable frame with rows
        self.reg_frame = ctk.CTkScrollableFrame(tab, fg_color=C["entry"], corner_radius=6)
        self.reg_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # Header row
        hdr = ctk.CTkFrame(self.reg_frame, fg_color=C["panel"], corner_radius=4)
        hdr.pack(fill="x", pady=(0, 2))
        for text, w in [("Offset", 70), ("Name", 150), ("Raw (hex)", 180),
                        ("Value", 100), ("Decoded", 200)]:
            ctk.CTkLabel(hdr, text=text, font=("Segoe UI", 10, "bold"),
                         text_color=C["accent"], width=w, anchor="w").pack(side="left", padx=4)

    # ─── Batch Tab ─────────────────────────────────────────────────────────

    def _build_batch_tab(self):
        tab = self.tab_batch

        ctrl = ctk.CTkFrame(tab, fg_color="transparent")
        ctrl.pack(fill="x", padx=10, pady=10)

        ctk.CTkLabel(ctrl, text="Iterations:", text_color=C["dim"]).pack(side="left")
        self.batch_count_var = ctk.StringVar(value="5")
        ctk.CTkEntry(ctrl, textvariable=self.batch_count_var, width=50).pack(side="left", padx=6)

        ctk.CTkLabel(ctrl, text="Devices:", text_color=C["dim"]).pack(side="left", padx=(12, 0))
        self.batch_addr_var = ctk.StringVar(value="0x38,0x3F")
        ctk.CTkEntry(ctrl, textvariable=self.batch_addr_var, width=200).pack(side="left", padx=6)

        self.batch_start_btn = ctk.CTkButton(ctrl, text="Start", width=80, fg_color=C["green"],
                                              text_color="#000", command=self._start_batch)
        self.batch_start_btn.pack(side="left", padx=6)

        ctk.CTkButton(ctrl, text="Export CSV", width=80, fg_color=C["btn"],
                      command=self._save_csv).pack(side="left")

        self.batch_progress = ctk.CTkProgressBar(tab, fg_color=C["panel"],
                                                  progress_color=C["accent"])
        self.batch_progress.pack(fill="x", padx=10, pady=(0, 5))
        self.batch_progress.set(0)

        self.batch_status_var = ctk.StringVar(value="Ready")
        ctk.CTkLabel(tab, textvariable=self.batch_status_var, text_color=C["dim"],
                     font=("Segoe UI", 10)).pack(anchor="w", padx=12)

        # Batch results
        self.batch_frame = ctk.CTkScrollableFrame(tab, fg_color=C["entry"], corner_radius=6)
        self.batch_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    # ─── Strap Decoder Tab ─────────────────────────────────────────────────

    def _build_strap_tab(self):
        tab = self.tab_straps

        ctk.CTkLabel(tab, text="ACE2 I2C Address Strap Configuration Calculator",
                     font=("Segoe UI", 14, "bold"), text_color=C["accent"]).pack(
            anchor="w", padx=16, pady=(16, 4))

        ctk.CTkLabel(tab, text="Enter Port 1 and Port 2 addresses to compute\n"
                     "the ADDR, CNTL1, and CNTL2 resistor configuration.",
                     text_color=C["dim"]).pack(anchor="w", padx=16)

        # Input
        inp = ctk.CTkFrame(tab, fg_color=C["card"], corner_radius=8)
        inp.pack(fill="x", padx=16, pady=10)

        ctk.CTkLabel(inp, text="Port 1 (hex):", text_color=C["dim"]).grid(
            row=0, column=0, sticky="w", padx=12, pady=8)
        self.strap_p1_var = ctk.StringVar(value="0x38")
        ctk.CTkEntry(inp, textvariable=self.strap_p1_var, width=80).grid(
            row=0, column=1, padx=8, pady=8)

        ctk.CTkLabel(inp, text="Port 2 (hex):", text_color=C["dim"]).grid(
            row=1, column=0, sticky="w", padx=12, pady=8)
        self.strap_p2_var = ctk.StringVar(value="0x38")
        ctk.CTkEntry(inp, textvariable=self.strap_p2_var, width=80).grid(
            row=1, column=1, padx=8, pady=8)

        ctk.CTkButton(inp, text="Calculate", width=90, fg_color=C["red"],
                      hover_color="#ff5a7a", command=self._calc_straps).grid(
            row=0, column=2, rowspan=2, padx=16, pady=8)

        # Results
        res = ctk.CTkFrame(tab, fg_color=C["card"], corner_radius=8)
        res.pack(fill="x", padx=16, pady=5)

        self.strap_result_labels = {}
        fields = [("ADDR Pin:", "addr_bits"), ("  Resistor:", "addr_resistor"),
                  ("CNTL1:", "cntl1"), ("  Source:", "cntl1_source"),
                  ("CNTL2:", "cntl2"), ("  Source:", "cntl2_source"),
                  ("Port 1:", "port1_addr"), ("Port 2:", "port2_addr")]
        for i, (label, key) in enumerate(fields):
            ctk.CTkLabel(res, text=label, text_color=C["dim"], font=("Segoe UI", 11)).grid(
                row=i, column=0, sticky="w", padx=12, pady=2)
            lbl = ctk.CTkLabel(res, text="--", font=("Consolas", 11))
            lbl.grid(row=i, column=1, sticky="w", padx=12, pady=2)
            self.strap_result_labels[key] = lbl

        # Reference table
        ref = ctk.CTkFrame(tab, fg_color=C["card"], corner_radius=8)
        ref.pack(fill="both", expand=True, padx=16, pady=(5, 10))

        ctk.CTkLabel(ref, text="Reference Addresses", font=("Segoe UI", 12, "bold"),
                     text_color=C["accent"]).pack(anchor="w", padx=12, pady=(8, 4))

        self.strap_ref_frame = ctk.CTkScrollableFrame(ref, fg_color=C["entry"], corner_radius=6)
        self.strap_ref_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self._update_strap_reference()

    # ─── Log Tab ───────────────────────────────────────────────────────────

    def _build_log_tab(self):
        tab = self.tab_log

        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=10)
        ctk.CTkButton(row, text="Clear", width=60, fg_color=C["btn"],
                      command=self._clear_log).pack(side="left")
        ctk.CTkButton(row, text="Copy All", width=70, fg_color=C["btn"],
                      command=self._copy_log).pack(side="left", padx=6)

        self.log_text = ctk.CTkTextbox(tab, fg_color=C["entry"], font=("Consolas", 11))
        self.log_text.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    # ─── OTP Tab ───────────────────────────────────────────────────────────

    def _build_otp_tab(self):
        tab = self.tab_otp

        ctk.CTkLabel(tab, text="OTP Memory Scanner & Diff Tool",
                     font=("Segoe UI", 14, "bold"), text_color=C["accent"]).pack(
            anchor="w", padx=16, pady=(12, 2))
        ctk.CTkLabel(tab, text="Read full register space (0x00-0x7F) and diff\n"
                     "vanilla vs OTP-ed chips to find OTP-backed registers.",
                     text_color=C["dim"]).pack(anchor="w", padx=16)

        ctrl = ctk.CTkFrame(tab, fg_color="transparent")
        ctrl.pack(fill="x", padx=16, pady=8)

        ctk.CTkLabel(ctrl, text="Device:", text_color=C["dim"]).pack(side="left")
        self.otp_addr_var = ctk.StringVar(value="0x38")
        ctk.CTkEntry(ctrl, textvariable=self.otp_addr_var, width=70).pack(side="left", padx=6)

        self.otp_scan_btn = ctk.CTkButton(ctrl, text="Scan OTP", width=90,
                                           fg_color=C["green"], text_color="#000",
                                           command=self._otp_scan_device)
        self.otp_scan_btn.pack(side="left", padx=6)
        ctk.CTkButton(ctrl, text="Import", width=60, fg_color=C["btn"],
                      command=self._otp_import_file).pack(side="left", padx=4)
        ctk.CTkButton(ctrl, text="Diff", width=50, fg_color=C["btn"],
                      command=self._otp_diff_dialog).pack(side="left", padx=4)

        self.otp_progress = ctk.CTkProgressBar(tab, fg_color=C["panel"],
                                                progress_color=C["accent"])
        self.otp_progress.pack(fill="x", padx=16, pady=(0, 4))
        self.otp_progress.set(0)

        self.otp_status_var = ctk.StringVar(value="Ready")
        ctk.CTkLabel(tab, textvariable=self.otp_status_var, text_color=C["dim"],
                     font=("Segoe UI", 10)).pack(anchor="w", padx=16)

        # Split: dump + diff
        split = ctk.CTkFrame(tab, fg_color="transparent")
        split.pack(fill="both", expand=True, padx=10, pady=(4, 10))
        split.grid_columnconfigure(0, weight=1)
        split.grid_columnconfigure(1, weight=1)
        split.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(split, fg_color=C["card"], corner_radius=8)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        ctk.CTkLabel(left, text="Current Dump", font=("Segoe UI", 11, "bold"),
                     text_color=C["accent"]).pack(anchor="w", padx=10, pady=(6, 2))
        self.otp_dump_text = ctk.CTkTextbox(left, fg_color=C["entry"],
                                             font=("Consolas", 10), state="disabled")
        self.otp_dump_text.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        right = ctk.CTkFrame(split, fg_color=C["card"], corner_radius=8)
        right.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        ctk.CTkLabel(right, text="Diff Result", font=("Segoe UI", 11, "bold"),
                     text_color=C["accent"]).pack(anchor="w", padx=10, pady=(6, 2))
        self.otp_diff_text = ctk.CTkTextbox(right, fg_color=C["entry"],
                                              font=("Consolas", 10), state="disabled")
        self.otp_diff_text.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    # ─── Logging ───────────────────────────────────────────────────────────

    def log(self, msg: str, level: str = "info"):
        ts = datetime.now().strftime("%H:%M:%S")
        color_map = {"ok": C["green"], "warn": C["yellow"], "err": C["red"],
                     "info": C["accent"], "cmd": C["orange"]}
        color = color_map.get(level, C["text"])

        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{ts}] ", "dim")
        self.log_text.insert("end", f"{msg}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _copy_log(self):
        self.clipboard_clear()
        self.clipboard_append(self.log_text.get("1.0", "end"))
        self.log("Log copied to clipboard", "ok")

    # ─── Connection ────────────────────────────────────────────────────────

    def _auto_detect(self):
        self.log("Scanning for I2C adapters...")
        adapter = detect_adapter()
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
        selection = self.adapter_var.get()
        self.log(f"Connecting to {selection}...")
        try:
            if selection == "Auto-detect":
                adapter = detect_adapter()
            elif selection == "FTDI FT232H":
                adapter = FTDIAdapter()
            elif selection in ("SMBus (Linux)", "CH341"):
                adapter = SMBusAdapter(bus_number=int(self.bus_var.get()))
            else:
                return

            if adapter is None:
                self.log("Could not connect", "err")
                return

            adapter.open()
            self.adapter = adapter
            self.connected = True
            self._update_conn_status(True)
            self.log(f"Connected: {type(adapter).__name__}", "ok")
        except Exception as e:
            self.log(f"Connection failed: {e}", "err")

    def _disconnect(self):
        if self.adapter:
            self.adapter.close()
            self.adapter = None
        self.connected = False
        self.devices.clear()
        self.scan_results.clear()
        self._update_conn_status(False)
        self._clear_devices()
        self.log("Disconnected")

    def _update_conn_status(self, connected):
        if connected:
            self.conn_status.configure(text="Connected", text_color=C["green"])
            self.connect_btn.configure(text="Disconnect", fg_color=C["red"])
        else:
            self.conn_status.configure(text="Disconnected", text_color=C["red"])
            self.connect_btn.configure(text="Connect", fg_color=C["red"])

    # ─── Bus Scanning ──────────────────────────────────────────────────────

    def _scan_bus(self):
        if not self._check_conn():
            return
        self.log("Scanning I2C bus (0x08-0x77)...")
        self.status_left.configure(text="Scanning...")

        def do():
            try:
                devices = self.adapter.scan(0x08, 0x77)
                self.scan_results = devices
                self.after(0, self._show_devices, devices)
            except Exception as e:
                self.after(0, self.log, f"Scan error: {e}", "err")
        threading.Thread(target=do, daemon=True).start()

    def _quick_scan(self):
        if not self._check_conn():
            return
        self.log("Quick scanning known ACE2 addresses...")

        def do():
            try:
                found = [a for a in KNOWN_ACE2_ADDRESSES if self.adapter.ping(a)]
                self.scan_results = found
                self.after(0, self._show_devices, found)
            except Exception as e:
                self.after(0, self.log, f"Quick scan error: {e}", "err")
        threading.Thread(target=do, daemon=True).start()

    def _show_devices(self, devices):
        self._clear_devices()
        self.device_count_var.set(f"{len(devices)} device(s)")
        if not devices:
            self.log("No devices found", "warn")
            return
        self.log(f"Found {len(devices)} device(s)", "ok")

        for addr in devices:
            is_ace2 = is_ace2_address(addr)
            desc = KNOWN_ACE2_ADDRESSES.get(addr, "")[:25]
            self._add_device_row(addr, "?", "?", "?", desc, is_ace2)

    def _add_device_row(self, addr, health, score, mode, desc, is_ace2=True):
        row = ctk.CTkFrame(self.device_frame, fg_color=C["card"], corner_radius=6, height=36)
        row.pack(fill="x", pady=2)
        row.pack_propagate(False)

        color = C["accent"] if is_ace2 else C["dim"]
        ctk.CTkLabel(row, text=f"0x{addr:02X}", font=("Consolas", 12, "bold"),
                     text_color=color, width=65, anchor="w").pack(side="left", padx=8)
        ctk.CTkLabel(row, text=health, font=("Segoe UI", 11, "bold"),
                     text_color=C["green"], width=55).pack(side="left")
        ctk.CTkLabel(row, text=str(score), font=("Segoe UI", 11),
                     text_color=C["text"], width=40).pack(side="left")
        ctk.CTkLabel(row, text=desc, text_color=C["dim"], font=("Segoe UI", 9),
                     anchor="w").pack(side="left", padx=8, fill="x", expand=True)

        ctk.CTkButton(row, text="Diagnose", width=70, height=24, fg_color=C["btn"],
                      command=lambda a=addr: self._diagnose_address(a)).pack(
            side="right", padx=6)

        self.device_rows[addr] = row

    def _clear_devices(self):
        for row in self.device_rows.values():
            row.destroy()
        self.device_rows.clear()

    def _refresh_devices(self):
        if not self._check_conn() or not self.scan_results:
            return
        self.log("Refreshing all devices...")

        def do():
            analyzer = CD3217Analyzer(self.adapter)
            for addr in self.scan_results:
                try:
                    self.devices[addr] = analyzer.diagnose_device(addr)
                except Exception as e:
                    self.log(f"Error 0x{addr:02X}: {e}", "err")
            self.after(0, self._refresh_display)
        threading.Thread(target=do, daemon=True).start()

    def _refresh_display(self):
        self._clear_devices()
        for addr, dev in sorted(self.devices.items()):
            health = dev.health.value
            color = C["green"] if dev.health == HealthStatus.PASS else (
                C["yellow"] if dev.health == HealthStatus.WARN else C["red"])
            chip = "Vanilla" if dev.is_vanilla else ("OTP" if dev.is_vanilla is False else "?")
            row = ctk.CTkFrame(self.device_frame, fg_color=C["card"], corner_radius=6, height=36)
            row.pack(fill="x", pady=2)
            row.pack_propagate(False)
            ctk.CTkLabel(row, text=f"0x{addr:02X}", font=("Consolas", 12, "bold"),
                         text_color=C["accent"], width=65, anchor="w").pack(side="left", padx=8)
            ctk.CTkLabel(row, text=health, font=("Segoe UI", 11, "bold"),
                         text_color=color, width=55).pack(side="left")
            ctk.CTkLabel(row, text=str(dev.health_score), font=("Segoe UI", 11),
                         text_color=C["text"], width=40).pack(side="left")
            ctk.CTkLabel(row, text=chip, text_color=C["dim"], font=("Segoe UI", 9),
                         width=50).pack(side="left")
            ctk.CTkButton(row, text="Diagnose", width=70, height=24, fg_color=C["btn"],
                          command=lambda a=addr: self._diagnose_address(a)).pack(
                side="right", padx=6)
            self.device_rows[addr] = row

    # ─── Diagnosis ─────────────────────────────────────────────────────────

    def _diagnose_selected(self):
        if not self._check_conn():
            return
        if self.device_rows:
            first_addr = next(iter(self.device_rows))
            self._diagnose_address(first_addr)
        else:
            self._diagnose_quick()

    def _diagnose_quick(self):
        if not self._check_conn():
            return
        addr_str = self.quick_addr_var.get().strip()
        try:
            addr = int(addr_str, 16) if addr_str.startswith("0x") else int(addr_str)
        except ValueError:
            self.log(f"Invalid address: {addr_str}", "err")
            return
        self._diagnose_address(addr)

    def _diagnose_address(self, address):
        self.log(f"Diagnosing 0x{address:02X}...")
        self.status_left.configure(text=f"Diagnosing 0x{address:02X}...")

        def do():
            try:
                result = CD3217Analyzer(self.adapter).diagnose_device(address)
                self.devices[address] = result
                self.after(0, self._show_result, result)
            except Exception as e:
                self.after(0, self.log, f"Error: {e}", "err")
        threading.Thread(target=do, daemon=True).start()

    def _show_result(self, result):
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
            parts.append(f"{result.scan_time_ms:.0f}ms response")
        self.health_detail.configure(text=" | ".join(parts) if parts else "All checks passed")

        self.info_labels["address"].configure(text=f"0x{result.address:02X}")
        self.info_labels["vid"].configure(text=result.vendor_id or "N/A")
        self.info_labels["did"].configure(text=result.device_id or "N/A")
        self.info_labels["mode"].configure(text=result.mode or "N/A")
        self.info_labels["type"].configure(text=result.device_type or "N/A")
        self.info_labels["time"].configure(text=f"{result.scan_time_ms:.1f} ms")

        OTP = {0x3A, 0x3B, 0x3C, 0x74, 0x76, 0x78, 0x79}
        VAN = {0x38, 0x3F, 0x2F, 0x28}
        if result.address in OTP:
            chip = "OTP-ed (Apple address)"
        elif result.address in VAN:
            chip = "Likely vanilla"
        else:
            chip = "Unknown type"
        self.info_labels["chip_type"].configure(text=chip)

        # Faults text
        self.faults_text.configure(state="normal")
        self.faults_text.delete("1.0", "end")
        if not result.faults:
            self.faults_text.insert("end", "All checks passed\n")
            self.faults_text.insert("end", "Device responding correctly on I2C.\n")
            self.faults_text.insert("end", "Vendor ID matches TI.\n")
            if result.mode and "APP" in result.mode.upper():
                self.faults_text.insert("end", "Device is in Application mode.\n")
        else:
            self.faults_text.insert("end", f"FAULTS: {len(result.faults)}\n\n")
            for fault in result.faults:
                self.faults_text.insert("end", f"  [{fault.value}]\n")
            if result.fault_details:
                self.faults_text.insert("end", "\nDetails:\n")
                for d in result.fault_details:
                    self.faults_text.insert("end", f"  - {d}\n")
        self.faults_text.configure(state="disabled")

        self.tabs.set("  Overview  ")
        self.log(f"0x{result.address:02X}: {result.health.value} ({score})", "ok"
                 if result.health == HealthStatus.PASS else "warn")
        self.status_left.configure(text=f"0x{result.address:02X}: {result.health.value} ({score}/100)")

    # ─── Register Dump ─────────────────────────────────────────────────────

    def _dump_selected(self):
        if not self._check_conn():
            return
        self._read_registers()

    def _read_registers(self):
        if not self._check_conn():
            return
        addr_str = self.reg_addr_var.get().strip()
        try:
            addr = int(addr_str, 16) if addr_str.startswith("0x") else int(addr_str)
        except ValueError:
            self.log(f"Invalid address: {addr_str}", "err")
            return

        self.log(f"Reading registers from 0x{addr:02X}...")

        # Clear existing rows (keep header)
        for widget in self.reg_frame.winfo_children()[1:]:
            widget.destroy()

        def do():
            try:
                analyzer = CD3217Analyzer(self.adapter)
                for offset in sorted(REGISTERS.keys()):
                    reg_def = REGISTERS[offset]
                    read = analyzer.read_register(addr, offset, reg_def.length)
                    if read:
                        hex_str = read.raw_bytes.hex()
                        decoded = read.decoded or f"0x{read.raw_value:X}"
                        self.after(0, self._add_reg_row, f"0x{offset:02X}",
                                   read.name, hex_str, f"0x{read.raw_value:X}", decoded)
                    else:
                        self.after(0, self._add_reg_row, f"0x{offset:02X}",
                                   reg_def.name, "ERROR", "--", "Read failed")
                self.after(0, self.log, f"Register dump: 0x{addr:02X}", "ok")
            except Exception as e:
                self.after(0, self.log, f"Register error: {e}", "err")
        threading.Thread(target=do, daemon=True).start()

    def _add_reg_row(self, offset, name, hex_val, value, decoded):
        row = ctk.CTkFrame(self.reg_frame, fg_color=C["card"], corner_radius=4, height=28)
        row.pack(fill="x", pady=1)
        row.pack_propagate(False)
        ctk.CTkLabel(row, text=offset, font=("Consolas", 10), text_color=C["accent"],
                     width=65, anchor="w").pack(side="left", padx=6)
        ctk.CTkLabel(row, text=name, font=("Consolas", 10), text_color=C["text"],
                     width=150, anchor="w").pack(side="left")
        ctk.CTkLabel(row, text=hex_val, font=("Consolas", 10), text_color=C["dim"],
                     width=180, anchor="w").pack(side="left")
        ctk.CTkLabel(row, text=value, font=("Consolas", 10), text_color=C["text"],
                     width=100, anchor="w").pack(side="left")
        ctk.CTkLabel(row, text=decoded, font=("Consolas", 10), text_color=C["text"],
                     anchor="w").pack(side="left", padx=4, fill="x", expand=True)

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

    def _switch_to_batch(self):
        self.tabs.set("  Batch  ")

    def _start_batch(self):
        if not self._check_conn():
            return
        try:
            count = int(self.batch_count_var.get())
        except ValueError:
            self.log("Invalid iteration count", "err")
            return
        try:
            addrs = []
            for part in self.batch_addr_var.get().split(","):
                part = part.strip()
                addrs.append(int(part, 16) if part.startswith("0x") else int(part))
        except ValueError:
            self.log("Invalid addresses", "err")
            return

        for widget in self.batch_frame.winfo_children():
            widget.destroy()
        self.batch_results.clear()
        self.batch_progress.set(0)
        self.batch_start_btn.configure(state="disabled")

        self.log(f"Batch: {count} x {len(addrs)} device(s)", "cmd")

        def do():
            analyzer = CD3217Analyzer(self.adapter, addresses=addrs)
            total = count * len(addrs)
            done = 0
            for i in range(count):
                for addr in addrs:
                    try:
                        result = analyzer.diagnose_device(addr)
                        self.batch_results.append(result)
                        fault_str = "; ".join(f.value for f in result.faults) if result.faults else ""
                        self.after(0, self._add_batch_row, i + 1, addr,
                                   result.health.value, result.health_score,
                                   result.mode or "--", fault_str,
                                   f"{result.scan_time_ms:.0f}")
                        done += 1
                        self.after(0, self.batch_progress.set, done / total)
                        self.after(0, self.batch_status_var.set, f"{done}/{total}")
                    except Exception as e:
                        self.after(0, self.log, f"Batch error: {e}", "err")
                        done += 1
                        self.after(0, self.batch_progress.set, done / total)
            self.after(0, lambda: self.batch_start_btn.configure(state="normal"))
            total_r = len(self.batch_results)
            passed = sum(1 for r in self.batch_results if r.health == HealthStatus.PASS)
            self.after(0, self.batch_status_var.set,
                       f"Done: {total_r} tests | {passed} pass | {total_r - passed} fail")
        threading.Thread(target=do, daemon=True).start()

    def _add_batch_row(self, iteration, addr, health, score, mode, faults, time_ms):
        color = C["green"] if health == "PASS" else (C["yellow"] if health == "WARN" else C["red"])
        row = ctk.CTkFrame(self.batch_frame, fg_color=C["card"], corner_radius=4, height=28)
        row.pack(fill="x", pady=1)
        row.pack_propagate(False)
        ctk.CTkLabel(row, text=str(iteration), font=("Consolas", 10), width=30,
                     text_color=C["dim"]).pack(side="left", padx=6)
        ctk.CTkLabel(row, text=f"0x{addr:02X}", font=("Consolas", 10), width=60,
                     text_color=C["accent"]).pack(side="left")
        ctk.CTkLabel(row, text=health, font=("Segoe UI", 10, "bold"), width=50,
                     text_color=color).pack(side="left")
        ctk.CTkLabel(row, text=str(score), font=("Consolas", 10), width=40).pack(side="left")
        ctk.CTkLabel(row, text=faults, font=("Consolas", 9), text_color=C["dim"],
                     anchor="w").pack(side="left", padx=6, fill="x", expand=True)
        ctk.CTkLabel(row, text=f"{time_ms}ms", font=("Consolas", 9),
                     text_color=C["dim"], width=50).pack(side="left")

    # ─── Strap Decoder ─────────────────────────────────────────────────────

    def _calc_straps(self):
        try:
            p1 = int(self.strap_p1_var.get().strip(), 16)
            p2 = int(self.strap_p2_var.get().strip(), 16)
        except ValueError:
            self.log("Invalid hex addresses", "err")
            return
        info = decode_i2c_address_straps(p1, p2)
        for key, value in info.items():
            if key in self.strap_result_labels:
                self.strap_result_labels[key].configure(text=str(value))
        self.log(f"Strap decode: P1=0x{p1:02X} P2=0x{p2:02X}", "info")

    def _update_strap_reference(self):
        for widget in self.strap_ref_frame.winfo_children():
            widget.destroy()

        if self.current_model:
            title = f"Addresses ({self.current_model.model_id})"
            items = [(p.ref, f"0x{p.address:02X}", f"0x{p.address:02X}",
                      p.addressing.capitalize(), f"Port {p.i2c_port}")
                     for p in self.current_model.positions]
        else:
            title = "Default Addresses"
            items = [
                ("UF400 (UPC0)", "0x38", "0x38", "Vanilla", "1"),
                ("UF500 (UPC1)", "0x3F", "0x3F", "Vanilla", "1"),
                ("UB300 (UPC2)", "0x20", "0x20", "OTP", "1"),
                ("UB400 (UPC3)", "0x74", "0x74", "OTP", "1"),
                ("UF500 (UPC4)", "0x39", "0x39", "Strap", "2"),
                ("UF600 (UPC5)", "0x10", "0x10", "Strap", "2"),
            ]

        for pos, p1, p2, typ, port in items:
            row = ctk.CTkFrame(self.strap_ref_frame, fg_color=C["card"],
                               corner_radius=4, height=28)
            row.pack(fill="x", pady=1)
            row.pack_propagate(False)
            ctk.CTkLabel(row, text=pos, font=("Consolas", 10), text_color=C["text"],
                         width=140, anchor="w").pack(side="left", padx=6)
            ctk.CTkLabel(row, text=p1, font=("Consolas", 10), text_color=C["accent"],
                         width=60).pack(side="left")
            ctk.CTkLabel(row, text=typ, font=("Consolas", 10), text_color=C["yellow"],
                         width=60).pack(side="left")
            ctk.CTkLabel(row, text=port, font=("Consolas", 10), text_color=C["dim"],
                         width=50).pack(side="left")

    def _on_model_change(self, selection):
        if selection == "Auto-detect":
            self.current_model = None
            self.log("Model: Auto-detect", "info")
        else:
            model_id = selection.split(" - ")[0].strip()
            self.current_model = get_model(model_id)
            if self.current_model:
                self.log(f"Model: {self.current_model.name}", "info")
                self._update_strap_reference()
                if self.current_model.positions:
                    addrs = [f"0x{p.address:02X}" for p in self.current_model.positions]
                    self.batch_addr_var.set(",".join(addrs))

    # ─── Flash Manager ─────────────────────────────────────────────────────

    def _build_flash_tab(self):
        tab = self.tab_flash

        ctk.CTkLabel(tab, text="SPI Flash Manager",
                     font=("Segoe UI", 14, "bold"), text_color=C["accent"]).pack(
            anchor="w", padx=16, pady=(12, 2))
        ctk.CTkLabel(tab, text="Read/write external SPI flash (firmware ROM)\n"
                     "Requires FTDI FT232H connected to flash chip SPI pins.",
                     text_color=C["dim"]).pack(anchor="w", padx=16)

        # Connection
        conn = ctk.CTkFrame(tab, fg_color=C["card"], corner_radius=8)
        conn.pack(fill="x", padx=16, pady=8)

        ctk.CTkButton(conn, text="Connect SPI", width=100, fg_color=C["green"],
                      text_color="#000", command=self._flash_connect).pack(side="left", padx=10, pady=8)
        self.flash_conn_status = ctk.CTkLabel(conn, text="Disconnected", text_color=C["red"])
        self.flash_conn_status.pack(side="left", padx=8)

        # Flash info
        info = ctk.CTkFrame(tab, fg_color=C["card"], corner_radius=8)
        info.pack(fill="x", padx=16, pady=5)

        ctk.CTkButton(info, text="Detect Chip", width=90, fg_color=C["btn"],
                      command=self._flash_detect).pack(side="left", padx=10, pady=8)
        ctk.CTkButton(info, text="Power Up", width=70, fg_color=C["btn"],
                      command=self._flash_power_up).pack(side="left", padx=4)
        ctk.CTkButton(info, text="Reset", width=60, fg_color=C["btn"],
                      command=self._flash_reset).pack(side="left", padx=4)

        self.flash_info_var = ctk.StringVar(value="No chip detected")
        ctk.CTkLabel(info, textvariable=self.flash_info_var, text_color=C["text"],
                     font=("Consolas", 11)).pack(side="left", padx=12)

        # Actions
        act = ctk.CTkFrame(tab, fg_color=C["card"], corner_radius=8)
        act.pack(fill="x", padx=16, pady=5)

        ctk.CTkButton(act, text="Read Flash", width=90, fg_color=C["green"],
                      text_color="#000", command=self._flash_read).pack(side="left", padx=10, pady=8)
        ctk.CTkButton(act, text="Write File", width=90, fg_color=C["red"],
                      hover_color="#ff5a7a", command=self._flash_write).pack(side="left", padx=4)
        ctk.CTkButton(act, text="Erase Chip", width=90, fg_color=C["red"],
                      hover_color="#ff5a7a", command=self._flash_erase).pack(side="left", padx=4)
        ctk.CTkButton(act, text="Restore", width=80, fg_color=C["orange"],
                      command=self._flash_restore).pack(side="left", padx=4)

        # Progress
        self.flash_progress = ctk.CTkProgressBar(tab, fg_color=C["panel"],
                                                   progress_color=C["accent"])
        self.flash_progress.pack(fill="x", padx=16, pady=(0, 4))
        self.flash_progress.set(0)

        self.flash_status_var = ctk.StringVar(value="Ready")
        ctk.CTkLabel(tab, textvariable=self.flash_status_var, text_color=C["dim"],
                     font=("Segoe UI", 10)).pack(anchor="w", padx=16)

        # Hex viewer
        hv = ctk.CTkFrame(tab, fg_color=C["card"], corner_radius=8)
        hv.pack(fill="both", expand=True, padx=16, pady=(5, 10))

        ctk.CTkLabel(hv, text="Flash Contents (hex preview)", font=("Segoe UI", 11, "bold"),
                     text_color=C["accent"]).pack(anchor="w", padx=10, pady=(6, 2))

        self.flash_hex_text = ctk.CTkTextbox(hv, fg_color=C["entry"],
                                              font=("Consolas", 10), state="disabled")
        self.flash_hex_text.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    def _flash_connect(self):
        try:
            self.spi_adapter = SPIAdapter()
            self.spi_adapter.open()
            self.flash = SPIFlash(self.spi_adapter)
            self.flash_conn_status.configure(text="Connected", text_color=C["green"])
            self.log("SPI flash connected", "ok")
        except Exception as e:
            self.log(f"SPI connect error: {e}", "err")
            self.flash_conn_status.configure(text="Error", text_color=C["red"])

    def _flash_detect(self):
        if not self.flash:
            self.log("Connect SPI first", "warn")
            return
        try:
            info = self.flash.detect()
            self.flash_info = info
            self.flash_info_var.set(f"{info.name} — {info.size_mb:.1f}MB "
                                    f"({info.sector_count} sectors) | "
                                    f"ID: 0x{info.jedec_id[0]:02X}{info.jedec_id[1]:02X}{info.jedec_id[2]:02X}")
            self.log(f"Flash detected: {info.name}", "ok")
        except Exception as e:
            self.log(f"Flash detect error: {e}", "err")
            self.flash_info_var.set("Detection failed")

    def _flash_power_up(self):
        if not self.flash:
            return
        try:
            self.flash.power_up()
            self.log("Flash powered up", "ok")
        except Exception as e:
            self.log(f"Power up error: {e}", "err")

    def _flash_reset(self):
        if not self.flash:
            return
        try:
            self.flash.reset()
            self.log("Flash reset", "ok")
        except Exception as e:
            self.log(f"Reset error: {e}", "err")

    def _flash_read(self):
        if not self.flash:
            self.log("Connect SPI first", "warn")
            return
        filepath = filedialog.asksaveasfilename(
            defaultextension=".bin",
            filetypes=[("Binary", "*.bin"), ("All", "*.*")],
            title="Save Flash Dump")
        if not filepath:
            return

        if not self.flash_info:
            self._flash_detect()
        if not self.flash_info or self.flash_info.size_bytes == 0:
            self.log("Cannot read — unknown flash size", "err")
            return

        self.flash_status_var.set("Reading flash...")
        self.flash_progress.set(0)

        def do():
            def progress(cur, total):
                self.after(0, self.flash_progress.set, cur / total)
            try:
                size = self.flash.dump_to_file(filepath, progress_cb=progress)
                self.after(0, self.flash_status_var.set, f"Read {size} bytes → {filepath}")
                self.after(0, self.log, f"Flash dumped: {filepath} ({size} bytes)", "ok")
                self.after(0, self._show_flash_hex, filepath, 256)
            except Exception as e:
                self.after(0, self.log, f"Flash read error: {e}", "err")
        threading.Thread(target=do, daemon=True).start()

    def _flash_write(self):
        if not self.flash:
            self.log("Connect SPI first", "warn")
            return
        filepath = filedialog.askopenfilename(
            filetypes=[("Binary", "*.bin"), ("All", "*.*")],
            title="Select firmware file to write")
        if not filepath:
            return

        data = Path(filepath).read_bytes()
        if self.flash_info and len(data) > self.flash_info.size_bytes:
            self.log(f"File too large: {len(data)} bytes", "err")
            return

        confirm = messagebox.askyesno("Confirm Write",
            f"ERASE and write {len(data)} bytes to flash?\n\n"
            f"This will destroy existing contents.\nFile: {filepath}")
        if not confirm:
            return

        self.flash_status_var.set("Writing flash...")
        self.flash_progress.set(0)

        def do():
            def progress(cur, total):
                self.after(0, self.flash_progress.set, cur / total)
            try:
                # Erase first
                self.after(0, self.flash_status_var.set, "Erasing chip...")
                self.flash.erase_chip()

                # Write
                self.after(0, self.flash_status_var.set, "Writing data...")
                self.flash.write(0, data, progress_cb=progress)

                # Verify
                self.after(0, self.flash_status_var.set, "Verifying...")
                readback = self.flash.read(0, len(data))
                if readback == data:
                    self.after(0, self.flash_status_var.set, f"Write complete — {len(data)} bytes verified")
                    self.after(0, self.log, f"Flash write verified: {len(data)} bytes", "ok")
                else:
                    for i in range(len(data)):
                        if readback[i] != data[i]:
                            self.after(0, self.log, f"Verify mismatch at 0x{i:06X}", "err")
                            break
                    self.after(0, self.flash_status_var.set, "Write complete — VERIFY FAILED")
            except Exception as e:
                self.after(0, self.log, f"Flash write error: {e}", "err")
        threading.Thread(target=do, daemon=True).start()

    def _flash_erase(self):
        if not self.flash:
            self.log("Connect SPI first", "warn")
            return
        confirm = messagebox.askyesno("Confirm Erase",
            "ERASE entire flash chip?\n\nThis will destroy all contents.")
        if not confirm:
            return

        self.flash_status_var.set("Erasing chip...")
        def do():
            try:
                self.flash.erase_chip()
                self.after(0, self.flash_status_var.set, "Erase complete")
                self.after(0, self.log, "Flash erased", "ok")
            except Exception as e:
                self.after(0, self.log, f"Erase error: {e}", "err")
        threading.Thread(target=do, daemon=True).start()

    def _flash_restore(self):
        if not self.flash:
            self.log("Connect SPI first", "warn")
            return
        filepath = filedialog.askopenfilename(
            filetypes=[("Binary", "*.bin"), ("All", "*.*")],
            title="Select firmware file to restore")
        if not filepath:
            return

        confirm = messagebox.askyesno("Confirm Restore",
            f"Erase and restore flash from:\n{filepath}\n\nContinue?")
        if not confirm:
            return

        self.flash_status_var.set("Restoring flash...")
        self.flash_progress.set(0)

        def do():
            def progress(phase, cur, total):
                self.after(0, self.flash_progress.set, cur / total if total else 0)
                self.after(0, self.flash_status_var.set, f"{phase}: {cur}/{total}")
            try:
                self.flash.full_restore(filepath, progress_cb=progress)
                self.after(0, self.flash_status_var.set, "Restore complete — verified")
                self.after(0, self.log, f"Flash restored: {filepath}", "ok")
            except Exception as e:
                self.after(0, self.log, f"Restore error: {e}", "err")
        threading.Thread(target=do, daemon=True).start()

    def _show_flash_hex(self, filepath, max_bytes=256):
        """Show hex preview of flash dump."""
        from pathlib import Path as P
        try:
            data = P(filepath).read_bytes()[:max_bytes]
            lines = [f"{'Addr':<8} {'Hex':<48} {'ASCII'}"]
            lines.append(f"{'-'*8} {'-'*48} {'-'*16}")
            for i in range(0, len(data), 16):
                chunk = data[i:i+16]
                hex_part = " ".join(f"{b:02X}" for b in chunk)
                ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
                lines.append(f"0x{i:06X}  {hex_part:<48} {ascii_part}")

            self.flash_hex_text.configure(state="normal")
            self.flash_hex_text.delete("1.0", "end")
            self.flash_hex_text.insert("end", "\n".join(lines))
            self.flash_hex_text.configure(state="disabled")
        except Exception:
            pass

    # ─── OTP Scanner ───────────────────────────────────────────────────────

    def _otp_scan_device(self):
        if not self._check_conn():
            return
        try:
            addr = int(self.otp_addr_var.get().strip(), 16)
        except ValueError:
            self.log("Invalid address", "err")
            return

        self.log(f"OTP scan: 0x{addr:02X}...")
        self.otp_scan_btn.configure(state="disabled")
        self.otp_progress.set(0)

        def do():
            def progress(cur, total):
                self.after(0, self.otp_progress.set, cur / total)
            try:
                dump = scan_otp(self.adapter, addr, label=f"0x{addr:02X}", progress_cb=progress)
                self.otp_current_dump = dump
                self.after(0, self._show_otp_dump, dump)
                self.after(0, self.otp_status_var.set,
                           f"Done: {dump.filled_count} regs, {dump.error_count} errors")
                self.after(0, self.log, f"OTP scan: {dump.filled_count} regs", "ok")
            except Exception as e:
                self.after(0, self.log, f"OTP error: {e}", "err")
            finally:
                self.after(0, lambda: self.otp_scan_btn.configure(state="normal"))
        threading.Thread(target=do, daemon=True).start()

    def _show_otp_dump(self, dump):
        self.otp_dump_text.configure(state="normal")
        self.otp_dump_text.delete("1.0", "end")
        self.otp_dump_text.insert("end", format_dump_table(dump, show_zeros=True))
        self.otp_dump_text.configure(state="disabled")

    def _otp_import_file(self):
        filepath = filedialog.askopenfilename(
            filetypes=[("OTP dumps", "*.json *.otp.bin"), ("All", "*.*")])
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
        file_a = filedialog.askopenfilename(title="Select Dump A (vanilla)",
            filetypes=[("OTP dumps", "*.json *.otp.bin"), ("All", "*.*")])
        if not file_a:
            return
        file_b = filedialog.askopenfilename(title="Select Dump B (OTP-ed)",
            filetypes=[("OTP dumps", "*.json *.otp.bin"), ("All", "*.*")])
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

        self.log(f"Diff: {result.match_count} same, {result.diff_count} different", "info")

        if result.diff_count > 0:
            save = messagebox.askyesno("Save", f"{result.diff_count} different registers. Save report?")
            if save:
                filepath = filedialog.asksaveasfilename(defaultextension=".txt")
                if filepath:
                    save_diff_report(result, filepath)
                    self.log(f"Saved: {filepath}", "ok")

    # ─── File Operations ───────────────────────────────────────────────────

    def _save_json(self):
        filepath = filedialog.asksaveasfilename(defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("All", "*.*")])
        if not filepath:
            return
        report = DiagnosticReport(timestamp=datetime.now().isoformat(),
                                   adapter_type=type(self.adapter).__name__ if self.adapter else "None")
        report.bus_scan_results = self.scan_results
        report.devices = list(self.devices.values())
        report.summary = f"GUI session - {len(self.devices)} device(s)"
        try:
            save_json_report(report, filepath)
            self.log(f"Saved: {filepath}", "ok")
        except Exception as e:
            self.log(f"Save error: {e}", "err")

    def _save_csv(self):
        if not self.batch_results:
            self.log("No batch data", "warn")
            return
        filepath = filedialog.asksaveasfilename(defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("All", "*.*")])
        if filepath:
            save_csv_log(self.batch_results, filepath, append=False)
            self.log(f"CSV saved: {filepath}", "ok")

    # ─── Utilities ─────────────────────────────────────────────────────────

    def _check_conn(self) -> bool:
        if not self.connected or not self.adapter:
            self.log("Not connected. Click Connect first.", "warn")
            return False
        return True


def main():
    app = Application()
    app.mainloop()


if __name__ == "__main__":
    main()
