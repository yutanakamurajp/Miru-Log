from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
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

    rows = _load_daily_rows(settings.capture.archive_root, target_date, logger)
    if not rows:
        logger.warning("No analyzed captures for %s", target_date)
        summary = DailySummary(
            date=target_date,
            segments=[],
            blocking_issues=[],
            follow_ups=[],
            total_active_minutes=0.0,
            markdown_path=None,
            dev_context=None,
        )
    else:
        summary = build_daily_summary(rows, target_date, settings.capture.interval_seconds)

    summary_dir = settings.output.summary_dir
    summary_dir.mkdir(parents=True, exist_ok=True)

    # Use compact date for filenames: daily-report-YYYYMMDD.*
    compact_date = target_date.replace("-", "")
    markdown_path = summary_dir / f"daily-report-{compact_date}.md"
    json_path = summary_dir / f"daily-report-{compact_date}.json"

    summary.markdown_path = markdown_path

    markdown_path.write_text(render_markdown(summary), encoding="utf-8")
    json_path.write_text(json.dumps(to_dict(summary), ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Daily summary saved to %s", markdown_path)


def _load_daily_rows(archive_root: Path, target_date: str, logger):
    """Load analyzed rows for a date.

    - If archive_root/mirulog.db exists: single DB mode.
    - Else: multi-PC mode (scan immediate subfolders for */mirulog.db).

    Returns rows compatible with build_daily_summary().
    In multi-PC mode, each row is prefixed with pc_name.
    """

    pc_dbs: list[tuple[str, Path]] = []
    if archive_root.exists():
        for child in archive_root.iterdir():
            if not child.is_dir():
                continue
            db_path = child / "mirulog.db"
            if db_path.exists():
                pc_dbs.append((child.name, db_path))

    # Prefer multi-PC aggregation when any PC DBs exist under archive_root.
    # This supports setups where an older single-DB (archive_root/mirulog.db)
    # still exists alongside the new per-PC folders.
    if pc_dbs:
        logger.info("Multi-PC archive detected under %s (pcs=%s)", archive_root, len(pc_dbs))
        all_rows = []
        for pc_name, db_path in sorted(pc_dbs, key=lambda x: x[0].lower()):
            repo = ObservationRepository(db_path)
            rows = repo.daily_analysis(target_date)
            for row in rows:
                all_rows.append((pc_name, *row))

        # Sort by captured_at (ISO) to make timeline coherent across PCs.
        all_rows.sort(key=lambda r: r[2])
        return all_rows

    single_db = archive_root / "mirulog.db"
    if single_db.exists():
        repo = ObservationRepository(single_db)
        return repo.daily_analysis(target_date)

    return []


def build_daily_summary(rows, date_str: str, interval_seconds: int) -> DailySummary:
    interval_minutes = interval_seconds / 60.0
    segments: List[SummarySegment] = []
    blocking: List[str] = []
    followups: List[str] = []
    current = None
    observed_files: set[str] = set()
    observed_repos: set[str] = set()
    observed_urls: set[str] = set()

    for row in rows:
        if len(row) == 9:
            pc_name = None
            capture_id, ts_str, window_title, app, description, task, confidence, tags_str, raw_response = row
        elif len(row) == 10:
            pc_name, capture_id, ts_str, window_title, app, description, task, confidence, tags_str, raw_response = row
        else:
            raise ValueError(f"Unexpected row shape: {len(row)}")

        ts = datetime.fromisoformat(ts_str)
        task = _normalize_task_label(task or "Unclassified")
        tags = [t.strip() for t in (tags_str or "").split(",") if t.strip()]
        highlight_prefix = f"[{pc_name}] " if pc_name else ""
        highlight = f"{highlight_prefix}{ts.strftime('%H:%M')} {description}"

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

        blocking_entry = f"{highlight_prefix}{description}" if pc_name else description
        if any(keyword in description.lower() for keyword in ("error", "fail", "exception")):
            blocking.append(blocking_entry)
        if any(tag.lower() in {"todo", "follow-up"} for tag in tags):
            followups.append(blocking_entry)

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
    totals = _aggregate_task_totals(summary, top_n=8)
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

    if summary.blocking_issues:
        lines.append("\n## ブロッカー\n")
        for issue in summary.blocking_issues:
            lines.append(f"- {issue}")

    if summary.follow_ups:
        lines.append("\n## フォローアップ\n")
        for item in summary.follow_ups:
            lines.append(f"- {item}")

    return "\n".join(lines)

def _aggregate_task_totals(summary: DailySummary, *, top_n: int = 8) -> list[tuple[str, float]]:
    totals: dict[str, float] = {}
    for segment in summary.segments:
        totals[segment.dominant_task] = totals.get(segment.dominant_task, 0.0) + segment.duration_minutes
    sorted_totals = sorted(totals.items(), key=lambda item: item[1], reverse=True)
    if top_n <= 0 or len(sorted_totals) <= top_n:
        return sorted_totals
    head = sorted_totals[:top_n]
    other_minutes = sum(m for _, m in sorted_totals[top_n:])
    if other_minutes > 0:
        head.append(("その他", other_minutes))
    return head

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


def _normalize_task_label(task: str) -> str:
    """Coarsen task labels so the report is easier to read.

    The analyzer's primary_task can be too fine-grained. We map common keywords
    into coarse buckets.
    """

    raw = (task or "").strip()
    if not raw:
        return "Unclassified"

    t = raw.lower()

    # Communication / coordination
    if any(k in t for k in ["メール", "mail", "slack", "teams", "チャット", "連絡", "返信", "対応"]):
        return "連絡/調整"

    # Meetings
    if any(k in t for k in ["mtg", "meeting", "会議", "打合せ", "打ち合わせ", "面談", "1on1", "レビュー会"]):
        return "ミーティング"

    # Coding / development
    if any(k in t for k in ["実装", "コーディング", "コード", "開発", "修正", "refactor", "リファクタ", "プログラム"]):
        return "開発(コード)"

    # Debugging / troubleshooting
    if any(k in t for k in ["debug", "デバッグ", "不具合", "バグ", "エラー", "障害", "原因", "調査(不具合)"]):
        return "デバッグ/不具合対応"

    # Research
    if any(k in t for k in ["調査", "リサーチ", "検討", "比較", "方針", "設計", "仕様", "理解"]):
        return "調査/検討"

    # Docs / writing
    if any(k in t for k in ["ドキュメント", "資料", "readme", "md", "markdown", "メモ", "日報", "報告", "文章"]):
        return "ドキュメント/記録"

    # Reading
    if any(k in t for k in ["閲覧", "読む", "読書", "視聴", "学習", "チュートリアル"]):
        return "閲覧/学習"

    return raw


if __name__ == "__main__":
    main()
