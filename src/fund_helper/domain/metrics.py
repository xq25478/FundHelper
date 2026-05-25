from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class PerfMetrics:
    cumulative_return: float
    annualized_return: float
    win_rate: float | None = None
    excess_return_vs_bench: float | None = None


@dataclass(slots=True)
class RiskMetrics:
    annualized_vol: float
    max_drawdown: float
    downside_vol: float | None = None
    var_95: float | None = None
    sharpe: float | None = None
    sortino: float | None = None
    calmar: float | None = None
    information_ratio: float | None = None
