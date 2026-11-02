# -*- coding: utf-8 -*-
"""Card and review-log persistence for the FSRS scheduler."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime

_COLUMNS = """
voc_id, vocabulary, state, step, stability, difficulty, due_at,
last_review_at, reps, lapses, is_leech, created_at,
interval, easiness, repetition, first_study_date, next_study_date
"""

_QUALIFIED_COLUMNS = ", ".join(
    f"s.{name.strip()}" for name in _COLUMNS.replace("\n", " ").split(",")
)

_DUE_PREDICATE = """
s.state > 0 AND s.due_at IS NOT NULL AND s.due_at <= ?
AND NOT EXISTS (
    SELECT 1 FROM review_log r
    WHERE r.voc_id = s.voc_id AND r.local_date = ? AND r.is_practice = 0
)
"""


@dataclass
class CardRow:
    voc_id: str
    vocabulary: str
    state: int
    step: int
    stability: float
    difficulty: float
    due_at: int
    last_review_at: int
    reps: int
    lapses: int
    is_leech: int
    created_at: int
    interval: int
    easiness: float
    repetition: int
    first_study_date: str
    next_study_date: str


def _to_card(row):
    keys = [column.strip() for column in _COLUMNS.replace("\n", " ").split(",")]
    return CardRow(**dict(zip(keys, row)))


def fetch_card(conn, voc_id):
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM study_list WHERE voc_id = ?", (voc_id,)
    ).fetchone()
    if row is None:
        return None
    return CardRow(**dict(row))


def add_card(conn, voc_id, vocabulary, now, today):
    """Insert a new card; returns 1 if inserted, 0 if it already existed."""
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO study_list
            (voc_id, vocabulary, add_date, state, created_at, reps, lapses, is_leech)
        VALUES (?, ?, ?, 0, ?, 0, 0, 0)
        """,
        (voc_id, vocabulary, today, now),
    )
    return cursor.rowcount


def due_cards(conn, now, today, limit):
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f"""
        SELECT {_COLUMNS} FROM study_list s
        WHERE {_DUE_PREDICATE}
        ORDER BY s.is_leech DESC, s.due_at ASC, s.id ASC
        LIMIT ?
        """,
        (now, today, limit),
    ).fetchall()
    return [CardRow(**dict(row)) for row in rows]


def count_due(conn, now, today):
    return conn.execute(
        f"SELECT COUNT(*) FROM study_list s WHERE {_DUE_PREDICATE}",
        (now, today),
    ).fetchone()[0]


