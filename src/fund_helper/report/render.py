from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

TPL_DIR = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(TPL_DIR)),
    autoescape=select_autoescape(enabled_extensions=("html",)),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _render(name: str, ctx: dict[str, Any]) -> str:
    return _env.get_template(name).render(**ctx)


def render_fund_card(app_cfg, code: str) -> str:
    """Build single-fund report. Pulls from local cache + analytics."""
    from datetime import datetime

    from ..analytics import (
        annualized_return, annualized_vol, cumulative_return,
        max_drawdown, sharpe_ratio, sortino_ratio, calmar_ratio,
    )
    from ..services.nav_service import NavService

    svc = NavService(app_cfg)
    series, _ = svc.get_nav(code, lookback_days=180)
    if series.frame.empty:
        return _render("fund_card.md.j2", {
            "code": code, "missing": True, "generated_at": datetime.now().isoformat(timespec="seconds"),
        })

    rets = series.returns()
    rf = app_cfg.risk_free_rate
    ctx = {
        "missing": False,
        "code": code,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "n_obs": int(len(rets)),
        "start": rets.index.min().date().isoformat() if not rets.empty else "-",
        "end":   rets.index.max().date().isoformat() if not rets.empty else "-",
        "cum_ret":  cumulative_return(rets),
        "ann_ret":  annualized_return(rets),
        "ann_vol":  annualized_vol(rets),
        "max_dd":   max_drawdown(rets),
        "sharpe":   sharpe_ratio(rets, rf),
        "sortino":  sortino_ratio(rets, rf),
        "calmar":   calmar_ratio(rets),
    }
    return _render("fund_card.md.j2", ctx)


def render_screen_report(rows: list[dict[str, Any]], spec_desc: str = "") -> str:
    from datetime import datetime
    return _render("screen_report.md.j2", {
        "rows": rows,
        "spec_desc": spec_desc,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    })
