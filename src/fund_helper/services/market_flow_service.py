"""A-share market fund-flow cache.

The service uses public AkShare adapters backed by Eastmoney endpoints. These
sources can fail or rate-limit intraday, so the caller always receives the last
valid sqlite snapshot plus explicit error notes instead of model-facing blanks.
"""
from __future__ import annotations

import json
import logging
import os
import io
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ..config import AppConfig
from ..storage import connect
from .market_service import _is_a_session

log = logging.getLogger(__name__)
CST = timezone(timedelta(hours=8))
SESSION_TTL_SECONDS = 60
OFF_SESSION_TTL_SECONDS = 12 * 3600


@dataclass(slots=True)
class MarketFlowRow:
    scope: str
    item: str
    trade_date: str | None
    net_amount: float | None
    net_pct: float | None
    main_net_amount: float | None
    super_large_net_amount: float | None
    large_net_amount: float | None
    medium_net_amount: float | None
    small_net_amount: float | None
    up_count: int | None
    flat_count: int | None
    down_count: int | None
    source: str
    fetched_at: str
    raw: dict[str, Any]


@dataclass(slots=True)
class MarketFlowPanel:
    rows: list[MarketFlowRow]
    refreshed_at: str
    cached: bool
    source_used: str
    errors: list[str]


class MarketFlowService:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.conn = connect(cfg.data_dir / "fund.db")

    def get_panel(self, *, force_refresh: bool = False) -> MarketFlowPanel:
        last = self._last_fetched_at()
        ttl = SESSION_TTL_SECONDS if _is_a_session() else OFF_SESSION_TTL_SECONDS
        fresh = last is not None and (_now() - last).total_seconds() <= ttl
        cached = fresh and not force_refresh
        errors: list[str] = []
        source_used = "sqlite"
        if not cached:
            rows, errors = self._fetch()
            if rows:
                self._upsert(rows)
                last = _now()
                source_used = "eastmoney"
            elif last is None:
                source_used = "unavailable"
        loaded = self._load()
        refreshed_at = _fmt(last) if last else "--"
        return MarketFlowPanel(
            rows=loaded,
            refreshed_at=refreshed_at,
            cached=bool(cached),
            source_used=source_used,
            errors=errors,
        )

    def _fetch(self) -> tuple[list[MarketFlowRow], list[str]]:
        rows: list[MarketFlowRow] = []
        errors: list[str] = []
        for fn in (_fetch_market_intraday_fund_flow, _fetch_market_fund_flow, _fetch_northbound_flow):
            try:
                rows.extend(fn())
            except Exception as e:  # noqa: BLE001
                errors.append(f"{fn.__name__}: {e}")
                log.warning("market flow fetch failed fn=%s: %s", fn.__name__, e)
        return rows, errors

    def _last_fetched_at(self) -> datetime | None:
        row = self.conn.execute("SELECT MAX(fetched_at) FROM market_flow_snapshot").fetchone()
        if not row or not row[0]:
            return None
        try:
            dt = datetime.fromisoformat(row[0])
            return dt if dt.tzinfo else dt.replace(tzinfo=CST)
        except ValueError:
            return None

    def _upsert(self, rows: list[MarketFlowRow]) -> None:
        data = [
            (
                r.scope, r.item, r.trade_date, r.net_amount, r.net_pct,
                r.main_net_amount, r.super_large_net_amount, r.large_net_amount,
                r.medium_net_amount, r.small_net_amount, r.up_count, r.flat_count,
                r.down_count, r.source, r.fetched_at,
                json.dumps(r.raw, ensure_ascii=False, default=str),
            )
            for r in rows
        ]
        self.conn.executemany(
            """
            INSERT INTO market_flow_snapshot
              (scope,item,trade_date,net_amount,net_pct,main_net_amount,
               super_large_net_amount,large_net_amount,medium_net_amount,
               small_net_amount,up_count,flat_count,down_count,source,fetched_at,raw_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(scope,item,source) DO UPDATE SET
              trade_date=excluded.trade_date,
              net_amount=excluded.net_amount,
              net_pct=excluded.net_pct,
              main_net_amount=excluded.main_net_amount,
              super_large_net_amount=excluded.super_large_net_amount,
              large_net_amount=excluded.large_net_amount,
              medium_net_amount=excluded.medium_net_amount,
              small_net_amount=excluded.small_net_amount,
              up_count=excluded.up_count,
              flat_count=excluded.flat_count,
              down_count=excluded.down_count,
              fetched_at=excluded.fetched_at,
              raw_json=excluded.raw_json
            """,
            data,
        )

    def _load(self) -> list[MarketFlowRow]:
        cur = self.conn.execute(
            """SELECT scope,item,trade_date,net_amount,net_pct,main_net_amount,
                      super_large_net_amount,large_net_amount,medium_net_amount,
                      small_net_amount,up_count,flat_count,down_count,source,fetched_at,raw_json
               FROM market_flow_snapshot
               ORDER BY scope,item"""
        )
        out: list[MarketFlowRow] = []
        for row in cur.fetchall():
            try:
                raw = json.loads(row[15] or "{}")
            except json.JSONDecodeError:
                raw = {}
            out.append(MarketFlowRow(
                scope=row[0], item=row[1], trade_date=row[2],
                net_amount=row[3], net_pct=row[4], main_net_amount=row[5],
                super_large_net_amount=row[6], large_net_amount=row[7],
                medium_net_amount=row[8], small_net_amount=row[9],
                up_count=row[10], flat_count=row[11], down_count=row[12],
                source=row[13], fetched_at=row[14], raw=raw,
            ))
        return out


