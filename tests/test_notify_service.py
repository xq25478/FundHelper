from __future__ import annotations

from fund_helper.config import AppConfig, PushCfg
from fund_helper.services.notify_service import _build_html_body, build_report_excerpt, push_report


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


def test_smtp_push_payload(tmp_path, monkeypatch):
    report = tmp_path / "latest.md"
    report.write_text("# 分析\n\n内容", encoding="utf-8")
    cfg = AppConfig(
        data_dir=tmp_path,
        push=PushCfg(
            enabled=True,
            provider="smtp",
            mails=["test@example.com", "dev@example.com"],
            smtp_host="smtp.test.com",
            smtp_port=465,
            smtp_user="user@test.com",
            smtp_password="secret",
            max_chars=200,
        ),
    )
    captured = {}

    class _FakeSMTP:
        def __init__(self, host, port, timeout):
            captured["host"] = host
            captured["port"] = port
            captured["timeout"] = timeout

        def login(self, user, password):
            captured["user"] = user
            captured["password"] = password

        def sendmail(self, from_addr, to_addrs, msg):
            captured["from"] = from_addr
            captured["to"] = to_addrs
            captured["msg"] = msg

        def quit(self):
            pass

    monkeypatch.setattr(
        "fund_helper.services.notify_service.smtplib.SMTP_SSL", _FakeSMTP
    )

    result = push_report(cfg, report, title="日报")

    assert result.ok is True
    assert result.message == "已发送至 2 个收件人"
    assert "Content-Type: multipart/alternative" in captured["msg"]
    assert "text/plain" in captured["msg"]
    assert "text/html" in captured["msg"]
    assert captured["host"] == "smtp.test.com"
    assert captured["port"] == 465
    assert captured["user"] == "user@test.com"
    assert captured["password"] == "secret"
    assert captured["to"] == ["test@example.com", "dev@example.com"]


def test_html_email_wraps_wide_tables():
    html = _build_html_body(
        "日报",
        "\n".join([
            "| 基金 | 当前权重 | 建议权重 | 操作 | 理由 | 止错信号 |",
            "|---|---:|---:|---|---|---|",
            "| 017811 | 23.17% | 21% | 持有 | 观察波动 | 跌破均线复核 |",
        ]),
        "2026-05-27 14:30 CST",
    )

    assert '<div class="table-wrap"><table>' in html
    assert "overflow-x:auto" in html
    assert "min-width:860px" in html
    assert "max-width:1080px" in html


def test_push_disabled_raises(tmp_path):
    cfg = AppConfig(
        data_dir=tmp_path,
        push=PushCfg(enabled=False),
    )
    report = tmp_path / "latest.md"
    report.write_text("test", encoding="utf-8")
    try:
        push_report(cfg, report)
    except RuntimeError as e:
        assert "未启用" in str(e)


def test_push_no_mails_raises(tmp_path):
    cfg = AppConfig(
        data_dir=tmp_path,
        push=PushCfg(enabled=True, provider="smtp", mails=[]),
    )
    report = tmp_path / "latest.md"
    report.write_text("test", encoding="utf-8")
    try:
        push_report(cfg, report)
    except RuntimeError as e:
        assert "收件人" in str(e)


def test_build_report_excerpt_drops_postprocess_comments(tmp_path):
    from fund_helper.services.notify_service import build_report_excerpt

    p = tmp_path / "r.md"
    p.write_text(
        "# 零、数据可信度\n\n"
        "<!-- 自动修正:模型自编了“同类基金样本=缺失”行，与可信度表冲突，已删除 -->\n"
        "| 同类基金样本/同类排名 | ✅ 可用 | 5/5 |\n"
        "\n# 一、大盘研判\n\n正文\n",
        encoding="utf-8",
    )
    out = build_report_excerpt(p, max_chars=5000)
    assert "自动修正" not in out
    assert "同类基金样本/同类排名" in out


def test_build_report_excerpt_truncates_by_section_when_too_long(tmp_path):
    from fund_helper.services.notify_service import build_report_excerpt

    sections = [
        "# 零、数据可信度\n" + "A" * 200,
        "# 一、大盘研判\n" + "B" * 200,
        "# 二、主力意图\n" + "C" * 200,
        "# 三、板块\n" + "D" * 200,
    ]
    body = "\n\n".join(sections)
    p = tmp_path / "r.md"
    p.write_text(body, encoding="utf-8")
    out = build_report_excerpt(p, max_chars=700)
    # Section 零 should stay intact, later sections drop with suffix.
    assert "# 零、数据可信度" in out
    assert "...(后续章节已省略" in out or "已强制截断" in out
    assert "DDDD" not in out  # tail section dropped
