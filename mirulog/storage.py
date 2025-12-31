from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable, List

from .models import AnalysisResult, CaptureRecord
from .utils import ensure_directory


class ObservationRepository:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        ensure_directory(db_path.parent)
        self._initialize()

    def _initialize(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS captures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    captured_at TEXT NOT NULL,
                    image_path TEXT NOT NULL,
                    window_title TEXT,
                    active_application TEXT,
                    session_state TEXT,
                    hash_digest TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS analysis (
                    capture_id INTEGER PRIMARY KEY,
                    description TEXT NOT NULL,
                    primary_task TEXT,
                    confidence REAL,
                    tags TEXT,
                    raw_response TEXT,
                    FOREIGN KEY (capture_id) REFERENCES captures(id)
                )
                """
            )
            conn.commit()

    def add_capture(self, record: CaptureRecord) -> int:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO captures (captured_at, image_path, window_title, active_application, session_state, hash_digest)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record.captured_at.isoformat(),
                    str(record.image_path),
                    record.window_title,
                    record.active_application,
                    record.session_state,
                    record.hash_digest,
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def pending_captures(self, limit: int = 25) -> List[CaptureRecord]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, captured_at, image_path, window_title, active_application, session_state, hash_digest
                FROM captures
                WHERE id NOT IN (SELECT capture_id FROM analysis)
                ORDER BY captured_at ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        records: List[CaptureRecord] = []
        for row in rows:
            captured_at = datetime.fromisoformat(row[1])
            records.append(
                CaptureRecord(
                    id=row[0],
                    captured_at=captured_at,
                    image_path=Path(row[2]),
                    window_title=row[3] or "",
                    active_application=row[4] or "",
                    session_state=row[5] or "active",
                    hash_digest=row[6],
                )
            )
        return records

    def pending_count(self) -> int:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT COUNT(*)
                FROM captures
                WHERE id NOT IN (SELECT capture_id FROM analysis)
                """
            ).fetchone()
        return int((row or [0])[0])

    def save_analysis(self, result: AnalysisResult) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO analysis (capture_id, description, primary_task, confidence, tags, raw_response)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    result.capture_id,
                    result.description,
                    result.primary_task,
                    result.confidence,
                    ",".join(result.tags),
                    result.raw_response,
                ),
            )
            conn.commit()

    def daily_analysis(self, date_prefix: str) -> List[tuple]:
        query = """
            SELECT c.id, c.captured_at, c.window_title, c.active_application, a.description, a.primary_task, a.confidence, a.tags, a.raw_response
            FROM captures c
            JOIN analysis a ON c.id = a.capture_id
            WHERE substr(c.captured_at, 1, 10) = ?
            ORDER BY c.captured_at
        """
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute(query, (date_prefix,)).fetchall()