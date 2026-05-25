"""统一的 180 天保留清理。

被各 service 在写入完成后调用：从 sqlite 删除 trade_date / published_at 早于 today-180d 的行。
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import date, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)
TZ = ZoneInfo("Asia/Shanghai")

MAX_HISTORY_DAYS = 180


def _cutoff() -> str:
    return (date.today() - timedelta(days=MAX_HISTORY_DAYS)).isoformat()


def prune_daily_tables(conn: sqlite3.Connection) -> dict[str, int]:
    """删除 nav_daily / stock_daily / index_daily 中 trade_date < cutoff 的行."""
    cutoff = _cutoff()
    out: dict[str, int] = {}
    for table in ("nav_daily", "stock_daily", "index_daily", "sector_daily"):
        try:
            cur = conn.execute(f"DELETE FROM {table} WHERE trade_date < ?", (cutoff,))
            if cur.rowcount > 0:
                out[table] = cur.rowcount
        except sqlite3.OperationalError:
            # 表不存在（极少见，schema 已建好）
            pass
    if out:
        log.info("retention prune cutoff=%s deleted=%s", cutoff, out)
    return out


def prune_news(conn: sqlite3.Connection) -> int:
    """新闻 published_at 是任意字符串；按 fetched_at < cutoff 删除（更稳）."""
    cutoff = _cutoff()
    cur = conn.execute("DELETE FROM news_item WHERE fetched_at < ?", (cutoff,))
    n = cur.rowcount
    if n > 0:
        log.info("retention prune news cutoff=%s deleted=%d", cutoff, n)
    return n
