"""Tests for bundle verification: collection-time recheck + file validation."""

import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock

from cd3217_analyzer.export_data import (
    collect_bundle, validate_bundle, write_bundle,
)


def _mk_regs(vendor_hex="04280000", did_hex="041832cd",
             mode_hex="20504150", type_hex="20493243",
             info_hex=None):
    def hx(b):
        return bytes.fromhex(b) if isinstance(b, str) else b
    regs = {
        "0x00": {"name": "VID", "raw": vendor_hex, "value": "0x2804",
                 "decoded": ""},
        "0x01": {"name": "DID", "raw": did_hex, "value": "0xCD321804",
                 "decoded": ""},
        "0x03": {"name": "Mode", "raw": mode_hex, "value": "0x50415020",
                 "decoded": "APP"},
        "0x04": {"name": "Type", "raw": type_hex, "value": "0x49324320",
                 "decoded": "I2C"},
        "0x2F": {"name": "DeviceInfo",
                 "raw": info_hex or "40434433323137202020"
                        "4857303032322046573030322e3137302e3030205a41434532"
                        "2d4a333136503031500000",
                 "value": "0x0", "decoded": ""},
    }
    return regs


class TestRecheck(unittest.TestCase):
    def _adapter(self, garbage_first_pass=False):
        """Fake bridge: healthy chip; optional garbled first read pass."""
        adapter = MagicMock()
        adapter.info.return_value = {"board": "pico", "sda": 8, "scl": 9}
        state = {"reads": 0}

        def rb(addr, reg, length):
            state["reads"] += 1
            if garbage_first_pass and state["reads"] <= 8:
                return bytes([0xFF] * length)      # first pass garbled
            return {
                0x00: bytes.fromhex("04280000"),
                0x01: bytes.fromhex("041832cd"),
                0x03: bytes.fromhex("20504150"),
                0x04: bytes.fromhex("20493243"),
                0x36: bytes.fromhex("e1200350"),
                0x2F: b"@CD3217   HW0022 FW002.170.00 ZACE2-J316P01P\x00\x00",
            }.get(reg, bytes([0x5A] * length))[:length]

        adapter.read_bytes.side_effect = rb
        adapter.scan.return_value = [0x38]
        return adapter

    def test_clean_chip_needs_no_recheck(self):
        adapter = self._adapter()
        bundle = collect_bundle(adapter, ["registers"], "A2251",
                                scan_results=[0x38])
        v = bundle["verification"]["register_dump"]["0x38"]
        self.assertEqual(v["status"], "ok")
        self.assertEqual(v["rechecks"], 0)
        # identity data clean in the bundle
        self.assertEqual(bundle["data"]["register_dump"]["0x38"]["0x00"]["raw"],
                         "04280000")

    def test_garbled_identity_is_rechecked_and_recovered(self):
        adapter = self._adapter(garbage_first_pass=True)
        bundle = collect_bundle(adapter, ["registers"], "A2251",
                                scan_results=[0x38])
        v = bundle["verification"]["register_dump"]["0x38"]
        self.assertEqual(v["rechecks"], 1)
        self.assertIn(v["status"], ("ok", "recovered"))
        # the bundled snapshot must be CLEAN, not the garbled first pass
        raw = bundle["data"]["register_dump"]["0x38"]["0x00"]["raw"]
        self.assertNotIn(set(raw), ({"0"}, {"f"}))


