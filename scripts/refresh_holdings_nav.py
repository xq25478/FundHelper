"""Refresh NAV for every fund in configs/holdings.yaml via NavService."""
from __future__ import annotations

import argparse

from fund_helper.config import load_config
from fund_helper.portfolio.holdings import load_holdings
from fund_helper.services.nav_service import NavService


def main(lookback: int) -> None:
    cfg = load_config()
    svc = NavService(cfg)
    h = load_holdings()
    fails: list[str] = []
    for p in h.positions:
        try:
            series, outcome = svc.get_nav(p.code, lookback_days=lookback)
        except Exception as e:
            fails.append(p.code)
            print(f"  [FAIL] {p.code} {p.name}: {e}")
            continue
        if series.frame.empty:
            fails.append(p.code)
            print(f"  [EMPTY] {p.code} {p.name}")
            continue
        rng = (series.frame.index.min().date(), series.frame.index.max().date())
        print(f"  [{outcome.status:11s}] {p.code} {p.name}: "
              f"{len(series.frame)} rows {rng[0]}~{rng[1]} "
              f"(fetched={outcome.rows_fetched})")
    if fails:
        print(f"\n{len(fails)} fund(s) failed: {fails}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--lookback", type=int, default=180,
                    help="lookback window in days (default 180)")
    main(ap.parse_args().lookback)
