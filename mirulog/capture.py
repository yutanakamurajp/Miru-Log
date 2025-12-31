from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

import pyautogui

from .models import CaptureRecord
from .utils import ensure_directory, get_active_window, hash_file, is_session_locked, timestamp_slug

pyautogui.FAILSAFE = False


class CaptureSkipped(RuntimeError):
    pass


class CaptureManager:
    def __init__(self, capture_root: Path, archive_root: Path, timezone, log):
        self._capture_root = ensure_directory(capture_root)
        self._archive_root = ensure_directory(archive_root)
        self._timezone = timezone
        self._logger = log

    def capture(self) -> CaptureRecord:
        if is_session_locked():
            raise CaptureSkipped("Session is locked")

        timestamp = datetime.now(tz=self._timezone)
        folder = ensure_directory(self._capture_root / timestamp.strftime("%Y-%m-%d"))
        slug = timestamp_slug(timestamp)
        path = folder / f"capture-{slug}.png"

        screenshot = pyautogui.screenshot()
        screenshot.save(path)
        window_title, app = get_active_window()
        digest = hash_file(path)

        record = CaptureRecord(
            captured_at=timestamp,
            image_path=path,
            window_title=window_title,
            active_application=app,
            hash_digest=digest,
        )
        self._logger.info("Captured screenshot %s (%s)", path.name, window_title)
        return record

    def archive(self, record: CaptureRecord, delete_original: bool = False) -> Path | None:
        source = record.image_path
        if delete_original:
            try:
                source.unlink(missing_ok=True)
                self._logger.debug("Deleted capture %s", source)
                return None
            except Exception as exc:
                self._logger.warning("Failed to delete %s: %s", source, exc)
                return None

        if not source.exists():
            return None

        target_folder = ensure_directory(self._archive_root / record.captured_at.strftime("%Y-%m-%d"))
        target = target_folder / source.name
        shutil.move(str(source), target)
        self._logger.debug("Archived capture to %s", target)
        return target