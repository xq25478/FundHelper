from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .config import load_config
from .logging_conf import setup_logging

app = typer.Typer(add_completion=False, help="A-share fund analysis helper.")
console = Console()


@app.callback()
def _root(
    ctx: typer.Context,
    config: Path = typer.Option(Path("configs/settings.yaml"), "--config", "-c"),
) -> None:
    cfg = load_config(config)
    setup_logging(cfg.log_level)
    ctx.obj = cfg


# --- refresh ---------------------------------------------------------------

refresh_app = typer.Typer(help="Refresh local cache from remote data sources.")
app.add_typer(refresh_app, name="refresh")


@refresh_app.command("universe")
def refresh_universe(ctx: typer.Context) -> None:
    """Pull full fund list and upsert into local cache."""
    from .datasource import build_default

    src = build_default(ctx.obj)
    funds = src.list_funds()
    console.print(f"[green]ok[/green] fetched {len(funds)} funds via {src.name}")


@refresh_app.command("nav")
def refresh_nav(
    ctx: typer.Context,
    code: str = typer.Option(..., "--code"),
    start: str | None = typer.Option(None),
    end: str | None = typer.Option(None),
) -> None:
    """Pull NAV history for one fund and persist as parquet."""
    from datetime import date

    from .services.nav_service import NavService

    svc = NavService(ctx.obj)
    s = date.fromisoformat(start) if start else None
    e = date.fromisoformat(end) if end else None
    if s and e:
        series, outcome = svc.get_nav_range(code, s, e, force_refresh=True)
    else:
        series, outcome = svc.get_nav(code, lookback_days=180, force_refresh=True)
    console.print(f"[green]ok[/green] {code}: db_after={len(series.frame)} rows "
                  f"(fetched={outcome.rows_fetched}, status={outcome.status})")




@refresh_app.command("holdings")
def refresh_holdings(
    ctx: typer.Context,
    months: int = typer.Option(6, "--months", help="lookback window in months"),
) -> None:
    """Refresh NAV history for every fund in configs/holdings.yaml."""
    from datetime import date, timedelta

    from .portfolio.holdings import load_holdings
    from .services.nav_service import NavService

    svc = NavService(ctx.obj)
    h = load_holdings()
    end = date.today()
    start = end - timedelta(days=months * 31)
    console.print(f"window: [cyan]{start} -> {end}[/cyan] ({months} months)")
    fails: list[str] = []
    for p in h.positions:
        try:
            series, outcome = svc.get_nav_range(p.code, start, end)
        except Exception as e:
            fails.append(p.code)
            console.print(f"  [red]FAIL[/red] {p.code} {p.name}: {e}")
            continue
        if series.frame.empty:
            fails.append(p.code)
            console.print(f"  [yellow]EMPTY[/yellow] {p.code} {p.name}")
            continue
        rng = (series.frame.index.min().date(), series.frame.index.max().date())
        console.print(
            f"  [green]{outcome.status}[/green] {p.code} {p.name}: "
            f"{len(series.frame)} rows {rng[0]}~{rng[1]} "
            f"(fetched={outcome.rows_fetched})"
        )
    if fails:
        console.print(f"[red]{len(fails)} failed[/red]: {fails}")


# --- report ----------------------------------------------------------------

@app.command()
def report(
    ctx: typer.Context,
    code: str,
    out: Path = typer.Option(Path("reports"), "--out", "-o"),
) -> None:
    """Render a single-fund analysis report (Markdown)."""
    from .report.render import render_fund_card

    md = render_fund_card(ctx.obj, code)
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"fund_{code}.md"
    target.write_text(md, encoding="utf-8")
    console.print(f"[green]ok[/green] wrote {target}")


# --- screen ----------------------------------------------------------------

