from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def synthetic_returns() -> pd.Series:
    rng = np.random.default_rng(42)
    idx = pd.bdate_range("2022-01-03", periods=244 * 2)  # ~2 years
    daily = rng.normal(loc=0.0006, scale=0.012, size=len(idx))
    return pd.Series(daily, index=idx, name="ret")


@pytest.fixture
def screener_df() -> pd.DataFrame:
    return pd.DataFrame({
        "code": ["000001", "000002", "000003", "000004"],
        "name": ["A", "B", "C", "D"],
        "fund_type": ["equity", "equity", "hybrid", "bond"],
        "aum": [1.2e9, 4e8, 8e8, 2e9],
        "years": [6.5, 1.0, 4.0, 10.0],
        "annualized_return": [0.18, 0.30, 0.09, 0.04],
        "sharpe": [1.1, 0.8, 0.6, 0.4],
        "calmar": [0.8, 0.5, 0.4, 1.5],
        "max_dd": [-0.22, -0.45, -0.15, -0.05],
        "tags": ["core", "small,new", "value", "bond"],
    })
