from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
import os.path
import pickle
import re
import hashlib
from typing import Iterable

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError

from mirulog.config import get_settings
from mirulog.logging_utils import init_logger
from mirulog.models import DailySummary, SummarySegment
from mirulog.visualizer import NanobananaClient


# Google Calendar APIのスコープ
SCOPES = ['https://www.googleapis.com/auth/calendar']


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
    export_name = f"{target_date.replace('-', '')}_Miru-Log.md"
    export_path = export_dir / export_name
    export_path.write_text(render_japanese_report(summary), encoding="utf-8")
    logger.info("Daily log exported to %s", export_path)

    if settings.visualization.enabled:
        _maybe_generate_infographic(summary, export_path.with_suffix(".png"), settings, logger)

    # Googleカレンダーにログ（活動があった時間帯）を追加
    _export_activity_windows_to_calendar(summary, settings, logger)


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
    # summarizer.py は daily-report-YYYYMMDD.json を出力する。
    # notifier は --date で YYYY-MM-DD を受け取りやすいので両方を探索する。
    compact_date = date_str.replace("-", "")
    candidates = [
        summary_dir / f"daily-report-{compact_date}.json",
        summary_dir / f"daily-report-{date_str}.json",
    ]
    json_path = next((p for p in candidates if p.exists()), None)
    if json_path is None:
        raise FileNotFoundError(
            "Summary JSON not found. Tried: " + ", ".join(str(p) for p in candidates)
        )

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


def authenticate_google_calendar():
    """Google Calendar APIに認証する"""
    creds = None
    # トークンファイルが存在する場合は読み込む
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)
    # 認証が必要な場合
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError:
                # invalid_grant 等で失効している場合はトークンを破棄して再認証
                try:
                    os.remove('token.pickle')
                except OSError:
                    pass
                creds = None
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            try:
                # ブラウザ→localhost へのリダイレクトを使う（一般的）
                creds = flow.run_local_server(port=0)
            except Exception:
                # 環境によっては localhost が拒否/プロキシで死ぬのでコンソール方式へフォールバック
                creds = flow.run_console()
        # トークンを保存
        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)
    return build('calendar', 'v3', credentials=creds)

_PERIOD_RE = re.compile(r"^\s*(\d{2}):(\d{2})\s*-\s*(\d{2}):(\d{2})\s*$")


def _parse_period_on_date(*, date_str: str, period_label: str, tz) -> tuple[datetime, datetime]:
    m = _PERIOD_RE.match(period_label or "")
    if not m:
        raise ValueError(f"Unexpected period label: {period_label!r}")
    sh, sm, eh, em = map(int, m.groups())
    day = datetime.fromisoformat(date_str).date()
    start = datetime(day.year, day.month, day.day, sh, sm, tzinfo=tz)
    end = datetime(day.year, day.month, day.day, eh, em, tzinfo=tz)
    # 日跨ぎ（稀）も念のためケア
    if end <= start:
        end = end + timedelta(days=1)
    return start, end


def _stable_event_id(*, date_compact: str, start_hhmm: str, end_hhmm: str, kind: str) -> str:
    base = f"{date_compact}|{start_hhmm}|{end_hhmm}|{kind}".encode("utf-8")
    # Calendar API の event.id は厳しめに弾かれることがあるため、
    # 確実性重視で「英小文字+数字のみ」の安定IDにする。
    digest = hashlib.sha1(base).hexdigest()  # 40 chars [0-9a-f]
    return f"ml{digest}"  # 42 chars, starts with a letter


