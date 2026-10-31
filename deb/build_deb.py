#!/usr/bin/env python3
"""Stage + build the ACA .deb for Debian/Ubuntu (CI & local).

Input : a pyinstaller onedir dist (default dist/ACA) that contains the
        'ACA' executable and its _internal support dir.
Output: dist/aca-analyzer_<version>_amd64.deb

Layout installed by the package (Filesystem Hierarchy Standard):
  /opt/aca-analyzer/             the pyinstaller tree + .deb-install marker
  /usr/bin/aca-analyzer          exec wrapper for the GUI (and CLI)
  /usr/share/applications/aca-analyzer.desktop   GUI menu entry
  /usr/share/pixmaps/aca-analyzer.png            menu icon

The updater recognizes deb installs by the marker + executable path —
keep the marker in sync with updater.DEB_MARKER / DEB_INSTALL_ROOT.

Usage:
    python3 packaging/build_deb.py [--dist DIR] [--version X.Y.Z]
Requires dpkg-deb (present on Debian/Ubuntu builders). No outside deps.
"""

import argparse
import os
import shutil
import subprocess
import sys

DEB_ROOT = "/opt/aca-analyzer"
WRAPPER = """#!/bin/sh
# ACA (Ace Controller Analyzer) launcher — GUI when no args, CLI otherwise.
exec "{root}/ACA" "$@"
"""


def stage_tree(dist: str, version: str) -> str:
    """Copy the pyinstaller output into the package staging tree."""
    src = os.path.abspath(dist)
    binary = os.path.join(src, "ACA")
    if not os.path.isfile(binary):
        raise SystemExit(f"no executable at {binary} — is dist correct?")

    stg = os.path.join("deb", ".deb_stage")
    shutil.rmtree(stg, ignore_errors=True)
    opt = os.path.join(stg, DEB_ROOT.lstrip("/"))
    os.makedirs(opt, exist_ok=True)
    os.makedirs(os.path.join(stg, "usr/bin"), exist_ok=True)
    os.makedirs(os.path.join(stg, "usr/share/applications"), exist_ok=True)
    os.makedirs(os.path.join(stg, "usr/share/pixmaps"), exist_ok=True)

    # the whole pyinstaller tree (opt dir pre-exists from makedirs above)
    shutil.copytree(src, opt, symlinks=True, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("*.deb"))

    # deb-install marker (recognised by updater.install_mode)
    marker = os.path.join(opt, ".deb-install")
    with open(marker, "w") as f:
        f.write(f"aca-analyzer {version}\n")

    # launcher wrapper
    wrapper = os.path.join(stg, "usr/bin/aca-analyzer")
    with open(wrapper, "w") as f:
        f.write(WRAPPER.format(root=DEB_ROOT))
    os.chmod(wrapper, 0o755)

    # .desktop entry + icon
    with open(os.path.join(stg, "usr/share/applications/"
                                "aca-analyzer.desktop"), "w") as f:
        f.write(
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=ACA Analyzer\n"
            "GenericName=CD3217 / ACE2 I2C Diagnostic Analyzer\n"
            "Comment=CD3217B12 (Apple ACE2) USB-C controller diagnostics\n"
            f"Exec={DEB_ROOT}/ACA\n"
            "Icon=aca-analyzer\n"
            "Terminal=false\n"
            "Categories=Development;Electronics;Utility;\n")
    icon_src = os.path.join("assets", "icon.png")
    if os.path.exists(icon_src):
        shutil.copy(icon_src, os.path.join(
            stg, "usr/share/pixmaps", "aca-analyzer.png"))

    # control file
    depend = "libc6 (>= 2.31), libusb-1.0-0, libx11-6, libgl1, " \
             "libasound2 | libasound2t64"
    deb_dir = os.path.join(stg, "DEBIAN")
    os.makedirs(deb_dir, exist_ok=True)
    with open(os.path.join(deb_dir, "control"), "w") as f:
        f.write(
            f"Package: aca-analyzer\n"
            f"Version: {version}\n"
            f"Section: utils\n"
            f"Priority: optional\n"
            f"Architecture: amd64\n"
            f"Depends: {depend}\n"
            f"Maintainer: ALeXXBody <ALeXXBody@users.noreply.github.com>\n"
            f"Homepage: https://github.com/ALeXXBody/\n"
            f"          Ace-Controller-Analyzer\n"
            f"Description: CD3217 / Apple ACE2 I2C diagnostic analyzer\n"
            f" Scan, diagnose and watch the CD3217B12 (Apple ACE2)\n"
            f" USB-C port-controller chips on MacBook boards over the\n"
            f" bench I2C bridge. Includes the scheduler/OTP/flash tools\n"
            f" and the GUI.\n")
    return stg


def build_deb(stg: str, version: str) -> str:
    dest = "dist"
    os.makedirs(dest, exist_ok=True)
    deb = os.path.join(dest, f"aca-analyzer_{version}_amd64.deb")
    # chown the tree to root:root so dpkg-deb records sane ownership
    if os.geteuid() == 0:
        for root, dirs, files in os.walk(stg):
            for d in dirs:
                os.chown(os.path.join(root, d), 0, 0)
            for fn in files:
                os.chown(os.path.join(root, fn), 0, 0)
        os.chown(os.path.join(stg, "DEBIAN"), 0, 0)
    else:
        # sudoless CI runs as root on GH runners; local gen needs fakeroot
        command = ["fakeroot", "dpkg-deb", "--build", stg, deb]
        print(f"non-root build: {' '.join(command)}")
        subprocess.run(command, check=True)
        return deb
    subprocess.run(["dpkg-deb", "--build", stg, deb], check=True)
    return deb


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dist", default="dist/ACA")
    ap.add_argument("--version", required=True)
    args = ap.parse_args()
    stg = stage_tree(args.dist, args.version)
    deb = build_deb(stg, args.version)
    print(f"built {deb}")


if __name__ == "__main__":
    main()
