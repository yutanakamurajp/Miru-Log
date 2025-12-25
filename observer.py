from __future__ import annotations

import signal
import time
from datetime import timedelta

from mirulog.activity import InputActivityMonitor
from mirulog.capture import CaptureManager
from mirulog.config import get_settings
from mirulog.logging_utils import init_logger
from mirulog.storage import ObservationRepository


def main() -> None:
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

    try:
        while running:
            if monitor.is_idle():
                time.sleep(interval)
                continue

            try:
                record = capture_manager.capture()
                record.id = repository.add_capture(record)
                logger.debug("Capture persisted with id=%s", record.id)
            except Exception as exc:
                logger.exception("Failed to capture screenshot: %s", exc)

            time.sleep(interval)
    finally:
        monitor.stop()
        logger.info("Observer stopped")


if __name__ == "__main__":
    main()