def _iter_activity_blocks(summary: DailySummary, *, date_str: str, tz) -> Iterable[dict]:
    """ログが存在する時間帯をブロック化する（タスク無視、連続時間は結合）。"""

    periods: list[tuple[datetime, datetime]] = []
    for seg in summary.segments:
        try:
            start_dt, end_dt = _parse_period_on_date(date_str=date_str, period_label=seg.period_label, tz=tz)
        except Exception:
            continue
        periods.append((start_dt, end_dt))

    periods.sort(key=lambda p: p[0])
    if not periods:
        return []

    merged: list[tuple[datetime, datetime]] = []
    cur_start, cur_end = periods[0]
    for start_dt, end_dt in periods[1:]:
        if start_dt <= cur_end:
            # 連続 or 重複は結合
            if end_dt > cur_end:
                cur_end = end_dt
            continue
        merged.append((cur_start, cur_end))
        cur_start, cur_end = start_dt, end_dt
    merged.append((cur_start, cur_end))

    blocks: list[dict] = []
    for start_dt, end_dt in merged:
        minutes = (end_dt - start_dt).total_seconds() / 60.0
        blocks.append({"start": start_dt, "end": end_dt, "minutes": minutes})
    return blocks


def _most_frequent_task_for_block(
    summary: DailySummary,
    *,
    date_str: str,
    tz,
    block_start: datetime,
    block_end: datetime,
) -> str:
    counts: defaultdict[str, int] = defaultdict(int)
    for seg in summary.segments:
        try:
            start_dt, end_dt = _parse_period_on_date(date_str=date_str, period_label=seg.period_label, tz=tz)
        except Exception:
            continue

        if end_dt <= block_start or start_dt >= block_end:
            continue

        task = (seg.dominant_task or "").strip()
        if not task:
            continue
        counts[task] += 1

    if not counts:
        return "作業"

    return max(counts.items(), key=lambda item: (item[1], item[0]))[0]


def _upsert_event(service, *, calendar_id: str, event: dict) -> None:
    try:
        service.events().insert(calendarId=calendar_id, body=event).execute()
    except HttpError as exc:
        # 409: already exists（同じ eventId で再実行）
        status = getattr(getattr(exc, "resp", None), "status", None)
        if status == 409:
            service.events().update(calendarId=calendar_id, eventId=event["id"], body=event).execute()
            return
        raise


def _export_activity_windows_to_calendar(summary: DailySummary, settings, logger) -> None:
    if not summary.segments:
        logger.info("No segments for %s; skip calendar export", summary.date)
        return

    calendar_id = (os.getenv("GOOGLE_CALENDAR_ID") or "primary").strip() or "primary"
    tz = settings.timezone
    date_compact = summary.date.replace("-", "")

    try:
        service = authenticate_google_calendar()
    except Exception as exc:
        logger.warning("Failed to authenticate Google Calendar: %s", exc)
        return

    blocks = list(_iter_activity_blocks(summary, date_str=summary.date, tz=tz))
    if not blocks:
        logger.info("No calendar blocks to export for %s", summary.date)
        return

    exported = 0
    for b in blocks:
        start_dt = b["start"]
        end_dt = b["end"]
        minutes = float(b.get("minutes", 0.0))
        start_hhmm = start_dt.strftime("%H%M")
        end_hhmm = end_dt.strftime("%H%M")
        task = _most_frequent_task_for_block(
            summary,
            date_str=summary.date,
            tz=tz,
            block_start=start_dt,
            block_end=end_dt,
        )

        desc = "\n".join(
            [
                f"date: {summary.date}",
                f"duration_minutes: {minutes:.1f}",
            ]
        )

        event = {
            "id": _stable_event_id(date_compact=date_compact, start_hhmm=start_hhmm, end_hhmm=end_hhmm, kind="activity"),
            "summary": f"Miru-Log: {task}",
            "description": desc,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": str(tz)},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": str(tz)},
        }

        try:
            _upsert_event(service, calendar_id=calendar_id, event=event)
            exported += 1
        except Exception as exc:
            logger.warning("Failed to upsert calendar event (%s-%s): %s", start_hhmm, end_hhmm, exc)

    logger.info("Exported %s calendar events for %s (calendarId=%s)", exported, summary.date, calendar_id)


if __name__ == "__main__":
    main()
