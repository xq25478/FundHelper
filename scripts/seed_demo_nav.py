"""Deprecated: synthetic NAV is no longer the primary path. Kept for offline UI demos.

Run only when you intentionally want fake data in sqlite.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from fund_helper.config import load_config
from fund_helper.domain import NavSeries
from fund_helper.portfolio.holdings import load_holdings
from fund_helper.storage import NavRepo, connect


def synth_nav(code: str, seed: int) -> NavSeries:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2023-01-03", periods=520)
    daily = rng.normal(loc=0.0007, scale=0.018, size=len(idx))
    unit = (1.0 + daily).cumprod()
    df = pd.DataFrame({"unit_nav": unit, "acc_nav": unit, "daily_return": daily}, index=idx)
    df.index.name = "trade_date"
    return NavSeries(code=code, frame=df)


def main() -> None:
    cfg = load_config()
    conn = connect(cfg.data_dir / "fund.db")
    repo = NavRepo(conn)
    for p in load_holdings().positions:
        s = synth_nav(p.code, seed=hash(p.code) & 0xFFFF)
        repo.upsert_series(s, source="synthetic")
        print(f"seeded {p.code} ({p.name}) -> {len(s.frame)} rows")


if __name__ == "__main__":
    main()
