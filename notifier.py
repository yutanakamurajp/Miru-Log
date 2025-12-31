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

    try:
        summary = load_summary(settings.output.summary_dir, target_date)
    except FileNotFoundError as exc:
        logger.warning("%s (exporting empty report)", exc)
        summary = DailySummary(
            date=target_date,
            segments=[],
            blocking_issues=[],
            follow_ups=[],
            total_active_minutes=0.0,
            markdown_path=None,
            dev_context=None,
        )

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

    if summary.dev_context:
        repos = summary.dev_context.get("observed_repositories") or []
        files = summary.dev_context.get("observed_files") or []
        urls = summary.dev_context.get("observed_urls") or []

        lines.append("## 開発コンテキスト")
        if repos:
            lines.append("- Repo/Workspace (画面から推定):")
            for name in repos:
                lines.append(f"  - {name}")
        if files:
            lines.append("- ファイル (画面から推定):")
            for p in files:
                lines.append(f"  - {p}")
        if urls:
            lines.append("- URL (画面から推定):")
            for u in urls:
                lines.append(f"  - {u}")
        lines.append("")

    lines.append("## タスク別累計（上位＋その他）")
    lines.append("| タスク | 合計時間 (分) | 割合 |")
    lines.append("| --- | ---: | ---: |")
    totals = _aggregate_task_totals(summary, top_n=8)
    total_minutes = summary.total_active_minutes or 1.0
    for task, minutes in totals:
        ratio = minutes / total_minutes * 100.0
        lines.append(f"| {task} | {minutes:.1f} | {ratio:.1f}% |")
    if not totals:
        lines.append("| (データ無し) | 0 | 0% |")
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
    return "\n".join(lines)

def _aggregate_task_totals(summary: DailySummary, *, top_n: int = 8) -> list[tuple[str, float]]:
    totals: defaultdict[str, float] = defaultdict(float)
    for segment in summary.segments:
        totals[segment.dominant_task] += segment.duration_minutes
    sorted_totals = sorted(totals.items(), key=lambda item: item[1], reverse=True)
    if top_n <= 0 or len(sorted_totals) <= top_n:
        return sorted_totals
    head = sorted_totals[:top_n]
    other_minutes = sum(m for _, m in sorted_totals[top_n:])
    if other_minutes > 0:
        head.append(("その他", other_minutes))
    return head


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
        dev_context=data.get("dev_context"),
    )
    return summary


if __name__ == "__main__":
    main()
