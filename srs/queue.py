# -*- coding: utf-8 -*-
"""Session building: at most 60 cards per session, hardest due reviews first."""

import random
from dataclasses import dataclass, field

from . import store

MIN_SESSION_SIZE = 40
MAX_SESSION_SIZE = 60


@dataclass
class QueueItem:
    card: store.CardRow
    is_new: bool
    is_extra: bool = False


@dataclass
class SessionPlan:
    items: list = field(default_factory=list)
    due_total: int = 0
    new_total: int = 0
    extra_total: int = 0
    mandatory_total: int = 0
    mandatory_pending: int = 0


def _interleave(due, new):
    """Two due reviews, then one new word; leftovers keep their order."""
    mixed = []
    due_index = 0
    new_index = 0
    while due_index < len(due) or new_index < len(new):
        for _ in range(2):
            if due_index < len(due):
                mixed.append(due[due_index])
                due_index += 1
        if new_index < len(new):
            mixed.append(new[new_index])
            new_index += 1
    return mixed


def build_session(conn, now, today):
    # A session is capped at MAX_SESSION_SIZE cards. Today's imports go first,
    # then the unlearned backlog, then due reviews ordered by wrong-answer
    # count. Cards answered today are no longer due, so running the session
    # again the same day continues with the due words not yet reviewed. When
    # the total is below MIN_SESSION_SIZE, learned cards fill the gap.
    mandatory = store.today_imported_cards(conn, today)[:MAX_SESSION_SIZE]
    budget = MAX_SESSION_SIZE - len(mandatory)

    new = store.new_cards(conn, today, budget, exclude_today=True)

    budget -= len(new)
    due = store.due_cards(conn, now, today, budget, exclude_today=True)

    items = [QueueItem(card, True) for card in mandatory]
    items += _interleave(
        [QueueItem(card, False) for card in due],
        [QueueItem(card, True) for card in new],
    )

    extra = []
    missing = MIN_SESSION_SIZE - len(items)
    if missing > 0:
        taken = [item.card.voc_id for item in items]
        extra = store.extra_cards(
            conn, today, missing, taken, exclude_today=True
        )
        items.extend(QueueItem(card, False, True) for card in extra)

    random.shuffle(items)

    return SessionPlan(
        items=items,
        due_total=len(due),
        new_total=len(mandatory) + len(new),
        extra_total=len(extra),
        mandatory_total=len(mandatory),
        mandatory_pending=len(store.today_new_cards(conn, today)),
    )


def session_stats(conn, now, today):
    plan = build_session(conn, now, today)
    total_new = store.count_new_total(conn)
    total_cards = conn.execute("SELECT COUNT(*) FROM study_list").fetchone()[0]
    return {
        "due": store.count_due(conn, now, today),
        "new_total": total_new,
        "new_available": total_new,
        "new_today": plan.mandatory_total,
        "new_today_pending": plan.mandatory_pending,
        "session_new": plan.new_total,
        "session_due": plan.due_total,
        "due_remaining": max(0, store.count_due(conn, now, today) - plan.due_total),
        "session_review": plan.due_total + plan.extra_total,
        "extra_available": plan.extra_total,
        "total": total_cards,
    }