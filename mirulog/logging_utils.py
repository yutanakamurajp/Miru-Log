from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def init_logger(name: str, log_dir: Path, level: str = "INFO") -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    if not logger.handlers:
        log_path = log_dir / f"{name}.log"
        file_handler = RotatingFileHandler(log_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        file_handler.setFormatter(formatter)

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)

        logger.addHandler(file_handler)
        logger.addHandler(console_handler)

    return logger