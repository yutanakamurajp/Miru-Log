from __future__ import annotations

import argparse
import os
import signal
import time
from datetime import timedelta

from mirulog.activity import InputActivityMonitor
from mirulog.capture import CaptureManager, CaptureSkipped
from mirulog.config import get_settings
from mirulog.logging_utils import init_logger
from mirulog.storage import ObservationRepository
from mirulog.utils import is_session_locked


def main() -> None:
    parser = argparse.ArgumentParser(description="Miru-Log observer (screen capture)")
    parser.add_argument(
        "--capture-root",
        default=None,
        help="Override CAPTURE_ROOT (e.g. D:/MiruLog/captures)",
    )
    parser.add_argument(
        "--archive-root",
        default=None,
        help="Override ARCHIVE_ROOT (e.g. D:/MiruLog/archive)",
    )
    args = parser.parse_args()

    # Observer does not need analyzer settings, but AppSettings validation
    # may require GEMINI_API_KEY when ANALYZER_BACKEND defaults to "gemini".
    # Default to local for observer unless explicitly overridden.
    os.environ.setdefault("ANALYZER_BACKEND", "local")

    if args.capture_root:
        os.environ["CAPTURE_ROOT"] = args.capture_root
    if args.archive_root:
        os.environ["ARCHIVE_ROOT"] = args.archive_root

    settings = get_settings()
    logger = init_logger("observer", settings.logging.directory, settings.logging.level)
    repository = ObservationRepository(settings.capture.archive_root / "mirulog.db")
    capture_manager = CaptureManager(settings.capture.capture_root, settings.capture.archive_root, settings.timezone, logger)
    monitor = InputActivityMonitor(timedelta(minutes=settings.capture.idle_threshold_minutes), logger)
    monitor.start()

    running = True

    def _graceful_stop(signum, frame):
        nonlocal running
        running = False
        logger.info("Received signal %s - shutting down observer", signum)

    signal.signal(signal.SIGINT, _graceful_stop)
    signal.signal(signal.SIGTERM, _graceful_stop)

    interval = settings.capture.interval_seconds
    logger.info("Observer started: interval=%ss idle_threshold=%sm", interval, settings.capture.idle_threshold_minutes)

    last_skip_reason: str | None = None
    last_skip_log_at = 0.0
    skip_log_interval_seconds = 60.0

    try:
        while running:
            now = time.time()

            if is_session_locked():
                if last_skip_reason != "locked" or (now - last_skip_log_at) >= skip_log_interval_seconds:
                    logger.info("Skipping capture: session is locked")
                    last_skip_reason = "locked"
                    last_skip_log_at = now
                time.sleep(interval)
                continue

            if monitor.is_idle():
                if last_skip_reason != "idle" or (now - last_skip_log_at) >= skip_log_interval_seconds:
                    logger.info("Skipping capture: idle (last activity at %s UTC)", monitor.last_activity().isoformat())
                    last_skip_reason = "idle"
                    last_skip_log_at = now
                time.sleep(interval)
                continue

            try:
                record = capture_manager.capture()
                record.id = repository.add_capture(record)
                logger.debug("Capture persisted with id=%s", record.id)
            except CaptureSkipped as exc:
                # Surface at INFO so users can diagnose "no captures" quickly.
                if last_skip_reason != "capture_skipped" or (now - last_skip_log_at) >= skip_log_interval_seconds:
                    logger.info("Skipping capture: %s", exc)
                    last_skip_reason = "capture_skipped"
                    last_skip_log_at = now
            except Exception as exc:
                logger.exception("Failed to capture screenshot: %s", exc)

            time.sleep(interval)
    finally:
        monitor.stop()
        logger.info("Observer stopped")


if __name__ == "__main__":
    main()