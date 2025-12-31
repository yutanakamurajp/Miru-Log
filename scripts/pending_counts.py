from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def pending_count(db_path: Path) -> int | None:
    if not db_path.exists():
        return None

    try:
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                """
                SELECT COUNT(*)
                FROM captures
                WHERE id NOT IN (SELECT capture_id FROM analysis)
                """
            ).fetchone()
        return int((row or [0])[0])
    except Exception:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Miru-Log: print pending (unanalyzed) capture count from SQLite")
    parser.add_argument("--db", required=True, help="Path to mirulog.db")
    args = parser.parse_args()

    db_path = Path(args.db)
    count = pending_count(db_path)
    if count is None:
        print("NA")
    else:
        print(count)


if __name__ == "__main__":
    main()
