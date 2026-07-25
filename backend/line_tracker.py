"""
Stores periodic odds snapshots so we can compute real line movement
(e.g. "spread opened -3, now -5.5, 80% of books moved toward home").
The Odds API free tier does not include historical odds, so we build
our own history by snapshotting whenever /api/picks is called or a
background refresh runs.
"""
import sqlite3
import json
import time
from contextlib import contextmanager
from config import DB_PATH


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL,
                sport_key TEXT NOT NULL,
                home_team TEXT,
                away_team TEXT,
                home_spread REAL,
                total REAL,
                home_ml REAL,
                away_ml REAL,
                captured_at REAL NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_event_time ON snapshots(event_id, captured_at)")


def record_snapshot(summary: dict):
    with get_db() as conn:
        conn.execute(
            """INSERT INTO snapshots
               (event_id, sport_key, home_team, away_team, home_spread, total, home_ml, away_ml, captured_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                summary["id"], summary["sport_key"], summary["home_team"], summary["away_team"],
                summary["consensus_home_spread"], summary["consensus_total"],
                summary["consensus_home_ml"], summary["consensus_away_ml"], time.time(),
            ),
        )


def get_line_movement(event_id: str) -> dict | None:
    """Return the earliest and latest recorded snapshot for an event, if we have 2+."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM snapshots WHERE event_id = ? ORDER BY captured_at ASC",
            (event_id,),
        ).fetchall()
    if len(rows) < 2:
        return None
    first, last = rows[0], rows[-1]
    return {
        "opening_spread": first["home_spread"],
        "current_spread": last["home_spread"],
        "spread_move": (
            round(last["home_spread"] - first["home_spread"], 1)
            if first["home_spread"] is not None and last["home_spread"] is not None
            else None
        ),
        "opening_total": first["total"],
        "current_total": last["total"],
        "total_move": (
            round(last["total"] - first["total"], 1)
            if first["total"] is not None and last["total"] is not None
            else None
        ),
        "opening_home_ml": first["home_ml"],
        "current_home_ml": last["home_ml"],
        "snapshots_count": len(rows),
        "first_seen": first["captured_at"],
        "last_seen": last["captured_at"],
    }
