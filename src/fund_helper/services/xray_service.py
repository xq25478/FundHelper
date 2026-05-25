"""持仓穿透 service：基金 -> 前十大重仓股 -> 个股近 180 天日 K.

缓存:
  - fund_top_holding: 每只基金 TTL 24h, miss 时调天天基金 F10
  - stock_daily: 每只股票 TTL 24h，miss 或 newest < end-2d 时增量拉取
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from ..config import AppConfig
from ..datasource import stock_akshare as src
from ..storage import connect
from ..storage.retention import prune_daily_tables

log = logging.getLogger(__name__)
TZ = ZoneInfo("Asia/Shanghai")

HOLDING_TTL_HOURS = 24
STOCK_TTL_HOURS = 24


@dataclass(slots=True)
class TopHolding:
    fund_code: str
    season: str
    rank: int
    stock_code: str
    stock_name: str
    pct_nav: float | None
    shares: float | None
    market_value: float | None


@dataclass(slots=True)
class StockSeries:
    stock_code: str
    stock_name: str
    frame: pd.DataFrame  # cols: trade_date, close, pct_change


class XrayService:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.conn = connect(cfg.data_dir / "fund.db")

    # ---------------- top holdings ----------------
    def get_top_holdings(self, fund_code: str, *, force_refresh: bool = False) -> list[TopHolding]:
        latest_fetch = self._latest_holding_fetch(fund_code)
        fresh = latest_fetch is not None and (datetime.now(TZ) - latest_fetch).total_seconds() < HOLDING_TTL_HOURS * 3600
        if force_refresh or not fresh:
            try:
                rows = src.fetch_fund_top_holdings(fund_code)
                if rows:
                    self._upsert_holdings(rows)
                    log.info("xray holdings fetched fund=%s rows=%d", fund_code, len(rows))
            except Exception as e:
                log.warning("xray holdings fetch failed fund=%s: %s", fund_code, e)
        return self._load_holdings(fund_code)

    # ---------------- stock daily -----------------
    def get_stock_daily(self, stock_code: str, *, lookback_days: int = 200,
                        force_refresh: bool = False) -> StockSeries:
        end = date.today()
        start = end - timedelta(days=lookback_days)
        max_local = self._max_stock_date(stock_code)
        latest_fetch = self._latest_stock_fetch(stock_code)
        need_remote = force_refresh or max_local is None
        if not need_remote and max_local is not None:
            stale = (end - max_local).days >= 2
            ttl_ok = latest_fetch is not None and (datetime.now(TZ) - latest_fetch).total_seconds() < STOCK_TTL_HOURS * 3600
            if stale and not ttl_ok:
                need_remote = True

        if need_remote:
            fetch_start = (max_local + timedelta(days=1)) if (max_local and not force_refresh) else start
            try:
                rows = src.fetch_stock_daily(stock_code, start=fetch_start, end=end)
                if rows:
                    self._upsert_stock_daily(rows)
                    log.info("xray stock fetched code=%s rows=%d window=%s..%s",
                             stock_code, len(rows), fetch_start, end)
                self._log_stock_fetch(stock_code, fetch_start, end, len(rows), "ok")
            except Exception as e:
                log.warning("xray stock fetch failed code=%s: %s", stock_code, e)
                self._log_stock_fetch(stock_code, fetch_start, end, 0, "error", str(e))

        return self._load_stock_series(stock_code, start, end)

    # ---------------- sqlite helpers ---------------
    def _latest_holding_fetch(self, fund_code: str) -> datetime | None:
        row = self.conn.execute(
            "SELECT MAX(fetched_at) FROM fund_top_holding WHERE fund_code=?",
            (fund_code,),
        ).fetchone()
        return _parse_iso(row[0]) if row and row[0] else None

    def _upsert_holdings(self, rows: list[dict]) -> None:
        now = datetime.now(TZ).isoformat(timespec="seconds")
        data = [
            (r["fund_code"], r["season"], r["rank"], r["stock_code"], r["stock_name"],
             r.get("pct_nav"), r.get("shares"), r.get("market_value"), now)
            for r in rows
        ]
        self.conn.execute("BEGIN")
        try:
            self.conn.executemany(
                """INSERT INTO fund_top_holding(fund_code,season,rank,stock_code,stock_name,
                                                 pct_nav,shares,market_value,fetched_at)
                   VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(fund_code,season,rank) DO UPDATE SET
                     stock_code=excluded.stock_code,
                     stock_name=excluded.stock_name,
                     pct_nav=excluded.pct_nav,
                     shares=excluded.shares,
                     market_value=excluded.market_value,
                     fetched_at=excluded.fetched_at""",
                data,
            )
            # 也写一份 stock_meta
            self.conn.executemany(
                """INSERT INTO stock_meta(stock_code,stock_name,market,updated_at)
                   VALUES(?,?,NULL,?)
                   ON CONFLICT(stock_code) DO UPDATE SET
                     stock_name=excluded.stock_name,
                     updated_at=excluded.updated_at""",
                [(r["stock_code"], r["stock_name"], now) for r in rows],
            )
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def _load_holdings(self, fund_code: str) -> list[TopHolding]:
        cur = self.conn.execute(
            """SELECT fund_code,season,rank,stock_code,stock_name,pct_nav,shares,market_value
               FROM fund_top_holding
               WHERE fund_code=?
               ORDER BY season DESC, rank ASC""",
            (fund_code,),
        )
        out: list[TopHolding] = []
        latest_season: str | None = None
        for row in cur.fetchall():
            if latest_season is None:
                latest_season = row[1]
            if row[1] != latest_season:
                break
            out.append(TopHolding(
                fund_code=row[0], season=row[1], rank=int(row[2]),
                stock_code=row[3], stock_name=row[4],
                pct_nav=row[5], shares=row[6], market_value=row[7],
            ))
        return out

    def _max_stock_date(self, stock_code: str) -> date | None:
        row = self.conn.execute(
            "SELECT MAX(trade_date) FROM stock_daily WHERE stock_code=?",
            (stock_code,),
        ).fetchone()
        if not row or not row[0]:
            return None
        try:
            return date.fromisoformat(row[0])
        except ValueError:
            return None

    def _latest_stock_fetch(self, stock_code: str) -> datetime | None:
        row = self.conn.execute(
            "SELECT MAX(fetched_at) FROM stock_daily WHERE stock_code=?",
            (stock_code,),
        ).fetchone()
        return _parse_iso(row[0]) if row and row[0] else None

    def _upsert_stock_daily(self, rows: list[dict]) -> None:
        now = datetime.now(TZ).isoformat(timespec="seconds")
        data = [
            (r["stock_code"], r["trade_date"], r["open"], r["close"], r["high"], r["low"],
             r["volume"], r["amount"], r["pct_change"], now)
            for r in rows
        ]
        self.conn.execute("BEGIN")
        try:
            self.conn.executemany(
                """INSERT INTO stock_daily(stock_code,trade_date,open,close,high,low,
                                            volume,amount,pct_change,fetched_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(stock_code,trade_date) DO UPDATE SET
                     open=excluded.open, close=excluded.close,
                     high=excluded.high, low=excluded.low,
                     volume=excluded.volume, amount=excluded.amount,
                     pct_change=excluded.pct_change, fetched_at=excluded.fetched_at""",
                data,
            )
            prune_daily_tables(self.conn)
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def _log_stock_fetch(self, stock_code: str, start: date, end: date,
                         rows: int, status: str, message: str | None = None) -> None:
        now = datetime.now(TZ).isoformat(timespec="seconds")
        self.conn.execute(
            """INSERT INTO stock_fetch_log(stock_code,window_start,window_end,rows_returned,
                                            source,status,message,fetched_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (stock_code, start.isoformat(), end.isoformat(), rows, "akshare_em", status, message, now),
        )

    def _load_stock_series(self, stock_code: str, start: date, end: date) -> StockSeries:
        cur = self.conn.execute(
            """SELECT trade_date,close,pct_change FROM stock_daily
               WHERE stock_code=? AND trade_date BETWEEN ? AND ?
               ORDER BY trade_date ASC""",
            (stock_code, start.isoformat(), end.isoformat()),
        )
        rows = cur.fetchall()
        name_row = self.conn.execute(
            "SELECT stock_name FROM stock_meta WHERE stock_code=?", (stock_code,)
        ).fetchone()
        name = name_row[0] if name_row else stock_code
        frame = pd.DataFrame(rows, columns=["trade_date", "close", "pct_change"])
        return StockSeries(stock_code=stock_code, stock_name=name, frame=frame)


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt
    except ValueError:
        return None
