"""Market index service: one-shot batch fetch via efinance + sqlite cache.

A 股 panel is fetched in a single efinance call (~3-4s), then filtered to
the 5 target indices. Result is persisted to sqlite and a process-local
cache stamp tracks TTL.

TTL:
- Asia/Shanghai trading session (Mon-Fri 09:30-11:30 / 13:00-15:00): 60s
- Off-session: 12h (so we still see the post-close prints once)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..config import AppConfig
from ..datasource.eastmoney_index import EastmoneyIndexSource, IndexQuote
from ..storage import connect

log = logging.getLogger(__name__)

A_SHARE_INDEXES: list[tuple[str, str, str]] = [
    ("1.000001", "上证指数",  "A"),
    ("0.399001", "深证成指",  "A"),
    ("1.000300", "沪深300",   "A"),
    ("0.399006", "创业板指",  "A"),
    ("1.000688", "科创50",    "A"),
]

CST = timezone(timedelta(hours=8))
SESSION_TTL_SECONDS     = 60
OFF_SESSION_TTL_SECONDS = 12 * 3600

# process-local cache so concurrent web requests share the same batch
_BATCH_CACHE: dict[str, datetime] = {}    # key=panel_id, value=fetched_at_cst
_PANEL_ID = "a_share"


def _now_cst() -> datetime:
    return datetime.now(CST)


def _is_a_session(now: datetime | None = None) -> bool:
    now = (now or _now_cst()).astimezone(CST)
    if now.weekday() >= 5:
        return False
    m_open  = now.replace(hour=9,  minute=30, second=0, microsecond=0)
    m_close = now.replace(hour=11, minute=30, second=0, microsecond=0)
    a_open  = now.replace(hour=13, minute=0,  second=0, microsecond=0)
    a_close = now.replace(hour=15, minute=0,  second=0, microsecond=0)
    return (m_open <= now <= m_close) or (a_open <= now <= a_close)


@dataclass(slots=True)
class MarketPanel:
    a_share: list[IndexQuote]
    refreshed_at: datetime
    source_used: str   # 'efinance' | 'sqlite'
    is_session: bool


class MarketService:
    def __init__(self, cfg: AppConfig,
                 source: EastmoneyIndexSource | None = None) -> None:
        self.cfg = cfg
        self.conn = connect(cfg.data_dir / "fund.db")
        self.source = source or EastmoneyIndexSource()

    # ---------------------------------------------------------------- public
    def get_a_share_panel(self, force_refresh: bool = False) -> MarketPanel:
        ttl = SESSION_TTL_SECONDS if _is_a_session() else OFF_SESSION_TTL_SECONDS
        fetched_at = _BATCH_CACHE.get(_PANEL_ID)
        within_ttl = (
            fetched_at is not None
            and (_now_cst() - fetched_at).total_seconds() <= ttl
        )

        if force_refresh or not within_ttl:
            try:
                fresh = self.source.fetch_a_share_indices(A_SHARE_INDEXES)
            except Exception as e:  # noqa: BLE001
                log.warning("market batch fetch failed: %s", e)
                fresh = {}
            if fresh:
                self._persist(fresh)
                _BATCH_CACHE[_PANEL_ID] = _now_cst()
                a_share = [fresh[sid] for sid, _, _ in A_SHARE_INDEXES if sid in fresh]
                return MarketPanel(
                    a_share=a_share, refreshed_at=_now_cst(),
                    source_used="efinance", is_session=_is_a_session(),
                )

        # fall back to sqlite snapshot
        a_share = []
        for secid, name, market in A_SHARE_INDEXES:
            snap = self._load_snapshot(secid)
            if snap:
                a_share.append(snap)
            else:
                a_share.append(IndexQuote(
                    secid=secid, name=name, market=market,
                    pre_close=None, now=None, open=None, delta=None, pct=None,
                    high=None, low=None, volume=None, amount=None,
                    last_ts=None, trade_date=None,
                ))
        return MarketPanel(
            a_share=a_share, refreshed_at=_now_cst(),
            source_used="sqlite", is_session=_is_a_session(),
        )

    # ---------------------------------------------------------------- storage
    def _persist(self, quotes: dict[str, IndexQuote]) -> None:
        now_iso = _now_cst().replace(tzinfo=None).isoformat(timespec="seconds")
        rows = [
            (q.secid, q.name, q.market, q.trade_date,
             q.pre_close, q.now, q.open, q.delta, q.pct, q.high, q.low,
             q.volume, q.amount, q.last_ts, now_iso)
            for q in quotes.values()
        ]
        self.conn.executemany(
            """
            INSERT INTO index_snapshot
              (secid,name,market,trade_date,pre_close,now,open,delta,pct,high,low,volume,amount,last_ts,fetched_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(secid) DO UPDATE SET
              name=excluded.name, market=excluded.market, trade_date=excluded.trade_date,
              pre_close=excluded.pre_close, now=excluded.now, open=excluded.open, delta=excluded.delta,
              pct=excluded.pct, high=excluded.high, low=excluded.low,
              volume=excluded.volume, amount=excluded.amount,
              last_ts=excluded.last_ts, fetched_at=excluded.fetched_at
            """,
            rows,
        )
        self.conn.commit()

    def _load_snapshot(self, secid: str) -> IndexQuote | None:
        row = self.conn.execute(
            "SELECT secid,name,market,trade_date,pre_close,now,delta,pct,"
            "high,low,last_ts,open,volume,amount FROM index_snapshot WHERE secid=?",
            (secid,),
        ).fetchone()
        if not row or row[5] is None:
            return None
        return IndexQuote(
            secid=row[0], name=row[1], market=row[2], trade_date=row[3],
            pre_close=row[4], now=row[5], open=row[11], delta=row[6], pct=row[7],
            high=row[8], low=row[9], volume=row[12], amount=row[13], last_ts=row[10],
        )
