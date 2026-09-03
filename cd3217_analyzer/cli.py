"""CLI interface for ACA - ACE Controller Analyzer (Apple ACE1/ACE2).

Usage:
    python -m cd3217_analyzer                    # Interactive mode
    python -m cd3217_analyzer --scan             # Scan bus for all devices
    python -m cd3217_analyzer --diagnose 0x38    # Diagnose specific device
    python -m cd3217_analyzer --full             # Full diagnostic report
    python -m cd3217_analyzer --dump 0x38        # Register dump
    python -m cd3217_analyzer --batch            # Batch test mode
    python -m cd3217_analyzer --adapter ftdi     # Specify adapter
    python -m cd3217_analyzer --addresses 0x38,0x3F  # Custom address list
"""

import argparse
import sys
import time
from typing import List, Optional

from .adapters import (
    ADAPTER_TYPES,
    I2CAdapter,
    detect_adapter,
    FTDIAdapter,
    SMBusAdapter,
)
from .analyzer import CD3217Analyzer, DeviceResult, HealthStatus
from .report import (
    format_batch_summary,
    format_compact_result,
    save_csv_log,
    save_json_report,
)
from .models import get_model, list_models, model_ids
from .otp import (
    diff_dumps,
    format_dump_table,
    load_dump_binary,
    load_dump_json,
    save_diff_report,
    save_dump_binary,
    save_dump_json,
    scan_otp,
)
from .spi_adapter import SPIAdapter
from .flash import SPIFlash, FlashError
from .usb_bridge import (UsbBridgeAdapter, list_bridge_ports,
                         list_ports_with_desc, normalize_port)
from .utils import parse_address_list, parse_hex_address
from . import __version__


def create_adapter(args) -> I2CAdapter:
    """Create an I2C adapter based on CLI arguments."""
    adapter_type = args.adapter

    if adapter_type == "auto":
        adapter = detect_adapter()
        if adapter is None:
            print("ERROR: No I2C adapter found!")
            print("Supported adapters:")
            print("  - FTDI FT232H (pip install pyftdi)")
            print("  - Linux SMBus/i2c-dev (pip install smbus2)")
            print()
            print("Make sure your adapter is connected and drivers are installed.")
            sys.exit(1)
        print(f"Auto-detected adapter: {type(adapter).__name__}")
        return adapter

    if adapter_type == "ftdi":
        url = args.ftdi_url or "ftdi://ftdi:232h/1"
        return FTDIAdapter(url=url)

    if adapter_type in ("smbus", "linux", "ch341"):
        bus = args.bus or 1
        return SMBusAdapter(bus_number=bus)

    if adapter_type in ("usb", "bridge", "board"):
        port = normalize_port(args.port) if args.port else (
            list_bridge_ports() or [None])[0]
        if not port:
            print("ERROR: No USB serial port found. Plug in the board and "
                  "specify --port COMx")
            sys.exit(1)
        print(f"USB bridge adapter on {port}")
        return UsbBridgeAdapter(port=port)

    print(f"Unknown adapter type: {adapter_type}")
    sys.exit(1)


def parse_addresses(addr_str: str) -> List[int]:
    """Parse comma-separated hex addresses."""
    return parse_address_list(addr_str)


def cmd_scan(analyzer: CD3217Analyzer) -> None:
    """Scan I2C bus for all devices."""
    print("Scanning I2C bus (0x08 - 0x77)...")
    devices = analyzer.scan_bus()

    if not devices:
        print("No devices found on I2C bus.")
        print("Check:")
        print("  - Is the chip powered? (VIN_3V3 or VBUS)")
        print("  - Are SDA/SCL connected correctly?")
        print("  - Are pullup resistors present?")
        return

    print(f"\nFound {len(devices)} device(s):")
    for addr in devices:
        from .registers import KNOWN_ACE2_ADDRESSES, is_ace2_address
        marker = " <-- ACE2" if is_ace2_address(addr) else ""
        desc = KNOWN_ACE2_ADDRESSES.get(addr, "")
        if desc:
            marker += f" ({desc})"
        print(f"  0x{addr:02X}{marker}")


def cmd_diagnose(analyzer: CD3217Analyzer, address: int) -> None:
    """Diagnose a single device."""
    print(f"Diagnosing device at 0x{address:02X}...")
    print()

    result = analyzer.diagnose_device(address)

    # Print detailed result
    print(f"Address:     0x{address:02X}")
    print(f"Responds:    {'Yes' if result.responds else 'No'}")
    print(f"Health:      {result.health.value} (score: {result.health_score}/100)")
    print(f"VID:         {result.vendor_id or 'N/A'}")
    print(f"DID:         {result.device_id or 'N/A'}")
    if result.device_info:
        print(f"Identity:    {result.device_info}")
    print(f"Mode:        {result.mode or 'N/A'}")
    print(f"Type:        {result.device_type or 'N/A'}")
    print(f"Scan Time:   {result.scan_time_ms:.1f}ms")

    if result.faults:
        print(f"\nFAULTS ({len(result.faults)}):")
        for fault in result.faults:
            print(f"  [{fault.value}]")
        print("\nDetails:")
        for detail in result.fault_details:
            print(f"  - {detail}")

    # Chip type identification
    chip_type = analyzer.identify_chip_type(address)
    if chip_type:
        print(f"\nChip Type:   {chip_type}")

    print()


