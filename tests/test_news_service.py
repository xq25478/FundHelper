from __future__ import annotations

from fund_helper.services import news_service
from fund_helper.services.news_relevance import RelevanceScorer
from fund_helper.storage import connect


def test_news_relevance_is_persisted(tmp_path, monkeypatch):
    row = {
        "source": "测试源",
        "title": "半导体设备订单增长",
        "content": "国产芯片和先进封装景气度改善。",
        "published_at": "2026-05-25 10:00:00",
        "url": None,
    }

    monkeypatch.setattr(news_service.src, "fetch_cls_telegraph", lambda symbol="全部": [row])
    monkeypatch.setattr(news_service.src, "fetch_em_global", lambda: [])
    monkeypatch.setattr(news_service.src, "fetch_futu_global", lambda: [])
    monkeypatch.setattr(news_service.src, "fetch_ths_global", lambda: [])
    monkeypatch.setattr(news_service.src, "fetch_sina_global", lambda: [])
    monkeypatch.setattr(news_service.src, "fetch_wallstreetcn_lives", lambda: [])
    monkeypatch.setattr(news_service.src, "fetch_cctv", lambda: [])
    monkeypatch.setattr(news_service.src, "fetch_cx_main", lambda: [])
    monkeypatch.setattr(news_service.src, "fetch_us_index_overnight", lambda: [])

    conn = connect(tmp_path / "fund.db")
    panel = news_service.get_news_panel(conn, force_refresh=True)

    item = panel.items_by_category["message"][0]
    assert item.relevance_score > 0
    assert "半导体" in item.themes
    assert "半导体" in item.kw_hits
    assert item.sentiment == 1

    mirror = panel.items_by_category["sentiment"][0]
    assert mirror.relevance_score == item.relevance_score
    assert mirror.themes == item.themes


def test_news_relevance_avoids_latin_substring_false_positive():
    rel = RelevanceScorer().score("SpaceX 提交 IPO 文件，披露亏损。")

    assert rel.score == 0
    assert "IP" not in rel.keywords


def test_policy_relevance_uses_title_for_cctv_bundle():
    row = {
        "source": "新闻联播",
        "title": "习近平同塞尔维亚总统会谈",
        "content": "节目其他段落提到人工智能产业发展。",
        "published_at": "2026-05-25",
        "url": None,
    }

    items = news_service._make_items(
        [row],
        base_category="policy",
        fetched_at="2026-05-26T10:00:00+08:00",
        scorer=RelevanceScorer(),
    )

    assert items[0].relevance_score == 0