def today_new_cards(conn, today):
    """Cards imported today and not yet studied: mandatory, quota-free."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f"""
        SELECT {_COLUMNS} FROM study_list s
        WHERE s.state = 0 AND s.add_date = ?
          AND NOT EXISTS (
              SELECT 1 FROM review_log r
              WHERE r.voc_id = s.voc_id AND r.local_date = ? AND r.is_practice = 0
          )
        ORDER BY s.id ASC
        """,
        (today, today),
    ).fetchall()
    return [CardRow(**dict(row)) for row in rows]


def new_cards(conn, today, limit, exclude_today=False):
    conn.row_factory = sqlite3.Row
    exclude_sql = "AND (s.add_date IS NULL OR s.add_date <> ?)" if exclude_today else ""
    params = [today]
    if exclude_today:
        params.append(today)
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT {_COLUMNS} FROM study_list s
        WHERE s.state = 0
          AND NOT EXISTS (
              SELECT 1 FROM review_log r
              WHERE r.voc_id = s.voc_id AND r.local_date = ? AND r.is_practice = 0
          )
          {exclude_sql}
        ORDER BY s.created_at IS NULL, s.created_at ASC, s.id ASC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [CardRow(**dict(row)) for row in rows]


def extra_cards(conn, today, limit, exclude_ids=()):
    """Learned cards used to top up a daily session.

    Today's reviewed cards come first, then the shakiest memories: leeches,
    many lapses, frequent mistakes, low stability and high difficulty.
    """
    if limit <= 0:
        return []
    exclude = [str(voc_id) for voc_id in exclude_ids]
    placeholders = ",".join("?" * len(exclude))
    exclude_sql = f"AND s.voc_id NOT IN ({placeholders})" if exclude else ""
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f"""
        SELECT {_QUALIFIED_COLUMNS} FROM study_list s
        LEFT JOIN (
            SELECT voc_id,
                   MAX(CASE WHEN local_date = ? THEN 1 ELSE 0 END) AS seen_today,
                   SUM(CASE WHEN rating = 1 THEN 1 ELSE 0 END) AS wrongs
            FROM review_log
            WHERE is_practice = 0
            GROUP BY voc_id
        ) h ON h.voc_id = s.voc_id
        WHERE s.state > 0
          {exclude_sql}
        ORDER BY COALESCE(h.seen_today, 0) DESC,
                 s.is_leech DESC,
                 COALESCE(h.wrongs, 0) DESC,
                 s.lapses DESC,
                 COALESCE(s.stability, 0) ASC,
                 COALESCE(s.difficulty, 0) DESC,
                 COALESCE(s.due_at, 0) ASC,
                 s.id ASC
        LIMIT ?
        """,
        [today, *exclude, limit],
    ).fetchall()
    return [CardRow(**dict(row)) for row in rows]


def count_new_total(conn):
    return conn.execute("SELECT COUNT(*) FROM study_list WHERE state = 0").fetchone()[0]


def new_introduced_today(conn, today):
    return conn.execute(
        """
        SELECT COUNT(DISTINCT voc_id) FROM review_log
        WHERE local_date = ? AND state_before = 0 AND is_practice = 0
        """,
        (today,),
    ).fetchone()[0]


def is_due(conn, voc_id, now):
    row = conn.execute(
        "SELECT state, due_at FROM study_list WHERE voc_id = ?", (voc_id,)
    ).fetchone()
    if row is None:
        return False
    state, due_at = row
    return bool(state and due_at and due_at <= now)


def log_review(conn, voc_id, rating, now, today, state_before,
               stability, difficulty, scheduled_days, elapsed_days,
               duration_ms, is_practice=0):
    conn.execute(
        """
        INSERT INTO review_log
            (voc_id, rating, reviewed_at, local_date, state_before, stability,
             difficulty, scheduled_days, elapsed_days, duration_ms, is_practice)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            voc_id, int(rating), now, today, state_before,
            stability, difficulty, scheduled_days, elapsed_days,
            duration_ms, int(bool(is_practice)),
        ),
    )


def apply_review(conn, card, state, step, stability, difficulty, due_epoch,
                 rating, now, today, duration_ms, leech_threshold):
    """Persist an FSRS review and its log row. Returns a summary dict."""
    reps = (card.reps or 0) + 1
    is_lapse = rating == 1 and card.state in (2, 3)
    lapses = (card.lapses or 0) + (1 if is_lapse else 0)
    is_leech = 1 if lapses >= leech_threshold else (card.is_leech or 0)
    scheduled_days = max(0, round((due_epoch - now) / 86400))
    elapsed_days = max(0, round((now - (card.last_review_at or now)) / 86400))
    due_local = datetime.fromtimestamp(due_epoch).strftime("%Y%m%d")
    first_date = card.first_study_date or (today if rating != 1 else None)

    conn.execute(
        """
        UPDATE study_list
        SET state = ?, step = ?, stability = ?, difficulty = ?, due_at = ?,
            last_review_at = ?, reps = ?, lapses = ?, is_leech = ?,
            interval = ?, repetition = ?, last_response = ?,
            next_study_date = ?, first_study_date = ?
        WHERE voc_id = ?
        """,
        (
            state, step, stability, difficulty, due_epoch,
            now, reps, lapses, is_leech,
            scheduled_days, reps, rating,
            due_local, first_date, card.voc_id,
        ),
    )
    log_review(
        conn, card.voc_id, rating, now, today, card.state,
        stability, difficulty, scheduled_days, elapsed_days,
        duration_ms, is_practice=0,
    )
    return {
        "state": state,
        "due_at": due_epoch,
        "due_local": due_local,
        "scheduled_days": scheduled_days,
        "lapses": lapses,
        "is_leech": is_leech,
    }
