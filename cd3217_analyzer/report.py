"""Reporting and logging for CD3217B12 diagnostic results."""

import csv
import json
import os
from datetime import datetime
from typing import List, Optional

from .analyzer import DeviceResult, DiagnosticReport, HealthStatus


def save_json_report(report: DiagnosticReport, filepath: str,
                     bus_stats: Optional[dict] = None) -> None:
    """Save diagnostic report as JSON.

    ``bus_stats`` (optional) is a serializable dict of session bus-integrity
    counters (see CD3217Analyzer.bus_health_summary) — including it lets a
    support dump show whether NACKs/garbled reads were seen, so a flaky probe
    tap is never confused with a genuinely failing chip.
    """
    data = {
        "timestamp": report.timestamp,
        "adapter_type": report.adapter_type,
        "bus_scan_results": [f"0x{a:02X}" for a in report.bus_scan_results],
        "devices": [],
        "summary": report.summary,
        "notes": report.notes,
    }
    if bus_stats is not None:
        data["bus_stats"] = bus_stats

    for dev in report.devices:
        dev_data = {
            "address": f"0x{dev.address:02X}",
            "responds": dev.responds,
            "vendor_id": dev.vendor_id,
            "device_id": dev.device_id,
            "did_raw": f"0x{dev.did_raw:08X}" if dev.did_raw is not None else None,
            "silicon": dev.silicon or "",
            "mode": dev.mode,
            "device_type": dev.device_type,
            "health": dev.health.value,
            "health_score": dev.health_score,
            "faults": [f.value for f in dev.faults],
            "fault_details": dev.fault_details,
            "registers": {},
            "notes": dev.notes,
        }
        for offset, read in dev.registers.items():
            dev_data["registers"][f"0x{offset:02X}"] = {
                "name": read.name,
                "raw": read.raw_bytes.hex(),
                "value": f"0x{read.raw_value:X}",
                "decoded": read.decoded,
            }
        data["devices"].append(dev_data)

    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)


def bus_stats_to_dict(bus_stats) -> Optional[dict]:
    """Serialize a CD3217Analyzer BusStats object for a JSON report."""
    if bus_stats is None:
        return None
    return {
        "pings": bus_stats.pings,
        "ping_failures": bus_stats.ping_failures,
        "ping_recovered": bus_stats.ping_recovered,
        "reads": bus_stats.reads,
        "read_failures": bus_stats.read_failures,
        "contaminated_rereads": bus_stats.contaminated_rereads,
        "nack_rate": round(bus_stats.nack_rate, 4),
        "bus_marginal": bool(bus_stats.marginal),
    }


def load_json_report(filepath: str) -> dict:
    """Load a previously saved JSON report."""
    with open(filepath, "r") as f:
        return json.load(f)


def save_csv_log(results: List[DeviceResult], filepath: str, append: bool = True) -> None:
    """Save device results as CSV for batch tracking."""
    file_exists = os.path.exists(filepath) and append

    fieldnames = [
        "timestamp", "address", "responds", "health", "health_score",
        "vendor_id", "device_id", "mode", "device_type",
        "fault_count", "faults", "fault_details", "notes",
    ]

    mode = "a" if file_exists else "w"
    with open(filepath, mode, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()

        for dev in results:
            writer.writerow({
                "timestamp": dev.timestamp,
                "address": f"0x{dev.address:02X}",
                "responds": dev.responds,
                "health": dev.health.value,
                "health_score": dev.health_score,
                "vendor_id": dev.vendor_id or "",
                "device_id": dev.device_id or "",
                "mode": dev.mode or "",
                "device_type": dev.device_type or "",
                "fault_count": len(dev.faults),
                "faults": "; ".join(f.value for f in dev.faults),
                "fault_details": "; ".join(dev.fault_details),
                "notes": dev.notes,
            })


def format_compact_result(dev: DeviceResult) -> str:
    """Format a single device result as a compact one-line string."""
    status = "OK" if dev.health == HealthStatus.PASS else \
             "WARN" if dev.health == HealthStatus.WARN else \
             "FAIL" if dev.health == HealthStatus.FAIL else "???"
    faults = f" [{','.join(f.value for f in dev.faults)}]" if dev.faults else ""
    mode = dev.mode or "N/A"
    return f"[{status}] 0x{dev.address:02X} mode={mode} score={dev.health_score}{faults}"


def format_batch_summary(results: List[DeviceResult]) -> str:
    """Format batch test results as a summary table."""
    lines = []
    lines.append(f"{'Address':>10} {'Health':>8} {'Score':>6} {'Mode':>8} {'Faults':>6} {'Details'}")
    lines.append("-" * 80)

    for dev in results:
        faults_str = str(len(dev.faults))
        details = dev.fault_details[0] if dev.fault_details else ""
        lines.append(
            f"  0x{dev.address:02X}    {dev.health.value:>6}   {dev.health_score:>4}   "
            f"{(dev.mode or 'N/A'):>6}   {faults_str:>4}    {details}"
        )

    total = len(results)
    passed = sum(1 for r in results if r.health == HealthStatus.PASS)
    warned = sum(1 for r in results if r.health == HealthStatus.WARN)
    failed = sum(1 for r in results if r.health == HealthStatus.FAIL)

    lines.append("-" * 80)
    lines.append(f"Total: {total} | Pass: {passed} | Warn: {warned} | Fail: {failed}")

    return "\n".join(lines)
