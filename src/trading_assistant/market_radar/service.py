"""市场雷达价格同步与运行状态编排。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from trading_assistant.data.config import load_instruments
from trading_assistant.data.pipeline import CatalogWriteMode
from trading_assistant.data.service import (
    select_instruments,
    sync_historical_specs,
)
from trading_assistant.market_radar.config import load_market_radar_config
from trading_assistant.market_radar.metrics import calculate_price_snapshot
from trading_assistant.market_radar.prices import (
    build_price_instrument_specs,
    load_internal_price_bars,
)
from trading_assistant.market_radar.storage import MarketRadarRepository, SyncRunStatus

LOGGER = logging.getLogger(__name__)

MarketRadarSyncMode = Literal["bootstrap", "daily", "reconcile"]
_WRITE_MODES: dict[MarketRadarSyncMode, CatalogWriteMode] = {
    "bootstrap": "append_missing",
    "daily": "replace_range",
    "reconcile": "replace_full",
}


@dataclass(frozen=True)
class MarketRadarPriceSyncSummary:
    """一次市场雷达价格同步的稳定结果。"""

    run_id: str
    mode: MarketRadarSyncMode
    status: SyncRunStatus
    instruments_processed: int
    bars_fetched: int
    bars_written: int
    corporate_actions_written: int
    report_path: Path
    snapshot_date: date | None

    @property
    def has_errors(self) -> bool:
        """返回本次运行是否失败关闭。"""
        return self.status == "FAILED"


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _run_id() -> str:
    return uuid4().hex


async def sync_market_radar_prices(
    *,
    catalog_path: Path,
    database_url: str,
    report_directory: Path,
    market_config_path: Path,
    trading_instruments_config_path: Path,
    data_config_path: Path,
    mode: MarketRadarSyncMode,
    start_date: date | None,
    end_date: date | None,
    selected_ids: tuple[str, ...] = (),
    eodhd_api_token: str | None = None,
    clock: Callable[[], datetime] = _utc_now,
    run_id_factory: Callable[[], str] = _run_id,
) -> MarketRadarPriceSyncSummary:
    """经共享管道同步价格, 并仅在指标快照完成后发布成功状态。"""
    if mode not in _WRITE_MODES:
        raise ValueError(f"unsupported market radar sync mode: {mode}")
    market_config = load_market_radar_config(market_config_path)
    trading_instruments = load_instruments(trading_instruments_config_path)
    configured = build_price_instrument_specs(market_config, trading_instruments)
    instruments = select_instruments(configured, selected_ids)
    if start_date is not None and end_date is not None and start_date >= end_date:
        raise ValueError("start date must be earlier than end date")

    repository = MarketRadarRepository(database_url)
    repository.create_schema()
    run_id = run_id_factory()
    repository.start_sync_run(
        run_id=run_id,
        source="eodhd_prices",
        started_at_utc=clock(),
        requested_start_date=start_date,
        requested_end_date=end_date,
        instrument_count=len(instruments),
    )
    instruments_processed = 0
    bars_fetched = 0
    bars_written = 0
    try:
        pipeline = await sync_historical_specs(
            catalog_path=catalog_path,
            report_directory=report_directory,
            data_config_path=data_config_path,
            instruments=instruments,
            start_date=start_date,
            end_date=end_date,
            eodhd_api_token=eodhd_api_token,
            write_mode=_WRITE_MODES[mode],
        )
        instruments_processed = pipeline.instruments_processed
        bars_fetched = pipeline.bars_fetched
        bars_written = pipeline.bars_written

        completed_at = clock()
        if pipeline.has_errors:
            status: SyncRunStatus = "FAILED"
            repository.fail_sync_run(
                run_id,
                completed_at_utc=completed_at,
                error_summary="data_quality",
                instruments_processed=pipeline.instruments_processed,
                bars_fetched=pipeline.bars_fetched,
                bars_written=pipeline.bars_written,
            )
            snapshot_date = None
        else:
            status = "COMPLETE"
            bars_by_instrument = load_internal_price_bars(
                catalog_path,
                market_config.price_instrument_ids,
            )
            snapshot = calculate_price_snapshot(
                market_config,
                bars_by_instrument,
                calculated_at_utc=completed_at,
            )
            repository.publish_price_snapshot_and_complete(
                run_id,
                snapshot=snapshot,
                completed_at_utc=completed_at,
                instruments_processed=pipeline.instruments_processed,
                bars_fetched=pipeline.bars_fetched,
                bars_written=pipeline.bars_written,
            )
            snapshot_date = snapshot.as_of_date
        return MarketRadarPriceSyncSummary(
            run_id=run_id,
            mode=mode,
            status=status,
            instruments_processed=pipeline.instruments_processed,
            bars_fetched=pipeline.bars_fetched,
            bars_written=pipeline.bars_written,
            corporate_actions_written=pipeline.corporate_actions_written,
            report_path=pipeline.report_path,
            snapshot_date=snapshot_date,
        )
    except Exception as exc:
        try:
            run = repository.get_sync_run(run_id)
            if run is not None and run.status == "RUNNING":
                repository.fail_sync_run(
                    run_id,
                    completed_at_utc=clock(),
                    error_summary=type(exc).__name__,
                    instruments_processed=instruments_processed,
                    bars_fetched=bars_fetched,
                    bars_written=bars_written,
                )
        except Exception as transition_error:
            LOGGER.error(
                "Failed to persist market radar sync failure: %s",
                type(transition_error).__name__,
            )
        raise
    finally:
        repository.close()
