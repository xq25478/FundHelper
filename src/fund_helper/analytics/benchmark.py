from __future__ import annotations

import pandas as pd


def excess_return(fund_ret: pd.Series, bench_ret: pd.Series) -> pd.Series:
    aligned = pd.concat([fund_ret, bench_ret], axis=1, join="inner").dropna()
    return aligned.iloc[:, 0] - aligned.iloc[:, 1]
