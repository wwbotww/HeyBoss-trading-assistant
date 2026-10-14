"""固定六来源每日采集; 与交易进程及网页写入完全分离。"""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from threading import Event
from types import FrameType
from uuid import uuid4

from nautilus_trader.model.data import BarType
from sqlalchemy.exc import SQLAlchemyError

from trading_assistant.data.catalog import CatalogRepository, catalog_lock
from trading_assistant.data.config import load_data_config
from trading_assistant.data.eodhd_http import EodhdTemporaryHttpError
from trading_assistant.market_radar.config import MarketRadarDailyConfig, load_market_radar_config
from trading_assistant.market_radar.eodhd_components import EodhdIndexComponentsSource
from trading_assistant.market_radar.fred import FredTemporaryHttpError
from trading_assistant.market_radar.runtime import MarketRadarRuntime, market_radar_writer_lock
from trading_assistant.market_radar.schedule import (
    DAILY_JOBS,
    PRICE_JOBS,
    DailyJob,
    expected_price_date,
    scheduled_at,
)
from trading_assistant.market_radar.service import (
    MarketRadarSyncError,
    sync_current_market_breadth,
    sync_market_earnings,
    sync_market_economic_events,
    sync_market_fundamentals,
    sync_market_macro,
    sync_market_radar_prices,
)
from trading_assistant.market_radar.state_street import StateStreetSpyHoldingsSource
from trading_assistant.market_radar.storage import (
    DailyAttemptStatus,
    DailyPublication,
    DailySyncAttempt,
    DailySyncWorker,
    MarketRadarRepository,
)

LOGGER = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class MarketRadarJobResult:
    """本轮决定; 日志不包含凭据、供应商正文或账户信息。"""

    job: DailyJob
    state: str
    reason: str | None = None


@dataclass(frozen=True)
class MarketRadarDailyResult:
    """一次到期检查的各来源结果。"""

    jobs: tuple[MarketRadarJobResult, ...]

    @property
    def has_errors(self) -> bool:
        return any(
            item.state in {"failed", "waiting_source", "waiting_dependency", "blocked"}
            for item in self.jobs
        )


def _satisfies(publication: DailyPublication | None, target: date) -> bool:
    return (
        publication is not None and publication.as_of_date >= target and publication.coverage_ready
    )


