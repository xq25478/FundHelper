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
import re
import time
from pathlib import Path as _Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

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


def _safe_url(url: str) -> str:
    """Remove userinfo/query fragments before printing request metadata."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        return urlunsplit((parts.scheme, host, parts.path, "", ""))
    except Exception:
        return url.split("?", 1)[0]


def format_ai_call_info(meta: dict[str, Any] | None) -> str:
    """Format model call metadata for CLI/TUI output without leaking secrets."""
    if not meta:
        return "大模型调用信息：无"
    elapsed = meta.get("elapsed_seconds")
    elapsed_s = "--" if elapsed is None else f"{float(elapsed):.2f}s"
    return (
        "大模型调用信息："
        f"protocol={meta.get('protocol', '--')} | "
        f"url={meta.get('url', '--')} | "
        f"model={meta.get('model', '--')} | "
        f"auth={meta.get('auth', '--')} | "
        f"timeout={meta.get('timeout_seconds', '--')}s | "
        f"max_tokens={meta.get('max_tokens', '--')} | "
        f"prompt_chars={meta.get('prompt_chars', '--')} | "
        f"system_prompt_chars={meta.get('system_prompt_chars', '--')} | "
        f"response_chars={meta.get('response_chars', '--')} | "
        f"elapsed={elapsed_s}"
    )


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

    meta: dict[str, Any] = {
        "protocol": proto,
        "url": _safe_url(url),
        "model": ai.model,
        "auth": "enabled" if ai.api_key and ai.api_key.upper() != "EMPTY" else "empty",
        "timeout_seconds": ai.timeout,
        "max_tokens": ai.max_tokens,
        "prompt_chars": len(prompt),
        "system_prompt_chars": len(ai.system_prompt or ""),
        "verify_tls": verify,
    }
    log.info("ai call protocol=%s url=%s model=%s prompt_len=%d", proto, url, ai.model, len(prompt))
    t0 = time.perf_counter()
    r = requests.post(url, headers=headers, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                      timeout=ai.timeout, verify=verify)
    meta["elapsed_seconds"] = time.perf_counter() - t0
    meta["status_code"] = r.status_code
    if not r.ok:
        raise RuntimeError(f"上游 {r.status_code}: {r.text[:400]} ({format_ai_call_info(meta)})")

    data = r.json()
    text = _extract_text(data, proto)
    meta["response_chars"] = len(text)
    return {"text": text, "raw": data, "ai_call": meta}


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
    import yaml as _yaml
    # 优先从 config.yml 读取
    user_path = _Path("config.yml")
    if user_path.exists():
        raw = _yaml.safe_load(user_path.read_text(encoding="utf-8")) or {}
        bp = raw.get("buyer_profile", "").strip()
        if bp:
            return bp
    # 回退到 buyer.md
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
            from .trading_calendar import TradingCalendarService

            cal = TradingCalendarService(cfg).get_calendar()
            max_date = max(cal.dates).isoformat() if cal.dates else "--"
            hint = f"用于下一个交易日/7天/本月窗口计算；覆盖至 {max_date}"
            if cal.error:
                hint += f"；最近刷新异常: {cal.error}"
            lines.append(
                f"| A股交易日历 | {len(cal.dates)} 个交易日；来源 {cal.source}；"
                f"刷新 {cal.fetched_at or '--'} | {hint} |"
            )
        except Exception as e:  # noqa: BLE001
            lines.append(f"| A股交易日历 | 读取失败 | 将退回工作日推算；{e} |")

        holding_codes: list[str] = []
        try:
            h = load_holdings()
            codes = [p.code for p in h.positions]
            holding_codes = codes
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
            "SUM(CASE WHEN volume IS NOT NULL THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN amount IS NOT NULL THEN 1 ELSE 0 END), "
            "MAX(fetched_at) FROM index_snapshot"
        ).fetchone()
        if row and row[0]:
            lines.append(
                f"| 大盘指数现货 | {int(row[1] or 0)}/{int(row[0] or 0)} 有报价；"
                f"量能 {int(row[2] or 0)}/{int(row[0] or 0)}；成交额 {int(row[3] or 0)}/{int(row[0] or 0)}；"
                f"最近抓取 {row[4] or '--'} | 现货只代表抓取时点，盘中成交量/额不可直接等同全天量 |"
            )
        else:
            lines.append("| 大盘指数现货 | 无本地快照 | 若抓取失败，只能做低置信度判断 |")

        row = conn.execute(
            "SELECT COUNT(*), "
            "SUM(CASE WHEN scope='market' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN scope='market_intraday' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN scope='northbound' THEN 1 ELSE 0 END), "
            "MAX(fetched_at) FROM market_flow_snapshot"
        ).fetchone()
        if row and row[0]:
            lines.append(
                f"| 大盘资金流 | 盘中资金 {int(row[2] or 0)} 条；盘后主力 {int(row[1] or 0)} 条；"
                f"北向摘要 {int(row[3] or 0)} 条；最近抓取 {row[4] or '--'} | "
                "仅使用公开源成功返回的缓存，抓取失败时不得自行推断资金流 |"
            )
        else:
            lines.append("| 大盘资金流 | 无本地快照 | 禁止输出主力资金/北向资金结论，只能说明数据缺口 |")

        row = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT secid), MAX(ts), MAX(fetched_at) "
            "FROM index_intraday"
        ).fetchone()
        if row and row[0]:
            lines.append(
                f"| 指数分钟线 | {int(row[1] or 0)} 个指数；{int(row[0] or 0)} 条；"
                f"最新分钟 {row[2] or '--'}；抓取 {row[3] or '--'} | 可用于日内走势，不代表逐笔盘口 |"
            )
        else:
            lines.append("| 指数分钟线 | 无本地快照 | 无法做日内曲线结构分析 |")

        row = conn.execute(
            "SELECT COUNT(*), MAX(trade_date), MAX(fetched_at) FROM market_margin_snapshot"
        ).fetchone()
        if row and row[0]:
            lines.append(
                f"| 融资融券 | {int(row[0] or 0)} 个市场；交易日 {row[1] or '--'}；"
                f"抓取 {row[2] or '--'} | 通常滞后一日披露，只作杠杆资金背景 |"
            )
        else:
            lines.append("| 融资融券 | 无本地快照 | 不得输出两融方向判断 |")

        if holding_codes:
            placeholders = ",".join("?" for _ in holding_codes)
            row = conn.execute(
                f"""SELECT COUNT(*), SUM(CASE WHEN estimate_pct IS NOT NULL THEN 1 ELSE 0 END),
                           MAX(estimate_time), MAX(fetched_at)
                    FROM fund_realtime_snapshot WHERE code IN ({placeholders})""",
                holding_codes,
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*), SUM(CASE WHEN estimate_pct IS NOT NULL THEN 1 ELSE 0 END), "
                "MAX(estimate_time), MAX(fetched_at) FROM fund_realtime_snapshot"
            ).fetchone()
        if row and row[0]:
            lines.append(
                f"| 基金当日公开估值 | {int(row[1] or 0)}/{int(row[0] or 0)} 有估值涨跌；"
                f"估值时间 {row[2] or '--'}；抓取 {row[3] or '--'} | 来自公开估值源，非本项目自行估算，亦非最终披露净值 |"
            )
        else:
            lines.append("| 基金当日公开估值 | 无本地快照 | 若需当日涨跌，先刷新公开估值源；不得自行估算 |")

        rows = conn.execute(
            "SELECT category, COUNT(*), MAX(fetched_at) "
            "FROM sector_snapshot GROUP BY category ORDER BY category"
        ).fetchall()
        if rows:
            desc = "；".join(f"{cat}: {cnt}条/{ts or '--'}" for cat, cnt, ts in rows)
            lines.append(f"| 板块行情 | {desc} | 关注涨跌幅、领涨股和题材覆盖，避免只看单日波动 |")
        else:
            lines.append("| 板块行情 | 无本地快照 | 板块结论需降级为观察 |")

        if holding_codes:
            placeholders = ",".join("?" for _ in holding_codes)
            row = conn.execute(
                f"SELECT COUNT(DISTINCT code), MAX(nav_date), MAX(fetched_at) "
                f"FROM fund_peer_rank_snapshot WHERE code IN ({placeholders})",
                holding_codes,
            ).fetchone()
            if row and row[0]:
                lines.append(
                    f"| 同类基金排行 | {int(row[0] or 0)}/{len(holding_codes)} 只；"
                    f"净值日 {row[1] or '--'}；抓取 {row[2] or '--'} | 只代表公开收益区间排名 |"
                )
            else:
                lines.append("| 同类基金排行 | 无持仓排行缓存 | 不得输出同类排名或均值结论 |")

        row = conn.execute(
            "SELECT COUNT(*), MAX(trade_date), MAX(fetched_at) FROM index_valuation_snapshot"
        ).fetchone()
        if row and row[0]:
            lines.append(
                f"| 指数估值分位 | {int(row[0] or 0)} 个指数；估值日 {row[1] or '--'}；"
                f"抓取 {row[2] or '--'} | 用于历史相对位置，不用于短线确定性判断 |"
            )
        else:
            lines.append("| 指数估值分位 | 无本地快照 | 不得输出估值水位判断 |")

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

        row = conn.execute(
            "SELECT COUNT(DISTINCT company_key), COUNT(*), MAX(fetched_at) FROM company_news_match"
        ).fetchone()
        if row and row[1]:
            lines.append(
                f"| 市场动态（重点公司消息） | {int(row[0] or 0)} 家公司，命中 {int(row[1] or 0)} 条；"
                f"抓取 {row[2] or '--'} | 用于跟踪手工配置公司和持仓基金前十大重仓股的公开消息 |"
            )
        else:
            lines.append("| 市场动态（重点公司消息） | 暂无命中 | 可自动跟踪手工配置公司和持仓基金前十大重仓股 |")

        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        return f"(数据质量摘要生成失败: {e})"


def _build_data_availability_section(cfg) -> str:
    """Deterministic report section for data availability.

    This section is post-processed into full reports so the model cannot
    accidentally mark available cached data as missing.
    """
    try:
        from ..portfolio.holdings import load_holdings
        from ..storage import connect

        conn = connect(cfg.data_dir / "fund.db")
        holdings = load_holdings()
        codes = [p.code for p in holdings.positions]
        lines = [
            "# 零、数据可信度与缺失原因",
            "",
            "| 数据域 | 状态 | 说明 |",
            "|---|---|---|",
        ]
        lines.append(_availability_nav_row(conn, codes))
        lines.append(_availability_realtime_row(conn, codes))
        lines.append(_availability_index_row(conn))
        lines.append(_availability_intraday_row(conn))
        lines.append(_availability_market_flow_row(conn))
        lines.append(_availability_margin_row(conn))
        lines.append(_availability_sector_row(conn))
        lines.append(_availability_news_row(conn))
        lines.append(_availability_company_watch_row(conn, cfg))
        lines.append(_availability_xray_row(conn, codes))
        lines.append(_availability_peer_rank_row(conn, codes))
        lines.append(_availability_valuation_row(conn))
        lines.extend([
            "",
            "使用边界：上表由程序根据本地 SQLite 缓存生成。若正文与本节冲突，以本节为准；已标为可用的数据不得在正文中再次声称整体缺失。"
            "当前只接入指数1分钟K线，未接入逐笔成交或盘口队列，不能做微观盘口推断。",
        ])
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        return "\n".join([
            "# 零、数据可信度与缺失原因",
            "",
            f"- 程序生成数据可信度表失败：{e}",
            "- 因数据可用性无法复核，报告结论需整体降级为观察。",
        ])


def _availability_nav_row(conn, codes: list[str]) -> str:
    if not codes:
        return "| 持仓基金净值 | ❌ 缺失 | 当前持仓为空，无法计算基金绩效。 |"
    placeholders = ",".join("?" for _ in codes)
    rows = conn.execute(
        f"""SELECT code, COUNT(*), MAX(trade_date), MAX(fetched_at)
            FROM nav_daily WHERE code IN ({placeholders}) GROUP BY code""",
        codes,
    ).fetchall()
    by_code = {row[0]: row for row in rows}
    ok_codes = [code for code in codes if by_code.get(code) and by_code[code][1]]
    dates = [str(by_code[code][2]) for code in ok_codes if by_code[code][2]]
    missing = [code for code in codes if code not in ok_codes]
    status = "✅ 可用" if len(ok_codes) == len(codes) else ("⚠️ 部分可用" if ok_codes else "❌ 缺失")
    span = f"{min(dates)} ~ {max(dates)}" if dates else "--"
    detail = f"{len(ok_codes)}/{len(codes)} 只基金有净值数据，净值日 {span}；可用于收益、波动、风险收益比(Sharpe)、最大回撤。"
    if missing:
        detail += f" 缺失：{', '.join(missing)}。"
    return f"| 持仓基金净值 | {status} | {detail} |"


def _availability_realtime_row(conn, codes: list[str]) -> str:
    if not codes:
        return "| 基金当日公开估值 | ❌ 缺失 | 当前持仓为空。 |"
    placeholders = ",".join("?" for _ in codes)
    rows = conn.execute(
        f"""SELECT code, estimate_pct, estimate_time, fetched_at, source
            FROM fund_realtime_snapshot WHERE code IN ({placeholders})""",
        codes,
    ).fetchall()
    by_code = {row[0]: row for row in rows}
    ok = [code for code in codes if by_code.get(code) and by_code[code][1] is not None]
    times = [str(by_code[code][2]) for code in ok if by_code[code][2]]
    sources = sorted({str(by_code[code][4]) for code in ok if by_code[code][4]})
    status = "✅ 可用" if len(ok) == len(codes) else ("⚠️ 部分可用" if ok else "❌ 缺失")
    latest = max(times) if times else "--"
    detail = (
        f"{len(ok)}/{len(codes)} 只基金有公开估值涨跌；估值时点 {latest}；"
        f"来源 {', '.join(sources) if sources else '--'}。该数据非本项目估算，亦非基金公司最终净值。"
    )
    missing = [code for code in codes if code not in ok]
    if missing:
        detail += f" 缺失：{', '.join(missing)}。"
    return f"| 基金当日公开估值 | {status} | {detail} |"


def _availability_index_row(conn) -> str:
    row = conn.execute(
        """SELECT COUNT(*),
                  SUM(CASE WHEN now IS NOT NULL THEN 1 ELSE 0 END),
                  SUM(CASE WHEN volume IS NOT NULL THEN 1 ELSE 0 END),
                  SUM(CASE WHEN amount IS NOT NULL THEN 1 ELSE 0 END),
                  MAX(fetched_at)
           FROM index_snapshot"""
    ).fetchone()
    total = int(row[0] or 0) if row else 0
    if not total:
        return "| 大盘现货量价 | ❌ 缺失 | 无指数快照缓存。 |"
    quote = int(row[1] or 0)
    volume = int(row[2] or 0)
    amount = int(row[3] or 0)
    status = "✅ 可用" if quote == volume == amount == total else "⚠️ 部分可用"
    return (
        f"| 大盘现货量价 | {status} | {quote}/{total} 有报价，{volume}/{total} 有成交量，"
        f"{amount}/{total} 有成交额；最近抓取 {row[4] or '--'}。可做指数快照量价分析，但不可替代逐笔/分时成交。 |"
    )


def _availability_market_flow_row(conn) -> str:
    row = conn.execute(
        """SELECT COUNT(*),
                  SUM(CASE WHEN scope='market' THEN 1 ELSE 0 END),
                  SUM(CASE WHEN scope='market_intraday' THEN 1 ELSE 0 END),
                  SUM(CASE WHEN scope='northbound' THEN 1 ELSE 0 END),
                  MAX(fetched_at)
           FROM market_flow_snapshot"""
    ).fetchone()
    total = int(row[0] or 0) if row else 0
    if not total:
        return "| 大盘资金流（主力/北向） | ❌ 缺失 | 无资金流缓存；不能输出主力资金或北向资金结论。 |"
    market = int(row[1] or 0)
    intraday = int(row[2] or 0)
    north = int(row[3] or 0)
    dates = conn.execute(
        "SELECT scope, GROUP_CONCAT(DISTINCT trade_date) FROM market_flow_snapshot GROUP BY scope"
    ).fetchall()
    from datetime import datetime, timedelta, timezone

    today = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    date_map = {scope: set((raw_dates or "").split(",")) for scope, raw_dates in dates}
    current_day_complete = (
        market
        and north
        and (today in date_map.get("market", set()) or today in date_map.get("market_intraday", set()))
        and today in date_map.get("northbound", set())
    )
    status = "✅ 可用" if current_day_complete else "⚠️ 部分可用"
    date_desc = "；".join(f"{scope}:{raw_dates or '--'}" for scope, raw_dates in dates)
    stale_note = ""
    if market and today not in date_map.get("market", set()):
        stale_note = " 主力资金未覆盖当前交易日，只能作为上一交易日背景。"
    return (
        f"| 大盘资金流（盘中/主力/北向） | {status} | 盘中资金 {intraday} 条，"
        f"盘后主力 {market} 条，北向摘要 {north} 条；交易日 {date_desc or '--'}；"
        f"最近抓取 {row[4] or '--'}。{stale_note}"
        "只可引用表内公开源缓存，不能推断未返回的资金项。 |"
    )


def _availability_intraday_row(conn) -> str:
    from datetime import datetime, timedelta, timezone

    today = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    row = conn.execute(
        """SELECT COUNT(*), COUNT(DISTINCT secid), MAX(ts), MAX(fetched_at)
           FROM index_intraday WHERE trade_date=?""",
        (today,),
    ).fetchone()
    total = int(row[0] or 0) if row else 0
    if not total:
        return "| 指数分钟线/分时 | ❌ 缺失 | 无今日指数1分钟K线缓存。 |"
    secids = int(row[1] or 0)
    status = "✅ 可用" if secids >= 5 else "⚠️ 部分可用"
    return (
        f"| 指数分钟线/分时 | {status} | 今日 {secids}/5 个宽基指数有1分钟K线，"
        f"共 {total} 条；最新分钟 {row[2] or '--'}；最近抓取 {row[3] or '--'}。该数据不是逐笔成交。 |"
    )


def _availability_margin_row(conn) -> str:
    row = conn.execute(
        "SELECT COUNT(*), GROUP_CONCAT(scope || ':' || COALESCE(trade_date, '--')), MAX(fetched_at) "
        "FROM market_margin_snapshot"
    ).fetchone()
    total = int(row[0] or 0) if row else 0
    if not total:
        return "| 融资融券 | ❌ 缺失 | 无交易所两融汇总缓存。 |"
    status = "✅ 可用" if total >= 2 else "⚠️ 部分可用"
    return (
        f"| 融资融券 | {status} | {total} 个市场有两融汇总；交易日 {row[1] or '--'}；"
        f"最近抓取 {row[2] or '--'}。两融通常滞后一日披露，只能作杠杆资金背景。 |"
    )


def _availability_sector_row(conn) -> str:
    rows = conn.execute(
        "SELECT category, COUNT(*), MAX(fetched_at) FROM sector_snapshot GROUP BY category ORDER BY category"
    ).fetchall()
    if not rows:
        return "| 板块行情 | ❌ 缺失 | 无板块行情缓存。 |"
    desc = "；".join(f"{cat}:{cnt}条/{ts or '--'}" for cat, cnt, ts in rows)
    return f"| 板块行情 | ✅ 可用 | {desc}；可用于今日强弱、领涨股和板块结构判断。 |"


def _availability_news_row(conn) -> str:
    rows = conn.execute(
        """SELECT category, COUNT(*),
                  SUM(CASE WHEN relevance_score > 0 THEN 1 ELSE 0 END),
                  MAX(fetched_at)
           FROM news_item GROUP BY category ORDER BY category"""
    ).fetchall()
    if not rows:
        return "| 新闻与消息面 | ❌ 缺失 | 无新闻缓存。 |"
    total = sum(int(row[1] or 0) for row in rows)
    rel = sum(int(row[2] or 0) for row in rows)
    latest = max((row[3] for row in rows if row[3]), default="--")
    desc = "；".join(f"{cat}:{cnt}条/相关{int(r or 0)}条" for cat, cnt, r, _ts in rows)
    return (
        f"| 新闻与消息面 | ✅ 可用 | 总 {total} 条，相关 {rel} 条；{desc}；"
        f"最近抓取 {latest}。低相关新闻只作宏观背景。 |"
    )


def _availability_company_watch_row(conn, cfg) -> str:
    if not getattr(cfg.company_watch, "enabled", True):
        return "| 市场动态（重点公司消息） | ⚠️ 未启用 | company_watch.enabled=false；不会跟踪公司消息。 |"
    try:
        from .company_watch_service import CompanyWatchService

        targets = CompanyWatchService(cfg).build_targets()
    except Exception as e:  # noqa: BLE001
        return f"| 市场动态（重点公司消息） | ⚠️ 部分可用 | 观察名单生成失败：{e}。 |"
    if not targets:
        return "| 市场动态（重点公司消息） | ⚠️ 未配置 | 未配置公司，且暂无持仓基金前十大重仓股缓存。 |"
    row = conn.execute(
        "SELECT COUNT(DISTINCT company_key), COUNT(*), MAX(published_at), MAX(fetched_at) "
        "FROM company_news_match"
    ).fetchone()
    matched_companies = int(row[0] or 0) if row else 0
    matches = int(row[1] or 0) if row else 0
    if not matches:
        return (
            f"| 市场动态（重点公司消息） | ⚠️ 已启用 | 观察名单 {len(targets)} 家；最近新闻暂未命中公司。"
            "可用于手工配置公司和持仓基金前十大重仓股。 |"
        )
    return (
        f"| 市场动态（重点公司消息） | ✅ 可用 | 观察名单 {len(targets)} 家，"
        f"已命中 {matched_companies} 家/{matches} 条；最新新闻 {row[2] or '--'}；"
        f"最近匹配 {row[3] or '--'}。用于公司级消息跟踪，不等同于个股买卖建议。 |"
    )


def _availability_xray_row(conn, codes: list[str]) -> str:
    if not codes:
        return "| 持仓穿透（重仓股） | ❌ 缺失 | 当前持仓为空。 |"
    placeholders = ",".join("?" for _ in codes)
    rows = conn.execute(
        f"""SELECT fund_code, COUNT(*), MAX(season), MAX(fetched_at)
            FROM fund_top_holding WHERE fund_code IN ({placeholders})
            GROUP BY fund_code""",
        codes,
    ).fetchall()
    by_code = {row[0]: row for row in rows}
    ok = [code for code in codes if by_code.get(code) and int(by_code[code][1] or 0) >= 10]
    status = "✅ 可用" if len(ok) == len(codes) else ("⚠️ 部分可用" if ok else "❌ 缺失")
    seasons = sorted({str(by_code[code][2]) for code in ok if by_code[code][2]})
    detail = f"{len(ok)}/{len(codes)} 只基金有前十大重仓；报告期 {', '.join(seasons) if seasons else '--'}。"
    missing = [code for code in codes if code not in ok]
    if missing:
        detail += f" 缺失：{', '.join(missing)}。"
    return f"| 持仓穿透（重仓股） | {status} | {detail} |"


def _availability_peer_rank_row(conn, codes: list[str]) -> str:
    if not codes:
        return "| 同类基金样本/同类排名 | ❌ 缺失 | 当前持仓为空。 |"
    placeholders = ",".join("?" for _ in codes)
    row = conn.execute(
        f"""SELECT COUNT(DISTINCT code), SUM(total), MAX(fetched_at), MAX(nav_date)
            FROM fund_peer_rank_snapshot WHERE code IN ({placeholders})""",
        codes,
    ).fetchone()
    count = int(row[0] or 0) if row else 0
    if not count:
        return "| 同类基金样本/同类排名 | ❌ 缺失 | 无持仓基金同类排行缓存。 |"
    status = "✅ 可用" if count == len(codes) else "⚠️ 部分可用"
    return (
        f"| 同类基金样本/同类排名 | {status} | {count}/{len(codes)} 只持仓基金有同类收益排行；"
        f"样本累计 {int(row[1] or 0)}；净值日 {row[3] or '--'}；最近抓取 {row[2] or '--'}。"
        "该排行只代表公开收益区间排名，不等同于基金经理能力排名。 |"
    )


def _availability_valuation_row(conn) -> str:
    row = conn.execute(
        "SELECT COUNT(*), SUM(CASE WHEN pe_percentile IS NOT NULL OR pb_percentile IS NOT NULL THEN 1 ELSE 0 END), "
        "MAX(trade_date), MAX(fetched_at) FROM index_valuation_snapshot"
    ).fetchone()
    total = int(row[0] or 0) if row else 0
    if not total:
        return "| 估值分位/PE/PB分位 | ❌ 缺失 | 无指数估值分位缓存。 |"
    with_percentile = int(row[1] or 0)
    status = "✅ 可用" if with_percentile == total else "⚠️ 部分可用"
    return (
        f"| 估值分位/PE/PB分位 | {status} | {with_percentile}/{total} 个指数有PE或PB历史分位；"
        f"估值日 {row[2] or '--'}；最近抓取 {row[3] or '--'}。缺 PB 的指数只可使用 PE 分位。 |"
    )


def _build_expert_context(cfg, *, force_refresh: bool = False) -> dict[str, Any]:
    """Build deterministic facts + rules injected into every analysis prompt."""
    from .fact_pack import build_portfolio_facts
    from .guardrails import (
        EvidenceAvailability,
        evidence_policy_markdown,
        extract_expert_rules,
        risk_rules_markdown,
    )

    buyer_profile = _read_buyer_profile()
    rules = extract_expert_rules(buyer_profile)
    try:
        facts = build_portfolio_facts(cfg, force_refresh=force_refresh)
        fact_pack = facts.to_markdown()
        evidence = facts.evidence
    except Exception as e:  # noqa: BLE001
        evidence = EvidenceAvailability()
        fact_pack = f"## 专家事实包\n\n- 生成失败：{e}\n- 因事实包缺失，所有操作建议必须降级为观察。"
    _enrich_evidence_from_cache(cfg, evidence)

    return {
        "buyer_profile": buyer_profile,
        "rules": rules,
        "evidence": evidence,
        "fact_pack": fact_pack,
        "risk_rules": risk_rules_markdown(rules),
        "evidence_policy": evidence_policy_markdown(evidence),
    }


def _enrich_evidence_from_cache(cfg, evidence) -> None:
    try:
        from ..storage import connect

        conn = connect(cfg.data_dir / "fund.db")
        row = conn.execute(
            "SELECT SUM(CASE WHEN volume IS NOT NULL OR amount IS NOT NULL THEN 1 ELSE 0 END) "
            "FROM index_snapshot"
        ).fetchone()
        turnover = bool(row and row[0])
        row = conn.execute("SELECT COUNT(*) FROM index_intraday").fetchone()
        evidence.turnover_data = turnover or bool(row and row[0])
        row = conn.execute(
            "SELECT SUM(CASE WHEN scope='northbound' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN scope IN ('market', 'market_intraday') THEN 1 ELSE 0 END) "
            "FROM market_flow_snapshot"
        ).fetchone()
        evidence.northbound_data = bool(row and row[0])
        if hasattr(evidence, "main_fund_flow_data"):
            evidence.main_fund_flow_data = bool(row and row[1])
        row = conn.execute("SELECT COUNT(*) FROM fund_peer_rank_snapshot").fetchone()
        evidence.peer_data = bool(row and row[0])
        row = conn.execute("SELECT COUNT(*) FROM index_valuation_snapshot").fetchone()
        evidence.valuation_data = bool(row and row[0])
        row = conn.execute("SELECT COUNT(*) FROM market_margin_snapshot").fetchone()
        evidence.margin_data = bool(row and row[0])
    except Exception as e:  # noqa: BLE001
        log.warning("evidence cache enrichment failed: %s", e)


def _chat_with_guardrails(
    cfg,
    prompt: str,
    *,
    rules,
    evidence,
    postprocess=None,
) -> dict[str, Any]:
    from .guardrails import validate_report_text

    result = chat(cfg, prompt)
    text = result.get("text", "")
    if text and postprocess is not None:
        text = postprocess(text)
    check = validate_report_text(text, rules=rules, evidence=evidence)
    result["guardrails"] = check.to_markdown()
    if text:
        result["text"] = text.rstrip() + "\n\n" + check.to_markdown()
    return result


def _build_market_snapshot(cfg, *, force_refresh: bool = False) -> str:
    try:
        from .market_service import MarketService
        panel = MarketService(cfg).get_a_share_panel(force_refresh=force_refresh)
    except Exception as e:
        return f"(大盘数据获取失败: {e})"
    from .index_daily_service import IndexDailyService
    idx_svc = IndexDailyService(cfg)

    lines = [
        f"时间: {panel.refreshed_at}  盘中: {'是' if panel.is_session else '否'}  源: {panel.source_used}",
        "- 量能说明：成交量/成交额为当前公开快照；盘中只能和历史全日均量做粗略进度对比，不能直接等同全天放量/缩量。",
    ]
    lines.append("| 指数 | 现价 | 涨跌幅 | 成交额 | 当前量/5日均量 | 今日振幅 | 近5日 | 近20日 | 近60日 | K线/均线结构 | 60日高点回撤 |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|")

    for q in panel.a_share:
        pct_5d, pct_20d, pct_60d = "--", "--", "--"
        volume_ratio, structure, high_dd = "--", "--", "--"
        try:
            series = idx_svc.get_series(q.secid, lookback_days=120, force_refresh=False)
            f = series.frame
            if not f.empty:
                f = f.sort_values("trade_date")
                closes = f["close"].astype(float).dropna()
                if len(closes) >= 5:
                    pct_5d = _fmt_pct(float(closes.iloc[-1] / closes.iloc[-min(5, len(closes))] - 1) * 100)
                if len(closes) >= 20:
                    pct_20d = _fmt_pct(float(closes.iloc[-1] / closes.iloc[-min(20, len(closes))] - 1) * 100)
                if len(closes) >= 60:
                    pct_60d = _fmt_pct(float(closes.iloc[-1] / closes.iloc[0] - 1) * 100)
                volume_ratio = _volume_ratio(q.volume, f)
                structure, high_dd = _index_trend_structure(q.now, f)
        except Exception:
            pass
        amplitude = "--"
        if q.high is not None and q.low is not None and q.pre_close and q.pre_close != 0:
            amplitude = f"{float(q.high - q.low) / q.pre_close * 100:+.2f}%"
        lines.append(
            f"| {q.name}({q.secid}) | {q.now if q.now is not None else '--'} | "
            f"{_fmt_ratio_pct(q.pct)} | {_fmt_yuan_yi(q.amount)} | {volume_ratio} | "
            f"{amplitude} | {pct_5d} | {pct_20d} | {pct_60d} | {structure} | {high_dd} |"
        )
    intraday = _build_index_intraday_snapshot(cfg, force_refresh=force_refresh)
    if intraday:
        lines.extend(["", intraday])
    return "\n".join(lines)


def _build_index_intraday_snapshot(cfg, *, force_refresh: bool = False) -> str:
    try:
        from .index_intraday_service import IndexIntradayService, render_intraday_markdown

        panel = IndexIntradayService(cfg).get_panel(force_refresh=force_refresh)
        return render_intraday_markdown(panel)
    except Exception as e:  # noqa: BLE001
        return f"### 指数分钟线结构\n- 获取失败：{e}"


def _build_market_flow_snapshot(cfg, *, force_refresh: bool = False) -> str:
    try:
        from .market_flow_service import MarketFlowService

        panel = MarketFlowService(cfg).get_panel(force_refresh=force_refresh)
    except Exception as e:  # noqa: BLE001
        return f"(资金流数据获取失败: {e})"
    lines = [
        f"时间: {panel.refreshed_at}  源: {panel.source_used}",
        "- 使用边界：仅引用下表中已成功抓取的公开资金流；若主力资金或北向为空，相关结论必须标记为缺数据。",
    ]
    if panel.errors:
        lines.append("- 抓取异常：" + "；".join(panel.errors[:3]))
    if not panel.rows:
        lines.append("- 暂无可用资金流缓存。")
        margin = _build_margin_snapshot(cfg, force_refresh=force_refresh)
        if margin:
            lines.extend(["", margin])
        return "\n".join(lines)
    lines.append("| 范围 | 项目 | 日期 | 净额 | 净占比/指数涨跌 | 涨/平/跌家数 | 来源 |")
    lines.append("|---|---|---|---:|---:|---:|---|")
    for row in panel.rows:
        breadth = "--"
        if row.up_count is not None or row.flat_count is not None or row.down_count is not None:
            breadth = f"{row.up_count or 0}/{row.flat_count or 0}/{row.down_count or 0}"
        lines.append(
            f"| {row.scope} | {row.item} | {row.trade_date or '--'} | "
            f"{_fmt_yuan_yi(row.net_amount)} | {_fmt_pct(row.net_pct)} | {breadth} | {row.source} |"
        )
    margin = _build_margin_snapshot(cfg, force_refresh=force_refresh)
    if margin:
        lines.extend(["", margin])
    return "\n".join(lines)


def _build_margin_snapshot(cfg, *, force_refresh: bool = False) -> str:
    try:
        from .margin_service import MarginService, render_margin_markdown

        panel = MarginService(cfg).get_panel(force_refresh=force_refresh)
        return render_margin_markdown(panel)
    except Exception as e:  # noqa: BLE001
        return f"### 融资融券（交易所公开汇总）\n- 获取失败：{e}"


def _build_main_force_intent(cfg) -> str:
    """Build deterministic evidence for main-force intent analysis.

    "主力意图" is an inference layer. We expose the public evidence and the
    classification path so the model cannot present it as a known trading plan.
    """
    try:
        from ..storage import connect

        conn = connect(cfg.data_dir / "fund.db")
        lines = [
            "## 主力意图分析事实框架（程序生成）",
            "- 使用边界：只能基于公开资金流、指数量价、板块宽度和持仓主题强弱做倾向判断；",
            "  不得写成已确认的主力操盘计划。",
        ]

        flow_rows = _load_intent_flow_rows(conn)
        flow_bias = _render_intent_flow(lines, flow_rows)

        index_rows = _load_intent_index_rows(conn)
        price_bias, style_note = _render_intent_index(lines, index_rows)

        sector_rows = _load_intent_sector_breadth(conn)
        sector_bias = _render_intent_sector_breadth(lines, sector_rows)

        theme_rows = _load_intent_theme_rows(conn)
        theme_bias = _render_intent_theme_rows(lines, theme_rows)

        conclusion = _main_force_conclusion(
            flow_bias=flow_bias,
            price_bias=price_bias,
            sector_bias=sector_bias,
            theme_bias=theme_bias,
            style_note=style_note,
        )
        lines.extend(["", "### 主力意图倾向（非确定事实）", f"- {conclusion}"])
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        return f"(主力意图分析生成失败: {e})"


def _load_intent_flow_rows(conn) -> list[tuple]:
    return conn.execute(
        """SELECT scope,item,trade_date,net_amount,net_pct,main_net_amount,
                  super_large_net_amount,large_net_amount,medium_net_amount,
                  small_net_amount,up_count,flat_count,down_count,source,fetched_at
           FROM market_flow_snapshot
           ORDER BY CASE scope WHEN 'market' THEN 0 WHEN 'northbound' THEN 1 ELSE 2 END,
                    item"""
    ).fetchall()


def _render_intent_flow(lines: list[str], rows: list[tuple]) -> int:
    lines.extend(["", "### 资金流证据"])
    if not rows:
        lines.append("- 无公开资金流缓存；主力意图只能降级为“缺资金流验证”。")
        return 0
    lines.append("| 范围 | 项目 | 日期 | 主力/净额 | 大单合计 | 小单 | 涨/平/跌 | 解读 |")
    lines.append("|---|---|---|---:|---:|---:|---:|---|")
    flow_bias = 0
    for row in rows:
        (
            scope,
            item,
            trade_date,
            net_amount,
            net_pct,
            main_net_amount,
            super_large,
            large,
            _medium,
            small,
            up_count,
            flat_count,
            down_count,
            _source,
            _fetched_at,
        ) = row
        main = main_net_amount if main_net_amount is not None else net_amount
        big = _sum_optional(super_large, large)
        if scope == "market_intraday":
            flow_bias = _direction(main)
        elif scope == "market" and flow_bias == 0:
            flow_bias = _direction(main)
        breadth = "--"
        if up_count is not None or flat_count is not None or down_count is not None:
            breadth = f"{up_count or 0}/{flat_count or 0}/{down_count or 0}"
        label = _flow_intent_label(main, big, small, net_pct)
        lines.append(
            f"| {scope} | {item} | {trade_date or '--'} | {_fmt_yuan_yi(main)} | "
            f"{_fmt_yuan_yi(big)} | {_fmt_yuan_yi(small)} | {breadth} | {label} |"
        )
    return flow_bias


def _load_intent_index_rows(conn) -> list[tuple]:
    return conn.execute(
        """SELECT name,pct,amount,volume,fetched_at
           FROM index_snapshot
           WHERE now IS NOT NULL
           ORDER BY name"""
    ).fetchall()


def _render_intent_index(lines: list[str], rows: list[tuple]) -> tuple[int, str]:
    lines.extend(["", "### 指数量价确认"])
    if not rows:
        lines.append("- 无指数快照；无法验证资金流是否被价格确认。")
        return 0, "风格无法判断"
    values = [(str(r[0]), _float_or_none(r[1])) for r in rows]
    valid = [(name, pct) for name, pct in values if pct is not None]
    up = sum(1 for _name, pct in valid if pct > 0)
    down = sum(1 for _name, pct in valid if pct < 0)
    avg_pct = _avg([pct for _name, pct in valid])
    growth = _avg([
        pct for name, pct in valid
        if any(key in name for key in ("创业板", "科创", "深证"))
    ])
    value = _avg([
        pct for name, pct in valid
        if any(key in name for key in ("上证", "沪深300"))
    ])
    style_gap = None if growth is None or value is None else growth - value
    if style_gap is None:
        style_note = "风格差无法判断"
    elif style_gap > 0.005:
        style_note = f"成长强于权重（差值 {_fmt_ratio_pct(style_gap)}）"
    elif style_gap < -0.005:
        style_note = f"权重强于成长（差值 {_fmt_ratio_pct(style_gap)}）"
    else:
        style_note = f"成长/权重大致均衡（差值 {_fmt_ratio_pct(style_gap)}）"
    if up >= 4:
        price_bias = 1
    elif down >= 4:
        price_bias = -1
    else:
        price_bias = 0
    lines.append(
        f"- 主要指数：上涨 {up} 个、下跌 {down} 个，平均涨跌 {_fmt_ratio_pct(avg_pct)}；"
        f"{style_note}。"
    )
    return price_bias, style_note


def _load_intent_sector_breadth(conn) -> list[tuple]:
    return conn.execute(
        """SELECT category, COUNT(*),
                  SUM(CASE WHEN pct > 0 THEN 1 ELSE 0 END),
                  SUM(CASE WHEN pct < 0 THEN 1 ELSE 0 END),
                  AVG(pct), MAX(fetched_at)
           FROM sector_snapshot
           GROUP BY category
           ORDER BY category"""
    ).fetchall()


def _render_intent_sector_breadth(lines: list[str], rows: list[tuple]) -> int:
    lines.extend(["", "### 板块宽度"])
    if not rows:
        lines.append("- 无板块快照；无法确认主力是扩散进攻还是局部抱团。")
        return 0
    lines.append("| 类别 | 上涨/下跌/总数 | 上涨占比 | 平均涨跌 | 最近抓取 |")
    lines.append("|---|---:|---:|---:|---|")
    ratios: list[float] = []
    for category, total, up, down, avg_pct, fetched_at in rows:
        total_i = int(total or 0)
        up_i = int(up or 0)
        down_i = int(down or 0)
        ratio = up_i / total_i if total_i else None
        if ratio is not None:
            ratios.append(ratio)
        ratio_text = "--" if ratio is None else f"{ratio:.0%}"
        lines.append(
            f"| {category} | {up_i}/{down_i}/{total_i} | {ratio_text} | "
            f"{_fmt_pct(avg_pct)} | {fetched_at or '--'} |"
        )
    avg_ratio = _avg(ratios)
    if avg_ratio is None:
        return 0
    if avg_ratio >= 0.6:
        return 1
    if avg_ratio <= 0.4:
        return -1
    return 0


def _load_intent_theme_rows(conn) -> list[tuple]:
    rows = conn.execute(
        """SELECT category,name,pct,leader_name,leader_pct,fetched_at
           FROM sector_snapshot
           ORDER BY pct DESC"""
    ).fetchall()
    return [row for row in rows if _is_current_theme_sector(str(row[1]))][:8]


def _render_intent_theme_rows(lines: list[str], rows: list[tuple]) -> int:
    lines.extend(["", "### 当前持仓主题强弱"])
    if not rows:
        lines.append("- 未命中当前持仓主题板块；持仓主题承接需要降级观察。")
        return 0
    lines.append("| 板块 | 涨跌幅 | 领涨股 | 领涨股涨跌 |")
    lines.append("|---|---:|---|---:|")
    pcts: list[float] = []
    for _category, name, pct, leader_name, leader_pct, _fetched_at in rows:
        pct_f = _float_or_none(pct)
        if pct_f is not None:
            pcts.append(pct_f)
        lines.append(f"| {name} | {_fmt_pct(pct)} | {leader_name or '--'} | {_fmt_pct(leader_pct)} |")
    avg_pct = _avg(pcts)
    if avg_pct is None:
        return 0
    if avg_pct >= 0.5:
        return 1
    if avg_pct <= -0.5:
        return -1
    return 0


def _main_force_conclusion(
    *,
    flow_bias: int,
    price_bias: int,
    sector_bias: int,
    theme_bias: int,
    style_note: str,
) -> str:
    if flow_bias > 0 and price_bias > 0 and sector_bias > 0:
        return f"偏进攻性增仓：资金、指数与板块宽度共振；{style_note}。"
    if flow_bias < 0 and price_bias < 0:
        return f"偏减仓避险：主力资金与指数方向同弱；{style_note}。"
    if theme_bias > 0 and sector_bias <= 0:
        return f"偏结构性抱团：全市场宽度一般，但当前持仓主题更强；{style_note}。"
    if flow_bias > 0 and price_bias <= 0:
        return f"偏低吸/护盘但价格确认不足：资金端偏正，指数尚未同步扩散；{style_note}。"
    if flow_bias < 0 and theme_bias > 0:
        return f"偏调仓换线：总量资金偏弱，但持仓主题仍有承接；{style_note}。"
    if sector_bias < 0 and price_bias >= 0:
        return f"偏权重托底或局部防守：指数表现好于板块宽度；{style_note}。"
    return f"偏震荡观望：资金、指数、板块之间未形成一致信号；{style_note}。"


def _flow_intent_label(
    main: float | None,
    big: float | None,
    small: float | None,
    net_pct: float | None,
) -> str:
    main_dir = _direction(main)
    big_dir = _direction(big)
    small_dir = _direction(small)
    pct_text = f"净占比 {_fmt_pct(net_pct)}" if net_pct is not None else "净占比 --"
    if main_dir > 0 and big_dir > 0 and small_dir <= 0:
        return f"大单承接、小单流出，偏吸筹/主动进攻；{pct_text}"
    if main_dir > 0 and big_dir > 0:
        return f"主力与大单同向流入，偏主动承接；{pct_text}"
    if main_dir < 0 and big_dir < 0 and small_dir > 0:
        return f"大单撤离、小单承接，偏派发/风险释放；{pct_text}"
    if main_dir < 0 and big_dir < 0:
        return f"主力与大单同向流出，偏降低风险；{pct_text}"
    if main_dir > 0:
        return f"主力净流入但大单确认不足，偏试探承接；{pct_text}"
    if main_dir < 0:
        return f"主力净流出但结构不一致，偏谨慎；{pct_text}"
    return f"资金方向不明显，偏观望；{pct_text}"


def _is_current_theme_sector(name: str) -> bool:
    keywords = (
        "AI", "人工智能", "算力", "半导体", "芯片", "存储", "CPO", "光模块",
        "光通信", "通信", "5G", "PCB", "覆铜板", "HDI", "国产替代",
    )
    return any(key in name for key in keywords)


def _sum_optional(*values: float | None) -> float | None:
    nums = [float(v) for v in values if v is not None]
    if not nums:
        return None
    return sum(nums)


def _direction(value: float | None, *, eps: float = 1e-9) -> int:
    if value is None:
        return 0
    if value > eps:
        return 1
    if value < -eps:
        return -1
    return 0


def _avg(values: list[float | None]) -> float | None:
    nums = [float(v) for v in values if v is not None]
    if not nums:
        return None
    return sum(nums) / len(nums)


def _float_or_none(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_yuan_yi(v) -> str:
    if v is None:
        return "--"
    try:
        return f"{float(v) / 100_000_000:+.2f}亿"
    except Exception:
        return str(v)


def _volume_ratio(current_volume, frame) -> str:
    if current_volume is None or frame is None or frame.empty or "volume" not in frame.columns:
        return "--"
    try:
        vols = frame["volume"].dropna().astype(float)
        vols = vols[vols > 0]
        if len(vols) < 5:
            return "--"
        avg5 = float(vols.tail(5).mean())
        if not avg5:
            return "--"
        cur = float(current_volume)
        ratio = cur / avg5
        ratio_100 = cur * 100 / avg5
        if ratio < 0.05 and 0.05 <= ratio_100 <= 3.5:
            ratio = ratio_100
        return f"{ratio:.2f}x"
    except Exception:
        return "--"


def _index_trend_structure(current_price, frame) -> tuple[str, str]:
    if current_price is None or frame is None or frame.empty or "close" not in frame.columns:
        return "--", "--"
    try:
        closes = frame["close"].dropna().astype(float)
        highs = frame["high"].dropna().astype(float) if "high" in frame.columns else closes
        lows = frame["low"].dropna().astype(float) if "low" in frame.columns else closes
        if len(closes) < 20:
            return "--", "--"
        price = float(current_price)
        ma5 = float(closes.tail(5).mean())
        ma20 = float(closes.tail(20).mean())
        high60 = float(highs.tail(min(60, len(highs))).max())
        low20 = float(lows.tail(20).min())
        high20 = float(highs.tail(20).max())
        if price > ma5 > ma20:
            trend = "多头排列"
        elif price < ma5 < ma20:
            trend = "空头排列"
        else:
            trend = "均线纠缠"
        if high20 > low20:
            pos = (price - low20) / (high20 - low20)
            if pos >= 0.7:
                zone = "20日高位"
            elif pos <= 0.3:
                zone = "20日低位"
            else:
                zone = "20日中位"
            trend = f"{trend}/{zone}"
        dd = None if not high60 else price / high60 - 1
        return trend, _fmt_ret(dd)
    except Exception:
        return "--", "--"


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
    parts = [
        f"新闻时间: {panel.refreshed_at}",
        "- 相关性规则：优先呈现命中持仓/关注主题关键词的新闻；低相关新闻只作为宏观背景，不作为板块或基金结论依据。",
    ]
    for c in CATEGORIES:
        all_items = panel.items_by_category.get(c, [])
        relevant = [it for it in all_items if _effective_news_hits(it)]
        items = sorted(
            relevant,
            key=lambda it: (it.relevance_score, it.sentiment != 0, it.published_at),
            reverse=True,
        )[:per_cat]
        if not items:
            if c in {"policy", "us_market"}:
                parts.append(f"\n### {CATEGORY_LABELS[c]}")
                parts.append("- 暂无命中持仓/关注主题关键词的高相关新闻。")
            continue
        parts.append(f"\n### {CATEGORY_LABELS[c]}")
        for it in items:
            tag = "利好" if it.sentiment > 0 else ("利空" if it.sentiment < 0 else "中性")
            ts = getattr(it, "published_at", "") or ""
            src_name = getattr(it, "source", "") or ""
            rel = ""
            hits_for_display = _effective_news_hits(it)
            if hits_for_display:
                themes = "、".join(it.themes[:3])
                hits = "、".join(hits_for_display[:5])
                rel = f"；相关度 {it.relevance_score:.2f}（{themes}；{hits}）"
            parts.append(f"- [{tag}] [{src_name} {ts}] {it.title}{rel}")
    return "\n".join(parts)


def _build_company_watch_digest(cfg, *, force_refresh: bool = False) -> str:
    try:
        from .company_watch_service import CompanyWatchService, render_company_watch_markdown

        panel = CompanyWatchService(cfg).get_panel(force_refresh=force_refresh, refresh_news=False)
        return render_company_watch_markdown(panel)
    except Exception as e:  # noqa: BLE001
        return f"(市场动态生成失败: {e})"


def _effective_news_hits(item) -> list[str]:
    try:
        from .news_relevance import _keyword_hit
    except Exception:  # noqa: BLE001
        return list(getattr(item, "kw_hits", []) or [])
    title = getattr(item, "title", "") or ""
    content = getattr(item, "content", "") or ""
    source = getattr(item, "source", "") or ""
    category = getattr(item, "category", "") or ""
    text = title if source == "新闻联播" or category == "policy" else f"{title} {content[:800]}"
    return [kw for kw in (getattr(item, "kw_hits", []) or []) if _keyword_hit(text, kw)]


def _build_holdings(cfg) -> str:
    try:
        from ..portfolio.holdings import load_holdings
        from ..analytics import max_drawdown, sharpe_ratio, annualized_return, annualized_vol
        from .nav_service import NavService

        h = load_holdings()
        svc = NavService(cfg)
        realtime = {}
        realtime_warning = ""
        try:
            from .fund_realtime_service import FundRealtimeService

            realtime = FundRealtimeService(cfg).get_quotes([p.code for p in h.positions])
        except Exception as e:  # noqa: BLE001
            realtime_warning = f"- 基金当日公开估值获取失败：{e}；持仓净值与绩效指标继续使用本地净值计算。"
        lines = [f"持仓组合: {h.name}  as_of: {h.as_of}  无风险利率: {cfg.risk_free_rate*100:.1f}%"]
        if realtime_warning:
            lines.append(realtime_warning)
        lines.extend([
            "| 基金 | 权重 | 净值日 | 当日公开估值涨跌 | 估值时间 | 近7日 | 近1月 | 近3月 | 近6月 | 年化收益 | 年化波动 | 风险收益比(Sharpe) | 最大回撤 |",
            "|---|---:|:---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ])
        for p in h.positions:
            q = realtime.get(p.code)
            rt_pct = f"{q.estimate_pct:+.2f}%" if q and q.estimate_pct is not None else "--"
            rt_time = q.estimate_time if q and q.estimate_time else "--"
            try:
                series, _ = svc.get_nav(p.code, lookback_days=252)
                f = series.frame
            except Exception as e:
                lines.append(f"| {p.code} {p.name} | {p.weight:.2%} | 获取失败 | {rt_pct} | {rt_time} | {e} | -- | -- | -- | -- | -- | -- | -- |")
                continue
            if f.empty:
                lines.append(f"| {p.code} {p.name} | {p.weight:.2%} | 无净值 | {rt_pct} | {rt_time} | 无数据 | -- | -- | -- | -- | -- | -- | -- |")
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
            if rets is None or rets.empty:
                lines.append(f"| {p.code} {p.name} | {p.weight:.2%} | {f.index.max().date().isoformat()} | "
                             f"{rt_pct} | {rt_time} | "
                             f"{_fmt_ret(_window_return(7))} | {_fmt_ret(_window_return(30))} | "
                             f"{_fmt_ret(_window_return(90))} | {_fmt_ret(_window_return(180))} | "
                             f"-- | -- | -- | -- |")
                continue

            ann_ret = annualized_return(rets)
            ann_vol = annualized_vol(rets)
            sharpe = sharpe_ratio(rets, risk_free=cfg.risk_free_rate)
            mdd = max_drawdown(rets)
            last_date = f.index.max().date().isoformat()
            lines.append(
                f"| {p.code} {p.name} | {p.weight:.2%} | {last_date} | "
                f"{rt_pct} | {rt_time} | "
                f"{_fmt_ret(_window_return(7))} | {_fmt_ret(_window_return(30))} | "
                f"{_fmt_ret(_window_return(90))} | {_fmt_ret(_window_return(180))} | "
                f"{_fmt_ret(ann_ret)} | {_fmt_ret(ann_vol)} | {sharpe:.2f} | "
                f"{_fmt_ret(mdd)} |"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"(持仓加载失败: {e})"


def _build_horizon_context(cfg=None, now=None) -> str:
    from calendar import monthrange
    from datetime import date, datetime, timedelta, timezone

    if now is None and isinstance(cfg, (date, datetime)):
        now = cfg
        cfg = None
    if now is None:
        now = datetime.now(timezone(timedelta(hours=8)))
    if isinstance(now, datetime):
        today = now.date()
    elif isinstance(now, date):
        today = now
    else:
        today = date.today()
    seven_day_end = today + timedelta(days=7)
    month_end = today.replace(day=monthrange(today.year, today.month)[1])
    remaining_days = max((month_end - today).days, 0)
    next_trade = _next_trading_day_weekday(today)
    seven_trade_days = _weekday_days_between(today, seven_day_end)
    month_trade_days = _weekday_days_between(today, month_end)
    source_line = "- 交易日历来源：工作日回退（未传入 AppConfig）。"
    if cfg is not None:
        try:
            from .trading_calendar import TradingCalendarService

            svc = TradingCalendarService(cfg)
            snapshot = svc.get_calendar()
            next_trade = svc.next_trading_day(today, snapshot=snapshot)
            seven_trade_days = svc.trading_days_between(today, seven_day_end, snapshot=snapshot)
            month_trade_days = svc.trading_days_between(today, month_end, snapshot=snapshot)
            source_line = (
                f"- 交易日历来源：{snapshot.source}"
                f"；最近刷新：{snapshot.fetched_at or '--'}"
                f"；覆盖至：{max(snapshot.dates).isoformat() if snapshot.dates else '--'}"
            )
            if snapshot.error:
                source_line += f"；刷新异常：{snapshot.error}"
        except Exception as e:  # noqa: BLE001
            source_line = f"- 交易日历来源：工作日回退；交易所日历读取失败：{e}"
    return "\n".join([
        "## 趋势分析时间窗（程序生成）",
        "",
        f"- 当前日期：{today.isoformat()} Asia/Shanghai",
        source_line,
        f"- 下一个交易日：{next_trade.isoformat()}",
        f"- 未来 7 天：{today.isoformat()} 至 {seven_day_end.isoformat()}（含 {seven_trade_days} 个 A 股交易日）",
        f"- 本月剩余窗口：{today.isoformat()} 至 {month_end.isoformat()}（剩余 {remaining_days} 个自然日，含 {month_trade_days} 个 A 股交易日）",
        "- 输出要求：A 股大盘、重点板块、持有基金都必须分别覆盖上述三个时间窗；每个判断写方向倾向、置信度、核心证据、失效条件。",
    ])


def _next_trading_day_weekday(today):
    from datetime import timedelta

    d = today + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _weekday_days_between(start, end) -> int:
    from datetime import timedelta

    if end < start:
        return 0
    cur = start
    count = 0
    while cur <= end:
        if cur.weekday() < 5:
            count += 1
        cur += timedelta(days=1)
    return count


def _render_template(tmpl: str, **vars: str) -> str:
    out = tmpl
    for k, v in vars.items():
        out = out.replace("{" + k + "}", v)
    return out


def _replace_data_availability_section(text: str, section: str) -> str:
    section = section.rstrip()
    text = _strip_existing_auto_check(text)
    if not text:
        return section
    pattern = re.compile(
        r"(?ms)^#{0,6}\s*零[、.．]\s*数据可信度(?:与缺失原因)?\s*.*?(?=^#{0,6}\s*一[、.．]\s*)"
    )
    match = pattern.search(text)
    if match:
        return section + "\n\n" + text[match.end():].lstrip()
    return section + "\n\n" + text.lstrip()


def _strip_existing_auto_check(text: str) -> str:
    if not text:
        return text
    pattern = re.compile(
        r"(?ms)^#{0,6}\s*自动校验\s*$.*?(?=^#{1,6}\s+|^以上为基于公开数据|\Z)"
    )
    return pattern.sub("", text).rstrip()


def _replace_main_force_intent_section(text: str, framework: str) -> str:
    framework = framework.rstrip()
    if not framework or framework == "(本任务未注入)":
        return text
    section = _main_force_report_section(framework)
    patterns = (
        r"(?ms)^(?P<heading>#{0,6}\s*二[、.．]\s*主力意图分析\s*)\n.*?(?=^#{0,6}\s*三[、.．]\s*)",
        r"(?ms)^(?P<heading>#{1,6}\s*三[、.．]\s*主力意图分析\s*)\n.*?(?=^#{1,6}\s*四[、.．]\s*)",
        r"(?ms)^(?P<heading>#{1,6}\s*主力意图分析\s*)\n.*?(?=^#{1,6}\s*)",
    )
    for raw_pattern in patterns:
        pattern = re.compile(raw_pattern)
        match = pattern.search(text)
        if match:
            heading = match.group("heading").strip()
            replacement = section.replace("# 二、主力意图分析", heading, 1)
            return text[:match.start()] + replacement + "\n\n" + text[match.end():].lstrip()

    insert = re.search(r"(?m)^#{0,6}\s*三[、.．]\s*板块轮动", text)
    if insert:
        return text[:insert.start()] + section + "\n\n" + text[insert.start():]
    return text.rstrip() + "\n\n" + section


def _main_force_report_section(framework: str) -> str:
    return "\n".join([
        "# 二、主力意图分析",
        "",
        "以下内容由程序根据公开资金流、指数量价、板块宽度和持仓主题强弱生成。",
        "主力意图只能理解为“盘面倾向”，不是确定知道主力计划。",
        "",
        framework.rstrip(),
        "",
        "解读口径：若资金流交易日滞后，只能作为背景；若指数、板块宽度与资金流互相矛盾，本节结论自动降为低置信度。",
    ])


def _postprocess_analysis_text(
    text: str,
    *,
    data_availability: str,
    market_intent: str,
) -> str:
    text = _replace_data_availability_section(text, data_availability)
    text = _replace_main_force_intent_section(text, market_intent)
    text = _correct_availability_misclaims(text, data_availability)
    return text


# Map a domain mentioned in body text → keys we expect in the program-generated
# availability section. Used by _correct_availability_misclaims to detect "the
# model declared X missing but the program says X is available" mismatches.
_AVAILABILITY_DOMAIN_KEYS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("同类基金样本", ("同类基金样本/同类排名", "同类基金排行")),
    ("同类排名", ("同类基金样本/同类排名", "同类基金排行")),
    ("估值分位", ("估值分位/PE/PB分位", "指数估值分位")),
    ("PE分位", ("估值分位/PE/PB分位",)),
    ("PB分位", ("估值分位/PE/PB分位",)),
    ("北向资金", ("大盘资金流（盘中/主力/北向）", "大盘资金流")),
    ("北向", ("大盘资金流（盘中/主力/北向）", "大盘资金流")),
    ("融资融券", ("融资融券",)),
    ("两融", ("融资融券",)),
    ("主力资金流", ("大盘资金流（盘中/主力/北向）", "大盘资金流")),
    ("板块行情", ("板块行情",)),
    ("基金净值", ("持仓基金净值",)),
    ("基金当日公开估值", ("基金当日公开估值",)),
    ("指数估值", ("估值分位/PE/PB分位",)),
)


def _parse_available_domains(data_availability: str) -> set[str]:
    """Read program-generated table to learn which domains are usable.

    Lines of interest look like:  "| 估值分位/PE/PB分位 | ✅ 可用 | ..."
    We keep the leading column verbatim so callers can compare to
    ``_AVAILABILITY_DOMAIN_KEYS``.
    """
    ok: set[str] = set()
    for raw in data_availability.splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        domain, status = cells[0], cells[1]
        if "✅" in status or "部分可用" in status:
            ok.add(domain)
    return ok


def _correct_availability_misclaims(text: str, data_availability: str) -> str:
    """Rewrite body sentences that claim a usable domain is missing.

    The model occasionally writes lines like ``未接入逐笔资金流数据`` even when
    the cache contains valid records. We do not fabricate any number — we only
    soften the wording into "see the data-availability table" so the reader is
    not misled. Pure 逐笔/盘口 references stay untouched because they really
    are not ingested (see docs/data_sources.md row 14-16).
    """
    if not text:
        return text
    available = _parse_available_domains(data_availability)
    if not available:
        return text

    misclaim_patterns = (
        re.compile(r"未(?:接入|提供|采集|拉取)(?:数据|缓存|样本)?"),
        re.compile(r"(?:暂)?无(?:数据|缓存|样本|可用)"),
    )

    out_lines: list[str] = []
    for line in text.splitlines():
        # Never touch guardrail audit lines — they describe earlier model
        # mistakes and changing them invalidates the audit trail.
        if re.search(r"\[P\d\]", line):
            out_lines.append(line)
            continue

        if not any(p.search(line) for p in misclaim_patterns):
            out_lines.append(line)
            continue

        # Skip lines that are explicitly about microstructure (those really
        # are not ingested). 逐笔/盘口/Level-2 belong to row 14-16 of the doc.
        if re.search(r"逐笔|盘口|Level[-\s]?2|L2|tick", line, re.IGNORECASE):
            out_lines.append(line)
            continue

        matched_domain = None
        for hint, candidates in _AVAILABILITY_DOMAIN_KEYS:
            if hint not in line:
                continue
            if any(c in available for c in candidates):
                matched_domain = hint
                break
        if matched_domain is None:
            out_lines.append(line)
            continue

        # If the offending line is itself a fake availability table row
        # (e.g. ``| 同类排名 | 未接入 | ❌ 缺失 |``), drop it entirely so the
        # reader is not left with a half-corrected row alongside ``❌``.
        if line.lstrip().startswith("|") and ("❌" in line or "缺失" in line):
            out_lines.append(
                f"<!-- 自动修正:模型自编了“{matched_domain}=缺失”行，与可信度表冲突，已删除 -->"
            )
            continue

        replacement = re.sub(
            r"未(?:接入|提供|采集|拉取)(?:数据|缓存|样本)?",
            f"已接入(详见“零、数据可信度”表中“{matched_domain}”行)",
            line,
        )
        replacement = re.sub(
            r"(?:暂)?无(?:数据|缓存|样本|可用)",
            f"已接入(详见“零、数据可信度”表中“{matched_domain}”行)",
            replacement,
        )
        out_lines.append(replacement)

    return "\n".join(out_lines)


def _analyze(cfg, template: str, snapshot_kind: str, *, force_refresh: bool = False) -> dict[str, Any]:
    """snapshot_kind: 'market' | 'sector'"""
    if not template:
        raise RuntimeError(f"prompt 模板未配置 ({snapshot_kind})，请检查 configs/prompts.yaml")
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone(timedelta(hours=8)))
    as_of = now.strftime("%Y-%m-%d %H:%M")
    framework = cfg.prompts.output_framework or ""
    expert = _build_expert_context(cfg, force_refresh=force_refresh)
    news_digest = _build_news_digest(cfg, force_refresh=force_refresh)
    company_watch = _build_company_watch_digest(cfg, force_refresh=True)

    vars_ = {
        "as_of": as_of,
        "buyer_profile": expert["buyer_profile"],
        "data_quality": _build_data_quality(cfg),
        "data_availability": "(稍后由程序生成)",
        "horizon_context": _build_horizon_context(cfg, now),
        "fact_pack": expert["fact_pack"],
        "risk_rules": expert["risk_rules"],
        "evidence_policy": expert["evidence_policy"],
        "holdings": _build_holdings(cfg),
        "news_digest": news_digest,
        "company_watch": company_watch,
        "output_framework": framework,
        "market_snapshot": "(本任务未注入)",
        "market_flow": "(本任务未注入)",
        "market_intent": "(本任务未注入)",
        "index_trends": "(本任务未注入)",
        "sector_snapshot": "(本任务未注入)",
        "sector_trends": "(本任务未注入)",
        "holdings_xray": "(本任务未注入)",
    }
    if snapshot_kind == "market":
        vars_["market_snapshot"] = _build_market_snapshot(cfg, force_refresh=force_refresh)
        vars_["market_flow"] = _build_market_flow_snapshot(cfg, force_refresh=force_refresh)
        vars_["market_intent"] = _build_main_force_intent(cfg)
    elif snapshot_kind == "sector":
        vars_["market_flow"] = _build_market_flow_snapshot(cfg, force_refresh=force_refresh)
        vars_["sector_snapshot"] = _build_sector_snapshot(cfg, force_refresh=force_refresh)
        vars_["sector_trends"] = _build_sector_trends(cfg, force_refresh=force_refresh)
        vars_["market_intent"] = _build_main_force_intent(cfg)
    elif snapshot_kind == "holdings":
        vars_["market_snapshot"] = _build_market_snapshot(cfg, force_refresh=force_refresh)
        vars_["market_flow"] = _build_market_flow_snapshot(cfg, force_refresh=force_refresh)
        vars_["sector_snapshot"] = _build_sector_snapshot(cfg, force_refresh=force_refresh)
        vars_["holdings_xray"] = _build_holdings_xray(cfg)
        vars_["sector_trends"] = _build_sector_trends(cfg, force_refresh=force_refresh)
        vars_["market_intent"] = _build_main_force_intent(cfg)

    vars_["data_availability"] = _build_data_availability_section(cfg)
    prompt = _render_template(template, **vars_)
    log.info("ai analyze kind=%s prompt_len=%d", snapshot_kind, len(prompt))
    result = _chat_with_guardrails(
        cfg,
        prompt,
        rules=expert["rules"],
        evidence=expert["evidence"],
        postprocess=lambda text: _postprocess_analysis_text(
            text,
            data_availability=vars_["data_availability"],
            market_intent=vars_["market_intent"],
        ),
    )
    result["prompt"] = prompt
    return result


def analyze_market(cfg, *, force_refresh: bool = False) -> dict[str, Any]:
    return _analyze(cfg, cfg.prompts.market_analysis, "market", force_refresh=force_refresh)


def analyze_sectors(cfg, *, force_refresh: bool = False) -> dict[str, Any]:
    return _analyze(cfg, cfg.prompts.sector_analysis, "sector", force_refresh=force_refresh)


def analyze_holdings(cfg, *, force_refresh: bool = False) -> dict[str, Any]:
    return _analyze(cfg, cfg.prompts.holdings_analysis, "holdings", force_refresh=force_refresh)


def analyze_full(cfg, *, force_refresh: bool = False) -> dict[str, Any]:
    """一次调用整合大盘+板块+持仓三份分析."""
    template = cfg.prompts.full_analysis
    if not template:
        raise RuntimeError("full_analysis prompt 未配置，请检查 configs/prompts.yaml")
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone(timedelta(hours=8)))
    as_of = now.strftime("%Y-%m-%d %H:%M")
    expert = _build_expert_context(cfg, force_refresh=force_refresh)

    market_snapshot = _build_market_snapshot(cfg, force_refresh=force_refresh)
    market_flow = _build_market_flow_snapshot(cfg, force_refresh=force_refresh)
    sector_snapshot = _build_sector_snapshot(cfg, force_refresh=force_refresh)
    sector_trends = _build_sector_trends(cfg, force_refresh=force_refresh)
    market_intent = _build_main_force_intent(cfg)
    data_availability = _build_data_availability_section(cfg)
    news_digest = _build_news_digest(cfg, force_refresh=force_refresh)
    company_watch = _build_company_watch_digest(cfg, force_refresh=True)

    prompt = _render_template(
        template,
        as_of=as_of,
        buyer_profile=expert["buyer_profile"],
        data_quality=_build_data_quality(cfg),
        data_availability=data_availability,
        horizon_context=_build_horizon_context(cfg, now),
        fact_pack=expert["fact_pack"],
        risk_rules=expert["risk_rules"],
        evidence_policy=expert["evidence_policy"],
        market_snapshot=market_snapshot,
        market_flow=market_flow,
        market_intent=market_intent,
        sector_snapshot=sector_snapshot,
        sector_trends=sector_trends,
        holdings=_build_holdings(cfg),
        holdings_xray=_build_holdings_xray(cfg),
        news_digest=news_digest,
        company_watch=company_watch,
        output_framework="",
    )
    log.info("ai analyze_full prompt_len=%d", len(prompt))
    result = _chat_with_guardrails(
        cfg,
        prompt,
        rules=expert["rules"],
        evidence=expert["evidence"],
        postprocess=lambda text: _postprocess_analysis_text(
            text,
            data_availability=data_availability,
            market_intent=market_intent,
        ),
    )
    result["prompt"] = prompt
    return result


# =============================================================================
# 数据构建辅助：持仓穿透 + 板块趋势 K 线
# =============================================================================


def _build_holdings_xray(cfg) -> str:
    """构建持仓穿透摘要：每只基金的前十大重仓股 + 近期涨跌."""
    try:
        from ..portfolio.holdings import load_holdings
        from .xray_service import XrayService

        h = load_holdings()
        svc = XrayService(cfg)
        lines: list[str] = []
        for p in h.positions:
            try:
                holdings = svc.get_top_holdings(p.code)
            except Exception as e:
                lines.append(
                    f"| {p.code} {p.name} | 获取失败: {e} | -- | -- | -- |"
                )
                continue
            if not holdings:
                lines.append(
                    f"| {p.code} {p.name} | 无重仓数据 | -- | -- | -- |"
                )
                continue
            season = holdings[0].season
            items = []
            for hld in holdings[:10]:
                pct_str = f"{hld.pct_nav:.1f}%" if hld.pct_nav is not None else "--"
                items.append(f"{hld.stock_name}({hld.stock_code}) {pct_str}")
            lines.append(
                f"| {p.code} {p.name} | {season}季报 | {len(holdings)}只重仓 | "
                f"{'、'.join(items)} |"
            )
        if not lines:
            return "(持仓穿透数据为空)"
        header = (
            "| 基金 | 报告期 | 重仓数 | 前十大重仓(代码 占净值%) |\n"
            "|---|---|---|---|"
        )
        return header + "\n" + "\n".join(lines)
    except Exception as e:
        return f"(持仓穿透获取失败: {e})"


def _build_sector_trends(cfg, *, force_refresh: bool = False) -> str:
    """构建重点板块近期K线趋势摘要."""
    try:
        from .sector_service import SectorService
        from .sector_daily_service import SectorDailyService

        panel = SectorService(cfg).get_panel(force_refresh=force_refresh)
        daily_svc = SectorDailyService(cfg)
        lines: list[str] = []

        watch_sectors = panel.watch_rows[:5]
        for r in watch_sectors:
            try:
                series = daily_svc.get_series(
                    r.category, r.label, r.name, lookback_days=20,
                    force_refresh=force_refresh,
                )
            except Exception:
                continue
            if series.frame.empty:
                continue
            f = series.frame.sort_values("trade_date")
            closes = f["close"]
            if len(closes) < 3:
                continue
            pct_5d = float(closes.iloc[-1] / closes.iloc[-min(5, len(closes))] - 1) * 100
            pct_10d = float(closes.iloc[-1] / closes.iloc[-min(10, len(closes))] - 1) * 100
            pct_20d = float(closes.iloc[-1] / closes.iloc[0] - 1) * 100
            lines.append(
                f"| {r.name} | {_fmt_pct(r.pct)} | "
                f"{_fmt_pct(pct_5d)} | {_fmt_pct(pct_10d)} | {_fmt_pct(pct_20d)} |"
            )
        if not lines:
            return "(板块K线趋势数据为空)"
        header = (
            "| 关注板块 | 今日涨跌 | 近5日 | 近10日 | 近20日 |\n"
            "|---|---:|---:|---:|---:|"
        )
        return header + "\n" + "\n".join(lines)
    except Exception as e:
        return f"(板块趋势获取失败: {e})"
