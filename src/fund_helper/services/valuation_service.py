"""Index valuation snapshots and historical percentiles from public sources."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from ..config import AppConfig
from ..storage import connect
from .market_flow_service import _quiet_fetch_env

log = logging.getLogger(__name__)
CST = timezone(timedelta(hours=8))
TTL_SECONDS = 24 * 3600


@dataclass(slots=True)
class ValuationRow:
    secid: str
    code: str
    name: str
    trade_date: str | None
    pe: float | None
    pb: float | None
    dividend_yield: float | None
    pe_percentile: float | None
    pb_percentile: float | None
    source: str
    fetched_at: str
    raw: dict[str, Any]


@dataclass(slots=True)
class ValuationPanel:
    rows: list[ValuationRow]
    refreshed_at: str
    cached: bool
    source_used: str
    errors: list[str]


@dataclass(frozen=True, slots=True)
class ValuationSpec:
    secid: str
    code: str
    name: str
    pe_kind: str | None = None
    pe_symbol: str | None = None
    pb_kind: str | None = None
    pb_symbol: str | None = None


VALUATION_SPECS: tuple[ValuationSpec, ...] = (
    ValuationSpec("1.000001", "000001", "上证指数", "market_pe_lg", "上证", "market_pb_lg", "上证"),
    ValuationSpec("0.399001", "399001", "深证成指", None, None, "market_pb_lg", "深证"),
    ValuationSpec("1.000300", "000300", "沪深300", "index_pe_lg", "沪深300", "index_pb_lg", "沪深300"),
    ValuationSpec("0.399006", "399006", "创业板指", "market_pe_lg", "创业板", "market_pb_lg", "创业板"),
    ValuationSpec("1.000688", "000688", "科创50", "csindex", "000688", None, None),
    ValuationSpec("931079", "931079", "中证5G通信主题指数", "csindex", "931079", None, None),
)


class ValuationService:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.conn = connect(cfg.data_dir / "fund.db")

    def get_panel(self, *, force_refresh: bool = False) -> ValuationPanel:
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
                source_used = "akshare_valuation"
            elif last is None:
                source_used = "unavailable"
        return ValuationPanel(
            rows=self._load(),
            refreshed_at=_fmt(last) if last else "--",
            cached=cached,
            source_used=source_used,
            errors=errors,
        )

    def _fetch(self) -> tuple[list[ValuationRow], list[str]]:
        rows: list[ValuationRow] = []
        errors: list[str] = []
        for spec in VALUATION_SPECS:
            try:
                row = _fetch_spec(spec)
                if row is not None:
                    rows.append(row)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{spec.name}: {e}")
                log.warning("valuation fetch failed secid=%s name=%s: %s", spec.secid, spec.name, e)
        return rows, errors

    def _last_fetched_at(self) -> datetime | None:
        row = self.conn.execute("SELECT MAX(fetched_at) FROM index_valuation_snapshot").fetchone()
        if not row or not row[0]:
            return None
        try:
            dt = datetime.fromisoformat(row[0])
            return dt if dt.tzinfo else dt.replace(tzinfo=CST)
        except ValueError:
            return None

    def _upsert(self, rows: list[ValuationRow]) -> None:
        self.conn.executemany(
            """
            INSERT INTO index_valuation_snapshot
              (secid,code,name,trade_date,pe,pb,dividend_yield,pe_percentile,pb_percentile,
               source,fetched_at,raw_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(secid,source) DO UPDATE SET
              code=excluded.code,
              name=excluded.name,
              trade_date=excluded.trade_date,
              pe=excluded.pe,
              pb=excluded.pb,
              dividend_yield=excluded.dividend_yield,
              pe_percentile=excluded.pe_percentile,
              pb_percentile=excluded.pb_percentile,
              fetched_at=excluded.fetched_at,
              raw_json=excluded.raw_json
            """,
            [
                (
                    r.secid, r.code, r.name, r.trade_date, r.pe, r.pb, r.dividend_yield,
                    r.pe_percentile, r.pb_percentile, r.source, r.fetched_at,
                    json.dumps(r.raw, ensure_ascii=False, default=str),
                )
                for r in rows
            ],
        )

    def _load(self) -> list[ValuationRow]:
        rows = self.conn.execute(
            """SELECT secid,code,name,trade_date,pe,pb,dividend_yield,pe_percentile,pb_percentile,
                      source,fetched_at,raw_json
               FROM index_valuation_snapshot ORDER BY secid"""
        ).fetchall()
        out: list[ValuationRow] = []
        for row in rows:
            try:
                raw = json.loads(row[11] or "{}")
            except json.JSONDecodeError:
                raw = {}
            out.append(ValuationRow(
                secid=row[0], code=row[1], name=row[2], trade_date=row[3],
                pe=row[4], pb=row[5], dividend_yield=row[6],
                pe_percentile=row[7], pb_percentile=row[8],
                source=row[9], fetched_at=row[10], raw=raw,
            ))
        return out


def render_valuation_markdown(panel: ValuationPanel) -> str:
    lines = [
        "### 指数估值分位（公开估值源）",
        f"- 最近刷新：{panel.refreshed_at}；来源：{panel.source_used}",
        "- 使用边界：估值分位只说明历史相对位置，不代表短期一定涨跌；缺 PB 的指数只展示 PE。",
    ]
    if panel.errors:
        lines.append("- 抓取异常：" + "；".join(panel.errors[:5]))
    if not panel.rows:
        lines.append("- 暂无可用估值缓存。")
        return "\n".join(lines)
    lines.append("| 指数 | 日期 | PE | PE分位 | PB | PB分位 | 股息率 |")
    lines.append("|---|---|---:|---:|---:|---:|---:|")
    for row in panel.rows:
        lines.append(
            f"| {row.name} | {row.trade_date or '--'} | {_num(row.pe)} | {_pct(row.pe_percentile)} | "
            f"{_num(row.pb)} | {_pct(row.pb_percentile)} | {_pct(row.dividend_yield)} |"
        )
    return "\n".join(lines)


def _fetch_spec(spec: ValuationSpec) -> ValuationRow | None:
    pe_value = pe_pct = dividend = None
    pb_value = pb_pct = None
    trade_dates: list[str] = []
    raw: dict[str, Any] = {}

    if spec.pe_kind and spec.pe_symbol:
        pe_df, pe_col, div_col = _fetch_pe_frame(spec.pe_kind, spec.pe_symbol)
        if pe_df is not None and not pe_df.empty and pe_col in pe_df:
            pe_df = pe_df.sort_values("日期")
            pe_series = pd.to_numeric(pe_df[pe_col], errors="coerce").dropna()
            if not pe_series.empty:
                pe_value = float(pe_series.iloc[-1])
                pe_pct = _percentile(pe_series, pe_value)
                latest = pe_df.iloc[-1]
                trade_dates.append(str(latest.get("日期") or ""))
                if div_col and div_col in pe_df:
                    dividend = _float(latest.get(div_col))
                raw["pe"] = {k: _jsonable(v) for k, v in latest.to_dict().items()}

    if spec.pb_kind and spec.pb_symbol:
        pb_df = _fetch_pb_frame(spec.pb_kind, spec.pb_symbol)
        if pb_df is not None and not pb_df.empty and "市净率" in pb_df:
            pb_df = pb_df.sort_values("日期")
            pb_series = pd.to_numeric(pb_df["市净率"], errors="coerce").dropna()
            if not pb_series.empty:
                pb_value = float(pb_series.iloc[-1])
                pb_pct = _percentile(pb_series, pb_value)
                latest = pb_df.iloc[-1]
                trade_dates.append(str(latest.get("日期") or ""))
                raw["pb"] = {k: _jsonable(v) for k, v in latest.to_dict().items()}

    if pe_value is None and pb_value is None:
        return None
    return ValuationRow(
        secid=spec.secid,
        code=spec.code,
        name=spec.name,
        trade_date=max((d for d in trade_dates if d), default=None),
        pe=pe_value,
        pb=pb_value,
        dividend_yield=dividend,
        pe_percentile=pe_pct,
        pb_percentile=pb_pct,
        source="akshare_lg_csindex",
        fetched_at=_now().isoformat(timespec="seconds"),
        raw=raw,
    )


def _fetch_pe_frame(kind: str, symbol: str) -> tuple[pd.DataFrame | None, str, str | None]:
    import akshare as ak

    with _quiet_fetch_env():
        if kind == "index_pe_lg":
            return ak.stock_index_pe_lg(symbol=symbol), "滚动市盈率", None
        if kind == "market_pe_lg":
            df = ak.stock_market_pe_lg(symbol=symbol)
            if "平均市盈率" in df:
                return df, "平均市盈率", None
            return df, "市盈率", None
        if kind == "csindex":
            return ak.stock_zh_index_value_csindex(symbol=symbol), "市盈率1", "股息率1"
    raise ValueError(f"unknown pe source kind: {kind}")


def _fetch_pb_frame(kind: str, symbol: str) -> pd.DataFrame | None:
    import akshare as ak

    with _quiet_fetch_env():
        if kind == "index_pb_lg":
            return ak.stock_index_pb_lg(symbol=symbol)
        if kind == "market_pb_lg":
            return ak.stock_market_pb_lg(symbol=symbol)
    raise ValueError(f"unknown pb source kind: {kind}")


def _percentile(series: pd.Series, value: float) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return None
    return float((clean <= value).sum() / len(clean))


def _now() -> datetime:
    return datetime.now(CST)


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _float(value) -> float | None:
    try:
        import math

        f = float(value)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _num(value: float | None) -> str:
    return "--" if value is None else f"{value:.2f}"


def _pct(value: float | None) -> str:
    return "--" if value is None else f"{value * 100:.0f}%"


def _jsonable(v):
    try:
        if hasattr(v, "item"):
            return v.item()
    except Exception:
        pass
    return v
