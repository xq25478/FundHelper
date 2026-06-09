"""Minute-level index bars for intraday market structure."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from ..config import AppConfig
from ..storage import connect
from .market_flow_service import _quiet_fetch_env
from .market_service import A_SHARE_INDEXES, _is_a_session

log = logging.getLogger(__name__)
CST = timezone(timedelta(hours=8))
SESSION_TTL_SECONDS = 60
OFF_SESSION_TTL_SECONDS = 12 * 3600


@dataclass(slots=True)
class IntradayIndexSeries:
    secid: str
    name: str
    frame: pd.DataFrame


@dataclass(slots=True)
class IntradayPanel:
    series: list[IntradayIndexSeries]
    refreshed_at: str
    cached: bool
    source_used: str
    errors: list[str]


class IndexIntradayService:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.conn = connect(cfg.data_dir / "fund.db")

    def get_panel(self, *, force_refresh: bool = False) -> IntradayPanel:
        today = _today()
        last = self._last_fetched_at(today)
        ttl = SESSION_TTL_SECONDS if _is_a_session() else OFF_SESSION_TTL_SECONDS
        fresh = last is not None and (_now() - last).total_seconds() <= ttl
        cached = fresh and not force_refresh
        errors: list[str] = []
        source_used = "sqlite"
        if not cached:
            rows, errors = self._fetch(today)
            if rows:
                self._upsert(rows)
                last = _now()
                source_used = "efinance_index_minute"
            elif last is None:
                source_used = "unavailable"
        return IntradayPanel(
            series=self._load(today),
            refreshed_at=_fmt(last) if last else "--",
            cached=cached,
            source_used=source_used,
            errors=errors,
        )

    def _fetch(self, trade_day: date) -> tuple[list[tuple], list[str]]:
        import efinance as ef
        from efinance.common.config import MarketType

        errors: list[str] = []
        rows: list[tuple] = []
        fetched_at = _now().isoformat(timespec="seconds")
        begin_end = trade_day.strftime("%Y%m%d")
        for secid, display, _market in A_SHARE_INDEXES:
            code = secid.split(".", 1)[1]
            try:
                df = _fetch_quote_history(
                    ef,
                    MarketType,
                    code,
                    begin_end,
                )
                if df is None or df.empty:
                    continue
                for _, r in df.iterrows():
                    ts = str(r.get("日期") or "")
                    if not ts:
                        continue
                    rows.append((
                        secid,
                        ts[:10],
                        ts,
                        _float(r.get("开盘")),
                        _float(r.get("收盘")),
                        _float(r.get("最高")),
                        _float(r.get("最低")),
                        _float(r.get("成交量")),
                        _float(r.get("成交额")),
                        None,
                        fetched_at,
                    ))
            except Exception as e:  # noqa: BLE001
                errors.append(f"{display}: {e}")
                log.warning("index intraday fetch failed secid=%s: %s", secid, e)
        return rows, errors

    def _last_fetched_at(self, trade_day: date) -> datetime | None:
        row = self.conn.execute(
            "SELECT MAX(fetched_at) FROM index_intraday WHERE trade_date=?",
            (trade_day.isoformat(),),
        ).fetchone()
        if not row or not row[0]:
            return None
        try:
            dt = datetime.fromisoformat(row[0])
            return dt if dt.tzinfo else dt.replace(tzinfo=CST)
        except ValueError:
            return None

    def _upsert(self, rows: list[tuple]) -> None:
        self.conn.executemany(
            """INSERT INTO index_intraday
                 (secid,trade_date,ts,open,close,high,low,volume,amount,avg,fetched_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(secid,ts) DO UPDATE SET
                 open=excluded.open,
                 close=excluded.close,
                 high=excluded.high,
                 low=excluded.low,
                 volume=excluded.volume,
                 amount=excluded.amount,
                 avg=excluded.avg,
                 fetched_at=excluded.fetched_at""",
            rows,
        )

    def _load(self, trade_day: date) -> list[IntradayIndexSeries]:
        name_by_secid = {secid: name for secid, name, _market in A_SHARE_INDEXES}
        out: list[IntradayIndexSeries] = []
        for secid, name, _market in A_SHARE_INDEXES:
            rows = self.conn.execute(
                """SELECT ts,open,close,high,low,volume,amount,avg
                   FROM index_intraday
                   WHERE secid=? AND trade_date=?
                   ORDER BY ts ASC""",
                (secid, trade_day.isoformat()),
            ).fetchall()
            frame = pd.DataFrame(
                rows,
                columns=["ts", "open", "close", "high", "low", "volume", "amount", "avg"],
            )
            out.append(IntradayIndexSeries(
                secid=secid,
                name=name_by_secid.get(secid, name),
                frame=frame,
            ))
        return out


def render_intraday_markdown(panel: IntradayPanel) -> str:
    lines = [
        "### 指数分钟线结构（公开1分钟K线）",
        f"- 最近刷新：{panel.refreshed_at}；来源：{panel.source_used}",
        "- 使用边界：这是分钟K线，不是逐笔成交；可观察日内走势、尾盘方向和成交额节奏。",
    ]
    if panel.errors:
        lines.append("- 抓取异常：" + "；".join(panel.errors[:5]))
    available = [s for s in panel.series if not s.frame.empty]
    if not available:
        lines.append("- 暂无可用分钟线缓存。")
        return "\n".join(lines)
    lines.append("| 指数 | 分钟数 | 最新时间 | 日内涨跌 | 近15分钟 | 日内振幅 | 成交额 |")
    lines.append("|---|---:|---|---:|---:|---:|---:|")
    for series in available:
        f = series.frame.dropna(subset=["close"]).copy()
        if f.empty:
            continue
        closes = f["close"].astype(float)
        highs = f["high"].dropna().astype(float) if "high" in f else closes
        lows = f["low"].dropna().astype(float) if "low" in f else closes
        amount = f["amount"].dropna().astype(float).sum() if "amount" in f else None
        first = closes.iloc[0]
        last = closes.iloc[-1]
        recent_base = closes.iloc[-min(15, len(closes))]
        amplitude = None
        if first and not highs.empty and not lows.empty:
            amplitude = float((highs.max() - lows.min()) / first)
        lines.append(
            f"| {series.name} | {len(f)} | {str(f['ts'].iloc[-1])[-5:]} | "
            f"{_pct_ratio((last / first - 1) if first else None)} | "
            f"{_pct_ratio((last / recent_base - 1) if recent_base else None)} | "
            f"{_pct_ratio(amplitude)} | {_fmt_yuan_yi(amount)} |"
        )
    return "\n".join(lines)


def _fetch_quote_history(ef, market_type, code: str, begin_end: str):
    kwargs = {
        "beg": begin_end,
        "end": begin_end,
        "klt": 1,
        "fqt": 0,
        "market_type": market_type.A_stock_index,
        "suppress_error": True,
    }
    try:
        return ef.stock.get_quote_history(code, **kwargs)
    except Exception as e:  # noqa: BLE001
        if "Proxy" not in str(e) and "proxy" not in str(e):
            raise
        with _quiet_fetch_env():
            return ef.stock.get_quote_history(code, **kwargs)


def _today() -> date:
    return _now().date()


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


def _pct_ratio(value: float | None) -> str:
    return "--" if value is None else f"{value * 100:+.2f}%"


def _fmt_yuan_yi(value: float | None) -> str:
    if value is None:
        return "--"
    try:
        return f"{float(value) / 100_000_000:+.2f}亿"
    except Exception:
        return str(value)
