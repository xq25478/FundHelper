"""板块收藏 service：增删查 + 置顶序."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from ..config import AppConfig
from ..storage import connect

log = logging.getLogger(__name__)
CST = timezone(timedelta(hours=8))


class SectorFavoriteService:
    def __init__(self, cfg: AppConfig) -> None:
        self.conn = connect(cfg.data_dir / "fund.db")

    def add(self, category: str, label: str, name: str) -> None:
        now = datetime.now(CST).isoformat(timespec="seconds")
        # 新收藏排在最前：sort_order = min - 1
        row = self.conn.execute("SELECT COALESCE(MIN(sort_order), 0) FROM sector_favorite").fetchone()
        new_order = (row[0] or 0) - 1
        self.conn.execute(
            """INSERT INTO sector_favorite(category,label,name,sort_order,added_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(category,label) DO UPDATE SET
                 name=excluded.name, sort_order=excluded.sort_order, added_at=excluded.added_at""",
            (category, label, name, new_order, now),
        )
        self.conn.commit()
        log.info("sector_favorite add %s/%s name=%s", category, label, name)

    def remove(self, category: str, label: str) -> None:
        self.conn.execute(
            "DELETE FROM sector_favorite WHERE category=? AND label=?",
            (category, label),
        )
        self.conn.commit()
        log.info("sector_favorite remove %s/%s", category, label)

    def list_keys(self) -> list[tuple[str, str, str]]:
        """[(category, label, name), ...] in display order."""
        rows = self.conn.execute(
            "SELECT category,label,name FROM sector_favorite ORDER BY sort_order ASC, added_at ASC"
        ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    def is_fav(self, category: str, label: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM sector_favorite WHERE category=? AND label=?",
            (category, label),
        ).fetchone()
        return row is not None
