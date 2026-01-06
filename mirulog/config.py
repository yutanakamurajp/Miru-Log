from __future__ import annotations

import os
import sys
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
    retention_days: int


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
class LocalLLMSettings:
    base_url: str
    api_key: str | None
    model: str
    max_tokens: int
    temperature: float
    timeout_seconds: float = 60.0


@dataclass(frozen=True)
class AnalyzerSettings:
    backend: str  # "gemini" or "local"


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
    analyzer: AnalyzerSettings
    gemini: GeminiSettings
    local_llm: LocalLLMSettings
    visualization: VisualizationSettings
    logging: LoggingSettings
    output: OutputSettings


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    dotenv_path = _find_dotenv_path()
    if dotenv_path is not None:
        load_dotenv(dotenv_path=dotenv_path, override=False, encoding="utf-8-sig")

    tz_name = os.getenv("TIMEZONE", "Asia/Tokyo")
    timezone = ZoneInfo(tz_name)

    capture = CaptureSettings(
        interval_seconds=int(os.getenv("CAPTURE_INTERVAL_SECONDS", "60")),
        idle_threshold_minutes=int(os.getenv("IDLE_THRESHOLD_MINUTES", "5")),
        capture_root=Path(_expand_env_vars(os.getenv("CAPTURE_ROOT", "data/captures"))).resolve(),
        archive_root=Path(_expand_env_vars(os.getenv("ARCHIVE_ROOT", "data/archive"))).resolve(),
        delete_after_analysis=_as_bool(os.getenv("DELETE_CAPTURE_AFTER_ANALYSIS", "true")),
        retention_days=int(os.getenv("DATA_RETENTION_DAYS", "7")),
    )

    analyzer_settings = AnalyzerSettings(
        backend=os.getenv("ANALYZER_BACKEND", "gemini").strip().lower(),
    )

    gemini_api_key = os.getenv("GEMINI_API_KEY")
    if analyzer_settings.backend != "local":
        gemini_api_key = _require("GEMINI_API_KEY")

    gemini = GeminiSettings(
        api_key=gemini_api_key or "",
        model=os.getenv("GEMINI_MODEL", "gemini-pro-vision"),
        max_tokens=int(os.getenv("GEMINI_MAX_TOKENS", "1024")),
        temperature=float(os.getenv("GEMINI_TEMPERATURE", "0.4")),
        max_retries=int(os.getenv("GEMINI_MAX_RETRIES", "5")),
        retry_buffer_seconds=float(os.getenv("GEMINI_RETRY_BUFFER_SECONDS", "0.5")),
        request_spacing_seconds=float(os.getenv("GEMINI_REQUEST_SPACING_SECONDS", "0")),
    )

    local_llm = LocalLLMSettings(
        base_url=os.getenv("LOCAL_LLM_BASE_URL", "http://localhost:1234/v1").rstrip("/"),
        api_key=os.getenv("LOCAL_LLM_API_KEY") or None,
        model=os.getenv("LOCAL_LLM_MODEL", "local-model"),
        max_tokens=int(os.getenv("LOCAL_LLM_MAX_TOKENS", "1024")),
        temperature=float(os.getenv("LOCAL_LLM_TEMPERATURE", "0.4")),
        timeout_seconds=float(os.getenv("LOCAL_LLM_TIMEOUT_SECONDS", "60")),
    )

    visualization = VisualizationSettings(
        endpoint=os.getenv("NANOBANANA_ENDPOINT", "https://api.nanobanana.pro/v1/images"),
        api_key=os.getenv("NANOBANANA_API_KEY"),
        model=os.getenv("NANOBANANA_MODEL", "nano-pro-vision"),
        enabled=_as_bool(os.getenv("ENABLE_VISUALIZATION", "false")),
    )

    logging_settings = LoggingSettings(
        directory=Path(_expand_env_vars(os.getenv("LOG_DIR", "logs"))).resolve(),
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )

    output_settings = OutputSettings(
        summary_dir=Path(_expand_env_vars(os.getenv("SUMMARY_OUTPUT_DIR", "output"))).resolve(),
        export_dir=Path(_expand_env_vars(os.getenv("REPORT_EXPORT_DIR", "reports"))).resolve(),
    )

    return AppSettings(
        timezone=timezone,
        capture=capture,
        analyzer=analyzer_settings,
        gemini=gemini,
        local_llm=local_llm,
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


def _expand_env_vars(value: str) -> str:
    """Expand environment variables inside a string.

    Supports patterns like:
    - Windows: %COMPUTERNAME%
    - POSIX: $HOME, ${HOME}
    """

    # Python's os.path.expandvars supports %VAR% on Windows and $VAR/${VAR}.
    return os.path.expandvars(value)


def _find_dotenv_path() -> Path | None:
    """Find a suitable .env path.

    Priority:
    1) MIRULOG_DOTENV (explicit)
    2) Current working directory
    3) Executable directory (PyInstaller / frozen)
    4) Repo root (relative to this file)
    """

    explicit = (os.getenv("MIRULOG_DOTENV") or "").strip()
    if explicit:
        path = Path(explicit)
        return path if path.exists() else None

    candidates: list[Path] = [Path.cwd() / ".env"]

    if getattr(sys, "frozen", False):
        # When bundled, __file__ points inside the temp extraction dir.
        candidates.append(Path(sys.executable).resolve().parent / ".env")

    candidates.append(Path(__file__).resolve().parents[1] / ".env")

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None