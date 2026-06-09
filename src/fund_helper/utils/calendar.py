from __future__ import annotations

from datetime import date, timedelta
from typing import Collection


def is_trading_day(d: date, trade_dates: Collection[date] | None = None) -> bool:
    """Return whether a date is an A-share trading day.

    Pass an exchange calendar from ``TradingCalendarService`` for holiday-aware
    checks. Without one, this falls back to weekdays for pure utility callers.
    """
    if trade_dates is not None:
        return d in trade_dates
    return d.weekday() < 5


def next_trading_day(d: date, trade_dates: Collection[date] | None = None) -> date:
    cur = d + timedelta(days=1)
    for _ in range(370):
        if is_trading_day(cur, trade_dates):
            return cur
        cur += timedelta(days=1)
    return cur


def trading_days_between(start: date, end: date, trade_dates: Collection[date] | None = None) -> int:
    if end < start:
        return 0
    if trade_dates is not None:
        return sum(1 for d in trade_dates if start <= d <= end)
    days = 0
    cur = start
    while cur <= end:
        if is_trading_day(cur):
            days += 1
        cur += timedelta(days=1)
    return days
