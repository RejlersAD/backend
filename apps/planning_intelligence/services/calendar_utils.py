"""Working-day calendar helpers shared by the activity generator."""
from __future__ import annotations

import datetime

from ..config import DEFAULT_CALENDAR


def add_working_days(start: datetime.date, days: int, working_days_per_week: int | None = None) -> datetime.date:
    """Add N working days to `start` (start counts as day 0). Assumes a
    Mon-Fri week when working_days_per_week == 5 (the only supported value
    per ADNOC schedule requirements); falls back to calendar days otherwise."""
    wdpw = working_days_per_week or DEFAULT_CALENDAR['working_days_per_week']
    if wdpw >= 7:
        return start + datetime.timedelta(days=days)

    current = start
    remaining = days
    while remaining > 0:
        current += datetime.timedelta(days=1)
        if current.weekday() < 5:  # Mon-Fri
            remaining -= 1
    return current


def working_days_between(start: datetime.date, end: datetime.date) -> int:
    if end <= start:
        return 0
    count = 0
    current = start
    while current < end:
        current += datetime.timedelta(days=1)
        if current.weekday() < 5:
            count += 1
    return count
