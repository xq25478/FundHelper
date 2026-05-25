"""Lightweight style regression placeholder.

Future: regress fund returns on size/value/growth factor returns to infer style.
"""
from __future__ import annotations

import pandas as pd


def style_regress(fund_ret: pd.Series, factor_ret: pd.DataFrame) -> dict[str, float]:
    aligned = pd.concat([fund_ret, factor_ret], axis=1, join="inner").dropna()
    if aligned.empty or aligned.shape[1] < 2:
        return {}
    y = aligned.iloc[:, 0].values
    X = aligned.iloc[:, 1:].values
    import numpy as np
    Xb = np.hstack([np.ones((X.shape[0], 1)), X])
    beta, *_ = np.linalg.lstsq(Xb, y, rcond=None)
    names = ["alpha", *aligned.columns[1:]]
    return {n: float(b) for n, b in zip(names, beta)}
