"""News fetchers (akshare + direct API). Returns plain dicts; no DB / sentiment here.

Sources actually wired:
  - 财联社电报           ak.stock_info_global_cls(symbol="全部"/"重点")
  - 东方财富全球财经     ak.stock_info_global_em()
  - 富途资讯             ak.stock_info_global_futu()
  - 同花顺资讯           ak.stock_info_global_ths()
  - 新浪财经             ak.stock_info_global_sina()
  - 财新主要新闻         ak.stock_news_main_cx()
  - 新闻联播文字稿       ak.news_cctv(date=...)
  - 华尔街见闻 7x24      api-prod.wallstreetcn.com (直连)
  - 美股三大指数日线     ak.index_us_stock_sina(symbol=...)

Sources requested but unavailable (no public/stable endpoint):
  - 科技第一线 / 科技圈 / 风向旗参考快讯：财联社订阅栏目，公开接口无该字段
  - 联合早报：站点 OSS referer policy 拒绝
  - 金十数据：官方 flash-api 502，多镜像反爬
  - 财经慢报：未找到公开 endpoint
  这些来源里大量内容会经财联社/东财/华尔街见闻同步转发，已通过上述源覆盖。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import akshare as ak
import requests

log = logging.getLogger(__name__)

TZ = ZoneInfo("Asia/Shanghai")
HTTP_TIMEOUT = 8
HEADERS = {"User-Agent": "Mozilla/5.0 (fund-helper)"}


def _today_str() -> str:
    return datetime.now(TZ).strftime("%Y%m%d")


def _to_ts(date_str: str, time_str: str | None = None) -> str:
    if time_str:
        return f"{date_str} {time_str}".strip()
    return date_str


def _row(*, source: str, title: str, content: str, published_at: str, url: str | None = None) -> dict[str, Any]:
    return {
        "source": source,
        "title": (title or "").strip(),
        "content": (content or "").strip(),
        "published_at": published_at,
        "url": url,
    }


# -------- akshare 源 ----------------------------------------------------

def fetch_cls_telegraph(symbol: str = "全部") -> list[dict[str, Any]]:
    df = ak.stock_info_global_cls(symbol=symbol)
    out: list[dict[str, Any]] = []
    for _, r in df.iterrows():
        d = str(r.get("发布日期", "")).strip()
        t = str(r.get("发布时间", "")).strip()
        out.append(_row(
            source="财联社",
            title=str(r.get("标题", "")),
            content=str(r.get("内容", "")),
            published_at=_to_ts(d, t),
        ))
    return out


def fetch_em_global() -> list[dict[str, Any]]:
    df = ak.stock_info_global_em()
    out: list[dict[str, Any]] = []
    for _, r in df.iterrows():
        out.append(_row(
            source="东方财富",
            title=str(r.get("标题", "")),
            content=str(r.get("摘要", "")),
            published_at=str(r.get("发布时间", "")).strip(),
            url=str(r.get("链接", "")) or None,
        ))
    return out


def fetch_futu_global() -> list[dict[str, Any]]:
    df = ak.stock_info_global_futu()
    out: list[dict[str, Any]] = []
    for _, r in df.iterrows():
        out.append(_row(
            source="富途资讯",
            title=str(r.get("标题", "")),
            content=str(r.get("内容", "")),
            published_at=str(r.get("发布时间", "")).strip(),
            url=str(r.get("链接", "")) or None,
        ))
    return out


def fetch_ths_global() -> list[dict[str, Any]]:
    df = ak.stock_info_global_ths()
    out: list[dict[str, Any]] = []
    for _, r in df.iterrows():
        out.append(_row(
            source="同花顺",
            title=str(r.get("标题", "")),
            content=str(r.get("内容", "")),
            published_at=str(r.get("发布时间", "")).strip(),
            url=str(r.get("链接", "")) or None,
        ))
    return out


def fetch_sina_global() -> list[dict[str, Any]]:
    df = ak.stock_info_global_sina()
    out: list[dict[str, Any]] = []
    for _, r in df.iterrows():
        ts = str(r.get("时间", "")).strip()
        content = str(r.get("内容", "")).strip()
        title = content.split("。")[0][:60] if content else ""
        out.append(_row(
            source="新浪财经",
            title=title,
            content=content,
            published_at=ts,
        ))
    return out


def fetch_cctv(date: str | None = None) -> list[dict[str, Any]]:
    d = date or _today_str()
    df = ak.news_cctv(date=d)
    out: list[dict[str, Any]] = []
    for _, r in df.iterrows():
        out.append(_row(
            source="新闻联播",
            title=str(r.get("title", "")),
            content=str(r.get("content", "")),
            published_at=str(r.get("date", d)),
        ))
    return out


def fetch_cx_main() -> list[dict[str, Any]]:
    df = ak.stock_news_main_cx()
    out: list[dict[str, Any]] = []
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    for _, r in df.iterrows():
        tag = str(r.get("tag", "")).strip()
        summary = str(r.get("summary", "")).strip()
        url = str(r.get("url", "")) or None
        out.append(_row(
            source="财新",
            title=(tag + " " + summary[:40]).strip() if tag else summary[:60],
            content=summary,
            published_at=now,
            url=url,
        ))
    return out


# -------- 华尔街见闻 7x24（直连） ---------------------------------------

WSCN_URL = "https://api-prod.wallstreetcn.com/apiv1/content/lives?channel=global-channel&limit=50"


def fetch_wallstreetcn_lives() -> list[dict[str, Any]]:
    r = requests.get(
        WSCN_URL,
        timeout=HTTP_TIMEOUT,
        headers={**HEADERS, "Referer": "https://wallstreetcn.com/"},
    )
    r.raise_for_status()
    items = (r.json().get("data") or {}).get("items") or []
    out: list[dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        ts = it.get("display_time")
        try:
            published = datetime.fromtimestamp(int(ts), TZ).strftime("%Y-%m-%d %H:%M:%S") if ts else ""
        except Exception:
            published = ""
        content = (it.get("content_text") or it.get("content") or "").strip()
        title = (it.get("title") or "").strip() or content.split("。")[0][:60]
        article = it.get("article") or {}
        url = article.get("uri") if isinstance(article, dict) else None
        out.append(_row(
            source="华尔街见闻",
            title=title,
            content=content,
            published_at=published,
            url=url,
        ))
    return out


# -------- 美股动态 -------------------------------------------------------

US_INDICES = [
    (".DJI", "道琼斯"),
    (".IXIC", "纳斯达克"),
    (".INX", "标普500"),
]


def fetch_us_index_overnight() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    for sym, name in US_INDICES:
        try:
            df = ak.index_us_stock_sina(symbol=sym)
            if df is None or len(df) < 2:
                continue
            df = df.sort_values("date")
            last = df.iloc[-1]
            prev = df.iloc[-2]
            close = float(last["close"])
            prev_close = float(prev["close"])
            pct = (close - prev_close) / prev_close * 100 if prev_close else 0.0
            arrow = "↑" if pct >= 0 else "↓"
            title = f"{name} {close:,.2f} {arrow}{pct:+.2f}%"
            content = (
                f"{name}（{sym}）最新交易日 {last['date']}：开 {float(last['open']):,.2f}，"
                f"高 {float(last['high']):,.2f}，低 {float(last['low']):,.2f}，"
                f"收 {close:,.2f}（前收 {prev_close:,.2f}，涨跌 {pct:+.2f}%）。"
            )
            out.append(_row(
                source="新浪财经",
                title=title,
                content=content,
                published_at=str(last["date"]),
            ))
        except Exception as e:  # pragma: no cover
            out.append(_row(
                source="新浪财经",
                title=f"{name} 抓取失败",
                content=f"{type(e).__name__}: {e}",
                published_at=now,
            ))
    return out
