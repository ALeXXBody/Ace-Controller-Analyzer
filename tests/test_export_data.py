"""Tests for the board-data export (export_data.py)."""

import json
import os
import tempfile
import unittest

from cd3217_analyzer.export_data import (
    DATA_DEFAULT,
    DATA_SOURCES,
    collect_bundle,
    load_token,
    sanitize_name,
    store_token,
    token_path,
    write_bundle,
)


class FakeAdapter:
    """Minimal I2C adapter: one responding device at 0x38 with real-ish VID."""
    def __init__(self, addrs=(0x38,)):
        self._addrs = set(addrs)

    def scan(self, start=0x08, end=0x77):
        return sorted(a for a in self._addrs if start <= a <= end)

    def ping(self, address):
        return address in self._addrs

    def read_bytes(self, address, register, length):
        if address not in self._addrs:
            raise IOError("no ack")
        # register 0x00 = VID 0x0451 little-endian
        if register == 0x00:
            raw = (0x0451).to_bytes(4, "little")
        else:
            raw = bytes([0x11]) * length
        return raw[:length]

    def info(self):
        return {
            "board": "esp32-s3-devkitc-1",
            "sda": 8, "scl": 9,
            "spi_sck": 12, "spi_miso": 13, "spi_mosi": 11, "spi_cs": 10,
            "hw": 2, "uart_rx": 4,
            "version": "0.6.6",
        }


class TestSanitizeName(unittest.TestCase):
    def test_ace_model_uppercased(self):
        self.assertEqual(sanitize_name("a2141"), "A2141")
        self.assertEqual(sanitize_name("A2338"), "A2338")

    def test_name_normalised(self):
        self.assertEqual(sanitize_name('MacBook Pro 13" 2019'),
                         "MacBook_Pro_13_2019")

    def test_no_path_traversal(self):
        self.assertNotIn("/", sanitize_name("../../etc/passwd"))
        self.assertNotIn("\\", sanitize_name("..\\..\\x"))

    def test_requires_name(self):
        with self.assertRaises(ValueError):
            sanitize_name("")
        with self.assertRaises(ValueError):
            sanitize_name("!!!")


class TestToken(unittest.TestCase):
    def test_store_and_load_roundtrip(self):
        try:
            store_token("ghp_test123")
            self.assertEqual(load_token(), "ghp_test123")
            self.assertTrue(os.path.exists(token_path()))
        finally:
            _remove_token()

    def test_no_token_by_default(self):
        # environment not set and file missing -> None
        import os
        os.environ.pop("CD3217_GH_TOKEN", None)
        if os.path.exists(token_path()):
            os.remove(token_path())
        self.assertIsNone(load_token())


def _remove_token():
    try:
        os.remove(token_path())
    except OSError:
        pass


class TestCollectBundle(unittest.TestCase):
    def test_info_and_report(self):
        adapter = FakeAdapter()
        bundle = collect_bundle(
            adapter, ["info", "registers", "report"], "A2141",
            scan_results=[0x38], mac_model="MacBook Pro 16\" 2019")
        self.assertEqual(bundle["format_version"], 1)
        self.assertEqual(bundle["name"], "A2141")
        self.assertEqual(bundle["mac_model"], 'MacBook Pro 16" 2019')
        self.assertEqual(bundle["data"]["info"]["board"], "esp32-s3-devkitc-1")
        self.assertIn("report", bundle["data"])
        self.assertIn("register_dump", bundle["data"])
        self.assertIn("0x38", bundle["data"]["register_dump"])

    def test_otp_source(self):
        adapter = FakeAdapter()
        bundle = collect_bundle(adapter, ["otp", "info"], "A2337",
                                scan_results=[0x38])
        self.assertIn("otp_dump", bundle["data"])
        self.assertIn("0x38", bundle["data"]["otp_dump"])

    def test_failed_source_recorded_not_fatal(self):
        class BadAdapter(FakeAdapter):
            def info(self):
                raise RuntimeError("busted")

        bundle = collect_bundle(BadAdapter(), ["info", "registers"], "X1",
                                scan_results=[0x38])
        # info failed, registers still collected, error recorded
        self.assertNotIn("info", bundle["data"])
        self.assertIn("register_dump", bundle["data"])
        self.assertTrue(any("info" in e for e in bundle["errors"]))

    def test_sources_defaults_are_valid(self):
        for s in DATA_DEFAULT:
            self.assertIn(s, [x[0] for x in DATA_SOURCES])


class TestWriteBundle(unittest.TestCase):
    def test_writes_json(self):
        d = tempfile.mkdtemp()
        p = write_bundle({"a": 1}, "A1708", out_dir=d)
        self.assertTrue(os.path.exists(p))
        with open(p) as f:
            self.assertEqual(json.load(f)["a"], 1)
        self.assertTrue(p.endswith("A1708.json"))


if __name__ == "__main__":
    unittest.main()
