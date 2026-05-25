from __future__ import annotations

import logging
from datetime import date

from ..domain import Fund, NavSeries, PortfolioSnapshot
from .base import FundDataSource

log = logging.getLogger(__name__)


class CompositeDataSource(FundDataSource):
    """Try sources in order; first non-raising result wins."""

    name = "composite"

    def __init__(self, sources: list[FundDataSource]) -> None:
        if not sources:
            raise ValueError("CompositeDataSource needs at least one source")
        self.sources = sources

    def _call(self, method: str, *args, **kwargs):
        last_err: Exception | None = None
        for src in self.sources:
            try:
                return getattr(src, method)(*args, **kwargs)
            except NotImplementedError:
                continue
            except Exception as e:
                log.warning("%s.%s failed: %s", src.name, method, e)
                last_err = e
        if last_err:
            raise last_err
        raise NotImplementedError(method)

    def list_funds(self) -> list[Fund]:
        return self._call("list_funds")

    def get_fund(self, code: str) -> Fund:
        return self._call("get_fund", code)

    def get_nav(self, code: str, start: date | None = None, end: date | None = None) -> NavSeries:
        return self._call("get_nav", code, start, end)

    def get_holdings(self, code: str, report_date: date | None = None) -> PortfolioSnapshot:
        return self._call("get_holdings", code, report_date)
