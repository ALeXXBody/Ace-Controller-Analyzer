"""Tests for firmware flashing (UF2 bootsel + file dispatch)."""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from cd3217_analyzer import flash_board


class TestFlashBoard(unittest.TestCase):
    def test_find_bootsel_drives_linux(self):
        # Fake Linux mounts named like RPI-RP2 / RP2350
        with tempfile.TemporaryDirectory() as td:
            boot = os.path.join(td, "RPI-RP2")
            os.makedirs(boot, exist_ok=True)
            other = os.path.join(td, "USB")
            os.makedirs(other, exist_ok=True)
            with patch.object(flash_board.sys, "platform", "linux"), \
                 patch("glob.glob", return_value=[boot + "/", other + "/"]):
                drives = flash_board.find_bootsel_drives()
        self.assertIn(boot.rstrip("/") if boot.endswith("/") else boot,
                      [d.rstrip("/") for d in drives])

    def test_flash_pico_uf2_copies_and_verifies(self):
        # Simulate: drive is present, then unmounts after copy (success).
        with tempfile.TemporaryDirectory() as td:
            uf2 = os.path.join(td, "cd3217_pico.uf2")
            with open(uf2, "wb") as fh:
                fh.write(b"UF2\x00" * 8)
            boot = os.path.join(td, "RPI-RP2")
            os.makedirs(boot, exist_ok=True)

            state = {"count": 0}

            def fake_find():
                state["count"] += 1
                # first call (detect) and the copy happen, then it disappears
                return [boot] if state["count"] <= 2 else []

            with patch.object(flash_board, "find_bootsel_drives", fake_find):
                msg = flash_board.flash_pico_uf2(uf2, bootsel_drive=boot, timeout=5)
            self.assertIn("rebooted", msg)
            self.assertTrue(os.path.exists(os.path.join(boot, "cd3217_pico.uf2")))

    def test_flash_pico_uf2_verify_timeout(self):
        # Drive never unmounts -> report the flash did not complete.
        with tempfile.TemporaryDirectory() as td:
            uf2 = os.path.join(td, "cd3217_pico.uf2")
            with open(uf2, "wb") as fh:
                fh.write(b"UF2\x00" * 8)
            boot = os.path.join(td, "RPI-RP2")
            os.makedirs(boot, exist_ok=True)
            with patch.object(flash_board, "find_bootsel_drives", return_value=[boot]):
                with self.assertRaises(RuntimeError):
                    flash_board.flash_pico_uf2(uf2, bootsel_drive=boot, timeout=1)

    def test_flash_pico_uf2_multiple_drives(self):
        with tempfile.TemporaryDirectory() as td:
            uf2 = os.path.join(td, "cd3217_pico.uf2")
            with open(uf2, "wb") as fh:
                fh.write(b"UF2\x00" * 8)
            with patch.object(flash_board, "find_bootsel_drives",
                              return_value=["R:/", "S:/"]):
                with self.assertRaises(RuntimeError):
                    flash_board.flash_pico_uf2(uf2, timeout=1)

    def test_flash_pico_uf2_missing(self):
        with self.assertRaises(FileNotFoundError):
            flash_board.flash_pico_uf2("/nonexistent/x.uf2")

    def test_flash_pico_uf2_bad_ext(self):
        with tempfile.TemporaryDirectory() as td:
            bad = os.path.join(td, "firmware.zip")
            with open(bad, "wb") as fh:
                fh.write(b"data")
            with self.assertRaises(ValueError):
                flash_board.flash_pico_uf2(bad)
        # wrong path still raises FileNotFoundError
        with self.assertRaises(FileNotFoundError):
            flash_board.flash_pico_uf2("/nonexistent/x.uf2")

    def test_dispatch_uf2(self):
        with patch.object(flash_board, "flash_pico_uf2", return_value="ok") as m:
            flash_board.flash_file("x.uf2", bootsel_drive="R:/")
        m.assert_called_once()

    def test_dispatch_bin_needs_port(self):
        with self.assertRaises(ValueError):
            flash_board.flash_file("x.bin")
        with patch.object(flash_board, "flash_esptool", return_value="ok") as m:
            flash_board.flash_file("x.bin", port="COM5")
        m.assert_called_once()


if __name__ == "__main__":
    unittest.main()
