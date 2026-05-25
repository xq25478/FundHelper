"""AkShare adapter — used as fallback / supplement for fields tiantian lacks."""
from __future__ import annotations

from datetime import date

from ..domain import Fund, NavSeries, PortfolioSnapshot
from .base import FundDataSource


class AkShareDataSource(FundDataSource):
    name = "akshare"

    def __init__(self) -> None:
        import akshare  # noqa: F401  fail fast if missing

    def list_funds(self) -> list[Fund]:
        """TODO: ak.fund_name_em() -> Fund list."""
        raise NotImplementedError

    def get_fund(self, code: str) -> Fund:
        """TODO: ak.fund_individual_basic_info_xq()."""
        raise NotImplementedError

    def get_nav(self, code: str, start: date | None = None, end: date | None = None) -> NavSeries:
        """TODO: ak.fund_open_fund_info_em(symbol=code, indicator='累计净值走势')."""
        raise NotImplementedError

    def get_holdings(self, code: str, report_date: date | None = None) -> PortfolioSnapshot:
        """TODO: ak.fund_portfolio_hold_em(symbol=code, date='YYYY')."""
        raise NotImplementedError
