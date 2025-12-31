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

    # Emergency escape hatch: allow disabling lock detection when it misbehaves
    # on certain environments (e.g., false positives from WTS APIs).
    if os.getenv("MIRULOG_DISABLE_LOCK_CHECK", "").strip().lower() in {"1", "true", "yes", "on"}:
        return False

    locked = _wts_is_session_locked()
    if locked is not None:
        # Some environments report false positives via WTS. If WTS says locked
        # but the input desktop is available, treat as unlocked.
        if locked:
            user32 = ctypes.windll.user32
            DESKTOP_SWITCHDESKTOP = 0x0100
            hdesktop = user32.OpenInputDesktop(0, False, DESKTOP_SWITCHDESKTOP)
            if hdesktop != 0:
                user32.CloseDesktop(hdesktop)
                return False
        return locked

    # Fallback: heuristic based on input desktop availability.
    user32 = ctypes.windll.user32
    DESKTOP_SWITCHDESKTOP = 0x0100
    hdesktop = user32.OpenInputDesktop(0, False, DESKTOP_SWITCHDESKTOP)
    if hdesktop == 0:
        return True
    user32.CloseDesktop(hdesktop)
    return False


def _wts_is_session_locked() -> bool | None:
    """Return True/False if we can determine lock state via WTS, else None."""
    try:
        wtsapi32 = ctypes.WinDLL("Wtsapi32")
        kernel32 = ctypes.WinDLL("Kernel32")

        WTS_CURRENT_SERVER_HANDLE = wintypes.HANDLE(0)
        WTS_INFO_CLASS_WTSInfoEx = 25

        class WTSINFOEX_LEVEL1_W(ctypes.Structure):
            _fields_ = [
                ("SessionId", wintypes.DWORD),
                ("SessionState", wintypes.DWORD),
                ("SessionFlags", wintypes.DWORD),
            ]

        class WTSINFOEX_LEVEL_W(ctypes.Union):
            _fields_ = [("WTSInfoExLevel1", WTSINFOEX_LEVEL1_W)]

        class WTSINFOEX_W(ctypes.Structure):
            _fields_ = [
                ("Level", wintypes.DWORD),
                ("Data", WTSINFOEX_LEVEL_W),
            ]

        wtsapi32.WTSQuerySessionInformationW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.DWORD),
        ]
        wtsapi32.WTSQuerySessionInformationW.restype = wintypes.BOOL
        wtsapi32.WTSFreeMemory.argtypes = [ctypes.c_void_p]
        wtsapi32.WTSFreeMemory.restype = None

        # Prefer the active console session (interactive user) when available.
        # This avoids false positives when the process session differs.
        kernel32.WTSGetActiveConsoleSessionId.argtypes = []
        kernel32.WTSGetActiveConsoleSessionId.restype = wintypes.DWORD

        session_id = wintypes.DWORD(kernel32.WTSGetActiveConsoleSessionId())
        # 0xFFFFFFFF means no active console session.
        if int(session_id.value) == 0xFFFFFFFF:
            kernel32.GetCurrentProcessId.argtypes = []
            kernel32.GetCurrentProcessId.restype = wintypes.DWORD
            kernel32.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
            kernel32.ProcessIdToSessionId.restype = wintypes.BOOL

            session_id = wintypes.DWORD(0)
            pid = kernel32.GetCurrentProcessId()
            if not kernel32.ProcessIdToSessionId(pid, ctypes.byref(session_id)):
                return None

        buffer = ctypes.c_void_p()
        bytes_returned = wintypes.DWORD(0)
        ok = wtsapi32.WTSQuerySessionInformationW(
            WTS_CURRENT_SERVER_HANDLE,
            session_id,
            WTS_INFO_CLASS_WTSInfoEx,
            ctypes.byref(buffer),
            ctypes.byref(bytes_returned),
        )
        if not ok or not buffer:
            return None

        try:
            info = ctypes.cast(buffer, ctypes.POINTER(WTSINFOEX_W)).contents
            if info.Level != 1:
                return None

            flags = int(info.Data.WTSInfoExLevel1.SessionFlags)
            # Documented behavior: 0 = locked, 1 = unlocked.
            if flags == 0:
                return True
            if flags == 1:
                return False
            return None
        finally:
            wtsapi32.WTSFreeMemory(buffer)
    except Exception:
        return None


def timestamp_slug(ts: datetime) -> str:
    return ts.strftime("%Y%m%d-%H%M%S")