def cmd_register_dump(analyzer: CD3217Analyzer, address: int) -> None:
    """Dump all registers from a device."""
    print(analyzer.register_dump(address))


def cmd_full_report(analyzer: CD3217Analyzer, output: Optional[str] = None) -> None:
    """Run full diagnostic and generate report."""
    print("Running full diagnostic scan...")
    print()

    report = analyzer.full_diagnostic()
    analyzer.print_report(report)

    if output:
        if output.endswith(".json"):
            save_json_report(report, output)
            print(f"\nReport saved to: {output}")
        elif output.endswith(".csv"):
            save_csv_log(report.devices, output, append=False)
            print(f"\nReport saved to: {output}")


def cmd_batch(analyzer: CD3217Analyzer, count: int = 1,
              output: Optional[str] = None) -> None:
    """Batch test mode - test same device(s) multiple times."""
    all_results = []

    for i in range(count):
        if count > 1:
            print(f"\n--- Test {i+1}/{count} ---")

        for addr in analyzer.addresses:
            if analyzer.adapter.ping(addr):
                result = analyzer.diagnose_device(addr)
                all_results.append(result)
                print(format_compact_result(result))

    if output:
        save_csv_log(all_results, output)
        print(f"\nResults appended to: {output}")

    if len(all_results) > 1:
        print("\n" + format_batch_summary(all_results))


