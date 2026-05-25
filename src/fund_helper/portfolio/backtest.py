from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(slots=True)
class BacktestResult:
    nav: pd.Series          # 组合净值，起点 1.0
    returns: pd.Series      # 日度收益
    weights_history: pd.DataFrame


def backtest_portfolio(
    returns_panel: pd.DataFrame,         # columns=code, index=date, daily returns
    weights: dict[str, float],
    rebalance: str = "QE",                # "D","W","M","Q","Y" or "none"
) -> BacktestResult:
    codes = [c for c in weights if c in returns_panel.columns]
    if not codes:
        raise ValueError("no overlapping codes between weights and returns")
    panel = returns_panel[codes].dropna(how="all").fillna(0.0)

    w = pd.Series({c: weights[c] for c in codes}, dtype=float)
    w /= w.sum()

    if rebalance.lower() == "none":
        cum = (1.0 + panel).cumprod()
        port_nav = (cum * w).sum(axis=1) / w.sum()
        port_ret = port_nav.pct_change().fillna(0.0)
        wh = pd.DataFrame([w], index=[panel.index[0]])
        return BacktestResult(nav=port_nav, returns=port_ret, weights_history=wh)

    legacy = {"D": "D", "W": "W-FRI", "M": "ME", "Q": "QE", "Y": "YE"}
    freq = legacy.get(rebalance.upper(), rebalance)
    groups = panel.groupby(pd.Grouper(freq=freq))
    port_ret = pd.Series(0.0, index=panel.index)
    wh_rows: list[tuple[pd.Timestamp, pd.Series]] = []
    for _, chunk in groups:
        if chunk.empty:
            continue
        wh_rows.append((chunk.index[0], w.copy()))
        cur_w = w.copy()
        for dt, row in chunk.iterrows():
            day_ret = float((cur_w * row).sum())
            port_ret.loc[dt] = day_ret
            cur_w = cur_w * (1.0 + row)
            tot = cur_w.sum()
            if tot > 0:
                cur_w = cur_w / tot * w.sum()
    nav = (1.0 + port_ret).cumprod()
    wh = pd.DataFrame([r[1] for r in wh_rows], index=[r[0] for r in wh_rows])
    return BacktestResult(nav=nav, returns=port_ret, weights_history=wh)
