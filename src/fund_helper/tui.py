"""Rich-powered terminal UI for fund-helper."""
from __future__ import annotations

import logging
import json
import os
import select
import sys
import termios
import tty
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rich import box
from rich.columns import Columns
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

from .config import AppConfig
from .services.background_refresh import BackgroundRefreshWorker
from .utils import atomic_write_text


def run_tui(cfg: AppConfig) -> None:
    os.environ.setdefault("TQDM_DISABLE", "1")
    previous_logging_disable = logging.root.manager.disable
    logging.disable(logging.WARNING)
    console = Console()
    refresher = BackgroundRefreshWorker(cfg, interval_seconds=60)
    if sys.stdin.isatty():
        console.clear()
    try:
        if sys.stdin.isatty():
            _run_live_tui(console, cfg, refresher)
        else:
            refresher.start()
            _run_prompt_tui(console, cfg, refresher)
    finally:
        refresher.stop()
        logging.disable(previous_logging_disable)


def _run_live_tui(console: Console, cfg: AppConfig, refresher: BackgroundRefreshWorker) -> None:
    started = False
    while True:
        choice = None
        with Live(
            _dashboard_group(cfg, refresher),
            console=console,
            auto_refresh=False,
            refresh_per_second=4,
            screen=True,
            vertical_overflow="crop",
        ) as live:
            last_render_key = None
            live.refresh()
            if not started:
                refresher.start()
                started = True
            while True:
                render_key = _dashboard_render_key(console, refresher)
                if render_key != last_render_key:
                    live.update(_dashboard_group(cfg, refresher), refresh=True)
                    last_render_key = render_key
                choice = _read_key(timeout=0.25)
                if choice in {"1", "2", "3", "4", "q"}:
                    break
        if choice == "q":
            return
        console.clear()
        _handle_choice(console, cfg, refresher, choice)
        Prompt.ask("\n按 Enter 返回主界面", default="")


def _run_prompt_tui(console: Console, cfg: AppConfig, refresher: BackgroundRefreshWorker) -> None:
    while True:
        console.clear()
        console.print(_dashboard_group(cfg, refresher))
        choice = Prompt.ask(
            "选择",
            choices=["1", "2", "3", "4", "q"],
            default="q",
        )
        if choice == "q":
            return
        console.clear()
        _handle_choice(console, cfg, refresher, choice)
        Prompt.ask("\n按 Enter 返回主界面", default="")


def _handle_choice(
    console: Console,
    cfg: AppConfig,
    refresher: BackgroundRefreshWorker,
    choice: str,
) -> None:
    if choice == "1":
        _run_analysis(console, cfg, refresher=refresher, target="all")
    elif choice == "2":
        _show_latest_report(console)
    elif choice == "3":
        _push_latest_report(console, cfg)
    elif choice == "4":
        _show_data_status(console, cfg, refresher)


def _dashboard_group(cfg: AppConfig, refresher: BackgroundRefreshWorker) -> Group:
    return Group(
        _holdings_table(cfg),
        Columns([_market_table(cfg), _sector_table(cfg)], expand=True, equal=False),
        _company_watch_table(cfg),
        "",
        "[bold]操作[/bold]  [cyan]1[/cyan] 完整分析  "
        "[cyan]2[/cyan] 查看最新报告  [cyan]3[/cyan] 推送最新报告  "
        "[cyan]4[/cyan] 数据状态  [cyan]q[/cyan] 退出",
        _refresh_status(refresher),
    )


def _dashboard_render_key(console: Console, refresher: BackgroundRefreshWorker) -> tuple:
    status = refresher.snapshot()
    return (
        console.size.width,
        console.size.height,
        status.get("running"),
        status.get("total_runs"),
        status.get("total_tasks"),
        status.get("completed_tasks"),
        status.get("current_task"),
        status.get("last_finished_at"),
        status.get("next_run_at"),
        status.get("last_ok"),
        status.get("last_message"),
    )


