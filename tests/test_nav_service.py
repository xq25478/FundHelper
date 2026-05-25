from __future__ import annotations

from datetime import date
from dataclasses import dataclass

import numpy as np
import pandas as pd
import pytest

from fund_helper.config import AppConfig
from fund_helper.datasource.base import FundDataSource
from fund_helper.domain import NavSeries
from fund_helper.services.nav_service import NavService


def _series(code: str, dates: list[date]) -> NavSeries:
    idx = pd.DatetimeIndex(pd.to_datetime(dates))
    df = pd.DataFrame({
        "unit_nav":     np.linspace(1.0, 1.1, len(idx)),
        "acc_nav":      np.linspace(1.0, 1.1, len(idx)),
        "daily_return": np.full(len(idx), 0.001),
    }, index=idx)
    df.index.name = "trade_date"
    return NavSeries(code=code, frame=df)


@dataclass
class _FakeSource(FundDataSource):
    name: str = "fake"
    calls: list[tuple[str, date, date]] = None

    def __post_init__(self):
        self.calls = []

    def list_funds(self):    raise NotImplementedError
    def get_fund(self, code): raise NotImplementedError
    def get_holdings(self, code, report_date=None): raise NotImplementedError

    def get_nav(self, code, start=None, end=None):
        self.calls.append((code, start, end))
        # return only dates inside [start, end] from a generous backbone
        backbone = pd.bdate_range("2025-11-01", periods=200).date.tolist()
        days = [d for d in backbone if (start is None or d >= start) and (end is None or d <= end)]
        return _series(code, days)


@pytest.fixture
def svc(tmp_path):
    cfg = AppConfig(data_dir=tmp_path)
    return NavService(cfg, source=_FakeSource())


def test_cold_then_hit(svc):
    end = date(2026, 5, 15)
    s1, o1 = svc.get_nav("X", lookback_days=180, end=end)
    assert o1.status == "fetched"
    assert len(s1.frame) > 100
    src_calls_first = len(svc.source.calls)

    s2, o2 = svc.get_nav("X", lookback_days=180, end=end)
    assert o2.status == "hit"
    assert o2.rows_fetched == 0
    assert len(svc.source.calls) == src_calls_first  # no new remote call
    assert len(s2.frame) == len(s1.frame)


def test_incremental_when_window_extends(svc):
    end1 = date(2026, 3, 15)
    svc.get_nav("X", lookback_days=120, end=end1)

    end2 = date(2026, 5, 15)
    _, o = svc.get_nav("X", lookback_days=180, end=end2)
    assert o.status in {"incremental", "fetched"}
    assert o.rows_fetched > 0
    # second remote call should start near old max date, not at original window_start
    second_call = svc.source.calls[-1]
    assert second_call[0] == "X"
    assert second_call[1] >= date(2026, 3, 1)


def test_force_refresh_replays_window(svc):
    end = date(2026, 5, 15)
    svc.get_nav("X", lookback_days=60, end=end)
    n_calls = len(svc.source.calls)
    _, o = svc.get_nav("X", lookback_days=60, end=end, force_refresh=True)
    assert o.status == "fetched"
    assert len(svc.source.calls) == n_calls + 1
