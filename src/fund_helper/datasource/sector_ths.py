"""同花顺板块历史 K 线 + 名称映射.

新浪现货板块名 -> 同花顺历史板块名（模糊匹配）.
板块名空间不完全重合，匹配不到时返回空列表.
"""
from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from typing import Any

import akshare as ak

log = logging.getLogger(__name__)


@contextmanager
def _no_proxy():
    saved = {}
    for k in ("http_proxy","https_proxy","all_proxy","HTTP_PROXY","HTTPS_PROXY","ALL_PROXY"):
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


# 名称表缓存（进程内，TTL 24h；akshare 同花顺接口几秒，不希望反复拉）
_NAME_CACHE: dict[str, tuple[float, list[str]]] = {}
_NAME_TTL = 24 * 3600


def _names(category: str) -> list[str]:
    now = time.time()
    cached = _NAME_CACHE.get(category)
    if cached and now - cached[0] < _NAME_TTL:
        return cached[1]
    with _no_proxy():
        if category == "industry":
            df = ak.stock_board_industry_summary_ths()
            names = df["板块"].astype(str).tolist() if df is not None and not df.empty else []
        else:
            df = ak.stock_board_concept_name_ths()
            names = df["name"].astype(str).tolist() if df is not None and not df.empty else []
    _NAME_CACHE[category] = (now, names)
    return names


def resolve_ths_name(category: str, sina_name: str) -> str | None:
    """新浪板块名 -> 同花顺板块名；精确 → 去括号 → 子串。"""
    if not sina_name:
        return None
    pool = _names(category)
    if not pool:
        return None
    if sina_name in pool:
        return sina_name
    # 去括号内容
    base = sina_name.split("(")[0].split("（")[0].strip()
    if base and base in pool:
        return base
    # 子串：先看 pool 中是否有以 base 开头的
    if base:
        for n in pool:
            if n.startswith(base):
                return n
        for n in pool:
            if base in n or n in base:
                return n
    return None


def fetch_sector_hist(category: str, ths_name: str, *,
                      start_date: str, end_date: str) -> list[dict[str, Any]]:
    """start_date/end_date: 'YYYYMMDD'."""
    with _no_proxy():
        if category == "industry":
            df = ak.stock_board_industry_index_ths(
                symbol=ths_name, start_date=start_date, end_date=end_date,
            )
        else:
            df = ak.stock_board_concept_index_ths(
                symbol=ths_name, start_date=start_date, end_date=end_date,
            )
    if df is None or df.empty:
        return []
    out: list[dict[str, Any]] = []
    prev: float | None = None
    for _, r in df.iterrows():
        close = _float(r.get("收盘价"))
        pct = None
        if close is not None and prev not in (None, 0):
            pct = (close - prev) / prev * 100
        out.append({
            "trade_date": str(r.get("日期")),
            "open":   _float(r.get("开盘价")),
            "close":  close,
            "high":   _float(r.get("最高价")),
            "low":    _float(r.get("最低价")),
            "volume": _float(r.get("成交量")),
            "amount": _float(r.get("成交额")),
            "pct_change": pct,
        })
        if close:
            prev = close
    return out


def _float(v) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except (ValueError, TypeError):
        return None