def _refresh_status(refresher: BackgroundRefreshWorker) -> Panel:
    status = refresher.snapshot()
    state = "刷新中" if status["running"] else ("正常" if status["last_ok"] is not False else "有失败")
    total = int(status.get("total_tasks") or 0)
    completed = int(status.get("completed_tasks") or 0)
    current = status.get("current_task") or "--"
    summary = (
        f"状态: {state} · 当前: {current} · "
        f"上次: {_short_time(status['last_finished_at'])} · "
        f"下次: {_short_time(status['next_run_at'])}"
    )
    detail = f"{_progress_text(completed, total)} · {status['last_message']}"
    body = Group(
        Text(summary, no_wrap=True, overflow="ellipsis"),
        Text(detail, style="cyan" if status["running"] else "dim", no_wrap=True, overflow="ellipsis"),
    )
    return Panel(
        body,
        title=f"后台刷新（每 {_format_interval(refresher.interval_seconds)}）",
        border_style="magenta",
        height=4,
    )


def _holdings_table(cfg: AppConfig) -> Panel:
    from datetime import date

    import pandas as pd

    from .analytics import max_drawdown
    from .portfolio.holdings import load_holdings
    from .storage import connect

    table = Table(box=box.SIMPLE_HEAVY, expand=True)
    table.add_column("基金", no_wrap=True, overflow="ellipsis", ratio=4)
    table.add_column("权重", justify="right", no_wrap=True, ratio=1)
    table.add_column("净值", justify="center", no_wrap=True, ratio=1)
    table.add_column("估值", justify="right", no_wrap=True, ratio=1)
    table.add_column("月", justify="right", no_wrap=True, ratio=1)
    table.add_column("7日", no_wrap=True, ratio=2)
    table.add_column("回撤", justify="right", no_wrap=True, ratio=1)
    try:
        conn = connect(cfg.data_dir / "fund.db")
        holdings = load_holdings()
        realtime = _load_realtime_quotes(conn, [p.code for p in holdings.positions])
        returns_panel: dict[str, pd.Series] = {}
        for pos in holdings.positions:
            rows = conn.execute(
                """SELECT trade_date, unit_nav, daily_return
                   FROM nav_daily
                   WHERE code=? AND trade_date>=?
                   ORDER BY trade_date ASC""",
                (pos.code, (date.today() - timedelta(days=180)).isoformat()),
            ).fetchall()
            rt = realtime.get(pos.code)
            rt_pct = _format_realtime_pct(rt)
            if not rows:
                table.add_row(_fund_label(pos.code, pos.name), _pct(pos.weight), "--", rt_pct, "--", "--", "--")
                continue
            frame = pd.DataFrame(rows, columns=["trade_date", "unit_nav", "daily_return"])
            frame["trade_date"] = pd.to_datetime(frame["trade_date"])
            frame = frame.set_index("trade_date").sort_index()
            unit = frame["unit_nav"].dropna().astype(float)
            rets = frame["daily_return"].dropna().astype(float)
            if rets.empty and len(unit) >= 2:
                rets = unit.pct_change().dropna()
            one_month = None
            cutoff = pd.Timestamp(date.today() - timedelta(days=30))
            sub = unit[unit.index >= cutoff]
            if len(sub) >= 2 and sub.iloc[0]:
                one_month = float(sub.iloc[-1] / sub.iloc[0] - 1)
            mdd = max_drawdown(rets) if not rets.empty else None
            if not rets.empty:
                returns_panel[pos.code] = rets
            table.add_row(
                _fund_label(pos.code, pos.name),
                _pct(pos.weight),
                _short_date(unit.index.max().date().isoformat()) if not unit.empty else "--",
                rt_pct,
                _pct(one_month),
                Text(_mini_curve(unit.tail(7).tolist(), width=7), style=_sparkline_style(unit.tail(7).tolist())),
                _pct(mdd),
            )
        metrics = _portfolio_metrics_from_returns(returns_panel, holdings.normalized_weights(), cfg.risk_free_rate)
        footer = (
            f"组合 Sharpe {_num(metrics.get('sharpe') if metrics else None)} · "
            f"最大回撤 {_pct(metrics.get('max_drawdown') if metrics else None)}"
        )
    except Exception as e:  # noqa: BLE001
        table.add_row("读取失败", "--", "--", "--", "--", "--", "--")
        footer = str(e)
    return Panel(table, title="静态区 · 持仓", subtitle=footer, border_style="green")


