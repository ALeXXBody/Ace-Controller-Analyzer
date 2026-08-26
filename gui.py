"""CD3217B12 (Apple ACE2) I2C Diagnostic Analyzer - Windows GUI.

A tkinter-based GUI application for testing CD3217B12 USB-C PD controllers
used in MacBook repair.
"""

import csv
import json
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from datetime import datetime
from typing import Dict, List, Optional

# Import our core modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cd3217_analyzer.registers import (
    KNOWN_ACE2_ADDRESSES,
    REGISTERS,
    decode_i2c_address_straps,
    decode_mode_reg,
    decode_vid,
    is_ace2_address,
)
from cd3217_analyzer.models import (
    MACBOOK_MODELS,
    MacBookModel,
    get_model,
    list_models,
    model_ids,
)
from cd3217_analyzer.analyzer import (
    CD3217Analyzer,
    DeviceResult,
    DiagnosticReport,
    FaultType,
    HealthStatus,
)
from cd3217_analyzer.adapters import (
    ADAPTER_TYPES,
    FTDIAdapter,
    SMBusAdapter,
    detect_adapter,
)
from cd3217_analyzer.report import save_json_report, save_csv_log
from cd3217_analyzer.otp import (
    OTPDump,
    diff_dumps,
    format_dump_table,
    load_dump_binary,
    load_dump_json,
    save_diff_report,
    save_dump_binary,
    save_dump_json,
    scan_otp,
)


# ─── Color Theme ──────────────────────────────────────────────────────────────
COLORS = {
    "bg_dark": "#1a1a2e",
    "bg_mid": "#16213e",
    "bg_panel": "#0f3460",
    "bg_card": "#1a1a3e",
    "accent": "#e94560",
    "accent2": "#00b4d8",
    "green": "#06d6a0",
    "yellow": "#ffd166",
    "red": "#ef476f",
    "orange": "#f4845f",
    "text": "#e0e0e0",
    "text_dim": "#8892a0",
    "text_bright": "#ffffff",
    "border": "#2a2a4a",
    "btn_bg": "#533483",
    "btn_hover": "#6a42a0",
    "entry_bg": "#0d1b2a",
    "tree_bg": "#0d1b2a",
    "tree_select": "#1b3a5c",
    "pass": "#06d6a0",
    "warn": "#ffd166",
    "fail": "#ef476f",
}


