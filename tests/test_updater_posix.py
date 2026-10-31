"""Cross-platform updater tests (§4.24, step 3).

install_mode() deb detection, POSIX apply_update modes (deb / linux tar /
macOS .app), the swap.sh + deb scripts, and asset lookup for the
version-suffixed .deb name.
"""

import io
import os
import sys
import tarfile
import unittest
import zipfile
from unittest.mock import MagicMock, patch

from cd3217_analyzer import updater


def _release(**extra):
    rel = {
        "tag": "v9.9.9", "version": "9.9.9",
        "setup_url": "u/setup", "portable_url": "u/portable",
        "deb_url": "u/deb", "macos_url": "u/mac",
        "linux_portable_url": "u/linux",
        "url": "u/rel", "notes": "", "assets": {},
    }
    rel.update(extra)
    return rel


class TestAssetLookup(unittest.TestCase):
    def test_deb_found_by_prefix_suffix(self):
        import json
        assets = {
            "aca-analyzer_0.12.15_amd64.deb": "u1",
            "aca_pico.uf2": "u2",
        }
        items = ",".join(
            json.dumps({"name": k, "browser_download_url": v})
            for k, v in assets.items())
        payload = ('{"tag_name":"v0.12.15","assets":[' + items + ']}')
        with patch.object(updater.urllib.request, "urlopen",
                          side_effect=lambda *a, **k:
                          io.BytesIO(payload.encode())):
            rel = updater.fetch_latest_release()
        self.assertEqual(rel["deb_url"], "u1")
        self.assertEqual(rel["tag"], "v0.12.15")

    def test_no_deb_asset_none(self):
        with patch.object(updater.urllib.request, "urlopen",
                          side_effect=lambda *a, **k:
                          io.BytesIO(b'{"tag_name":"v1","assets":[]}')):
            rel = updater.fetch_latest_release()
        self.assertIsNone(rel["deb_url"])


class TestInstallModeDeb(unittest.TestCase):
    def test_deb_mode_detected(self):
        self.assertIsNone(
            updater.__dict__.get("_x"), None)  # sanity import marker
        with patch.object(sys, "frozen", True, create=True), \
             patch.object(updater.os, "name", "posix"), \
             patch.object(sys, "platform", "linux"), \
             patch.object(sys, "executable",
                          "/opt/aca-analyzer/ACA"), \
             patch.object(updater.os.path, "isfile",
                          return_value=True):
            self.assertEqual(updater.install_mode(), "deb")

    def test_linux_portable_when_no_marker(self):
        with patch.object(sys, "frozen", True, create=True), \
             patch.object(updater.os, "name", "posix"), \
             patch.object(sys, "platform", "linux"), \
             patch.object(sys, "executable", "/opt/aca-analyzer/ACA"), \
             patch.object(updater.os.path, "isfile",
                          return_value=False):
            self.assertEqual(updater.install_mode(), "portable")


class _FakeDL:
    """Records the download and fabricates the archive at dest."""
    def __init__(self, make_archive):
        self.make = make_archive
        self.calls = []

    def __call__(self, url, dest, progress=None):
        self.calls.append(url)
        self.make(dest)
        return dest


