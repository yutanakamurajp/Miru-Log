from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from mirulog.config import get_settings
from mirulog.logging_utils import init_logger


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Miru-Log pipeline: analyze → summarize → notify"
    )
    parser.add_argument(
        "--date",
        help="Target date YYYY-MM-DD (defaults to today)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum pending captures to process in analyzer. When --until-empty, this is the batch size.",
    )
    parser.add_argument(
        "--until-empty",
        action="store_true",
        help="Keep analyzing in batches until no pending captures remain (analyzer only)",
    )
    parser.add_argument(
        "--skip-analyze",
        action="store_true",
        help="Skip analysis step (only summarize and notify)",
    )
    parser.add_argument(
        "--skip-notify",
        action="store_true",
        help="Skip notification step (only analyze and summarize)",
    )
    args = parser.parse_args()

    settings = get_settings()
    logger = init_logger("pipeline", settings.logging.directory, settings.logging.level)
    target_date = args.date or datetime.now(tz=settings.timezone).strftime("%Y-%m-%d")

    logger.info("=" * 60)
    logger.info("Miru-Log Pipeline Started")
    logger.info("Target date: %s", target_date)
    logger.info("=" * 60)

    # Step 0: Cleanup old data
    logger.info("Step 0: Running cleanup...")
    try:
        _run_cleanup(logger)
        logger.info("Cleanup completed successfully")
    except Exception as exc:
        logger.exception("Cleanup failed: %s", exc)
        logger.warning("Continuing pipeline despite cleanup failure")

    # Step 1: Analyze
    if not args.skip_analyze:
        logger.info("Step 1/3: Running analyzer...")
        try:
            _run_analyzer(args.limit, args.until_empty, logger)
            logger.info("Analyzer completed successfully")
        except Exception as exc:
            logger.exception("Analyzer failed: %s", exc)
            logger.error("Pipeline aborted due to analyzer failure")
            sys.exit(1)
    else:
        logger.info("Step 1/3: Skipped (--skip-analyze)")

    # Step 2: Summarize
    logger.info("Step 2/3: Running summarizer...")
    try:
        _run_summarizer(target_date, logger)
        logger.info("Summarizer completed successfully")
    except Exception as exc:
        logger.exception("Summarizer failed: %s", exc)
        logger.error("Pipeline aborted due to summarizer failure")
        sys.exit(1)

    # Step 3: Notify
    if not args.skip_notify:
        logger.info("Step 3/3: Running notifier...")
        try:
            _run_notifier(target_date, logger)
            logger.info("Notifier completed successfully")
        except Exception as exc:
            logger.exception("Notifier failed: %s", exc)
            logger.error("Pipeline aborted due to notifier failure")
            sys.exit(1)
    else:
        logger.info("Step 3/3: Skipped (--skip-notify)")

    logger.info("=" * 60)
    logger.info("Miru-Log Pipeline Completed Successfully")
    logger.info("=" * 60)


def _run_cleanup(logger) -> None:
    """Run database cleanup to remove old records."""
    settings = get_settings()
    from mirulog.storage import ObservationRepository

    retention_days = settings.capture.retention_days
    cleanup_targets = _resolve_cleanup_targets(settings.analyzer.data_root)
    if not cleanup_targets:
        logger.info("No database found for cleanup under %s", settings.analyzer.data_root)
        return

    total_deleted = 0
    for pc_name, db_path in cleanup_targets:
        scope = f"pc={pc_name}" if pc_name else "single"
        logger.info("Cleaning up records older than %s days (%s db=%s)", retention_days, scope, db_path)

        repo = ObservationRepository(db_path)
        deleted_count = repo.cleanup_old_records(retention_days)
        total_deleted += deleted_count
        logger.info("Deleted %s old capture records (%s)", deleted_count, scope)

        if deleted_count > 0:
            logger.info("Running VACUUM to reclaim disk space (%s)", scope)
            repo.vacuum()
            logger.info("VACUUM completed (%s)", scope)

    if total_deleted == 0:
        logger.info("Cleanup found no expired records")


def _resolve_cleanup_targets(data_root: Path) -> list[tuple[str | None, Path]]:
    pc_dbs: list[tuple[str, Path]] = []
    if data_root.exists():
        for child in data_root.iterdir():
            if not child.is_dir():
                continue
            db_path = child / "mirulog.db"
            if db_path.exists():
                pc_dbs.append((child.name, db_path))

    if pc_dbs:
        return sorted(pc_dbs, key=lambda item: item[0].lower())

    single_db = data_root / "mirulog.db"
    if single_db.exists():
        return [(None, single_db)]

    return []


def _run_analyzer(limit: int | None, until_empty: bool, logger) -> None:
    """Run analyzer.py with specified arguments."""
    # Temporarily modify sys.argv to pass arguments to analyzer
    original_argv = sys.argv.copy()
    try:
        sys.argv = ["analyzer.py"]
        if limit is not None:
            sys.argv.extend(["--limit", str(limit)])
        if until_empty:
            sys.argv.append("--until-empty")

        # Import and run analyzer
        from analyzer import main as analyzer_main
        analyzer_main()
    finally:
        sys.argv = original_argv


def _run_summarizer(target_date: str, logger) -> None:
    """Run summarizer.py with specified date."""
    original_argv = sys.argv.copy()
    try:
        sys.argv = ["summarizer.py", "--date", target_date]

        # Import and run summarizer
        from summarizer import main as summarizer_main
        summarizer_main()
    finally:
        sys.argv = original_argv


def _run_notifier(target_date: str, logger) -> None:
    """Run notifier.py with specified date."""
    original_argv = sys.argv.copy()
    try:
        sys.argv = ["notifier.py", "--date", target_date]

        # Import and run notifier
        from notifier import main as notifier_main
        notifier_main()
    finally:
        sys.argv = original_argv


if __name__ == "__main__":
    main()