class _DailyRunner:
    """唯一采集进程的具体装配, 保存本进程已核对的 Catalog 状态。"""

    def __init__(
        self,
        runtime: MarketRadarRuntime,
        settings: MarketRadarDailyConfig,
        environ: Mapping[str, str],
        clock: Callable[[], datetime],
        stop: Event,
    ) -> None:
        self.runtime = runtime
        self.settings = settings
        self.environ = environ
        self.clock = clock
        self.stop = stop
        self.started_at = clock()
        self.active_job: DailyJob | None = None
        self.catalog_checked: set[tuple[DailyJob, date]] = set()
        self.repository = MarketRadarRepository(runtime.database_url)
        self.repository.create_schema()

    def heartbeat(self, *, stopped: bool = False) -> None:
        now = self.clock()
        if now < self.started_at:
            raise ValueError("Radar clock moved backwards")
        self.repository.update_daily_worker(
            DailySyncWorker(
                self.started_at,
                now,
                now if stopped else None,
                self.active_job,
                scheduled_at(now, self.settings.run_at_utc) + timedelta(days=1),
                self.settings.run_at_utc,
                self.settings.poll_interval_seconds,
                self.settings.retry_interval_seconds,
                self.settings.max_attempts_per_day,
            )
        )

    def recover(self) -> None:
        """只能在持有唯一写入锁后收尾本调度器关联的孤立运行。"""
        now = self.clock()
        for attempt in self.repository.daily_attempts(running_only=True):
            run = self.repository.get_sync_run(attempt.run_id)
            publication = self.repository.daily_publication(attempt.job)
            complete = (
                run is not None
                and run.status == "COMPLETE"
                and publication is not None
                and publication.run_id == attempt.run_id
            )
            status: DailyAttemptStatus = "interrupted"
            code = "process_interrupted"
            if complete:
                status = (
                    "complete" if _satisfies(publication, attempt.target_date) else "waiting_source"
                )
                code = "target_not_ready"
            elif run is not None and run.status == "RUNNING":
                self.repository.fail_sync_run(
                    run.run_id,
                    completed_at_utc=now,
                    error_summary=code,
                    instruments_processed=run.instruments_processed,
                    bars_fetched=run.bars_fetched,
                    bars_written=run.bars_written,
                )
            self.repository.finish_daily_attempt(
                attempt.run_id,
                status=status,
                completed_at_utc=now,
                error_code=None if status == "complete" else code,
                next_retry_at_utc=self._retry_at(now, attempt.attempt_number)
                if status != "complete"
                else None,
            )

    def _retry_at(self, now: datetime, attempt: int) -> datetime | None:
        return (
            None
            if attempt >= self.settings.max_attempts_per_day
            else now + timedelta(seconds=self.settings.retry_interval_seconds)
        )

    def _catalog_ready(self, job: DailyJob, target: date) -> bool:
        if job not in PRICE_JOBS:
            return True
        if (self.runtime.catalog_path / ".catalog-writing").exists():
            return False
        if (job, target) in self.catalog_checked:
            return True
        if not (self.runtime.catalog_path / ".catalog.lock").is_file():
            return False
        config = load_market_radar_config(
            self.runtime.project_root / "config" / "market-radar.yaml"
        )
        if job == "prices":
            ids = config.price_instrument_ids
        elif job == "macro":
            ids = config.macro_price_instrument_ids
        else:
            membership = self.repository.latest_current_membership()
            if membership is None:
                return False
            ids = (config.benchmark, *(member.instrument_id for member in membership.members))
        catalog = CatalogRepository(self.runtime.catalog_path)
        with catalog_lock(self.runtime.catalog_path, exclusive=False):
            for instrument_id in ids:
                timestamp = catalog.latest_bar_timestamp(
                    BarType.from_str(f"{instrument_id}-1-DAY-LAST-INTERNAL")
                )
                # 快照已新而 Catalog 旧/空时仍须初始化, 不复用另一目录的成功。
                if (
                    timestamp is None
                    or datetime.fromtimestamp(timestamp / 1e9, UTC).date() < target
                ):
                    return False
        self.catalog_checked.add((job, target))
        return True

    def tick(self, now: datetime) -> MarketRadarDailyResult:
        results: list[MarketRadarJobResult] = []
        for job in DAILY_JOBS:
            if self.stop.is_set():
                break
            self.active_job = job
            results.append(self._run_job(job, now))
            now = self.clock()
        self.active_job = None
        return MarketRadarDailyResult(tuple(results))

    def _run_job(self, job: DailyJob, now: datetime) -> MarketRadarJobResult:
        due = scheduled_at(now, self.settings.run_at_utc)
        target = (
            expected_price_date(now, self.settings.run_at_utc)
            if job in PRICE_JOBS
            else now.astimezone(UTC).date()
        )
        publication = self.repository.daily_publication(job)
        if publication is not None and publication.published_at_utc > now:
            raise ValueError("Radar publication is in the future")
        # 当前来源每计划日采集一次; 午夜变日不能提前启动下一轮。
        due_target = target if job in PRICE_JOBS else due.date()
        if _satisfies(publication, due_target) and self._catalog_ready(job, target):
            return MarketRadarJobResult(job, "complete")
        attempts = self.repository.daily_attempts(job=job, scheduled_at_utc=due)
        if attempts:
            latest = attempts[0]
            if (
                latest.attempt_number >= self.settings.max_attempts_per_day
                or latest.next_retry_at_utc is None
            ):
                return MarketRadarJobResult(
                    job, "blocked", latest.error_code or "attempt_budget_exhausted"
                )
            if latest.next_retry_at_utc is not None and latest.next_retry_at_utc > now:
                return MarketRadarJobResult(job, "waiting_source", latest.error_code)
        if job == "breadth":
            price = self.repository.daily_publication("prices")
            if (
                price is None
                or price.as_of_date < target
                or not self._catalog_ready("prices", target)
            ):
                return MarketRadarJobResult(job, "waiting_dependency", "benchmark_not_ready")
        number = max((item.attempt_number for item in attempts), default=0) + 1
        run_id = str(uuid4())
        self.repository.start_daily_attempt(
            DailySyncAttempt(run_id, job, due, target, number, now, None, "running", None, None)
        )
        status: DailyAttemptStatus = "failed"
        code: str | None = None
        retryable = False
        try:
            if job in PRICE_JOBS and (self.runtime.catalog_path / ".catalog-writing").exists():
                raise MarketRadarSyncError("catalog_recovery_required", retryable=False)
            asyncio.run(self._sync_job(job, target, run_id))
            published = self.repository.daily_publication(job)
            if published is None or published.run_id != run_id:
                raise MarketRadarSyncError("publication_missing", retryable=False)
            if not _satisfies(published, target):
                raise MarketRadarSyncError("target_not_ready", retryable=True, waiting=True)
            status = "complete"
            self.catalog_checked.add((job, target))
        except MarketRadarSyncError as exc:
            status = "waiting_source" if exc.waiting else "failed"
            code, retryable = exc.code, exc.retryable
        except (EodhdTemporaryHttpError, FredTemporaryHttpError) as exc:
            code, retryable = (
                "rate_limited" if exc.status_code == 429 else "temporary_source",
                True,
            )
        except (ConnectionError, TimeoutError):
            code, retryable = "temporary_source", True
        except SQLAlchemyError:
            # 审计不可写时不能继续运行; 心跳也会过期。
            raise
        except Exception as exc:
            code = type(exc).__name__
        completed = self.clock()
        self.repository.finish_daily_attempt(
            run_id,
            status=status,
            completed_at_utc=completed,
            error_code=code,
            next_retry_at_utc=self._retry_at(completed, number) if retryable else None,
        )
        LOGGER.info("Radar sync: job=%s status=%s reason=%s", job, status, code or "none")
        return MarketRadarJobResult(job, status, code)

    async def _sync_job(self, job: DailyJob, target: date, run_id: str) -> None:
        """固定分派到六个既有服务, 不另写采集器或指标实现。"""
        root = self.runtime.project_root / "config"
        data_path = root / "data.yaml"
        market_path = root / "market-radar.yaml"
        token = self.environ.get("EODHD_API_TOKEN", "").strip()
        if not token:
            raise MarketRadarSyncError("missing_eodhd_token", retryable=False)
        if job == "macro" and not self.environ.get("FRED_API_KEY", "").strip():
            raise MarketRadarSyncError("missing_fred_key", retryable=False)
        if job == "prices":
            await sync_market_radar_prices(
                catalog_path=self.runtime.catalog_path,
                database_url=self.runtime.database_url,
                report_directory=self.runtime.report_directory,
                market_config_path=market_path,
                trading_instruments_config_path=root / "instruments.yaml",
                data_config_path=data_path,
                mode="daily",
                start_date=target - timedelta(days=400),
                end_date=target,
                eodhd_api_token=token,
                clock=self.clock,
                run_id_factory=lambda: run_id,
                expected_price_date=target,
            )
        elif job == "macro":
            await sync_market_macro(
                catalog_path=self.runtime.catalog_path,
                database_url=self.runtime.database_url,
                report_directory=self.runtime.report_directory,
                market_config_path=market_path,
                data_config_path=data_path,
                mode="daily",
                start_date=target - timedelta(days=365 * 4),
                end_date=target,
                eodhd_api_token=token,
                fred_api_key=self.environ.get("FRED_API_KEY"),
                clock=self.clock,
                run_id_factory=lambda: run_id,
                expected_price_date=target,
            )
        elif job == "economic_events":
            await sync_market_economic_events(
                database_url=self.runtime.database_url,
                data_config_path=data_path,
                eodhd_api_token=token,
                clock=self.clock,
                run_id_factory=lambda: run_id,
            )
        elif job == "fundamentals":
            await sync_market_fundamentals(
                database_url=self.runtime.database_url,
                market_config_path=market_path,
                data_config_path=data_path,
                trading_instruments_config_path=root / "instruments.yaml",
                eodhd_api_token=token,
                clock=self.clock,
                run_id_factory=lambda: run_id,
            )
        else:
            data = load_data_config(data_path).historical_data
            membership = StateStreetSpyHoldingsSource(
                request_timeout_seconds=data.request_timeout_seconds
            )
            if job == "earnings":
                config = load_market_radar_config(market_path)
                classification = EodhdIndexComponentsSource(
                    api_token=token or "",
                    index_symbol=config.index_membership_symbol,
                    request_timeout_seconds=data.request_timeout_seconds,
                    max_attempts=data.max_attempts,
                    retry_backoff_seconds=data.retry_backoff_seconds,
                )
                await sync_market_earnings(
                    membership_source=membership,
                    classification_source=classification,
                    database_url=self.runtime.database_url,
                    market_config_path=market_path,
                    data_config_path=data_path,
                    eodhd_api_token=token,
                    clock=self.clock,
                    run_id_factory=lambda: run_id,
                )
            else:
                await sync_current_market_breadth(
                    membership_source=membership,
                    catalog_path=self.runtime.catalog_path,
                    database_url=self.runtime.database_url,
                    report_directory=self.runtime.report_directory,
                    market_config_path=market_path,
                    trading_instruments_config_path=root / "instruments.yaml",
                    data_config_path=data_path,
                    mode="daily",
                    start_date=target - timedelta(days=400),
                    end_date=target,
                    eodhd_api_token=token,
                    clock=self.clock,
                    run_id_factory=lambda: run_id,
                    expected_price_date=target,
                )


