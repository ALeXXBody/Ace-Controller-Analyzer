"""Tests for packaging/build_deb.py — the Linux .deb staging (§4.24).

The deb layout is load-bearing: the GUI menu entry, the marker file the
updater keys on, and the /usr/bin wrapper. Staging must produce the FHS
tree and control file even without dpkg-deb present.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from deb.build_deb import stage_tree  # noqa: E402


class TestStageTree(unittest.TestCase):
    def setUp(self):
        # fake pyinstaller dist: ACA executable + _internal
        import tempfile
        self.td = tempfile.mkdtemp(prefix=".deb_t_")
        self.dist = os.path.join(self.td, "dist", "ACA")
        os.makedirs(self.dist)
        with open(os.path.join(self.dist, "ACA"), "wb") as f:
            f.write(b"\x7fELF")
        os.makedirs(os.path.join(self.dist, "_internal"))
        self.oldcwd = os.getcwd()
        os.chdir(os.path.dirname(self.td))   # repo-equivalent root cwd

    def tearDown(self):
        os.chdir(self.oldcwd)

    def _stage(self, version="0.12.16"):
        return stage_tree(self.dist, version)

    def test_tree_layout(self):
        stg = self._stage()
        self.assertTrue(os.path.isfile(
            os.path.join(stg, "opt/aca-analyzer/ACA")))
        self.assertTrue(os.path.isdir(
            os.path.join(stg, "opt/aca-analyzer/_internal")))
        self.assertTrue(os.path.isfile(os.path.join(
            stg, "opt/aca-analyzer/.deb-install")))
        self.assertTrue(os.path.isfile(os.path.join(stg, "usr/bin/",
                                                    "aca-analyzer")))
        # executable bits on the wrapper
        self.assertTrue(os.access(
            os.path.join(stg, "usr/bin/aca-analyzer"), os.X_OK))

    def test_marker_matches_updater_constants(self):
        """The marker path MUST match cd3217_analyzer.updater — a mismatch
        silently drops deb installs back to portable update mode."""
        from cd3217_analyzer.updater import DEB_INSTALL_ROOT, DEB_MARKER
        stg = self._stage()
        self.assertTrue(os.path.isfile(
            os.path.join(stg, DEB_INSTALL_ROOT.lstrip("/"), DEB_MARKER)))

    def test_desktop_and_control(self):
        stg = self._stage(version="9.9.9")
        desktop = open(os.path.join(
            stg, "usr/share/applications/aca-analyzer.desktop")).read()
        self.assertIn("Exec=/opt/aca-analyzer/ACA", desktop)
        control = open(os.path.join(stg, "DEBIAN/control")).read()
        self.assertIn("Package: aca-analyzer", control)
        self.assertIn("Version: 9.9.9", control)
        self.assertIn("Architecture: amd64", control)
        self.assertIn("Depends:", control)

    def test_missing_binary_raises(self):
        with patch.object(os.path, "isfile", return_value=False):
            with self.assertRaises(SystemExit):
                self._stage()


if __name__ == "__main__":
    unittest.main()
