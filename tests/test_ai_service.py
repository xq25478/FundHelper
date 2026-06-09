from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fund_helper.services.ai_service import (
    _build_data_availability_section,
    _build_horizon_context,
    _build_main_force_intent,
    _fmt_pct,
    _fmt_ratio_pct,
    _next_trading_day_weekday,
    _postprocess_analysis_text,
    _replace_data_availability_section,
    _replace_main_force_intent_section,
    _strip_existing_auto_check,
    format_ai_call_info,
)
from fund_helper.config import AppConfig
from fund_helper.storage import connect


def test_percent_formatters_distinguish_percent_and_ratio_values():
    assert _fmt_pct(4.25) == "+4.25%"
    assert _fmt_ratio_pct(0.0032) == "+0.32%"


def test_format_ai_call_info_hides_secret_and_prints_details():
    text = format_ai_call_info({
        "protocol": "openai_chat",
        "url": "https://api.example/v1/chat/completions",
        "model": "deepseek-chat",
        "auth": "enabled",
        "timeout_seconds": 120,
        "max_tokens": 12000,
        "prompt_chars": 1234,
        "system_prompt_chars": 56,
        "response_chars": 789,
        "elapsed_seconds": 1.23,
    })

    assert "protocol=openai_chat" in text
    assert "model=deepseek-chat" in text
    assert "prompt_chars=1234" in text
    assert "elapsed=1.23s" in text
    assert "sk-" not in text


def test_horizon_context_includes_required_windows():
    text = _build_horizon_context(datetime(2026, 5, 26, 11, 0, tzinfo=timezone.utc))

    assert "下一个交易日：2026-05-27" in text
    assert "未来 7 天：2026-05-26 至 2026-06-02" in text
    assert "本月剩余窗口：2026-05-26 至 2026-05-31" in text
    assert "A 股大盘、重点板块、持有基金" in text


def test_next_trading_day_weekday_skips_weekend():
    assert _next_trading_day_weekday(date(2026, 5, 29)).isoformat() == "2026-06-01"


def test_data_availability_marks_cached_volume_and_flow_available(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yml").write_text(
        """
holdings:
  name: test
  positions:
    - code: "017811"
      name: 东方人工智能主题混合C
      weight: 1.0
""",
        encoding="utf-8",
    )
    cfg = AppConfig(data_dir=tmp_path / "data")
    conn = connect(cfg.data_dir / "fund.db")
    conn.execute(
        """INSERT INTO nav_daily
           (code,trade_date,unit_nav,acc_nav,daily_return,source,fetched_at)
           VALUES('017811','2026-05-26',1.0,1.0,0.01,'test','2026-05-27T10:00:00')"""
    )
    conn.execute(
        """INSERT INTO fund_realtime_snapshot
           (code,name,estimate_pct,estimate_time,source,fetched_at)
           VALUES('017811','东方人工智能主题混合C',1.23,'2026-05-27 11:30','test','2026-05-27T11:30:00')"""
    )
    conn.execute(
        """INSERT INTO index_snapshot
           (secid,name,now,volume,amount,fetched_at)
           VALUES('1.000001','上证指数',4100,100,200,'2026-05-27T11:30:00')"""
    )
    conn.execute(
        """INSERT INTO market_flow_snapshot
           (scope,item,trade_date,net_amount,net_pct,source,fetched_at)
           VALUES('market','沪深A股',?,100,1.2,'test','2026-05-27T11:30:00')""",
        (datetime.now(timezone(timedelta(hours=8))).date().isoformat(),),
    )
    conn.execute(
        """INSERT INTO market_flow_snapshot
           (scope,item,trade_date,net_amount,net_pct,source,fetched_at)
           VALUES('northbound','沪股通',?,50,0.5,'test','2026-05-27T11:30:00')""",
        (datetime.now(timezone(timedelta(hours=8))).date().isoformat(),),
    )
    for rank in range(1, 11):
        conn.execute(
            """INSERT INTO fund_top_holding
               (fund_code,season,rank,stock_code,stock_name,fetched_at)
               VALUES('017811','2026Q1',?,?,?,'2026-05-27T11:30:00')""",
            (rank, f"0000{rank:02d}", f"股票{rank}"),
        )

    text = _build_data_availability_section(cfg)

    assert "| 大盘现货量价 | ✅ 可用 |" in text
    assert "1/1 有成交量" in text
    assert "1/1 有成交额" in text
    assert "| 大盘资金流（盘中/主力/北向） | ✅ 可用 |" in text
    assert "盘后主力 1 条，北向摘要 1 条" in text


