"""历史数据管道编排测试。"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from nautilus_trader.model.data import Bar
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from tests.data.helpers import make_bar, utc_ns
from trading_assistant.data.catalog import CatalogRepository
from trading_assistant.data.config import (
    DataPipelineConfig,
    HistoricalDataConfig,
    InstrumentSpec,
    QualityConfig,
)
from trading_assistant.data.corporate_actions import (
    CorporateActionRepository,
    CorporateActions,
    SplitAction,
)
from trading_assistant.data.pipeline import HistoricalDataPipeline


class FakeSource:
    """可控制失败次数的内存历史数据源。"""

    def __init__(self, bars: list[Bar], *, failures: int = 0, resolve: bool = True) -> None:
        self.bars = bars
        self.failures = failures
        self.resolve = resolve
        self.connect_count = 0
        self.close_count = 0
        self.requests: list[tuple[datetime, datetime]] = []
        self.action_requests: list[tuple[datetime, datetime]] = []

    async def connect(self) -> None:
        self.connect_count += 1

    async def request_instruments(
        self,
        specs: Sequence[InstrumentSpec],
    ) -> list[Instrument]:
        if not self.resolve:
            return []
        return [TestInstrumentProvider.equity("SPY", "US")]

    async def request_corporate_actions(
        self,
        spec: InstrumentSpec,
        start: datetime,
        end: datetime,
    ) -> CorporateActions:
        self.action_requests.append((start, end))
        return CorporateActions(spec.instrument_id, (), ())

    async def request_daily_bars(
        self,
        spec: InstrumentSpec,
        start: datetime,
        end: datetime,
    ) -> list[Bar]:
        self.requests.append((start, end))
        if self.failures > 0:
            self.failures -= 1
            raise TimeoutError("temporary timeout")
        return self.bars

    async def close(self) -> None:
        self.close_count += 1


async def no_sleep(_: float) -> None:
    """测试中跳过退避等待。"""


def _config(*, request_window_days: int | None = 365, max_attempts: int = 3) -> DataPipelineConfig:
    """构造紧凑的管道配置。"""
    return DataPipelineConfig(
        historical_data=HistoricalDataConfig(
            provider="ibkr",
            price_basis="split_adjusted",
            refresh_mode="append",
            history_years=5,
            signal_bar_type_suffix="1-DAY-LAST-EXTERNAL",
            execution_bar_type_suffix="1-DAY-LAST-EXTERNAL",
            use_regular_trading_hours=True,
            request_window_days=request_window_days,
            request_interval_seconds=0,
            max_attempts=max_attempts,
            retry_backoff_seconds=(0, 0),
            live_sync_delay_minutes=30,
            overlap_days=10,
            request_timeout_seconds=120,
            max_concurrent_requests=2,
        ),
        quality=QualityConfig(max_absolute_daily_return=0.25, stale_after_days=5),
    )


def _spec() -> InstrumentSpec:
    """返回测试标的配置。"""
    return InstrumentSpec(
        "SPY",
        "SPY.US",
        "SPY.US",
        "SMART",
        "ARCA",
        "USD",
        4,
        "0.0100",
        1,
        "SPY.ARCA",
    )


def _replace_config() -> DataPipelineConfig:
    """构造 EODHD 全范围替换配置。"""
    config = _config()
    return replace(
        config,
        historical_data=replace(
            config.historical_data,
            provider="eodhd",
            price_basis="total_return_adjusted",
            refresh_mode="replace",
            signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
            request_window_days=None,
        ),
    )


def _pipeline(
    tmp_path: Path,
    source: FakeSource | None,
    *,
    config: DataPipelineConfig | None = None,
) -> tuple[HistoricalDataPipeline, CatalogRepository]:
    """构造使用临时 Catalog 的管道。"""
    catalog = CatalogRepository(tmp_path / "catalog")
    pipeline = HistoricalDataPipeline(
        config=config or _config(),
        catalog=catalog,
        report_directory=tmp_path / "reports",
        source=source,
        corporate_actions=CorporateActionRepository(tmp_path / "catalog-actions"),
        clock_ns=lambda: utc_ns(date(2026, 7, 20)),
        sleep=no_sleep,
    )
    return pipeline, catalog


def test_pipeline_sync_is_chunked_retried_and_idempotent(tmp_path: Path) -> None:
    """同步应串行分块、重试; 并在第二次运行时写入零条。"""
    bars = [
        make_bar(date(2026, 7, 13), instrument_id="SPY.US"),
        make_bar(date(2026, 7, 14), instrument_id="SPY.US", close=102),
    ]
    source = FakeSource(bars, failures=1)
    pipeline, catalog = _pipeline(tmp_path, source, config=_config(request_window_days=2))
    start = datetime(2026, 7, 10)
    end = datetime(2026, 7, 15)

    first = asyncio.run(pipeline.sync([_spec()], start=start, end=end))
    assert first.bars_written == 2
    assert first.bars_fetched == 2
    assert first.instruments_processed == 1
    assert first.corporate_actions_written == 1
    assert first.report_path.exists()
    assert source.connect_count == source.close_count == 1
    assert len(source.requests) == 4  # 首个窗口重试一次; 加上后续两个窗口。

    second_source = FakeSource(bars)
    second = HistoricalDataPipeline(
        config=_config(request_window_days=20),
        catalog=catalog,
        report_directory=tmp_path / "reports",
        source=second_source,
        clock_ns=lambda: utc_ns(date(2026, 7, 20)),
        sleep=no_sleep,
    )
    result = asyncio.run(second.sync([_spec()], start=start, end=end))
    assert result.bars_written == 0
    assert len(catalog.read_bars(bars[0].bar_type)) == 2


def test_pipeline_reports_missing_instrument_and_fetch_failure(tmp_path: Path) -> None:
    """合约解析和请求失败应形成错误; 并确保关闭连接。"""
    missing = FakeSource([], resolve=False)
    pipeline, _ = _pipeline(tmp_path / "missing", missing)
    result = asyncio.run(
        pipeline.sync([_spec()], start=datetime(2026, 7, 10), end=datetime(2026, 7, 15)),
    )
    assert result.has_errors
    assert result.issues[0].code == "instrument_not_found"
    assert missing.close_count == 1

    failing = FakeSource([], failures=3)
    pipeline, _ = _pipeline(tmp_path / "failing", failing)
    result = asyncio.run(
        pipeline.sync([_spec()], start=datetime(2026, 7, 10), end=datetime(2026, 7, 15)),
    )
    assert result.has_errors
    assert result.issues[0].code == "fetch_failed"
    assert failing.close_count == 1


def test_pipeline_filters_incomplete_bars_and_validates_offline(tmp_path: Path) -> None:
    """未来完成时间的 Bar 不得入库; 离线检查应复用相同规则。"""
    complete = make_bar(date(2026, 7, 14), instrument_id="SPY.US")
    incomplete = make_bar(
        date(2026, 7, 20),
        instrument_id="SPY.US",
        ts_init=utc_ns(date(2026, 7, 21)),
    )
    source = FakeSource([complete, incomplete])
    pipeline, catalog = _pipeline(tmp_path, source)
    result = asyncio.run(
        pipeline.sync([_spec()], start=datetime(2026, 7, 10), end=datetime(2026, 7, 21)),
    )
    assert result.bars_written == 1
    assert catalog.read_bars(complete.bar_type) == [complete]

    offline = pipeline.validate_catalog([_spec()])
    assert offline.instruments_processed == 1
    assert not offline.has_errors


def test_pipeline_replaces_adjusted_history_after_validation(tmp_path: Path) -> None:
    """EODHD 修订应告警并替换完整序列; 不与旧供应商数据混合。"""
    stored = make_bar(date(2026, 7, 13), instrument_id="SPY.US", close=101)
    revised = make_bar(date(2026, 7, 13), instrument_id="SPY.US", close=100)
    latest = make_bar(date(2026, 7, 14), instrument_id="SPY.US", close=102)
    signal_revised = make_bar(
        date(2026, 7, 13),
        instrument_id="SPY.US",
        bar_type_suffix="1-DAY-LAST-INTERNAL",
        low=94,
        close=95,
    )
    signal_latest = make_bar(
        date(2026, 7, 14),
        instrument_id="SPY.US",
        bar_type_suffix="1-DAY-LAST-INTERNAL",
        low=96,
        close=97,
    )
    source = FakeSource([revised, signal_revised, latest, signal_latest])
    pipeline, catalog = _pipeline(tmp_path, source, config=_replace_config())
    assert catalog.append_new_bars([stored]) == 1

    result = asyncio.run(
        pipeline.sync([_spec()], start=datetime(2026, 7, 10), end=datetime(2026, 7, 15)),
    )

    assert result.bars_written == 4
    assert len(source.requests) == 1
    assert {issue.code for issue in result.issues} == {"historical_revision_detected"}
    assert catalog.read_bars(stored.bar_type) == [revised, latest]
    assert catalog.read_bars(signal_revised.bar_type) == [signal_revised, signal_latest]


def test_explicit_append_missing_fetches_full_window_without_revising_overlap(
    tmp_path: Path,
) -> None:
    """bootstrap 必须补齐两端缺口, 但不得覆盖既有时间戳。"""
    stored = make_bar(date(2026, 7, 13), instrument_id="SPY.US", close=101)
    earlier = make_bar(
        date(2026, 1, 2),
        instrument_id="SPY.US",
        open_price=89,
        high=91,
        low=88,
        close=90,
    )
    revised = make_bar(
        date(2026, 7, 13),
        instrument_id="SPY.US",
        open_price=98,
        high=101,
        low=97,
        close=99,
    )
    latest = make_bar(
        date(2026, 7, 14),
        instrument_id="SPY.US",
        high=103,
        close=102,
    )
    source = FakeSource([earlier, revised, latest])
    pipeline, catalog = _pipeline(
        tmp_path,
        source,
        config=_config(request_window_days=800),
    )
    assert catalog.append_new_bars([stored]) == 1
    start = datetime(2026, 1, 1)

    result = asyncio.run(
        pipeline.sync(
            [_spec()],
            start=start,
            end=datetime(2026, 7, 15),
            write_mode="append_missing",
        )
    )

    assert source.requests[0][0] == start
    assert result.bars_written == 2
    assert {issue.code for issue in result.issues} == {"historical_revision_detected"}
    assert catalog.read_bars(stored.bar_type) == [earlier, stored, latest]


def test_explicit_range_replace_uses_overlap_and_preserves_older_data(
    tmp_path: Path,
) -> None:
    """daily 仅修订重叠窗口, 同时保留多年 Bar 与公司行动。"""
    external_old = make_bar(
        date(2026, 1, 2),
        instrument_id="SPY.US",
        open_price=89,
        high=91,
        low=88,
        close=90,
    )
    internal_old = make_bar(
        date(2026, 1, 2),
        instrument_id="SPY.US",
        bar_type_suffix="1-DAY-LAST-INTERNAL",
        open_price=89,
        high=91,
        low=88,
        close=90,
    )
    external_stored = make_bar(date(2026, 7, 10), instrument_id="SPY.US", close=101)
    internal_stored = make_bar(
        date(2026, 7, 10),
        instrument_id="SPY.US",
        bar_type_suffix="1-DAY-LAST-INTERNAL",
        close=100,
    )
    external_revised = make_bar(
        date(2026, 7, 10),
        instrument_id="SPY.US",
        close=100,
    )
    internal_revised = make_bar(
        date(2026, 7, 10),
        instrument_id="SPY.US",
        bar_type_suffix="1-DAY-LAST-INTERNAL",
        close=99,
    )
    external_latest = make_bar(
        date(2026, 7, 13),
        instrument_id="SPY.US",
        high=103,
        close=102,
    )
    internal_latest = make_bar(
        date(2026, 7, 13),
        instrument_id="SPY.US",
        bar_type_suffix="1-DAY-LAST-INTERNAL",
        high=103,
        close=102,
    )
    source = FakeSource([external_revised, internal_revised, external_latest, internal_latest])
    pipeline, catalog = _pipeline(tmp_path, source, config=_replace_config())
    catalog.replace_bars([external_old, external_stored, internal_old, internal_stored])
    actions = CorporateActionRepository(tmp_path / "catalog-actions")
    actions.write(
        CorporateActions(
            instrument_id="SPY.US",
            dividends=(),
            splits=(SplitAction(date(2020, 8, 31), Decimal("4")),),
        )
    )
    start = datetime(2026, 1, 1)

    result = asyncio.run(
        pipeline.sync(
            [_spec()],
            start=start,
            end=datetime(2026, 7, 13),
            write_mode="replace_range",
        )
    )

    assert source.requests[0][0] > start
    assert result.bars_written == 4
    assert catalog.read_bars(external_old.bar_type) == [
        external_old,
        external_revised,
        external_latest,
    ]
    assert catalog.read_bars(internal_old.bar_type) == [
        internal_old,
        internal_revised,
        internal_latest,
    ]
    assert actions.read("SPY.US").splits == (SplitAction(date(2020, 8, 31), Decimal("4")),)


def test_pipeline_backfills_when_catalog_only_contains_recent_history(
    tmp_path: Path,
) -> None:
    """近期冒烟数据不得让全量同步跳过更早的目标历史。"""
    old = make_bar(
        date(2025, 1, 2),
        instrument_id="SPY.US",
        open_price=90,
        high=91,
        low=89,
        close=90,
    )
    recent = make_bar(date(2026, 7, 14), instrument_id="SPY.US", close=102)
    source = FakeSource([old, recent])
    pipeline, catalog = _pipeline(tmp_path, source, config=_config(request_window_days=800))
    assert catalog.append_new_bars([recent]) == 1
    start = datetime(2025, 1, 1)
    end = datetime(2026, 7, 15)

    result = asyncio.run(pipeline.sync([_spec()], start=start, end=end))
    assert result.bars_written == 1
    assert source.requests[0][0] == start
    assert catalog.read_bars(recent.bar_type) == [old, recent]

    incremental_source = FakeSource([recent])
    incremental = HistoricalDataPipeline(
        config=_config(request_window_days=800),
        catalog=catalog,
        report_directory=tmp_path / "reports",
        source=incremental_source,
        clock_ns=lambda: utc_ns(date(2026, 7, 20)),
        sleep=no_sleep,
    )
    second = asyncio.run(incremental.sync([_spec()], start=start, end=end))
    assert second.bars_written == 0
    assert incremental_source.requests[0][0] > start


def test_pipeline_requires_source_and_handles_empty_window(tmp_path: Path) -> None:
    """同步必须提供数据源; 空窗口不应发起历史请求。"""
    pipeline, _ = _pipeline(tmp_path / "none", None)
    with pytest.raises(RuntimeError, match="required"):
        asyncio.run(
            pipeline.sync([_spec()], start=datetime(2026, 7, 15), end=datetime(2026, 7, 10)),
        )

    source = FakeSource([])
    pipeline, _ = _pipeline(tmp_path / "empty", source)
    result = asyncio.run(
        pipeline.sync([_spec()], start=datetime(2026, 7, 15), end=datetime(2026, 7, 15)),
    )
    assert result.has_errors
    assert source.requests == []


def test_pipeline_uses_explicit_instrument_lifecycle(tmp_path: Path) -> None:
    """上市日晚于全局起点时应从上市日校验覆盖且不误报缺失。"""
    first_session = date(2026, 7, 13)
    bars = [
        make_bar(first_session, instrument_id="SPY.US"),
        make_bar(date(2026, 7, 14), instrument_id="SPY.US", close=102),
        make_bar(
            first_session,
            instrument_id="SPY.US",
            bar_type_suffix="1-DAY-LAST-INTERNAL",
        ),
        make_bar(
            date(2026, 7, 14),
            instrument_id="SPY.US",
            bar_type_suffix="1-DAY-LAST-INTERNAL",
            close=102,
        ),
    ]
    source = FakeSource(bars)
    pipeline, _ = _pipeline(tmp_path, source, config=_replace_config())
    spec = replace(_spec(), first_trading_date=first_session)

    result = asyncio.run(
        pipeline.sync([spec], start=datetime(2020, 1, 1), end=datetime(2026, 7, 15))
    )

    assert not result.has_errors
    assert source.requests[0][0].date() == first_session
    assert source.action_requests[0][0].date() == first_session


def test_pipeline_can_delegate_late_listing_history_to_caller_coverage(
    tmp_path: Path,
) -> None:
    """当前横截面可写入新上市标的已有历史, 默认严格策略保持不变。"""
    bar = make_bar(date(2026, 7, 13), instrument_id="SPY.US")
    start = datetime(2026, 7, 1)
    end = datetime(2026, 7, 15)

    strict, strict_catalog = _pipeline(tmp_path / "strict", FakeSource([bar]))
    strict_result = asyncio.run(strict.sync([_spec()], start=start, end=end))
    assert strict_result.has_errors
    assert strict_result.issues[0].code == "start_coverage_missing"
    assert strict_catalog.read_bars(bar.bar_type) == []

    partial, partial_catalog = _pipeline(tmp_path / "partial", FakeSource([bar]))
    partial_result = asyncio.run(
        partial.sync(
            [_spec()],
            start=start,
            end=end,
            require_start_coverage=False,
        )
    )
    assert not partial_result.has_errors
    assert partial_catalog.read_bars(bar.bar_type) == [bar]


def test_pipeline_skips_lifecycle_without_window_and_caps_delisted_staleness(
    tmp_path: Path,
) -> None:
    """生命周期无交集时不请求; 退市后离线检查不持续报告陈旧。"""
    source = FakeSource([])
    pipeline, catalog = _pipeline(tmp_path, source)
    future = replace(_spec(), first_trading_date=date(2027, 1, 1))
    result = asyncio.run(
        pipeline.sync(
            [future],
            start=datetime(2026, 1, 1),
            end=datetime(2026, 12, 31),
        )
    )
    assert not result.has_errors
    assert source.requests == []
    assert source.action_requests == []

    last_session = date(2026, 7, 10)
    bar = make_bar(last_session, instrument_id="SPY.US")
    assert catalog.append_new_bars([bar]) == 1
    delisted = replace(_spec(), last_trading_date=last_session)
    offline = pipeline.validate_catalog([delisted])
    issues = [issue.code for report in offline.instrument_reports for issue in report.issues]
    assert "stale_data" not in issues
