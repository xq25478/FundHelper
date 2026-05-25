from __future__ import annotations

import pandas as pd

DEFAULT_WEIGHTS: dict[str, float] = {
    "annualized_return": 0.30,
    "sharpe":            0.25,
    "calmar":            0.20,
    "max_dd":            0.15,   # less negative = better, so we invert below
    "aum":               0.05,
    "years":             0.05,
}


def _zscore(s: pd.Series) -> pd.Series:
    std = s.std(ddof=0)
    if not std or pd.isna(std):
        return pd.Series(0, index=s.index)
    return (s - s.mean()) / std


def score(df: pd.DataFrame, weights: dict[str, float] | None = None) -> pd.DataFrame:
    w = weights or DEFAULT_WEIGHTS
    out = df.copy()
    out["_score"] = 0.0
    for col, weight in w.items():
        if col not in out.columns:
            continue
        col_vals = out[col].astype(float)
        if col == "max_dd":
            col_vals = -col_vals  # -0.5 -> 0.5; less drawdown is better
        out["_score"] += _zscore(col_vals).fillna(0) * weight
    return out.sort_values("_score", ascending=False)
