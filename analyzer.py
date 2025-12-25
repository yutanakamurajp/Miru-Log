from __future__ import annotations

import argparse

from mirulog.capture import CaptureManager
from mirulog.config import get_settings
from mirulog.gemini_client import GeminiAnalyzer
from mirulog.logging_utils import init_logger
from mirulog.storage import ObservationRepository


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze pending Miru-Log captures via Gemini")
    parser.add_argument("--limit", type=int, default=20, help="Maximum pending captures to process")
    args = parser.parse_args()

    settings = get_settings()
    logger = init_logger("analyzer", settings.logging.directory, settings.logging.level)
    repo = ObservationRepository(settings.capture.archive_root / "mirulog.db")
    capture_manager = CaptureManager(settings.capture.capture_root, settings.capture.archive_root, settings.timezone, logger)
    analyzer = GeminiAnalyzer(settings.gemini, logger)

    pending = repo.pending_captures(limit=args.limit)
    if not pending:
        logger.info("No pending captures to analyze")
        return

    logger.info("Analyzing %s pending captures", len(pending))
    for index, record in enumerate(pending, start=1):
        try:
            result = analyzer.analyze(record)
            repo.save_analysis(result)
            capture_manager.archive(record, delete_original=settings.capture.delete_after_analysis)
            logger.info("Capture %s/%s analyzed (id=%s) -> %s", index, len(pending), record.id, result.primary_task)
        except Exception as exc:
            message = str(exc)
            is_rate_limited = "429" in message or "Quota exceeded" in message or "rate limit" in message.lower()
            if is_rate_limited:
                logger.warning(
                    "Rate limited while analyzing capture %s/%s (id=%s). Stopping this run.",
                    index,
                    len(pending),
                    record.id,
                )
                logger.exception("Last error: %s", exc)
                break
            logger.exception("Failed to analyze capture %s/%s (id=%s): %s", index, len(pending), record.id, exc)


if __name__ == "__main__":
    main()