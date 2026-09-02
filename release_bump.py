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
