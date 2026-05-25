from __future__ import annotations

from pathlib import Path

import pandas as pd

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config import AppConfig, load_config
from ..portfolio.holdings import load_holdings
from ..services.nav_service import NavService

PKG_DIR = Path(__file__).parent
TPL_DIR = PKG_DIR / "templates"
STATIC_DIR = PKG_DIR / "static"


def create_app(cfg: AppConfig | None = None) -> FastAPI:
    cfg = cfg or load_config()
    app = FastAPI(title="fund-helper", docs_url=None, redoc_url=None)

    templates = Jinja2Templates(directory=str(TPL_DIR))
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.state.cfg = cfg

    # --- pages -----------------------------------------------------------
    @app.get("/", include_in_schema=False)
    def root_redirect() -> RedirectResponse:
        return RedirectResponse(url="/holdings", status_code=307)

    @app.get("/holdings", response_class=HTMLResponse)
    def holdings_page(request: Request, window: str = Query(default="6m")):
        from ._windows import WINDOWS, resolve_window
        from ..analytics import (
            max_drawdown, annualized_return,
        )
        from ..services.nav_service import NavService
        spec = resolve_window(window)
        h = load_holdings()
        svc = NavService(cfg)
        cards = []
        for p in h.positions:
            series, _ = svc.get_nav(p.code, lookback_days=max(spec.days, 7))
            frame = series.frame
            if not frame.empty:
                start_ts = frame.index[frame.index >= pd.Timestamp(spec.start)]
                if len(start_ts):
                    frame = frame.loc[start_ts[0]:]
            rets = frame["daily_return"].dropna() if "daily_return" in frame.columns else None
            if frame.empty or rets is None or rets.empty:
                cards.append({
                    "code": p.code, "name": p.name,
                    "weight": round(p.weight * 100, 2),
                    "missing": True,
                })
                continue
            nav_path = (1.0 + rets).cumprod()
            cum = float(nav_path.iloc[-1] - 1.0)
            mdd = float(max_drawdown(rets))
            unit = frame["unit_nav"].dropna()
            cards.append({
                "code": p.code, "name": p.name,
                "weight": round(p.weight * 100, 2),
                "missing": False,
                "n_obs": int(len(rets)),
                "start": rets.index.min().date().isoformat(),
                "end":   rets.index.max().date().isoformat(),
                "cum_ret": cum,
                "ann_ret": float(annualized_return(rets)),
                "max_dd":  mdd,
                "nav_min": float(unit.min()) if not unit.empty else None,
                "nav_max": float(unit.max()) if not unit.empty else None,
                "nav_now": float(unit.iloc[-1]) if not unit.empty else None,
                "chart": {
                    "dates":  [d.date().isoformat() for d in nav_path.index],
                    "values": [round(v, 6) for v in nav_path.values],
                },
            })
        return templates.TemplateResponse(
            request,
            "holdings.html",
            {
                "active": "holdings",
                "windows": WINDOWS,
                "window_key": spec.key,
                "window_label": spec.label,
                "cards": cards,
                "total_weight": round(sum(c["weight"] for c in cards), 2),
                "as_of": h.as_of,
            },
        )

    @app.get("/fund/{code}", response_class=HTMLResponse)
    def fund_detail(code: str, request: Request):
        from datetime import datetime

        from ..analytics import (
            annualized_return, annualized_vol, cumulative_return,
            max_drawdown, sharpe_ratio, sortino_ratio, calmar_ratio,
        )

        svc = NavService(cfg)
        series, outcome = svc.get_nav(code, lookback_days=180)
        ctx = {
            "active": "holdings",
            "code": code,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "fetch_status": outcome.status,
        }
        if series is None or series.frame.empty:
            ctx["missing"] = True
            return templates.TemplateResponse(request, "fund_detail.html", ctx)

        rets = series.returns()
        ctx["missing"] = False
        ctx["metrics"] = {
            "n_obs": int(len(rets)),
            "start": rets.index.min().date().isoformat() if not rets.empty else "-",
            "end":   rets.index.max().date().isoformat() if not rets.empty else "-",
            "cum_ret":  cumulative_return(rets),
            "ann_ret":  annualized_return(rets),
            "ann_vol":  annualized_vol(rets),
            "max_dd":   max_drawdown(rets),
            "sharpe":   sharpe_ratio(rets, cfg.risk_free_rate),
            "sortino":  sortino_ratio(rets, cfg.risk_free_rate),
            "calmar":   calmar_ratio(rets),
        }
        nav_path = (1.0 + rets).cumprod()
        ctx["nav_chart"] = {
            "dates": [d.date().isoformat() for d in nav_path.index],
            "values": [round(v, 6) for v in nav_path.values],
        }
        return templates.TemplateResponse(request, "fund_detail.html", ctx)


    @app.get("/holdings_xray", response_class=HTMLResponse)
    def holdings_xray_page(request: Request,
                           window: str = Query(default="6m"),
                           refresh: int = Query(default=0)):
        from ._windows import WINDOWS, resolve_window
        from ..services.xray_service import XrayService
        spec = resolve_window(window)
        h = load_holdings()
        svc = XrayService(cfg)
        funds_view = []
        last_refreshed = None
        for p in h.positions:
            tops = svc.get_top_holdings(p.code, force_refresh=bool(refresh))
            season = tops[0].season if tops else None
            holdings_view = []
            for t in tops:
                series = svc.get_stock_daily(t.stock_code,
                                             lookback_days=max(spec.days, 7),
                                             force_refresh=bool(refresh))
                f = series.frame
                f = f[f["trade_date"] >= spec.start.isoformat()] if not f.empty else f
                if f.empty:
                    holdings_view.append({
                        "rank": t.rank, "stock_code": t.stock_code,
                        "stock_name": t.stock_name, "pct_nav": t.pct_nav,
                        "missing": True, "cum_ret": None,
                    })
                    continue
                closes = f["close"].astype(float).tolist()
                dates  = f["trade_date"].tolist()
                close_now = closes[-1]
                close_first = closes[0]
                cum_ret = (close_now / close_first - 1.0) if close_first else 0.0
                holdings_view.append({
                    "rank": t.rank, "stock_code": t.stock_code,
                    "stock_name": t.stock_name, "pct_nav": t.pct_nav,
                    "missing": False,
                    "cum_ret": cum_ret,
                    "close_now": close_now,
                    "close_max": max(closes),
                    "close_min": min(closes),
                    "chart": {"dates": dates, "values": closes},
                })
            funds_view.append({
                "code": p.code, "name": p.name,
                "weight": round(p.weight * 100, 2),
                "season": season,
                "holdings": holdings_view,
            })
        # 顺手取最近一次股票抓取时间
        row = svc.conn.execute("SELECT MAX(fetched_at) FROM stock_daily").fetchone()
        if row and row[0]:
            last_refreshed = row[0]
        return templates.TemplateResponse(
            request, "holdings_xray.html",
            {
                "active": "xray",
                "funds": funds_view,
                "windows": WINDOWS,
                "window_key": spec.key,
                "window_label": spec.label,
                "last_refreshed": last_refreshed,
            },
        )

    @app.get("/market", response_class=HTMLResponse)
    def market_page(request: Request,
                    refresh: int = Query(default=0),
                    window: str = Query(default="6m")):
        from ._windows import WINDOWS, resolve_window
        from ..services.market_service import MarketService
        from ..services.index_daily_service import IndexDailyService
        spec = resolve_window(window)
        svc = MarketService(cfg)
        panel = svc.get_a_share_panel(force_refresh=bool(refresh))
        idx_svc = IndexDailyService(cfg) if spec.days > 0 else None
        cards = []
        for q in panel.a_share:
            series = (idx_svc.get_series(q.secid, lookback_days=max(spec.days + 5, 14),
                                          force_refresh=bool(refresh))
                       if idx_svc else None)
            chart = None
            range_ret = None
            close_max = None
            close_min = None
            f = series.frame if series else None
            if f is not None and not f.empty:
                f = f[f["trade_date"] >= spec.start.isoformat()]
            if f is not None and not f.empty:
                closes = f["close"].astype(float).tolist()
                dates  = f["trade_date"].tolist()
                chart = {"dates": dates, "values": closes}
                first = closes[0]
                last = closes[-1]
                if first:
                    range_ret = (last / first - 1.0)
                close_max = max(closes)
                close_min = min(closes)
            cards.append({
                "secid": q.secid, "name": q.name,
                "now": q.now, "delta": q.delta, "pct": q.pct,
                "pre_close": q.pre_close, "high": q.high, "low": q.low,
                "last_ts": q.last_ts, "trade_date": q.trade_date,
                "missing": q.now is None,
                "chart": chart,
                "range_ret": range_ret,
                "close_max": close_max,
                "close_min": close_min,
                "n_obs": (len(f) if (f is not None and not f.empty) else 0),
            })
        return templates.TemplateResponse(
            request, "market.html",
            {
                "active": "market",
                "cards": cards,
                "is_session": panel.is_session,
                "source_used": panel.source_used,
                "refreshed_at": panel.refreshed_at.strftime("%Y-%m-%d %H:%M:%S"),
                "windows": WINDOWS,
                "window_key": spec.key,
                "window_label": spec.label,
            },
        )

    @app.get("/sectors", response_class=HTMLResponse)
    def sectors_page(request: Request,
                     refresh: int = Query(default=0),
                     tab: str = Query(default="fav")):
        from ..services.sector_service import SectorService
        svc = SectorService(cfg)
        panel = svc.get_panel(force_refresh=bool(refresh))
        if tab not in ("fav", "watch", "industry", "concept"):
            tab = "fav"
        if tab == "fav":
            rows = [r.to_dict() for r in panel.favorite_rows]
        elif tab == "watch":
            rows = [r.to_dict() for r in panel.watch_rows]
        else:
            rows = [r.to_dict() for r in panel.rows_by_category.get(tab, [])]
        return templates.TemplateResponse(
            request, "sectors.html",
            {
                "active": "sectors",
                "tab": tab,
                "rows": rows,
                "counts": {
                    "fav":   len(panel.favorite_rows),
                    "watch": len(panel.watch_rows),
                    "industry": len(panel.rows_by_category.get("industry", [])),
                    "concept": len(panel.rows_by_category.get("concept", [])),
                },
                "favorite_rows": [r.to_dict() for r in panel.favorite_rows],
                "refreshed_at": panel.refreshed_at,
                "is_session": panel.is_session,
                "source_used": panel.source_used,
            },
        )

    @app.get("/sectors/{category}/{label}", response_class=HTMLResponse)
    def sector_detail_page(request: Request, category: str, label: str,
                           refresh: int = Query(default=0),
                           window: str = Query(default="6m")):
        from ._windows import WINDOWS, resolve_window
        from ..services.sector_service import SectorService
        from ..services.sector_daily_service import SectorDailyService
        from ..services.sector_favorite_service import SectorFavoriteService
        if category not in ("industry", "concept"):
            from fastapi import HTTPException
            raise HTTPException(404, "unknown category")
        spec = resolve_window(window)
        # 找 snapshot 行（拿 name + 现货指标）
        panel = SectorService(cfg).get_panel(force_refresh=False)
        target = None
        for r in panel.rows_by_category.get(category, []):
            if r.label == label:
                target = r
                break
        if target is None:
            from fastapi import HTTPException
            raise HTTPException(404, "sector not found")
        name = target.name
        # 历史
        series = SectorDailyService(cfg).get_series(
            category, label, name,
            lookback_days=max(spec.days + 5, 14),
            force_refresh=bool(refresh),
        )
        f = series.frame
        chart = None
        range_ret = None
        close_max = None
        close_min = None
        n_obs = 0
        if f is not None and not f.empty:
            f = f[f["trade_date"] >= spec.start.isoformat()]
        if f is not None and not f.empty:
            closes = f["close"].astype(float).tolist()
            dates  = f["trade_date"].tolist()
            chart = {"dates": dates, "values": closes}
            if closes[0]:
                range_ret = closes[-1] / closes[0] - 1.0
            close_max = max(closes)
            close_min = min(closes)
            n_obs = len(closes)
        is_fav = SectorFavoriteService(cfg).is_fav(category, label)
        return templates.TemplateResponse(
            request, "sector_detail.html",
            {
                "active": "sectors",
                "category": category,
                "category_label": "行业" if category == "industry" else "概念",
                "label": label,
                "name": name,
                "ths_name": series.ths_name,
                "row": target.to_dict(),
                "windows": WINDOWS,
                "window_key": spec.key,
                "window_label": spec.label,
                "chart": chart,
                "range_ret": range_ret,
                "close_max": close_max,
                "close_min": close_min,
                "n_obs": n_obs,
                "is_fav": is_fav,
                "is_session": panel.is_session,
                "source_used": panel.source_used,
                "refreshed_at": panel.refreshed_at,
            },
        )

    @app.post("/api/sector/favorite")
    def api_sector_favorite_add(payload: dict):
        from ..services.sector_favorite_service import SectorFavoriteService
        cat = payload.get("category")
        lbl = payload.get("label")
        name = payload.get("name") or ""
        if cat not in ("industry", "concept") or not lbl:
            return JSONResponse({"ok": False, "error": "bad params"}, status_code=400)
        SectorFavoriteService(cfg).add(cat, lbl, name)
        return JSONResponse({"ok": True})

    @app.delete("/api/sector/favorite")
    def api_sector_favorite_del(category: str, label: str):
        from ..services.sector_favorite_service import SectorFavoriteService
        if category not in ("industry", "concept") or not label:
            return JSONResponse({"ok": False, "error": "bad params"}, status_code=400)
        SectorFavoriteService(cfg).remove(category, label)
        return JSONResponse({"ok": True})

    @app.get("/news", response_class=HTMLResponse)
    def news_page(request: Request, refresh: int = Query(default=0), category: str = Query(default="sentiment")):
        from ..services.news_service import get_news_panel, CATEGORIES, CATEGORY_LABELS
        from ..storage import connect
        if category not in CATEGORIES:
            category = "sentiment"
        conn = connect(cfg.data_dir / "fund.db")
        panel = get_news_panel(conn, force_refresh=bool(refresh))
        items = [it.to_dict() for it in panel.items_by_category.get(category, [])]
        tabs = [(c, CATEGORY_LABELS[c], len(panel.items_by_category.get(c, []))) for c in CATEGORIES]
        return templates.TemplateResponse(
            request, "news.html",
            {
                "active": "news",
                "items": items,
                "tabs": tabs,
                "current": category,
                "current_label": CATEGORY_LABELS[category],
                "refreshed_at": panel.refreshed_at,
                "cached": panel.cached,
            },
        )

    @app.get("/ai", response_class=HTMLResponse)
    def ai_get(request: Request):
        return templates.TemplateResponse(
            request, "ai.html",
            {"active": "ai", "ai": cfg.ai, "prompt": "", "answer": None,
             "error": None, "elapsed": None},
        )

    @app.post("/ai", response_class=HTMLResponse)
    def ai_post(request: Request,
                prompt: str = Form(default=""),
                action: str = Form(default="chat")):
        from ..services.ai_service import chat, analyze_market, analyze_sectors
        import time as _t
        t0 = _t.time()
        answer = None
        error = None
        used_prompt = prompt
        try:
            if action == "analyze_market":
                result = analyze_market(cfg)
                used_prompt = result.get("prompt", "")
                answer = result["text"] or "(模型返回为空)"
            elif action == "analyze_sectors":
                result = analyze_sectors(cfg)
                used_prompt = result.get("prompt", "")
                answer = result["text"] or "(模型返回为空)"
            else:
                if not prompt.strip():
                    raise RuntimeError("请输入要分析的内容")
                result = chat(cfg, prompt)
                answer = result["text"] or "(模型返回为空)"
        except Exception as e:
            error = str(e)
        return templates.TemplateResponse(
            request, "ai.html",
            {"active": "ai", "ai": cfg.ai, "prompt": used_prompt,
             "answer": answer, "error": error, "elapsed": _t.time() - t0,
             "action": action},
        )

    # --- json apis -------------------------------------------------------
    @app.get("/api/holdings")
    def api_holdings():
        h = load_holdings()
        return JSONResponse({
            "name": h.name,
            "as_of": h.as_of.isoformat() if hasattr(h.as_of, "isoformat") else h.as_of,
            "positions": [
                {"code": p.code, "name": p.name, "weight": p.weight}
                for p in h.positions
            ],
        })

    @app.get("/api/healthz")
    def healthz():
        return {"ok": True}

    return app
