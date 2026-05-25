from __future__ import annotations

from fund_helper.config import AppConfig, PushCfg
from fund_helper.services.notify_service import build_report_excerpt, push_report


def test_report_excerpt_strips_prompt_details(tmp_path):
    report = tmp_path / "latest.md"
    report.write_text(
        "# 标题\n\n正文\n\n<details><summary>Prompt</summary>secret</details>\n\n尾部",
        encoding="utf-8",
    )

    excerpt = build_report_excerpt(report, max_chars=200)

    assert "正文" in excerpt
    assert "尾部" in excerpt
    assert "secret" not in excerpt


def test_wecom_push_payload(tmp_path, monkeypatch):
    report = tmp_path / "latest.md"
    report.write_text("# 分析\n\n内容", encoding="utf-8")
    cfg = AppConfig(
        data_dir=tmp_path,
        push=PushCfg(
            enabled=True,
            provider="wecom",
            webhook_url="test-key",
            max_chars=200,
        ),
    )
    captured = {}

    class _Resp:
        ok = True
        status_code = 200
        text = '{"errcode":0,"errmsg":"ok"}'

        def json(self):
            return {"errcode": 0, "errmsg": "ok"}

    def _fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr("fund_helper.services.notify_service.requests.post", _fake_post)

    result = push_report(cfg, report, title="日报")

    assert result.ok is True
    assert captured["url"].endswith("key=test-key")
    assert captured["json"]["msgtype"] == "markdown"
    assert "日报" in captured["json"]["markdown"]["content"]
