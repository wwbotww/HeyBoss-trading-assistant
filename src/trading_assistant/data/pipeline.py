"""历史日线同步、质量检查与 Catalog 写入编排。"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from nautilus_trader.model.data import Bar, BarType

from trading_assistant.data.catalog import CatalogRepository
from trading_assistant.data.config import DataPipelineConfig, InstrumentSpec
from trading_assistant.data.corporate_actions import CorporateActionRepository, CorporateActions
from trading_assistant.data.quality import (
    DataQualityReport,
    QualityIssue,
    detect_historical_revisions,
    validate_daily_bars,
)
from trading_assistant.data.source import HistoricalBarSource, HistoricalDataAuthenticationError

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineSummary:
    """一次同步或离线检查的汇总。"""

    instruments_processed: int
    bars_fetched: int
    bars_written: int
    corporate_actions_written: int
    instrument_reports: tuple[DataQualityReport, ...]
    issues: tuple[QualityIssue, ...]
    report_path: Path

    @property
    def has_errors(self) -> bool:
        """是否包含错误级问题。"""
        return any(issue.severity == "error" for issue in self.issues) or any(
            report.has_errors for report in self.instrument_reports
        )


@dataclass(frozen=True)
class _FetchContext:
    """单个标的同步前计算出的本地状态。"""

    spec: InstrumentSpec
    bar_types: tuple[BarType, ...]
    fetch_start: datetime
    has_start_coverage: bool


@dataclass(frozen=True)
class _FetchResult:
    """单个标的的远端获取结果。"""

    context: _FetchContext
    bars: tuple[Bar, ...]
    actions: CorporateActions | None
    error: Exception | None


def _utc_now_ns() -> int:
    """返回当前 UTC Unix 纳秒。"""
    return int(datetime.now(tz=UTC).timestamp() * 1_000_000_000)


def _deduplicate_bars(bars: Sequence[Bar]) -> list[Bar]:
    """按 BarType 与 ts_init 去重并排序。"""
    unique = {(str(bar.bar_type), bar.ts_init): bar for bar in bars}
    return [unique[key] for key in sorted(unique, key=lambda value: (value[1], value[0]))]


class HistoricalDataPipeline:
    """统一供 CLI 与后续实盘收盘同步复用的数据管道。"""

    def __init__(
        self,
        *,
        config: DataPipelineConfig,
        catalog: CatalogRepository,
        report_directory: Path,
        source: HistoricalBarSource | None = None,
        corporate_actions: CorporateActionRepository | None = None,
        clock_ns: Callable[[], int] = _utc_now_ns,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._config = config
        self._catalog = catalog
        self._report_directory = report_directory
        self._source = source
        self._corporate_actions = corporate_actions
        self._clock_ns = clock_ns
        self._sleep = sleep

    def _bar_type(self, instrument_id: str) -> BarType:
        """返回增量边界检查使用的 execution BarType。"""
        suffix = self._config.historical_data.execution_bar_type_suffix
        return BarType.from_str(f"{instrument_id}-{suffix}")

    def _bar_types(self, instrument_id: str) -> tuple[BarType, ...]:
        """返回一个标的需要完整同步的全部规范 BarType。"""
        config = self._config.historical_data
        values = (
            BarType.from_str(f"{instrument_id}-{config.execution_bar_type_suffix}"),
            BarType.from_str(f"{instrument_id}-{config.signal_bar_type_suffix}"),
        )
        return tuple(dict.fromkeys(values))

    def _write_report(
        self,
        *,
        mode: str,
        reports: Sequence[DataQualityReport],
        issues: Sequence[QualityIssue],
        bars_fetched: int,
        bars_written: int,
        corporate_actions_written: int,
    ) -> Path:
        """写入当前运行的质量报告; 该文件不是数据版本 manifest。"""
        self._report_directory.mkdir(parents=True, exist_ok=True)
        now = datetime.now(tz=UTC)
        path = self._report_directory / f"data-quality-{now.strftime('%Y%m%dT%H%M%SZ')}.json"
        payload = {
            "mode": mode,
            "generated_at_utc": now.isoformat(),
            "bars_fetched": bars_fetched,
            "bars_written": bars_written,
            "corporate_actions_written": corporate_actions_written,
            "issues": [issue.to_dict() for issue in issues],
            "instruments": [report.to_dict() for report in reports],
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return path

    def _windows(self, start: datetime, end: datetime) -> tuple[tuple[datetime, datetime], ...]:
        """按配置把请求范围切成串行窗口。"""
        if start >= end:
            return ()
        window_days = self._config.historical_data.request_window_days
        if window_days is None:
            return ((start, end),)
        windows: list[tuple[datetime, datetime]] = []
        cursor = start
        chunk = timedelta(days=window_days)
        while cursor < end:
            window_end = min(cursor + chunk, end)
            windows.append((cursor, window_end))
            cursor = window_end
        return tuple(windows)

    async def _request_with_retry(
        self,
        spec: InstrumentSpec,
        start: datetime,
        end: datetime,
    ) -> list[Bar]:
        """串行请求单个窗口并按配置退避重试。"""
        if self._source is None:
            raise RuntimeError("Historical data source is required for sync")
        config = self._config.historical_data
        for attempt in range(config.max_attempts):
            try:
                return await self._source.request_daily_bars(spec, start, end)
            except (ConnectionError, TimeoutError, RuntimeError) as exc:
                if attempt + 1 >= config.max_attempts:
                    raise
                delay = config.retry_backoff_seconds[attempt]
                LOGGER.warning(
                    "Historical request failed for %s; retrying in %.1fs: %s",
                    spec.instrument_id,
                    delay,
                    exc,
                )
                await self._sleep(delay)
        raise AssertionError("unreachable")

    async def _request_actions_with_retry(
        self,
        spec: InstrumentSpec,
        start: datetime,
        end: datetime,
    ) -> CorporateActions:
        """请求公司行动并按相同瞬时故障策略退避重试。"""
        if self._source is None:
            raise RuntimeError("Historical data source is required for sync")
        config = self._config.historical_data
        for attempt in range(config.max_attempts):
            try:
                return await self._source.request_corporate_actions(spec, start, end)
            except (ConnectionError, TimeoutError, RuntimeError) as exc:
                if attempt + 1 >= config.max_attempts:
                    raise
                delay = config.retry_backoff_seconds[attempt]
                LOGGER.warning(
                    "Corporate action request failed for %s; retrying in %.1fs: %s",
                    spec.instrument_id,
                    delay,
                    exc,
                )
                await self._sleep(delay)
        raise AssertionError("unreachable")

    async def _fetch(
        self,
        context: _FetchContext,
        end: datetime,
        semaphore: asyncio.Semaphore,
    ) -> _FetchResult:
        """并发获取单个标的; Catalog 校验和写入仍由主协程串行执行。"""
        async with semaphore:
            fetched: list[Bar] = []
            try:
                actions = await self._request_actions_with_retry(
                    context.spec,
                    context.fetch_start,
                    end,
                )
                windows = self._windows(context.fetch_start, end)
                for index, (window_start, window_end) in enumerate(windows):
                    fetched.extend(
                        await self._request_with_retry(
                            context.spec,
                            window_start,
                            window_end,
                        ),
                    )
                    if index + 1 < len(windows):
                        await self._sleep(
                            self._config.historical_data.request_interval_seconds,
                        )
            except HistoricalDataAuthenticationError:
                raise
            except (ConnectionError, TimeoutError, RuntimeError, ValueError) as exc:
                return _FetchResult(context, (), None, exc)
            return _FetchResult(context, tuple(fetched), actions, None)

    async def sync(
        self,
        instruments: Sequence[InstrumentSpec],
        *,
        start: datetime,
        end: datetime,
    ) -> PipelineSummary:
        """同步标的定义与日线; 校验后按配置追加或替换 Catalog。"""
        if self._source is None:
            raise RuntimeError("Historical data source is required for sync")

        reports: list[DataQualityReport] = []
        issues: list[QualityIssue] = []
        bars_fetched = 0
        bars_written = 0
        corporate_actions_written = 0
        processed = 0
        now_ns = self._clock_ns()

        await self._source.connect()
        try:
            loaded = await self._source.request_instruments(instruments)
            self._catalog.write_instruments(loaded)
            loaded_ids = {instrument.id.value for instrument in loaded}

            contexts: list[_FetchContext] = []
            for spec in instruments:
                if spec.instrument_id not in loaded_ids:
                    issues.append(
                        QualityIssue(
                            code="instrument_not_found",
                            severity="error",
                            instrument_id=spec.instrument_id,
                            timestamp_ns=None,
                            message="The data source did not resolve the configured instrument.",
                        ),
                    )
                    continue

                processed += 1
                bar_types = self._bar_types(spec.instrument_id)
                execution_bar_type = self._bar_type(spec.instrument_id)
                earliest_ns = self._catalog.earliest_bar_timestamp(execution_bar_type)
                latest_ns = self._catalog.latest_bar_timestamp(execution_bar_type)
                fetch_start = start
                start_coverage_limit_ns = int(
                    (start.replace(tzinfo=UTC) + timedelta(days=7)).timestamp() * 1_000_000_000,
                )
                has_start_coverage = (
                    earliest_ns is not None and earliest_ns <= start_coverage_limit_ns
                )
                if (
                    self._config.historical_data.refresh_mode == "append"
                    and latest_ns is not None
                    and has_start_coverage
                ):
                    latest = datetime.fromtimestamp(latest_ns / 1_000_000_000, tz=UTC)
                    overlap_start = latest - timedelta(
                        days=self._config.historical_data.overlap_days,
                    )
                    fetch_start = max(start, overlap_start.replace(tzinfo=None))

                contexts.append(
                    _FetchContext(
                        spec=spec,
                        bar_types=bar_types,
                        fetch_start=fetch_start,
                        has_start_coverage=has_start_coverage,
                    )
                )

            semaphore = asyncio.Semaphore(self._config.historical_data.max_concurrent_requests)
            results = await asyncio.gather(
                *(self._fetch(context, end, semaphore) for context in contexts)
            )
            for result in results:
                context = result.context
                spec = context.spec
                if result.error is not None:
                    issues.append(
                        QualityIssue(
                            code="fetch_failed",
                            severity="error",
                            instrument_id=spec.instrument_id,
                            timestamp_ns=None,
                            message=f"Historical data request failed: {result.error}",
                        ),
                    )
                    continue

                if result.actions is None:
                    raise AssertionError("successful fetch must include corporate actions")
                actions = result.actions
                bar_types = context.bar_types
                fetched = list(result.bars)
                fetched = [
                    bar
                    for bar in _deduplicate_bars(fetched)
                    if bar.bar_type in bar_types and bar.ts_init <= now_ns
                ]
                bars_fetched += len(fetched)

                coverage_limit_ns = int(
                    (start.replace(tzinfo=UTC) + timedelta(days=7)).timestamp() * 1_000_000_000,
                )
                must_verify_start = (
                    self._config.historical_data.refresh_mode == "replace"
                    or not context.has_start_coverage
                )
                first_by_type = {
                    bar_type: next(
                        (bar for bar in fetched if bar.bar_type == bar_type),
                        None,
                    )
                    for bar_type in bar_types
                }
                if must_verify_start and any(
                    first is None or first.ts_event > coverage_limit_ns
                    for first in first_by_type.values()
                ):
                    issues.append(
                        QualityIssue(
                            code="start_coverage_missing",
                            severity="error",
                            instrument_id=spec.instrument_id,
                            timestamp_ns=min(
                                (
                                    first.ts_init
                                    for first in first_by_type.values()
                                    if first is not None
                                ),
                                default=None,
                            ),
                            message="Provider history does not cover the configured start date.",
                        ),
                    )
                    continue

                start_ns = int(context.fetch_start.replace(tzinfo=UTC).timestamp() * 1_000_000_000)
                instrument_reports: list[DataQualityReport] = []
                for bar_type in bar_types:
                    typed_bars = [bar for bar in fetched if bar.bar_type == bar_type]
                    existing = self._catalog.read_bars(bar_type, start_ns=start_ns)
                    issues.extend(
                        detect_historical_revisions(
                            spec.instrument_id,
                            existing,
                            typed_bars,
                        )
                    )
                    instrument_reports.append(
                        validate_daily_bars(
                            spec.instrument_id,
                            typed_bars,
                            bar_type,
                            self._config.quality,
                            as_of_ns=now_ns,
                        )
                    )
                reports.extend(instrument_reports)
                if any(report.has_errors for report in instrument_reports):
                    continue
                if self._config.historical_data.refresh_mode == "replace":
                    bars_written += self._catalog.replace_bars(fetched)
                else:
                    bars_written += self._catalog.append_new_bars(fetched)
                if self._corporate_actions is not None:
                    corporate_actions_written += int(self._corporate_actions.write(actions))
        finally:
            await self._source.close()

        report_path = self._write_report(
            mode="sync",
            reports=reports,
            issues=issues,
            bars_fetched=bars_fetched,
            bars_written=bars_written,
            corporate_actions_written=corporate_actions_written,
        )
        return PipelineSummary(
            instruments_processed=processed,
            bars_fetched=bars_fetched,
            bars_written=bars_written,
            corporate_actions_written=corporate_actions_written,
            instrument_reports=tuple(reports),
            issues=tuple(issues),
            report_path=report_path,
        )

    def validate_catalog(self, instruments: Sequence[InstrumentSpec]) -> PipelineSummary:
        """不连接外部数据源; 检查 Catalog 当前全部日线。"""
        reports: list[DataQualityReport] = []
        now_ns = self._clock_ns()
        for spec in instruments:
            for bar_type in self._bar_types(spec.instrument_id):
                bars = self._catalog.read_bars(bar_type)
                reports.append(
                    validate_daily_bars(
                        spec.instrument_id,
                        bars,
                        bar_type,
                        self._config.quality,
                        as_of_ns=now_ns,
                    ),
                )

        report_path = self._write_report(
            mode="validate-only",
            reports=reports,
            issues=(),
            bars_fetched=0,
            bars_written=0,
            corporate_actions_written=0,
        )
        return PipelineSummary(
            instruments_processed=len(instruments),
            bars_fetched=0,
            bars_written=0,
            corporate_actions_written=0,
            instrument_reports=tuple(reports),
            issues=(),
            report_path=report_path,
        )
