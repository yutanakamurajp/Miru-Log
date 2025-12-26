from __future__ import annotations

import argparse
import json
import re
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
    observed_files: set[str] = set()
    observed_repos: set[str] = set()
    observed_urls: set[str] = set()

    for capture_id, ts_str, window_title, app, description, task, confidence, tags_str, raw_response in rows:
        ts = datetime.fromisoformat(ts_str)
        task = task or "Unclassified"
        tags = [t.strip() for t in (tags_str or "").split(",") if t.strip()]
        highlight = f"{ts.strftime('%H:%M')} {description}"

        payload = _best_effort_parse_json(raw_response or "")
        observed_files.update(_coerce_str_list(payload.get("observed_files")))
        observed_repos.update(_coerce_str_list(payload.get("observed_repositories")))
        observed_urls.update(_coerce_str_list(payload.get("observed_urls")))

        # Backward-compatible heuristic extraction for older rows.
        observed_files.update(_extract_file_like_tokens(window_title or ""))
        observed_files.update(_extract_file_like_tokens(description or ""))
        repo_from_title = _extract_vscode_workspace_name(window_title or "")
        if repo_from_title:
            observed_repos.add(repo_from_title)
        observed_urls.update(_extract_urls(description or ""))

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
        dev_context={
            "observed_files": sorted(observed_files),
            "observed_repositories": sorted(observed_repos),
            "observed_urls": sorted(observed_urls),
        }
        if (observed_files or observed_repos or observed_urls)
        else None,
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

    if summary.dev_context:
        lines.append("\n## 開発コンテキスト\n")
        repos = summary.dev_context.get("observed_repositories") or []
        files = summary.dev_context.get("observed_files") or []
        urls = summary.dev_context.get("observed_urls") or []

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
        "dev_context": summary.dev_context,
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

def _best_effort_parse_json(text: str) -> dict:
    cleaned = (text or "").strip()
    if not cleaned:
        return {}
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1]
        if "```" in cleaned:
            cleaned = cleaned.split("```", 1)[0]
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return {}


def _coerce_str_list(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()] if str(value).strip() else []


_FILE_TOKEN_RE = re.compile(
    r"\b[\w./\\-]+\.(?:py|md|txt|json|ya?ml|toml|ini|cfg|csv|ts|js|jsx|tsx|html|css|ps1|bat|sh|ipynb)\b",
    re.IGNORECASE,
)


def _extract_file_like_tokens(text: str) -> list[str]:
    if not text:
        return []
    return [m.group(0) for m in _FILE_TOKEN_RE.finditer(text)]


def _extract_urls(text: str) -> list[str]:
    if not text:
        return []
    # Simple URL regex: stop at whitespace.
    return re.findall(r"https?://\S+", text)


def _extract_vscode_workspace_name(window_title: str) -> str | None:
    # Typical: "<file> - <workspace> - Visual Studio Code" or "<workspace> - Visual Studio Code"
    if not window_title:
        return None
    if "Visual Studio Code" not in window_title:
        return None
    parts = [p.strip() for p in window_title.split(" - ") if p.strip()]
    if not parts:
        return None
    # Remove trailing "Visual Studio Code"
    if parts and "Visual Studio Code" in parts[-1]:
        parts = parts[:-1]
    if not parts:
        return None
    # If there are 2+ parts left, the last one is usually workspace.
    if len(parts) >= 2:
        candidate = parts[-1].strip()
        return _filter_workspace_candidate(candidate)
    # Single part might be workspace.
    return _filter_workspace_candidate(parts[0].strip())


def _filter_workspace_candidate(candidate: str) -> str | None:
    if not candidate:
        return None
    # VS Code often includes diagnostic/status strings in the title; exclude obvious noise.
    noise_keywords = [
        "このファイルに",
        "問題",
        "変更済み",
        "保留中",
        "チャット",
    ]
    if any(k in candidate for k in noise_keywords):
        return None
    if len(candidate) > 60:
        return None
    return candidate


if __name__ == "__main__":
    main()
