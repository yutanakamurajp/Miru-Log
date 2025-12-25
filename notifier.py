from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from mirulog.config import get_settings
from mirulog.logging_utils import init_logger
from mirulog.models import DailySummary, SummarySegment
from mirulog.visualizer import NanobananaClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Miru-Log daily summary")
    parser.add_argument("--date", help="Target date YYYY-MM-DD (defaults to today)")
    args = parser.parse_args()

    settings = get_settings()
    logger = init_logger("notifier", settings.logging.directory, settings.logging.level)
    target_date = args.date or datetime.now(tz=settings.timezone).strftime("%Y-%m-%d")

    summary = load_summary(settings.output.summary_dir, target_date)

    export_dir = settings.output.export_dir
    export_dir.mkdir(parents=True, exist_ok=True)
    export_name = f"{target_date.replace('-', '')}_log.md"
    export_path = export_dir / export_name
    export_path.write_text(render_japanese_report(summary), encoding="utf-8")
    logger.info("Daily log exported to %s", export_path)

    if settings.visualization.enabled:
        _maybe_generate_infographic(summary, export_path.with_suffix(".png"), settings, logger)


def render_japanese_report(summary: DailySummary) -> str:
    header_date = summary.date.replace("-", "/")
    lines: list[str] = []
    lines.append(f"# {header_date} の Miru-Log 日報")
    lines.append("")
    lines.append(f"- アクティブ時間: **{summary.total_active_minutes:.1f} 分**")
    lines.append(f"- セグメント数: {len(summary.segments)}")
    lines.append("")

    lines.append("## タスク別累計時間")
    lines.append("| タスク | 合計時間 (分) | 割合 |")
    lines.append("| --- | ---: | ---: |")
    totals = _aggregate_task_totals(summary)
    total_minutes = summary.total_active_minutes or 1.0
    for task, minutes in totals:
        ratio = minutes / total_minutes * 100.0
        lines.append(f"| {task} | {minutes:.1f} | {ratio:.1f}% |")
    if not totals:
        lines.append("| (データ無し) | 0 | 0% |")
    lines.append("")

    lines.append("## タイムテーブル")
    lines.append("| 時間帯 | タスク | 主要アクション | 所要時間 |")
    lines.append("| --- | --- | --- | ---: |")
    for segment in summary.segments:
        highlights = "<br>".join(segment.highlights) if segment.highlights else "-"
        lines.append(
            f"| {segment.period_label} | {segment.dominant_task} | {highlights} | {segment.duration_minutes:.0f}m |")
    if not summary.segments:
        lines.append("| (データ無し) | - | - | 0m |")
    lines.append("")

    lines.append("## 詳細メモ")
    if summary.segments:
        for segment in summary.segments:
            lines.append(f"### {segment.period_label} — {segment.dominant_task}")
            for highlight in segment.highlights:
                lines.append(f"- {highlight}")
            if not segment.highlights:
                lines.append("- 活動記録なし")
            lines.append("")
    else:
        lines.append("- 活動記録がありません")
        lines.append("")

    if summary.blocking_issues:
        lines.append("## ブロッカー")
        for issue in summary.blocking_issues:
            lines.append(f"- {issue}")
        lines.append("")

    if summary.follow_ups:
        lines.append("## フォローアップ")
        for item in summary.follow_ups:
            lines.append(f"- {item}")
        lines.append("")

    lines.append("---")
    lines.append("Generated automatically by Miru-Log")
    return "\n".join(lines)


def _aggregate_task_totals(summary: DailySummary) -> list[tuple[str, float]]:
    totals: defaultdict[str, float] = defaultdict(float)
    for segment in summary.segments:
        totals[segment.dominant_task] += segment.duration_minutes
    return sorted(totals.items(), key=lambda item: item[1], reverse=True)


def _maybe_generate_infographic(summary: DailySummary, image_path: Path, settings, logger) -> None:
    if not settings.visualization.api_key:
        logger.warning("Visualization enabled but NANOBANANA_API_KEY is missing. Skipping image generation.")
        return

    viz_client = NanobananaClient(settings.visualization, logger)
    try:
        viz_client.render_summary(summary, image_path)
        logger.info("Infographic saved to %s", image_path)
    except Exception as exc:
        logger.warning("Failed to create infographic: %s", exc)


def load_summary(summary_dir: Path, date_str: str) -> DailySummary:
    json_path = summary_dir / f"daily-report-{date_str}.json"
    if not json_path.exists():
        raise FileNotFoundError(f"Summary JSON not found: {json_path}")

    data = json.loads(json_path.read_text(encoding="utf-8"))
    segments = [
        SummarySegment(
            period_label=item["period"],
            highlights=item.get("highlights", []),
            dominant_task=item["task"],
            duration_minutes=item.get("duration_minutes", 0.0),
        )
        for item in data.get("segments", [])
    ]

    summary = DailySummary(
        date=data.get("date", date_str),
        segments=segments,
        blocking_issues=data.get("blocking_issues", []),
        follow_ups=data.get("follow_ups", []),
        total_active_minutes=float(data.get("total_active_minutes", 0.0)),
        markdown_path=None,
    )
    return summary


if __name__ == "__main__":
    main()
