"""板块行情 service：盘中 60s / 非盘中 12h 缓存."""
from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Any

from ..config import AppConfig
from ..datasource import sector_sina as src
from ..storage import connect

log = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))
SESSION_TTL = 60
OFF_SESSION_TTL = 12 * 3600


def _now() -> datetime:
    return datetime.now(CST)


def _is_session(now: datetime | None = None) -> bool:
    now = now or _now()
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    return (9 * 60 + 30 <= hm <= 11 * 60 + 30) or (13 * 60 <= hm <= 15 * 60)


# 我关注的关键词（持仓题材 + buyer.md + 用户点名）
WATCHLIST_KEYWORDS: list[str] = [
    # 持仓题材
    "AI", "人工智能", "算力", "光模块", "光通信", "CPO",
    "半导体", "芯片", "存储", "存储芯片", "国产芯片", "EDA",
    "电力", "电网", "特高压", "智能电网",
    "通信", "5G", "5G概念",
    # buyer.md 补充
    "国产替代", "自主可控",
    # 用户点名
    "新能源", "新能源车", "锂电", "锂矿", "锂电池", "电池",
    "钠电池", "固态电池", "BC电池", "钒电池",
    "机器人", "机器人概念", "人形机器人", "机器视觉",
    "商业航天", "航天", "军工航天",
]


@dataclass(slots=True)
class SectorRow:
    category: str
    label: str
    name: str
    companies: int | None
    avg_price: float | None
    delta: float | None
    pct: float | None
    total_amt: float | None
    leader_code: str | None
    leader_name: str | None
    leader_pct: float | None
    leader_price: float | None
    leader_delta: float | None
    is_watch: bool = False
    is_fav: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SectorPanel:
    rows_by_category: dict[str, list[SectorRow]]
    watch_rows: list[SectorRow]
    favorite_rows: list[SectorRow]
    refreshed_at: str
    is_session: bool
    source_used: str


_BATCH_TS: dict[str, datetime] = {}


def _is_watch(name: str) -> bool:
    return any(kw in name for kw in WATCHLIST_KEYWORDS)


class SectorService:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.conn = connect(cfg.data_dir / "fund.db")

    def get_panel(self, *, force_refresh: bool = False) -> SectorPanel:
        ttl = SESSION_TTL if _is_session() else OFF_SESSION_TTL
        last = _BATCH_TS.get("sector")
        within = last is not None and (_now() - last).total_seconds() <= ttl
        source = "sqlite"
        if force_refresh or not within:
            ok = False
            for cat in src.CATEGORIES:
                try:
                    rows = src.fetch_sector_spot(cat)
                    if rows:
                        self._upsert(rows)
                        ok = True
                        log.info("sector fetched cat=%s rows=%d", cat, len(rows))
                except Exception as e:
                    log.warning("sector fetch failed cat=%s: %s", cat, e)
            if ok:
                _BATCH_TS["sector"] = _now()
                source = "sina"

        rows_by_cat: dict[str, list[SectorRow]] = {}
        for cat in src.CATEGORIES:
            rows_by_cat[cat] = self._load(cat)

        # favorites
        from .sector_favorite_service import SectorFavoriteService
        fav_keys = set(
            (category, label)
            for (category, label, _name) in SectorFavoriteService(self.cfg).list_keys()
        )
        for cat in src.CATEGORIES:
            for r in rows_by_cat[cat]:
                if (r.category, r.label) in fav_keys:
                    r.is_fav = True

        watch: list[SectorRow] = []
        for cat in src.CATEGORIES:
            for r in rows_by_cat[cat]:
                if r.is_watch:
                    watch.append(r)
        watch.sort(key=lambda r: (r.pct is None, -(r.pct or 0)))

        favs: list[SectorRow] = []
        for cat in src.CATEGORIES:
            for r in rows_by_cat[cat]:
                if r.is_fav:
                    favs.append(r)
        favs.sort(key=lambda r: (r.pct is None, -(r.pct or 0)))

        return SectorPanel(
            rows_by_category=rows_by_cat,
            watch_rows=watch,
            favorite_rows=favs,
            refreshed_at=_now().strftime("%Y-%m-%d %H:%M:%S"),
            is_session=_is_session(),
            source_used=source,
        )

    def _upsert(self, rows: list[dict]) -> None:
        now = _now().isoformat(timespec="seconds")
        data = [
            (r["category"], r["label"], r["name"], r["companies"], r["avg_price"],
             r["delta"], r["pct"], r["total_vol"], r["total_amt"],
             r["leader_code"], r["leader_name"], r["leader_pct"],
             r["leader_price"], r["leader_delta"], now)
            for r in rows
        ]
        self.conn.execute("BEGIN")
        try:
            self.conn.executemany(
                """INSERT INTO sector_snapshot
                     (category,label,name,companies,avg_price,delta,pct,
                      total_vol,total_amt,leader_code,leader_name,leader_pct,
                      leader_price,leader_delta,fetched_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(category,label) DO UPDATE SET
                     name=excluded.name, companies=excluded.companies,
                     avg_price=excluded.avg_price, delta=excluded.delta,
                     pct=excluded.pct, total_vol=excluded.total_vol,
                     total_amt=excluded.total_amt,
                     leader_code=excluded.leader_code,
                     leader_name=excluded.leader_name,
                     leader_pct=excluded.leader_pct,
                     leader_price=excluded.leader_price,
                     leader_delta=excluded.leader_delta,
                     fetched_at=excluded.fetched_at""",
                data,
            )
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def _load(self, category: str) -> list[SectorRow]:
        cur = self.conn.execute(
            """SELECT category,label,name,companies,avg_price,delta,pct,
                      total_amt,leader_code,leader_name,leader_pct,
                      leader_price,leader_delta
               FROM sector_snapshot
               WHERE category=?
               ORDER BY pct DESC""",
            (category,),
        )
        out: list[SectorRow] = []
        for row in cur.fetchall():
            name = row[2]
            out.append(SectorRow(
                category=row[0], label=row[1], name=name,
                companies=row[3], avg_price=row[4],
                delta=row[5], pct=row[6], total_amt=row[7],
                leader_code=row[8], leader_name=row[9],
                leader_pct=row[10], leader_price=row[11], leader_delta=row[12],
                is_watch=_is_watch(name),
            ))
        return out
