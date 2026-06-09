from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fund_helper.config import AppConfig, CompanyWatchCfg, WatchedCompanyCfg
from fund_helper.services.company_watch_service import (
    CompanyWatchService,
    render_company_watch_markdown,
)
from fund_helper.storage import connect


def test_company_watch_matches_manual_and_fund_top_holdings(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yml").write_text(
        """
holdings:
  name: test
  positions:
    - code: "017811"
      name: 东方人工智能主题混合C
      weight: 1.0
""",
        encoding="utf-8",
    )
    cfg = AppConfig(
        data_dir=tmp_path / "data",
        company_watch=CompanyWatchCfg(
            companies=[
                WatchedCompanyCfg(
                    code="688981",
                    name="中芯国际",
                    aliases=["SMIC"],
                    themes=["晶圆制造"],
                    priority=3,
                )
            ]
        ),
    )
    conn = connect(cfg.data_dir / "fund.db")
    now = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
    conn.execute(
        """INSERT INTO fund_top_holding
           (fund_code,season,rank,stock_code,stock_name,pct_nav,fetched_at)
           VALUES('017811','2026Q1',1,'002371','北方华创',9.0,?)""",
        (now,),
    )
    conn.execute(
        """INSERT INTO news_item
           (id,category,source,title,content,url,published_at,sentiment,
            relevance_score,themes,kw_hits,fetched_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "n1",
            "message",
            "test",
            "中芯国际披露先进制程订单进展",
            "公司客户订单增长，晶圆制造产能继续扩张。",
            None,
            now,
            1,
            1.0,
            '["半导体"]',
            '["中芯国际"]',
            now,
        ),
    )
    conn.execute(
        """INSERT INTO news_item
           (id,category,source,title,content,url,published_at,sentiment,
            relevance_score,themes,kw_hits,fetched_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "n2",
            "message",
            "test",
            "北方华创扩产项目进入设备交付阶段",
            "半导体设备订单和产能扩张受到关注。",
            None,
            now,
            1,
            1.0,
            '["半导体设备"]',
            '["北方华创"]',
            now,
        ),
    )

    panel = CompanyWatchService(cfg).get_panel(force_refresh=True)

    assert {target.name for target in panel.targets} >= {"中芯国际", "北方华创"}
    assert {match.company_name for match in panel.matches} >= {"中芯国际", "北方华创"}
    assert any("017811" in match.fund_codes for match in panel.matches if match.company_name == "北方华创")

    text = render_company_watch_markdown(panel)
    assert "市场动态" in text
    assert "中芯国际" in text
    assert "北方华创" in text
