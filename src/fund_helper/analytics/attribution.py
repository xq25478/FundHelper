"""Holdings-based attribution placeholder (top-10 contribution estimate)."""
from __future__ import annotations

import pandas as pd

from ..domain import PortfolioSnapshot


def top10_contribution_estimate(snap: PortfolioSnapshot,
                                ticker_returns: pd.DataFrame) -> pd.Series:
    """ticker_returns: index=date, columns=ticker, values=daily return."""
    weights = {h.ticker: h.weight for h in snap.holdings if h.is_top10}
    cols = [t for t in weights if t in ticker_returns.columns]
    if not cols:
        return pd.Series(dtype=float)
    weighted = ticker_returns[cols].mul(pd.Series(weights)[cols], axis=1)
    return weighted.sum(axis=0).sort_values(ascending=False)
