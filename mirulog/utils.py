from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import os
from datetime import datetime
from pathlib import Path
from typing import Tuple


def ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def hash_file(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            sha.update(chunk)
    return sha.hexdigest()


def get_active_window() -> Tuple[str, str]:
    if os.name != "nt":
        return "Unknown", "Unknown"

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return "Unknown", "Unknown"

    length = user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    title = buffer.value

    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe_name = "Unknown"

    if pid.value:
        try:
            import psutil

            exe_name = psutil.Process(pid.value).name()
        except Exception:
            exe_name = f"PID-{pid.value}"
    return title or "Unknown", exe_name


def is_session_locked() -> bool:
    if os.name != "nt":
        return False

    user32 = ctypes.windll.user32
    DESKTOP_SWITCHDESKTOP = 0x0100
    hdesktop = user32.OpenInputDesktop(0, False, DESKTOP_SWITCHDESKTOP)
    if hdesktop == 0:
        return True
    user32.CloseDesktop(hdesktop)
    return False


def timestamp_slug(ts: datetime) -> str:
    return ts.strftime("%Y%m%d-%H%M%S")