def _execute_tick(
    runner: _DailyRunner, executor: ThreadPoolExecutor, now: datetime
) -> MarketRadarDailyResult:
    """用一个物理采集线程隔离磁盘工作, 主线程持续维护心跳。"""
    future = executor.submit(runner.tick, now)
    while True:
        runner.heartbeat()
        try:
            return future.result(timeout=runner.settings.poll_interval_seconds)
        except TimeoutError:
            if future.done():
                return future.result()


def run_market_radar_tick(
    *,
    runtime: MarketRadarRuntime,
    settings: MarketRadarDailyConfig,
    environ: Mapping[str, str],
    now: datetime,
    clock: Callable[[], datetime] = _utc_now,
) -> MarketRadarDailyResult:
    """一次到期检查也持独占锁并维护心跳; 不强制重跑成功来源。"""
    with market_radar_writer_lock(runtime):
        runner = _DailyRunner(runtime, settings, environ, clock, Event())
        try:
            runner.recover()
            with ThreadPoolExecutor(max_workers=1) as executor:
                return _execute_tick(runner, executor, now)
        finally:
            runner.active_job = None
            try:
                runner.heartbeat(stopped=True)
            finally:
                runner.repository.close()


def serve_market_radar(
    *,
    runtime: MarketRadarRuntime,
    settings: MarketRadarDailyConfig,
    environ: Mapping[str, str],
    stop: Event | None = None,
    clock: Callable[[], datetime] = _utc_now,
) -> None:
    """常驻服务按持久化结果运行; 退出前让当前写入安全收尾。"""
    stop_event = stop if stop is not None else Event()
    previous_handlers: dict[
        signal.Signals, Callable[[int, FrameType | None], object] | int | None
    ] = {}
    if stop is None:

        def request_stop(_signum: int, _frame: object) -> None:
            stop_event.set()

        for signum in (signal.SIGTERM, signal.SIGINT):
            previous_handlers[signum] = signal.signal(signum, request_stop)
    try:
        with market_radar_writer_lock(runtime):
            runner = _DailyRunner(runtime, settings, environ, clock, stop_event)
            try:
                runner.recover()
                with ThreadPoolExecutor(max_workers=1) as executor:
                    while not stop_event.is_set():
                        _execute_tick(runner, executor, clock())
                        runner.heartbeat()
                        stop_event.wait(settings.poll_interval_seconds)
            finally:
                runner.active_job = None
                try:
                    runner.heartbeat(stopped=True)
                finally:
                    runner.repository.close()
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