@app.command()
def screen(
    ctx: typer.Context,
    fund_type: str | None = typer.Option(None, "--type"),
    min_aum: float | None = typer.Option(None, "--min-aum"),
    min_years: float | None = typer.Option(None, "--min-years"),
    min_sharpe: float | None = typer.Option(None, "--min-sharpe"),
    max_dd: float | None = typer.Option(None, "--max-dd",
                                        help="negative number, e.g. -0.3"),
    limit: int = typer.Option(30, "--limit"),
) -> None:
    """Filter + rank the local fund universe and print the top N."""
    import pandas as pd

    from .screener import FilterSpec, apply_filters, score

    cache_csv = ctx.obj.data_dir / "screener_input.csv"
    if not cache_csv.exists():
        console.print(
            f"[yellow]missing[/yellow] {cache_csv} — run `fh refresh universe` "
            "and a metrics build step first."
        )
        raise typer.Exit(code=1)
    df = pd.read_csv(cache_csv)

    filtered = apply_filters(
        df,
        FilterSpec(
            fund_type=fund_type,
            min_aum=min_aum,
            min_years=min_years,
            min_sharpe=min_sharpe,
            max_drawdown=max_dd,
        ),
    )
    ranked = score(filtered).head(limit)

    table = Table(title=f"top {limit} funds")
    for col in ["code", "name", "fund_type", "annualized_return",
                "sharpe", "max_dd", "aum", "_score"]:
        if col in ranked.columns:
            table.add_column(col)
    for _, row in ranked.iterrows():
        table.add_row(*[f"{row[c]}" for c in table.columns if c.header in ranked.columns
                        or hasattr(row, c.header)])
    console.print(table)


# --- backtest --------------------------------------------------------------

@app.command()
def backtest(ctx: typer.Context, portfolio: Path) -> None:
    """Run a simple weighted-portfolio backtest from a YAML spec."""
    import yaml
    import pandas as pd

    from .portfolio import backtest_portfolio
    from .services.nav_service import NavService

    spec = yaml.safe_load(portfolio.read_text())
    weights = spec["weights"]
    rebalance = {"daily": "D", "weekly": "W-FRI", "monthly": "ME",
                 "quarterly": "QE", "yearly": "YE", "none": "none"}.get(
        str(spec.get("rebalance", "quarterly")).lower(), "QE"
    )

    svc = NavService(ctx.obj)
    cols: dict[str, pd.Series] = {}
    for code in weights:
        series, _ = svc.get_nav(code, lookback_days=365 * 3)
        if series.frame.empty:
            console.print(f"[red]missing NAV[/red] {code} — run `fh refresh nav --code {code}`")
            raise typer.Exit(code=1)
        cols[code] = series.returns()
    panel = pd.concat(cols, axis=1).dropna(how="all")

    if spec.get("start"):
        panel = panel[panel.index >= pd.Timestamp(spec["start"])]
    if spec.get("end"):
        panel = panel[panel.index <= pd.Timestamp(spec["end"])]

    res = backtest_portfolio(panel, weights, rebalance=rebalance)
    console.print(f"[green]ok[/green] backtest done, final NAV = {res.nav.iloc[-1]:.4f}")


# --- analyze ---------------------------------------------------------------

@app.command("data-status")
def data_status(ctx: typer.Context) -> None:
    """Print local data freshness and coverage used by automated analysis."""
    from .services.ai_service import _build_data_quality

    console.print(_build_data_quality(ctx.obj))


@app.command("push-report")
def push_report_cmd(
    ctx: typer.Context,
    report: Path = typer.Argument(Path("reports/advice/latest.md")),
    title: str | None = typer.Option(None, "--title", "-t"),
) -> None:
    """Push a generated Markdown report via SMTP email."""
    from .services.notify_service import push_report

    result = push_report(ctx.obj, report, title=title)
    console.print(f"[green]ok[/green] pushed via {result.provider}: {result.message}")


def _refresh_holdings_nav(cfg, months: int) -> list[str]:
    """Refresh local NAV cache for configured holdings; returns failed fund codes."""
    from datetime import date

    from .portfolio.holdings import load_holdings
    from .services.nav_service import NavService

    svc = NavService(cfg)
    h = load_holdings()
    end = date.today()
    start = end - timedelta(days=months * 31)
    fails: list[str] = []
    console.print(f"refresh NAV window: [cyan]{start} -> {end}[/cyan]")
    for p in h.positions:
        try:
            series, outcome = svc.get_nav_range(p.code, start, end)
        except Exception as e:
            fails.append(p.code)
            console.print(f"  [red]FAIL[/red] {p.code} {p.name}: {e}")
            continue
        if series.frame.empty:
            fails.append(p.code)
            console.print(f"  [yellow]EMPTY[/yellow] {p.code} {p.name}")
            continue
        rng = (series.frame.index.min().date(), series.frame.index.max().date())
        console.print(
            f"  [green]{outcome.status}[/green] {p.code} {p.name}: "
            f"{len(series.frame)} rows {rng[0]}~{rng[1]} "
            f"(fetched={outcome.rows_fetched})"
        )
    return fails


