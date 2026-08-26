"""CLI interface for CD3217B12 (Apple ACE2) I2C Diagnostic Analyzer.

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

    print(f"Unknown adapter type: {adapter_type}")
    sys.exit(1)


def parse_addresses(addr_str: str) -> List[int]:
    """Parse comma-separated hex addresses."""
    addrs = []
    for part in addr_str.split(","):
        part = part.strip()
        if part.startswith("0x"):
            addrs.append(int(part, 16))
        else:
            addrs.append(int(part))
    return addrs


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


def cmd_interactive(analyzer: CD3217Analyzer) -> None:
    """Interactive mode with menu."""
    print("=" * 50)
    print("  CD3217B12 (Apple ACE2) I2C Analyzer")
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
        description="CD3217B12 (Apple ACE2) I2C Diagnostic Analyzer",
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
        """,
    )

    parser.add_argument("--adapter", "-a", default="auto",
                        choices=["auto", "ftdi", "ch341", "smbus", "linux"],
                        help="I2C adapter type (default: auto-detect)")
    parser.add_argument("--ftdi-url", default=None,
                        help="FTDI device URL (default: ftdi://ftdi:232h/1)")
    parser.add_argument("--bus", "-b", type=int, default=1,
                        help="I2C bus number for smbus/ch341 (default: 1)")
    parser.add_argument("--addresses", default=None,
                        help="Comma-separated hex addresses to check (e.g., 0x38,0x3F)")

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
    group.add_argument("--interactive", "-i", action="store_true",
                       help="Interactive mode (default if no command given)")

    parser.add_argument("-n", "--count", type=int, default=3,
                        help="Number of iterations for batch mode (default: 3)")
    parser.add_argument("-o", "--output", default=None,
                        help="Output file (.json or .csv)")

    args = parser.parse_args()

    # Create adapter
    adapter = create_adapter(args)

    # Parse custom addresses if provided
    addresses = None
    if args.addresses:
        addresses = parse_addresses(args.addresses)

    try:
        with adapter:
            analyzer = CD3217Analyzer(adapter, addresses=addresses)

            if args.scan:
                cmd_scan(analyzer)
            elif args.diagnose:
                addr = int(args.diagnose, 16) if args.diagnose.startswith("0x") \
                    else int(args.diagnose)
                cmd_diagnose(analyzer, addr)
            elif args.dump:
                addr = int(args.dump, 16) if args.dump.startswith("0x") \
                    else int(args.dump)
                cmd_register_dump(analyzer, addr)
            elif args.full:
                cmd_full_report(analyzer, args.output)
            elif args.batch:
                cmd_batch(analyzer, count=args.count, output=args.output)
            elif args.strap:
                from .registers import decode_i2c_address_straps
                a1 = int(args.strap[0], 16) if args.strap[0].startswith("0x") \
                    else int(args.strap[0])
                a2 = int(args.strap[1], 16) if args.strap[1].startswith("0x") \
                    else int(args.strap[1])
                info = decode_i2c_address_straps(a1, a2)
                print(f"Port 1 address: {info['port1_addr']}")
                print(f"Port 2 address: {info['port2_addr']}")
                print(f"ADDR bits: {info['addr_bits']} -> {info['addr_resistor']}")
                print(f"CNTL1: {info['cntl1']} -> {info['cntl1_source']}")
                print(f"CNTL2: {info['cntl2']} -> {info['cntl2_source']}")
            else:
                cmd_interactive(analyzer)

    except KeyboardInterrupt:
        print("\nAborted.")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