def test_replace_data_availability_section_overrides_model_claims():
    model_text = """# 零、数据可信度与缺失原因

| 数据域 | 状态 |
|---|---|
| 大盘资金流 | ❌ 缺失 |

# 一、大盘研判

正文
"""
    section = "# 零、数据可信度与缺失原因\n\n| 数据域 | 状态 |\n|---|---|\n| 大盘资金流 | ✅ 可用 |"

    out = _replace_data_availability_section(model_text, section)

    assert "| 大盘资金流 | ✅ 可用 |" in out
    assert "| 大盘资金流 | ❌ 缺失 |" not in out
    assert "# 一、大盘研判" in out


def test_replace_main_force_intent_section_overrides_model_claims():
    model_text = """# 零、数据可信度与缺失原因

正文

# 一、大盘研判

正文

# 二、主力意图分析

程序未提供主力意图分析事实框架，只能推断。

# 三、板块轮动

正文
"""
    framework = """## 主力意图分析事实框架（程序生成）
- 使用边界：只能基于公开资金流、指数量价、板块宽度和持仓主题强弱做倾向判断。

### 资金流证据
| 范围 | 项目 | 日期 | 主力/净额 |
|---|---|---|---:|
| market | 沪深A股 | 2026-05-28 | +12.00亿 |

### 主力意图倾向（非确定事实）
- 偏结构性抱团：全市场宽度一般，但当前持仓主题更强。
"""

    out = _replace_main_force_intent_section(model_text, framework)

    assert "程序未提供主力意图分析事实框架" not in out
    assert "以下内容由程序根据公开资金流" in out
    assert "偏结构性抱团" in out
    assert "# 三、板块轮动" in out


def test_postprocess_analysis_text_replaces_data_and_intent_before_guardrails():
    model_text = """完整分析报告

零、数据可信度与缺失原因

| 数据域 | 状态 |
|---|---|
| 大盘成交额/量 | ⚠️ 缺失 |

一、大盘研判

正文

二、主力意图分析

主力资金缺失，无法分析。

三、板块轮动

正文
"""
    data_availability = """# 零、数据可信度与缺失原因

| 数据域 | 状态 | 说明 |
|---|---|---|
| 大盘现货量价 | ✅ 可用 | 5/5 有报价，5/5 有成交量，5/5 有成交额。 |
"""
    market_intent = """## 主力意图分析事实框架（程序生成）

### 资金流证据
- 主力资金 1 条，北向摘要 2 条。

### 主力意图倾向（非确定事实）
- 偏震荡观望：资金、指数、板块之间未形成一致信号。
"""

    out = _postprocess_analysis_text(
        model_text,
        data_availability=data_availability,
        market_intent=market_intent,
    )

    assert out.startswith("# 零、数据可信度与缺失原因")
    assert "完整分析报告" not in out
    assert "大盘成交额/量 | ⚠️ 缺失" not in out
    assert "| 大盘现货量价 | ✅ 可用 |" in out
    assert "主力资金缺失，无法分析" not in out
    assert "偏震荡观望" in out
    assert "三、板块轮动" in out


def test_strip_existing_auto_check_removes_stale_findings():
    text = """# 一、大盘研判

正文

## 自动校验

- [P2] 旧的错误。

以上为基于公开数据的专业分析推演，不构成投资建议。
"""

    out = _strip_existing_auto_check(text)

    assert "旧的错误" not in out
    assert "以上为基于公开数据" in out


