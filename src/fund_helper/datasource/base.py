from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from ..domain import Fund, NavSeries, PortfolioSnapshot


class FundDataSource(ABC):
    """Unified interface every concrete data source must implement."""

    name: str = "base"

    @abstractmethod
    def list_funds(self) -> list[Fund]: ...

    @abstractmethod
    def get_fund(self, code: str) -> Fund: ...

    @abstractmethod
    def get_nav(self, code: str, start: date | None = None, end: date | None = None) -> NavSeries: ...

    @abstractmethod
    def get_holdings(self, code: str, report_date: date | None = None) -> PortfolioSnapshot: ...
