from __future__ import annotations

from fund_helper.config import AppConfig
from fund_helper.services.background_refresh import BackgroundRefreshWorker


def test_background_refresh_worker_run_once_updates_status(tmp_path, monkeypatch):
    worker = BackgroundRefreshWorker(AppConfig(data_dir=tmp_path), run_immediately=False)

    monkeypatch.setattr(worker, "_refresh_holdings_nav", lambda: "nav ok")
    monkeypatch.setattr(worker, "_refresh_fund_realtime", lambda: "rt ok")
    monkeypatch.setattr(worker, "_refresh_market", lambda: "market ok")
    monkeypatch.setattr(worker, "_refresh_index_intraday", lambda: "intraday ok")
    monkeypatch.setattr(worker, "_refresh_market_flow", lambda: "flow ok")
    monkeypatch.setattr(worker, "_refresh_sectors", lambda: "sector ok")
    monkeypatch.setattr(worker, "_refresh_peer_rank", lambda: "peer ok")
    monkeypatch.setattr(worker, "_refresh_valuation", lambda: "valuation ok")
    monkeypatch.setattr(worker, "_refresh_margin", lambda: "margin ok")
    monkeypatch.setattr(worker, "_refresh_news", lambda: "news ok")
    monkeypatch.setattr(worker, "_refresh_company_watch", lambda: "company ok")

    worker.run_once()

    status = worker.snapshot()
    assert status["running"] is False
    assert status["total_runs"] == 1
    assert status["total_tasks"] == 11
    assert status["completed_tasks"] == 11
    assert status["current_task"] is None
    assert status["last_ok"] is True
    assert "nav ok" in status["last_message"]
    assert status["next_run_at"]


def test_background_refresh_worker_records_task_failure(tmp_path, monkeypatch):
    worker = BackgroundRefreshWorker(AppConfig(data_dir=tmp_path), run_immediately=False)

    monkeypatch.setattr(worker, "_refresh_holdings_nav", lambda: "nav ok")
    monkeypatch.setattr(worker, "_refresh_fund_realtime", lambda: "rt ok")
    monkeypatch.setattr(worker, "_refresh_market", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(worker, "_refresh_index_intraday", lambda: "intraday ok")
    monkeypatch.setattr(worker, "_refresh_market_flow", lambda: "flow ok")
    monkeypatch.setattr(worker, "_refresh_sectors", lambda: "sector ok")
    monkeypatch.setattr(worker, "_refresh_peer_rank", lambda: "peer ok")
    monkeypatch.setattr(worker, "_refresh_valuation", lambda: "valuation ok")
    monkeypatch.setattr(worker, "_refresh_margin", lambda: "margin ok")
    monkeypatch.setattr(worker, "_refresh_news", lambda: "news ok")
    monkeypatch.setattr(worker, "_refresh_company_watch", lambda: "company ok")

    worker.run_once()

    status = worker.snapshot()
    assert status["last_ok"] is False
    assert "大盘行情: 失败(boom)" in status["last_message"]
    assert any("大盘行情: 失败(boom)" in err for err in status["errors"])


def test_background_refresh_worker_run_due_skips_recent_tasks(tmp_path, monkeypatch):
    worker = BackgroundRefreshWorker(AppConfig(data_dir=tmp_path), run_immediately=False)
    calls = {"nav": 0}

    def nav():
        calls["nav"] += 1
        return "nav ok"

    monkeypatch.setattr(worker, "_refresh_holdings_nav", nav)
    monkeypatch.setattr(worker, "_refresh_fund_realtime", lambda: "rt ok")
    monkeypatch.setattr(worker, "_refresh_market", lambda: "market ok")
    monkeypatch.setattr(worker, "_refresh_index_intraday", lambda: "intraday ok")
    monkeypatch.setattr(worker, "_refresh_market_flow", lambda: "flow ok")
    monkeypatch.setattr(worker, "_refresh_sectors", lambda: "sector ok")
    monkeypatch.setattr(worker, "_refresh_peer_rank", lambda: "peer ok")
    monkeypatch.setattr(worker, "_refresh_valuation", lambda: "valuation ok")
    monkeypatch.setattr(worker, "_refresh_margin", lambda: "margin ok")
    monkeypatch.setattr(worker, "_refresh_news", lambda: "news ok")
    monkeypatch.setattr(worker, "_refresh_company_watch", lambda: "company ok")
    worker._tasks = (("持仓净值", "_refresh_holdings_nav", 3600),)

    worker.run_due()
    worker.run_due()

    assert calls["nav"] == 1
