from __future__ import annotations

from datetime import date, datetime, timezone

from fund_helper.config import AppConfig
from fund_helper.services import trading_calendar
from fund_helper.services.ai_service import _build_horizon_context
from fund_helper.services.trading_calendar import TradingCalendarService


def test_trading_calendar_uses_exchange_holidays(tmp_path, monkeypatch):
    dates = {
        date(2026, 4, 30),
        date(2026, 5, 6),
        date(2026, 5, 7),
        date(2026, 5, 8),
    }
    monkeypatch.setattr(trading_calendar, "_fetch_trade_dates", lambda: dates)

    svc = TradingCalendarService(AppConfig(data_dir=tmp_path))
    snapshot = svc.get_calendar(force_refresh=True)

    assert svc.next_trading_day(date(2026, 4, 30), snapshot=snapshot) == date(2026, 5, 6)
    assert svc.trading_days_between(date(2026, 4, 30), date(2026, 5, 6), snapshot=snapshot) == 2
    assert not svc.is_trading_day(date(2026, 5, 1), snapshot=snapshot)


def test_horizon_context_uses_cached_exchange_calendar(tmp_path, monkeypatch):
    dates = {
        date(2026, 4, 30),
        date(2026, 5, 6),
        date(2026, 5, 7),
        date(2026, 5, 8),
        date(2026, 5, 11),
        date(2026, 5, 12),
        date(2026, 5, 13),
    }
    monkeypatch.setattr(trading_calendar, "_fetch_trade_dates", lambda: dates)
    cfg = AppConfig(data_dir=tmp_path)

    text = _build_horizon_context(cfg, datetime(2026, 4, 30, 11, 0, tzinfo=timezone.utc))

    assert "下一个交易日：2026-05-06" in text
    assert "交易日历来源：akshare.tool_trade_date_hist_sina" in text
    assert "未来 7 天：2026-04-30 至 2026-05-07（含 3 个 A 股交易日）" in text
