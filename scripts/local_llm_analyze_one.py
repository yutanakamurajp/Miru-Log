from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from mirulog.config import LocalLLMSettings
from mirulog.local_llm_client import LocalLLMAnalyzer
from mirulog.logging_utils import init_logger
from mirulog.models import CaptureRecord


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:1234/v1")
    parser.add_argument("--model", default="gemma-3-4b-it-qat")
    parser.add_argument("--image", required=True)
    args = parser.parse_args()

    logger = init_logger("local_llm_test", Path("logs").resolve(), "INFO")

    settings = LocalLLMSettings(
        base_url=args.base_url.rstrip("/"),
        api_key=None,
        model=args.model,
        max_tokens=512,
        temperature=0.2,
        timeout_seconds=60.0,
    )

    analyzer = LocalLLMAnalyzer(settings, logger)

    record = CaptureRecord(
        id=1,
        captured_at=datetime.now(),
        image_path=Path(args.image),
        window_title="(test)",
        active_application="(test)",
    )

    result = analyzer.analyze(record)
    print("primary_task:", result.primary_task)
    print("description:", result.description)
    print("confidence:", result.confidence)
    print("tags:", result.tags)


if __name__ == "__main__":
    main()
