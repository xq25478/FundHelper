"""A 股指数日线 (close-only) via akshare sina."""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any

import akshare as ak

log = logging.getLogger(__name__)

# secid (efinance / market_service 用的) -> sina symbol
SECID_TO_SINA: dict[str, str] = {
    "1.000001": "sh000001",  # 上证指数
    "0.399001": "sz399001",  # 深证成指
    "1.000300": "sh000300",  # 沪深300
    "0.399006": "sz399006",  # 创业板指
    "1.000688": "sh000688",  # 科创50
}


@contextmanager
def _no_proxy():
    saved = {}
    keys = ("http_proxy","https_proxy","all_proxy",
            "HTTP_PROXY","HTTPS_PROXY","ALL_PROXY")
    for k in keys:
        if k in os.environ:
            saved[k] = os.environ.pop(k)
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"
    try:
        yield
    finally:
        for k, v in saved.items():
            os.environ[k] = v
        os.environ.pop("NO_PROXY", None)
        os.environ.pop("no_proxy", None)


def fetch_index_daily(secid: str, *, since_date: str | None = None) -> list[dict[str, Any]]:
    """since_date: ISO 'YYYY-MM-DD'，仅返回 trade_date >= since_date 的行；用于减少入库数据量。"""
    sym = SECID_TO_SINA.get(secid)
    if not sym:
        log.warning("no sina mapping for secid=%s", secid)
        return []
    with _no_proxy():
        df = ak.stock_zh_index_daily(symbol=sym)
    if df is None or df.empty:
        return []
    if since_date:
        df = df[df["date"].astype(str) >= since_date]
    out: list[dict[str, Any]] = []
    prev: float | None = None
    for _, r in df.iterrows():
        close = float(r["close"]) if r.get("close") is not None else None
        pct = None
        if close is not None and prev not in (None, 0):
            pct = (close - prev) / prev * 100
        out.append({
            "secid": secid,
            "trade_date": str(r["date"]),
            "open":   float(r["open"])   if r.get("open")   is not None else None,
            "close":  close,
            "high":   float(r["high"])   if r.get("high")   is not None else None,
            "low":    float(r["low"])    if r.get("low")    is not None else None,
            "volume": float(r["volume"]) if r.get("volume") is not None else None,
            "pct_change": pct,
        })
        if close:
            prev = close
    return out
