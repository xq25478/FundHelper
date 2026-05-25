from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class FundType(str, Enum):
    EQUITY = "equity"        # 股票型
    HYBRID = "hybrid"        # 混合型
    BOND = "bond"            # 债券型
    MONEY = "money"          # 货币型
    INDEX = "index"          # 指数型
    QDII = "qdii"            # QDII
    FOF = "fof"
    OTHER = "other"


@dataclass(slots=True)
class Manager:
    name: str
    start_date: date | None = None
    tenure_days: int | None = None
    bio: str | None = None


@dataclass(slots=True)
class Fund:
    code: str                           # 6位代码，如 "005827"
    name: str
    fund_type: FundType = FundType.OTHER
    inception_date: date | None = None
    aum: float | None = None            # 最新规模（元）
    benchmark: str | None = None
    company: str | None = None
    managers: list[Manager] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
