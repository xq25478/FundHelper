"""Market-wide margin financing snapshots from public exchange data."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ..config import AppConfig
from ..storage import connect
from .market_flow_service import _quiet_fetch_env

log = logging.getLogger(__name__)
CST = timezone(timedelta(hours=8))
TTL_SECONDS = 12 * 3600


@dataclass(slots=True)
class MarginRow:
    scope: str
    trade_date: str | None
    financing_buy: float | None
    financing_balance: float | None
    securities_sell_volume: float | None
    securities_balance: float | None
    margin_balance: float | None
    source: str
    fetched_at: str
    raw: dict[str, Any]


@dataclass(slots=True)
class MarginPanel:
    rows: list[MarginRow]
    refreshed_at: str
    cached: bool
    source_used: str
    errors: list[str]


class MarginService:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.conn = connect(cfg.data_dir / "fund.db")

    def get_panel(self, *, force_refresh: bool = False) -> MarginPanel:
        last = self._last_fetched_at()
        fresh = last is not None and (_now() - last).total_seconds() <= TTL_SECONDS
        cached = fresh and not force_refresh
        errors: list[str] = []
        source_used = "sqlite"
        if not cached:
            rows, errors = self._fetch()
            if rows:
                self._upsert(rows)
                last = _now()
                source_used = "akshare_exchange_margin"
            elif last is None:
                source_used = "unavailable"
        return MarginPanel(
            rows=self._load(),
            refreshed_at=_fmt(last) if last else "--",
            cached=cached,
            source_used=source_used,
            errors=errors,
        )

    def _fetch(self) -> tuple[list[MarginRow], list[str]]:
        import akshare as ak

        rows: list[MarginRow] = []
        errors: list[str] = []
        funcs = (
            ("上交所", "sse_margin", ak.macro_china_market_margin_sh),
            ("深交所", "szse_margin", ak.macro_china_market_margin_sz),
        )
        for scope, source, fn in funcs:
            try:
                with _quiet_fetch_env():
                    df = fn()
                if df is None or df.empty:
                    continue
                r = df.sort_values("日期").iloc[-1]
                rows.append(MarginRow(
                    scope=scope,
                    trade_date=str(r.get("日期") or ""),
                    financing_buy=_float(r.get("融资买入额")),
                    financing_balance=_float(r.get("融资余额")),
                    securities_sell_volume=_float(r.get("融券卖出量")),
                    securities_balance=_first_float(r, ("融券余额", "融券余量金额")),
                    margin_balance=_float(r.get("融资融券余额")),
                    source=source,
                    fetched_at=_now().isoformat(timespec="seconds"),
                    raw={k: _jsonable(v) for k, v in r.to_dict().items()},
                ))
            except Exception as e:  # noqa: BLE001
                errors.append(f"{scope}: {e}")
                log.warning("margin fetch failed scope=%s: %s", scope, e)
        return rows, errors

    def _last_fetched_at(self) -> datetime | None:
        row = self.conn.execute("SELECT MAX(fetched_at) FROM market_margin_snapshot").fetchone()
        if not row or not row[0]:
            return None
        try:
            dt = datetime.fromisoformat(row[0])
            return dt if dt.tzinfo else dt.replace(tzinfo=CST)
        except ValueError:
            return None

    def _upsert(self, rows: list[MarginRow]) -> None:
        self.conn.executemany(
            """
            INSERT INTO market_margin_snapshot
              (scope,trade_date,financing_buy,financing_balance,securities_sell_volume,
               securities_balance,margin_balance,source,fetched_at,raw_json)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(scope,source) DO UPDATE SET
              trade_date=excluded.trade_date,
              financing_buy=excluded.financing_buy,
              financing_balance=excluded.financing_balance,
              securities_sell_volume=excluded.securities_sell_volume,
              securities_balance=excluded.securities_balance,
              margin_balance=excluded.margin_balance,
              fetched_at=excluded.fetched_at,
              raw_json=excluded.raw_json
            """,
            [
                (
                    r.scope, r.trade_date, r.financing_buy, r.financing_balance,
                    r.securities_sell_volume, r.securities_balance, r.margin_balance,
                    r.source, r.fetched_at, json.dumps(r.raw, ensure_ascii=False, default=str),
                )
                for r in rows
            ],
        )

    def _load(self) -> list[MarginRow]:
        rows = self.conn.execute(
            """SELECT scope,trade_date,financing_buy,financing_balance,securities_sell_volume,
                      securities_balance,margin_balance,source,fetched_at,raw_json
               FROM market_margin_snapshot ORDER BY scope"""
        ).fetchall()
        out: list[MarginRow] = []
        for row in rows:
            try:
                raw = json.loads(row[9] or "{}")
            except json.JSONDecodeError:
                raw = {}
            out.append(MarginRow(
                scope=row[0],
                trade_date=row[1],
                financing_buy=row[2],
                financing_balance=row[3],
                securities_sell_volume=row[4],
                securities_balance=row[5],
                margin_balance=row[6],
                source=row[7],
                fetched_at=row[8],
                raw=raw,
            ))
        return out


def render_margin_markdown(panel: MarginPanel) -> str:
    lines = [
        "### 融资融券（交易所公开汇总）",
        f"- 最近刷新：{panel.refreshed_at}；来源：{panel.source_used}",
        "- 使用边界：交易所两融通常滞后一日披露，适合判断杠杆资金背景，不代表盘中实时主力动向。",
    ]
    if panel.errors:
        lines.append("- 抓取异常：" + "；".join(panel.errors[:3]))
    if not panel.rows:
        lines.append("- 暂无可用两融缓存。")
        return "\n".join(lines)
    lines.append("| 市场 | 日期 | 融资买入额 | 融资余额 | 融券余额 | 两融余额 |")
    lines.append("|---|---|---:|---:|---:|---:|")
    for row in panel.rows:
        lines.append(
            f"| {row.scope} | {row.trade_date or '--'} | {_fmt_yuan_yi(row.financing_buy)} | "
            f"{_fmt_yuan_yi(row.financing_balance)} | {_fmt_yuan_yi(row.securities_balance)} | "
            f"{_fmt_yuan_yi(row.margin_balance)} |"
        )
    return "\n".join(lines)


def _now() -> datetime:
    return datetime.now(CST)


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _float(v) -> float | None:
    try:
        import math

        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _first_float(row, keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _float(row.get(key))
        if value is not None:
            return value
    return None


def _jsonable(v):
    try:
        if hasattr(v, "item"):
            return v.item()
    except Exception:
        pass
    return v


def _fmt_yuan_yi(v) -> str:
    if v is None:
        return "--"
    try:
        return f"{float(v) / 100_000_000:+.2f}亿"
    except Exception:
        return str(v)
