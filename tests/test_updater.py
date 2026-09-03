"""Tests for the self-update mechanism (cd3217_analyzer/updater.py)."""

import io
import json
import os
import struct
import sys
import unittest
import zipfile
from unittest.mock import patch

from cd3217_analyzer import updater


def fake_release(tag="v9.9.9", with_assets=True):
    assets = []
    if with_assets:
        assets = [
            {"name": updater.SETUP_ASSET,
             "browser_download_url": f"https://x/{updater.SETUP_ASSET}"},
            {"name": updater.PORTABLE_ASSET,
             "browser_download_url": f"https://x/{updater.PORTABLE_ASSET}"},
            {"name": "cd3217_pico.uf2",
             "browser_download_url": "https://x/pico.uf2"},
        ]
    return {
        "tag_name": tag,
        "html_url": "https://github.com/x/releases/v9.9.9",
        "body": "release notes",
        "assets": assets,
    }


class FakeResponse(io.BytesIO):
    """urlopen()-ish context manager returning canned bytes."""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @property
    def headers(self):
        return {"Content-Length": str(len(self.getvalue()))}


class TestVersionCompare(unittest.TestCase):
    def test_newer(self):
        self.assertTrue(updater.is_newer("0.4.3", "0.4.2"))
        self.assertTrue(updater.is_newer("v0.5", "0.4.9"))
        self.assertTrue(updater.is_newer("0.4.3.1", "0.4.3"))
        self.assertTrue(updater.is_newer("1.0", "0.9"))

    def test_not_newer(self):
        self.assertFalse(updater.is_newer("0.4.2", "0.4.2"))
        self.assertFalse(updater.is_newer("v0.4.2", "0.4.3"))
        self.assertFalse(updater.is_newer("0.4", "0.4.0"))  # equal
        self.assertFalse(updater.is_newer("", "0.4.2"))
        self.assertFalse(updater.is_newer("garbage", "0.4.2"))

    def test_parse(self):
        self.assertEqual(updater.parse_version("v1.2.3"), (1, 2, 3))
        self.assertEqual(updater.parse_version("0.4"), (0, 4))
        self.assertEqual(updater.parse_version(""), (0,))


class TestFetchRelease(unittest.TestCase):
    def test_fetch_parses_assets(self):
        payload = json.dumps(fake_release()).encode()
        with patch.object(updater.urllib.request, "urlopen",
                          return_value=FakeResponse(payload)):
            rel = updater.fetch_latest_release()
        self.assertEqual(rel["version"], "9.9.9")
        self.assertEqual(rel["setup_url"], f"https://x/{updater.SETUP_ASSET}")
        self.assertEqual(rel["portable_url"],
                         f"https://x/{updater.PORTABLE_ASSET}")

    def test_fetch_network_error_returns_none(self):
        with patch.object(updater.urllib.request, "urlopen",
                          side_effect=OSError("offline")):
            self.assertIsNone(updater.fetch_latest_release())

    def test_check_for_update(self):
        payload = json.dumps(fake_release("v0.4.3")).encode()
        fresh = lambda *a, **k: FakeResponse(payload)  # noqa: E731
        with patch.object(updater.urllib.request, "urlopen",
                          side_effect=fresh):
            self.assertIsNone(updater.check_for_update("0.4.3"))  # same
            self.assertIsNotNone(updater.check_for_update("0.4.2"))  # older


class TestInstallMode(unittest.TestCase):
    def test_source_mode_when_not_frozen(self):
        with patch.object(sys, "frozen", False, create=True):
            self.assertEqual(updater.install_mode(), "source")

    def test_portable_when_no_registry(self):
        with patch.object(sys, "frozen", True, create=True), \
             patch.dict(os.environ, {}):
            if os.name == "nt":
                import winreg
                with patch.object(winreg, "OpenKey",
                                  side_effect=OSError("no key")):
                    self.assertEqual(updater.install_mode(), "portable")
            else:
                # non-Windows: falls through to portable
                self.assertEqual(updater.install_mode(), "portable")


class TestFindAppDir(unittest.TestCase):
    def test_finds_nested_app_dir(self):
        # zip layout: ACA/ACA.exe + files
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("ACA/ACA.exe", "MZ")
            zf.writestr("ACA/_internal/x.dll", "d")
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as zf:
                zf.extractall(td)
            self.assertEqual(
                os.path.basename(updater._find_app_dir(td)),
                "ACA")

    def test_finds_flat_layout(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            open(os.path.join(td, updater.APP_EXE), "w").write("MZ")
            self.assertEqual(updater._find_app_dir(td), td)

    def test_missing_exe(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(updater._find_app_dir(td))


class TestDownloadFile(unittest.TestCase):
    def test_download_with_progress(self):
        data = b"0123456789" * 100
        calls = []

        def cb(done, total):
            calls.append((done, total))

        import tempfile
        with tempfile.TemporaryDirectory() as td:
            dest = os.path.join(td, "f.bin")
            with patch.object(updater.urllib.request, "urlopen",
                              return_value=FakeResponse(data)):
                updater.download_file("http://x", dest, cb)
            self.assertEqual(open(dest, "rb").read(), data)
        self.assertEqual(calls[-1], (len(data), len(data)))


if __name__ == "__main__":
    unittest.main()