class TestValidateBundle(unittest.TestCase):
    def _write(self, bundle, name="T"):
        td = tempfile.mkdtemp()
        return write_bundle(bundle, name, out_dir=td)

    def _bundle(self, **over):
        b = {
            "format": "cd3217-analyzer/board-export",
            "format_version": 1,
            "name": "A2251",
            "generated_utc": "2026-09-02T00:00:00",
            "app_version": "0.7.9",
            "adapter_type": "UsbBridgeAdapter",
            "mac_model": "A2251",
            "sources": ["registers"],
            "data": {"register_dump": {"0x38": _mk_regs()}},
            "verification": {"register_dump": {"0x38": {
                "status": "ok", "rechecks": 0}}},
            "errors": [],
        }
        b.update(over)
        return b

    def test_valid_complete_bundle(self):
        path = self._write(self._bundle())
        res = validate_bundle(path)
        self.assertTrue(res["valid"], res)
        names = [c["name"] for c in res["checks"]]
        self.assertIn("integrity (sha256)", names)
        self.assertIn("model coverage", names)
        self.assertTrue(any("verified clean" in c["detail"]
                            for c in res["checks"]
                            if c["name"] == "collection recheck"))

    def test_missing_chip_data_flagged(self):
        b = self._bundle(data={"register_dump": {
            "0x38": _mk_regs(vendor_hex="ff" * 4)}})
        path = self._write(b)
        res = validate_bundle(path)
        self.assertTrue(res["valid"])          # warn, not critical
        self.assertTrue(any("garbled" in c["detail"]
                            for c in res["checks"] if "chip 0x38" in c["name"]))

    def test_partially_garbled_vid_triggers_recheck(self):
        """Real-world case (user's A2251 export): chip 0x3C read VID 0xFF04
        — partially garbled, not all-FF — and slipped into the bundle. The
        recheck must catch an unexpected VID and recover a clean one."""
        adapter = MagicMock()
        adapter.info.return_value = {"board": "pico", "sda": 8, "scl": 9}
        reads = {"n": 0}

        def rb(addr, reg, length):
            reads["n"] += 1
            if reads["n"] == 1:
                return bytes.fromhex("ff042800")   # garbled VID (0xFF04)
            return {
                0x00: bytes.fromhex("04280000"),   # 0x2804 Apple
                0x01: bytes.fromhex("041832cd"),
                0x03: bytes.fromhex("20504150"),
                0x04: bytes.fromhex("20493243"),
                0x2F: b"@CD3217   HW0022 FW002.170.00 ZACE2-J316P01P",
            }.get(reg, bytes([0x5A] * length))[:length]

        adapter.read_bytes.side_effect = rb
        adapter.scan.return_value = [0x3C]
        bundle = collect_bundle(adapter, ["registers"], "A2251",
                                scan_results=[0x3C])
        v = bundle["verification"]["register_dump"]["0x3C"]
        self.assertIn(v["status"], ("ok", "recovered"))
        self.assertEqual(
            bundle["data"]["register_dump"]["0x3C"]["0x00"]["raw"],
            "04280000")

    def test_low_otp_fill_flagged(self):
        otp = {f"0x{o:02X}": "aa" for o in range(0, 20)}
        b = self._bundle(sources=["registers", "otp"],
                         data={"register_dump": {"0x38": _mk_regs()},
                               "otp_dump": {"0x38": {
                                   "address": 0x38, "registers": otp,
                                   "read_errors": [0x50] * 12}}})
        path = self._write(b)
        res = validate_bundle(path)
        self.assertTrue(any("otp 0x38" in c["name"] and "only 20/32" in
                            c["detail"] for c in res["checks"]))

    def test_tampered_file_fails_integrity(self):
        path = self._write(self._bundle())
        with open(path, "a") as f:
            f.write(" ")
        res = validate_bundle(path)
        self.assertFalse(res["valid"])
        self.assertTrue(any(c["name"] == "integrity (sha256)"
                            and c["level"] == "critical"
                            for c in res["checks"]))

    def test_corrupt_json_fails(self):
        td = tempfile.mkdtemp()
        path = os.path.join(td, "broken.json")
        with open(path, "w") as f:
            f.write("{not json")
        res = validate_bundle(path)
        self.assertFalse(res["valid"])

    def test_model_coverage_warns_on_missing_sockets(self):
        # A2251 expects 4 sockets; bundle captured only 1 chip
        path = self._write(self._bundle())
        res = validate_bundle(path)
        cov = [c for c in res["checks"] if c["name"] == "model coverage"]
        self.assertTrue(cov and "only 1/4" in cov[0]["detail"])


class TestSettings(unittest.TestCase):
    def test_roundtrip(self):
        from cd3217_analyzer.export_data import (
            load_settings, save_settings, settings_path)
        with tempfile.TemporaryDirectory() as td:
            sp = os.path.join(td, "settings.json")
            orig = os.path.join(os.path.expanduser("~"), ".cd3217_analyzer")
            import unittest.mock as um
            with um.patch(
                    "cd3217_analyzer.export_data.settings_path",
                    return_value=sp):
                save_settings({"last_adapter": "USB Bridge (board)",
                               "last_port": "COM10"})
                st = load_settings()
        self.assertEqual(st["last_port"], "COM10")
        self.assertEqual(st["last_adapter"], "USB Bridge (board)")

    def test_missing_file_returns_empty(self):
        from cd3217_analyzer.export_data import load_settings
        import unittest.mock as um
        with tempfile.TemporaryDirectory() as td:
            sp = os.path.join(td, "none.json")
            with um.patch(
                    "cd3217_analyzer.export_data.settings_path",
                    return_value=sp):
                self.assertEqual(load_settings(), {})


if __name__ == "__main__":
    unittest.main()
