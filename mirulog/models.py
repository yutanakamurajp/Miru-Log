from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional


@dataclass
class CaptureRecord:
    captured_at: datetime
    image_path: Path
    window_title: str
    active_application: str
    session_state: str = "active"
    hash_digest: Optional[str] = None
    id: Optional[int] = None


@dataclass
class AnalysisResult:
    capture_id: int
    description: str
    primary_task: str
    confidence: float
    tags: List[str] = field(default_factory=list)
    raw_response: str | None = None


@dataclass
class SummarySegment:
    period_label: str
    highlights: List[str]
    dominant_task: str
    duration_minutes: float


@dataclass
class DailySummary:
    date: str
    segments: List[SummarySegment]
    blocking_issues: List[str]
    follow_ups: List[str]
    total_active_minutes: float
    markdown_path: Path | None = None
    dev_context: dict[str, Any] | None = None