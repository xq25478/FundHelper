from __future__ import annotations

from fund_helper.screener import FilterSpec, apply_filters, score


def test_apply_filters_basic(screener_df):
    out = apply_filters(
        screener_df,
        FilterSpec(fund_type="equity", min_aum=5e8, min_years=3, min_sharpe=1.0),
    )
    assert list(out["code"]) == ["000001"]


def test_score_orders_by_composite(screener_df):
    ranked = score(screener_df)
    # bond fund (000004) is heavily penalised by low return/sharpe; top pick must be an equity/hybrid
    assert ranked.iloc[0]["code"] != "000004"
    assert "_score" in ranked.columns