def _market_table(cfg: AppConfig) -> Panel:
    from .services.market_service import A_SHARE_INDEXES, _is_a_session
    from .storage import connect

    table = Table(box=box.SIMPLE_HEAVY, expand=True)
    table.add_column("指数", no_wrap=True)
    table.add_column("现价", justify="right", no_wrap=True)
    table.add_column("涨跌幅", justify="right", no_wrap=True)
    table.add_column("成交额", justify="right", no_wrap=True)
    table.add_column("量比", justify="right", no_wrap=True)
    table.add_column("20日走势", no_wrap=True)
    table.add_column("结构", no_wrap=True)
    try:
        conn = connect(cfg.data_dir / "fund.db")
        refreshed_at = None
        for secid, fallback_name, _market in A_SHARE_INDEXES:
            row = conn.execute(
                """SELECT name, now, pct, trade_date, fetched_at, amount, volume
                   FROM index_snapshot WHERE secid=?""",
                (secid,),
            ).fetchone()
            if row:
                name, now, pct, _trade_date, fetched_at, amount, volume = row
                refreshed_at = max(refreshed_at or fetched_at, fetched_at)
            else:
                name, now, pct, amount, volume = fallback_name, None, None, None, None
            closes = _load_index_closes(conn, secid, current=now, limit=20)
            vol_ratio = _market_volume_ratio(conn, secid, volume)
            table.add_row(
                name,
                "--" if now is None else f"{float(now):.2f}",
                _styled_ratio_pct(pct),
                _yuan_yi(amount),
                vol_ratio,
                Text(_mini_curve(closes, width=14), style=_sparkline_style(closes)),
                _market_structure(closes),
            )
        ts = (refreshed_at or "--").replace("T", " ")
        subtitle = f"sqlite · {'盘中' if _is_a_session() else '非盘中'} · {ts}"
    except Exception as e:  # noqa: BLE001
        table.add_row("读取失败", "--", "--", "--", "--", "--", "--")
        subtitle = str(e)
    return Panel(table, title="动态区 · 大盘行情与走势", subtitle=subtitle, border_style="blue")


def _sector_table(cfg: AppConfig) -> Panel:
    from .services.sector_service import WATCHLIST_KEYWORDS
    from .storage import connect

    table = Table(box=box.SIMPLE_HEAVY, expand=True)
    table.add_column("板块", no_wrap=True, overflow="ellipsis", ratio=2)
    table.add_column("涨跌", justify="right", no_wrap=True, ratio=1)
    table.add_column("领涨", no_wrap=True, overflow="ellipsis", ratio=2)
    table.add_column("强度", justify="right", no_wrap=True, ratio=1)
    try:
        conn = connect(cfg.data_dir / "fund.db")
        rows = conn.execute(
            """SELECT name,pct,leader_name,leader_pct,fetched_at
               FROM sector_snapshot
               ORDER BY fetched_at DESC"""
        ).fetchall()
        latest = max((row[4] for row in rows if row[4]), default="--")
        watch = [
            row for row in rows
            if any(keyword in str(row[0]) for keyword in WATCHLIST_KEYWORDS)
        ]
        picked = _dedupe_sector_rows(sorted(
            watch,
            key=lambda row: (abs(float(row[1] or 0)), float(row[1] or 0)),
            reverse=True,
        ))[:6]
        if not picked:
            picked = _dedupe_sector_rows(sorted(
                rows,
                key=lambda row: abs(float(row[1] or 0)),
                reverse=True,
            ))[:6]
        for name, pct, leader, leader_pct, _fetched_at in picked:
            table.add_row(
                str(name),
                _styled_percent_units(pct),
                str(leader or "--"),
                _styled_percent_units(leader_pct),
            )
        subtitle = f"sqlite · {latest.replace('T', ' ') if latest else '--'}"
    except Exception as e:  # noqa: BLE001
        table.add_row("读取失败", "--", "--", "--")
        subtitle = str(e)
    return Panel(table, title="动态区 · 关注板块", subtitle=subtitle, border_style="cyan")


