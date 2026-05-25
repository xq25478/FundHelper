from __future__ import annotations

import numpy as np
import pandas as pd

from .performance import annualized_return
from .risk import annualized_vol, downside_vol, max_drawdown

TRADING_DAYS = 244


def sharpe_ratio(returns: pd.Series, risk_free: float = 0.0) -> float:
    vol = annualized_vol(returns)
    if not vol or np.isnan(vol):
        return float("nan")
    return float((annualized_return(returns) - risk_free) / vol)


def sortino_ratio(returns: pd.Series, risk_free: float = 0.0) -> float:
    dvol = downside_vol(returns, mar=risk_free / TRADING_DAYS)
    if not dvol or np.isnan(dvol):
        return float("nan")
    return float((annualized_return(returns) - risk_free) / dvol)


def calmar_ratio(returns: pd.Series) -> float:
    mdd = max_drawdown(returns)
    if mdd == 0 or np.isnan(mdd):
        return float("nan")
    return float(annualized_return(returns) / abs(mdd))


def information_ratio(returns: pd.Series, bench_returns: pd.Series) -> float:
    aligned = pd.concat([returns, bench_returns], axis=1, join="inner").dropna()
    if aligned.empty:
        return float("nan")
    active = aligned.iloc[:, 0] - aligned.iloc[:, 1]
    te = active.std(ddof=1) * np.sqrt(TRADING_DAYS)
    if not te:
        return float("nan")
    return float(active.mean() * TRADING_DAYS / te)
