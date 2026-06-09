"""Background cache refresh loop used by the TUI."""
from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ..config import AppConfig

log = logging.getLogger(__name__)
TZ = ZoneInfo("Asia/Shanghai")


@dataclass(slots=True)
class RefreshStatus:
    running: bool = False
    total_runs: int = 0
    total_tasks: int = 0
    completed_tasks: int = 0
    current_task: str | None = None
    last_started_at: str | None = None
    last_finished_at: str | None = None
    next_run_at: str | None = None
    last_ok: bool | None = None
    last_message: str = "尚未刷新"
    errors: list[str] = field(default_factory=list)


class BackgroundRefreshWorker:
    """Periodically refreshes all TUI data caches.

    The TUI should read from sqlite and never block on network calls while
    rendering. This worker owns the remote fetches.
    """

    def __init__(
        self,
        cfg: AppConfig,
        *,
        interval_seconds: int = 60,
        nav_days: int = 10,
        run_immediately: bool = True,
    ) -> None:
        self.cfg = cfg
        self.interval_seconds = interval_seconds
        self.nav_days = nav_days
        self.run_immediately = run_immediately
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._run_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._status = RefreshStatus(next_run_at=_fmt(_now()))
        self._task_last_run: dict[str, datetime] = {}
        self._errors: deque[str] = deque(maxlen=20)
        self._tasks: tuple[tuple[str, str, int], ...] = (
            ("持仓净值", "_refresh_holdings_nav", interval_seconds),
            ("基金公开估值", "_refresh_fund_realtime", interval_seconds),
            ("大盘行情", "_refresh_market", interval_seconds),
            ("指数分钟线", "_refresh_index_intraday", interval_seconds),
            ("资金流", "_refresh_market_flow", interval_seconds),
            ("板块行情", "_refresh_sectors", interval_seconds),
            ("同类排行", "_refresh_peer_rank", 12 * 3600),
            ("指数估值", "_refresh_valuation", 12 * 3600),
            ("融资融券", "_refresh_margin", 3600),
            ("新闻", "_refresh_news", interval_seconds),
            ("市场动态", "_refresh_company_watch", interval_seconds),
        )

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._loop,
            name="fund-helper-refresh",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def snapshot(self) -> dict:
        with self._lock:
            return asdict(self._status)

    def run_once(self) -> None:
        with self._run_lock:
            self._run_once_locked(force=True)

    def run_due(self) -> None:
        with self._run_lock:
            self._run_once_locked(force=False)

    def _run_once_locked(self, *, force: bool) -> None:
        started = _now()
        tasks = self._due_tasks(force=force, now=started)
        with self._lock:
            self._status.running = True
            self._status.total_tasks = len(tasks)
            self._status.completed_tasks = 0
            self._status.current_task = "准备刷新" if tasks else None
            self._status.last_started_at = _fmt(started)
            self._status.last_message = "刷新中"
        messages: list[str] = []
        ok = True
        for idx, (name, method_name, _interval) in enumerate(tasks):
            with self._lock:
                self._status.current_task = name
                self._status.completed_tasks = idx
                self._status.last_message = f"正在刷新{name}"
            try:
                fn = getattr(self, method_name)
                msg = fn()
                messages.append(f"{name}: {msg}")
                self._task_last_run[name] = _now()
            except Exception as e:  # noqa: BLE001
                ok = False
                err = f"{_fmt(_now())} {name}: 失败({e})"
                messages.append(f"{name}: 失败({e})")
                self._errors.append(err)
                log.warning("background refresh failed task=%s: %s", name, e)
            finally:
                with self._lock:
                    self._status.completed_tasks = idx + 1
        finished = _now()
        next_run = finished + timedelta(seconds=self.interval_seconds)
        with self._lock:
            self._status.running = False
            self._status.completed_tasks = len(tasks)
            self._status.current_task = None
            self._status.total_runs += 1
            self._status.last_finished_at = _fmt(finished)
            self._status.next_run_at = _fmt(next_run)
            self._status.last_ok = ok
            self._status.last_message = "；".join(messages) if messages else "暂无到期刷新任务"
            self._status.errors = list(self._errors)

    def _loop(self) -> None:
        if self.run_immediately:
            self.run_once()
        while not self._stop.wait(self.interval_seconds):
            self.run_due()

    def _task_due(self, name: str, interval_seconds: int, now: datetime) -> bool:
        last = self._task_last_run.get(name)
        return last is None or (now - last).total_seconds() >= interval_seconds

    def _due_tasks(self, *, force: bool, now: datetime) -> list[tuple[str, str, int]]:
        if force:
            return list(self._tasks)
        return [
            task
            for task in self._tasks
            if self._task_due(task[0], task[2], now)
        ]

    def _refresh_holdings_nav(self) -> str:
        from ..portfolio.holdings import load_holdings
        from .nav_service import NavService

        holdings = load_holdings()
        svc = NavService(self.cfg)
        end = date.today()
        start = end - timedelta(days=self.nav_days)
        fetched = 0
        for pos in holdings.positions:
            _series, outcome = svc.get_nav_range(pos.code, start, end, force_refresh=True)
            fetched += outcome.rows_fetched
        return f"{len(holdings.positions)}只，新增/更新{fetched}行"

    def _refresh_fund_realtime(self) -> str:
        from ..portfolio.holdings import load_holdings
        from .fund_realtime_service import FundRealtimeService

        holdings = load_holdings()
        quotes = FundRealtimeService(self.cfg).get_quotes(
            [p.code for p in holdings.positions],
            force_refresh=True,
        )
        return f"{len(quotes)}/{len(holdings.positions)}只"

    def _refresh_market(self) -> str:
        from .market_service import MarketService

        panel = MarketService(self.cfg).get_a_share_panel(force_refresh=True)
        return f"{len(panel.a_share)}个指数，源{panel.source_used}"

    def _refresh_market_flow(self) -> str:
        from .market_flow_service import MarketFlowService

        panel = MarketFlowService(self.cfg).get_panel(force_refresh=True)
        msg = f"{len(panel.rows)}条，源{panel.source_used}"
        if panel.errors:
            msg += f"，失败{len(panel.errors)}项"
        return msg

    def _refresh_index_intraday(self) -> str:
        from .index_intraday_service import IndexIntradayService

        panel = IndexIntradayService(self.cfg).get_panel(force_refresh=True)
        rows = sum(len(s.frame) for s in panel.series)
        msg = f"{rows}条，源{panel.source_used}"
        if panel.errors:
            msg += f"，失败{len(panel.errors)}项"
        return msg

    def _refresh_sectors(self) -> str:
        from .sector_service import SectorService

        panel = SectorService(self.cfg).get_panel(force_refresh=True)
        count = sum(len(v) for v in panel.rows_by_category.values())
        return f"{count}条，源{panel.source_used}"

    def _refresh_news(self) -> str:
        from ..storage import connect
        from .news_service import get_news_panel

        conn = connect(self.cfg.data_dir / "fund.db")
        panel = get_news_panel(conn, force_refresh=True)
        count = sum(len(v) for v in panel.items_by_category.values())
        return f"{count}条"

    def _refresh_company_watch(self) -> str:
        from .company_watch_service import CompanyWatchService

        panel = CompanyWatchService(self.cfg).get_panel(force_refresh=True, refresh_news=False)
        return f"{len(panel.targets)}家公司，命中{len(panel.matches)}条"

    def _refresh_peer_rank(self) -> str:
        from .peer_rank_service import PeerRankService

        panel = PeerRankService(self.cfg).get_panel(force_refresh=True)
        msg = f"{len(panel.rows)}只，源{panel.source_used}"
        if panel.errors:
            msg += f"，失败{len(panel.errors)}项"
        return msg

    def _refresh_valuation(self) -> str:
        from .valuation_service import ValuationService

        panel = ValuationService(self.cfg).get_panel(force_refresh=True)
        msg = f"{len(panel.rows)}项，源{panel.source_used}"
        if panel.errors:
            msg += f"，失败{len(panel.errors)}项"
        return msg

    def _refresh_margin(self) -> str:
        from .margin_service import MarginService

        panel = MarginService(self.cfg).get_panel(force_refresh=True)
        msg = f"{len(panel.rows)}项，源{panel.source_used}"
        if panel.errors:
            msg += f"，失败{len(panel.errors)}项"
        return msg


def _now() -> datetime:
    return datetime.now(TZ)


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")