def _advice_markdown(
    *,
    generated_at: datetime,
    sections: list[tuple[str, str, str | None]],
    errors: list[str],
    include_prompt: bool,
) -> str:
    lines = [
        "# Fund Helper 自动分析与操作建议",
        "",
        f"- 生成时间：{generated_at.strftime('%Y-%m-%d %H:%M:%S')} Asia/Shanghai",
        "- 数据来源：公开行情、基金净值、新闻与本项目本地缓存",
        "- 风险提示：以下为基于公开数据的自动化分析推演，不构成投资建议，投资有风险。",
        "",
    ]
    if errors:
        lines.extend(["## 自动化告警", ""])
        lines.extend(f"- {e}" for e in errors)
        lines.append("")

    for title, text, prompt in sections:
        lines.extend([f"## {title}", "", text.strip() or "(模型返回为空)", ""])
        if include_prompt and prompt:
            lines.extend([
                "<details>",
                "<summary>展开本次 Prompt</summary>",
                "",
                "```markdown",
                prompt.strip(),
                "```",
                "",
                "</details>",
                "",
            ])
    return "\n".join(lines).rstrip() + "\n"


@app.command("analyze")
def analyze(
    ctx: typer.Context,
    target: str = typer.Option("all", "--target", "-t", help="market | sectors | all"),
    out: Path = typer.Option(Path("reports/advice"), "--out", "-o"),
    refresh_nav: bool = typer.Option(False, "--refresh-nav/--skip-nav"),
    nav_months: int = typer.Option(6, "--nav-months", min=1),
    force_refresh: bool = typer.Option(True, "--force-refresh/--cache-ok"),
    latest: bool = typer.Option(True, "--latest/--no-latest"),
    include_prompt: bool = typer.Option(False, "--include-prompt/--no-include-prompt"),
) -> None:
    """Generate an automated Markdown analysis report for GitHub Actions."""
    from .services.ai_service import analyze_market, analyze_sectors

    target = target.lower().strip()
    if target == "all":
        jobs = [
            ("大盘分析", analyze_market),
            ("板块与持仓相关分析", analyze_sectors),
        ]
    elif target == "market":
        jobs = [("大盘分析", analyze_market)]
    elif target in {"sector", "sectors"}:
        jobs = [("板块与持仓相关分析", analyze_sectors)]
    else:
        raise typer.BadParameter("target must be one of: market, sectors, all")

    errors: list[str] = []
    if refresh_nav:
        nav_fails = _refresh_holdings_nav(ctx.obj, nav_months)
        if nav_fails:
            errors.append(f"NAV refresh failed for: {', '.join(nav_fails)}")

    sections: list[tuple[str, str, str | None]] = []
    for title, fn in jobs:
        console.print(f"[cyan]analyzing[/cyan] {title} ...")
        try:
            result = fn(ctx.obj, force_refresh=force_refresh)
        except Exception as e:  # noqa: BLE001
            msg = f"{title} failed: {e}"
            errors.append(msg)
            sections.append((title, f"> 自动分析失败：{e}", None))
            continue
        sections.append((title, result.get("text", ""), result.get("prompt")))

    now = datetime.now(timezone(timedelta(hours=8)))
    out.mkdir(parents=True, exist_ok=True)
    report = _advice_markdown(
        generated_at=now,
        sections=sections,
        errors=errors,
        include_prompt=include_prompt,
    )
    stamp = now.strftime("%Y%m%d_%H%M")
    target_path = out / f"advice_{stamp}.md"
    target_path.write_text(report, encoding="utf-8")
    console.print(f"[green]ok[/green] wrote {target_path}")
    if latest:
        latest_path = out / "latest.md"
        latest_path.write_text(report, encoding="utf-8")
        console.print(f"[green]ok[/green] wrote {latest_path}")
    if errors:
        raise typer.Exit(code=1)




# --- serve -----------------------------------------------------------------

@app.command()
def serve(
    ctx: typer.Context,
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(7788, "--port"),
    open_browser: bool = typer.Option(True, "--open/--no-open"),
) -> None:
    """Launch the local web UI."""
    import threading
    import time
    import webbrowser

    import uvicorn

    from .web import create_app

    application = create_app(ctx.obj)

    if open_browser:
        url = f"http://{host}:{port}/"
        def _open():
            time.sleep(0.8)
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    uvicorn.run(application, host=host, port=port, log_level=ctx.obj.log_level.lower())


if __name__ == "__main__":
    app()
