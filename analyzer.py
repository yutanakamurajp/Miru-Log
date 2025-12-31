from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from mirulog.capture import CaptureManager
from mirulog.config import get_settings
from mirulog.gemini_client import GeminiAnalyzer
from mirulog.local_llm_client import LocalLLMAnalyzer
from mirulog.logging_utils import init_logger
from mirulog.storage import ObservationRepository


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze pending Miru-Log captures via Gemini")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum pending captures to process. When --until-empty, this is the batch size. If omitted: gemini=20, local=unlimited.",
    )
    parser.add_argument(
        "--until-empty",
        action="store_true",
        help="Keep analyzing in batches until no pending captures remain (stops on rate limit/errors)",
    )
    args = parser.parse_args()

    settings = get_settings()
    logger = init_logger("analyzer", settings.logging.directory, settings.logging.level)
    repo = ObservationRepository(settings.capture.archive_root / "mirulog.db")
    capture_manager = CaptureManager(settings.capture.capture_root, settings.capture.archive_root, settings.timezone, logger)

    tray_state_path = os.getenv("MIRULOG_TRAY_STATE_PATH")
    tray_state_file = Path(tray_state_path).resolve() if tray_state_path else None
    if settings.analyzer.backend == "local":
        analyzer = LocalLLMAnalyzer(settings.local_llm, logger)
        logger.info("Analyzer backend: local (%s)", settings.local_llm.base_url)
    else:
        analyzer = GeminiAnalyzer(settings.gemini, logger)
        logger.info("Analyzer backend: gemini (%s)", settings.gemini.model)

    if args.limit is None:
        batch_size = 1_000_000 if settings.analyzer.backend == "local" else 20
    else:
        batch_size = max(1, int(args.limit))
    total_processed = 0

    def write_progress(*, last_task: str | None = None, last_capture_id: int | None = None) -> None:
        if not tray_state_file:
            return
        try:
            pending = repo.pending_count()
        except Exception:
            pending = None
        progress = {
            "processed": total_processed,
            "pending": pending,
            "last_task": last_task,
            "last_capture_id": last_capture_id,
            "updated_at": datetime.now().isoformat(),
        }
        _update_tray_state(tray_state_file, "analyzer.py", {"progress": progress})

    while True:
        pending = repo.pending_captures(limit=batch_size)
        if not pending:
            if total_processed == 0:
                logger.info("No pending captures to analyze")
            else:
                logger.info("No pending captures remain (processed=%s)", total_processed)
            return

        logger.info("Analyzing %s pending captures", len(pending))
        write_progress()
        for index, record in enumerate(pending, start=1):
            try:
                result = analyzer.analyze(record)
                repo.save_analysis(result)
                capture_manager.archive(record, delete_original=settings.capture.delete_after_analysis)
                total_processed += 1
                write_progress(last_task=result.primary_task, last_capture_id=record.id)
                logger.info(
                    "Capture %s/%s analyzed (id=%s) -> %s",
                    index,
                    len(pending),
                    record.id,
                    result.primary_task,
                )
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
                    _update_tray_state(tray_state_file, "analyzer.py", {"last_error": str(exc)})
                    return
                logger.exception("Failed to analyze capture %s/%s (id=%s): %s", index, len(pending), record.id, exc)
                _update_tray_state(tray_state_file, "analyzer.py", {"last_error": str(exc)})

        if not args.until_empty:
            return


def _update_tray_state(path: Path | None, script_key: str, updates: dict) -> None:
    if not path:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        state: dict = {}
        if path.exists():
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                state = {}

        entry = state.get(script_key)
        if not isinstance(entry, dict):
            entry = {}
        entry.update(updates)
        state[script_key] = entry

        # Atomic-ish write on Windows: write temp then replace.
        with NamedTemporaryFile("w", delete=False, encoding="utf-8", dir=str(path.parent)) as tmp:
            tmp.write(json.dumps(state, ensure_ascii=False, indent=2))
            tmp_path = Path(tmp.name)
        tmp_path.replace(path)
    except Exception:
        # Never fail analyzer due to tray state issues.
        return


if __name__ == "__main__":
    main()