class Application(tk.Tk):
    """Main application window."""

    def __init__(self):
        super().__init__()

        self.title("CD3217B12 (Apple ACE2) I2C Diagnostic Analyzer")
        self.geometry("1200x850")
        self.minsize(900, 700)
        self.configure(bg=COLORS["bg_dark"])

        # State
        self.adapter = None
        self.analyzer = None
        self.connected = False
        self.scan_results = []
        self.devices: Dict[int, DeviceResult] = {}
        self.selected_address = None
        self.current_model: Optional[MacBookModel] = None

        # Configure styles
        self._setup_styles()

        # Build UI
        self._build_menu()
        self._build_ui()

        # Check for adapters on startup
        self.after(500, self._auto_detect_adapter)

    def _setup_styles(self):
        """Configure ttk styles for dark theme."""
        style = ttk.Style()
        style.theme_use("clam")

        # General
        style.configure(".", background=COLORS["bg_dark"], foreground=COLORS["text"],
                        borderwidth=0, focuscolor=COLORS["accent2"])
        style.configure("TFrame", background=COLORS["bg_dark"])
        style.configure("TLabel", background=COLORS["bg_dark"],
                        foreground=COLORS["text"], font=("Segoe UI", 10))
        style.configure("TButton", background=COLORS["btn_bg"],
                        foreground=COLORS["text_bright"], font=("Segoe UI", 10, "bold"),
                        padding=(12, 6))
        style.map("TButton",
                   background=[("active", COLORS["btn_hover"]),
                               ("disabled", "#333355")],
                   foreground=[("disabled", "#666688")])
        style.configure("Accent.TButton", background=COLORS["accent"],
                        foreground=COLORS["text_bright"],
                        font=("Segoe UI", 10, "bold"))
        style.map("Accent.TButton",
                   background=[("active", "#ff5a7a"), ("disabled", "#663344")])
        style.configure("Green.TButton", background=COLORS["green"],
                        foreground="#000000",
                        font=("Segoe UI", 10, "bold"))
        style.configure("Small.TButton", padding=(6, 3),
                        font=("Segoe UI", 9))

        # Labels
        style.configure("Title.TLabel", font=("Segoe UI", 16, "bold"),
                        foreground=COLORS["text_bright"], background=COLORS["bg_dark"])
        style.configure("Subtitle.TLabel", font=("Segoe UI", 12, "bold"),
                        foreground=COLORS["accent2"], background=COLORS["bg_dark"])
        style.configure("Card.TLabel", font=("Segoe UI", 10),
                        background=COLORS["bg_card"], foreground=COLORS["text"])
        style.configure("Status.TLabel", font=("Consolas", 10),
                        background=COLORS["bg_mid"], foreground=COLORS["text"])
        style.configure("Big.TLabel", font=("Segoe UI", 22, "bold"),
                        background=COLORS["bg_card"])
        style.configure("Health.TLabel", font=("Segoe UI", 28, "bold"),
                        background=COLORS["bg_card"])

        # Notebook (tabs)
        style.configure("TNotebook", background=COLORS["bg_dark"],
                        borderwidth=0)
        style.configure("TNotebook.Tab", background=COLORS["bg_mid"],
                        foreground=COLORS["text"], padding=(16, 8),
                        font=("Segoe UI", 10))
        style.map("TNotebook.Tab",
                   background=[("selected", COLORS["bg_panel"])],
                   foreground=[("selected", COLORS["accent2"])])

        # Combobox
        style.configure("TCombobox", fieldbackground=COLORS["entry_bg"],
                        background=COLORS["entry_bg"],
                        foreground=COLORS["text"], padding=6)
        style.map("TCombobox",
                   fieldbackground=[("readonly", COLORS["entry_bg"])],
                   selectbackground=[("readonly", COLORS["bg_panel"])])

        # Treeview
        style.configure("Treeview", background=COLORS["tree_bg"],
                        foreground=COLORS["text"], fieldbackground=COLORS["tree_bg"],
                        font=("Consolas", 10), rowheight=24)
        style.configure("Treeview.Heading", background=COLORS["bg_mid"],
                        foreground=COLORS["accent2"], font=("Segoe UI", 10, "bold"))
        style.map("Treeview",
                   background=[("selected", COLORS["tree_select"])],
                   foreground=[("selected", COLORS["text_bright"])])

        # Scrollbar
        style.configure("Vertical.TScrollbar",
                        background=COLORS["bg_mid"],
                        troughcolor=COLORS["bg_dark"],
                        arrowcolor=COLORS["text_dim"])

        # Progressbar
        style.configure("Custom.Horizontal.TProgressbar",
                        background=COLORS["accent2"],
                        troughcolor=COLORS["bg_mid"])

        # Entry
        style.configure("TEntry", fieldbackground=COLORS["entry_bg"],
                        foreground=COLORS["text"], insertcolor=COLORS["text"],
                        padding=6)

        # Separator
        style.configure("TSeparator", background=COLORS["border"])

        # LabelFrame
        style.configure("TLabelframe", background=COLORS["bg_mid"],
                        foreground=COLORS["accent2"],
                        bordercolor=COLORS["border"])
        style.configure("TLabelframe.Label", background=COLORS["bg_mid"],
                        foreground=COLORS["accent2"], font=("Segoe UI", 10, "bold"))

    def _build_menu(self):
        """Build the menu bar."""
        menubar = tk.Menu(self, bg=COLORS["bg_mid"], fg=COLORS["text"],
                          activebackground=COLORS["bg_panel"],
                          activeforeground=COLORS["text_bright"])

        file_menu = tk.Menu(menubar, tearoff=0, bg=COLORS["bg_mid"],
                            fg=COLORS["text"])
        file_menu.add_command(label="Save Report (JSON)...", command=self._save_json,
                              accelerator="Ctrl+S")
        file_menu.add_command(label="Export Log (CSV)...", command=self._save_csv)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.quit)
        menubar.add_cascade(label="File", menu=file_menu)

        tools_menu = tk.Menu(menubar, tearoff=0, bg=COLORS["bg_mid"],
                             fg=COLORS["text"])
        tools_menu.add_command(label="Strap Decoder", command=self._show_strap_decoder)
        tools_menu.add_command(label="Address Calculator", command=self._show_addr_calc)
        menubar.add_cascade(label="Tools", menu=tools_menu)

        help_menu = tk.Menu(menubar, tearoff=0, bg=COLORS["bg_mid"],
                            fg=COLORS["text"])
        help_menu.add_command(label="About", command=self._show_about)
        help_menu.add_command(label="Wiring Guide", command=self._show_wiring_guide)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.config(menu=menubar)
        self.bind_all("<Control-s>", lambda e: self._save_json())

    def _build_ui(self):
        """Build the main UI layout."""
        # Main container
        main = ttk.Frame(self)
        main.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # Top bar: Title + Connection
        self._build_top_bar(main)

        # PanedWindow: Left (devices) + Right (details)
        paned = ttk.PanedWindow(main, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        # Left panel
        left_frame = ttk.Frame(paned, width=420)
        paned.add(left_frame, weight=1)
        self._build_device_panel(left_frame)

        # Right panel (notebook with tabs)
        right_frame = ttk.Frame(paned)
        paned.add(right_frame, weight=2)
        self._build_detail_tabs(right_frame)

        # Bottom: Status bar
        self._build_status_bar(main)

    def _build_top_bar(self, parent):
        """Build the top connection bar."""
        top = ttk.Frame(parent)
        top.pack(fill=tk.X)

        # Title
        ttk.Label(top, text="CD3217B12 Analyzer", style="Title.TLabel").pack(
            side=tk.LEFT, padx=(0, 20))

        # Model selector
        model_frame = ttk.Frame(top)
        model_frame.pack(side=tk.LEFT, padx=(0, 16))
        ttk.Label(model_frame, text="Model:").pack(side=tk.LEFT, padx=(0, 4))
        self.model_var = tk.StringVar(value="Auto-detect")
        model_values = ["Auto-detect"] + [
            f"{m.model_id} - {m.name}" for m in list_models()
        ]
        self.model_combo = ttk.Combobox(
            model_frame, textvariable=self.model_var, width=38,
            values=model_values, state="readonly")
        self.model_combo.pack(side=tk.LEFT)
        self.model_combo.bind("<<ComboboxSelected>>", self._on_model_change)

        # Connection controls
        conn_frame = ttk.Frame(top)
        conn_frame.pack(side=tk.RIGHT, fill=tk.X, expand=True)

        ttk.Label(conn_frame, text="Adapter:").pack(side=tk.LEFT, padx=(0, 4))
        self.adapter_var = tk.StringVar(value="Auto-detect")
        self.adapter_combo = ttk.Combobox(
            conn_frame, textvariable=self.adapter_var, width=18,
            values=["Auto-detect", "FTDI FT232H", "SMBus (Linux)", "CH341"],
            state="readonly")
        self.adapter_combo.pack(side=tk.LEFT, padx=(0, 8))

        ttk.Label(conn_frame, text="Bus:").pack(side=tk.LEFT, padx=(0, 4))
        self.bus_var = tk.StringVar(value="1")
        bus_entry = ttk.Entry(conn_frame, textvariable=self.bus_var, width=4)
        bus_entry.pack(side=tk.LEFT, padx=(0, 8))

        self.connect_btn = ttk.Button(conn_frame, text="Connect",
                                       style="Accent.TButton",
                                       command=self._toggle_connection)
        self.connect_btn.pack(side=tk.LEFT, padx=4)

        self.conn_status = ttk.Label(conn_frame, text="Disconnected",
                                      foreground=COLORS["red"])
        self.conn_status.pack(side=tk.LEFT, padx=8)

    def _build_device_panel(self, parent):
        """Build the left device list panel."""
        # Scan controls
        scan_frame = ttk.Frame(parent)
        scan_frame.pack(fill=tk.X, pady=(0, 8))

        ttk.Button(scan_frame, text="Scan Bus", style="Green.TButton",
                   command=self._scan_bus).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(scan_frame, text="Quick Scan", style="Small.TButton",
                   command=self._quick_scan).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(scan_frame, text="Refresh", style="Small.TButton",
                   command=self._refresh_devices).pack(side=tk.LEFT)

        # Device count
        self.device_count_var = tk.StringVar(value="0 devices")
        ttk.Label(scan_frame, textvariable=self.device_count_var,
                  foreground=COLORS["text_dim"]).pack(side=tk.RIGHT)

        # Device list (Treeview)
        list_frame = ttk.Frame(parent)
        list_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("addr", "health", "score", "mode", "vid", "type")
        self.device_tree = ttk.Treeview(list_frame, columns=columns,
                                         show="headings", selectmode="browse")

        self.device_tree.heading("addr", text="Address")
        self.device_tree.heading("health", text="Health")
        self.device_tree.heading("score", text="Score")
        self.device_tree.heading("mode", text="Mode")
        self.device_tree.heading("vid", text="VID")
        self.device_tree.heading("type", text="Type")

        self.device_tree.column("addr", width=70, stretch=False)
        self.device_tree.column("health", width=60, stretch=False)
        self.device_tree.column("score", width=50, stretch=False)
        self.device_tree.column("mode", width=70, stretch=False)
        self.device_tree.column("vid", width=70, stretch=False)
        self.device_tree.column("type", width=80, stretch=False)

        scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL,
                                command=self.device_tree.yview)
        self.device_tree.configure(yscrollcommand=scroll.set)

        self.device_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.device_tree.bind("<<TreeviewSelect>>", self._on_device_select)

        # Action buttons
        action_frame = ttk.Frame(parent)
        action_frame.pack(fill=tk.X, pady=(8, 0))

        ttk.Button(action_frame, text="Diagnose Selected",
                   command=self._diagnose_selected).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(action_frame, text="Register Dump",
                   command=self._dump_selected).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(action_frame, text="Batch Test",
                   command=self._batch_test).pack(side=tk.LEFT)

        # Quick address entry
        quick_frame = ttk.Frame(parent)
        quick_frame.pack(fill=tk.X, pady=(8, 0))

        ttk.Label(quick_frame, text="Address:").pack(side=tk.LEFT, padx=(0, 4))
        self.quick_addr_var = tk.StringVar(value="0x38")
        ttk.Entry(quick_frame, textvariable=self.quick_addr_var, width=8).pack(
            side=tk.LEFT, padx=(0, 4))
        ttk.Button(quick_frame, text="Diagnose", style="Small.TButton",
                   command=self._diagnose_quick).pack(side=tk.LEFT)

    def _build_detail_tabs(self, parent):
        """Build the right detail tabs."""
        self.notebook = ttk.Notebook(parent)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        # Tab 1: Device Overview
        self._build_overview_tab()

        # Tab 2: Register Dump
        self._build_register_tab()

        # Tab 3: Batch Results
        self._build_batch_tab()

        # Tab 4: Strap Decoder
        self._build_strap_tab()

        # Tab 5: Log
        self._build_log_tab()

        # Tab 6: OTP Scanner
        self._build_otp_tab()

    def _build_overview_tab(self):
        """Build the device overview tab."""
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="  Overview  ")

        # Health score panel (big visual)
        score_frame = tk.Frame(tab, bg=COLORS["bg_card"], bd=1, relief=tk.RIDGE)
        score_frame.pack(fill=tk.X, padx=8, pady=8)

        inner = ttk.Frame(score_frame)
        inner.configure(style="TFrame")
        inner.pack(fill=tk.X, padx=16, pady=12)

        self.health_label = tk.Label(inner, text="--", font=("Segoe UI", 36, "bold"),
                                      bg=COLORS["bg_card"], fg=COLORS["text_dim"])
        self.health_label.pack(side=tk.LEFT, padx=(0, 20))

        right_info = ttk.Frame(inner)
        right_info.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.health_status_label = tk.Label(right_info, text="No device selected",
                                             font=("Segoe UI", 14, "bold"),
                                             bg=COLORS["bg_card"], fg=COLORS["text"])
        self.health_status_label.pack(anchor=tk.W)

        self.health_detail_label = tk.Label(right_info,
                                             text="Select a device and run diagnosis",
                                             font=("Segoe UI", 10),
                                             bg=COLORS["bg_card"],
                                             fg=COLORS["text_dim"])
        self.health_detail_label.pack(anchor=tk.W)

        # Device info grid
        info_frame = ttk.LabelFrame(tab, text="Device Information")
        info_frame.pack(fill=tk.X, padx=8, pady=(0, 8))

        self.info_labels = {}
        fields = [
            ("Address", "address"),
            ("Vendor ID", "vid"),
            ("Device ID", "did"),
            ("Mode", "mode"),
            ("Type", "type"),
            ("Response Time", "time"),
            ("Chip Type", "chip_type"),
        ]

        for i, (label_text, key) in enumerate(fields):
            ttk.Label(info_frame, text=f"{label_text}:",
                      foreground=COLORS["text_dim"]).grid(
                row=i, column=0, sticky=tk.W, padx=8, pady=3)
            lbl = ttk.Label(info_frame, text="--", font=("Consolas", 10))
            lbl.grid(row=i, column=1, sticky=tk.W, padx=8, pady=3)
            self.info_labels[key] = lbl

        info_frame.columnconfigure(1, weight=1)

        # Faults panel
        faults_frame = ttk.LabelFrame(tab, text="Faults & Diagnostics")
        faults_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        self.faults_text = scrolledtext.ScrolledText(
            faults_frame, height=6, bg=COLORS["entry_bg"],
            fg=COLORS["text"], font=("Consolas", 10),
            insertbackground=COLORS["text"], borderwidth=0,
            state=tk.DISABLED)
        self.faults_text.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # Configure text tags for colored output
        self.faults_text.tag_configure("pass", foreground=COLORS["pass"])
        self.faults_text.tag_configure("warn", foreground=COLORS["warn"])
        self.faults_text.tag_configure("fail", foreground=COLORS["fail"])
        self.faults_text.tag_configure("info", foreground=COLORS["accent2"])
        self.faults_text.tag_configure("bold", font=("Consolas", 10, "bold"))

    def _build_register_tab(self):
        """Build the register dump tab."""
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="  Registers  ")

        # Toolbar
        toolbar = ttk.Frame(tab)
        toolbar.pack(fill=tk.X, padx=8, pady=4)

        ttk.Button(toolbar, text="Read All Registers",
                   command=self._read_registers).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(toolbar, text="Copy to Clipboard",
                   style="Small.TButton",
                   command=self._copy_registers).pack(side=tk.LEFT)

        self.reg_addr_var = tk.StringVar(value="0x38")
        ttk.Label(toolbar, text="Device:").pack(side=tk.LEFT, padx=(16, 4))
        ttk.Entry(toolbar, textvariable=self.reg_addr_var, width=8).pack(
            side=tk.LEFT)

        # Register table
        reg_frame = ttk.Frame(tab)
        reg_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        columns = ("offset", "name", "hex", "value", "decoded")
        self.reg_tree = ttk.Treeview(reg_frame, columns=columns,
                                      show="headings", selectmode="browse")

        self.reg_tree.heading("offset", text="Offset")
        self.reg_tree.heading("name", text="Name")
        self.reg_tree.heading("hex", text="Raw (hex)")
        self.reg_tree.heading("value", text="Value")
        self.reg_tree.heading("decoded", text="Decoded")

        self.reg_tree.column("offset", width=65, stretch=False)
        self.reg_tree.column("name", width=140, stretch=False)
        self.reg_tree.column("hex", width=180, stretch=False)
        self.reg_tree.column("value", width=100, stretch=False)
        self.reg_tree.column("decoded", width=200, stretch=True)

        reg_scroll = ttk.Scrollbar(reg_frame, orient=tk.VERTICAL,
                                    command=self.reg_tree.yview)
        self.reg_tree.configure(yscrollcommand=reg_scroll.set)

        self.reg_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        reg_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    def _build_batch_tab(self):
        """Build the batch testing tab."""
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="  Batch Test  ")

        # Controls
        ctrl = ttk.Frame(tab)
        ctrl.pack(fill=tk.X, padx=8, pady=8)

        ttk.Label(ctrl, text="Iterations:").pack(side=tk.LEFT, padx=(0, 4))
        self.batch_count_var = tk.StringVar(value="5")
        ttk.Entry(ctrl, textvariable=self.batch_count_var, width=6).pack(
            side=tk.LEFT, padx=(0, 12))

        ttk.Label(ctrl, text="Device(s):").pack(side=tk.LEFT, padx=(0, 4))
        self.batch_addr_var = tk.StringVar(value="0x38,0x3F")
        ttk.Entry(ctrl, textvariable=self.batch_addr_var, width=20).pack(
            side=tk.LEFT, padx=(0, 12))

        self.batch_start_btn = ttk.Button(ctrl, text="Start Batch Test",
                                           style="Green.TButton",
                                           command=self._start_batch)
        self.batch_start_btn.pack(side=tk.LEFT, padx=(0, 4))

        ttk.Button(ctrl, text="Export CSV",
                   style="Small.TButton",
                   command=self._save_csv).pack(side=tk.LEFT)

        # Progress
        self.batch_progress = ttk.Progressbar(
            tab, style="Custom.Horizontal.TProgressbar",
            mode="determinate")
        self.batch_progress.pack(fill=tk.X, padx=8, pady=(0, 4))

        self.batch_status_var = tk.StringVar(value="Ready")
        ttk.Label(tab, textvariable=self.batch_status_var,
                  foreground=COLORS["text_dim"]).pack(padx=8, anchor=tk.W)

        # Results table
        batch_frame = ttk.Frame(tab)
        batch_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        columns = ("iter", "addr", "health", "score", "mode", "faults", "time")
        self.batch_tree = ttk.Treeview(batch_frame, columns=columns,
                                        show="headings", selectmode="browse")

        self.batch_tree.heading("iter", text="#")
        self.batch_tree.heading("addr", text="Address")
        self.batch_tree.heading("health", text="Health")
        self.batch_tree.heading("score", text="Score")
        self.batch_tree.heading("mode", text="Mode")
        self.batch_tree.heading("faults", text="Faults")
        self.batch_tree.heading("time", text="Time (ms)")

        self.batch_tree.column("iter", width=40, stretch=False)
        self.batch_tree.column("addr", width=70, stretch=False)
        self.batch_tree.column("health", width=60, stretch=False)
        self.batch_tree.column("score", width=55, stretch=False)
        self.batch_tree.column("mode", width=70, stretch=False)
        self.batch_tree.column("faults", width=200, stretch=True)
        self.batch_tree.column("time", width=80, stretch=False)

        batch_scroll = ttk.Scrollbar(batch_frame, orient=tk.VERTICAL,
                                      command=self.batch_tree.yview)
        self.batch_tree.configure(yscrollcommand=batch_scroll.set)

        self.batch_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        batch_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.batch_results: List[DeviceResult] = []

    def _build_strap_tab(self):
        """Build the strap decoder tab."""
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="  Strap Decoder  ")

        ttk.Label(tab, text="ACE2 I2C Address Strap Configuration Calculator",
                  style="Subtitle.TLabel").pack(padx=16, pady=(16, 8), anchor=tk.W)

        ttk.Label(tab, text=(
            "Enter the desired Port 1 and Port 2 addresses to compute\n"
            "the ADDR, CNTL1, and CNTL2 resistor configuration."
        ), foreground=COLORS["text_dim"]).pack(padx=16, anchor=tk.W)

        # Input frame
        inp = ttk.LabelFrame(tab, text="Address Configuration")
        inp.pack(fill=tk.X, padx=16, pady=8)

        ttk.Label(inp, text="Port 1 Address (hex):").grid(
            row=0, column=0, sticky=tk.W, padx=8, pady=6)
        self.strap_p1_var = tk.StringVar(value="0x38")
        ttk.Entry(inp, textvariable=self.strap_p1_var, width=10).grid(
            row=0, column=1, sticky=tk.W, padx=8, pady=6)

        ttk.Label(inp, text="Port 2 Address (hex):").grid(
            row=1, column=0, sticky=tk.W, padx=8, pady=6)
        self.strap_p2_var = tk.StringVar(value="0x38")
        ttk.Entry(inp, textvariable=self.strap_p2_var, width=10).grid(
            row=1, column=1, sticky=tk.W, padx=8, pady=6)

        ttk.Button(inp, text="Calculate", style="Accent.TButton",
                   command=self._calc_straps).grid(
            row=0, column=2, rowspan=2, padx=16, pady=6)

        # Result frame
        self.strap_result_frame = ttk.LabelFrame(tab, text="Configuration")
        self.strap_result_frame.pack(fill=tk.X, padx=16, pady=8)

        self.strap_result_labels = {}
        fields = [
            ("ADDR Pin:", "addr_bits"),
            ("  Resistor:", "addr_resistor"),
            ("CNTL1 Pin:", "cntl1"),
            ("  Source:", "cntl1_source"),
            ("CNTL2 Pin:", "cntl2"),
            ("  Source:", "cntl2_source"),
            ("Port 1 Address:", "port1_addr"),
            ("Port 2 Address:", "port2_addr"),
        ]

        for i, (label_text, key) in enumerate(fields):
            ttk.Label(self.strap_result_frame, text=label_text,
                      foreground=COLORS["text_dim"]).grid(
                row=i, column=0, sticky=tk.W, padx=8, pady=2)
            lbl = ttk.Label(self.strap_result_frame, text="--",
                            font=("Consolas", 10))
            lbl.grid(row=i, column=1, sticky=tk.W, padx=8, pady=2)
            self.strap_result_labels[key] = lbl

        # Common configs reference
        self.strap_ref_frame = ttk.LabelFrame(tab, text="Common Addresses")
        self.strap_ref_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 8))

        self.strap_ref_tree = ttk.Treeview(
            self.strap_ref_frame,
            columns=("pos", "p1", "p2", "type", "port"),
            show="headings", height=6)
        self.strap_ref_tree.heading("pos", text="Position")
        self.strap_ref_tree.heading("p1", text="Port 1")
        self.strap_ref_tree.heading("p2", text="Port 2")
        self.strap_ref_tree.heading("type", text="Type")
        self.strap_ref_tree.heading("port", text="Port #")
        self.strap_ref_tree.column("pos", width=140)
        self.strap_ref_tree.column("p1", width=80)
        self.strap_ref_tree.column("p2", width=80)
        self.strap_ref_tree.column("type", width=80)
        self.strap_ref_tree.column("port", width=60)

        self.strap_ref_tree.pack(fill=tk.X, padx=4, pady=4)

        # Populate with default data
        self._update_strap_reference()

    def _build_log_tab(self):
        """Build the log tab."""
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="  Log  ")

        toolbar = ttk.Frame(tab)
        toolbar.pack(fill=tk.X, padx=8, pady=4)

        ttk.Button(toolbar, text="Clear Log",
                   style="Small.TButton",
                   command=self._clear_log).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="Copy All",
                   style="Small.TButton",
                   command=self._copy_log).pack(side=tk.LEFT, padx=(4, 0))

        self.log_text = scrolledtext.ScrolledText(
            tab, bg=COLORS["entry_bg"], fg=COLORS["text"],
            font=("Consolas", 10), insertbackground=COLORS["text"],
            borderwidth=0, state=tk.NORMAL)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        # Log text tags
        self.log_text.tag_configure("time", foreground=COLORS["text_dim"])
        self.log_text.tag_configure("info", foreground=COLORS["accent2"])
        self.log_text.tag_configure("ok", foreground=COLORS["pass"])
        self.log_text.tag_configure("warn", foreground=COLORS["warn"])
        self.log_text.tag_configure("err", foreground=COLORS["fail"])
        self.log_text.tag_configure("cmd", foreground=COLORS["orange"])

    def _build_otp_tab(self):
        """Build the OTP Scanner tab for reverse engineering OTP fuse maps."""
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="  OTP Scanner  ")

        ttk.Label(tab, text="OTP Memory Scanner & Diff Tool",
                  style="Subtitle.TLabel").pack(padx=16, pady=(12, 4), anchor=tk.W)
        ttk.Label(tab, text=(
            "Read the full register space (0x00-0x7F) from a CD3217B12.\n"
            "Compare vanilla vs OTP-ed chips to identify OTP-backed registers."
        ), foreground=COLORS["text_dim"]).pack(padx=16, anchor=tk.W)

        # Controls
        ctrl = ttk.Frame(tab)
        ctrl.pack(fill=tk.X, padx=16, pady=8)

        ttk.Label(ctrl, text="Device:").pack(side=tk.LEFT, padx=(0, 4))
        self.otp_addr_var = tk.StringVar(value="0x38")
        ttk.Entry(ctrl, textvariable=self.otp_addr_var, width=8).pack(
            side=tk.LEFT, padx=(0, 8))

        self.otp_scan_btn = ttk.Button(ctrl, text="Scan OTP",
                                        style="Green.TButton",
                                        command=self._otp_scan_device)
        self.otp_scan_btn.pack(side=tk.LEFT, padx=(0, 4))

        ttk.Button(ctrl, text="Import Dump",
                   style="Small.TButton",
                   command=self._otp_import_file).pack(side=tk.LEFT, padx=(0, 4))

        ttk.Button(ctrl, text="Diff Two Dumps",
                   style="Small.TButton",
                   command=self._otp_diff_dialog).pack(side=tk.LEFT, padx=(0, 4))

        # Dump A / B selection
        dump_frame = ttk.Frame(tab)
        dump_frame.pack(fill=tk.X, padx=16, pady=(0, 4))

        self.otp_dump_a_var = tk.StringVar(value="")
        self.otp_dump_b_var = tk.StringVar(value="")

        ttk.Label(dump_frame, text="Dump A (vanilla/empty):",
                  foreground=COLORS["text_dim"]).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Entry(dump_frame, textvariable=self.otp_dump_a_var, width=30).pack(
            side=tk.LEFT, padx=(0, 8))

        ttk.Label(dump_frame, text="Dump B (OTP-ed):",
                  foreground=COLORS["text_dim"]).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Entry(dump_frame, textvariable=self.otp_dump_b_var, width=30).pack(
            side=tk.LEFT)

        # Progress bar
        self.otp_progress = ttk.Progressbar(
            tab, style="Custom.Horizontal.TProgressbar",
            mode="determinate")
        self.otp_progress.pack(fill=tk.X, padx=16, pady=(0, 4))

        self.otp_status_var = tk.StringVar(value="Ready")
        ttk.Label(tab, textvariable=self.otp_status_var,
                  foreground=COLORS["text_dim"]).pack(padx=16, anchor=tk.W)

        # Split view: Dump A | Diff
        paned = ttk.PanedWindow(tab, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=16, pady=(4, 8))

        # Dump A view
        left = ttk.Frame(paned)
        paned.add(left, weight=1)
        ttk.Label(left, text="Current Dump", style="Subtitle.TLabel").pack(
            anchor=tk.W, pady=(4, 2))

        self.otp_dump_text = scrolledtext.ScrolledText(
            left, bg=COLORS["entry_bg"], fg=COLORS["text"],
            font=("Consolas", 9), insertbackground=COLORS["text"],
            borderwidth=0, state=tk.DISABLED)
        self.otp_dump_text.pack(fill=tk.BOTH, expand=True)

        # Diff result view
        right = ttk.Frame(paned)
        paned.add(right, weight=1)
        ttk.Label(right, text="Diff Result", style="Subtitle.TLabel").pack(
            anchor=tk.W, pady=(4, 2))

        self.otp_diff_text = scrolledtext.ScrolledText(
            right, bg=COLORS["entry_bg"], fg=COLORS["text"],
            font=("Consolas", 9), insertbackground=COLORS["text"],
            borderwidth=0, state=tk.DISABLED)
        self.otp_diff_text.pack(fill=tk.BOTH, expand=True)

        # Color tags for diff output
        for widget in (self.otp_dump_text, self.otp_diff_text):
            widget.tag_configure("pass", foreground=COLORS["pass"])
            widget.tag_configure("warn", foreground=COLORS["warn"])
            widget.tag_configure("fail", foreground=COLORS["fail"])
            widget.tag_configure("info", foreground=COLORS["accent2"])
            widget.tag_configure("bold", font=("Consolas", 9, "bold"))
            widget.tag_configure("dim", foreground=COLORS["text_dim"])

        # Store dumps
        self.otp_current_dump: Optional[OTPDump] = None

    def _build_status_bar(self, parent):
        """Build the bottom status bar."""
        status = tk.Frame(parent, bg=COLORS["bg_mid"], height=28)
        status.pack(fill=tk.X, pady=(8, 0))

        self.status_left = tk.Label(status, text="Ready",
                                     bg=COLORS["bg_mid"], fg=COLORS["text_dim"],
                                     font=("Segoe UI", 9))
        self.status_left.pack(side=tk.LEFT, padx=8)

        self.status_right = tk.Label(status, text="v1.0.0",
                                      bg=COLORS["bg_mid"], fg=COLORS["text_dim"],
                                      font=("Segoe UI", 9))
        self.status_right.pack(side=tk.RIGHT, padx=8)

    # ─── Logging ──────────────────────────────────────────────────────────

    def _on_model_change(self, event=None):
        """Handle model selector change."""
        sel = self.model_var.get()
        if sel == "Auto-detect":
            self.current_model = None
            self.log("Model: Auto-detect (generic scan)", "info")
        else:
            model_id = sel.split(" - ")[0].strip()
            self.current_model = get_model(model_id)
            if self.current_model:
                self.log(f"Model: {self.current_model.name} ({self.current_model.board_id})",
                         "info")
                self._update_strap_reference()
                self._update_batch_addresses()

    def _update_strap_reference(self):
        """Update the strap decoder reference table for the selected model."""
        # Clear existing ref tree
        for item in self.strap_ref_tree.get_children():
            self.strap_ref_tree.delete(item)

        if self.current_model:
            title = f"Common Addresses ({self.current_model.model_id})"
            for pos in self.current_model.positions:
                p1 = f"0x{pos.address:02X}"
                p2 = f"0x{pos.address:02X}"
                tag = pos.addressing.capitalize()
                self.strap_ref_tree.insert("", tk.END, values=(
                    pos.ref, p1, p2, tag, f"Port {pos.i2c_port}"))
        else:
            title = "Common Addresses"
            defaults = [
                ("UF400 (UPC0)", "0x38", "0x38", "Vanilla", "1"),
                ("UF500 (UPC1)", "0x3F", "0x3F", "Vanilla", "1"),
                ("UB300 (UPC2)", "0x20", "0x20", "OTP", "1"),
                ("UB400 (UPC3)", "0x74", "0x74", "OTP", "1"),
                ("UF500 (UPC4)", "0x39", "0x39", "Strap", "2"),
                ("UF600 (UPC5)", "0x10", "0x10", "Strap", "2"),
            ]
            for vals in defaults:
                self.strap_ref_tree.insert("", tk.END, values=vals)

        # Update label frame text
        self.strap_ref_frame.configure(text=title)

    def _update_batch_addresses(self):
        """Update batch address field based on selected model."""
        if self.current_model and self.current_model.positions:
            addrs = [f"0x{p.address:02X}" for p in self.current_model.positions]
            self.batch_addr_var.set(",".join(addrs))

    def log(self, msg: str, level: str = "info"):
        """Add a message to the log."""
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{ts}] ", "time")
        self.log_text.insert(tk.END, f"{msg}\n", level)
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.NORMAL)

    def _clear_log(self):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)

    def _copy_log(self):
        self.clipboard_clear()
        self.clipboard_append(self.log_text.get("1.0", tk.END))
        self.log("Log copied to clipboard", "ok")

    # ─── Connection ───────────────────────────────────────────────────────

    def _auto_detect_adapter(self):
        """Auto-detect I2C adapter on startup."""
        self.log("Scanning for I2C adapters...")
        adapter = detect_adapter()
        if adapter:
            self.adapter = adapter
            self.connected = True
            self._update_connection_status(True)
            self.log(f"Auto-detected: {type(adapter).__name__}", "ok")
        else:
            self.log("No I2C adapter found. Select adapter and click Connect.", "warn")

    def _toggle_connection(self):
        """Connect or disconnect the I2C adapter."""
        if self.connected:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        """Connect to selected adapter."""
        selection = self.adapter_var.get()
        self.log(f"Connecting to {selection}...")

        try:
            if selection == "Auto-detect":
                adapter = detect_adapter()
            elif selection == "FTDI FT232H":
                adapter = FTDIAdapter()
            elif selection in ("SMBus (Linux)", "CH341"):
                bus = int(self.bus_var.get())
                adapter = SMBusAdapter(bus_number=bus)
            else:
                self.log(f"Unknown adapter: {selection}", "err")
                return

            if adapter is None:
                self.log("Could not connect to adapter", "err")
                messagebox.showerror("Connection Error",
                                     "Could not connect to I2C adapter.\n\n"
                                     "Check that:\n"
                                     "- Adapter is plugged in\n"
                                     "- Drivers are installed\n"
                                     "- Correct adapter type selected")
                return

            adapter.open()
            self.adapter = adapter
            self.connected = True
            self._update_connection_status(True)
            self.log(f"Connected to {type(adapter).__name__}", "ok")

        except Exception as e:
            self.log(f"Connection failed: {e}", "err")
            messagebox.showerror("Connection Error", str(e))

    def _disconnect(self):
        """Disconnect the adapter."""
        if self.adapter:
            self.adapter.close()
            self.adapter = None
        self.connected = False
        self.devices.clear()
        self.scan_results.clear()
        self._update_connection_status(False)
        self._clear_device_tree()
        self.log("Disconnected")

    def _update_connection_status(self, connected: bool):
        """Update the connection status indicator."""
        if connected:
            self.conn_status.configure(text="Connected", foreground=COLORS["pass"])
            self.connect_btn.configure(text="Disconnect")
        else:
            self.conn_status.configure(text="Disconnected", foreground=COLORS["red"])
            self.connect_btn.configure(text="Connect")

    # ─── Bus Scanning ─────────────────────────────────────────────────────

    def _scan_bus(self):
        """Scan the full I2C bus."""
        if not self._check_connected():
            return

        self.log("Scanning I2C bus (0x08 - 0x77)...")
        self.status_left.configure(text="Scanning bus...")

        def do_scan():
            try:
                devices = self.adapter.scan(0x08, 0x77)
                self.scan_results = devices
                self.after(0, self._display_scan_results, devices)
            except Exception as e:
                self.after(0, self.log, f"Scan error: {e}", "err")
                self.after(0, self.status_left.configure,
                           {"text": "Scan failed"})

        threading.Thread(target=do_scan, daemon=True).start()

    def _quick_scan(self):
        """Quick scan of known ACE2 addresses."""
        if not self._check_connected():
            return

        self.log("Quick scanning known ACE2 addresses...")

        def do_quick():
            try:
                found = []
                for addr in KNOWN_ACE2_ADDRESSES:
                    if self.adapter.ping(addr):
                        found.append(addr)
                self.scan_results = found
                self.after(0, self._display_scan_results, found)
            except Exception as e:
                self.after(0, self.log, f"Quick scan error: {e}", "err")

        threading.Thread(target=do_quick, daemon=True).start()

    def _display_scan_results(self, devices: List[int]):
        """Display scan results in the device tree."""
        self._clear_device_tree()

        if not devices:
            self.log("No devices found on I2C bus", "warn")
            self.device_count_var.set("0 devices")
            self.status_left.configure(text="Scan complete - no devices")
            return

        self.log(f"Found {len(devices)} device(s)", "ok")
        self.device_count_var.set(f"{len(devices)} device(s)")

        for addr in devices:
            is_ace2 = is_ace2_address(addr)
            tag = "ace2" if is_ace2 else "other"
            desc = KNOWN_ACE2_ADDRESSES.get(addr, "")

            self.device_tree.insert("", tk.END, values=(
                f"0x{addr:02X}", "?", "?", "?", "?", desc[:20]
            ), tags=(tag,))

        self.device_tree.tag_configure("ace2", foreground=COLORS["accent2"])
        self.device_tree.tag_configure("other", foreground=COLORS["text_dim"])

        self.status_left.configure(text=f"Scan complete - {len(devices)} device(s)")

    def _clear_device_tree(self):
        for item in self.device_tree.get_children():
            self.device_tree.delete(item)

    def _refresh_devices(self):
        """Re-diagnose all known devices."""
        if not self._check_connected():
            return

        if not self.scan_results:
            self.log("No devices to refresh. Run a scan first.", "warn")
            return

        self.log("Refreshing all devices...")

        def do_refresh():
            analyzer = CD3217Analyzer(self.adapter)
            for addr in self.scan_results:
                try:
                    result = analyzer.diagnose_device(addr)
                    self.devices[addr] = result
                except Exception as e:
                    self.log(f"Error diagnosing 0x{addr:02X}: {e}", "err")
            self.after(0, self._update_device_tree_display)

        threading.Thread(target=do_refresh, daemon=True).start()

    def _update_device_tree_display(self):
        """Update device tree with diagnosis results."""
        self._clear_device_tree()

        for addr, dev in sorted(self.devices.items()):
            health_str = dev.health.value
            mode_str = dev.mode or "--"
            vid_str = dev.vendor_id or "--"
            chip_type = "Vanilla" if dev.is_vanilla else (
                "OTP" if dev.is_vanilla is False else "?")

            tag = "pass" if dev.health == HealthStatus.PASS else \
                  "warn" if dev.health == HealthStatus.WARN else "fail"

            self.device_tree.insert("", tk.END, values=(
                f"0x{addr:02X}", health_str, str(dev.health_score),
                mode_str, vid_str, chip_type
            ), tags=(tag,))

        self.device_tree.tag_configure("pass", foreground=COLORS["pass"])
        self.device_tree.tag_configure("warn", foreground=COLORS["warn"])
        self.device_tree.tag_configure("fail", foreground=COLORS["fail"])

    # ─── Device Selection & Diagnosis ─────────────────────────────────────

    def _on_device_select(self, event):
        """Handle device selection in the tree."""
        selection = self.device_tree.selection()
        if not selection:
            return

        item = self.device_tree.item(selection[0])
        addr_str = item["values"][0]
        self.selected_address = int(addr_str, 16)
        self.quick_addr_var.set(addr_str)
        self.reg_addr_var.set(addr_str)

    def _diagnose_selected(self):
        """Diagnose the currently selected device."""
        if not self._check_connected():
            return

        selection = self.device_tree.selection()
        if not selection:
            messagebox.showinfo("No Selection", "Select a device from the list first.")
            return

        item = self.device_tree.item(selection[0])
        addr_str = item["values"][0]
        addr = int(addr_str, 16)
        self._diagnose_address(addr)

    def _diagnose_quick(self):
        """Diagnose the device at the quick address entry."""
        if not self._check_connected():
            return

        addr_str = self.quick_addr_var.get().strip()
        try:
            addr = int(addr_str, 16) if addr_str.startswith("0x") else int(addr_str)
        except ValueError:
            messagebox.showerror("Invalid Address", f"Cannot parse address: {addr_str}")
            return

        self._diagnose_address(addr)

    def _diagnose_address(self, address: int):
        """Run full diagnosis on a specific address."""
        self.log(f"Diagnosing 0x{address:02X}...")
        self.status_left.configure(text=f"Diagnosing 0x{address:02X}...")

        def do_diagnose():
            try:
                analyzer = CD3217Analyzer(self.adapter)
                result = analyzer.diagnose_device(address)
                self.devices[address] = result
                self.after(0, self._show_device_result, result)
                self.after(0, self._update_device_tree_display)
            except Exception as e:
                self.after(0, self.log, f"Diagnosis error: {e}", "err")
                self.after(0, self.status_left.configure,
                           {"text": "Diagnosis failed"})

        threading.Thread(target=do_diagnose, daemon=True).start()

    def _show_device_result(self, result: DeviceResult):
        """Display diagnosis result in the overview tab."""
        # Health score display
        score = result.health_score
        if result.health == HealthStatus.PASS:
            color = COLORS["pass"]
            status = "HEALTHY"
        elif result.health == HealthStatus.WARN:
            color = COLORS["warn"]
            status = "WARNING"
        elif result.health == HealthStatus.FAIL:
            color = COLORS["fail"]
            status = "FAULTY"
        else:
            color = COLORS["text_dim"]
            status = "UNKNOWN"

        self.health_label.configure(text=str(score), fg=color)
        self.health_status_label.configure(text=status, fg=color)

        detail_parts = []
        if result.faults:
            detail_parts.append(f"{len(result.faults)} fault(s)")
        if result.scan_time_ms:
            detail_parts.append(f"{result.scan_time_ms:.0f}ms response")
        self.health_detail_label.configure(
            text=" | ".join(detail_parts) if detail_parts else "All checks passed")

        # Info labels
        self.info_labels["address"].configure(text=f"0x{result.address:02X}")
        self.info_labels["vid"].configure(text=result.vendor_id or "N/A")
        self.info_labels["did"].configure(text=result.device_id or "N/A")
        self.info_labels["mode"].configure(text=result.mode or "N/A")
        self.info_labels["type"].configure(text=result.device_type or "N/A")
        self.info_labels["time"].configure(text=f"{result.scan_time_ms:.1f} ms")

        # Identify chip type based on address
        OTP_ADDRESSES = {0x3A, 0x3B, 0x3C, 0x74, 0x76, 0x78, 0x79}
        VANILLA_ADDRESSES = {0x38, 0x3F, 0x2F, 0x28}
        if result.address in OTP_ADDRESSES:
            chip_type = "OTP-ed (Apple address)"
        elif result.address in VANILLA_ADDRESSES:
            chip_type = "Likely vanilla"
        else:
            chip_type = "Unknown type"
        self.info_labels["chip_type"].configure(text=chip_type or "Unknown")

        # Faults text
        self.faults_text.configure(state=tk.NORMAL)
        self.faults_text.delete("1.0", tk.END)

        if not result.faults:
            self.faults_text.insert(tk.END, "All checks passed\n", "pass")
            self.faults_text.insert(tk.END, "\nDevice is responding correctly on I2C.\n")
            self.faults_text.insert(tk.END, "Vendor ID matches Texas Instruments.\n")
            if result.mode and "APP" in result.mode.upper():
                self.faults_text.insert(tk.END, "Device is in Application mode.\n")
        else:
            self.faults_text.insert(tk.END, f"FAULTS DETECTED: {len(result.faults)}\n\n", "fail")
            for fault in result.faults:
                self.faults_text.insert(tk.END, f"  [{fault.value}]\n", "fail")
            if result.fault_details:
                self.faults_text.insert(tk.END, "\nDetails:\n", "bold")
                for detail in result.fault_details:
                    self.faults_text.insert(tk.END, f"  - {detail}\n")

        self.faults_text.configure(state=tk.DISABLED)

        # Switch to overview tab
        self.notebook.select(0)

        self.log(f"Diagnosis complete: 0x{result.address:02X} = {result.health.value} "
                 f"(score {result.health_score})", "ok" if result.health == HealthStatus.PASS
                 else "warn" if result.health == HealthStatus.WARN else "err")
        self.status_left.configure(
            text=f"0x{result.address:02X}: {result.health.value} ({result.health_score}/100)")

    # ─── Register Dump ────────────────────────────────────────────────────

    def _dump_selected(self):
        """Register dump of selected device."""
        if not self._check_connected():
            return

        selection = self.device_tree.selection()
        if selection:
            item = self.device_tree.item(selection[0])
            addr_str = item["values"][0]
            self.reg_addr_var.set(addr_str)

        self._read_registers()

    def _read_registers(self):
        """Read all registers from the configured device."""
        if not self._check_connected():
            return

        addr_str = self.reg_addr_var.get().strip()
        try:
            addr = int(addr_str, 16) if addr_str.startswith("0x") else int(addr_str)
        except ValueError:
            messagebox.showerror("Invalid Address", f"Cannot parse address: {addr_str}")
            return

        self.log(f"Reading registers from 0x{addr:02X}...")

        # Clear existing entries
        for item in self.reg_tree.get_children():
            self.reg_tree.delete(item)

        def do_read():
            try:
                analyzer = CD3217Analyzer(self.adapter)
                for offset in sorted(REGISTERS.keys()):
                    reg_def = REGISTERS[offset]
                    read = analyzer.read_register(addr, offset, reg_def.length)
                    if read:
                        hex_str = read.raw_bytes.hex()
                        decoded = read.decoded or f"0x{read.raw_value:X}"
                        self.after(0, self.reg_tree.insert, "", tk.END, {
                            "values": (
                                f"0x{offset:02X}", read.name,
                                hex_str, f"0x{read.raw_value:X}",
                                decoded
                            )
                        })
                    else:
                        self.after(0, self.reg_tree.insert, "", tk.END, {
                            "values": (
                                f"0x{offset:02X}", reg_def.name,
                                "ERROR", "--", "Read failed"
                            )
                        })
                self.after(0, self.log,
                           f"Register dump complete for 0x{addr:02X}", "ok")
            except Exception as e:
                self.after(0, self.log, f"Register read error: {e}", "err")

        threading.Thread(target=do_read, daemon=True).start()

    def _copy_registers(self):
        """Copy register dump to clipboard."""
        lines = []
        for item in self.reg_tree.get_children():
            vals = self.reg_tree.item(item)["values"]
            lines.append(f"[{vals[0]}] {vals[1]:20s} = {vals[2]:32s} ({vals[4]})")

        self.clipboard_clear()
        self.clipboard_append("\n".join(lines))
        self.log("Register dump copied to clipboard", "ok")

    # ─── Batch Testing ────────────────────────────────────────────────────

    def _batch_test(self):
        """Switch to batch tab and prepare."""
        self.notebook.select(2)  # Batch tab

    def _start_batch(self):
        """Start batch testing."""
        if not self._check_connected():
            return

        try:
            count = int(self.batch_count_var.get())
        except ValueError:
            messagebox.showerror("Invalid Count", "Enter a valid number of iterations")
            return

        addr_str = self.batch_addr_var.get().strip()
        try:
            addrs = []
            for part in addr_str.split(","):
                part = part.strip()
                a = int(part, 16) if part.startswith("0x") else int(part)
                addrs.append(a)
        except ValueError:
            messagebox.showerror("Invalid Address", f"Cannot parse: {addr_str}")
            return

        # Clear previous results
        for item in self.batch_tree.get_children():
            self.batch_tree.delete(item)
        self.batch_results.clear()
        self.batch_progress["value"] = 0
        self.batch_progress["maximum"] = count * len(addrs)
        self.batch_start_btn.configure(state=tk.DISABLED)

        self.log(f"Starting batch test: {count} iterations x {len(addrs)} device(s)",
                 "cmd")

        def do_batch():
            analyzer = CD3217Analyzer(self.adapter, addresses=addrs)
            total = count * len(addrs)
            done = 0

            for i in range(count):
                for addr in addrs:
                    try:
                        result = analyzer.diagnose_device(addr)
                        self.batch_results.append(result)

                        fault_str = "; ".join(f.value for f in result.faults) if result.faults else ""

                        self.after(0, self.batch_tree.insert, "", tk.END, {
                            "values": (
                                i + 1, f"0x{addr:02X}",
                                result.health.value, result.health_score,
                                result.mode or "--", fault_str,
                                f"{result.scan_time_ms:.0f}"
                            ),
                            "tags": (result.health.value.lower(),)
                        })

                        done += 1
                        self.after(0, self.batch_progress.configure,
                                   {"value": done})
                        self.after(0, self.batch_status_var.set,
                                   f"Completed {done}/{total}")

                    except Exception as e:
                        self.after(0, self.log,
                                   f"Batch error on 0x{addr:02X}: {e}", "err")
                        done += 1
                        self.after(0, self.batch_progress.configure,
                                   {"value": done})

            self.after(0, self._batch_complete)

        threading.Thread(target=do_batch, daemon=True).start()

    def _batch_complete(self):
        """Called when batch test finishes."""
        self.batch_start_btn.configure(state=tk.NORMAL)
        self.batch_tree.tag_configure("pass", foreground=COLORS["pass"])
        self.batch_tree.tag_configure("warn", foreground=COLORS["warn"])
        self.batch_tree.tag_configure("fail", foreground=COLORS["fail"])

        total = len(self.batch_results)
        passed = sum(1 for r in self.batch_results if r.health == HealthStatus.PASS)
        failed = sum(1 for r in self.batch_results if r.health == HealthStatus.FAIL)

        self.batch_status_var.set(
            f"Complete: {total} tests | {passed} pass | {failed} fail")
        self.log(f"Batch complete: {total} tests | {passed} pass | {failed} fail",
                 "ok" if failed == 0 else "warn")

    # ─── Strap Decoder ────────────────────────────────────────────────────

    def _show_strap_decoder(self):
        """Switch to strap decoder tab."""
        self.notebook.select(3)

    def _calc_straps(self):
        """Calculate strap configuration."""
        p1_str = self.strap_p1_var.get().strip()
        p2_str = self.strap_p2_var.get().strip()

        try:
            p1 = int(p1_str, 16) if p1_str.startswith("0x") else int(p1_str)
            p2 = int(p2_str, 16) if p2_str.startswith("0x") else int(p2_str)
        except ValueError:
            messagebox.showerror("Invalid Address", "Enter valid hex addresses")
            return

        info = decode_i2c_address_straps(p1, p2)

        for key, value in info.items():
            if key in self.strap_result_labels:
                self.strap_result_labels[key].configure(text=str(value))

        self.log(f"Strap decode: Port1=0x{p1:02X} Port2=0x{p2:02X}", "info")

    def _show_addr_calc(self):
        """Show address calculator dialog."""
        self._show_strap_decoder()

    # ─── OTP Scanner ─────────────────────────────────────────────────────

    def _otp_scan_device(self):
        """Scan full OTP register space from connected device."""
        if not self._check_connected():
            return

        addr_str = self.otp_addr_var.get().strip()
        try:
            addr = int(addr_str, 16) if addr_str.startswith("0x") else int(addr_str)
        except ValueError:
            messagebox.showerror("Invalid Address", f"Cannot parse address: {addr_str}")
            return

        self.log(f"OTP scan: 0x{addr:02X} (0x00-0x7F)...")
        self.otp_status_var.set(f"Scanning 0x{addr:02X}...")
        self.otp_scan_btn.configure(state=tk.DISABLED)
        self.otp_progress["value"] = 0
        self.otp_progress["maximum"] = 32  # 32 chunks of 4 bytes

        def do_scan():
            def progress(current, total):
                self.after(0, self.otp_progress.configure, {"value": current})

            try:
                dump = scan_otp(self.adapter, addr, label=f"0x{addr:02X}",
                               progress_cb=progress)
                self.otp_current_dump = dump
                self.after(0, self._show_otp_dump, dump)
                self.after(0, self.otp_status_var.set,
                           f"Scan complete: {dump.filled_count} registers, "
                           f"{dump.error_count} errors")
                self.after(0, self.log,
                           f"OTP scan complete: 0x{addr:02X} — "
                           f"{dump.filled_count} regs, {dump.error_count} errors", "ok")
            except Exception as e:
                self.after(0, self.log, f"OTP scan error: {e}", "err")
                self.after(0, self.otp_status_var.set, f"Error: {e}")
            finally:
                self.after(0, self.otp_scan_btn.configure, {"state": "normal"})

        threading.Thread(target=do_scan, daemon=True).start()

    def _show_otp_dump(self, dump: OTPDump):
        """Display an OTP dump in the text widget."""
        text = format_dump_table(dump, show_zeros=True)

        self.otp_dump_text.configure(state=tk.NORMAL)
        self.otp_dump_text.delete("1.0", tk.END)
        self.otp_dump_text.insert(tk.END, text)

        # Highlight non-zero registers (likely OTP content)
        for offset in sorted(dump.registers.keys()):
            raw = dump.registers[offset]
            val = int.from_bytes(raw, "little")
            if val != 0:
                line_start = self.otp_dump_text.search(
                    f"0x{offset:02X}", "1.0", tk.END)
                if line_start:
                    line_end = f"{line_start}+1line"
                    self.otp_dump_text.tag_add("warn", line_start, line_end)

        self.otp_dump_text.configure(state=tk.DISABLED)

        # Auto-fill dump A field
        self.otp_dump_a_var.set(dump.label)

    def _otp_import_file(self):
        """Import an OTP dump from file."""
        filepath = filedialog.askopenfilename(
            filetypes=[("OTP dumps", "*.json *.otp.bin"), ("All files", "*.*")],
            title="Import OTP Dump"
        )
        if not filepath:
            return

        dump = load_dump_json(filepath) or load_dump_binary(filepath)
        if dump is None:
            messagebox.showerror("Import Error", f"Could not load {filepath}")
            return

        self.otp_current_dump = dump
        self._show_otp_dump(dump)
        self.log(f"Imported OTP dump: {dump.label} ({dump.filled_count} registers)", "ok")

    def _otp_diff_dialog(self):
        """Show dialog to select two dumps and diff them."""
        # Use the current dump as Dump A if available
        if self.otp_current_dump:
            self.otp_dump_a_var.set(self.otp_current_dump.label)

        file_a = filedialog.askopenfilename(
            initialdir=".",
            filetypes=[("OTP dumps", "*.json *.otp.bin"), ("All files", "*.*")],
            title="Select Dump A (vanilla/empty)"
        )
        if not file_a:
            return

        file_b = filedialog.askopenfilename(
            initialdir=".",
            filetypes=[("OTP dumps", "*.json *.otp.bin"), ("All files", "*.*")],
            title="Select Dump B (OTP-ed)"
        )
        if not file_b:
            return

        dump_a = load_dump_json(file_a) or load_dump_binary(file_a)
        dump_b = load_dump_json(file_b) or load_dump_binary(file_b)

        if dump_a is None or dump_b is None:
            messagebox.showerror("Load Error", "Could not load one or both dumps")
            return

        self.otp_dump_a_var.set(dump_a.label)
        self.otp_dump_b_var.set(dump_b.label)

        result = diff_dumps(dump_a, dump_b)

        # Show diff result
        self.otp_diff_text.configure(state=tk.NORMAL)
        self.otp_diff_text.delete("1.0", tk.END)
        self.otp_diff_text.insert(tk.END, result.summary())

        # Highlight different registers
        for offset in result.different:
            line_start = self.otp_diff_text.search(
                f"0x{offset:02X}", "1.0", tk.END)
            if line_start:
                line_end = f"{line_start}+1line"
                self.otp_diff_text.tag_add("fail", line_start, line_end)

        self.otp_diff_text.configure(state=tk.DISABLED)

        self.log(f"OTP diff: {result.match_count} identical, "
                 f"{result.diff_count} different", "info")

        # Offer to save diff report
        if result.diff_count > 0:
            save = messagebox.askyesno(
                "Save Diff Report",
                f"Found {result.diff_count} different registers.\n"
                "Save diff report to file?"
            )
            if save:
                filepath = filedialog.asksaveasfilename(
                    defaultextension=".txt",
                    filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
                    title="Save Diff Report"
                )
                if filepath:
                    save_diff_report(result, filepath)
                    self.log(f"Diff report saved: {filepath}", "ok")

    # ─── File Operations ──────────────────────────────────────────────────

    def _save_json(self):
        """Save diagnostic report as JSON."""
        filepath = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            title="Save Diagnostic Report"
        )
        if not filepath:
            return

        report = DiagnosticReport(
            timestamp=datetime.now().isoformat(),
            adapter_type=type(self.adapter).__name__ if self.adapter else "None",
        )
        report.bus_scan_results = self.scan_results
        report.devices = list(self.devices.values())
        report.summary = f"GUI session report - {len(self.devices)} device(s) diagnosed"

        try:
            save_json_report(report, filepath)
            self.log(f"Report saved to {filepath}", "ok")
        except Exception as e:
            self.log(f"Save error: {e}", "err")
            messagebox.showerror("Save Error", str(e))

    def _save_csv(self):
        """Save batch results as CSV."""
        if not self.batch_results:
            messagebox.showinfo("No Data", "Run a batch test first to generate data.")
            return

        filepath = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            title="Export Batch Results"
        )
        if not filepath:
            return

        try:
            save_csv_log(self.batch_results, filepath, append=False)
            self.log(f"CSV exported to {filepath}", "ok")
        except Exception as e:
            self.log(f"CSV export error: {e}", "err")

    # ─── Help Dialogs ─────────────────────────────────────────────────────

    def _show_about(self):
        """Show about dialog."""
        messagebox.showinfo(
            "About CD3217B12 Analyzer",
            "CD3217B12 (Apple ACE2) I2C Diagnostic Analyzer\n"
            "Version 1.0.0\n\n"
            "A diagnostic tool for testing CD3217B12 USB-C PD\n"
            "controllers used in MacBook repair.\n\n"
            "Based on TPS65982 reference documentation and\n"
            "community reverse-engineering efforts.\n\n"
            "Supports: FTDI FT232H, CH341, Linux SMBus"
        )

    def _show_wiring_guide(self):
        """Show wiring guide dialog."""
        messagebox.showinfo(
            "Wiring Guide",
            "CD3217B12 I2C Connections:\n\n"
            "Port 1 (Debug/TBT):\n"
            "  SDA = Pin B5\n"
            "  SCL = Pin A4\n"
            "  IRQ = Pin D7\n\n"
            "Port 2 (SMC):\n"
            "  SDA = Pin B7\n"
            "  SCL = Pin A6\n"
            "  IRQ = Pin C8\n\n"
            "Power:\n"
            "  VIN_3V3 = 3.3V supply\n"
            "  GND = Ground\n\n"
            "Pullups: 2.2k from SDA/SCL to 3.3V\n\n"
            "ADDR Pin (M19):\n"
            "  Controls I2C address via resistor to GND\n"
            "  0Ω = 0x38, Float = 0x3F"
        )

    # ─── Utilities ────────────────────────────────────────────────────────

    def _check_connected(self) -> bool:
        """Check if adapter is connected, show warning if not."""
        if not self.connected or not self.adapter:
            messagebox.showwarning(
                "Not Connected",
                "No I2C adapter connected.\n\n"
                "Select an adapter and click Connect."
            )
            return False
        return True


def main():
    """Launch the GUI application."""
    app = Application()
    app.mainloop()


if __name__ == "__main__":
    main()
