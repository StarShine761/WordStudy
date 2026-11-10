# -*- coding: utf-8 -*-
"""Session building: all new words (no daily cap), reviews = 2x new words."""

import random
from dataclasses import dataclass, field

from . import store


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
    # Every word imported today is studied in every session today, even if it
    # was already answered. All remaining unlearned new words join them: there
    # is no daily cap. The final order is shuffled so repeated runs do not
    # replay the same sequence.
    mandatory = store.today_imported_cards(conn, today)
    new = store.new_cards(conn, today, exclude_today=True)
    new_count = len(mandatory) + len(new)

    if new_count:
        # Reviews are twice the new-word count; due cards first, learned
        # cards top up when fewer than 2x are due.
        review_target = 2 * new_count
        due = store.due_cards(conn, now, today, review_target, exclude_today=True)
    else:
        # No new words today: review everything that is due.
        review_target = None
        due = store.due_cards(conn, now, today, exclude_today=True)

    items = [QueueItem(card, True) for card in mandatory]
    items += _interleave(
        [QueueItem(card, False) for card in due],
        [QueueItem(card, True) for card in new],
    )

    extra = []
    if review_target is not None:
        missing = review_target - len(due)
        if missing > 0:
            taken = [item.card.voc_id for item in items if not item.is_new]
            extra = store.extra_cards(
                conn, today, missing, taken, exclude_today=True
            )
            items.extend(QueueItem(card, False, True) for card in extra)

    random.shuffle(items)

    return SessionPlan(
        items=items,
        due_total=len(due),
        new_total=new_count,
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
        "session_review": plan.due_total + plan.extra_total,
        "extra_available": plan.extra_total,
        "total": total_cards,
    }