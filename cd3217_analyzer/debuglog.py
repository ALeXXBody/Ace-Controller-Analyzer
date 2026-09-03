"""Comprehensive debug trace for ACA (ACE Controller Analyzer).

Activated by a GUI switch (Log tab). When enabled, every bridge
transaction, I2C-level error, retry decision, watcher tick and app-level
event is recorded with millisecond timestamps and thread names — so a
misbehaving session can be exported and analysed offline instead of
guessed at.

Design notes:
  * zero-ish cost when disabled (single flag check)
  * thread-safe: any worker may log
  * keeps an in-memory ring (last RING_MAX lines) — always exportable
    even if the file sink failed
  * optional file sink (one line per event, flushed) — survives an app
    crash/force-kill, unlike the ring in a dead process
"""
from __future__ import annotations

import os
import platform
import threading
import time
from typing import List, Optional

_lock = threading.Lock()
_enabled = False
_ring: List[str] = []
_ring_max = 6000
_file = None
_file_path: Optional[str] = None
_t0 = time.time()


def _ts() -> str:
    # wall clock with milliseconds — matches the GUI log clock for
    # side-by-side comparison with user screenshots
    t = time.time()
    ms = int((t % 1) * 1000)
    return time.strftime("%H:%M:%S", time.localtime(t)) + f".{ms:03d}"


def enable(path: Optional[str] = None) -> Optional[str]:
    """Turn the trace on. Returns the file path used (None = ring only)."""
    global _enabled, _file, _file_path
    with _lock:
        if _enabled:
            return _file_path
        _enabled = True
        _ring.clear()
        _t0 = time.time()
        if path:
            try:
                os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
                _file = open(path, "a", encoding="utf-8")
                _file_path = path
            except Exception:
                _file = None
                _file_path = None
        _emit("debug trace ENABLED")
        _emit(f"app session start (uptime reset) — python {platform.python_version()}, "
              f"{platform.system()} {platform.release()}")
        if _file_path is None and path:
            _emit(f"WARNING: could not open file sink {path!r} — ring buffer only")
    return _file_path


def disable() -> None:
    global _enabled, _file
    with _lock:
        if not _enabled:
            return
        _emit("debug trace disabled")
        _enabled = False
        if _file:
            try:
                _file.close()
            except Exception:
                pass
        _file = None


def is_enabled() -> bool:
    return _enabled


def file_path() -> Optional[str]:
    return _file_path


def log(msg: str, *args) -> None:
    """Record one trace line. Cheap no-op when the trace is disabled."""
    if not _enabled:
        return
    try:
        text = msg % args if args else msg
    except Exception:
        text = msg
    with _lock:
        _emit(text)


def _emit(text: str) -> None:
    """Caller must hold _lock (or be enable/disable)."""
    line = f"{_ts()} [{threading.current_thread().name}] {text}"
    _ring.append(line)
    del _ring[:-_ring_max]
    if _file:
        try:
            _file.write(line + "\n")
            _file.flush()
        except Exception:
            pass


def entries() -> List[str]:
    with _lock:
        return list(_ring)


def clear() -> None:
    with _lock:
        _ring.clear()