def _fetch_market_fund_flow() -> list[MarketFlowRow]:
    import akshare as ak

    with _quiet_fetch_env():
        df = ak.stock_market_fund_flow()
    if df is None or df.empty:
        return []
    r = df.sort_values("日期").iloc[-1]
    return [
        MarketFlowRow(
            scope="market",
            item="沪深A股",
            trade_date=str(r.get("日期") or ""),
            net_amount=_float(r.get("主力净流入-净额")),
            net_pct=_float(r.get("主力净流入-净占比")),
            main_net_amount=_float(r.get("主力净流入-净额")),
            super_large_net_amount=_float(r.get("超大单净流入-净额")),
            large_net_amount=_float(r.get("大单净流入-净额")),
            medium_net_amount=_float(r.get("中单净流入-净额")),
            small_net_amount=_float(r.get("小单净流入-净额")),
            up_count=None,
            flat_count=None,
            down_count=None,
            source="eastmoney_market_fund_flow",
            fetched_at=_now().isoformat(timespec="seconds"),
            raw={k: _jsonable(v) for k, v in r.to_dict().items()},
        )
    ]


def _fetch_market_intraday_fund_flow() -> list[MarketFlowRow]:
    import akshare as ak

    with _quiet_fetch_env():
        df = ak.stock_fund_flow_individual(symbol="即时")
    if df is None or df.empty:
        return []
    net_values = [_parse_cn_money(v) for v in df.get("净额", [])]
    amount_values = [_parse_cn_money(v) for v in df.get("成交额", [])]
    net_amount = sum(v for v in net_values if v is not None)
    amount = sum(v for v in amount_values if v is not None)
    pct_values = [_parse_percent(v) for v in df.get("涨跌幅", [])]
    up_count = sum(1 for v in pct_values if v is not None and v > 0)
    flat_count = sum(1 for v in pct_values if v == 0)
    down_count = sum(1 for v in pct_values if v is not None and v < 0)
    net_pct = (net_amount / amount * 100) if amount else None
    return [
        MarketFlowRow(
            scope="market_intraday",
            item="沪深A股即时资金流",
            trade_date=_now().date().isoformat(),
            net_amount=net_amount,
            net_pct=net_pct,
            main_net_amount=net_amount,
            super_large_net_amount=None,
            large_net_amount=None,
            medium_net_amount=None,
            small_net_amount=None,
            up_count=up_count,
            flat_count=flat_count,
            down_count=down_count,
            source="eastmoney_stock_fund_flow_intraday",
            fetched_at=_now().isoformat(timespec="seconds"),
            raw={
                "rows": len(df),
                "net_amount": net_amount,
                "amount": amount,
                "note": "由东方财富个股即时资金流汇总得到，非逐笔盘口。",
            },
        )
    ]


def _fetch_northbound_flow() -> list[MarketFlowRow]:
    import akshare as ak

    with _quiet_fetch_env():
        df = ak.stock_hsgt_fund_flow_summary_em()
    if df is None or df.empty:
        return []
    out: list[MarketFlowRow] = []
    for _, r in df.iterrows():
        direction = str(r.get("资金方向") or "")
        board = str(r.get("板块") or "")
        if direction != "北向":
            continue
        out.append(MarketFlowRow(
            scope="northbound",
            item=board,
            trade_date=str(r.get("交易日") or ""),
            net_amount=_scale_yi_to_yuan(_float(r.get("成交净买额"))),
            net_pct=_float(r.get("指数涨跌幅")),
            main_net_amount=None,
            super_large_net_amount=None,
            large_net_amount=None,
            medium_net_amount=None,
            small_net_amount=None,
            up_count=_int(r.get("上涨数")),
            flat_count=_int(r.get("持平数")),
            down_count=_int(r.get("下跌数")),
            source="eastmoney_hsgt_summary",
            fetched_at=_now().isoformat(timespec="seconds"),
            raw={k: _jsonable(v) for k, v in r.to_dict().items()},
        ))
    return out


@contextmanager
def _quiet_fetch_env():
    saved: dict[str, str] = {}
    for key in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        if key in os.environ:
            saved[key] = os.environ.pop(key)
    old_tqdm = os.environ.get("TQDM_DISABLE")
    os.environ["TQDM_DISABLE"] = "1"
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            yield
    finally:
        for key, value in saved.items():
            os.environ[key] = value
        os.environ.pop("NO_PROXY", None)
        os.environ.pop("no_proxy", None)
        if old_tqdm is None:
            os.environ.pop("TQDM_DISABLE", None)
        else:
            os.environ["TQDM_DISABLE"] = old_tqdm


def _now() -> datetime:
    return datetime.now(CST)


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _float(v: Any) -> float | None:
    try:
        import math

        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _scale_yi_to_yuan(v: float | None) -> float | None:
    return None if v is None else v * 100_000_000


def _parse_cn_money(value: Any) -> float | None:
    text = str(value or "").strip().replace(",", "")
    if not text or text in {"--", "nan", "None"}:
        return None
    sign = -1 if text.startswith("-") else 1
    text = text.lstrip("+-")
    multiplier = 1.0
    if text.endswith("亿"):
        multiplier = 100_000_000.0
        text = text[:-1]
    elif text.endswith("万"):
        multiplier = 10_000.0
        text = text[:-1]
    try:
        return sign * float(text) * multiplier
    except ValueError:
        return None


def _parse_percent(value: Any) -> float | None:
    text = str(value or "").strip().replace("%", "")
    return _float(text)


def _jsonable(v: Any) -> Any:
    if hasattr(v, "item"):
        try:
            return v.item()
        except Exception:  # noqa: BLE001
            pass
    if hasattr(v, "isoformat"):
        try:
            return v.isoformat()
        except Exception:  # noqa: BLE001
            pass
    return v


def row_to_dict(row: MarketFlowRow) -> dict[str, Any]:
    return asdict(row)
