from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 244


def annualized_vol(returns: pd.Series, periods_per_year: int = TRADING_DAYS) -> float:
    if returns.empty:
        return float("nan")
    return float(returns.std(ddof=1) * np.sqrt(periods_per_year))


def max_drawdown(returns: pd.Series) -> float:
    """Returns a non-positive number, e.g. -0.32 for -32% max drawdown."""
    if returns.empty:
        return float("nan")
    nav = (1.0 + returns).cumprod()
    peak = nav.cummax()
    dd = nav / peak - 1.0
    return float(dd.min())


def downside_vol(returns: pd.Series, mar: float = 0.0,
                 periods_per_year: int = TRADING_DAYS) -> float:
    if returns.empty:
        return float("nan")
    diff = (returns - mar).clip(upper=0.0)
    return float(np.sqrt((diff ** 2).mean()) * np.sqrt(periods_per_year))


def var_historical(returns: pd.Series, alpha: float = 0.05) -> float:
    if returns.empty:
        return float("nan")
    return float(returns.quantile(alpha))
