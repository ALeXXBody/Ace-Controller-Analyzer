"""Export report pass reuses collected data (audit fix 5, §4.18).

collect_bundle's report source used to call analyzer.full_diagnostic()
even when the registers pass had already scanned + diagnosed every chip
— ~2-3x the I2C traffic per export on a marginal bus. The report pass
now reuses scan_results/devices and only probes UNEXPECTED (non-ACE2)
scan hits.
"""

import unittest

from cd3217_analyzer.export_data import collect_bundle


class _CallCountingAdapter:
    """FakeAdapter + a scan counter, one healthy device at 0x38."""

    def __init__(self, unexpected_addr=None):
        self._addrs = {0x38}
        if unexpected_addr:
            self._addrs.add(unexpected_addr)
        self.scan_calls = 0
        self.diag_boards = []

    def scan(self, start=0x08, end=0x77):
        self.scan_calls += 1
        return sorted(a for a in self._addrs if start <= a <= end)

    def ping(self, address):
        return address in self._addrs

    def read_bytes(self, address, register, length):
        if address not in self._addrs:
            raise IOError("no ack")
        if register == 0x00:
            return (0x0451).to_bytes(4, "little")
        return bytes([0x11]) * length

    def info(self):
        return {"board": "esp32-s3-devkitc-1", "version": "0.6.6"}


def _mk_devices(addr, adapter):
    """A REAL DeviceResult from a real diagnose against the fake
    adapter — _serialize_report needs the complete object."""
    from cd3217_analyzer.analyzer import CD3217Analyzer
    analyzer = CD3217Analyzer(adapter, addresses=[addr])
    return analyzer.diagnose_device(addr)


class TestReportReuse(unittest.TestCase):
    def test_report_reuses_scan_and_devices(self):
        """With scan_results + devices supplied, the report bus scan must
        NOT re-run: the registers pass already did the work."""
        adapter = _CallCountingAdapter()
        bundle = collect_bundle(
            adapter, ["report"], "A2141",
            scan_results=[0x38],
            devices={0x38: _mk_devices(0x38, adapter)})
        report = bundle["data"]["report"]
        self.assertEqual(report["bus_scan_results"], ["0x38"])
        self.assertEqual(
            [d["address"] for d in report["devices"]], ["0x38"])
        self.assertEqual(adapter.scan_calls, 0)
        self.assertEqual(bundle["errors"], [])

    def test_unexpected_addr_is_probed(self):
        """A non-ACE2 scan hit gets the VID probe (full_diagnostic step 4).
        The fake answers 0x0451 at 0x62 too, so it passes the probe and is
        diagnosed with the 'non-standard address' note — proving step 4
        runs WITHOUT a full re-scan."""
        adapter = _CallCountingAdapter(unexpected_addr=0x62)
        bundle = collect_bundle(
            adapter, ["report"], "A2141",
            scan_results=[0x38, 0x62],
            devices={0x38: _mk_devices(0x38, adapter)})
        report = bundle["data"]["report"]
        self.assertEqual(adapter.scan_calls, 0)
        addrs = [d["address"] for d in report["devices"]]
        self.assertEqual(addrs, ["0x38", "0x62"])
        unexpected = [d for d in report["devices"]
                      if d["address"] == "0x62"][0]
        self.assertIn("non-standard address", unexpected.get("notes", ""))

    def test_no_collected_data_falls_back_to_full_diagnostic(self):
        adapter = _CallCountingAdapter()
        bundle = collect_bundle(adapter, ["report"], "A2141")
        report = bundle["data"]["report"]
        self.assertEqual(report["bus_scan_results"], ["0x38"])
        self.assertEqual(adapter.scan_calls, 1)   # full_diagnostic's scan


if __name__ == "__main__":
    unittest.main()
