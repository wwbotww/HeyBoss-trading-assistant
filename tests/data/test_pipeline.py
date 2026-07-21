"""历史数据管道编排测试。"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path

import pytest
from nautilus_trader.model.data import Bar
from nautilus_trader.model.identifiers import InstrumentId
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

    async def connect(self) -> None:
        self.connect_count += 1

    async def request_instruments(
        self,
        instrument_ids: Sequence[InstrumentId],
    ) -> list[Instrument]:
        if not self.resolve:
            return []
        return [TestInstrumentProvider.equity("SPY", "ARCA")]

    async def request_daily_bars(
        self,
        instrument_id: InstrumentId,
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


def _config(*, chunk_days: int = 365, max_attempts: int = 3) -> DataPipelineConfig:
    """构造紧凑的管道配置。"""
    return DataPipelineConfig(
        historical_data=HistoricalDataConfig(
            history_years=5,
            bar_type_suffix="1-DAY-LAST-EXTERNAL",
            use_regular_trading_hours=True,
            chunk_days=chunk_days,
            request_interval_seconds=0,
            max_attempts=max_attempts,
            retry_backoff_seconds=(0, 0),
            live_sync_delay_minutes=30,
            overlap_days=10,
            request_timeout_seconds=120,
        ),
        quality=QualityConfig(max_absolute_daily_return=0.25, stale_after_days=5),
    )


def _spec() -> InstrumentSpec:
    """返回测试标的配置。"""
    return InstrumentSpec("SPY", "SPY.ARCA", "SMART", "ARCA", "USD")


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
        clock_ns=lambda: utc_ns(date(2026, 7, 20)),
        sleep=no_sleep,
    )
    return pipeline, catalog


def test_pipeline_sync_is_chunked_retried_and_idempotent(tmp_path: Path) -> None:
    """同步应串行分块、重试; 并在第二次运行时写入零条。"""
    bars = [
        make_bar(date(2026, 7, 13)),
        make_bar(date(2026, 7, 14), close=102),
    ]
    source = FakeSource(bars, failures=1)
    pipeline, catalog = _pipeline(tmp_path, source, config=_config(chunk_days=2))
    start = datetime(2026, 7, 10)
    end = datetime(2026, 7, 15)

    first = asyncio.run(pipeline.sync([_spec()], start=start, end=end))
    assert first.bars_written == 2
    assert first.bars_fetched == 2
    assert first.instruments_processed == 1
    assert first.report_path.exists()
    assert source.connect_count == source.close_count == 1
    assert len(source.requests) == 4  # 首个窗口重试一次; 加上后续两个窗口。

    second_source = FakeSource(bars)
    second = HistoricalDataPipeline(
        config=_config(chunk_days=20),
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
    complete = make_bar(date(2026, 7, 14))
    incomplete = make_bar(
        date(2026, 7, 20),
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


def test_pipeline_backfills_when_catalog_only_contains_recent_history(
    tmp_path: Path,
) -> None:
    """近期冒烟数据不得让全量同步跳过更早的目标历史。"""
    old = make_bar(
        date(2025, 1, 2),
        open_price=90,
        high=91,
        low=89,
        close=90,
    )
    recent = make_bar(date(2026, 7, 14), close=102)
    source = FakeSource([old, recent])
    pipeline, catalog = _pipeline(tmp_path, source, config=_config(chunk_days=800))
    assert catalog.append_new_bars([recent]) == 1
    start = datetime(2025, 1, 1)
    end = datetime(2026, 7, 15)

    result = asyncio.run(pipeline.sync([_spec()], start=start, end=end))
    assert result.bars_written == 1
    assert source.requests[0][0] == start
    assert catalog.read_bars(recent.bar_type) == [old, recent]

    incremental_source = FakeSource([recent])
    incremental = HistoricalDataPipeline(
        config=_config(chunk_days=800),
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
