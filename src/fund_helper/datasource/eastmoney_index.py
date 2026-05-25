"""Index adapter for A-share market data.

Primary source: Sina (via `akshare.stock_zh_index_spot_sina`)
   - Single batch call returns ~560 indices in ~1s.
   - Independent IP pool from Eastmoney, no rate-limit issues observed.

Fallback source: efinance (Eastmoney)
   - `ef.stock.get_realtime_quotes(['沪深系列指数'])`, ~1k rows in ~3s.
   - Triggers Eastmoney rate-limit when polled too often.

Strategy: try Sina first; if it raises, fall back to efinance.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


@dataclass(slots=True)
class IndexTrendPoint:
    ts: str
    open: float
    close: float
    high: float
    low: float
    volume: float
    amount: float
    avg: float


@dataclass(slots=True)
class IndexQuote:
    secid: str
    name: str
    market: str
    pre_close: float | None
    now: float | None
    delta: float | None
    pct: float | None
    high: float | None
    low: float | None
    last_ts: str | None
    trade_date: str | None
    trends: list[IndexTrendPoint] = field(default_factory=list)


def _safe(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    import math
    if math.isnan(f):
        return None
    return f


def _secid_to_sina(secid: str) -> str:
    """'1.000001' -> 'sh000001'; '0.399001' -> 'sz399001'."""
    market_id, code = secid.split(".", 1)
    prefix = "sh" if market_id == "1" else "sz"
    return f"{prefix}{code}"


class EastmoneyIndexSource:
    name = "sina+efinance"

    # ------------------------------------------------------------- public
    def fetch_a_share_indices(self, targets: list[tuple[str, str, str]]
                              ) -> dict[str, IndexQuote]:
        """Return {secid: IndexQuote}. Tries Sina then efinance."""
        try:
            res = self._fetch_via_sina(targets)
            if res:
                return res
            log.warning("sina returned empty; falling back to efinance")
        except Exception as e:  # noqa: BLE001
            log.warning("sina index fetch failed: %s; falling back to efinance", e)

        try:
            return self._fetch_via_efinance(targets)
        except Exception as e:  # noqa: BLE001
            log.warning("efinance index fetch failed: %s", e)
            return {}

    # ------------------------------------------------------------- sina
    def _fetch_via_sina(self, targets: list[tuple[str, str, str]]
                        ) -> dict[str, IndexQuote]:
        import akshare as ak
        df = ak.stock_zh_index_spot_sina()
        if df is None or df.empty:
            return {}
        results: dict[str, IndexQuote] = {}
        for secid, display, market in targets:
            sina_code = _secid_to_sina(secid)
            row = df[df['代码'] == sina_code]
            if row.empty:
                continue
            r = row.iloc[0]
            now   = _safe(r.get('最新价'))
            pre   = _safe(r.get('昨收'))
            delta = _safe(r.get('涨跌额'))
            pct_v = _safe(r.get('涨跌幅'))      # already in percent
            pct   = pct_v / 100.0 if pct_v is not None else None
            high  = _safe(r.get('最高'))
            low   = _safe(r.get('最低'))
            results[secid] = IndexQuote(
                secid=secid,
                name=display or str(r.get('名称') or ''),
                market=market,
                pre_close=pre, now=now, delta=delta, pct=pct,
                high=high, low=low,
                last_ts=None, trade_date=None,
            )
        return results

    # ------------------------------------------------------------- efinance
    def _fetch_via_efinance(self, targets: list[tuple[str, str, str]]
                            ) -> dict[str, IndexQuote]:
        import efinance as ef
        df = ef.stock.get_realtime_quotes(['沪深系列指数'])
        df['股票代码'] = df['股票代码'].astype(str).str.zfill(6)
        results: dict[str, IndexQuote] = {}
        for secid, display, market in targets:
            code = secid.split(".", 1)[-1]
            row = df[df['股票代码'] == code]
            if row.empty:
                continue
            r = row.iloc[0]
            pre   = _safe(r.get('昨日收盘'))
            now   = _safe(r.get('最新价'))
            delta = _safe(r.get('涨跌额'))
            pct_v = _safe(r.get('涨跌幅'))
            pct   = pct_v / 100.0 if pct_v is not None else None
            high  = _safe(r.get('最高'))
            low   = _safe(r.get('最低'))
            results[secid] = IndexQuote(
                secid=secid,
                name=display or str(r.get('股票名称') or ''),
                market=market,
                pre_close=pre, now=now, delta=delta, pct=pct,
                high=high, low=low,
                last_ts=str(r.get('更新时间')   or '') or None,
                trade_date=str(r.get('最新交易日') or '') or None,
            )
        return results
