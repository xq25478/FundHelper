"""Tiantian Fund (fund.eastmoney.com) adapter — primary fund-level source.

Public endpoints used:
- https://api.fund.eastmoney.com/f10/lsjz       NAV history (JSON, paged)
- https://fund.eastmoney.com/js/fundcode_search.js  full fund list (JSONP-ish)
- https://fundgz.1234567.com.cn/js/{code}.js    intra-day estimate (not used here)

Notes:
- lsjz REQUIRES a Referer header from fundf10.eastmoney.com; otherwise 403.
- DWJZ / LJJZ may be empty strings on non-disclosure days; we drop those.
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Any

import pandas as pd

from ..domain import Fund, FundType, NavSeries, PortfolioSnapshot
from ..utils.http import RateLimiter, make_client
from .base import FundDataSource

log = logging.getLogger(__name__)

LSJZ_URL = "https://api.fund.eastmoney.com/f10/lsjz"
LSJZ_REFERER = "http://fundf10.eastmoney.com/"
LIST_URL = "https://fund.eastmoney.com/js/fundcode_search.js"

# Eastmoney fund-type Chinese label -> our enum
_TYPE_MAP: dict[str, FundType] = {
    "股票型": FundType.EQUITY,
    "股票指数": FundType.INDEX,
    "混合型":   FundType.HYBRID,
    "债券型":   FundType.BOND,
    "货币型":   FundType.MONEY,
    "QDII":    FundType.QDII,
    "FOF":     FundType.FOF,
}


def _to_float(x: Any) -> float | None:
    if x is None or x == "" or x == "--":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


class TiantianDataSource(FundDataSource):
    name = "tiantian"

    def __init__(self, timeout: int = 10, rate_per_sec: int = 4,
                 page_size: int = 20) -> None:
        self.client = make_client(timeout=timeout)
        self.client.headers.update({"Referer": LSJZ_REFERER})
        self.limiter = RateLimiter(per_sec=rate_per_sec)
        self.page_size = page_size

    # ------------------------------------------------------------------ list
    def list_funds(self) -> list[Fund]:
        """Parse fundcode_search.js -> Fund list (code, name, type label)."""
        self.limiter.wait()
        r = self.client.get(LIST_URL)
        r.raise_for_status()
        text = r.text
        # body looks like: var r = [["000001","HXCZHH","华夏成长混合","混合型",...], ...];
        m = re.search(r"\[\[.+\]\]", text, re.DOTALL)
        if not m:
            raise RuntimeError("fundcode_search.js parsing failed")
        import json
        rows = json.loads(m.group(0))
        funds: list[Fund] = []
        for row in rows:
            if not row or len(row) < 4:
                continue
            code, _pinyin, name, type_label = row[0], row[1], row[2], row[3]
            funds.append(Fund(
                code=str(code),
                name=str(name),
                fund_type=_TYPE_MAP.get(type_label, FundType.OTHER),
                tags=[type_label] if type_label else [],
            ))
        return funds

    # ------------------------------------------------------------------ one
    def get_fund(self, code: str) -> Fund:
        """Light implementation: scan list_funds() and pick. Replace with F10 later."""
        for f in self.list_funds():
            if f.code == code:
                return f
        raise KeyError(f"fund {code} not found in tiantian listing")

    # ------------------------------------------------------------------ nav
    def get_nav(self, code: str, start: date | None = None,
                end: date | None = None) -> NavSeries:
        """Pull NAV history with paging. Returns chronological NavSeries."""
        end = end or date.today()
        start = start or (end - timedelta(days=365 * 3))

        page_index = 1
        rows: list[dict[str, Any]] = []
        while True:
            self.limiter.wait()
            params = {
                "fundCode":  code,
                "pageIndex": page_index,
                "pageSize":  self.page_size,
                "startDate": start.isoformat(),
                "endDate":   end.isoformat(),
            }
            r = self.client.get(LSJZ_URL, params=params)
            r.raise_for_status()
            payload = r.json()
            data = (payload or {}).get("Data") or {}
            lsjz = data.get("LSJZList") or []
            rows.extend(lsjz)
            # Eastmoney does not always return TotalCount; rely on short-page-as-EOF.
            if len(lsjz) < self.page_size:
                break
            page_index += 1
            if page_index > 200:  # hard safety bound
                log.warning("get_nav(%s) hit page cap", code)
                break

        if not rows:
            empty = pd.DataFrame(
                columns=["unit_nav", "acc_nav", "daily_return"],
                index=pd.DatetimeIndex([], name="trade_date"),
            )
            return NavSeries(code=code, frame=empty)

        df = pd.DataFrame(rows)
        df["trade_date"] = pd.to_datetime(df["FSRQ"])
        df["unit_nav"]     = df["DWJZ"].map(_to_float)
        df["acc_nav"]      = df["LJJZ"].map(_to_float)
        df["daily_return"] = df["JZZZL"].map(_to_float).div(100.0)
        df = (
            df[["trade_date", "unit_nav", "acc_nav", "daily_return"]]
            .dropna(subset=["unit_nav"])
            .set_index("trade_date")
            .sort_index()
        )
        return NavSeries(code=code, frame=df)

    # ------------------------------------------------------------------ holdings
    def get_holdings(self, code: str, report_date: date | None = None) -> PortfolioSnapshot:
        raise NotImplementedError
