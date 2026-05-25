"""Refresh NAV for a single fund into sqlite. Usage:
    python scripts/refresh_nav.py 005827 [lookback_days]
"""
from __future__ import annotations

import sys

from fund_helper.config import load_config
from fund_helper.services.nav_service import NavService


def main(code: str, lookback: int = 180) -> None:
    cfg = load_config()
    svc = NavService(cfg)
    series, outcome = svc.get_nav(code, lookback_days=lookback, force_refresh=True)
    print(f"{code}: db_after={len(series.frame)} rows "
          f"(fetched={outcome.rows_fetched}, status={outcome.status})")


if __name__ == "__main__":
    code = sys.argv[1]
    lookback = int(sys.argv[2]) if len(sys.argv) > 2 else 180
    main(code, lookback)
