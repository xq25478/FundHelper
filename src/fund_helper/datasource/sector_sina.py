"""板块行情 — 新浪 stock_sector_spot.

可选 indicator: "新浪行业" (49) / "概念" (175) / "行业" (84) / "启明星行业" (63) / "地域"。
我们使用 "新浪行业" + "概念" 两个维度。
"""
from __future__ import annotations

import logging
import os
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


CATEGORIES = ("industry", "concept")
CATEGORY_INDICATOR = {"industry": "新浪行业", "concept": "概念"}
CATEGORY_LABEL = {"industry": "行业", "concept": "概念"}


def fetch_sector_spot(category: str) -> list[dict[str, Any]]:
    indicator = CATEGORY_INDICATOR[category]
    with _no_proxy():
        df = ak.stock_sector_spot(indicator=indicator)
    if df is None or df.empty:
        return []
    out: list[dict[str, Any]] = []
    for _, r in df.iterrows():
        out.append({
            "category":   category,
            "label":      str(r.get("label", "")),
            "name":       str(r.get("板块", "")),
            "companies":  _int(r.get("公司家数")),
            "avg_price":  _float(r.get("平均价格")),
            "delta":      _float(r.get("涨跌额")),
            "pct":        _float(r.get("涨跌幅")),
            "total_vol":  _float(r.get("总成交量")),
            "total_amt":  _float(r.get("总成交额")),
            "leader_code":  str(r.get("股票代码", "")),
            "leader_name":  str(r.get("股票名称", "")),
            "leader_pct":   _float(r.get("个股-涨跌幅")),
            "leader_price": _float(r.get("个股-当前价")),
            "leader_delta": _float(r.get("个股-涨跌额")),
        })
    return out


def _int(v) -> int | None:
    try:
        if v is None:
            return None
        return int(v)
    except (ValueError, TypeError):
        return None


def _float(v) -> float | None:
    try:
        if v is None:
            return None
        f = float(v)
        return f
    except (ValueError, TypeError):
        return None
