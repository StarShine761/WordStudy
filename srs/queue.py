# -*- coding: utf-8 -*-
"""Session building: due reviews first, new words under a daily quota."""

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
    new_introduced_today: int = 0
    new_quota: int = 0
    extra_total: int = 0
    mandatory_total: int = 0
    mandatory_pending: int = 0


def _interleave(due, new):
    """Three due reviews, then one new word; leftovers keep their order."""
    mixed = []
    due_index = 0
    new_index = 0
    while due_index < len(due) or new_index < len(new):
        for _ in range(3):
            if due_index < len(due):
                mixed.append(due[due_index])
                due_index += 1
        if new_index < len(new):
            mixed.append(new[new_index])
            new_index += 1
    return mixed


def build_session(conn, now, today, size, new_limit):
    size = max(1, int(size))
    # Every word imported today is studied in every session today, even if it
    # was already answered: they lead the session and bypass the daily quota.
    mandatory = store.today_imported_cards(conn, today)
    slots = max(0, size - len(mandatory))
    due = store.due_cards(conn, now, today, slots, exclude_today=True)
    introduced = store.new_introduced_today(conn, today)
    quota = max(0, int(new_limit) - introduced)
    quota = min(quota, max(0, slots - len(due)))
    new = store.new_cards(conn, today, quota, exclude_today=True)

    items = [QueueItem(card, True) for card in mandatory]
    items += _interleave(
        [QueueItem(card, False) for card in due],
        [QueueItem(card, True) for card in new],
    )

    # Always fill up to the session size: when nothing is due and the daily
    # new-word quota is used up, revisit today's and the shakiest learned
    # words instead of ending the day with fewer than the target count.
    extra = []
    missing = size - len(items)
    if missing > 0:
        taken = [item.card.voc_id for item in items if not item.is_new]
        extra = store.extra_cards(
            conn, today, missing, taken, exclude_today=True
        )
        items.extend(QueueItem(card, False, True) for card in extra)

    return SessionPlan(
        items=items,
        due_total=len(due),
        new_total=len(mandatory) + len(new),
        new_introduced_today=introduced,
        new_quota=quota,
        extra_total=len(extra),
        mandatory_total=len(mandatory),
        mandatory_pending=len(store.today_new_cards(conn, today)),
    )


def session_stats(conn, now, today, new_limit, size):
    plan = build_session(conn, now, today, size, new_limit)
    introduced = plan.new_introduced_today
    total_new = store.count_new_total(conn)
    total_cards = conn.execute("SELECT COUNT(*) FROM study_list").fetchone()[0]
    remaining = max(0, int(new_limit) - introduced)
    pending = plan.mandatory_pending
    regular_new = max(0, total_new - pending)
    return {
        "due": store.count_due(conn, now, today),
        "new_introduced": introduced,
        "new_total": total_new,
        "new_available": pending + min(regular_new, remaining),
        "new_today": plan.mandatory_total,
        "new_today_pending": pending,
        "new_limit": int(new_limit),
        "extra_available": plan.extra_total,
        "total": total_cards,
    }
