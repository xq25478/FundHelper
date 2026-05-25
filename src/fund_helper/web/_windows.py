"""Window key parsing and date math for the holdings page."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

# (key, label, days)
# (key, label, days)；days=0 表示"当天"快照模式
WINDOWS: list[tuple[str, str, int]] = [
    ("today", "当天", 0),
    ("7d",    "7天",   7),
    ("2w",    "2周",   14),
    ("1m",    "1月",   31),
    ("2m",    "2月",   62),
    ("3m",    "3月",   93),
    ("6m",    "6月",   180),
]
DEFAULT_WINDOW = "6m"
WINDOW_KEYS = {k for k, _, _ in WINDOWS}


@dataclass(slots=True)
class WindowSpec:
    key: str
    label: str
    days: int

    @property
    def start(self) -> date:
        return date.today() - timedelta(days=self.days)


def resolve_window(key: str | None) -> WindowSpec:
    if key not in WINDOW_KEYS:
        key = DEFAULT_WINDOW
    label, days = next((lbl, d) for k, lbl, d in WINDOWS if k == key)
    return WindowSpec(key=key, label=label, days=days)
