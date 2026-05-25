"""Stock-side fetchers via akshare.

- fund_top_holdings(fund_code): 前十大重仓股（天天基金 F10）
- stock_daily(stock_code, start, end): A 股日 K (qfq)，东方财富
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from datetime import date
from typing import Any

import akshare as ak


@contextmanager
def _no_proxy():
    """akshare/requests 默认 trust_env=True，会读 macOS 系统代理（如 7790）。
    push2his.eastmoney.com 走代理常会被拒，临时关掉系统代理变量。"""
    saved = {}
    keys = ("http_proxy", "https_proxy", "all_proxy",
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")
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

log = logging.getLogger(__name__)


# ETF 联接基金 / 通过指数穿透的备用映射:
# 当 fund_portfolio_hold_em 返回 0 行（典型为 ETF 联接基金），改用其跟踪指数的前 10 大成份股。
INDEX_PROXY_MAP: dict[str, dict[str, str]] = {
    "010524": {"index_code": "931079", "index_name": "中证5G通信主题指数"},
}


def fetch_index_top_holdings(fund_code: str, index_code: str, index_name: str) -> list[dict[str, Any]]:
    """用指数成份股权重前 10 名作为 ETF / ETF 联接基金的穿透代理."""
    with _no_proxy():
        df = ak.index_stock_cons_weight_csindex(symbol=index_code)
    if df is None or df.empty:
        return []
    df = df.copy()
    df["权重"] = df["权重"].astype(float)
    df = df.sort_values("权重", ascending=False).head(10).reset_index(drop=True)
    season = f"指数代理: {index_name} ({index_code})"
    out: list[dict[str, Any]] = []
    for i, r in df.iterrows():
        out.append({
            "fund_code": fund_code,
            "season": season,
            "rank": i + 1,
            "stock_code": str(r["成分券代码"]).zfill(6),
            "stock_name": str(r["成分券名称"]),
            "pct_nav": float(r["权重"]),
            "shares": None,
            "market_value": None,
        })
    return out


def fetch_fund_top_holdings(fund_code: str, year: str | None = None) -> list[dict[str, Any]]:
    """返回最近一期的前十大重仓股 (季度字段保留原始 '2026年1季度股票投资明细')."""
    year = year or str(date.today().year)
    with _no_proxy():
        df = ak.fund_portfolio_hold_em(symbol=fund_code, date=year)
    out: list[dict[str, Any]] = []
    if df is not None and not df.empty:
        if "季度" in df.columns:
            latest_season = df["季度"].iloc[0]
            df = df[df["季度"] == latest_season]
        for _, r in df.iterrows():
            out.append({
                "fund_code": fund_code,
                "season": str(r.get("季度", "")),
                "rank": int(r.get("序号", 0) or 0),
                "stock_code": str(r.get("股票代码", "")).zfill(6),
                "stock_name": str(r.get("股票名称", "")),
                "pct_nav": float(r.get("占净值比例") or 0) or None,
                "shares": float(r.get("持股数") or 0) or None,
                "market_value": float(r.get("持仓市值") or 0) or None,
            })

    # 走指数代理（ETF / ETF 联接基金）
    if not out:
        proxy = INDEX_PROXY_MAP.get(fund_code)
        if proxy:
            try:
                out = fetch_index_top_holdings(fund_code,
                                               proxy["index_code"], proxy["index_name"])
                log.info("holdings index-proxy fund=%s index=%s rows=%d",
                         fund_code, proxy["index_code"], len(out))
            except Exception as e:
                log.warning("holdings index-proxy failed fund=%s: %s", fund_code, e)
    return out


def _market_prefix(stock_code: str) -> str:
    """"sh" for 6/9，"sz" for 0/2/3，"bj" for 4/8。"""
    head = stock_code[:1]
    if head in ("6", "9"):
        return "sh"
    if head in ("4", "8"):
        return "bj"
    return "sz"


def _normalize_em(df, stock_code):
    out = []
    for _, r in df.iterrows():
        out.append({
            "stock_code": stock_code,
            "trade_date": str(r["日期"]),
            "open":   float(r.get("开盘") or 0) or None,
            "close":  float(r.get("收盘") or 0) or None,
            "high":   float(r.get("最高") or 0) or None,
            "low":    float(r.get("最低") or 0) or None,
            "volume": float(r.get("成交量") or 0) or None,
            "amount": float(r.get("成交额") or 0) or None,
            "pct_change": float(r.get("涨跌幅") or 0) or None,
        })
    return out


def _normalize_sina(df, stock_code):
    out = []
    for _, r in df.iterrows():
        close = float(r.get("close") or 0) or None
        open_ = float(r.get("open") or 0) or None
        high = float(r.get("high") or 0) or None
        low = float(r.get("low") or 0) or None
        out.append({
            "stock_code": stock_code,
            "trade_date": str(r["date"]),
            "open": open_, "close": close, "high": high, "low": low,
            "volume": float(r.get("volume") or 0) or None,
            "amount": float(r.get("amount") or 0) or None,
            "pct_change": None,
        })
    # 补 pct_change（基于前一日 close）
    prev = None
    for row in out:
        if prev is not None and row["close"] and prev:
            row["pct_change"] = (row["close"] - prev) / prev * 100
        if row["close"]:
            prev = row["close"]
    return out


def fetch_stock_daily(
    stock_code: str,
    *,
    start: date,
    end: date,
    adjust: str = "qfq",
) -> list[dict[str, Any]]:
    start_s = start.strftime("%Y%m%d")
    end_s = end.strftime("%Y%m%d")
    # 主：新浪（稳定，无频次限制）
    sym = f"{_market_prefix(stock_code)}{stock_code}"
    try:
        with _no_proxy():
            df = ak.stock_zh_a_daily(
                symbol=sym, start_date=start_s, end_date=end_s, adjust=adjust,
            )
        if df is not None and not df.empty:
            df = df[(df["date"].astype(str) >= start.isoformat()) &
                    (df["date"].astype(str) <= end.isoformat())]
            return _normalize_sina(df, stock_code)
    except Exception as e:
        log.warning("sina hist failed code=%s sym=%s: %s", stock_code, sym, str(e)[:120])

    # 备：东方财富（有频控）
    try:
        with _no_proxy():
            df = ak.stock_zh_a_hist(
                symbol=stock_code, period="daily",
                start_date=start_s, end_date=end_s, adjust=adjust,
            )
        if df is not None and not df.empty:
            return _normalize_em(df, stock_code)
    except Exception as e:
        log.warning("em hist failed code=%s: %s", stock_code, str(e)[:120])
    return []
