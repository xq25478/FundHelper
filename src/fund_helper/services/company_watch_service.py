"""Market dynamics radar for configured companies and fund top holdings."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from ..config import AppConfig, WatchedCompanyCfg
from ..portfolio.holdings import load_holdings
from ..storage import connect

log = logging.getLogger(__name__)
CST = timezone(timedelta(hours=8))

TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "业绩": ("业绩", "利润", "营收", "净利", "预增", "预减", "扭亏", "亏损", "不及预期"),
    "订单": ("订单", "合同", "中标", "客户", "供应", "采购", "交付", "量产"),
    "产能": ("产能", "扩产", "投产", "产线", "工厂", "基地", "封测", "晶圆"),
    "技术": ("突破", "研发", "专利", "新品", "技术", "制程", "工艺", "良率"),
    "资本动作": ("并购", "重组", "定增", "回购", "分红", "解禁", "减持", "增持", "股东"),
    "监管风险": ("处罚", "问询", "调查", "诉讼", "违规", "退市", "风险警示"),
    "海外映射": ("英伟达", "台积电", "美光", "海力士", "苹果", "特斯拉", "出口管制", "制裁"),
}


@dataclass(slots=True)
class CompanyTarget:
    key: str
    code: str | None
    name: str
    aliases: list[str] = field(default_factory=list)
    themes: list[str] = field(default_factory=list)
    role: str = ""
    priority: int = 2
    source: str = "manual"
    fund_codes: list[str] = field(default_factory=list)
    exposure: float | None = None


@dataclass(slots=True)
class CompanyNewsMatch:
    company_key: str
    company_code: str | None
    company_name: str
    company_source: str
    fund_codes: list[str]
    exposure: float | None
    news_id: str
    title: str
    source: str | None
    url: str | None
    published_at: str | None
    category: str | None
    sentiment: int
    score: float
    matched_terms: list[str]
    topics: list[str]
    impact_note: str
    fetched_at: str
    raw: dict[str, Any]


@dataclass(slots=True)
class CompanyWatchPanel:
    targets: list[CompanyTarget]
    matches: list[CompanyNewsMatch]
    refreshed_at: str
    cached: bool
    source_used: str
    errors: list[str]


class CompanyWatchService:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.conn = connect(cfg.data_dir / "fund.db")

    def get_panel(self, *, force_refresh: bool = False, refresh_news: bool = False) -> CompanyWatchPanel:
        watch_cfg = self.cfg.company_watch
        if not watch_cfg.enabled:
            return CompanyWatchPanel([], [], "--", True, "disabled", [])

        errors: list[str] = []
        targets = self.build_targets(errors=errors)
        if refresh_news:
            try:
                from .news_service import get_news_panel

                get_news_panel(self.conn, force_refresh=force_refresh)
            except Exception as e:  # noqa: BLE001
                errors.append(f"新闻刷新失败: {e}")

        cached = not force_refresh
        source_used = "sqlite"
        if force_refresh and targets:
            matches = self._match_news(targets)
            if matches:
                self._upsert(matches)
                source_used = "news_item_match"
            else:
                source_used = "news_item_no_match"
            cached = False

        matches = self._load_matches(
            [target.key for target in targets],
            limit=max(20, len(targets) * watch_cfg.max_news_per_company),
        )
        refreshed_at = self._last_fetched_at() or "--"
        return CompanyWatchPanel(targets, matches, refreshed_at, cached, source_used, errors)

    def build_targets(self, *, errors: list[str] | None = None) -> list[CompanyTarget]:
        merged: dict[str, CompanyTarget] = {}
        for raw in self.cfg.company_watch.companies:
            target = _target_from_config(raw)
            if target is None:
                continue
            _merge_target(merged, target)

        if self.cfg.company_watch.include_fund_top_holdings:
            try:
                for target in self._targets_from_fund_top_holdings():
                    _merge_target(merged, target)
            except Exception as e:  # noqa: BLE001
                log.warning("company watch auto targets failed: %s", e)
                if errors is not None:
                    errors.append(f"持仓重仓股观察名单生成失败: {e}")

        return sorted(
            merged.values(),
            key=lambda t: ((t.exposure or 0), t.priority, t.name),
            reverse=True,
        )

    def _targets_from_fund_top_holdings(self) -> list[CompanyTarget]:
        holdings = load_holdings()
        fund_weights = {pos.code: float(pos.weight or 0) for pos in holdings.positions}
        if not fund_weights:
            return []
        placeholders = ",".join("?" for _ in fund_weights)
        rows = self.conn.execute(
            f"""SELECT fund_code, stock_code, stock_name, pct_nav, season
                FROM fund_top_holding
                WHERE fund_code IN ({placeholders})
                ORDER BY fund_code, rank ASC""",
            list(fund_weights),
        ).fetchall()
        by_key: dict[str, CompanyTarget] = {}
        for fund_code, stock_code, stock_name, pct_nav, _season in rows:
            name = str(stock_name or "").strip()
            code = _clean_code(stock_code)
            if not name and not code:
                continue
            key = code or name
            target = by_key.get(key)
            if target is None:
                target = CompanyTarget(
                    key=key,
                    code=code,
                    name=name or code,
                    aliases=[],
                    themes=[],
                    role="持仓基金前十大重仓股",
                    priority=2,
                    source="fund_top_holding",
                    fund_codes=[],
                    exposure=0.0,
                )
                by_key[key] = target
            if fund_code not in target.fund_codes:
                target.fund_codes.append(str(fund_code))
            if pct_nav is not None:
                target.exposure = (target.exposure or 0) + fund_weights.get(str(fund_code), 0) * float(pct_nav) / 100

        return sorted(
            by_key.values(),
            key=lambda t: (t.exposure or 0),
            reverse=True,
        )[: self.cfg.company_watch.max_auto_companies]

    def _match_news(self, targets: list[CompanyTarget]) -> list[CompanyNewsMatch]:
        news_rows = self._load_recent_news()
        all_matches: list[CompanyNewsMatch] = []
        fetched_at = _now_iso()
        for target in targets:
            matches_for_company: list[CompanyNewsMatch] = []
            terms = _terms_for_target(target)
            for row in news_rows:
                match = _match_row(target, terms, row, fetched_at=fetched_at)
                if match is not None:
                    matches_for_company.append(match)
            matches_for_company.sort(key=lambda m: (m.score, m.published_at or ""), reverse=True)
            all_matches.extend(matches_for_company[: self.cfg.company_watch.max_news_per_company])
        return all_matches

    def _load_recent_news(self) -> list[dict[str, Any]]:
        cutoff = (_now() - timedelta(days=self.cfg.company_watch.lookback_days)).isoformat(timespec="seconds")
        rows = self.conn.execute(
            """SELECT id,category,source,title,content,url,published_at,sentiment,
                      relevance_score,themes,kw_hits,fetched_at
               FROM news_item
               WHERE fetched_at>=? OR published_at>=?
               ORDER BY published_at DESC, fetched_at DESC
               LIMIT ?""",
            (cutoff, cutoff, self.cfg.company_watch.max_news_scan),
        ).fetchall()
        return [
            {
                "id": row[0],
                "category": row[1],
                "source": row[2],
                "title": row[3] or "",
                "content": row[4] or "",
                "url": row[5],
                "published_at": row[6],
                "sentiment": int(row[7] or 0),
                "relevance_score": float(row[8] or 0),
                "themes": _loads_list(row[9]),
                "kw_hits": _loads_list(row[10]),
                "fetched_at": row[11],
            }
            for row in rows
        ]

    def _upsert(self, matches: list[CompanyNewsMatch]) -> None:
        self.conn.executemany(
            """
            INSERT INTO company_news_match
              (company_key,company_code,company_name,company_source,fund_codes,exposure,
               news_id,title,source,url,published_at,category,sentiment,score,
               matched_terms,topics,impact_note,fetched_at,raw_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(company_key,news_id) DO UPDATE SET
              company_code=excluded.company_code,
              company_name=excluded.company_name,
              company_source=excluded.company_source,
              fund_codes=excluded.fund_codes,
              exposure=excluded.exposure,
              title=excluded.title,
              source=excluded.source,
              url=excluded.url,
              published_at=excluded.published_at,
              category=excluded.category,
              sentiment=excluded.sentiment,
              score=excluded.score,
              matched_terms=excluded.matched_terms,
              topics=excluded.topics,
              impact_note=excluded.impact_note,
              fetched_at=excluded.fetched_at,
              raw_json=excluded.raw_json
            """,
            [
                (
                    m.company_key,
                    m.company_code,
                    m.company_name,
                    m.company_source,
                    json.dumps(m.fund_codes, ensure_ascii=False),
                    m.exposure,
                    m.news_id,
                    m.title,
                    m.source,
                    m.url,
                    m.published_at,
                    m.category,
                    m.sentiment,
                    m.score,
                    json.dumps(m.matched_terms, ensure_ascii=False),
                    json.dumps(m.topics, ensure_ascii=False),
                    m.impact_note,
                    m.fetched_at,
                    json.dumps(m.raw, ensure_ascii=False, default=str),
                )
                for m in matches
            ],
        )

    def _load_matches(self, target_keys: list[str], *, limit: int) -> list[CompanyNewsMatch]:
        if not target_keys:
            return []
        placeholders = ",".join("?" for _ in target_keys)
        rows = self.conn.execute(
            f"""SELECT company_key,company_code,company_name,company_source,fund_codes,exposure,
                      news_id,title,source,url,published_at,category,sentiment,score,
                      matched_terms,topics,impact_note,fetched_at,raw_json
               FROM company_news_match
               WHERE company_key IN ({placeholders})
               ORDER BY score DESC, published_at DESC
               LIMIT ?""",
            (*target_keys, limit),
        ).fetchall()
        out: list[CompanyNewsMatch] = []
        for row in rows:
            out.append(CompanyNewsMatch(
                company_key=row[0],
                company_code=row[1],
                company_name=row[2],
                company_source=row[3],
                fund_codes=_loads_list(row[4]),
                exposure=row[5],
                news_id=row[6],
                title=row[7],
                source=row[8],
                url=row[9],
                published_at=row[10],
                category=row[11],
                sentiment=int(row[12] or 0),
                score=float(row[13] or 0),
                matched_terms=_loads_list(row[14]),
                topics=_loads_list(row[15]),
                impact_note=row[16] or "",
                fetched_at=row[17],
                raw=_loads_dict(row[18]),
            ))
        return out

    def _last_fetched_at(self) -> str | None:
        row = self.conn.execute("SELECT MAX(fetched_at) FROM company_news_match").fetchone()
        return row[0] if row and row[0] else None


def render_company_watch_markdown(panel: CompanyWatchPanel, *, max_rows: int = 18) -> str:
    lines = [
        "### 市场动态（重点公司消息）",
        f"- 最近匹配：{panel.refreshed_at}；目标公司：{len(panel.targets)} 家；来源：{panel.source_used}",
        "- 覆盖范围：手工配置公司 + 持仓基金前十大重仓股；只引用本地新闻缓存命中的公开消息。",
    ]
    if panel.errors:
        lines.append("- 异常：" + "；".join(panel.errors[:5]))
    if not panel.targets:
        lines.append("- 暂无观察公司。可在 config.yml 的 company_watch.companies 中添加公司，或先刷新持仓穿透。")
        return "\n".join(lines)
    if not panel.matches:
        lines.append("- 观察名单已生成，但最近新闻缓存暂未命中公司消息。")
        return "\n".join(lines)

    lines.append("")
    lines.append("| 公司 | 来源 | 关联基金 | 时间 | 情绪 | 重要点 | 标题 | 投资影响提示 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for match in panel.matches[:max_rows]:
        lines.append(
            f"| {match.company_name}{_code_suffix(match.company_code)} | {_source_label(match.company_source)} | "
            f"{'、'.join(match.fund_codes[:4]) or '--'} | {match.published_at or '--'} | "
            f"{_sentiment_label(match.sentiment)} | {'、'.join(match.topics[:3]) or '--'} | "
            f"{_compact(match.title, 42)} | {_compact(match.impact_note, 36)} |"
        )
    return "\n".join(lines)


def _target_from_config(raw: WatchedCompanyCfg) -> CompanyTarget | None:
    code = _clean_code(raw.code)
    name = raw.name.strip()
    if not code and not name:
        return None
    key = code or name
    return CompanyTarget(
        key=key,
        code=code,
        name=name or code,
        aliases=[a.strip() for a in raw.aliases if a.strip()],
        themes=[t.strip() for t in raw.themes if t.strip()],
        role=raw.role.strip(),
        priority=max(1, int(raw.priority or 1)),
        source="manual",
    )


def _merge_target(merged: dict[str, CompanyTarget], target: CompanyTarget) -> None:
    existing = merged.get(target.key)
    if existing is None:
        merged[target.key] = target
        return
    existing.aliases = _dedupe(existing.aliases + target.aliases)
    existing.themes = _dedupe(existing.themes + target.themes)
    existing.fund_codes = _dedupe(existing.fund_codes + target.fund_codes)
    existing.priority = max(existing.priority, target.priority)
    if target.role and target.role not in existing.role:
        existing.role = "；".join([r for r in [existing.role, target.role] if r])
    if existing.source != target.source:
        existing.source = "+".join(_dedupe(existing.source.split("+") + target.source.split("+")))
    if target.exposure is not None:
        existing.exposure = (existing.exposure or 0) + target.exposure


def _match_row(
    target: CompanyTarget,
    terms: list[str],
    row: dict[str, Any],
    *,
    fetched_at: str,
) -> CompanyNewsMatch | None:
    title = str(row.get("title") or "")
    content = str(row.get("content") or "")
    text = f"{title} {content[:1200]}"
    matched = [term for term in terms if _term_hit(text, term)]
    if not matched:
        return None
    title_hits = [term for term in matched if _term_hit(title, term)]
    topics = _topic_hits(text)
    sentiment = int(row.get("sentiment") or 0)
    score = (
        float(target.priority)
        + len(matched) * 0.8
        + len(title_hits) * 1.5
        + len(topics) * 0.5
        + abs(sentiment) * 0.4
        + float(row.get("relevance_score") or 0)
    )
    impact = _impact_note(sentiment=sentiment, topics=topics, target=target)
    raw = {
        "news_themes": row.get("themes") or [],
        "news_kw_hits": row.get("kw_hits") or [],
        "target_themes": target.themes,
        "target_role": target.role,
    }
    return CompanyNewsMatch(
        company_key=target.key,
        company_code=target.code,
        company_name=target.name,
        company_source=target.source,
        fund_codes=target.fund_codes,
        exposure=target.exposure,
        news_id=str(row.get("id")),
        title=title,
        source=row.get("source"),
        url=row.get("url"),
        published_at=row.get("published_at"),
        category=row.get("category"),
        sentiment=sentiment,
        score=score,
        matched_terms=matched,
        topics=topics,
        impact_note=impact,
        fetched_at=fetched_at,
        raw=raw,
    )


def _terms_for_target(target: CompanyTarget) -> list[str]:
    terms: list[str] = []
    if target.name:
        terms.append(target.name)
    if target.code:
        terms.append(target.code)
        bare = target.code.split(".")[0]
        if bare != target.code:
            terms.append(bare)
    terms.extend(target.aliases)
    return [term for term in _dedupe(terms) if len(term) >= 2]


def _term_hit(text: str, term: str) -> bool:
    if not text or not term:
        return False
    if re.fullmatch(r"\d{6}", term):
        return re.search(rf"(?<!\d){re.escape(term)}(?!\d)", text) is not None
    return term in text


def _topic_hits(text: str) -> list[str]:
    out: list[str] = []
    for topic, keywords in TOPIC_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            out.append(topic)
    return out


def _impact_note(*, sentiment: int, topics: list[str], target: CompanyTarget) -> str:
    topic_text = "、".join(topics[:2]) if topics else "公司动态"
    relation = "，与持仓基金重仓股相关" if target.fund_codes else ""
    if sentiment > 0:
        return f"偏正面，关注{topic_text}能否兑现到业绩{relation}"
    if sentiment < 0:
        return f"偏风险，核实{topic_text}是否影响基本面{relation}"
    return f"中性观察，重点看{topic_text}后续是否有实质进展{relation}"


def _clean_code(code: Any) -> str | None:
    raw = str(code or "").strip()
    if not raw:
        return None
    match = re.search(r"\d{6}", raw)
    return match.group(0) if match else raw


def _loads_list(raw: Any) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    try:
        value = json.loads(str(raw))
    except json.JSONDecodeError:
        return []
    if isinstance(value, list):
        return [str(x) for x in value]
    return []


def _loads_dict(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(str(raw))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _dedupe(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        key = item.strip()
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def _now() -> datetime:
    return datetime.now(CST)


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


def _sentiment_label(sentiment: int) -> str:
    if sentiment > 0:
        return "利好"
    if sentiment < 0:
        return "利空"
    return "中性"


def _source_label(source: str) -> str:
    labels = {
        "manual": "手工",
        "fund_top_holding": "重仓股",
        "manual+fund_top_holding": "手工+重仓",
    }
    return labels.get(source, source)


def _code_suffix(code: str | None) -> str:
    return f"({code})" if code else ""


def _compact(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