def cmd_otp_scan(analyzer: CD3217Analyzer, address: int,
                 output: Optional[str] = None) -> None:
    """Full OTP register scan of a CD3217B12 chip."""

    print(f"Scanning OTP registers at 0x{address:02X} (0x00-0x7F)...")
    print("This reads 32 x 4-byte chunks. May take a few seconds.\n")

    def progress(current, total):
        pct = int(current / total * 100)
        bar = "#" * (pct // 5) + "." * (20 - pct // 5)
        print(f"\r  [{bar}] {pct:3d}%", end="", flush=True)

    dump = scan_otp(analyzer.adapter, address, label=f"0x{address:02X}",
                    progress_cb=progress)
    print("\n")

    print(format_dump_table(dump, show_zeros=True))
    print(f"\nRead errors: {dump.error_count} register(s)")

    if output:
        if output.endswith(".json"):
            save_dump_json(dump, output)
        elif output.endswith(".otp.bin"):
            save_dump_binary(dump, output)
        else:
            save_dump_json(dump, output)
        print(f"Saved to: {output}")


def cmd_otp_diff(file_a: str, file_b: str,
                 output: Optional[str] = None) -> None:
    """Compare two OTP dumps to find OTP-backed registers."""

    # Load dumps (try JSON first, then binary)
    dump_a = load_dump_json(file_a) or load_dump_binary(file_a)
    dump_b = load_dump_json(file_b) or load_dump_binary(file_b)

    if dump_a is None:
        print(f"ERROR: Could not load dump from {file_a}")
        print("Supported formats: .json, .otp.bin")
        return

    if dump_b is None:
        print(f"ERROR: Could not load dump from {file_b}")
        print("Supported formats: .json, .otp.bin")
        return

    result = diff_dumps(dump_a, dump_b)
    print(result.summary())

    if output:
        save_diff_report(result, output)
        print(f"\nDiff report saved to: {output}")


def cmd_otp_export(analyzer: CD3217Analyzer, address: int,
                   filepath: str) -> None:
    """Export OTP dump to file."""

    print(f"Scanning OTP at 0x{address:02X}...")

    def progress(current, total):
        pct = int(current / total * 100)
        bar = "#" * (pct // 5) + "." * (20 - pct // 5)
        print(f"\r  [{bar}] {pct:3d}%", end="", flush=True)

    dump = scan_otp(analyzer.adapter, address, label=f"0x{address:02X}",
                    progress_cb=progress)
    print("\n")

    if filepath.endswith(".otp.bin"):
        save_dump_binary(dump, filepath)
    else:
        save_dump_json(dump, filepath)

    print(f"Saved: {filepath}")
    print(f"Registers: {dump.filled_count} | Errors: {dump.error_count}")


def cmd_otp_import(filepath: str) -> None:
    """Import and display an OTP dump."""

    dump = load_dump_json(filepath) or load_dump_binary(filepath)

    if dump is None:
        print(f"ERROR: Could not load {filepath}")
        print("Supported formats: .json, .otp.bin")
        return

    print(format_dump_table(dump, show_zeros=True))


def cmd_board_update(args) -> None:
    """Handle --board-update: flash the newest firmware to a board."""
    from .usb_bridge import UsbBridgeAdapter, list_bridge_ports, normalize_port

    port = normalize_port(args.port) if getattr(args, "port", None) else None
    if not port:
        ports = list_bridge_ports()
        if not ports:
            print("ERROR: no serial port found; plug in the board or use "
                  "--port COMx")
            sys.exit(1)
        port = ports[0]
    adapter = UsbBridgeAdapter(port=port)
    adapter.open()
    if not adapter.handshake():
        adapter.close()
        print(f"ERROR: board on {port} did not answer PING")
        sys.exit(1)

    info = adapter.info()
    board = (info or {}).get("board")
    fw = (info or {}).get("version")
    if not board:
        adapter.close()
        print("ERROR: board did not report its type — cannot pick firmware")
        sys.exit(1)
    print(f"Board: {board} (fw {fw or 'unknown'}) on {port}")

    from .updater import (download_board_firmware, fetch_latest_release,
                          is_newer)
    rel = fetch_latest_release()
    if not rel:
        adapter.close()
        print("ERROR: could not reach GitHub — check the connection")
        sys.exit(1)
    if fw and not is_newer(rel["version"], fw):
        print(f"Already up to date (fw {fw}, latest {rel['version']})")
        adapter.close()
        return
    print(f"Downloading firmware {rel['version']}...")

    try:
        path = download_board_firmware(board, rel)
    except IOError as e:
        adapter.close()
        print(f"ERROR: {e}")
        sys.exit(1)

    if path.lower().endswith(".uf2"):
        print("Rebooting board into BOOTSEL and copying UF2...")
        adapter.fw_reboot_bootsel()
        adapter.close()
        import time as _t
        from .flash_board import find_bootsel_drives, flash_pico_uf2
        deadline = _t.time() + 20
        while _t.time() < deadline:
            drives = find_bootsel_drives()
            if drives:
                break
            _t.sleep(0.5)
        else:
            print("ERROR: board did not enter BOOTSEL mode")
            sys.exit(1)
        msg = flash_pico_uf2(path, bootsel_drive=drives[0])
        print(msg)
    else:
        with open(path, "rb") as f:
            data = f.read()
        print(f"Writing {len(data)/1024:.0f} KB over the bridge...")
        try:
            adapter.fw_update_image(data)
        finally:
            adapter.close()
        print("Firmware written and verified — board is restarting.")
    print("Done. Reconnect with --adapter usb --port <port> if needed.")


def cmd_export(args) -> None:
    """Handle --export: collect board data and optionally push to GitHub.

    Self-contained (builds its own I2C adapter + optional SPI bridge) so it
    can run independently of the other device commands.
    """
    from .export_data import (
        DATA_DEFAULT,
        collect_bundle,
        push_bundle,
        write_bundle,
    )

    name = args.export
    sources = ([s.strip() for s in args.with_sources.split(",") if s.strip()]
               if args.with_sources else list(DATA_DEFAULT))
    valid = {"info", "registers", "otp", "flash", "uart", "report"}
    for s in sources:
        if s not in valid:
            print(f"Unknown source '{s}' — choose from: {', '.join(sorted(valid))}")
            sys.exit(1)

    adapter = create_adapter(args)

    mac_model = None
    try:
        info = adapter.info()
        mac_model = info.get("board")
    except Exception:
        pass

    scan_results = []
    try:
        scan_results = CD3217Analyzer(adapter).scan_bus()
    except Exception:
        pass

    flash = None
    spi = None
    if "flash" in sources:
        if args.adapter in ("usb", "bridge", "board") or (
                args.adapter == "auto" and args.port):
            from .spi_bridge import make_bridge_flash
            bridge, flash = make_bridge_flash(args.port)
            spi = None
            print(f"SPI via board USB bridge on {bridge.port}")
        else:
            from .spi_adapter import SPIAdapter
            from .flash import SPIFlash
            spi = SPIAdapter()
            spi.open()
            flash = SPIFlash(spi)

    try:
        with adapter:
            print(f"Exporting {name} (sources: {', '.join(sources)}) ...")
            bundle = collect_bundle(
                adapter, sources, name,
                scan_results=scan_results, flash=flash, mac_model=mac_model,
                progress_cb=lambda m: print("  " + m))
    finally:
        if spi is not None:
            try:
                spi.close()
            except Exception:
                pass

    local_path = write_bundle(bundle, name)
    print(f"Local bundle: {local_path}")

    if args.push:
        print("Pushing to GitHub ...")
        from .export_data import GitHubPushError
        try:
            url = push_bundle(bundle, name, repo=args.github_repo,
                              progress_cb=lambda m: print("  " + m))
            print(f"Pushed: {url}")
        except GitHubPushError as e:
            print(f"Push failed: {e}")
            print(f"Bundle kept locally at {local_path}")
            sys.exit(1)


def cmd_stress(analyzer: CD3217Analyzer, address: int) -> None:
    """Handle --stress: bus-speed margin probe (100 kHz vs 400 kHz)."""
    res = analyzer.stress_test_margin(address)
    print(f"Stress test 0x{address:02X}: [{res['verdict']}]")
    print(f"  {res['detail']}")
    sys.exit(0 if res["verdict"] == "ample-margin" else 1)


def cmd_verify_export(path: str) -> None:
    """Handle --verify-export: offline bundle completeness check."""
    from .export_data import validate_bundle
    res = validate_bundle(path)
    print(f"Verifying: {path}")
    print(f"Result: {res['summary']}")
    for c in res["checks"]:
        mark = {"ok": "✓", "warn": "•", "critical": "!"}[c["level"]]
        line = f"  {mark} {c['name']}"
        if c["detail"]:
            line += f" — {c['detail']}"
        print(line)
    sys.exit(0 if res["valid"] else 1)


def cmd_bus_check(args) -> None:
    """Handle --bus-check: I2C idle levels through a connected board."""
    from .usb_bridge import (UsbBridgeAdapter, list_bridge_ports,
                             normalize_port)

    port = normalize_port(args.port) if getattr(args, "port", None) else None
    if not port:
        ports = list_bridge_ports()
        if not ports:
            print("ERROR: no serial port found; plug in the board or use "
                  "--port COMx")
            sys.exit(1)
        port = ports[0]
    adapter = UsbBridgeAdapter(port=port)
    adapter.open()
    if not adapter.handshake():
        adapter.close()
        print(f"ERROR: board on {port} did not answer PING")
        sys.exit(1)
    try:
        res = adapter.bus_check()
    except Exception as e:
        adapter.close()
        print(f"ERROR: {e}")
        sys.exit(1)
    adapter.close()
    for line, lvl in (("SDA", res["sda"]), ("SCL", res["scl"])):
        print(f"{line} idle: {'HIGH (healthy)' if lvl else 'LOW — stuck/absent'}")
    if not (res["sda"] and res["scl"]):
        print("A line is held LOW: a chip is stuck on the bus or the wiring "
              "is shorted/absent. The bridge clears a stuck bus on the next "
              "failed transaction; otherwise power-cycle the board.")


def cmd_uart(args) -> None:
    """Handle --uart-autobaud / --uart-sniff (RX-only UART capture)."""
    from .usb_bridge import (UsbBridgeAdapter, list_bridge_ports,
                             normalize_port)

    port = normalize_port(args.port) if getattr(args, "port", None) else None
    if not port:
        ports = list_bridge_ports()
        if not ports:
            print("ERROR: no serial port found; plug in the board or use "
                  "--port COMx")
            sys.exit(1)
        port = ports[0]
    adapter = UsbBridgeAdapter(port=port)
    adapter.open()
    if not adapter.handshake():
        adapter.close()
        print(f"ERROR: board on {port} did not answer PING")
        sys.exit(1)

    try:
        if args.uart_autobaud:
            print("Measuring UART baud (~1.5s)...")
            baud = adapter.uart_autobaud()
            if not baud:
                print("No UART activity detected on the RX pin — check "
                      "wiring/pull-up and that the target is transmitting.")
                sys.exit(1)
            print(f"Detected baud: {baud}")
            return

        # --uart-sniff BAUD
        baud = str(args.uart_sniff).lower()
        if baud == "auto":
            print("Auto-detecting baud (~1.5s)...")
            detected = adapter.uart_autobaud()
            if not detected:
                print("No UART activity detected — set the baud explicitly "
                      "(--uart-sniff 115200).")
                sys.exit(1)
            baud = detected
            print(f"Detected baud: {baud}")
        else:
            baud = int(baud)
        adapter.uart_setup(baud)
        print(f"Sniffing UART on {port} at {baud} baud (Ctrl+C to stop)...")
        total = 0
        import time as _t
        try:
            while True:
                data = adapter.uart_read()
                if data:
                    total += len(data)
                    sys.stdout.write("".join(
                        "\n" if c == 10 else "" if c == 13 else
                        (f"<{c:02X}>" if c < 32 or c > 126 else chr(c))
                        for c in data))
                    sys.stdout.flush()
                else:
                    _t.sleep(0.05)
        except KeyboardInterrupt:
            print(f"\nStopped — {total} bytes captured")
    finally:
        try:
            adapter.uart_setup(0)
        except Exception:
            pass
        adapter.close()


def cmd_flash(args) -> None:
    """Handle flash commands (detect, read, write, erase, restore).

    SPI backend: the CD3217 board USB bridge (--adapter usb, default when a
    port is given) or an FTDI FT232H dongle (--adapter ftdi).
    """
    try:
        if args.adapter in ("usb", "bridge", "board") or (
                args.adapter == "auto" and args.port):
            from .spi_bridge import make_bridge_flash
            bridge, flash = make_bridge_flash(args.port)
            spi = flash.spi
            print(f"SPI via board USB bridge on {bridge.port}")
        else:
            from .spi_adapter import SPIAdapter
            from .flash import SPIFlash
            spi = SPIAdapter()
            spi.open()
            flash = SPIFlash(spi)
    except Exception as e:
        print(f"ERROR: Could not connect to SPI adapter: {e}")
        print("Use --adapter usb --port COMx with a CD3217 board, or connect "
              "an FTDI FT232H (with pyftdi installed).")
        sys.exit(1)

    try:
        if args.flash_detect:
            cmd_flash_detect(flash)
        elif args.flash_read:
            cmd_flash_read(flash, args.flash_read)
        elif args.flash_write:
            cmd_flash_write(flash, args.flash_write)
        elif args.flash_erase:
            cmd_flash_erase(flash)
        elif args.flash_restore:
            cmd_flash_restore(flash, args.flash_restore)
    finally:
        spi.close()


def cmd_flash_detect(flash: SPIFlash) -> None:
    """Detect and display SPI flash info."""
    try:
        info = flash.detect()
        print(f"Flash detected: {info.name}")
        print(f"  JEDEC ID:  0x{info.jedec_id[0]:02X} 0x{info.jedec_id[1]:02X} 0x{info.jedec_id[2]:02X}")
        print(f"  Size:      {info.size_mb:.1f} MB ({info.size_bytes:,} bytes)")
        print(f"  Sectors:   {info.sector_count} x 4KB")
        print(f"  Pages:     {info.size_bytes // 256} x 256 bytes")
    except Exception as e:
        print(f"Detection failed: {e}")


def cmd_flash_read(flash: SPIFlash, filepath: str) -> None:
    """Read entire flash to file."""
    info = flash.detect()
    print(f"Reading {info.name} ({info.size_mb:.1f}MB)...")
    print(f"Saving to: {filepath}")

    def progress(cur, total):
        pct = int(cur / total * 100) if total else 0
        bar = "#" * (pct // 5) + "." * (20 - pct // 5)
        print(f"\r  [{bar}] {pct:3d}% ({cur:,}/{total:,})", end="", flush=True)

    try:
        size = flash.dump_to_file(filepath, progress_cb=progress)
        print(f"\nDone: {size:,} bytes saved to {filepath}")
    except Exception as e:
        print(f"\nRead error: {e}")


def cmd_flash_write(flash: SPIFlash, filepath: str) -> None:
    """Erase and write flash from file."""
    from pathlib import Path

    data = Path(filepath).read_bytes()
    info = flash.detect()

    if len(data) > info.size_bytes:
        print(f"ERROR: File too large ({len(data):,} > {info.size_bytes:,} bytes)")
        return

    print(f"File: {filepath} ({len(data):,} bytes)")
    print(f"Flash: {info.name} ({info.size_mb:.1f}MB)")

    # Erase
    print("Erasing chip...", end=" ", flush=True)
    flash.erase_chip()
    print("done")

    # Write
    print("Writing...", end=" ", flush=True)

    def progress(cur, total):
        pct = int(cur / total * 100) if total else 0
        bar = "#" * (pct // 5) + "." * (20 - pct // 5)
        print(f"\r  [{bar}] {pct:3d}%", end="", flush=True)

    flash.write(0, data, progress_cb=progress)
    print("\nWriting done")

    # Verify
    print("Verifying...", end=" ", flush=True)
    readback = flash.read(0, len(data))
    if readback == data:
        print(f"OK — {len(data):,} bytes verified")
    else:
        for i in range(len(data)):
            if readback[i] != data[i]:
                print(f"\nVERIFY FAILED at 0x{i:06X}: expected 0x{data[i]:02X}, got 0x{readback[i]:02X}")
                break


def cmd_flash_erase(flash: SPIFlash) -> None:
    """Erase entire flash chip."""
    info = flash.detect()
    print(f"Erasing {info.name} ({info.size_mb:.1f}MB)...", end=" ", flush=True)
    flash.erase_chip()
    print("done")


def cmd_flash_restore(flash: SPIFlash, filepath: str) -> None:
    """Erase + write + verify flash from file."""
    print("Step 1: Erase")
    cmd_flash_erase(flash)
    print("\nStep 2: Write")
    cmd_flash_write(flash, filepath)


def cmd_interactive(analyzer: CD3217Analyzer) -> None:
    """Interactive mode with menu."""
    print("=" * 50)
    print("  ACA - ACE Controller Analyzer")
    print("  Interactive Mode")
    print("=" * 50)
    print()

    while True:
        print("Commands:")
        print("  scan           - Scan I2C bus for all devices")
        print("  diagnose ADDR  - Diagnose device (e.g., diagnose 0x38)")
        print("  dump ADDR      - Register dump (e.g., dump 0x38)")
        print("  quick          - Quick scan of known ACE2 addresses")
        print("  full           - Full diagnostic report")
        print("  batch [N]      - Batch test (default: 3 iterations)")
        print("  strap ADDR1 ADDR2 - Decode strap config from addresses")
        print("  quit           - Exit")
        print()

        try:
            cmd = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not cmd:
            continue

        parts = cmd.split()
        action = parts[0].lower()

        if action in ("quit", "exit", "q"):
            break
        elif action == "scan":
            cmd_scan(analyzer)
        elif action == "diagnose" and len(parts) >= 2:
            addr = int(parts[1], 16) if parts[1].startswith("0x") else int(parts[1])
            cmd_diagnose(analyzer, addr)
        elif action == "dump" and len(parts) >= 2:
            addr = int(parts[1], 16) if parts[1].startswith("0x") else int(parts[1])
            cmd_register_dump(analyzer, addr)
        elif action == "quick":
            print("Scanning known ACE2 addresses...")
            found = analyzer.quick_scan()
            if found:
                for addr in found:
                    health, score, msg = analyzer.quick_health_check(addr)
                    print(format_compact_result(
                        analyzer.diagnose_device(addr)
                    ))
            else:
                print("No ACE2 devices found at known addresses.")
        elif action == "full":
            cmd_full_report(analyzer)
        elif action == "batch":
            count = int(parts[1]) if len(parts) >= 2 else 3
            cmd_batch(analyzer, count=count)
        elif action == "strap" and len(parts) >= 3:
            from .registers import decode_i2c_address_straps
            a1 = int(parts[1], 16) if parts[1].startswith("0x") else int(parts[1])
            a2 = int(parts[2], 16) if parts[2].startswith("0x") else int(parts[2])
            info = decode_i2c_address_straps(a1, a2)
            print(f"Port 1 address: {info['port1_addr']}")
            print(f"Port 2 address: {info['port2_addr']}")
            print(f"ADDR bits: {info['addr_bits']} -> {info['addr_resistor']}")
            print(f"CNTL1: {info['cntl1']} -> {info['cntl1_source']}")
            print(f"CNTL2: {info['cntl2']} -> {info['cntl2_source']}")
        else:
            print(f"Unknown command: {cmd}")
            print("Type 'help' for available commands.")


def main():
    parser = argparse.ArgumentParser(
        description=f"ACA - ACE Controller Analyzer v{__version__}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                          Interactive mode
  %(prog)s --scan                   Scan all I2C devices
  %(prog)s --diagnose 0x38          Diagnose device at 0x38
  %(prog)s --full                   Full diagnostic report
  %(prog)s --full -o report.json    Full report saved to JSON
  %(prog)s --dump 0x38              Register dump
  %(prog)s --batch -n 5             Batch test 5 iterations
  %(prog)s --adapter ftdi           Use FTDI FT232H adapter
  %(prog)s --adapter smbus --bus 1  Use Linux SMBus bus 1
  %(prog)s --strap 0x38 0x2F        Decode strap config
  %(prog)s --otp-scan 0x38          Full OTP scan (0x00-0x7F)
  %(prog)s --otp-export 0x38 a.bin  Export OTP to binary file
  %(prog)s --otp-diff a.json b.json Compare two OTP dumps
  %(prog)s --otp-import dump.json   View a saved OTP dump
  %(prog)s --model A2442 --scan     Use model-specific addresses
        """,
    )

    parser.add_argument("--adapter", "-a", default="auto",
                        choices=["auto", "ftdi", "ch341", "smbus", "linux", "usb"],
                        help="I2C adapter type (default: auto-detect)")
    parser.add_argument("--ftdi-url", default=None,
                        help="FTDI device URL (default: ftdi://ftdi:232h/1)")
    parser.add_argument("--port", default=None,
                        help="Serial port for USB bridge adapter (e.g., COM5)")
    parser.add_argument("--bus", "-b", type=int, default=1,
                        help="I2C bus number for smbus/ch341 (default: 1)")
    parser.add_argument("--addresses", default=None,
                        help="Comma-separated hex addresses to check (e.g., 0x38,0x3F)")
    parser.add_argument("--model", "-m", default=None,
                        help="MacBook model (e.g., A2442, A2337) for model-specific address map")
    parser.add_argument("--list-models", action="store_true",
                        help="List all supported MacBook models and exit")

    group = parser.add_mutually_exclusive_group()
    group.add_argument("--scan", action="store_true",
                       help="Scan I2C bus for all devices")
    group.add_argument("--diagnose", metavar="ADDR",
                       help="Diagnose specific device (hex address)")
    group.add_argument("--dump", metavar="ADDR",
                       help="Register dump of specific device")
    group.add_argument("--full", action="store_true",
                       help="Full diagnostic report")
    group.add_argument("--batch", action="store_true",
                       help="Batch test mode")
    group.add_argument("--strap", nargs=2, metavar=("ADDR1", "ADDR2"),
                       help="Decode I2C strap configuration from two addresses")
    group.add_argument("--otp-scan", metavar="ADDR",
                       help="Full OTP register scan of device at ADDR (0x00-0x7F)")
    group.add_argument("--otp-diff", nargs=2, metavar=("FILE1", "FILE2"),
                       help="Diff two OTP dumps (.json or .otp.bin)")
    group.add_argument("--otp-export", nargs=2, metavar=("ADDR", "FILE"),
                       help="Export OTP dump from device at ADDR to FILE (.json or .otp.bin)")
    group.add_argument("--otp-import", metavar="FILE",
                       help="Import and display an OTP dump from FILE")
    group.add_argument("--flash-detect", action="store_true",
                       help="Detect SPI flash chip (FTDI FT232H or CD3217 board via --adapter usb)")
    group.add_argument("--flash-read", metavar="FILE",
                       help="Dump SPI flash contents to FILE.bin")
    group.add_argument("--flash-write", metavar="FILE",
                       help="Write FILE.bin to SPI flash (erases first)")
    group.add_argument("--flash-erase", action="store_true",
                       help="Erase entire SPI flash chip")
    group.add_argument("--flash-restore", metavar="FILE",
                       help="Erase + write + verify FILE.bin to SPI flash")
    group.add_argument("--flash-board", metavar="FILE",
                       help="Flash firmware FILE (.uf2 or .bin) to a connected board")
    group.add_argument("--list-ports", action="store_true",
                       help="List serial ports with device names and exit (helps "
                            "identify which COM is the Pico 2 / RP2040 board)")
    group.add_argument("--uart-sniff", metavar="BAUD",
                       help="Sniff (listen-only) a UART line through a "
                            "connected board; BAUD is a number or 'auto'. "
                            "Streams to stdout until Ctrl+C. Optionally "
                            "combined with --port.")
    group.add_argument("--uart-autobaud", action="store_true",
                       help="Measure the UART line's baud through a "
                            "connected board and print it (no sniffing)")
    group.add_argument("--bus-check", action="store_true",
                       help="Measure the I2C lines' idle levels through a "
                            "connected board (1=HIGH healthy, 0=held LOW) "
                            "and exit")
    group.add_argument("--verify-export", metavar="PATH",
                       help="Verify an exported bundle .json: format, "
                            "sha256 integrity, per-chip register/OTP "
                            "completeness, model coverage; exit 0 = valid")
    group.add_argument("--stress", metavar="ADDR",
                       help="Bus-speed stress probe on ADDR: identity "
                            "registers re-read at 100 kHz vs 400 kHz; "
                            "reports the timing-margin verdict (board "
                            "adapter only)")
    group.add_argument("--board-update", action="store_true",
                       help="Update a connected board's firmware from the "
                            "latest GitHub release (auto-detects the board)")
    group.add_argument("--export", metavar="NAME",
                       help="Export the board's data to a JSON bundle named "
                            "NAME (MacBook or board model, e.g. 'A2141'). "
                            "Use --with to pick sources.")
    group.add_argument("--with", dest="with_sources",
                       default=None,
                       help="Comma list of sources for --export: "
                            "info,registers,otp,flash,uart,report "
                            "(default: info,registers,report)")
    group.add_argument("--push", action="store_true",
                       help="With --export: push the bundle to the GitHub "
                            "data branch (needs a stored token)")
    group.add_argument("--set-token", metavar="TOKEN",
                       help="Store a GitHub Personal Access Token locally "
                            "(needed for --push) and exit")
    group.add_argument("--github-repo", default=None,
                       help="Override GitHub repo for --push "
                            "(default: ALeXXBody/cd3217-analyzer)")
    group.add_argument("--interactive", "-i", action="store_true",
                       help="Interactive mode (default if no command given)")

    parser.add_argument("-n", "--count", type=int, default=3,
                        help="Number of iterations for batch mode (default: 3)")
    parser.add_argument("-o", "--output", default=None,
                        help="Output file (.json or .csv)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    args = parser.parse_args()

    # Handle --board-update
    if args.board_update:
        cmd_board_update(args)

    if args.verify_export:
        cmd_verify_export(args.verify_export)
        return

    if args.bus_check:
        cmd_bus_check(args)
        return

    # Handle --set-token (no adapter needed)
    if args.set_token:
        from .export_data import store_token
        store_token(args.set_token)
        print("GitHub token stored locally (owner-only permissions).")
        sys.exit(0)

    # Handle --uart-autobaud / --uart-sniff
    if args.uart_autobaud or args.uart_sniff:
        cmd_uart(args)

    # Handle --list-ports
    if args.list_ports:
        ports = list_ports_with_desc()
        if not ports:
            print("No serial ports found. Plug in the board and check "
                  "Device Manager (Ports / COM & LPT), then use --port COMx.")
            sys.exit(0)
        print(f"{'PORT':<8} {'DEVICE'}")
        print("-" * 64)
        for port, desc, hwid in ports:
            print(f"{port:<8} {desc}  {hwid}")
        sys.exit(0)

    # Handle --list-models
    if args.list_models:
        print("Supported MacBook Models:")
        print(f"{'ID':<10} {'Name':<45} {'Chips':<6} {'Board ID'}")
        print("-" * 85)
        for m in list_models():
            print(f"{m.model_id:<10} {m.name:<45} {m.chip_count:<6} {m.board_id}")
        print()
        print("Use --model <ID> to load a model's address map.")
        sys.exit(0)

    # Handle --model
    model = None
    if args.model:
        model = get_model(args.model)
        if model is None:
            print(f"Unknown model: {args.model}")
            print(f"Available models: {', '.join(model_ids())}")
            sys.exit(1)
        print(f"Model: {model.name} ({model.board_id})")
        print(f"CD3217 positions:")
        for pos in model.positions:
            addr = f"0x{pos.address:02X}"
            print(f"  {pos.ref:<10} {addr}  ({pos.addressing}, "
                  f"Port {pos.i2c_port}, {pos.notes or 'no notes'})")
        print()

        # Auto-populate addresses from model if none provided
        if not args.addresses:
            args.addresses = ",".join(f"0x{p.address:02X}" for p in model.positions)
            print(f"Using model addresses: {args.addresses}")
            print()

    # Commands that do not need an I2C adapter
    if args.flash_detect or args.flash_read or args.flash_write \
            or args.flash_erase or args.flash_restore:
        try:
            cmd_flash(args)
        except KeyboardInterrupt:
            print("\nAborted.")
        except Exception as e:
            print(f"Error: {e}")
            sys.exit(1)
        return

    # --export builds its own adapter + optional SPI bridge
    if args.export:
        try:
            cmd_export(args)
        except KeyboardInterrupt:
            print("\nAborted.")
        except Exception as e:
            print(f"Error: {e}")
            sys.exit(1)
        return

    if args.flash_board:
        from .flash_board import flash_file
        try:
            msg = flash_file(args.flash_board, port=args.port)
            print(msg)
        except KeyboardInterrupt:
            print("\nAborted.")
        except Exception as e:
            print(f"Error: {e}")
            sys.exit(1)
        return

    if args.strap:
        from .registers import decode_i2c_address_straps
        a1 = parse_hex_address(args.strap[0])
        a2 = parse_hex_address(args.strap[1])
        info = decode_i2c_address_straps(a1, a2)
        print(f"Port 1 address: {info['port1_addr']}")
        print(f"Port 2 address: {info['port2_addr']}")
        print(f"ADDR bits: {info['addr_bits']} -> {info['addr_resistor']}")
        print(f"CNTL1: {info['cntl1']} -> {info['cntl1_source']}")
        print(f"CNTL2: {info['cntl2']} -> {info['cntl2_source']}")
        return

    if args.otp_diff:
        cmd_otp_diff(args.otp_diff[0], args.otp_diff[1], args.output)
        return

    if args.otp_import:
        cmd_otp_import(args.otp_import)
        return

    # Create I2C adapter for remaining commands
    adapter = create_adapter(args)

    addresses = None
    if args.addresses:
        addresses = parse_addresses(args.addresses)

    try:
        with adapter:
            analyzer = CD3217Analyzer(adapter, addresses=addresses)

            if args.scan:
                cmd_scan(analyzer)
            elif args.diagnose:
                cmd_diagnose(analyzer, parse_hex_address(args.diagnose))
            elif args.dump:
                cmd_register_dump(analyzer, parse_hex_address(args.dump))
            elif args.full:
                cmd_full_report(analyzer, args.output)
            elif args.batch:
                cmd_batch(analyzer, count=args.count, output=args.output)
            elif args.stress:
                cmd_stress(analyzer, parse_hex_address(args.stress))
            elif args.otp_scan:
                cmd_otp_scan(analyzer, parse_hex_address(args.otp_scan), args.output)
            elif args.otp_export:
                cmd_otp_export(
                    analyzer,
                    parse_hex_address(args.otp_export[0]),
                    args.otp_export[1],
                )
            else:
                cmd_interactive(analyzer)

    except KeyboardInterrupt:
        print("\nAborted.")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
