from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(slots=True)
class FilterSpec:
    fund_type: str | None = None
    min_aum: float | None = None          # 元
    min_years: float | None = None
    min_sharpe: float | None = None
    max_drawdown: float | None = None     # e.g. -0.3 means keep if mdd >= -0.3
    exclude_tags: list[str] | None = None


def apply_filters(df: pd.DataFrame, spec: FilterSpec) -> pd.DataFrame:
    """df is expected to have columns: code, fund_type, aum, years, sharpe, max_dd, tags."""
    mask = pd.Series(True, index=df.index)
    if spec.fund_type:
        mask &= df["fund_type"] == spec.fund_type
    if spec.min_aum is not None:
        mask &= df["aum"].fillna(0) >= spec.min_aum
    if spec.min_years is not None:
        mask &= df["years"].fillna(0) >= spec.min_years
    if spec.min_sharpe is not None:
        mask &= df["sharpe"].fillna(-1e9) >= spec.min_sharpe
    if spec.max_drawdown is not None:
        mask &= df["max_dd"].fillna(-1.0) >= spec.max_drawdown
    if spec.exclude_tags:
        bad = set(spec.exclude_tags)
        mask &= ~df["tags"].fillna("").apply(
            lambda t: any(x in bad for x in (t.split(",") if isinstance(t, str) else t))
        )
    return df.loc[mask].copy()
