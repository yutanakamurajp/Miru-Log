from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


@dataclass(frozen=True)
class CaptureSettings:
    interval_seconds: int
    idle_threshold_minutes: int
    capture_root: Path
    archive_root: Path
    delete_after_analysis: bool


@dataclass(frozen=True)
class GeminiSettings:
    api_key: str
    model: str
    max_tokens: int
    temperature: float
    max_retries: int = 5
    retry_buffer_seconds: float = 0.5
    request_spacing_seconds: float = 0.0


@dataclass(frozen=True)
class VisualizationSettings:
    endpoint: str
    api_key: str | None
    model: str
    enabled: bool


@dataclass(frozen=True)
class LoggingSettings:
    directory: Path
    level: str = "INFO"


@dataclass(frozen=True)
class OutputSettings:
    summary_dir: Path
    export_dir: Path


@dataclass(frozen=True)
class AppSettings:
    timezone: ZoneInfo
    capture: CaptureSettings
    gemini: GeminiSettings
    visualization: VisualizationSettings
    logging: LoggingSettings
    output: OutputSettings


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    dotenv_path = Path(__file__).resolve().parents[1] / ".env"
    load_dotenv(dotenv_path=dotenv_path, override=False, encoding="utf-8-sig")

    tz_name = os.getenv("TIMEZONE", "Asia/Tokyo")
    timezone = ZoneInfo(tz_name)

    capture = CaptureSettings(
        interval_seconds=int(os.getenv("CAPTURE_INTERVAL_SECONDS", "60")),
        idle_threshold_minutes=int(os.getenv("IDLE_THRESHOLD_MINUTES", "5")),
        capture_root=Path(os.getenv("CAPTURE_ROOT", "data/captures")).resolve(),
        archive_root=Path(os.getenv("ARCHIVE_ROOT", "data/archive")).resolve(),
        delete_after_analysis=_as_bool(os.getenv("DELETE_CAPTURE_AFTER_ANALYSIS", "true")),
    )

    gemini = GeminiSettings(
        api_key=_require("GEMINI_API_KEY"),
        model=os.getenv("GEMINI_MODEL", "gemini-pro-vision"),
        max_tokens=int(os.getenv("GEMINI_MAX_TOKENS", "1024")),
        temperature=float(os.getenv("GEMINI_TEMPERATURE", "0.4")),
        max_retries=int(os.getenv("GEMINI_MAX_RETRIES", "5")),
        retry_buffer_seconds=float(os.getenv("GEMINI_RETRY_BUFFER_SECONDS", "0.5")),
        request_spacing_seconds=float(os.getenv("GEMINI_REQUEST_SPACING_SECONDS", "0")),
    )

    visualization = VisualizationSettings(
        endpoint=os.getenv("NANOBANANA_ENDPOINT", "https://api.nanobanana.pro/v1/images"),
        api_key=os.getenv("NANOBANANA_API_KEY"),
        model=os.getenv("NANOBANANA_MODEL", "nano-pro-vision"),
        enabled=_as_bool(os.getenv("ENABLE_VISUALIZATION", "false")),
    )

    logging_settings = LoggingSettings(
        directory=Path(os.getenv("LOG_DIR", "logs")).resolve(),
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )

    output_settings = OutputSettings(
        summary_dir=Path(os.getenv("SUMMARY_OUTPUT_DIR", "output")).resolve(),
        export_dir=Path(os.getenv("REPORT_EXPORT_DIR", "reports")).resolve(),
    )

    return AppSettings(
        timezone=timezone,
        capture=capture,
        gemini=gemini,
        visualization=visualization,
        logging=logging_settings,
        output=output_settings,
    )


def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise RuntimeError(f"Environment variable '{key}' is required but missing")
    return value


def _as_bool(raw: str | None, default: bool | None = None) -> bool:
    if raw is None:
        if default is None:
            return False
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}