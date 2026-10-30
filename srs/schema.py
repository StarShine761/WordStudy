# -*- coding: utf-8 -*-
"""Schema v2 for word_study.db.

Adds FSRS card state to study_list and a review_log table. All changes are
idempotent and additive so the reading bank tables in the same file stay
untouched.
"""

import sqlite3
import time
from datetime import datetime

SCHEMA_VERSION = 2

CARD_COLUMNS = {
    "state": "INTEGER DEFAULT 0",
    "step": "INTEGER DEFAULT 0",
    "stability": "REAL",
    "difficulty": "REAL",
    "due_at": "INTEGER",
    "last_review_at": "INTEGER",
    "reps": "INTEGER DEFAULT 0",
    "lapses": "INTEGER DEFAULT 0",
    "is_leech": "INTEGER DEFAULT 0",
    "created_at": "INTEGER",
}

CREATE_STUDY_LIST = """
CREATE TABLE IF NOT EXISTS study_list (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    voc_id TEXT UNIQUE,
    book_id TEXT,
    vocabulary TEXT,
    add_date TEXT,
    first_study_date TEXT,
    next_study_date TEXT,
    last_response INTEGER DEFAULT 0,
    interval INTEGER DEFAULT 1,
    easiness REAL DEFAULT 2.5,
    repetition INTEGER DEFAULT 0,
    state INTEGER DEFAULT 0,
    step INTEGER DEFAULT 0,
    stability REAL,
    difficulty REAL,
    due_at INTEGER,
    last_review_at INTEGER,
    reps INTEGER DEFAULT 0,
    lapses INTEGER DEFAULT 0,
    is_leech INTEGER DEFAULT 0,
    created_at INTEGER
)
"""

CREATE_REVIEW_LOG = """
CREATE TABLE IF NOT EXISTS review_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    voc_id TEXT NOT NULL,
    rating INTEGER NOT NULL,
    reviewed_at INTEGER NOT NULL,
    local_date TEXT NOT NULL,
    state_before INTEGER,
    stability REAL,
    difficulty REAL,
    scheduled_days INTEGER,
    elapsed_days INTEGER,
    duration_ms INTEGER,
    is_practice INTEGER DEFAULT 0
)
"""


def _parse_local_date(value):
    try:
        return datetime.strptime(value, "%Y%m%d")
    except (TypeError, ValueError):
        return None


def _backfill_v1_cards(conn):
    """Translate the old SM-2 fields into an approximate FSRS starting state."""
    now = int(time.time())
    rows = conn.execute(
        """
        SELECT id, repetition, interval, easiness,
               first_study_date, next_study_date, add_date
        FROM study_list
        """
    ).fetchall()
    for row_id, repetition, interval, easiness, first_date, next_date, add_date in rows:
        repetition = repetition or 0
        created_dt = _parse_local_date(add_date)
        created_at = int(created_dt.timestamp()) if created_dt else now

        if repetition > 0:
            state = 2
            stability = max(float(interval or 1), 1.0)
            difficulty = min(10.0, max(1.0, 10.0 - ((easiness or 2.5) - 1.3) * 5.0))
            due_dt = _parse_local_date(next_date) or datetime.now()
            due_at = int(due_dt.timestamp())
            last_dt = _parse_local_date(first_date)
            last_review_at = int(last_dt.timestamp()) + 12 * 3600 if last_dt else None
        else:
            state = 0
            stability = None
            difficulty = None
            due_at = None
            last_review_at = None

        conn.execute(
            """
            UPDATE study_list
            SET state = ?, stability = ?, difficulty = ?, due_at = ?,
                last_review_at = ?, reps = COALESCE(reps, 0),
                lapses = COALESCE(lapses, 0), is_leech = COALESCE(is_leech, 0),
                created_at = ?
            WHERE id = ?
            """,
            (
                state, stability, difficulty, due_at,
                last_review_at, created_at, row_id,
            ),
        )


def ensure_schema(db_path):
    """Create or migrate word_study.db to schema v2."""
    conn = sqlite3.connect(db_path)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.execute(CREATE_STUDY_LIST)

        existing = {row[1] for row in conn.execute("PRAGMA table_info(study_list)")}
        for column, declaration in CARD_COLUMNS.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE study_list ADD COLUMN {column} {declaration}")

        conn.execute(CREATE_REVIEW_LOG)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_study_due ON study_list(state, due_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_study_created ON study_list(created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_review_log_voc ON review_log(voc_id, reviewed_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_review_log_date ON review_log(local_date)"
        )

        if version < SCHEMA_VERSION:
            _backfill_v1_cards(conn)

        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    finally:
        conn.close()