def _company_watch_table(cfg: AppConfig) -> Panel:
    from .storage import connect

    table = Table(box=box.SIMPLE_HEAVY, expand=True)
    table.add_column("公司", no_wrap=True, overflow="ellipsis", ratio=2)
    table.add_column("情绪", no_wrap=True, justify="center", ratio=1)
    table.add_column("主题", no_wrap=True, overflow="ellipsis", ratio=2)
    table.add_column("标题", overflow="ellipsis", ratio=7)
    table.add_column("时间", no_wrap=True, ratio=2)
    try:
        conn = connect(cfg.data_dir / "fund.db")
        rows = conn.execute(
            """SELECT company_name,company_code,sentiment,topics,title,published_at,fetched_at
               FROM company_news_match
               ORDER BY score DESC, published_at DESC
               LIMIT 4"""
        ).fetchall()
        latest = max((row[6] for row in rows if row[6]), default="--")
        if not rows:
            table.add_row("暂无命中", "--", "--", "等待新闻刷新或在 company_watch 中添加公司", "--")
        for name, code, sentiment, topics, title, published_at, _fetched_at in rows:
            table.add_row(
                f"{name}({code})" if code else str(name),
                _sentiment_text(sentiment),
                _json_list_preview(topics, limit=2),
                str(title or "--"),
                _short_datetime(published_at),
            )
        subtitle = f"sqlite · {latest.replace('T', ' ') if latest else '--'}"
    except Exception as e:  # noqa: BLE001
        table.add_row("读取失败", "--", "--", str(e), "--")
        subtitle = str(e)
    return Panel(table, title="动态区 · 市场动态", subtitle=subtitle, border_style="yellow", height=7)


def _run_analysis(
    console: Console,
    cfg: AppConfig,
    *,
    refresher: BackgroundRefreshWorker,
    target: str,
) -> None:
    from .services.ai_service import (
        analyze_full,
        analyze_holdings,
        analyze_market,
        analyze_sectors,
        format_ai_call_info,
    )

    console.print("[cyan]刷新缓存...[/cyan]")
    refresher.run_once()
    console.print("[cyan]构建事实包并调用大模型...[/cyan]")
    if target == "all":
        result = analyze_full(cfg, force_refresh=False)
        title = "完整分析报告"
    elif target == "holdings":
        result = analyze_holdings(cfg, force_refresh=False)
        title = "持仓基金分析与操作建议"
    elif target == "market":
        result = analyze_market(cfg, force_refresh=False)
        title = "大盘分析"
    elif target == "sectors":
        result = analyze_sectors(cfg, force_refresh=False)
        title = "板块与持仓相关分析"
    else:
        raise RuntimeError(f"unknown analysis target: {target}")
    answer = result.get("text", "") or "(模型返回为空)"
    console.print("[cyan]校验报告并写入文件...[/cyan]")
    path = _write_report(title, answer)
    _record_tui_report(path, target, title, answer, result.get("guardrails", ""))
    console.print(Panel(format_ai_call_info(result.get("ai_call")), title="大模型调用", border_style="yellow"))
    console.print(f"[green]ok[/green] 已写入 {path}")
    console.print(Markdown(answer[:6000]))
    if len(answer) > 6000:
        console.print("[dim]报告较长，已仅预览前 6000 字符。[/dim]")


