"""Public intraday fund estimate snapshots.

This service does not calculate fund changes from holdings. It only fetches and
caches the public Tiantian/Fundgz estimate payload for each fund.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..config import AppConfig
from ..storage import connect

log = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))
SESSION_TTL_SECONDS = 60
OFF_SESSION_TTL_SECONDS = 30 * 60
SOURCE = "tiantian_fundgz"


@dataclass(slots=True)
class FundRealtimeQuote:
    code: str
    name: str
    nav_date: str | None
    unit_nav: float | None
    estimate_nav: float | None
    estimate_pct: float | None  # percent units, e.g. 1.23 means +1.23%
    estimate_time: str | None
    source: str
    fetched_at: str


def _now() -> datetime:
    return datetime.now(CST)


def _is_session(now: datetime | None = None) -> bool:
    now = now or _now()
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    return (9 * 60 + 30 <= hm <= 11 * 60 + 30) or (13 * 60 <= hm <= 15 * 60)


class FundRealtimeService:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.conn = connect(cfg.data_dir / "fund.db")
        self.timeout = cfg.datasource.http.timeout

    def get_quote(self, code: str, *, force_refresh: bool = False) -> FundRealtimeQuote | None:
        cached = self._load(code)
        ttl = SESSION_TTL_SECONDS if _is_session() else OFF_SESSION_TTL_SECONDS
        fresh = cached is not None and _age_seconds(cached.fetched_at) <= ttl
        if not force_refresh and fresh:
            return cached

        try:
            raw = _fetch_fund_estimate(code, timeout=self.timeout)
        except Exception as e:  # noqa: BLE001
            log.warning("fund realtime fetch failed code=%s: %s", code, e)
            return cached
        if not raw:
            return cached
        quote = FundRealtimeQuote(
            code=raw["code"],
            name=raw.get("name") or "",
            nav_date=raw.get("nav_date"),
            unit_nav=raw.get("unit_nav"),
            estimate_nav=raw.get("estimate_nav"),
            estimate_pct=raw.get("estimate_pct"),
            estimate_time=raw.get("estimate_time"),
            source=SOURCE,
            fetched_at=_now().isoformat(timespec="seconds"),
        )
        self._upsert(quote)
        return quote

    def get_quotes(
        self,
        codes: list[str],
        *,
        force_refresh: bool = False,
    ) -> dict[str, FundRealtimeQuote]:
        out: dict[str, FundRealtimeQuote] = {}
        for code in codes:
            q = self.get_quote(code, force_refresh=force_refresh)
            if q is not None:
                out[code] = q
        return out

    def _load(self, code: str) -> FundRealtimeQuote | None:
        row = self.conn.execute(
            """SELECT code,name,nav_date,unit_nav,estimate_nav,estimate_pct,
                      estimate_time,source,fetched_at
               FROM fund_realtime_snapshot WHERE code=?""",
            (code,),
        ).fetchone()
        if not row:
            return None
        return FundRealtimeQuote(
            code=row[0],
            name=row[1] or "",
            nav_date=row[2],
            unit_nav=row[3],
            estimate_nav=row[4],
            estimate_pct=row[5],
            estimate_time=row[6],
            source=row[7],
            fetched_at=row[8],
        )

    def _upsert(self, q: FundRealtimeQuote) -> None:
        self.conn.execute(
            """INSERT INTO fund_realtime_snapshot
                 (code,name,nav_date,unit_nav,estimate_nav,estimate_pct,
                  estimate_time,source,fetched_at)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(code) DO UPDATE SET
                 name=excluded.name,
                 nav_date=excluded.nav_date,
                 unit_nav=excluded.unit_nav,
                 estimate_nav=excluded.estimate_nav,
                 estimate_pct=excluded.estimate_pct,
                 estimate_time=excluded.estimate_time,
                 source=excluded.source,
                 fetched_at=excluded.fetched_at""",
            (
                q.code, q.name, q.nav_date, q.unit_nav, q.estimate_nav,
                q.estimate_pct, q.estimate_time, q.source, q.fetched_at,
            ),
        )


def _age_seconds(value: str | None) -> float:
    if not value:
        return float("inf")
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CST)
        return (_now() - dt.astimezone(CST)).total_seconds()
    except ValueError:
        return float("inf")


def _fetch_fund_estimate(code: str, *, timeout: int) -> dict | None:
    """Import the public estimate fetcher lazily so optional failures stay local."""
    try:
        from ..datasource.tiantian import fetch_fund_estimate
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"fetch_fund_estimate import failed: {e}") from e
    return fetch_fund_estimate(code, timeout=timeout)
