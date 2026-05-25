"""News aggregation: fetch -> classify -> sentiment-score -> sqlite cache."""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from fund_helper.datasource import news_akshare as src
from fund_helper.services.news_relevance import RelevanceScorer
from fund_helper.storage.retention import prune_news

log = logging.getLogger(__name__)
TZ = ZoneInfo("Asia/Shanghai")

# 情绪关键词（来自 .skills/fund-advisor.skill.md §4 消息面情绪）
POS_WORDS = [
    "利好", "超预期", "增长", "突破", "创新高", "涨停", "爆发", "回暖", "复苏", "反弹",
    "扩张", "订单", "签约", "量产", "增持", "减税", "降准", "降息", "稳增长", "刺激",
    "新政", "支持", "扶持", "重组", "并购", "分红", "回购", "盈利", "扭亏", "大涨",
]
NEG_WORDS = [
    "利空", "不及预期", "下滑", "跌停", "暴跌", "亏损", "巨亏", "违规", "处罚", "退市",
    "诉讼", "调查", "风险", "警示", "制裁", "加息", "紧缩", "萎缩", "降级", "减持",
    "裁员", "破产", "爆雷", "违约", "下调", "预亏", "停牌", "大跌", "崩盘", "冲突",
]
# 美股 / 海外动态关键词
US_WORDS = [
    "美股", "道指", "道琼斯", "纳指", "纳斯达克", "标普", "标普500",
    "美联储", "鲍威尔", "Fed", "FOMC", "美元", "美债", "美国国债",
    "美债收益率", "非农", "CPI", "耶伦", "特朗普", "拜登",
    "华尔街", "美国财政部", "SEC", "苹果", "英伟达", "特斯拉", "微软",
    "谷歌", "亚马逊", "Meta",
]

# 政策面关键词
POLICY_WORDS = [
    "央行", "证监会", "发改委", "财政部", "国常会", "国务院", "工信部", "央企",
    "降准", "降息", "MLF", "LPR", "稳增长", "扶持", "新政", "监管", "整顿",
    "减税", "退税", "补贴", "新闻联播", "习近平", "李强", "金融工作会议", "政治局",
]

CATEGORIES = ("sentiment", "message", "policy", "us_market")
CATEGORY_LABELS = {
    "sentiment": "情绪面",
    "message": "消息面",
    "policy": "政策面",
    "us_market": "美股动态",
}

TTL_SECONDS = 600  # 10 min in-session


@dataclass
class NewsItem:
    id: str
    category: str
    source: str
    title: str
    content: str
    url: str | None
    published_at: str
    sentiment: int
    pos_hits: list[str]
    neg_hits: list[str]
    relevance_score: float
    themes: list[str]
    kw_hits: list[str]
    fetched_at: str

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["category_label"] = CATEGORY_LABELS.get(self.category, self.category)
        return d


def _hash_id(category: str, source: str, title: str, published_at: str) -> str:
    h = hashlib.sha1()
    h.update(f"{category}|{source}|{title}|{published_at}".encode("utf-8"))
    return h.hexdigest()[:16]


def _score(text: str) -> tuple[int, list[str], list[str]]:
    t = text or ""
    pos = [w for w in POS_WORDS if w in t]
    neg = [w for w in NEG_WORDS if w in t]
    delta = len(pos) - len(neg)
    if delta > 0:
        return 1, pos, neg
    if delta < 0:
        return -1, pos, neg
    return 0, pos, neg


def _is_policy(text: str) -> bool:
    return any(w in (text or "") for w in POLICY_WORDS)


def _is_us(text: str) -> bool:
    return any(w in (text or "") for w in US_WORDS)


def _classify(*, base_category: str, title: str, content: str) -> str:
    """Promotes message-source items to policy or us_market when keywords hit."""
    if base_category == "us_market":
        return "us_market"
    if base_category == "policy":
        return "policy"
    text = f"{title} {content}"
    if _is_us(text):
        return "us_market"
    if _is_policy(text):
        return "policy"
    return base_category


def _build_relevance_scorer() -> RelevanceScorer:
    try:
        from fund_helper.portfolio.holdings import load_holdings

        return RelevanceScorer(load_holdings().normalized_weights())
    except Exception as e:  # noqa: BLE001
        log.warning("news relevance holdings load failed: %s", e)
        return RelevanceScorer()


def _make_items(
    rows: Iterable[dict[str, Any]],
    *,
    base_category: str,
    fetched_at: str,
    scorer: RelevanceScorer,
) -> list[NewsItem]:
    out: list[NewsItem] = []
    for r in rows:
        title = r.get("title", "") or ""
        content = r.get("content", "") or ""
        if not title and not content:
            continue
        cat = _classify(base_category=base_category, title=title, content=content)
        score, pos, neg = _score(title + " " + content)
        rel = scorer.score(f"{title} {content}")
        nid = _hash_id(cat, r["source"], title, r["published_at"])
        out.append(NewsItem(
            id=nid,
            category=cat,
            source=r["source"],
            title=title,
            content=content,
            url=r.get("url"),
            published_at=r["published_at"],
            sentiment=score,
            pos_hits=pos,
            neg_hits=neg,
            relevance_score=rel.score,
            themes=rel.themes,
            kw_hits=rel.keywords,
            fetched_at=fetched_at,
        ))
    return out


