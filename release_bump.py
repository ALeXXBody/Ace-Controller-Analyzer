#!/usr/bin/env python3
"""Bump cd3217_analyzer/__init__.py to the given version — exits non-zero
if the pattern does not match exactly (prevents silent stale-version
releases). Usage: python3 release_bump.py 0.9.7"""
import re
import subprocess
import sys

if len(sys.argv) != 2:
    print("usage: release_bump.py VERSION")
    sys.exit(2)
version = sys.argv[1].lstrip("v")
path = "cd3217_analyzer/__init__.py"
src = open(path).read()
new, n = re.subn(r'__version__ = "[^"]*"',
                 f'__version__ = "{version}"', src)
if n != 1:
    print(f"FAIL: expected exactly one __version__ line, found {n}")
    sys.exit(1)
open(path, "w").write(new)
print(f"version bumped to {version}")
subprocess.run([sys.executable, "-m", "py_compile", path], check=True)

# FIRMWARE GATE: if firmware_esp32/ changed since the last tag, the
# LAST_FIRMWARE_CHANGE tuple in gui.py must be bumped to this version.
# (Missed twice: boards silently skipped reflash and kept old behavior.)
try:
    last_tag = subprocess.run(
        ["git", "describe", "--tags", "--abbrev=0"],
        capture_output=True, text=True, check=True).stdout.strip()
    fw_changed = subprocess.run(
        ["git", "diff", "--stat", last_tag, "--", "firmware_esp32/"],
        capture_output=True, text=True).stdout.strip()
    if fw_changed:
        gui = open("gui.py").read()
        m = re.search(r"LAST_FIRMWARE_CHANGE = \((\d+), (\d+), (\d+)\)", gui)
        cur = tuple(int(x) for x in m.groups()) if m else (0, 0, 0)
        want = tuple(int(x) for x in version.split("."))
        if cur < want:
            print("FAIL: firmware_esp32/ changed but LAST_FIRMWARE_CHANGE "
                  f"in gui.py is {cur} — bump it to {want} before "
                  "releasing (boards otherwise skip the reflash).")
            sys.exit(1)
except FileNotFoundError:
    pass  # not in a git checkout (standalone run)
