"""Refresh full fund list. Equivalent to: fh refresh universe."""
from __future__ import annotations

from fund_helper.config import load_config
from fund_helper.datasource import build_default


def main() -> None:
    cfg = load_config()
    src = build_default(cfg)
    funds = src.list_funds()
    print(f"fetched {len(funds)} funds via {src.name}")


if __name__ == "__main__":
    main()
