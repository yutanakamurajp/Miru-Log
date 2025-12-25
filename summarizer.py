from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from typing import List

from mirulog.config import get_settings
from mirulog.logging_utils import init_logger
from mirulog.models import DailySummary, SummarySegment
from mirulog.storage import ObservationRepository


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize Miru-Log analysis for a given day")
    parser.add_argument("--date", help="Target date YYYY-MM-DD (defaults to today)")
    args = parser.parse_args()

    settings = get_settings()
    logger = init_logger("summarizer", settings.logging.directory, settings.logging.level)
    target_date = args.date or datetime.now(tz=settings.timezone).strftime("%Y-%m-%d")

    repo = ObservationRepository(settings.capture.archive_root / "mirulog.db")
    rows = repo.daily_analysis(target_date)
    if not rows:
        logger.warning("No analyzed captures for %s", target_date)
        return

    summary = build_daily_summary(rows, target_date, settings.capture.interval_seconds)
    summary_dir = settings.output.summary_dir
    summary_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = summary_dir / f"daily-report-{target_date}.md"
    json_path = summary_dir / f"daily-report-{target_date}.json"
    summary.markdown_path = markdown_path

    markdown_path.write_text(render_markdown(summary), encoding="utf-8")
    json_path.write_text(json.dumps(to_dict(summary), ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Daily summary saved to %s", markdown_path)


def build_daily_summary(rows, date_str: str, interval_seconds: int) -> DailySummary:
    interval_minutes = interval_seconds / 60.0
    segments: List[SummarySegment] = []
    blocking: List[str] = []
    followups: List[str] = []
    current = None

    for capture_id, ts_str, window_title, app, description, task, confidence, tags_str in rows:
        ts = datetime.fromisoformat(ts_str)
        task = task or "Unclassified"
        tags = [t.strip() for t in (tags_str or "").split(",") if t.strip()]
        highlight = f"{ts.strftime('%H:%M')} {description}"

        if any(keyword in description.lower() for keyword in ("error", "fail", "exception")):
            blocking.append(description)
        if any(tag.lower() in {"todo", "follow-up"} for tag in tags):
            followups.append(description)

        if current and current["task"] == task:
            current["count"] += 1
            current["end"] = ts
            current["highlights"].append(highlight)
        else:
            if current:
                segments.append(_finalize_segment(current, interval_minutes))
            current = {
                "task": task,
                "start": ts,
                "end": ts,
                "highlights": [highlight],
                "count": 1,
            }

    if current:
        segments.append(_finalize_segment(current, interval_minutes))

    total_active_minutes = len(rows) * interval_minutes
    return DailySummary(
        date=date_str,
        segments=segments,
        blocking_issues=blocking[:5],
        follow_ups=followups[:5],
        total_active_minutes=total_active_minutes,
        markdown_path=None,
    )

def _finalize_segment(segment_state, interval_minutes: float) -> SummarySegment:
    start = segment_state["start"]
    end = segment_state["end"] + timedelta(minutes=interval_minutes)
    period = f"{start.strftime('%H:%M')} - {end.strftime('%H:%M')}"
    highlights = segment_state["highlights"][:3]
    duration = segment_state["count"] * interval_minutes
    return SummarySegment(period_label=period, highlights=highlights, dominant_task=segment_state["task"], duration_minutes=duration)

def render_markdown(summary: DailySummary) -> str:
    header_date = summary.date.replace("-", "/")
    lines = [f"# {header_date} の Miru-Log 日報", ""]
    lines.append(f"- アクティブ時間: **{summary.total_active_minutes:.1f} 分**")
    lines.append(f"- セグメント数: {len(summary.segments)}")

    lines.append("\n## タスク別累計時間")
    lines.append("| タスク | 合計時間 (分) | 割合 |")
    lines.append("| --- | ---: | ---: |")
    totals = _aggregate_task_totals(summary)
    total_minutes = summary.total_active_minutes or 1.0
    for task, minutes in totals:
        ratio = minutes / total_minutes * 100.0
        lines.append(f"| {task} | {minutes:.1f} | {ratio:.1f}% |")
    if not totals:
        lines.append("| (データ無し) | 0 | 0% |")

    lines.append("\n## タイムライン\n")
    lines.append("| 時間帯 | タスク | 所要時間 | ハイライト |")
    lines.append("| --- | --- | ---: | --- |")
    for segment in summary.segments:
        highlights = "<br>".join(segment.highlights) if segment.highlights else "-"
        lines.append(
            f"| {segment.period_label} | {segment.dominant_task} | {segment.duration_minutes:.0f}m | {highlights} |"
        )
    if not summary.segments:
        lines.append("| (データ無し) | - | 0m | - |")

    lines.append("\n## 詳細ログ\n")
    if summary.segments:
        for segment in summary.segments:
            for highlight in segment.highlights:
                lines.append(f"- {segment.dominant_task}: {highlight}")
            if not segment.highlights:
                lines.append(f"- {segment.dominant_task}: 活動記録なし")
    else:
        lines.append("- 活動記録がありません")

    if summary.blocking_issues:
        lines.append("\n## ブロッカー\n")
        for issue in summary.blocking_issues:
            lines.append(f"- {issue}")

    if summary.follow_ups:
        lines.append("\n## フォローアップ\n")
        for item in summary.follow_ups:
            lines.append(f"- {item}")

    return "\n".join(lines)

def _aggregate_task_totals(summary: DailySummary) -> list[tuple[str, float]]:
    totals: dict[str, float] = {}
    for segment in summary.segments:
        totals[segment.dominant_task] = totals.get(segment.dominant_task, 0.0) + segment.duration_minutes
    return sorted(totals.items(), key=lambda item: item[1], reverse=True)

def to_dict(summary: DailySummary) -> dict:
    return {
        "date": summary.date,
        "total_active_minutes": summary.total_active_minutes,
        "segments": [
            {
                "period": segment.period_label,
                "task": segment.dominant_task,
                "duration_minutes": segment.duration_minutes,
                "highlights": segment.highlights,
            }
            for segment in summary.segments
        ],
        "blocking_issues": summary.blocking_issues,
        "follow_ups": summary.follow_ups,
    }


if __name__ == "__main__":
    main()
