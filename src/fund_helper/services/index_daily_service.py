"""指数日线 service：缓存 + 增量抓取。"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from ..config import AppConfig
from ..datasource import index_daily as src
from ..storage import connect
from ..storage.retention import prune_daily_tables

log = logging.getLogger(__name__)
TZ = ZoneInfo("Asia/Shanghai")
TTL_HOURS = 24


@dataclass(slots=True)
class IndexSeries:
    secid: str
    frame: pd.DataFrame  # trade_date, open, close, high, low, volume, pct_change


class IndexDailyService:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.conn = connect(cfg.data_dir / "fund.db")

    def get_series(self, secid: str, *, lookback_days: int = 200,
                   force_refresh: bool = False) -> IndexSeries:
        end = date.today()
        start = end - timedelta(days=lookback_days)
        max_local = self._max_date(secid)
        latest_fetch = self._latest_fetch(secid)
        need_remote = force_refresh or max_local is None
        if not need_remote and max_local is not None:
            stale = (end - max_local).days >= 2
            ttl_ok = latest_fetch is not None and (datetime.now(TZ) - latest_fetch).total_seconds() < TTL_HOURS * 3600
            if stale and not ttl_ok:
                need_remote = True
        if need_remote:
            try:
                # 只抓 cutoff 之后的数据，cutoff = today - MAX_HISTORY_DAYS
                from ..storage.retention import MAX_HISTORY_DAYS
                cutoff = (date.today() - timedelta(days=MAX_HISTORY_DAYS)).isoformat()
                rows = src.fetch_index_daily(secid, since_date=cutoff)
                if rows:
                    self._upsert(rows)
                    log.info("index_daily fetched secid=%s rows=%d", secid, len(rows))
            except Exception as e:
                log.warning("index_daily fetch failed secid=%s: %s", secid, e)
        return self._load(secid, start, end)

    def _max_date(self, secid: str) -> date | None:
        row = self.conn.execute(
            "SELECT MAX(trade_date) FROM index_daily WHERE secid=?", (secid,)
        ).fetchone()
        if not row or not row[0]:
            return None
        try:
            return date.fromisoformat(row[0])
        except ValueError:
            return None

    def _latest_fetch(self, secid: str) -> datetime | None:
        row = self.conn.execute(
            "SELECT MAX(fetched_at) FROM index_daily WHERE secid=?", (secid,)
        ).fetchone()
        if not row or not row[0]:
            return None
        try:
            dt = datetime.fromisoformat(row[0])
            return dt if dt.tzinfo else dt.replace(tzinfo=TZ)
        except ValueError:
            return None

    def _upsert(self, rows: list[dict]) -> None:
        now = datetime.now(TZ).isoformat(timespec="seconds")
        data = [
            (r["secid"], r["trade_date"], r["open"], r["close"], r["high"], r["low"],
             r["volume"], r["pct_change"], now)
            for r in rows
        ]
        self.conn.execute("BEGIN")
        try:
            self.conn.executemany(
                """INSERT INTO index_daily(secid,trade_date,open,close,high,low,
                                            volume,pct_change,fetched_at)
                   VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(secid,trade_date) DO UPDATE SET
                     open=excluded.open, close=excluded.close,
                     high=excluded.high, low=excluded.low,
                     volume=excluded.volume, pct_change=excluded.pct_change,
                     fetched_at=excluded.fetched_at""",
                data,
            )
            prune_daily_tables(self.conn)
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def _load(self, secid: str, start: date, end: date) -> IndexSeries:
        cur = self.conn.execute(
            """SELECT trade_date,open,close,high,low,volume,pct_change FROM index_daily
               WHERE secid=? AND trade_date BETWEEN ? AND ?
               ORDER BY trade_date ASC""",
            (secid, start.isoformat(), end.isoformat()),
        )
        rows = cur.fetchall()
        frame = pd.DataFrame(rows, columns=["trade_date", "open", "close", "high", "low", "volume", "pct_change"])
        return IndexSeries(secid=secid, frame=frame)
