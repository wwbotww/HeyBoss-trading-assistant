"""IBKR 历史日线同步、质量检查与 Catalog 写入编排。"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId

from trading_assistant.data.catalog import CatalogRepository
from trading_assistant.data.config import DataPipelineConfig, InstrumentSpec
from trading_assistant.data.ibkr import HistoricalBarSource
from trading_assistant.data.quality import (
    DataQualityReport,
    QualityIssue,
    detect_historical_revisions,
    validate_daily_bars,
)

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineSummary:
    """一次同步或离线检查的汇总。"""

    instruments_processed: int
    bars_fetched: int
    bars_written: int
    instrument_reports: tuple[DataQualityReport, ...]
    issues: tuple[QualityIssue, ...]
    report_path: Path

    @property
    def has_errors(self) -> bool:
        """是否包含错误级问题。"""
        return any(issue.severity == "error" for issue in self.issues) or any(
            report.has_errors for report in self.instrument_reports
        )


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
        clock_ns: Callable[[], int] = _utc_now_ns,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._config = config
        self._catalog = catalog
        self._report_directory = report_directory
        self._source = source
        self._clock_ns = clock_ns
        self._sleep = sleep

    def _bar_type(self, instrument_id: str) -> BarType:
        """从唯一配置构建标准 NT BarType。"""
        suffix = self._config.historical_data.bar_type_suffix
        return BarType.from_str(f"{instrument_id}-{suffix}")

    def _write_report(
        self,
        *,
        mode: str,
        reports: Sequence[DataQualityReport],
        issues: Sequence[QualityIssue],
        bars_fetched: int,
        bars_written: int,
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
        windows: list[tuple[datetime, datetime]] = []
        cursor = start
        chunk = timedelta(days=self._config.historical_data.chunk_days)
        while cursor < end:
            window_end = min(cursor + chunk, end)
            windows.append((cursor, window_end))
            cursor = window_end
        return tuple(windows)

    async def _request_with_retry(
        self,
        instrument_id: InstrumentId,
        start: datetime,
        end: datetime,
    ) -> list[Bar]:
        """串行请求单个窗口并按配置退避重试。"""
        if self._source is None:
            raise RuntimeError("Historical data source is required for sync")
        config = self._config.historical_data
        for attempt in range(config.max_attempts):
            try:
                return await self._source.request_daily_bars(instrument_id, start, end)
            except (ConnectionError, TimeoutError, RuntimeError) as exc:
                if attempt + 1 >= config.max_attempts:
                    raise
                delay = config.retry_backoff_seconds[attempt]
                LOGGER.warning(
                    "Historical request failed for %s; retrying in %.1fs: %s",
                    instrument_id,
                    delay,
                    exc,
                )
                await self._sleep(delay)
        raise AssertionError("unreachable")

    async def sync(
        self,
        instruments: Sequence[InstrumentSpec],
        *,
        start: datetime,
        end: datetime,
    ) -> PipelineSummary:
        """同步标的定义与日线; 校验后幂等追加到 Catalog。"""
        if self._source is None:
            raise RuntimeError("Historical data source is required for sync")

        reports: list[DataQualityReport] = []
        issues: list[QualityIssue] = []
        bars_fetched = 0
        bars_written = 0
        processed = 0
        now_ns = self._clock_ns()

        await self._source.connect()
        try:
            requested_ids = [InstrumentId.from_str(spec.instrument_id) for spec in instruments]
            loaded = await self._source.request_instruments(requested_ids)
            self._catalog.write_instruments(loaded)
            loaded_ids = {instrument.id.value for instrument in loaded}

            for spec in instruments:
                if spec.instrument_id not in loaded_ids:
                    issues.append(
                        QualityIssue(
                            code="instrument_not_found",
                            severity="error",
                            instrument_id=spec.instrument_id,
                            timestamp_ns=None,
                            message="IBKR did not resolve the configured instrument.",
                        ),
                    )
                    continue

                processed += 1
                bar_type = self._bar_type(spec.instrument_id)
                earliest_ns = self._catalog.earliest_bar_timestamp(bar_type)
                latest_ns = self._catalog.latest_bar_timestamp(bar_type)
                fetch_start = start
                start_coverage_limit_ns = int(
                    (start.replace(tzinfo=UTC) + timedelta(days=7)).timestamp() * 1_000_000_000,
                )
                has_start_coverage = (
                    earliest_ns is not None and earliest_ns <= start_coverage_limit_ns
                )
                if latest_ns is not None and has_start_coverage:
                    latest = datetime.fromtimestamp(latest_ns / 1_000_000_000, tz=UTC)
                    overlap_start = latest - timedelta(
                        days=self._config.historical_data.overlap_days,
                    )
                    fetch_start = max(start, overlap_start.replace(tzinfo=None))

                fetched: list[Bar] = []
                try:
                    windows = self._windows(fetch_start, end)
                    for index, (window_start, window_end) in enumerate(windows):
                        fetched.extend(
                            await self._request_with_retry(
                                InstrumentId.from_str(spec.instrument_id),
                                window_start,
                                window_end,
                            ),
                        )
                        if index + 1 < len(windows):
                            await self._sleep(
                                self._config.historical_data.request_interval_seconds,
                            )
                except (ConnectionError, TimeoutError, RuntimeError) as exc:
                    issues.append(
                        QualityIssue(
                            code="fetch_failed",
                            severity="error",
                            instrument_id=spec.instrument_id,
                            timestamp_ns=None,
                            message=f"Historical data request failed: {exc}",
                        ),
                    )
                    continue

                fetched = [
                    bar
                    for bar in _deduplicate_bars(fetched)
                    if bar.bar_type == bar_type and bar.ts_init <= now_ns
                ]
                bars_fetched += len(fetched)

                existing = self._catalog.read_bars(
                    bar_type,
                    start_ns=int(fetch_start.replace(tzinfo=UTC).timestamp() * 1_000_000_000),
                )
                issues.extend(detect_historical_revisions(spec.instrument_id, existing, fetched))
                report = validate_daily_bars(
                    spec.instrument_id,
                    fetched,
                    bar_type,
                    self._config.quality,
                    as_of_ns=now_ns,
                )
                reports.append(report)
                if report.has_errors:
                    continue
                bars_written += self._catalog.append_new_bars(fetched)
        finally:
            await self._source.close()

        report_path = self._write_report(
            mode="sync",
            reports=reports,
            issues=issues,
            bars_fetched=bars_fetched,
            bars_written=bars_written,
        )
        return PipelineSummary(
            instruments_processed=processed,
            bars_fetched=bars_fetched,
            bars_written=bars_written,
            instrument_reports=tuple(reports),
            issues=tuple(issues),
            report_path=report_path,
        )

    def validate_catalog(self, instruments: Sequence[InstrumentSpec]) -> PipelineSummary:
        """不连接 IBKR; 检查 Catalog 当前全部日线。"""
        reports: list[DataQualityReport] = []
        now_ns = self._clock_ns()
        for spec in instruments:
            bar_type = self._bar_type(spec.instrument_id)
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
        )
        return PipelineSummary(
            instruments_processed=len(instruments),
            bars_fetched=0,
            bars_written=0,
            instrument_reports=tuple(reports),
            issues=(),
            report_path=report_path,
        )