def _write_report(title: str, answer: str) -> Path:
    now = datetime.now(timezone(timedelta(hours=8)))
    out = Path("reports/advice")
    out.mkdir(parents=True, exist_ok=True)
    body = "\n".join([
        "# Fund Helper 自动分析与操作建议",
        "",
        f"- 生成时间：{now.strftime('%Y-%m-%d %H:%M:%S')} Asia/Shanghai",
        "- 数据来源：公开行情、基金净值、新闻与本项目本地缓存",
        "- 风险提示：以下为基于公开数据的自动化分析推演，不构成投资建议。",
        "",
        f"## {title}",
        "",
        answer.strip(),
        "",
    ])
    stamped = out / f"advice_{now.strftime('%Y%m%d_%H%M')}.md"
    atomic_write_text(stamped, body)
    atomic_write_text(out / "latest.md", body)
    return stamped


def _show_latest_report(console: Console) -> None:
    path = Path("reports/advice/latest.md")
    if not path.exists():
        console.print("[yellow]还没有 latest.md，先运行分析。[/yellow]")
        return
    text = path.read_text(encoding="utf-8")
    console.print(Markdown(text[:12000]))
    if len(text) > 12000:
        console.print("[dim]报告较长，已仅预览前 12000 字符。[/dim]")


def _push_latest_report(console: Console, cfg: AppConfig) -> None:
    from .services.notify_service import push_report

    path = Path("reports/advice/latest.md")
    if not path.exists():
        console.print("[yellow]还没有 latest.md，先运行分析。[/yellow]")
        return
    title = Prompt.ask("邮件标题", default="基金分析日报")
    result = push_report(cfg, path, title=title)
    console.print(f"[green]ok[/green] pushed via {result.provider}: {result.message}")


def _show_advice_review(console: Console, cfg: AppConfig) -> None:
    from .services.advice_log import load_entries, review_entries

    horizon = int(Prompt.ask("复盘周期（天）", default="7"))
    rows = review_entries(cfg, horizon_days=horizon, limit=10)
    if not rows and not load_entries(limit=1):
        console.print("[yellow]还没有建议日志，先运行分析。[/yellow]")
        return
    table = Table(box=box.SIMPLE_HEAVY, expand=True)
    table.add_column("生成时间")
    table.add_column("目标")
    table.add_column("天数", justify="right")
    table.add_column("组合收益", justify="right")
    table.add_column("状态")
    for row in rows:
        ret = row["portfolio_return"]
        table.add_row(
            str(row["generated_at"])[:19],
            str(row["target"]),
            str(row["age_days"]),
            "--" if ret is None else f"{ret:+.2%}",
            str(row["status"]),
        )
    console.print(Panel(table, title="建议复盘", border_style="magenta"))


def _show_data_status(console: Console, cfg: AppConfig, refresher: BackgroundRefreshWorker | None = None) -> None:
    from .services.ai_service import _build_data_quality

    console.print(Markdown(_build_data_quality(cfg)))
    if refresher is not None:
        status = refresher.snapshot()
        errors = status.get("errors") or []
        if errors:
            table = Table(title="后台刷新最近错误")
            table.add_column("错误")
            for err in errors[-10:]:
                table.add_row(err)
            console.print(table)


def _record_tui_report(path: Path, target: str, title: str, answer: str, guardrails: str) -> None:
    from .services.advice_log import record_report

    record_report(
        report_path=path,
        target=target,
        title=title,
        text=answer,
        guardrail_findings=guardrails.count("[P1]") + guardrails.count("[P2]") + guardrails.count("[P3]"),
    )


def _load_realtime_quotes(conn, codes: list[str]) -> dict[str, dict]:
    if not codes:
        return {}
    placeholders = ",".join("?" for _ in codes)
    rows = conn.execute(
        f"""SELECT code, estimate_pct, estimate_time, source, fetched_at
            FROM fund_realtime_snapshot
            WHERE code IN ({placeholders})""",
        codes,
    ).fetchall()
    return {
        row[0]: {
            "estimate_pct": row[1],
            "estimate_time": row[2],
            "source": row[3],
            "fetched_at": row[4],
        }
        for row in rows
    }


