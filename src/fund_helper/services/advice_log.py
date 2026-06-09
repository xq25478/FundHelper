"""Advice report archive and lightweight review helpers."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


LOG_PATH = Path("reports/advice/advice_log.jsonl")
_ACTION_RE = re.compile(r"(增持|减持|加仓|减仓|持有|观察|再平衡)")
_CODE_RE = re.compile(r"\b\d{6}\b")


@dataclass(slots=True)
class AdviceEntry:
    generated_at: str
    target: str
    title: str
    report_path: str
    sha256: str
    action_lines: list[str]
    guardrail_findings: int
    metadata: dict[str, Any] = field(default_factory=dict)


def record_report(
    *,
    report_path: Path,
    target: str,
    title: str,
    text: str,
    guardrail_findings: int = 0,
    metadata: dict[str, Any] | None = None,
) -> AdviceEntry:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    meta = dict(metadata or {})
    meta.setdefault("holdings", _holdings_snapshot())
    entry = AdviceEntry(
        generated_at=datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
        target=target,
        title=title,
        report_path=str(report_path),
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        action_lines=_extract_action_lines(text),
        guardrail_findings=guardrail_findings,
        metadata=meta,
    )
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
    return entry


def load_entries(limit: int | None = None) -> list[AdviceEntry]:
    if not LOG_PATH.exists():
        return []
    rows: list[AdviceEntry] = []
    for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            raw.setdefault("metadata", {})
            rows.append(AdviceEntry(**raw))
        except Exception:
            continue
    rows.sort(key=lambda x: x.generated_at, reverse=True)
    return rows[:limit] if limit else rows


def review_entries(cfg, *, horizon_days: int = 7, limit: int = 20) -> list[dict[str, Any]]:
    from ..portfolio.holdings import load_holdings
    from .nav_service import NavService

    holdings = load_holdings()
    weights = holdings.normalized_weights()
    svc = NavService(cfg)
    today = date.today()
    out: list[dict[str, Any]] = []
    for entry in load_entries(limit=limit):
        generated = _parse_date(entry.generated_at)
        if generated is None:
            continue
        age = (today - generated).days
        if age < horizon_days:
            out.append({
                "generated_at": entry.generated_at,
                "target": entry.target,
                "age_days": age,
                "portfolio_return": None,
                "status": f"未满 {horizon_days} 天",
            })
            continue
        ret = _portfolio_return_since(svc, weights, generated, today)
        out.append({
            "generated_at": entry.generated_at,
            "target": entry.target,
            "age_days": age,
            "portfolio_return": ret,
            "status": "可复盘" if ret is not None else "净值不足",
        })
    return out


def _extract_action_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or not _ACTION_RE.search(line):
            continue
        if _CODE_RE.search(line) or "建议" in line or "操作" in line:
            lines.append(line[:240])
    return lines[:40]


def _holdings_snapshot() -> list[dict[str, Any]]:
    try:
        from ..portfolio.holdings import load_holdings

        return [
            {"code": p.code, "name": p.name, "weight": p.weight}
            for p in load_holdings().positions
        ]
    except Exception:
        return []


def _parse_date(value: str) -> date | None:
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None


def _portfolio_return_since(svc, weights: dict[str, float], start: date, end: date) -> float | None:
    total = 0.0
    seen = 0.0
    for code, weight in weights.items():
        try:
            series, _ = svc.get_nav_range(code, start, end, force_refresh=False)
        except Exception:
            continue
        frame = series.frame
        if frame.empty or "unit_nav" not in frame.columns:
            continue
        unit = frame["unit_nav"].dropna()
        if len(unit) < 2 or not unit.iloc[0]:
            continue
        total += float(unit.iloc[-1] / unit.iloc[0] - 1) * weight
        seen += weight
    if seen <= 0:
        return None
    return total / seen
