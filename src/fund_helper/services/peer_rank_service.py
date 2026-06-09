"""Same-category fund return ranking from public Eastmoney fund lists."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from ..config import AppConfig
from ..portfolio.holdings import Position, load_holdings
from ..storage import connect
from .market_flow_service import _quiet_fetch_env

log = logging.getLogger(__name__)
CST = timezone(timedelta(hours=8))
TTL_SECONDS = 12 * 3600
RETURN_COLUMNS = {
    "ret_1w": "近1周",
    "ret_1m": "近1月",
    "ret_3m": "近3月",
    "ret_6m": "近6月",
    "ret_1y": "近1年",
    "ret_ytd": "今年来",
}
RANK_COLUMNS = {
    "rank_1w": "ret_1w",
    "rank_1m": "ret_1m",
    "rank_3m": "ret_3m",
    "rank_6m": "ret_6m",
    "rank_1y": "ret_1y",
    "rank_ytd": "ret_ytd",
}


@dataclass(slots=True)
class PeerRankRow:
    code: str
    name: str | None
    category: str
    nav_date: str | None
    ret_1w: float | None
    ret_1m: float | None
    ret_3m: float | None
    ret_6m: float | None
    ret_1y: float | None
    ret_ytd: float | None
    rank_1w: int | None
    rank_1m: int | None
    rank_3m: int | None
    rank_6m: int | None
    rank_1y: int | None
    rank_ytd: int | None
    total: int
    source: str
    fetched_at: str
    raw: dict[str, Any]


@dataclass(slots=True)
class PeerRankPanel:
    rows: list[PeerRankRow]
    refreshed_at: str
    cached: bool
    source_used: str
    errors: list[str]


class PeerRankService:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.conn = connect(cfg.data_dir / "fund.db")

    def get_panel(self, *, force_refresh: bool = False) -> PeerRankPanel:
        holdings = load_holdings()
        codes = [p.code for p in holdings.positions]
        last = self._last_fetched_at(codes)
        fresh = last is not None and (_now() - last).total_seconds() <= TTL_SECONDS
        cached = fresh and not force_refresh
        errors: list[str] = []
        source_used = "sqlite"
        if not cached:
            rows, errors = self._fetch(holdings.positions)
            if rows:
                self._upsert(rows)
                last = _now()
                source_used = "eastmoney_fund_rank"
            elif last is None:
                source_used = "unavailable"
        return PeerRankPanel(
            rows=self._load(codes),
            refreshed_at=_fmt(last) if last else "--",
            cached=cached,
            source_used=source_used,
            errors=errors,
        )

    def _fetch(self, holdings: list[Position]) -> tuple[list[PeerRankRow], list[str]]:
        import akshare as ak

        by_category: dict[str, list[Position]] = {}
        for holding in holdings:
            by_category.setdefault(_infer_category(holding.name), []).append(holding)

        rows: list[PeerRankRow] = []
        errors: list[str] = []
        for category, items in by_category.items():
            try:
                with _quiet_fetch_env():
                    df = ak.fund_open_fund_rank_em(symbol=category)
                if df is None or df.empty:
                    continue
                prepared = _prepare_rank_frame(df)
                for holding in items:
                    row = _row_for_holding(prepared, holding.code)
                    if row is None and category != "全部":
                        row = _fallback_row(ak, holding.code)
                    if row is None:
                        errors.append(f"{holding.code}: 未在{category}排行中找到")
                        continue
                    rows.append(_to_peer_row(row, category, total=len(prepared)))
            except Exception as e:  # noqa: BLE001
                errors.append(f"{category}: {e}")
                log.warning("peer rank fetch failed category=%s: %s", category, e)
        return rows, errors

    def _last_fetched_at(self, codes: list[str]) -> datetime | None:
        if not codes:
            return None
        placeholders = ",".join("?" for _ in codes)
        row = self.conn.execute(
            f"SELECT MIN(fetched_at) FROM fund_peer_rank_snapshot WHERE code IN ({placeholders})",
            codes,
        ).fetchone()
        if not row or not row[0]:
            return None
        try:
            dt = datetime.fromisoformat(row[0])
            return dt if dt.tzinfo else dt.replace(tzinfo=CST)
        except ValueError:
            return None

    def _upsert(self, rows: list[PeerRankRow]) -> None:
        self.conn.executemany(
            """
            INSERT INTO fund_peer_rank_snapshot
              (code,name,category,nav_date,ret_1w,ret_1m,ret_3m,ret_6m,ret_1y,ret_ytd,
               rank_1w,rank_1m,rank_3m,rank_6m,rank_1y,rank_ytd,total,source,fetched_at,raw_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(code,category) DO UPDATE SET
              name=excluded.name,
              nav_date=excluded.nav_date,
              ret_1w=excluded.ret_1w,
              ret_1m=excluded.ret_1m,
              ret_3m=excluded.ret_3m,
              ret_6m=excluded.ret_6m,
              ret_1y=excluded.ret_1y,
              ret_ytd=excluded.ret_ytd,
              rank_1w=excluded.rank_1w,
              rank_1m=excluded.rank_1m,
              rank_3m=excluded.rank_3m,
              rank_6m=excluded.rank_6m,
              rank_1y=excluded.rank_1y,
              rank_ytd=excluded.rank_ytd,
              total=excluded.total,
              fetched_at=excluded.fetched_at,
              raw_json=excluded.raw_json
            """,
            [
                (
                    r.code, r.name, r.category, r.nav_date,
                    r.ret_1w, r.ret_1m, r.ret_3m, r.ret_6m, r.ret_1y, r.ret_ytd,
                    r.rank_1w, r.rank_1m, r.rank_3m, r.rank_6m, r.rank_1y, r.rank_ytd,
                    r.total, r.source, r.fetched_at,
                    json.dumps(r.raw, ensure_ascii=False, default=str),
                )
                for r in rows
            ],
        )

    def _load(self, codes: list[str]) -> list[PeerRankRow]:
        if not codes:
            return []
        placeholders = ",".join("?" for _ in codes)
        rows = self.conn.execute(
            f"""SELECT code,name,category,nav_date,ret_1w,ret_1m,ret_3m,ret_6m,ret_1y,ret_ytd,
                       rank_1w,rank_1m,rank_3m,rank_6m,rank_1y,rank_ytd,total,source,fetched_at,raw_json
                FROM fund_peer_rank_snapshot WHERE code IN ({placeholders})
                ORDER BY category, code""",
            codes,
        ).fetchall()
        out: list[PeerRankRow] = []
        for row in rows:
            try:
                raw = json.loads(row[19] or "{}")
            except json.JSONDecodeError:
                raw = {}
            out.append(PeerRankRow(
                code=row[0], name=row[1], category=row[2], nav_date=row[3],
                ret_1w=row[4], ret_1m=row[5], ret_3m=row[6], ret_6m=row[7],
                ret_1y=row[8], ret_ytd=row[9], rank_1w=row[10], rank_1m=row[11],
                rank_3m=row[12], rank_6m=row[13], rank_1y=row[14], rank_ytd=row[15],
                total=int(row[16] or 0), source=row[17], fetched_at=row[18], raw=raw,
            ))
        return out


def render_peer_rank_markdown(panel: PeerRankPanel) -> str:
    lines = [
        "### 同类基金收益排行（公开排行源）",
        f"- 最近刷新：{panel.refreshed_at}；来源：{panel.source_used}",
        "- 使用边界：这是同类型开放式基金的收益分位，适合看近期表现强弱；不是基金经理能力的完整排名。",
    ]
    if panel.errors:
        lines.append("- 抓取异常：" + "；".join(panel.errors[:5]))
    if not panel.rows:
        lines.append("- 暂无可用同类排行缓存。")
        return "\n".join(lines)
    lines.append("| 基金 | 同类 | 净值日 | 近1月 | 近1月排名 | 近3月 | 近3月排名 | 今年来排名 |")
    lines.append("|---|---|---|---:|---:|---:|---:|---:|")
    for row in panel.rows:
        lines.append(
            f"| {row.code} {row.name or ''} | {row.category} | {row.nav_date or '--'} | "
            f"{_pct(row.ret_1m)} | {_rank(row.rank_1m, row.total)} | "
            f"{_pct(row.ret_3m)} | {_rank(row.rank_3m, row.total)} | "
            f"{_rank(row.rank_ytd, row.total)} |"
        )
    return "\n".join(lines)


def _prepare_rank_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["基金代码"] = out["基金代码"].astype(str).str.zfill(6)
    for attr, col in RETURN_COLUMNS.items():
        out[attr] = pd.to_numeric(out.get(col), errors="coerce")
    for rank_col, ret_col in RANK_COLUMNS.items():
        out[rank_col] = out[ret_col].rank(ascending=False, method="min")
    return out


def _row_for_holding(frame: pd.DataFrame, code: str) -> pd.Series | None:
    rows = frame[frame["基金代码"] == str(code).zfill(6)]
    if rows.empty:
        return None
    return rows.iloc[0]


def _fallback_row(ak, code: str) -> pd.Series | None:
    with _quiet_fetch_env():
        df = ak.fund_open_fund_rank_em(symbol="全部")
    if df is None or df.empty:
        return None
    return _row_for_holding(_prepare_rank_frame(df), code)


def _to_peer_row(row: pd.Series, category: str, *, total: int) -> PeerRankRow:
    raw = {k: _jsonable(v) for k, v in row.to_dict().items() if not str(k).startswith("rank_")}
    return PeerRankRow(
        code=str(row.get("基金代码") or "").zfill(6),
        name=str(row.get("基金简称") or ""),
        category=category,
        nav_date=str(row.get("日期") or "") or None,
        ret_1w=_float(row.get("ret_1w")),
        ret_1m=_float(row.get("ret_1m")),
        ret_3m=_float(row.get("ret_3m")),
        ret_6m=_float(row.get("ret_6m")),
        ret_1y=_float(row.get("ret_1y")),
        ret_ytd=_float(row.get("ret_ytd")),
        rank_1w=_int(row.get("rank_1w")),
        rank_1m=_int(row.get("rank_1m")),
        rank_3m=_int(row.get("rank_3m")),
        rank_6m=_int(row.get("rank_6m")),
        rank_1y=_int(row.get("rank_1y")),
        rank_ytd=_int(row.get("rank_ytd")),
        total=total,
        source="eastmoney_open_fund_rank",
        fetched_at=_now().isoformat(timespec="seconds"),
        raw=raw,
    )


def _infer_category(name: str) -> str:
    if "ETF" in name or "联接" in name or "指数" in name:
        return "指数型"
    if "股票" in name:
        return "股票型"
    if "债" in name:
        return "债券型"
    if "货币" in name:
        return "货币型"
    return "混合型"


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


def _int(value) -> int | None:
    try:
        f = _float(value)
        return None if f is None else int(f)
    except (TypeError, ValueError):
        return None


def _pct(value: float | None) -> str:
    return "--" if value is None else f"{value:+.2f}%"


def _rank(rank: int | None, total: int) -> str:
    if rank is None or not total:
        return "--"
    return f"{rank}/{total} ({rank / total:.0%})"


def _jsonable(v):
    try:
        if hasattr(v, "item"):
            return v.item()
    except Exception:
        pass
    return v
