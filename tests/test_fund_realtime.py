from __future__ import annotations

from fund_helper.config import AppConfig
from fund_helper.datasource.tiantian import parse_fund_estimate_jsonp
from fund_helper.services import fact_pack
from fund_helper.services.fund_realtime_service import FundRealtimeService


def test_parse_fund_estimate_jsonp():
    payload = (
        'jsonpgz({"fundcode":"017811","name":"东方人工智能主题混合C",'
        '"jzrq":"2026-05-22","dwjz":"2.1000","gsz":"2.1420",'
        '"gszzl":"2.00","gztime":"2026-05-25 14:57"});'
    )

    got = parse_fund_estimate_jsonp(payload)

    assert got == {
        "code": "017811",
        "name": "东方人工智能主题混合C",
        "nav_date": "2026-05-22",
        "unit_nav": 2.1,
        "estimate_nav": 2.142,
        "estimate_pct": 2.0,
        "estimate_time": "2026-05-25 14:57",
    }


def test_parse_fund_estimate_jsonp_rejects_invalid_payload():
    assert parse_fund_estimate_jsonp("var x = 1;") is None


def test_realtime_service_degrades_when_estimate_fetch_fails(tmp_path, monkeypatch):
    def _boom(*_args, **_kwargs):
        raise RuntimeError("fetch_fund_estimate import failed")

    monkeypatch.setattr("fund_helper.services.fund_realtime_service._fetch_fund_estimate", _boom)

    svc = FundRealtimeService(AppConfig(data_dir=tmp_path))

    assert svc.get_quote("017811", force_refresh=True) is None


def test_fact_pack_realtime_failure_is_domain_scoped(tmp_path, monkeypatch):
    class _BrokenRealtimeService:
        def __init__(self, _cfg):
            pass

        def get_quotes(self, _codes, *, force_refresh=False):
            raise RuntimeError("realtime unavailable")

    monkeypatch.setattr(
        "fund_helper.services.fund_realtime_service.FundRealtimeService",
        _BrokenRealtimeService,
    )
    missing: list[str] = []

    quotes = fact_pack._load_realtime_quotes(
        AppConfig(data_dir=tmp_path),
        ["017811"],
        force_refresh=True,
        include_realtime=True,
        missing=missing,
    )

    assert quotes == {}
    assert "基金当日公开估值" in missing[0]
    assert "不影响净值" in missing[0]
