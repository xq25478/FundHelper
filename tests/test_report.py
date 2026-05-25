from __future__ import annotations

from fund_helper.report import render_fund_card
from fund_helper.config import AppConfig


def test_render_fund_card_missing(tmp_path):
    cfg = AppConfig(data_dir=tmp_path)
    md = render_fund_card(cfg, "000000")
    assert "未找到" in md
    assert "000000" in md
