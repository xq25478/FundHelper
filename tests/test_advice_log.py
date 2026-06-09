from __future__ import annotations

import json

from fund_helper.services import advice_log


def test_record_report_writes_slots_dataclass(tmp_path, monkeypatch):
    log_path = tmp_path / "advice_log.jsonl"
    monkeypatch.setattr(advice_log, "LOG_PATH", log_path)

    entry = advice_log.record_report(
        report_path=tmp_path / "latest.md",
        target="holdings",
        title="持仓诊断",
        text="| 017811 | 建议持有观察 |",
        guardrail_findings=1,
    )

    rows = log_path.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    raw = json.loads(rows[0])
    assert raw["target"] == "holdings"
    assert raw["guardrail_findings"] == 1
    assert raw["sha256"] == entry.sha256
    assert raw["action_lines"] == ["| 017811 | 建议持有观察 |"]
    assert "metadata" in raw
