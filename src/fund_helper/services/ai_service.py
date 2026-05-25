"""AI service: 把用户文本发给配置中的大模型，返回纯文本回答.

支持三种协议（与项目 config.ai.protocol 对齐）:
  - anthropic        : POST {base_url}/messages
  - openai_chat      : POST {base_url}/chat/completions
  - openai_responses : POST {base_url}/responses

api_key == "" 或 "EMPTY" 时不发送 Authorization 头（与 buyer 偏好一致）。
本地自签 HTTPS 时 verify=False。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path as _Path
from typing import Any

import requests

log = logging.getLogger(__name__)


def _headers(api_key: str, protocol: str) -> dict[str, str]:
    h: dict[str, str] = {"Content-Type": "application/json"}
    if api_key and api_key.upper() != "EMPTY":
        if protocol == "anthropic":
            h["x-api-key"] = api_key
            h["anthropic-version"] = "2023-06-01"
        else:
            h["Authorization"] = f"Bearer {api_key}"
    elif protocol == "anthropic":
        h["anthropic-version"] = "2023-06-01"
    return h


def _verify_for(base_url: str) -> bool:
    return not base_url.startswith("https://127.0.0.1") and not base_url.startswith("https://localhost")


def chat(cfg, prompt: str) -> dict[str, Any]:
    """返回 {'text': str, 'raw': dict}; 错误抛 RuntimeError."""
    ai = cfg.ai
    if not ai.enabled:
        raise RuntimeError("AI 功能未启用（settings.yaml → ai.enabled = true）")
    if not ai.base_url or not ai.model:
        raise RuntimeError("ai.base_url / ai.model 未配置")

    proto = ai.protocol
    url_base = ai.base_url.rstrip("/")
    headers = _headers(ai.api_key, proto)
    verify = _verify_for(ai.base_url)

    if proto == "anthropic":
        url = f"{url_base}/messages"
        body: dict[str, Any] = {
            "model": ai.model,
            "max_tokens": ai.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if ai.system_prompt:
            body["system"] = ai.system_prompt
    elif proto == "openai_chat":
        url = f"{url_base}/chat/completions"
        msgs: list[dict[str, Any]] = []
        if ai.system_prompt:
            msgs.append({"role": "system", "content": ai.system_prompt})
        msgs.append({"role": "user", "content": prompt})
        body = {
            "model": ai.model,
            "max_tokens": ai.max_tokens,
            "messages": msgs,
        }
    elif proto == "openai_responses":
        url = f"{url_base}/responses"
        body = {
            "model": ai.model,
            "max_output_tokens": ai.max_tokens,
            "input": prompt,
        }
        if ai.system_prompt:
            body["instructions"] = ai.system_prompt
    else:
        raise RuntimeError(f"未知协议: {proto}")

    log.info("ai call protocol=%s url=%s model=%s prompt_len=%d", proto, url, ai.model, len(prompt))
    r = requests.post(url, headers=headers, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                      timeout=ai.timeout, verify=verify)
    if not r.ok:
        raise RuntimeError(f"上游 {r.status_code}: {r.text[:400]}")

    data = r.json()
    text = _extract_text(data, proto)
    return {"text": text, "raw": data}


def _extract_text(data: dict[str, Any], proto: str) -> str:
    try:
        if proto == "anthropic":
            chunks = data.get("content") or []
            return "".join(c.get("text", "") for c in chunks if c.get("type") == "text").strip()
        if proto == "openai_chat":
            return ((data.get("choices") or [{}])[0]
                    .get("message", {}).get("content", "")).strip()
        if proto == "openai_responses":
            if "output_text" in data:
                return (data["output_text"] or "").strip()
            out = data.get("output") or []
            for item in out:
                for c in (item.get("content") or []):
                    if c.get("type") in ("output_text", "text"):
                        return (c.get("text") or "").strip()
            return ""
    except Exception as e:  # noqa: BLE001
        log.warning("ai parse failed: %s", e)
    return json.dumps(data, ensure_ascii=False)


# =============================================================================
# 一键分析：大盘 / 板块
# =============================================================================


def _read_buyer_profile() -> str:
    for p in (_Path("buyer.md"), _Path(__file__).resolve().parents[3] / "buyer.md"):
        try:
            if p.exists():
                return p.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return "(未提供 buyer.md)"


def _fmt_pct(v) -> str:
    if v is None:
        return "--"
    try:
        return f"{float(v):+.2f}%"
    except Exception:
        return str(v)


def _fmt_ratio_pct(v) -> str:
    if v is None:
        return "--"
    try:
        return f"{float(v) * 100:+.2f}%"
    except Exception:
        return str(v)


def _fmt_ret(v) -> str:
    if v is None:
        return "--"
    try:
        return f"{float(v):+.2%}"
    except Exception:
        return str(v)


def _build_data_quality(cfg) -> str:
    """Summarize freshness/coverage so the model can down-weight stale context."""
    try:
        from ..portfolio.holdings import load_holdings
        from ..storage import connect

        conn = connect(cfg.data_dir / "fund.db")
        lines = [
            "| 数据域 | 覆盖与时间 | 使用提示 |",
            "|---|---|---|",
        ]

        try:
            h = load_holdings()
            codes = [p.code for p in h.positions]
            latest_dates: list[str] = []
            missing: list[str] = []
            for p in h.positions:
                row = conn.execute(
                    "SELECT COUNT(*), MAX(trade_date), MAX(fetched_at) "
                    "FROM nav_daily WHERE code=?",
                    (p.code,),
                ).fetchone()
                if row and row[0]:
                    latest_dates.append(str(row[1]))
                else:
                    missing.append(p.code)
            date_span = (
                f"{min(latest_dates)} ~ {max(latest_dates)}"
                if latest_dates else "无本地净值"
            )
            hint = "可用于持仓近期收益/回撤"
            if missing:
                hint += f"；缺失: {', '.join(missing)}"
            lines.append(f"| 持仓基金净值 | {len(latest_dates)}/{len(codes)} 有数据；{date_span} | {hint} |")
        except Exception as e:  # noqa: BLE001
            lines.append(f"| 持仓基金净值 | 读取失败 | {e} |")

        row = conn.execute(
            "SELECT COUNT(*), SUM(CASE WHEN now IS NOT NULL THEN 1 ELSE 0 END), "
            "MAX(fetched_at) FROM index_snapshot"
        ).fetchone()
        if row and row[0]:
            lines.append(
                f"| 大盘指数现货 | {int(row[1] or 0)}/{int(row[0] or 0)} 有报价；"
                f"最近抓取 {row[2] or '--'} | 现货只代表抓取时点，非实时逐笔行情 |"
            )
        else:
            lines.append("| 大盘指数现货 | 无本地快照 | 若抓取失败，只能做低置信度判断 |")

        rows = conn.execute(
            "SELECT category, COUNT(*), MAX(fetched_at) "
            "FROM sector_snapshot GROUP BY category ORDER BY category"
        ).fetchall()
        if rows:
            desc = "；".join(f"{cat}: {cnt}条/{ts or '--'}" for cat, cnt, ts in rows)
            lines.append(f"| 板块行情 | {desc} | 关注涨跌幅、领涨股和题材覆盖，避免只看单日波动 |")
        else:
            lines.append("| 板块行情 | 无本地快照 | 板块结论需降级为观察 |")

        rows = conn.execute(
            "SELECT category, COUNT(*), "
            "SUM(CASE WHEN relevance_score > 0 THEN 1 ELSE 0 END), MAX(fetched_at) "
            "FROM news_item GROUP BY category ORDER BY category"
        ).fetchall()
        if rows:
            desc = "；".join(
                f"{cat}: {cnt}条/相关{int(rel or 0)}条/{ts or '--'}"
                for cat, cnt, rel, ts in rows
            )
            lines.append(f"| 新闻与政策 | {desc} | 优先使用高相关度新闻，低相关新闻仅作宏观背景 |")
        else:
            lines.append("| 新闻与政策 | 无本地新闻 | 不要臆测消息面，只能提示等待刷新 |")

        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        return f"(数据质量摘要生成失败: {e})"


def _build_market_snapshot(cfg, *, force_refresh: bool = False) -> str:
    try:
        from .market_service import MarketService
        panel = MarketService(cfg).get_a_share_panel(force_refresh=force_refresh)
    except Exception as e:
        return f"(大盘数据获取失败: {e})"
    lines = [f"时间: {panel.refreshed_at}  会话: {'盘中' if panel.is_session else '非盘中'}  源: {panel.source_used}"]
    lines.append("| 指数 | 现价 | 涨跌幅 | 昨收 | 最高 | 最低 |")
    lines.append("|---|---|---|---|---|---|")
    for q in panel.a_share:
        lines.append(
            f"| {q.name}({q.secid}) | {q.now if q.now is not None else '--'} | "
            f"{_fmt_ratio_pct(q.pct)} | {q.pre_close if q.pre_close is not None else '--'} | "
            f"{q.high if q.high is not None else '--'} | {q.low if q.low is not None else '--'} |"
        )
    return "\n".join(lines)


def _build_sector_snapshot(cfg, top_n: int = 15, *, force_refresh: bool = False) -> str:
    try:
        from .sector_service import SectorService
        panel = SectorService(cfg).get_panel(force_refresh=force_refresh)
    except Exception as e:
        return f"(板块数据获取失败: {e})"
    parts = [f"时间: {panel.refreshed_at}  会话: {'盘中' if panel.is_session else '非盘中'}  源: {panel.source_used}"]

    def _render(title: str, rows) -> None:
        if not rows:
            return
        parts.append(f"\n### {title}")
        parts.append("| 板块 | 涨跌幅 | 家数 | 领涨股 | 领涨股涨跌幅 |")
        parts.append("|---|---|---|---|---|")
        for r in rows:
            parts.append(
                f"| {r.name} | {_fmt_pct(r.pct)} | {r.companies if r.companies is not None else '--'} | "
                f"{r.leader_name or '--'} | {_fmt_pct(r.leader_pct)} |"
            )

    _render("我重点关注的板块", panel.watch_rows[:top_n])
    _render(f"行业板块 涨幅 Top{top_n}", panel.rows_by_category.get("industry", [])[:top_n])
    _render(f"行业板块 跌幅 Top{top_n}", list(reversed(panel.rows_by_category.get("industry", [])))[:top_n])
    _render(f"概念板块 涨幅 Top{top_n}", panel.rows_by_category.get("concept", [])[:top_n])
    _render(f"概念板块 跌幅 Top{top_n}", list(reversed(panel.rows_by_category.get("concept", [])))[:top_n])
    return "\n".join(parts)


def _build_news_digest(cfg, per_cat: int = 8, *, force_refresh: bool = False) -> str:
    try:
        from .news_service import get_news_panel, CATEGORIES, CATEGORY_LABELS
        from ..storage import connect
        conn = connect(cfg.data_dir / "fund.db")
        panel = get_news_panel(conn, force_refresh=force_refresh)
    except Exception as e:
        return f"(新闻获取失败: {e})"
    parts = [f"新闻时间: {panel.refreshed_at}"]
    for c in CATEGORIES:
        items = sorted(
            panel.items_by_category.get(c, []),
            key=lambda it: (it.relevance_score, it.sentiment != 0, it.published_at),
            reverse=True,
        )[:per_cat]
        if not items:
            continue
        parts.append(f"\n### {CATEGORY_LABELS[c]}")
        for it in items:
            tag = "利好" if it.sentiment > 0 else ("利空" if it.sentiment < 0 else "中性")
            ts = getattr(it, "published_at", "") or ""
            src_name = getattr(it, "source", "") or ""
            rel = ""
            if it.relevance_score > 0:
                themes = "、".join(it.themes[:3])
                hits = "、".join(it.kw_hits[:5])
                rel = f"；相关度 {it.relevance_score:.2f}（{themes}；{hits}）"
            parts.append(f"- [{tag}] [{src_name} {ts}] {it.title}{rel}")
    return "\n".join(parts)


def _build_holdings(cfg) -> str:
    try:
        from ..portfolio.holdings import load_holdings
        from ..analytics import max_drawdown
        from .nav_service import NavService

        h = load_holdings()
        svc = NavService(cfg)
        lines = [
            f"持仓组合: {h.name}  as_of: {h.as_of}",
            "| 基金 | 权重 | 净值日期 | 近7日 | 近1月 | 近3月 | 近6月 | 近6月最大回撤 |",
            "|---|---:|---|---:|---:|---:|---:|---:|",
        ]
        for p in h.positions:
            try:
                series, _ = svc.get_nav(p.code, lookback_days=200)
                f = series.frame
            except Exception as e:
                lines.append(f"| {p.code} {p.name} | {p.weight:.2%} | 获取失败: {e} | -- | -- | -- | -- | -- |")
                continue
            if f.empty:
                lines.append(f"| {p.code} {p.name} | {p.weight:.2%} | 无净值 | -- | -- | -- | -- | -- |")
                continue

            def _window_return(days: int) -> float | None:
                import pandas as pd
                from datetime import timedelta

                cutoff = pd.Timestamp.today().normalize() - timedelta(days=days)
                sub = f[f.index >= cutoff]
                unit = sub["unit_nav"].dropna() if "unit_nav" in sub.columns else sub.iloc[:, 0].dropna()
                if len(unit) < 2 or not unit.iloc[0]:
                    return None
                return float(unit.iloc[-1] / unit.iloc[0] - 1.0)

            rets = f["daily_return"].dropna() if "daily_return" in f.columns else None
            mdd = float(max_drawdown(rets)) if rets is not None and not rets.empty else None
            last_date = f.index.max().date().isoformat()
            lines.append(
                f"| {p.code} {p.name} | {p.weight:.2%} | {last_date} | "
                f"{_fmt_ret(_window_return(7))} | {_fmt_ret(_window_return(30))} | "
                f"{_fmt_ret(_window_return(90))} | {_fmt_ret(_window_return(180))} | "
                f"{_fmt_ret(mdd)} |"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"(持仓加载失败: {e})"


def _render_template(tmpl: str, **vars: str) -> str:
    out = tmpl
    for k, v in vars.items():
        out = out.replace("{" + k + "}", v)
    return out


def _analyze(cfg, template: str, snapshot_kind: str, *, force_refresh: bool = False) -> dict[str, Any]:
    """snapshot_kind: 'market' | 'sector'"""
    if not template:
        raise RuntimeError(f"prompt 模板未配置 ({snapshot_kind})，请检查 configs/prompts.yaml")
    from datetime import datetime, timezone, timedelta
    as_of = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    framework = cfg.prompts.output_framework or ""

    vars_ = {
        "as_of": as_of,
        "buyer_profile": _read_buyer_profile(),
        "data_quality": _build_data_quality(cfg),
        "holdings": _build_holdings(cfg),
        "news_digest": _build_news_digest(cfg, force_refresh=force_refresh),
        "output_framework": framework,
        "market_snapshot": "(本任务未注入)",
        "sector_snapshot": "(本任务未注入)",
    }
    if snapshot_kind == "market":
        vars_["market_snapshot"] = _build_market_snapshot(cfg, force_refresh=force_refresh)
    elif snapshot_kind == "sector":
        vars_["sector_snapshot"] = _build_sector_snapshot(cfg, force_refresh=force_refresh)

    prompt = _render_template(template, **vars_)
    log.info("ai analyze kind=%s prompt_len=%d", snapshot_kind, len(prompt))
    result = chat(cfg, prompt)
    result["prompt"] = prompt
    return result


def analyze_market(cfg, *, force_refresh: bool = False) -> dict[str, Any]:
    return _analyze(cfg, cfg.prompts.market_analysis, "market", force_refresh=force_refresh)


def analyze_sectors(cfg, *, force_refresh: bool = False) -> dict[str, Any]:
    return _analyze(cfg, cfg.prompts.sector_analysis, "sector", force_refresh=force_refresh)