def _fund_label(code: str, name: str, *, max_name_chars: int = 18) -> str:
    short = name if len(name) <= max_name_chars else f"{name[:max_name_chars - 1]}…"
    return f"{code} {short}"


def _load_index_closes(conn, secid: str, *, current, limit: int = 20) -> list[float]:
    rows = conn.execute(
        """SELECT close FROM index_daily
           WHERE secid=? AND close IS NOT NULL
           ORDER BY trade_date DESC
           LIMIT ?""",
        (secid, limit),
    ).fetchall()
    closes = [float(row[0]) for row in reversed(rows) if row[0] is not None]
    if current is not None:
        cur = float(current)
        if closes:
            closes[-1] = cur
        else:
            closes.append(cur)
    return closes


def _market_volume_ratio(conn, secid: str, current_volume) -> str:
    if current_volume is None:
        return "--"
    rows = conn.execute(
        """SELECT volume FROM index_daily
           WHERE secid=? AND volume IS NOT NULL AND volume>0
           ORDER BY trade_date DESC
           LIMIT 5""",
        (secid,),
    ).fetchall()
    vols = [float(row[0]) for row in rows if row[0] is not None]
    if not vols:
        return "--"
    avg = sum(vols) / len(vols)
    if not avg:
        return "--"
    ratio = float(current_volume) / avg
    ratio_100 = float(current_volume) * 100 / avg
    if ratio < 0.05 and 0.05 <= ratio_100 <= 3.5:
        ratio = ratio_100
    return f"{ratio:.2f}x"


def _mini_curve(values: list[float], *, width: int = 18) -> str:
    clean = [float(v) for v in values if v is not None]
    if len(clean) < 2:
        return "─" * width
    points = _sample_points(clean, width)
    span = max(points) - min(points)
    if not span:
        return "─" * len(points)
    levels = "⎽⎼⎻⎺"
    scale = len(levels) - 1
    return "".join(levels[round((value - min(points)) / span * scale)] for value in points)


def _interpolate_values(values: list[float], count: int) -> list[float]:
    if count <= 1:
        return [values[-1]]
    if len(values) == count:
        return values
    max_src = len(values) - 1
    out: list[float] = []
    for i in range(count):
        pos = i * max_src / (count - 1)
        left = int(pos)
        right = min(left + 1, max_src)
        frac = pos - left
        out.append(values[left] * (1 - frac) + values[right] * frac)
    return out


def _sample_points(values: list[float], width: int) -> list[float]:
    if len(values) <= width:
        return values
    step = (len(values) - 1) / (width - 1)
    return [values[round(i * step)] for i in range(width)]


def _sparkline_style(values: list[float]) -> str:
    if len(values) < 2:
        return "dim"
    return "red" if values[-1] >= values[0] else "green"


def _market_structure(values: list[float]) -> str:
    if len(values) < 5:
        return "--"
    price = values[-1]
    ma5 = sum(values[-5:]) / 5
    ma20 = sum(values[-20:]) / min(len(values), 20)
    if price > ma5 > ma20:
        return "多头"
    if price < ma5 < ma20:
        return "空头"
    if price >= ma20:
        return "震荡偏强"
    return "震荡偏弱"


def _dedupe_sector_rows(rows) -> list:
    seen: set[str] = set()
    out = []
    for row in rows:
        name = str(row[0])
        if name in seen:
            continue
        seen.add(name)
        out.append(row)
    return out


def _read_key(timeout: float) -> str | None:
    with _cbreak_stdin():
        readable, _w, _x = select.select([sys.stdin], [], [], timeout)
        if not readable:
            return None
        return sys.stdin.read(1)


@contextmanager
def _cbreak_stdin():
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _format_interval(seconds: int) -> str:
    if seconds % 60 == 0:
        minutes = seconds // 60
        return f"{minutes} 分钟"
    return f"{seconds} 秒"


