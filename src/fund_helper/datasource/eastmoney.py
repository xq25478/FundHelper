"""Eastmoney market-data adapter (indices & equities for benchmarks/attribution).

This source intentionally does NOT implement fund-level NAV/holdings —
those live in `tiantian.py`. CompositeDataSource will skip NotImplementedError
and try the next source.
"""
from __future__ import annotations

from datetime import date

from ..domain import Fund, NavSeries, PortfolioSnapshot
from .base import FundDataSource


class EastmoneyDataSource(FundDataSource):
    name = "eastmoney"

    # --- fund-level: defer to tiantian/akshare ---
    def list_funds(self) -> list[Fund]:
        raise NotImplementedError

    def get_fund(self, code: str) -> Fund:
        raise NotImplementedError

    def get_nav(self, code: str, start: date | None = None, end: date | None = None) -> NavSeries:
        raise NotImplementedError

    def get_holdings(self, code: str, report_date: date | None = None) -> PortfolioSnapshot:
        raise NotImplementedError

    # --- market-data extensions (benchmarks, holding tickers) ---
    def get_index_quote(self, index_code: str, start: date, end: date):
        """TODO: push.eastmoney.com k-line endpoint -> DataFrame[date, close]."""
        raise NotImplementedError

    def get_stock_quote(self, ticker: str, start: date, end: date):
        """TODO: same endpoint, secid 0./1. + ticker."""
        raise NotImplementedError

    def get_stock_industry(self, ticker: str) -> str | None:
        """TODO: industry classification from eastmoney."""
        raise NotImplementedError
