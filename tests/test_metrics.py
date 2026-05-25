from __future__ import annotations

import math

import pandas as pd

from fund_helper.analytics import (
    annualized_return, annualized_vol, cumulative_return,
    max_drawdown, sharpe_ratio, sortino_ratio, calmar_ratio,
)


def test_cumulative_zero_returns():
    rets = pd.Series([0.0] * 100,
                     index=pd.bdate_range("2024-01-02", periods=100))
    assert cumulative_return(rets) == 0.0
    assert annualized_return(rets) == 0.0


def test_max_drawdown_known_path():
    rets = pd.Series([0.10, -0.20, 0.05, -0.10],
                     index=pd.bdate_range("2024-01-02", periods=4))
    # nav path: 1.10, 0.88, 0.924, 0.8316; peak=1.10; mdd ~ 0.8316/1.10 - 1
    assert max_drawdown(rets) == pytest_approx(0.8316 / 1.10 - 1.0)


def test_metrics_on_synthetic(synthetic_returns):
    ar = annualized_return(synthetic_returns)
    av = annualized_vol(synthetic_returns)
    sr = sharpe_ratio(synthetic_returns, risk_free=0.02)
    assert math.isfinite(ar) and math.isfinite(av)
    assert math.isfinite(sr)
    # vol must be non-negative
    assert av >= 0
    # sortino/calmar should be finite on positive-drift series
    assert math.isfinite(sortino_ratio(synthetic_returns, 0.02))
    assert math.isfinite(calmar_ratio(synthetic_returns))


def pytest_approx(x, tol=1e-9):
    import pytest
    return pytest.approx(x, abs=tol)
