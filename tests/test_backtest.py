from __future__ import annotations

import numpy as np
import pandas as pd

from fund_helper.portfolio import backtest_portfolio


def test_backtest_runs_no_rebalance():
    idx = pd.bdate_range("2024-01-02", periods=60)
    rets = pd.DataFrame({
        "A": np.full(len(idx), 0.001),
        "B": np.full(len(idx), 0.0005),
    }, index=idx)
    res = backtest_portfolio(rets, weights={"A": 0.5, "B": 0.5}, rebalance="none")
    assert res.nav.iloc[-1] > 1.0
    assert len(res.returns) == len(idx)


def test_backtest_quarterly_rebalance_finite():
    idx = pd.bdate_range("2024-01-02", periods=200)
    rets = pd.DataFrame({
        "A": np.random.default_rng(0).normal(0.0006, 0.01, len(idx)),
        "B": np.random.default_rng(1).normal(0.0003, 0.008, len(idx)),
    }, index=idx)
    res = backtest_portfolio(rets, weights={"A": 0.6, "B": 0.4}, rebalance="Q")
    assert np.isfinite(res.nav).all()
