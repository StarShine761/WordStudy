# -*- coding: utf-8 -*-
"""Thin wrapper around py-fsrs with the CLI's rating policy."""

import hashlib
import time
from datetime import datetime, timedelta, timezone

from fsrs import Card, Rating, Scheduler, State

RATING_BY_RESPONSE = {1: Rating.Good, 2: Rating.Hard, 3: Rating.Again}


def choose_rating(correct, duration_ms, slow_ms, hint=False):
    """Map a CLI answer to an FSRS rating.

    Wrong answers must be Again; slow or hinted recalls are Hard.
    Easy is intentionally not auto-assigned, since fast guessing would pollute
    the difficulty estimate.
    """
    if not correct:
        return Rating.Again
    if hint or (duration_ms is not None and duration_ms > slow_ms):
        return Rating.Hard
    return Rating.Good


def _card_id(voc_id):
    return int(hashlib.sha1(str(voc_id).encode("utf-8")).hexdigest()[:15], 16)


class FSRSEngine:
    def __init__(self, desired_retention=0.9, maximum_interval=3650,
                 enable_fuzzing=True):
        self.scheduler = Scheduler(
            desired_retention=desired_retention,
            learning_steps=(),
            relearning_steps=(),
            maximum_interval=maximum_interval,
            enable_fuzzing=enable_fuzzing,
        )

    def _to_card(self, row, now):
        card_id = _card_id(row.voc_id)
        if row.state == 0 and not (row.reps or 0):
            return Card(
                card_id=card_id,
                state=State.Learning,
                step=0,
                stability=None,
                difficulty=None,
                due=now,
                last_review=None,
            )
        due = (
            datetime.fromtimestamp(row.due_at, tz=timezone.utc)
            if row.due_at else now
        )
        last_review = (
            datetime.fromtimestamp(row.last_review_at, tz=timezone.utc)
            if row.last_review_at else None
        )
        return Card(
            card_id=card_id,
            state=State(row.state or State.Learning.value),
            step=row.step or 0,
            stability=row.stability,
            difficulty=row.difficulty,
            due=due,
            last_review=last_review,
        )

    def review(self, row, rating, now_epoch=None, duration_ms=None):
        """Return (updated_card, is_first_review)."""
        now = datetime.fromtimestamp(now_epoch or time.time(), tz=timezone.utc)
        updated, _ = self.scheduler.review_card(
            self._to_card(row, now),
            rating,
            review_datetime=now,
            review_duration=duration_ms,
        )
        is_new = row.state == 0 and not (row.reps or 0)
        if is_new and rating != Rating.Again:
            # First successful exposure always comes back tomorrow, then FSRS
            # takes over from the second review.
            updated.due = now + timedelta(days=1)
        return updated, is_new
