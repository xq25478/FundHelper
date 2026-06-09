"""Deterministic facts used by the AI analyst and the terminal UI."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pandas as pd

from ..config import AppConfig
from ..portfolio.holdings import load_holdings
from .guardrails import EvidenceAvailability


BENCHMARK_ALIASES: dict[str, tuple[str, str]] = {
    "1.000001": ("1.000001", "上证指数"),
    "000001.SH": ("1.000001", "上证指数"),
    "0.399001": ("0.399001", "深证成指"),
    "399001.SZ": ("0.399001", "深证成指"),
    "1.000300": ("1.000300", "沪深300"),
    "000300.SH": ("1.000300", "沪深300"),
    "0.399006": ("0.399006", "创业板指"),
    "399006.SZ": ("0.399006", "创业板指"),
    "1.000688": ("1.000688", "科创50"),
    "000688.SH": ("1.000688", "科创50"),
}


@dataclass(slots=True)
class FundFact:
    code: str
    name: str
    weight: float
    latest_nav_date: str | None = None
    realtime_pct: float | None = None
    realtime_time: str | None = None
    realtime_source: str | None = None
    return_1m: float | None = None
    return_3m: float | None = None
    annualized_return: float | None = None
    annualized_vol: float | None = None
    sharpe: float | None = None
    max_drawdown: float | None = None
    benchmark_name: str | None = None
    benchmark_excess_3m: float | None = None
    benchmark_corr: float | None = None
    benchmark_beta: float | None = None
    information_ratio: float | None = None
    profile: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PortfolioFacts:
    generated_at: str
    funds: list[FundFact]
    weighted_return: float | None
    weighted_vol: float | None
    weighted_sharpe: float | None
    weighted_max_drawdown: float | None
    top_overlap_pairs: list[tuple[str, str, int]]
    missing: list[str]
    evidence: EvidenceAvailability
    peer_rank_markdown: str | None = None
    valuation_markdown: str | None = None
    margin_markdown: str | None = None

    def to_markdown(self) -> str:
        peer_note = (
            "- 同类基金收益排行：已接入公开排行源。只用于观察近期收益分位，不等同于基金经理能力排名。"
            if self.peer_rank_markdown
            else "- 同类基金样本：未接入。因此禁止输出同类均值、同类排名或同类胜率结论。"
        )
        valuation_note = (
            "- 指数估值分位：已接入公开 PE/PB 历史分位；缺 PB 的指数只能引用 PE 分位。"
            if self.valuation_markdown
            else "- 估值分位：未接入。因此只能标记为缺口，不能用于结论。"
        )
        margin_note = (
            "- 融资融券：已接入交易所公开汇总，通常滞后一日，只能作为杠杆资金背景。"
            if self.margin_markdown
            else "- 融资融券：未接入。因此只能标记为缺口，不能用于结论。"
        )
        lines = [
            "## 专家事实包（由程序计算，优先级高于模型推断）",
            "",
            f"- 生成时间：{self.generated_at} Asia/Shanghai",
            peer_note,
            valuation_note,
            margin_note,
            "- 逐笔资金流：未接入；已接入指数分钟线时，也不得写成逐笔盘口结论。",
            "",
            "### 持仓与风险指标",
            "| 基金 | 权重 | 净值日 | 近1月 | 近3月 | 年化收益 | 年化波动 | 风险收益比(Sharpe) | 最大回撤 |",
            "|---|---:|---|---:|---:|---:|---:|---:|---:|",
        ]
        for f in self.funds:
            lines.append(
                f"| {f.code} {f.name} | {_pct(f.weight)} | {f.latest_nav_date or '--'} | "
                f"{_pct(f.return_1m)} | {_pct(f.return_3m)} | {_pct(f.annualized_return)} | "
                f"{_pct(f.annualized_vol)} | {_num(f.sharpe)} | {_pct(f.max_drawdown)} |"
            )

        lines.extend([
            "",
            "### 当日公开估值涨跌",
            "| 基金 | 估值涨跌幅 | 估值时间 | 来源 |",
            "|---|---:|---|---|",
        ])
        for f in self.funds:
            lines.append(
                f"| {f.code} {f.name} | {_pct_from_percent(f.realtime_pct)} | "
                f"{f.realtime_time or '--'} | {f.realtime_source or '--'} |"
            )
        lines.append("- 说明：该栏来自公开数据源的基金净值估算，不是本项目自行估算，也不是基金公司已披露的最终净值。")

        lines.extend([
            "",
            "### 组合近似指标",
            "| 指标 | 数值 |",
            "|---|---:|",
            f"| 加权年化收益 | {_pct(self.weighted_return)} |",
            f"| 加权年化波动 | {_pct(self.weighted_vol)} |",
            f"| 组合风险收益比(Sharpe) | {_num(self.weighted_sharpe)} |",
            f"| 加权最大回撤 | {_pct(self.weighted_max_drawdown)} |",
            "",
            "### 主题代理基准对比",
            "| 基金 | 代理基准 | 近3月超额 | 相关性 | 跟随基准弹性(Beta) | 信息比率 |",
            "|---|---|---:|---:|---:|---:|",
        ])
        for f in self.funds:
            lines.append(
                f"| {f.code} {f.name} | {f.benchmark_name or '--'} | "
                f"{_pct(f.benchmark_excess_3m)} | {_num(f.benchmark_corr)} | "
                f"{_num(f.benchmark_beta)} | {_num(f.information_ratio)} |"
            )

        if any(f.profile for f in self.funds):
            lines.extend(["", "### 基金档案（本地配置）"])
            lines.append("| 基金 | 类别 | 基金经理 | 规模 | 费率 | 备注 |")
            lines.append("|---|---|---|---:|---|---|")
            for f in self.funds:
                p = f.profile
                if not p:
                    continue
                lines.append(
                    f"| {f.code} {f.name} | {p.get('category', '--')} | {p.get('manager', '--')} | "
                    f"{p.get('aum', '--')} | {p.get('fee', '--')} | {p.get('note', '--')} |"
                )

        if self.top_overlap_pairs:
            lines.extend(["", "### 前十大重仓股重合度"])
            lines.append("| 基金A | 基金B | 重合股票数 |")
            lines.append("|---|---|---:|")
            for a, b, n in self.top_overlap_pairs[:10]:
                lines.append(f"| {a} | {b} | {n} |")

        for section in (self.peer_rank_markdown, self.valuation_markdown, self.margin_markdown):
            if section:
                lines.extend(["", section])

        if self.missing:
            lines.extend(["", "### 缺口"])
            lines.extend(f"- {m}" for m in self.missing)

        return "\n".join(lines)


def build_portfolio_facts(
    cfg: AppConfig,
    *,
    force_refresh: bool = False,
    lookback_days: int = 252,
    include_xray: bool = True,
    include_realtime: bool = True,
) -> PortfolioFacts:
    from ..analytics import (
        annualized_return,
        annualized_vol,
        information_ratio,
        max_drawdown,
        sharpe_ratio,
    )
    from .index_daily_service import IndexDailyService
    from .nav_service import NavService
    from .xray_service import XrayService

    holdings = load_holdings()
    nav_svc = NavService(cfg)
    idx_svc = IndexDailyService(cfg)
    xray_svc = XrayService(cfg) if include_xray else None
    funds: list[FundFact] = []
    returns_panel: dict[str, pd.Series] = {}
    missing: list[str] = []
    benchmark_count = 0
    profile_count = 0
    realtime_quotes = _load_realtime_quotes(
        cfg,
        [p.code for p in holdings.positions],
        force_refresh=force_refresh,
        include_realtime=include_realtime,
        missing=missing,
    )

    for pos in holdings.positions:
        fact = FundFact(code=pos.code, name=pos.name, weight=pos.weight)
        if pos.code in realtime_quotes:
            q = realtime_quotes[pos.code]
            fact.realtime_pct = q.estimate_pct
            fact.realtime_time = q.estimate_time
            fact.realtime_source = q.source
        profile = _profile_for(cfg, pos.code)
        if profile:
            fact.profile = profile
            profile_count += 1
        try:
            series, _outcome = nav_svc.get_nav(
                pos.code, lookback_days=lookback_days, force_refresh=force_refresh
            )
            frame = series.frame
            if frame.empty:
                missing.append(f"{pos.code} {pos.name}: 无净值数据")
                funds.append(fact)
                continue
            rets = series.returns().dropna()
            returns_panel[pos.code] = rets
            fact.latest_nav_date = frame.index.max().date().isoformat()
            fact.return_1m = _window_return(frame, 30)
            fact.return_3m = _window_return(frame, 90)
            fact.annualized_return = annualized_return(rets)
            fact.annualized_vol = annualized_vol(rets)
            fact.sharpe = sharpe_ratio(rets, cfg.risk_free_rate)
            fact.max_drawdown = max_drawdown(rets)

            bench = _benchmark_for(cfg, pos.code, pos.name)
            if bench:
                secid, name = bench
                fact.benchmark_name = name
                bench_series = idx_svc.get_series(
                    secid, lookback_days=lookback_days, force_refresh=force_refresh
                )
                bench_ret = _index_returns(bench_series.frame)
                if not bench_ret.empty:
                    benchmark_count += 1
                    fact.benchmark_excess_3m = _excess_window(rets, bench_ret, 90)
                    aligned = pd.concat([rets, bench_ret], axis=1, join="inner").dropna()
                    if len(aligned) >= 20:
                        f_ret = aligned.iloc[:, 0]
                        b_ret = aligned.iloc[:, 1]
                        fact.benchmark_corr = float(f_ret.corr(b_ret))
                        var = float(b_ret.var(ddof=1))
                        if var:
                            fact.benchmark_beta = float(f_ret.cov(b_ret) / var)
                        fact.information_ratio = information_ratio(f_ret, b_ret)
        except Exception as e:  # noqa: BLE001
            missing.append(f"{pos.code} {pos.name}: 指标计算失败 ({e})")
        funds.append(fact)

    weights = holdings.normalized_weights()
    weighted_return = _weighted(funds, "annualized_return", weights)
    weighted_vol = _weighted(funds, "annualized_vol", weights)
    weighted_mdd = _weighted(funds, "max_drawdown", weights)
    weighted_sharpe = None
    portfolio_metrics = _portfolio_metrics(returns_panel, weights, cfg.risk_free_rate)
    if portfolio_metrics:
        weighted_return = portfolio_metrics["annualized_return"]
        weighted_vol = portfolio_metrics["annualized_vol"]
        weighted_sharpe = portfolio_metrics["sharpe"]
        weighted_mdd = portfolio_metrics["max_drawdown"]
    if weighted_return is not None and weighted_vol not in (None, 0) and not math.isnan(weighted_vol):
        weighted_sharpe = (weighted_return - cfg.risk_free_rate) / weighted_vol

    top_overlap_pairs = (
        _top_holding_overlaps(xray_svc, [f.code for f in funds], missing)
        if xray_svc is not None else []
    )
    peer_rank_markdown = _optional_peer_rank_markdown(cfg, force_refresh, missing)
    valuation_markdown = _optional_valuation_markdown(cfg, force_refresh, missing)
    margin_markdown = _optional_margin_markdown(cfg, force_refresh, missing)
    evidence = EvidenceAvailability(
        peer_data=bool(peer_rank_markdown),
        valuation_data=bool(valuation_markdown),
        northbound_data=False,
        margin_data=bool(margin_markdown),
        turnover_data=False,
        benchmark_data=benchmark_count > 0,
        fund_profile_data=profile_count > 0,
    )

    generated_at = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    return PortfolioFacts(
        generated_at=generated_at,
        funds=funds,
        weighted_return=weighted_return,
        weighted_vol=weighted_vol,
        weighted_sharpe=weighted_sharpe,
        weighted_max_drawdown=weighted_mdd,
        top_overlap_pairs=top_overlap_pairs,
        missing=missing,
        evidence=evidence,
        peer_rank_markdown=peer_rank_markdown,
        valuation_markdown=valuation_markdown,
        margin_markdown=margin_markdown,
    )


def _profile_for(cfg: AppConfig, code: str) -> dict[str, Any]:
    raw = getattr(cfg, "fund_profiles", {}) or {}
    item = raw.get(code)
    if item is None:
        return {}
    if hasattr(item, "model_dump"):
        return {k: v for k, v in item.model_dump().items() if v not in (None, "")}
    if isinstance(item, dict):
        return {k: v for k, v in item.items() if v not in (None, "")}
    return {}


def _benchmark_for(cfg: AppConfig, code: str, name: str) -> tuple[str, str] | None:
    by_fund = getattr(cfg.benchmarks, "by_fund", {}) or {}
    raw = by_fund.get(code) or _infer_benchmark(name) or cfg.benchmarks.default
    return BENCHMARK_ALIASES.get(str(raw))


def _infer_benchmark(name: str) -> str | None:
    if any(k in name for k in ("人工智能", "半导体", "芯片", "创新", "科创")):
        return "1.000688"
    if any(k in name for k in ("5G", "通信", "电网", "电力")):
        return "0.399006"
    return None


def _load_realtime_quotes(
    cfg: AppConfig,
    codes: list[str],
    *,
    force_refresh: bool,
    include_realtime: bool,
    missing: list[str],
) -> dict[str, Any]:
    if not include_realtime:
        return {}
    try:
        from .fund_realtime_service import FundRealtimeService

        return FundRealtimeService(cfg).get_quotes(codes, force_refresh=force_refresh)
    except Exception as e:  # noqa: BLE001
        missing.append(f"基金当日公开估值: 获取失败 ({e})；不影响净值、组合风险和基准指标")
        return {}


def _optional_peer_rank_markdown(
    cfg: AppConfig,
    force_refresh: bool,
    missing: list[str],
) -> str | None:
    try:
        from .peer_rank_service import PeerRankService, render_peer_rank_markdown

        panel = PeerRankService(cfg).get_panel(force_refresh=force_refresh)
        if not panel.rows:
            if panel.errors:
                missing.append("同类基金排行: " + "；".join(panel.errors[:3]))
            return None
        return render_peer_rank_markdown(panel)
    except Exception as e:  # noqa: BLE001
        missing.append(f"同类基金排行: 获取失败 ({e})")
        return None


def _optional_valuation_markdown(
    cfg: AppConfig,
    force_refresh: bool,
    missing: list[str],
) -> str | None:
    try:
        from .valuation_service import ValuationService, render_valuation_markdown

        panel = ValuationService(cfg).get_panel(force_refresh=force_refresh)
        if not panel.rows:
            if panel.errors:
                missing.append("指数估值分位: " + "；".join(panel.errors[:3]))
            return None
        return render_valuation_markdown(panel)
    except Exception as e:  # noqa: BLE001
        missing.append(f"指数估值分位: 获取失败 ({e})")
        return None


def _optional_margin_markdown(
    cfg: AppConfig,
    force_refresh: bool,
    missing: list[str],
) -> str | None:
    try:
        from .margin_service import MarginService, render_margin_markdown

        panel = MarginService(cfg).get_panel(force_refresh=force_refresh)
        if not panel.rows:
            if panel.errors:
                missing.append("融资融券: " + "；".join(panel.errors[:3]))
            return None
        return render_margin_markdown(panel)
    except Exception as e:  # noqa: BLE001
        missing.append(f"融资融券: 获取失败 ({e})")
        return None


def _window_return(frame: pd.DataFrame, days: int) -> float | None:
    if frame.empty or "unit_nav" not in frame.columns:
        return None
    cutoff = pd.Timestamp(date.today() - timedelta(days=days))
    sub = frame[frame.index >= cutoff]["unit_nav"].dropna()
    if len(sub) < 2 or not sub.iloc[0]:
        return None
    return float(sub.iloc[-1] / sub.iloc[0] - 1.0)


def _index_returns(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype=float)
    f = frame.copy()
    f["trade_date"] = pd.to_datetime(f["trade_date"])
    f = f.sort_values("trade_date").set_index("trade_date")
    close = f["close"].astype(float).dropna()
    return close.pct_change().dropna()


def _excess_window(fund_ret: pd.Series, bench_ret: pd.Series, days: int) -> float | None:
    cutoff = pd.Timestamp(date.today() - timedelta(days=days))
    aligned = pd.concat([fund_ret, bench_ret], axis=1, join="inner").dropna()
    aligned = aligned[aligned.index >= cutoff]
    if len(aligned) < 2:
        return None
    fund_total = float((1 + aligned.iloc[:, 0]).prod() - 1)
    bench_total = float((1 + aligned.iloc[:, 1]).prod() - 1)
    return fund_total - bench_total


def _weighted(funds: list[FundFact], attr: str, weights: dict[str, float]) -> float | None:
    total = 0.0
    seen = 0.0
    for f in funds:
        v = getattr(f, attr)
        if v is None or math.isnan(v):
            continue
        w = weights.get(f.code, f.weight)
        total += float(v) * w
        seen += w
    if seen <= 0:
        return None
    return total / seen


def _portfolio_metrics(
    returns_panel: dict[str, pd.Series],
    weights: dict[str, float],
    risk_free_rate: float,
) -> dict[str, float] | None:
    if not returns_panel:
        return None
    from ..analytics import annualized_return, annualized_vol, max_drawdown, sharpe_ratio

    panel = pd.concat(returns_panel, axis=1).dropna(how="all").fillna(0.0)
    codes = [c for c in weights if c in panel.columns]
    if not codes:
        return None
    w = pd.Series({c: weights[c] for c in codes}, dtype=float)
    w = w / w.sum()
    port_ret = panel[codes].mul(w, axis=1).sum(axis=1)
    if port_ret.empty:
        return None
    return {
        "annualized_return": annualized_return(port_ret),
        "annualized_vol": annualized_vol(port_ret),
        "sharpe": sharpe_ratio(port_ret, risk_free_rate),
        "max_drawdown": max_drawdown(port_ret),
    }


def _top_holding_overlaps(xray_svc, codes: list[str], missing: list[str]) -> list[tuple[str, str, int]]:
    stock_sets: dict[str, set[str]] = {}
    for code in codes:
        try:
            tops = xray_svc.get_top_holdings(code)
        except Exception as e:  # noqa: BLE001
            missing.append(f"{code}: 重仓股穿透失败 ({e})")
            continue
        if not tops:
            missing.append(f"{code}: 无前十大重仓股数据")
            continue
        stock_sets[code] = {h.stock_code for h in tops[:10]}

    overlaps: list[tuple[str, str, int]] = []
    for i, a in enumerate(codes):
        for b in codes[i + 1:]:
            if a not in stock_sets or b not in stock_sets:
                continue
            n = len(stock_sets[a] & stock_sets[b])
            if n:
                overlaps.append((a, b, n))
    overlaps.sort(key=lambda x: x[2], reverse=True)
    return overlaps


def _pct(v: float | None) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "--"
    return f"{float(v):+.2%}"


def _pct_from_percent(v: float | None) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "--"
    return f"{float(v):+.2f}%"


def _num(v: float | None) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "--"
    return f"{float(v):.2f}"
