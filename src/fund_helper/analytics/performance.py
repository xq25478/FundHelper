from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 244


def cumulative_return(returns: pd.Series) -> float:
    if returns.empty:
        return float("nan")
    return float((1.0 + returns).prod() - 1.0)


def annualized_return(returns: pd.Series, periods_per_year: int = TRADING_DAYS) -> float:
    if returns.empty:
        return float("nan")
    total = (1.0 + returns).prod()
    n = len(returns)
    if n <= 0 or total <= 0:
        return float("nan")
    return float(total ** (periods_per_year / n) - 1.0)


def rolling_return(returns: pd.Series, window: int) -> pd.Series:
    return (1.0 + returns).rolling(window).apply(np.prod, raw=True) - 1.0