def _short_time(value: str | None) -> str:
    if not value:
        return "--"
    if len(value) >= 19:
        return value[11:19]
    return value


def _short_date(value: str | None) -> str:
    if not value:
        return "--"
    if len(value) >= 10:
        return value[5:10]
    return value


def _short_datetime(value: str | None) -> str:
    if not value:
        return "--"
    text = value.replace("T", " ")
    if len(text) >= 16:
        return text[5:16]
    return text


def _json_list_preview(raw: str | None, *, limit: int) -> str:
    if not raw:
        return "--"
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return "--"
    if not isinstance(value, list) or not value:
        return "--"
    return "、".join(str(x) for x in value[:limit])


def _sentiment_text(sentiment: int | None) -> Text:
    value = int(sentiment or 0)
    if value > 0:
        return Text("利好", style="red")
    if value < 0:
        return Text("利空", style="green")
    return Text("中性", style="dim")


def _progress_text(completed: int, total: int, *, width: int = 36) -> str:
    total = max(total, 1)
    completed = min(max(completed, 0), total)
    filled = int(width * completed / total)
    return f"[{'#' * filled}{'-' * (width - filled)}] {completed}/{total}"


def _format_realtime_pct(rt: dict | None) -> str:
    if not rt:
        return "--"
    pct = _percent_units(rt.get("estimate_pct"))
    estimate_time = rt.get("estimate_time") or ""
    fetched_at = rt.get("fetched_at") or ""
    marker = _stale_marker(estimate_time or fetched_at)
    return f"{pct}{marker}"


def _stale_marker(value: str | None) -> str:
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        try:
            dt = datetime.strptime(value[:10], "%Y-%m-%d")
        except ValueError:
            return ""
    today = datetime.now().date()
    if dt.date() < today:
        return " 昨"
    return ""


def _portfolio_metrics_from_returns(
    returns_panel,
    weights: dict[str, float],
    risk_free_rate: float,
) -> dict[str, float] | None:
    if not returns_panel:
        return None
    import pandas as pd

    from .analytics import max_drawdown, sharpe_ratio

    panel = pd.concat(returns_panel, axis=1).dropna(how="all").fillna(0.0)
    codes = [c for c in weights if c in panel.columns]
    if not codes:
        return None
    w = pd.Series({c: weights[c] for c in codes}, dtype=float)
    w = w / w.sum()
    port_ret = panel[codes].mul(w, axis=1).sum(axis=1)
    if port_ret.empty:
        return None
    return {
        "sharpe": sharpe_ratio(port_ret, risk_free_rate),
        "max_drawdown": max_drawdown(port_ret),
    }


def _pct(v) -> str:
    if v is None:
        return "--"
    try:
        return f"{float(v):+.2%}"
    except Exception:
        return "--"


def _ratio_pct(v) -> str:
    if v is None:
        return "--"
    try:
        return f"{float(v) * 100:+.2f}%"
    except Exception:
        return "--"


def _styled_ratio_pct(v) -> Text | str:
    if v is None:
        return "--"
    try:
        n = float(v)
    except Exception:
        return "--"
    style = "red" if n >= 0 else "green"
    return Text(f"{n * 100:+.2f}%", style=style)


def _styled_percent_units(v) -> Text | str:
    if v is None:
        return "--"
    try:
        n = float(v)
    except Exception:
        return "--"
    style = "red" if n >= 0 else "green"
    return Text(f"{n:+.2f}%", style=style)


def _yuan_yi(v) -> str:
    if v is None:
        return "--"
    try:
        return f"{float(v) / 100_000_000:.0f}亿"
    except Exception:
        return "--"


def _percent_units(v) -> str:
    if v is None:
        return "--"
    try:
        return f"{float(v):+.2f}%"
    except Exception:
        return "--"


def _num(v) -> str:
    if v is None:
        return "--"
    try:
        return f"{float(v):.2f}"
    except Exception:
        return "--"
