"""Expert guardrails for AI-generated fund analysis.

The model may write the prose, but portfolio constraints and evidence limits
belong in deterministic code. This module checks generated reports for common
violations before they are persisted or pushed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable


@dataclass(slots=True)
class ExpertRules:
    max_single_fund_weight: float = 0.25
    max_drawdown_tolerance: float = -0.30
    allow_exact_trade_timing: bool = False

    @property
    def max_weight_pct(self) -> float:
        return self.max_single_fund_weight * 100


@dataclass(slots=True)
class EvidenceAvailability:
    peer_data: bool = False
    valuation_data: bool = False
    northbound_data: bool = False
    margin_data: bool = False
    turnover_data: bool = False
    main_fund_flow_data: bool = False
    benchmark_data: bool = False
    fund_profile_data: bool = False

    def forbidden_topics(self) -> dict[str, tuple[bool, tuple[str, ...]]]:
        return {
            "同类均值/同类排名": (self.peer_data, ("同类均值", "同类排名", "同类基金", "同类平均")),
            "估值分位": (self.valuation_data, ("估值分位", "估值百分位", "估值处于")),
            "北向资金": (self.northbound_data, ("北向资金", "陆股通")),
            "融资融券": (self.margin_data, ("融资融券", "两融余额", "融资余额")),
            "成交量/成交额/量能": (
                self.turnover_data,
                ("成交额", "成交量", "放量", "缩量", "量能"),
            ),
            "主力资金流": (
                self.main_fund_flow_data,
                ("资金流入", "资金流出", "主力资金", "主力净流入", "主力净流出"),
            ),
        }


@dataclass(slots=True)
class GuardrailFinding:
    severity: str
    message: str
    snippet: str = ""


@dataclass(slots=True)
class GuardrailReport:
    findings: list[GuardrailFinding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings

    def add(self, severity: str, message: str, snippet: str = "") -> None:
        self.findings.append(GuardrailFinding(severity, message, snippet.strip()))

    def to_markdown(self) -> str:
        if self.ok:
            return (
                "## 自动校验\n\n"
                "- 通过：未发现建议权重越界或无数据支撑的高风险表述。\n"
            )
        lines = [
            "## 自动校验",
            "",
            "以下结论由规则引擎复核生成；若与上文模型建议冲突，以本节为准。",
            "",
        ]
        for item in self.findings:
            suffix = f"（原文：{item.snippet}）" if item.snippet else ""
            lines.append(f"- [{item.severity}] {item.message}{suffix}")
        return "\n".join(lines) + "\n"


_PERCENT_RE = re.compile(r"(?P<num>\d+(?:\.\d+)?)\s*%")
_CODE_RE = re.compile(r"(?<!\d)\d{6}(?!\d)")
_WEIGHT_CONTEXT = (
    "建议权重", "目标权重", "调整后权重", "建议仓位", "目标仓位", "调整后仓位",
    "增至", "升至", "降至", "加至", "减至", "调整至", "恢复至", "维持在",
)
_ACTION_WORDS = ("建议", "增持", "减持", "加仓", "减仓", "调仓", "再平衡", "目标")
_EXACT_TIMING_WORDS = ("立即买入", "立即卖出", "满仓", "清仓", "必买", "必卖")
_NEGATED_TRADE_PREFIXES = ("不是", "并非", "非", "不等于", "不建议", "避免", "禁止", "不得", "不应")
_SUGGESTED_WEIGHT_HEADERS = (
    "建议权重", "目标权重", "调整后权重", "建议仓位", "目标仓位", "调整后仓位",
)


def extract_expert_rules(profile_text: str | None) -> ExpertRules:
    """Best-effort parser for constraints embedded in buyer profile prose/YAML."""
    text = profile_text or ""
    rules = ExpertRules()

    patterns = (
        r"max_single_fund_weight\s*:\s*(0?\.\d+|\d+(?:\.\d+)?)",
        r"单只基金权重\s*[≤<=]\s*(\d+(?:\.\d+)?)\s*%",
        r"单只.*?权重.*?不(?:超过|高于|大于)\s*(\d+(?:\.\d+)?)\s*%",
    )
    for pat in patterns:
        m = re.search(pat, text, flags=re.I | re.S)
        if not m:
            continue
        val = float(m.group(1))
        rules.max_single_fund_weight = val if val <= 1 else val / 100
        break

    dd_patterns = (
        r"max_drawdown_tolerance\s*:\s*(-?0?\.\d+|-?\d+(?:\.\d+)?)",
        r"最大回撤.*?(-\d+(?:\.\d+)?)\s*%",
    )
    for pat in dd_patterns:
        m = re.search(pat, text, flags=re.I | re.S)
        if not m:
            continue
        val = float(m.group(1))
        rules.max_drawdown_tolerance = val if abs(val) <= 1 else val / 100
        break

    return rules


def evidence_policy_markdown(evidence: EvidenceAvailability) -> str:
    missing = [
        label for label, (available, _terms) in evidence.forbidden_topics().items()
        if not available
    ]
    lines = [
        "### 证据边界（强约束）",
        "- 未在下方事实包中出现的数据，不得作为结论依据。",
        "- 若必须讨论缺失数据，只能写成“缺数据，需后续补充观察”。",
    ]
    if missing:
        lines.append("- 当前未提供，禁止直接引用或据此下结论的数据域：" + "、".join(missing))
    if not evidence.peer_data:
        lines.append("- 未提供同类基金样本，因此不得写“跑赢/跑输同类均值或同类排名”。")
    return "\n".join(lines)


def risk_rules_markdown(rules: ExpertRules) -> str:
    return "\n".join([
        "### 投资画像硬约束",
        f"- 单只基金建议权重不得超过 {rules.max_weight_pct:.0f}%。",
        f"- 组合最大回撤容忍线为 {rules.max_drawdown_tolerance:.0%}；超过时必须降级为风控复核。",
        "- 不得给出确定性买卖点、满仓/清仓指令或价格目标。",
        "- 数据缺失、过旧或互相矛盾时，操作建议只能是持有观察、分批、等待复核。",
    ])


def validate_report_text(
    text: str,
    *,
    rules: ExpertRules | None = None,
    evidence: EvidenceAvailability | None = None,
) -> GuardrailReport:
    rules = rules or ExpertRules()
    evidence = evidence or EvidenceAvailability()
    report = GuardrailReport()
    _check_weight_caps(text, rules, report)
    _check_available_data_marked_missing(text, evidence, report)
    _check_missing_evidence_claims(text, evidence, report)
    _check_trade_timing(text, rules, report)
    return report


def _check_weight_caps(text: str, rules: ExpertRules, report: GuardrailReport) -> None:
    table_header: list[str] | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            table_header = None
            continue
        row = _split_markdown_row(line)
        if row is not None:
            if _is_markdown_separator(row):
                continue
            if any(any(key in cell for key in _SUGGESTED_WEIGHT_HEADERS) for cell in row):
                table_header = row
                continue
            if "%" not in line:
                continue
            checked = False
            if table_header:
                checked = _check_table_weight_cells(row, table_header, line, rules, report)
            if checked:
                continue
            inferred_cells = _infer_weight_cells_from_action_row(row)
            if inferred_cells:
                _add_weight_findings(inferred_cells, line, rules, report)
                continue
        elif "%" not in line:
            table_header = None
            continue

        has_action = any(word in line for word in _ACTION_WORDS)
        has_weight_context = any(word in line for word in _WEIGHT_CONTEXT)
        codes = set(_CODE_RE.findall(line))
        has_code = bool(codes)
        if not has_action:
            continue
        if not has_weight_context and not has_code:
            continue
        if len(codes) > 1 and any(word in line for word in ("之和", "合计", "总权重", "总仓位", "整体仓位")):
            continue
        candidates = _candidate_weight_texts(line)
        if candidates:
            _add_weight_findings(candidates, line, rules, report)


def _split_markdown_row(line: str) -> list[str] | None:
    if not line.startswith("|") or "|" not in line[1:]:
        return None
    return [cell.strip() for cell in line.strip("|").split("|")]


def _is_markdown_separator(row: list[str]) -> bool:
    return bool(row) and all(cell and set(cell) <= {"-", ":"} for cell in row)


def _check_table_weight_cells(
    row: list[str],
    header: list[str],
    line: str,
    rules: ExpertRules,
    report: GuardrailReport,
) -> bool:
    if len(row) != len(header):
        return False
    cells = [
        row[idx]
        for idx, title in enumerate(header)
        if any(key in title for key in _SUGGESTED_WEIGHT_HEADERS)
    ]
    if not cells:
        return False
    _add_weight_findings(cells, line, rules, report)
    return True


def _infer_weight_cells_from_action_row(row: list[str]) -> list[str]:
    """Best-effort fallback for markdown rows without their header.

    In operation tables the suggested weight is usually immediately before the
    action cell. This avoids treating performance/volatility percentages in a
    long diagnosis cell as suggested weights.
    """
    for idx, cell in enumerate(row):
        if any(word in cell for word in _ACTION_WORDS):
            if idx > 0 and "%" in row[idx - 1]:
                return [row[idx - 1]]
    return []


def _candidate_weight_texts(line: str) -> list[str]:
    out: list[str] = []
    for key in _WEIGHT_CONTEXT:
        for match in re.finditer(re.escape(key), line):
            start = max(0, match.start() - 12)
            end = min(len(line), match.end() + 28)
            snippet = line[start:end]
            if "%" in snippet:
                out.append(snippet)
    return out


def _add_weight_findings(
    candidate_texts: list[str],
    line: str,
    rules: ExpertRules,
    report: GuardrailReport,
) -> None:
    nums: list[float] = []
    for text in candidate_texts:
        nums.extend(float(m.group("num")) for m in _PERCENT_RE.finditer(text))
    over = [n for n in nums if n > rules.max_weight_pct + 1e-9]
    if not over:
        return
    code = (_CODE_RE.search(line).group(0) if _CODE_RE.search(line) else "相关基金")
    report.add(
        "P1",
        f"{code} 的建议权重出现 {max(over):.2f}%，超过单只基金上限 {rules.max_weight_pct:.0f}%；"
        f"超过部分应视为无效，建议上限按 {rules.max_weight_pct:.0f}% 处理。",
        line,
    )


def _check_missing_evidence_claims(
    text: str,
    evidence: EvidenceAvailability,
    report: GuardrailReport,
) -> None:
    for label, (available, terms) in evidence.forbidden_topics().items():
        if available:
            continue
        for snippet in _matching_lines(text, terms):
            if _is_missing_evidence_disclaimer(snippet):
                continue
            report.add(
                "P2",
                f"报告引用了“{label}”，但本次事实包未提供该数据；相关结论应降级为观察。",
                snippet,
            )


def _check_available_data_marked_missing(
    text: str,
    evidence: EvidenceAvailability,
    report: GuardrailReport,
) -> None:
    checks = (
        (
            evidence.peer_data,
            "同类基金样本/同类排名",
            (r"未(?:接入|提供).*同类", r"同类.*(?:缺失|未接入|未提供)"),
        ),
        (
            evidence.valuation_data,
            "估值分位",
            (r"未(?:接入|提供).*估值分位", r"估值分位.*(?:缺失|未接入|未提供)"),
        ),
        (
            evidence.northbound_data,
            "北向资金",
            (r"未(?:接入|提供).*北向", r"北向资金.*(?:缺失|未接入|未提供)"),
        ),
        (
            evidence.margin_data,
            "融资融券",
            (r"未(?:接入|提供).*融资融券", r"融资融券.*(?:缺失|未接入|未提供)"),
        ),
        (
            evidence.turnover_data,
            "成交量/成交额/量价",
            (
                r"未(?:接入|提供).*成交[量额]",
                r"成交[量额].*(?:缺失|未接入|未提供)",
                r"未(?:接入|提供).*量价",
                r"无法(?:定量)?(?:验证|判断).*量价",
            ),
        ),
        (
            evidence.main_fund_flow_data,
            "资金流",
            (
                r"未(?:接入|提供).*资金流",
                r"资金流.*(?:缺失|未接入|未提供)",
                r"无法引用资金流",
            ),
        ),
    )
    for available, label, patterns in checks:
        if not available:
            continue
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if any(re.search(pattern, line) for pattern in patterns):
                report.add(
                    "P1",
                    f"报告把已接入的“{label}”写成缺失；应以程序数据可信度表为准。",
                    line,
                )
                break


def _check_trade_timing(text: str, rules: ExpertRules, report: GuardrailReport) -> None:
    if rules.allow_exact_trade_timing:
        return
    for snippet in _matching_lines(text, _EXACT_TIMING_WORDS):
        if _is_negated_trade_instruction(snippet):
            continue
        report.add(
            "P2",
            "报告包含确定性交易指令；当前定位是研究辅助，不应输出强制买卖动作。",
            snippet,
        )


def _is_negated_trade_instruction(line: str) -> bool:
    for word in _EXACT_TIMING_WORDS:
        idx = line.find(word)
        if idx < 0:
            continue
        prefix = line[max(0, idx - 8):idx]
        if any(term in prefix for term in _NEGATED_TRADE_PREFIXES):
            return True
    return False


def _matching_lines(text: str, terms: Iterable[str]) -> list[str]:
    out: list[str] = []
    in_auto_check = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.match(r"^#{0,6}\s*自动校验\s*$", line):
            in_auto_check = True
            continue
        if in_auto_check:
            if re.match(r"^#{1,6}\s+", line):
                in_auto_check = False
            else:
                continue
        if line.startswith("- [P"):
            continue
        if any(term in line for term in terms):
            out.append(line[:180])
    return out


def _is_missing_evidence_disclaimer(line: str) -> bool:
    disclaimer_terms = (
        "未提供", "未接入", "缺", "缺失", "无法", "不能", "禁止", "不得",
        "不应", "待补充", "需后续", "数据为空", "无本地", "无可用",
        "后续", "未来", "若", "如果", "关注", "跟踪", "观察", "反证", "触发",
        "未出现", "没有证据", "无法确认", "待", "条件", "失效", "验证", "确认",
        "配合", "企稳", "收复",
    )
    return any(term in line for term in disclaimer_terms)
