from __future__ import annotations

from fund_helper.services.guardrails import (
    EvidenceAvailability,
    extract_expert_rules,
    validate_report_text,
)


def test_extract_rules_from_buyer_profile_text():
    rules = extract_expert_rules("风险偏好：单只基金权重 ≤ 25%，组合最大回撤 -30% 以内。")

    assert rules.max_single_fund_weight == 0.25
    assert rules.max_drawdown_tolerance == -0.30


def test_validate_report_flags_weight_above_cap():
    report = validate_report_text(
        "| 017811 | 23.17% | 26% - 28% | 增持 | 纯度高 | |",
        rules=extract_expert_rules("单只基金权重 ≤ 25%"),
        evidence=EvidenceAvailability(),
    )

    assert not report.ok
    assert "超过单只基金上限" in report.to_markdown()


def test_validate_report_flags_unsupported_peer_claim():
    report = validate_report_text(
        "该基金近 1 月显著跑赢同类均值，建议继续加仓。",
        evidence=EvidenceAvailability(peer_data=False),
    )

    assert not report.ok
    assert "同类均值" in report.to_markdown()


def test_validate_report_allows_missing_data_disclaimer():
    report = validate_report_text(
        "当前未提供成交量/成交额/北向资金数据，无法进行流动性验证。",
        evidence=EvidenceAvailability(turnover_data=False, northbound_data=False),
    )

    assert report.ok


def test_validate_report_allows_volume_terms_as_future_validation_condition():
    report = validate_report_text(
        "失效条件：中微公司、北方华创等龙头放量企稳后，重新评估半导体设备方向。",
        evidence=EvidenceAvailability(turnover_data=False),
    )

    assert report.ok


def test_validate_report_flags_actual_volume_claim_without_evidence():
    report = validate_report_text(
        "今日半导体板块放量下跌，说明承接不足。",
        evidence=EvidenceAvailability(turnover_data=False),
    )

    assert not report.ok
    assert "成交量/成交额/量能" in report.to_markdown()


def test_validate_report_allows_negated_clearance_phrase():
    report = validate_report_text(
        "这更像阶段性减仓与高低切换，不是清仓离场。",
        evidence=EvidenceAvailability(),
    )

    assert report.ok


def test_validate_report_does_not_treat_aggregate_weight_as_single_fund_cap():
    report = validate_report_text(
        "建议：若017811、014143、025209三只权重之和超过60%，考虑降低半导体总仓位。",
        rules=extract_expert_rules("单只基金权重 ≤ 25%"),
        evidence=EvidenceAvailability(),
    )

    assert report.ok


def test_validate_report_does_not_treat_performance_percent_as_weight():
    report = validate_report_text(
        "| 017811 | 东方人工智能主题混合C | Sharpe 8.47，近1月涨幅42.84%，年化波动44.78%。 | 23.17% | 21% | 小幅减持 | 近1月涨幅大，降低组合波动 | |",
        rules=extract_expert_rules("单只基金权重 ≤ 25%"),
        evidence=EvidenceAvailability(),
    )

    assert report.ok


def test_validate_report_flags_available_data_marked_missing():
    report = validate_report_text(
        "因未接入成交量/成交额数据，无法验证量价关系。\n未接入：北向资金。",
        evidence=EvidenceAvailability(turnover_data=True, northbound_data=True),
    )

    md = report.to_markdown()
    assert not report.ok
    assert "已接入" in md
    assert "成交量/成交额/量价" in md
    assert "北向资金" in md


def test_validate_report_flags_suggested_weight_cell_only():
    report = validate_report_text(
        "\n".join([
            "| 基金 | 当前权重 | 建议权重 | 操作 | 理由 |",
            "|---|---:|---:|---|---|",
            "| 017811 | 23.17% | 28% | 增持 | 近1月涨幅42.84%，弹性强 |",
        ]),
        rules=extract_expert_rules("单只基金权重 ≤ 25%"),
        evidence=EvidenceAvailability(),
    )

    assert not report.ok
    assert "28.00%" in report.to_markdown()
