"""Cache-aware NAV access. Library code should call get_nav_cached(),
never call data sources directly.

Cache policy (per code, per call):
  1. Look at sqlite for rows in [start, end].
  2. If no rows at all -> remote fetch full window.
  3. If rows exist but newest local trade_date < end - 2 cal-days
       AND latest fetched_at is older than TTL  -> incremental fetch
       from (max_trade_date + 1) to end.
  4. Otherwise -> serve straight from sqlite.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from ..config import AppConfig
from ..datasource.base import FundDataSource
from ..datasource.tiantian import TiantianDataSource
from ..domain import NavSeries
from ..storage import NavRepo, connect
from ..storage.retention import prune_daily_tables

log = logging.getLogger(__name__)


@dataclass(slots=True)
class FetchOutcome:
    code: str
    window_start: date
    window_end: date
    rows_in_db_before: int
    rows_fetched: int
    rows_in_db_after: int
    source_used: str
    status: str          # 'hit' | 'fetched' | 'incremental' | 'empty' | 'error'
    message: str | None = None


class NavService:
    def __init__(self, cfg: AppConfig,
                 source: FundDataSource | None = None) -> None:
        self.cfg = cfg
        self.conn = connect(cfg.data_dir / "fund.db")
        self.repo = NavRepo(self.conn)
        self.source = source or TiantianDataSource()
        self.ttl_hours = cfg.cache.nav_ttl_hours

    # ---------------------------------------------------------------- public
    def get_nav(self, code: str, lookback_days: int = 180,
                end: date | None = None,
                force_refresh: bool = False) -> tuple[NavSeries, FetchOutcome]:
        end = end or date.today()
        start = end - timedelta(days=lookback_days)
        return self._get_window(code, start, end, force_refresh)

    def get_nav_range(self, code: str, start: date, end: date,
                      force_refresh: bool = False) -> tuple[NavSeries, FetchOutcome]:
        return self._get_window(code, start, end, force_refresh)

    # ---------------------------------------------------------------- core
    def _get_window(self, code: str, start: date, end: date,
                    force_refresh: bool) -> tuple[NavSeries, FetchOutcome]:
        start_s, end_s = start.isoformat(), end.isoformat()
        before = self.repo.load(code, start_s, end_s)
        rows_before = 0 if before is None else len(before.frame)
        max_local = self.repo.max_trade_date(code)
        latest_fetch = self.repo.latest_fetched_at(code)

        # decide if we need remote
        need_remote, status = self._needs_remote(
            rows_before, max_local, latest_fetch, end, force_refresh
        )

        if not need_remote:
            series = before or self._empty(code)
            outcome = FetchOutcome(
                code=code, window_start=start, window_end=end,
                rows_in_db_before=rows_before, rows_fetched=0,
                rows_in_db_after=rows_before, source_used="sqlite",
                status="hit",
            )
            return series, outcome

        fetch_start = self._fetch_start(max_local, start, force_refresh)
        log.info("nav-fetch %s %s -> %s (db_before=%d)",
                 code, fetch_start, end, rows_before)
        try:
            remote = self.source.get_nav(code, fetch_start, end)
        except Exception as e:  # noqa: BLE001
            self.repo.log_fetch(code, fetch_start.isoformat(), end_s,
                                0, self.source.name, "error", str(e))
            if before is not None:
                return before, FetchOutcome(
                    code=code, window_start=start, window_end=end,
                    rows_in_db_before=rows_before, rows_fetched=0,
                    rows_in_db_after=rows_before, source_used="sqlite",
                    status="error", message=str(e),
                )
            raise

        written = self.repo.upsert_series(remote, source=self.source.name)
        self.repo.log_fetch(
            code, fetch_start.isoformat(), end_s,
            written, self.source.name,
            "ok" if written else "empty",
        )
        prune_daily_tables(self.conn)

        after = self.repo.load(code, start_s, end_s) or self._empty(code)
        return after, FetchOutcome(
            code=code, window_start=start, window_end=end,
            rows_in_db_before=rows_before, rows_fetched=written,
            rows_in_db_after=len(after.frame),
            source_used=self.source.name,
            status=status,
        )

    # ---------------------------------------------------------------- helpers
    def _needs_remote(self, rows_before: int, max_local: str | None,
                      latest_fetch: datetime | None, end: date,
                      force_refresh: bool) -> tuple[bool, str]:
        if force_refresh:
            return True, "fetched"
        if rows_before == 0 or max_local is None:
            return True, "fetched"
        # incremental: local max date is more than 2 calendar days behind end
        local_max = date.fromisoformat(max_local)
        if (end - local_max).days > 2:
            return True, "incremental"
        # TTL check: even if range looks complete, refresh tail periodically
        if latest_fetch is None:
            return True, "incremental"
        age = datetime.now(timezone.utc).replace(tzinfo=None) - latest_fetch
        if age > timedelta(hours=self.ttl_hours):
            return True, "incremental"
        return False, "hit"

    def _fetch_start(self, max_local: str | None, window_start: date,
                     force_refresh: bool) -> date:
        if force_refresh or max_local is None:
            return window_start
        # incremental from last known day, minus 2 cal-days to repair late prints
        return max(window_start, date.fromisoformat(max_local) - timedelta(days=2))

    def _empty(self, code: str) -> NavSeries:
        import pandas as pd
        frame = pd.DataFrame(
            columns=["unit_nav", "acc_nav", "daily_return"],
            index=pd.DatetimeIndex([], name="trade_date"),
        )
        return NavSeries(code=code, frame=frame)
