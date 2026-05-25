from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd


@dataclass(slots=True)
class NavPoint:
    trade_date: date
    unit_nav: float
    acc_nav: float | None = None
    daily_return: float | None = None


@dataclass(slots=True)
class NavSeries:
    code: str
    frame: pd.DataFrame   # columns: trade_date(idx), unit_nav, acc_nav, daily_return

    def __post_init__(self) -> None:
        if not isinstance(self.frame.index, pd.DatetimeIndex):
            raise ValueError("NavSeries.frame must be indexed by DatetimeIndex")

    def returns(self) -> pd.Series:
        if "daily_return" in self.frame.columns:
            return self.frame["daily_return"].dropna()
        base = self.frame.get("acc_nav", self.frame["unit_nav"])
        return base.pct_change().dropna()

    def slice(self, start: str | None = None, end: str | None = None) -> "NavSeries":
        f = self.frame
        if start:
            f = f[f.index >= pd.Timestamp(start)]
        if end:
            f = f[f.index <= pd.Timestamp(end)]
        return NavSeries(self.code, f)
