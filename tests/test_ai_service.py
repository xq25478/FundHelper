from __future__ import annotations

from fund_helper.services.ai_service import _fmt_pct, _fmt_ratio_pct


def test_percent_formatters_distinguish_percent_and_ratio_values():
    assert _fmt_pct(4.25) == "+4.25%"
    assert _fmt_ratio_pct(0.0032) == "+0.32%"