def _upsert(conn: sqlite3.Connection, items: list[NewsItem]) -> None:
    rows = [
        (
            it.id, it.category, it.source, it.title, it.content, it.url,
            it.published_at, it.sentiment,
            json.dumps(it.pos_hits, ensure_ascii=False),
            json.dumps(it.neg_hits, ensure_ascii=False),
            it.relevance_score,
            json.dumps(it.themes, ensure_ascii=False),
            json.dumps(it.kw_hits, ensure_ascii=False),
            it.fetched_at,
        )
        for it in items
    ]
    conn.executemany(
        """
        INSERT INTO news_item(id,category,source,title,content,url,published_at,
                              sentiment,pos_hits,neg_hits,relevance_score,themes,kw_hits,fetched_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            content=excluded.content,
            sentiment=excluded.sentiment,
            pos_hits=excluded.pos_hits,
            neg_hits=excluded.neg_hits,
            relevance_score=excluded.relevance_score,
            themes=excluded.themes,
            kw_hits=excluded.kw_hits,
            fetched_at=excluded.fetched_at
        """,
        rows,
    )


def _select(conn: sqlite3.Connection, category: str, limit: int) -> list[NewsItem]:
    cur = conn.execute(
        """
        SELECT id,category,source,title,content,url,published_at,sentiment,
               pos_hits,neg_hits,relevance_score,themes,kw_hits,fetched_at
        FROM news_item
        WHERE category=?
        ORDER BY published_at DESC, fetched_at DESC
        LIMIT ?
        """,
        (category, limit),
    )
    out: list[NewsItem] = []
    for row in cur.fetchall():
        out.append(NewsItem(
            id=row[0], category=row[1], source=row[2], title=row[3],
            content=row[4], url=row[5], published_at=row[6],
            sentiment=int(row[7] or 0),
            pos_hits=json.loads(row[8] or "[]"),
            neg_hits=json.loads(row[9] or "[]"),
            relevance_score=float(row[10] or 0),
            themes=json.loads(row[11] or "[]"),
            kw_hits=json.loads(row[12] or "[]"),
            fetched_at=row[13],
        ))
    return out


def _last_fetched_at(conn: sqlite3.Connection) -> datetime | None:
    cur = conn.execute("SELECT MAX(fetched_at) FROM news_item")
    row = cur.fetchone()
    if not row or not row[0]:
        return None
    try:
        return datetime.fromisoformat(row[0])
    except ValueError:
        return None


def _fetch_all() -> list[NewsItem]:
    fetched_at = datetime.now(TZ).isoformat(timespec="seconds")
    items: list[NewsItem] = []
    scorer = _build_relevance_scorer()

    fetchers = [
        ("message",   src.fetch_cls_telegraph,      {"symbol": "全部"}),
        ("message",   src.fetch_em_global,          {}),
        ("message",   src.fetch_futu_global,        {}),
        ("message",   src.fetch_ths_global,         {}),
        ("message",   src.fetch_sina_global,        {}),
        ("message",   src.fetch_wallstreetcn_lives, {}),
        ("policy",    src.fetch_cctv,               {}),
        ("message",   src.fetch_cx_main,            {}),
        ("us_market", src.fetch_us_index_overnight, {}),
    ]
    for base_cat, fn, kwargs in fetchers:
        t = time.time()
        try:
            rows = fn(**kwargs)
            items.extend(_make_items(
                rows,
                base_category=base_cat,
                fetched_at=fetched_at,
                scorer=scorer,
            ))
            log.info("news fetch %s %s rows=%d %.2fs", fn.__name__, base_cat, len(rows), time.time() - t)
        except Exception as e:
            log.warning("news fetch %s failed: %s", fn.__name__, e)

    # 情绪面 = 强情绪打分的消息面（绝对值 >=1 的）派生镜像
    sentiment_view: list[NewsItem] = []
    seen_ids: set[str] = set()
    for it in items:
        if it.category == "message" and it.sentiment != 0:
            mirror_id = _hash_id("sentiment", it.source, it.title, it.published_at)
            if mirror_id in seen_ids:
                continue
            seen_ids.add(mirror_id)
            sentiment_view.append(NewsItem(
                id=mirror_id, category="sentiment", source=it.source,
                title=it.title, content=it.content, url=it.url,
                published_at=it.published_at, sentiment=it.sentiment,
                pos_hits=it.pos_hits, neg_hits=it.neg_hits,
                relevance_score=it.relevance_score,
                themes=it.themes,
                kw_hits=it.kw_hits,
                fetched_at=fetched_at,
            ))
    items.extend(sentiment_view)
    return items


@dataclass
class NewsPanel:
    items_by_category: dict[str, list[NewsItem]]
    refreshed_at: str
    cached: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "items_by_category": {
                cat: [it.to_dict() for it in lst]
                for cat, lst in self.items_by_category.items()
            },
            "refreshed_at": self.refreshed_at,
            "cached": self.cached,
        }


def get_news_panel(conn: sqlite3.Connection, *, force_refresh: bool = False, per_category: int = 30) -> NewsPanel:
    last = _last_fetched_at(conn)
    fresh = last is not None and (datetime.now(TZ) - last).total_seconds() < TTL_SECONDS
    cached = fresh and not force_refresh
    if not cached:
        items = _fetch_all()
        if items:
            conn.execute("BEGIN")
            try:
                _upsert(conn, items)
                prune_news(conn)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            last = datetime.now(TZ)

    by_cat = {cat: _select(conn, cat, per_category) for cat in CATEGORIES}
    refreshed_at = (last or datetime.now(TZ)).strftime("%Y-%m-%d %H:%M:%S") if last else "—"
    return NewsPanel(items_by_category=by_cat, refreshed_at=refreshed_at, cached=cached)
