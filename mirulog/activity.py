from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Optional

from pynput import keyboard, mouse

from .utils import is_session_locked


class InputActivityMonitor:
    def __init__(self, idle_threshold: timedelta, log):
        self._idle_threshold = idle_threshold
        self._logger = log
        self._last_activity = datetime.utcnow()
        self._lock = threading.Lock()
        self._mouse_listener: Optional[mouse.Listener] = None
        self._keyboard_listener: Optional[keyboard.Listener] = None

    def start(self) -> None:
        if self._mouse_listener or self._keyboard_listener:
            return

        self._mouse_listener = mouse.Listener(on_move=self._on_mouse, on_click=self._on_mouse, on_scroll=self._on_mouse)
        self._keyboard_listener = keyboard.Listener(on_press=self._on_keyboard)
        self._mouse_listener.start()
        self._keyboard_listener.start()
        self._logger.debug("Activity monitor listeners started")

    def stop(self) -> None:
        if self._mouse_listener:
            self._mouse_listener.stop()
            self._mouse_listener = None
        if self._keyboard_listener:
            self._keyboard_listener.stop()
            self._keyboard_listener = None

    def _on_mouse(self, *args, **kwargs):
        self._update_activity()

    def _on_keyboard(self, key):
        self._update_activity()

    def _update_activity(self):
        with self._lock:
            self._last_activity = datetime.utcnow()

    def is_idle(self) -> bool:
        with self._lock:
            last = self._last_activity
        idle = datetime.utcnow() - last > self._idle_threshold
        if idle:
            self._logger.debug("System idle for %s", datetime.utcnow() - last)
        return idle or is_session_locked()

    def last_activity(self) -> datetime:
        with self._lock:
            return self._last_activity