def test_main_force_intent_uses_public_flow_price_and_sector_breadth(tmp_path):
    cfg = AppConfig(data_dir=tmp_path / "data")
    conn = connect(cfg.data_dir / "fund.db")
    today = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    fetched_at = f"{today}T11:30:00"
    conn.execute(
        """INSERT INTO market_flow_snapshot
           (scope,item,trade_date,net_amount,net_pct,main_net_amount,
            super_large_net_amount,large_net_amount,medium_net_amount,small_net_amount,
            up_count,flat_count,down_count,source,fetched_at)
           VALUES('market','沪深A股',?,3000000000,1.2,3000000000,
                  2000000000,1200000000,-500000000,-800000000,
                  NULL,NULL,NULL,'test',?)""",
        (today, fetched_at),
    )
    for secid, name, pct in (
        ("1.000001", "上证指数", 0.004),
        ("0.399001", "深证成指", 0.012),
        ("1.000300", "沪深300", 0.006),
        ("0.399006", "创业板指", 0.018),
        ("1.000688", "科创50", 0.015),
    ):
        conn.execute(
            """INSERT INTO index_snapshot
               (secid,name,now,pct,amount,volume,fetched_at)
               VALUES(?,?,?,?,100000000,10000,?)""",
            (secid, name, 1000.0, pct, fetched_at),
        )
    for idx, (category, label, name, pct) in enumerate((
        ("concept", "CPO", "CPO概念", 2.1),
        ("concept", "PCB", "PCB概念", 1.8),
        ("concept", "AI", "人工智能", 1.2),
        ("industry", "semi", "半导体", 0.9),
        ("industry", "comm", "通信设备", 0.6),
    )):
        conn.execute(
            """INSERT INTO sector_snapshot
               (category,label,name,companies,pct,leader_name,leader_pct,fetched_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (category, label, name, 10 + idx, pct, "测试龙头", pct + 3, fetched_at),
        )

    text = _build_main_force_intent(cfg)

    assert "主力意图分析事实框架" in text
    assert "大单承接" in text
    assert "偏进攻性增仓" in text
    assert "非确定事实" in text


def test_correct_availability_misclaims_rewrites_only_safe_lines():
    from fund_helper.services.ai_service import _correct_availability_misclaims

    section = "\n".join([
        "| 数据域 | 状态 | 说明 |",
        "|---|---|---|",
        "| 同类基金样本/同类排名 | ✅ 可用 | 5/5 只有排行 |",
        "| 估值分位/PE/PB分位 | ✅ 可用 | 6/6 有分位 |",
        "| 大盘资金流（盘中/主力/北向） | ✅ 可用 | 4 条 |",
        "| 融资融券 | ✅ 可用 | 2 个市场 |",
    ])
    body = "\n".join([
        "| 同类基金样本 | 未接入 | ❌ 缺失 |",
        "| 估值分位数 | 未接入 | ❌ 缺失 |",
        "| 北向资金 | 未接入 | ❌ 缺失 |",
        "| 融资融券 | 未接入 | ❌ 缺失 |",
        "说明:北向资金未接入,因此外资流向无法判断。",
        "因同类基金样本缺失,本次分析无法提供同类均值结论。",  # 不应被改
        "个股逐笔成交数据未接入,盘口判断降级。",                # 不应被改(逐笔)
        "- [P2] 报告引用了估值分位,但本次事实包未提供该数据。",  # 不应被改(P2)
    ])
    out = _correct_availability_misclaims(body, section)

    assert "“同类基金样本=缺失”行，与可信度表冲突" in out
    assert "“估值分位=缺失”行" in out
    assert "“北向资金=缺失”行" in out
    assert "“融资融券=缺失”行" in out

    # Inline sentence got softened
    assert "北向资金已接入" in out

    # Conclusion-level wording must be preserved
    assert "本次分析无法提供同类均值结论" in out

    # Microstructure references must stay untouched
    assert "个股逐笔成交数据未接入" in out

    # Guardrail audit lines must stay untouched
    assert "[P2] 报告引用了估值分位" in out
    assert "本次事实包未提供该数据" in out


def test_correct_availability_misclaims_keeps_text_when_program_table_empty():
    from fund_helper.services.ai_service import _correct_availability_misclaims

    body = "| 同类基金样本 | 未接入 | ❌ 缺失 |"
    assert _correct_availability_misclaims(body, "") == body
