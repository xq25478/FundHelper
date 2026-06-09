from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(slots=True)
class Position:
    code: str
    name: str
    weight: float


@dataclass(slots=True)
class Holdings:
    name: str
    as_of: str | None
    source: str | None
    positions: list[Position]

    @property
    def weights(self) -> dict[str, float]:
        return {p.code: p.weight for p in self.positions}

    def normalized_weights(self) -> dict[str, float]:
        total = sum(p.weight for p in self.positions) or 1.0
        return {p.code: p.weight / total for p in self.positions}


def _parse_holdings(raw: dict) -> Holdings:
    items = raw.get("holdings", raw.get("positions", []))
    return Holdings(
        name=raw.get("name", "default"),
        as_of=raw.get("as_of"),
        source=raw.get("source"),
        positions=[
            Position(code=str(it["code"]), name=it.get("name", ""), weight=float(it["weight"]))
            for it in items
        ],
    )


def load_holdings(path: str | Path = "configs/holdings.yaml") -> Holdings:
    # 优先从 config.yml 读取
    user_path = Path("config.yml")
    if user_path.exists():
        raw = yaml.safe_load(user_path.read_text(encoding="utf-8")) or {}
        hr = raw.get("holdings")
        if hr and (hr.get("holdings") or hr.get("positions")):
            return _parse_holdings(hr)
    return _parse_holdings(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})