class TestApplyUpdatePosix(unittest.TestCase):
    def setUp(self):
        self.dl = MagicMock()
        self.tmpdir = None

    def _prep_tmp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp(prefix=".aca_test_")
        return self.tmpdir

    def test_linux_tar_flow(self):
        """Portable tar: extract, find the ACA binary, spawn swap.sh with
        launcher, return swap-launched (the GUI exits itself)."""
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            data = b"#!/bin/sh\n"  # tiny stand-in for the pyinstaller exe
            info = tarfile.TarInfo("ACA_Linux/ACA")
            info.size = len(data)
            info.mode = 0o755          # executable bit — the finder checks
            tf.addfile(info, io.BytesIO(data))
        made = _FakeDL(lambda dest: open(dest, "wb").write(buf.getvalue()))
        spawned = []

        with patch.object(sys, "frozen", True, create=True), \
             patch.object(updater.os, "name", "posix"), \
             patch.object(sys, "platform", "linux"), \
             patch.object(sys, "executable", "/home/u/apps/ACA/ACA"), \
             patch.object(updater, "download_file", made), \
             patch.object(updater, "tempfile") as tf_mod, \
             patch.object(updater.subprocess, "Popen",
                          side_effect=lambda *a, **k:
                          spawned.append(a)):
            tf_mod.mkdtemp.return_value = self._prep_tmp()
            os.makedirs(self.tmpdir, exist_ok=True)
            result = updater.apply_update(_release())

        self.assertEqual(result["action"], "swap-launched")
        self.assertEqual(result["mode"], "portable")
        self.assertIn("u/linux", made.calls)
        self.assertEqual(len(spawned), 1)
        self.assertEqual(spawned[0][0][0], "/bin/sh")

    def test_macos_app_flow(self):
        """macOS zip containing ACA.app — swap targets the BUNDLE."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("ACA.app/Contents/MacOS/ACA", "MZ")
            zf.writestr("ACA.app/Contents/Info.plist", "<plist/>")
        made = _FakeDL(lambda dest: open(dest, "wb").write(buf.getvalue()))
        spawned = []

        with patch.object(sys, "frozen", True, create=True), \
             patch.object(updater.os, "name", "posix"), \
             patch.object(sys, "platform", "darwin"), \
             patch.object(sys, "executable",
                          "/Applications/ACA.app/Contents/MacOS/ACA"), \
             patch.object(updater, "download_file", made), \
             patch.object(updater, "tempfile") as tf_mod, \
             patch.object(updater.subprocess, "Popen",
                          side_effect=lambda *a, **k:
                          spawned.append(a)):
            tf_mod.mkdtemp.return_value = self._prep_tmp()
            os.makedirs(self.tmpdir, exist_ok=True)
            result = updater.apply_update(_release())
        self.assertEqual(result["action"], "swap-launched")
        self.assertTrue(result["path"].endswith("ACA.app"))
        self.assertIn("u/mac", made.calls)
        self.assertEqual(len(spawned), 1)
        self.assertEqual(spawned[0][0][0], "/bin/sh")

    def test_deb_flow(self):
        made = _FakeDL(lambda dest: open(dest, "wb").write(b"deb"))
        spawned = []
        with patch.object(sys, "frozen", True, create=True), \
             patch.object(updater.os, "name", "posix"), \
             patch.object(sys, "platform", "linux"), \
             patch.object(sys, "executable", "/opt/aca-analyzer/ACA"), \
             patch.object(updater.os.path, "isfile", return_value=True), \
             patch.object(updater, "download_file", made), \
             patch.object(updater, "tempfile") as tf_mod, \
             patch.object(updater.subprocess, "Popen",
                          side_effect=lambda *a, **k:
                          spawned.append(a)):
            tf_mod.mkdtemp.return_value = self._prep_tmp()
            os.makedirs(self.tmpdir, exist_ok=True)
            result = updater.apply_update(_release())
        self.assertEqual(result["mode"], "deb")
        self.assertEqual(result["action"], "swap-launched")
        self.assertIn("u/deb", made.calls)
        args = spawned[0][0]
        self.assertEqual(args[0], "/bin/sh")

    def test_missing_asset_raises(self):
        rel = _release(linux_portable_url=None)
        with patch.object(sys, "frozen", True, create=True), \
             patch.object(updater.os, "name", "posix"), \
             patch.object(sys, "platform", "linux"), \
             patch.object(sys, "executable", "/home/u/apps/ACA/ACA"), \
             patch.object(updater, "tempfile") as tf_mod:
            tf_mod.mkdtemp.return_value = self._prep_tmp()
            os.makedirs(self.tmpdir, exist_ok=True)
            with self.assertRaises(IOError):
                updater.apply_update(rel)

    def test_windows_tar_not_used_on_windows(self):
        """Windows routing intact: nt goes down the zip/PowerShell path
        even when linux assets exist."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("ACA/ACA.exe", "MZ")
        made = _FakeDL(lambda dest: open(dest, "wb").write(buf.getvalue()))
        spawned = []
        with patch.object(sys, "frozen", True, create=True), \
             patch.object(updater.os, "name", "nt"), \
             patch.object(sys, "executable", "C:/apps/ACA/ACA.exe"), \
             patch.object(updater, "download_file", made), \
             patch.object(updater, "tempfile") as tf_mod, \
             patch.object(updater, "_find_app_dir",
                          return_value="/tmp/aca_w/ACA"), \
             patch.object(updater.subprocess, "Popen",
                          side_effect=lambda *a, **k:
                          spawned.append(a)):
            tf_mod.mkdtemp.return_value = self._prep_tmp()
            os.makedirs(self.tmpdir, exist_ok=True)
            result = updater.apply_update(_release())
        self.assertEqual(result["action"], "swap-launched")
        self.assertIn("u/portable", made.calls)   # NOT the linux url
        self.assertIn("powershell", spawned[0][0][0])


if __name__ == "__main__":
    unittest.main()
