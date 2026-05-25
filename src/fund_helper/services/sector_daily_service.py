"""板块日线 service：缓存 + 增量抓取（同花顺）。

外部 API:
  SectorDailyService(cfg).get_series(category, label, name, lookback_days, force_refresh) -> SectorSeries
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from ..config import AppConfig
from ..datasource import sector_ths as src
from ..storage import connect
from ..storage.retention import MAX_HISTORY_DAYS

log = logging.getLogger(__name__)
TZ = ZoneInfo("Asia/Shanghai")
TTL_HOURS = 24


@dataclass(slots=True)
class SectorSeries:
    category: str
    label: str
    name: str
    ths_name: str | None
    frame: pd.DataFrame  # trade_date, close, pct_change


class SectorDailyService:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.conn = connect(cfg.data_dir / "fund.db")

    def get_series(self, category: str, label: str, name: str, *,
                   lookback_days: int = 200, force_refresh: bool = False) -> SectorSeries:
        end = date.today()
        start = end - timedelta(days=lookback_days)
        max_local = self._max_date(category, label)
        latest_fetch = self._latest_fetch(category, label)
        need_remote = force_refresh or max_local is None
        if not need_remote and max_local is not None:
            stale = (end - max_local).days >= 2
            ttl_ok = latest_fetch is not None and (datetime.now(TZ) - latest_fetch).total_seconds() < TTL_HOURS * 3600
            if stale and not ttl_ok:
                need_remote = True

        ths_name: str | None = None
        if need_remote:
            try:
                ths_name = src.resolve_ths_name(category, name)
                if not ths_name:
                    log.warning("sector_daily ths_name unresolved category=%s name=%s", category, name)
                else:
                    cutoff = (date.today() - timedelta(days=MAX_HISTORY_DAYS))
                    rows = src.fetch_sector_hist(
                        category, ths_name,
                        start_date=cutoff.strftime("%Y%m%d"),
                        end_date=end.strftime("%Y%m%d"),
                    )
                    if rows:
                        self._upsert(category, label, rows)
                        log.info("sector_daily fetched %s/%s rows=%d", category, label, len(rows))
            except Exception as e:
                log.warning("sector_daily fetch failed %s/%s: %s", category, label, e)

        return SectorSeries(
            category=category, label=label, name=name, ths_name=ths_name,
            frame=self._load(category, label, start, end),
        )

    def _max_date(self, category: str, label: str) -> date | None:
        row = self.conn.execute(
            "SELECT MAX(trade_date) FROM sector_daily WHERE category=? AND label=?",
            (category, label),
        ).fetchone()
        if not row or not row[0]:
            return None
        try:
            return date.fromisoformat(row[0])
        except ValueError:
            return None

    def _latest_fetch(self, category: str, label: str) -> datetime | None:
        row = self.conn.execute(
            "SELECT MAX(fetched_at) FROM sector_daily WHERE category=? AND label=?",
            (category, label),
        ).fetchone()
        if not row or not row[0]:
            return None
        try:
            dt = datetime.fromisoformat(row[0])
            return dt if dt.tzinfo else dt.replace(tzinfo=TZ)
        except ValueError:
            return None

    def _upsert(self, category: str, label: str, rows: list[dict]) -> None:
        now = datetime.now(TZ).isoformat(timespec="seconds")
        data = [
            (category, label, r["trade_date"], r["open"], r["close"], r["high"],
             r["low"], r["volume"], r["amount"], r["pct_change"], now)
            for r in rows if r.get("trade_date")
        ]
        self.conn.execute("BEGIN")
        try:
            self.conn.executemany(
                """INSERT INTO sector_daily
                     (category,label,trade_date,open,close,high,low,volume,amount,pct_change,fetched_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(category,label,trade_date) DO UPDATE SET
                     open=excluded.open, close=excluded.close,
                     high=excluded.high, low=excluded.low,
                     volume=excluded.volume, amount=excluded.amount,
                     pct_change=excluded.pct_change, fetched_at=excluded.fetched_at""",
                data,
            )
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def _load(self, category: str, label: str, start: date, end: date) -> pd.DataFrame:
        rows = self.conn.execute(
            """SELECT trade_date, close, pct_change FROM sector_daily
               WHERE category=? AND label=? AND trade_date>=? AND trade_date<=?
               ORDER BY trade_date ASC""",
            (category, label, start.isoformat(), end.isoformat()),
        ).fetchall()
        if not rows:
            return pd.DataFrame(columns=["trade_date", "close", "pct_change"])
        return pd.DataFrame(rows, columns=["trade_date", "close", "pct_change"])
