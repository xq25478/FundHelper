"""A-share exchange trading calendar cache."""
from __future__ import annotations

import io
import logging
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ..config import AppConfig
from ..storage import connect

log = logging.getLogger(__name__)
TZ = ZoneInfo("Asia/Shanghai")
EXCHANGE = "CN_A_SHARE"
SOURCE = "akshare.tool_trade_date_hist_sina"
CACHE_TTL_DAYS = 7
MIN_FUTURE_DAYS = 180


@dataclass(slots=True)
class TradingCalendarSnapshot:
    dates: set[date]
    source: str
    fetched_at: str | None
    error: str | None = None

    @property
    def is_fallback(self) -> bool:
        return self.source == "weekday_fallback"


class TradingCalendarService:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.conn = connect(cfg.data_dir / "fund.db")

    def get_calendar(self, *, force_refresh: bool = False) -> TradingCalendarSnapshot:
        cached = self._load()
        if not force_refresh and self._cache_usable(cached):
            return cached

        try:
            dates = _fetch_trade_dates()
            if dates:
                self._replace(dates)
                return self._load()
        except Exception as e:  # noqa: BLE001
            log.warning("trading calendar fetch failed: %s", e)
            if cached.dates:
                cached.error = str(e)
                return cached
            return _weekday_snapshot(error=str(e))

        if cached.dates:
            return cached
        return _weekday_snapshot(error="remote calendar returned empty")

    def is_trading_day(
        self,
        d: date,
        *,
        snapshot: TradingCalendarSnapshot | None = None,
    ) -> bool:
        snapshot = snapshot or self.get_calendar()
        if snapshot.dates:
            return d in snapshot.dates
        return d.weekday() < 5

    def next_trading_day(
        self,
        d: date,
        *,
        snapshot: TradingCalendarSnapshot | None = None,
    ) -> date:
        snapshot = snapshot or self.get_calendar()
        cur = d + timedelta(days=1)
        for _ in range(370):
            if self.is_trading_day(cur, snapshot=snapshot):
                return cur
            cur += timedelta(days=1)
        return _next_weekday(d)

    def trading_days_between(
        self,
        start: date,
        end: date,
        *,
        snapshot: TradingCalendarSnapshot | None = None,
    ) -> int:
        if end < start:
            return 0
        snapshot = snapshot or self.get_calendar()
        if snapshot.dates:
            return sum(1 for d in snapshot.dates if start <= d <= end)
        cur = start
        count = 0
        while cur <= end:
            if cur.weekday() < 5:
                count += 1
            cur += timedelta(days=1)
        return count

    def _load(self) -> TradingCalendarSnapshot:
        rows = self.conn.execute(
            "SELECT trade_date, source, fetched_at FROM trading_calendar WHERE exchange=?",
            (EXCHANGE,),
        ).fetchall()
        dates: set[date] = set()
        source = SOURCE
        fetched_at = None
        for raw_date, raw_source, raw_fetched_at in rows:
            try:
                dates.add(date.fromisoformat(raw_date))
            except ValueError:
                continue
            source = raw_source or source
            fetched_at = max(fetched_at or raw_fetched_at, raw_fetched_at)
        return TradingCalendarSnapshot(dates=dates, source=source, fetched_at=fetched_at)

    def _replace(self, dates: set[date]) -> None:
        now = datetime.now(TZ).isoformat(timespec="seconds")
        rows = [(EXCHANGE, d.isoformat(), SOURCE, now) for d in sorted(dates)]
        self.conn.execute("BEGIN")
        try:
            self.conn.execute("DELETE FROM trading_calendar WHERE exchange=?", (EXCHANGE,))
            self.conn.executemany(
                """INSERT INTO trading_calendar(exchange, trade_date, source, fetched_at)
                   VALUES(?,?,?,?)""",
                rows,
            )
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def _cache_usable(self, snapshot: TradingCalendarSnapshot) -> bool:
        if not snapshot.dates or not snapshot.fetched_at:
            return False
        today = datetime.now(TZ).date()
        if min(snapshot.dates) > today or max(snapshot.dates) < today + timedelta(days=MIN_FUTURE_DAYS):
            return False
        try:
            fetched = datetime.fromisoformat(snapshot.fetched_at)
            if fetched.tzinfo is None:
                fetched = fetched.replace(tzinfo=TZ)
        except ValueError:
            return False
        return (datetime.now(TZ) - fetched.astimezone(TZ)).days < CACHE_TTL_DAYS


def _fetch_trade_dates() -> set[date]:
    import akshare as ak

    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        df = ak.tool_trade_date_hist_sina()
    dates: set[date] = set()
    for value in df["trade_date"].tolist():
        if isinstance(value, date):
            dates.add(value)
        else:
            dates.add(date.fromisoformat(str(value)[:10]))
    return dates


def _weekday_snapshot(*, error: str | None = None) -> TradingCalendarSnapshot:
    today = datetime.now(TZ).date()
    start = today - timedelta(days=370)
    end = today + timedelta(days=370)
    cur = start
    dates: set[date] = set()
    while cur <= end:
        if cur.weekday() < 5:
            dates.add(cur)
        cur += timedelta(days=1)
    return TradingCalendarSnapshot(
        dates=dates,
        source="weekday_fallback",
        fetched_at=datetime.now(TZ).isoformat(timespec="seconds"),
        error=error,
    )


def _next_weekday(d: date) -> date:
    cur = d + timedelta(days=1)
    while cur.weekday() >= 5:
        cur += timedelta(days=1)
    return cur
