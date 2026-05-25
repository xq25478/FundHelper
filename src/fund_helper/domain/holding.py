from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(slots=True)
class Holding:
    ticker: str
    name: str
    weight: float          # 占基金净值比例 0~1
    industry: str | None = None
    is_top10: bool = True


@dataclass(slots=True)
class PortfolioSnapshot:
    code: str
    report_date: date
    stock_pct: float | None = None
    bond_pct: float | None = None
    cash_pct: float | None = None
    holdings: list[Holding] = field(default_factory=list)
