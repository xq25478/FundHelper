from __future__ import annotations

from datetime import date, timedelta


def is_trading_day(d: date) -> bool:
    """Naive: weekdays only. Replace with real exchange calendar later."""
    return d.weekday() < 5


def trading_days_between(start: date, end: date) -> int:
    if end < start:
        return 0
    days = 0
    cur = start
    while cur <= end:
        if is_trading_day(cur):
            days += 1
        cur += timedelta(days=1)
    return days
