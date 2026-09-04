"""市场雷达价格同步与运行状态编排。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import uuid4

from trading_assistant.data.config import load_data_config, load_instruments
from trading_assistant.data.pipeline import CatalogWriteMode
from trading_assistant.data.service import (
    select_instruments,
    sync_historical_specs,
)
from trading_assistant.market_radar.config import load_market_radar_config
from trading_assistant.market_radar.fred import FredApiObservationSource, FredObservationSource
from trading_assistant.market_radar.macro import calculate_risk_appetite_snapshot
from trading_assistant.market_radar.membership import CurrentMarketMembershipSource
from trading_assistant.market_radar.metrics import (
    calculate_current_breadth_snapshot,
    calculate_price_snapshot,
)
from trading_assistant.market_radar.prices import (
    build_macro_price_instrument_specs,
    build_membership_instrument_specs,
    build_price_instrument_specs,
    load_internal_price_bars,
)
from trading_assistant.market_radar.regime import calculate_macro_regime_snapshot
from trading_assistant.market_radar.storage import MarketRadarRepository, SyncRunStatus

LOGGER = logging.getLogger(__name__)

MarketRadarSyncMode = Literal["bootstrap", "daily", "reconcile"]
_WRITE_MODES: dict[MarketRadarSyncMode, CatalogWriteMode] = {
    "bootstrap": "append_missing",
    "daily": "replace_range",
    "reconcile": "replace_full",
}
MarketBreadthSyncMode = Literal["bootstrap", "daily"]
_BREADTH_WRITE_MODES: dict[MarketBreadthSyncMode, CatalogWriteMode] = {
    "bootstrap": "append_missing",
    "daily": "replace_range",
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


@dataclass(frozen=True)
class MarketBreadthSyncSummary:
    """一次当前成员价格与宽度同步的稳定结果。"""

    run_id: str
    mode: MarketBreadthSyncMode
    status: SyncRunStatus
    membership_source: str
    membership_date: date
    member_count: int
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


@dataclass(frozen=True)
class MarketMacroSyncSummary:
    """一次宏观价格、实际利率与四象限同步结果。"""

    run_id: str
    mode: MarketRadarSyncMode
    status: SyncRunStatus
    instruments_processed: int
    bars_fetched: int
    bars_written: int
    corporate_actions_written: int
    report_path: Path
    snapshot_date: date | None
    risk_appetite_validity: str | None
    regime_validity: str | None
    real_rate_observations: int
    real_rate_missing_values: int

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


async def sync_market_macro(
    *,
    catalog_path: Path,
    database_url: str,
    report_directory: Path,
    market_config_path: Path,
    data_config_path: Path,
    mode: MarketRadarSyncMode,
    start_date: date | None,
    end_date: date | None,
    eodhd_api_token: str | None = None,
    fred_api_key: str | None = None,
    fred_source: FredObservationSource | None = None,
    clock: Callable[[], datetime] = _utc_now,
    run_id_factory: Callable[[], str] = _run_id,
) -> MarketMacroSyncSummary:
    """同步两类宏观来源并原子发布风险偏好与四象限快照。"""
    if mode not in _WRITE_MODES:
        raise ValueError(f"unsupported market macro sync mode: {mode}")
    started_at = clock()
    if started_at.tzinfo is None or started_at.utcoffset() is None:
        raise ValueError("market radar clock must return a timezone-aware timestamp")
    resolved_end = end_date or started_at.astimezone(UTC).date()
    resolved_start = start_date or resolved_end - timedelta(days=365 * 4)
    if resolved_start >= resolved_end:
        raise ValueError("start date must be earlier than end date")

    market_config = load_market_radar_config(market_config_path)
    data_config = load_data_config(data_config_path)
    real_rate_source = fred_source or FredApiObservationSource(
        api_key=fred_api_key or "",
        request_timeout_seconds=data_config.historical_data.request_timeout_seconds,
        max_attempts=data_config.historical_data.max_attempts,
        retry_backoff_seconds=data_config.historical_data.retry_backoff_seconds,
    )
    instruments = build_macro_price_instrument_specs(market_config)
    repository = MarketRadarRepository(database_url)
    repository.create_schema()
    run_id = run_id_factory()
    repository.start_sync_run(
        run_id=run_id,
        source="macro_regime",
        started_at_utc=started_at,
        requested_start_date=resolved_start,
        requested_end_date=resolved_end,
        instrument_count=len(instruments),
    )
    instruments_processed = 0
    bars_fetched = 0
    bars_written = 0
    real_rate_observations = 0
    real_rate_missing_values = 0
    try:
        pipeline = await sync_historical_specs(
            catalog_path=catalog_path,
            report_directory=report_directory,
            data_config_path=data_config_path,
            instruments=instruments,
            start_date=resolved_start,
            end_date=resolved_end,
            eodhd_api_token=eodhd_api_token,
            write_mode=_WRITE_MODES[mode],
        )
        instruments_processed = pipeline.instruments_processed
        bars_fetched = pipeline.bars_fetched
        bars_written = pipeline.bars_written
        if pipeline.has_errors:
            completed_at = clock()
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
            risk_appetite_validity = None
            regime_validity = None
        else:
            status = "COMPLETE"
            bars = load_internal_price_bars(
                catalog_path,
                market_config.macro_price_instrument_ids,
            )
            bounded_bars = {
                instrument_id: tuple(
                    bar for bar in instrument_bars if resolved_start <= bar.day <= resolved_end
                )
                for instrument_id, instrument_bars in bars.items()
            }
            real_rates = await real_rate_source.request_observations(
                market_config.real_rate_series,
                resolved_start,
                resolved_end,
            )
            real_rate_observations = len(real_rates.observations)
            real_rate_missing_values = real_rates.missing_values
            completed_at = clock()
            risk_snapshot = calculate_risk_appetite_snapshot(
                hyg_bars=bounded_bars[market_config.credit_proxy[0]],
                lqd_bars=bounded_bars[market_config.credit_proxy[1]],
                vix_bars=bounded_bars[market_config.vix],
                vix3m_bars=bounded_bars[market_config.vix3m],
                calculated_at_utc=completed_at,
            )
            regime_snapshot = calculate_macro_regime_snapshot(
                real_rate_observations=real_rates.observations,
                risk_appetite=risk_snapshot,
                calculated_at_utc=completed_at,
            )
            repository.publish_macro_bundle_and_complete(
                run_id,
                risk_snapshot=risk_snapshot,
                observations=real_rates.observations,
                regime_snapshot=regime_snapshot,
                ingested_at_utc=completed_at,
                completed_at_utc=completed_at,
                instruments_processed=pipeline.instruments_processed,
                bars_fetched=pipeline.bars_fetched,
                bars_written=pipeline.bars_written,
            )
            snapshot_date = regime_snapshot.as_of_date
            risk_appetite_validity = risk_snapshot.validity
            regime_validity = regime_snapshot.validity
        return MarketMacroSyncSummary(
            run_id=run_id,
            mode=mode,
            status=status,
            instruments_processed=pipeline.instruments_processed,
            bars_fetched=pipeline.bars_fetched,
            bars_written=pipeline.bars_written,
            corporate_actions_written=pipeline.corporate_actions_written,
            report_path=pipeline.report_path,
            snapshot_date=snapshot_date,
            risk_appetite_validity=risk_appetite_validity,
            regime_validity=regime_validity,
            real_rate_observations=real_rate_observations,
            real_rate_missing_values=real_rate_missing_values,
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
                "Failed to persist market macro sync failure: %s",
                type(transition_error).__name__,
            )
        raise
    finally:
        repository.close()


async def sync_current_market_breadth(
    *,
    membership_source: CurrentMarketMembershipSource,
    catalog_path: Path,
    database_url: str,
    report_directory: Path,
    market_config_path: Path,
    trading_instruments_config_path: Path,
    data_config_path: Path,
    mode: MarketBreadthSyncMode,
    start_date: date | None,
    end_date: date | None,
    eodhd_api_token: str | None = None,
    clock: Callable[[], datetime] = _utc_now,
    run_id_factory: Callable[[], str] = _run_id,
) -> MarketBreadthSyncSummary:
    """通过可替换成员源和既有价格管道发布当前宽度快照。"""
    if mode not in _BREADTH_WRITE_MODES:
        raise ValueError(f"unsupported current breadth sync mode: {mode}")
    if start_date is not None and end_date is not None and start_date >= end_date:
        raise ValueError("start date must be earlier than end date")
    market_config = load_market_radar_config(market_config_path)
    trading_instruments = load_instruments(trading_instruments_config_path)
    membership = await membership_source.fetch_current_membership()
    instruments = build_membership_instrument_specs(membership, trading_instruments)

    started_at = clock()
    if started_at.tzinfo is None or started_at.utcoffset() is None:
        raise ValueError("market radar clock must return a timezone-aware timestamp")
    resolved_end = end_date or started_at.astimezone(UTC).date()
    resolved_start = start_date or resolved_end - timedelta(days=400)
    if resolved_start >= resolved_end:
        raise ValueError("start date must be earlier than end date")
    if membership.membership_date > resolved_end:
        raise ValueError("market membership date cannot be later than the requested end date")
    if (resolved_end - membership.membership_date).days > 7:
        raise ValueError("market membership snapshot is stale")

    repository = MarketRadarRepository(database_url)
    repository.create_schema()
    run_id = run_id_factory()
    repository.start_sync_run(
        run_id=run_id,
        source="current_breadth",
        started_at_utc=started_at,
        requested_start_date=resolved_start,
        requested_end_date=resolved_end,
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
            start_date=resolved_start,
            end_date=resolved_end,
            eodhd_api_token=eodhd_api_token,
            write_mode=_BREADTH_WRITE_MODES[mode],
            require_start_coverage=False,
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
            member_ids = tuple(member.instrument_id for member in membership.members)
            requested_ids = tuple(dict.fromkeys((market_config.benchmark, *member_ids)))
            loaded = load_internal_price_bars(catalog_path, requested_ids)
            snapshot = calculate_current_breadth_snapshot(
                membership,
                {instrument_id: loaded[instrument_id] for instrument_id in member_ids},
                loaded[market_config.benchmark],
                calculated_at_utc=completed_at,
            )
            repository.publish_current_breadth_and_complete(
                run_id,
                membership=membership,
                snapshot=snapshot,
                completed_at_utc=completed_at,
                instruments_processed=pipeline.instruments_processed,
                bars_fetched=pipeline.bars_fetched,
                bars_written=pipeline.bars_written,
            )
            snapshot_date = snapshot.as_of_date
        return MarketBreadthSyncSummary(
            run_id=run_id,
            mode=mode,
            status=status,
            membership_source=membership.source,
            membership_date=membership.membership_date,
            member_count=len(membership.members),
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
                "Failed to persist current breadth sync failure: %s",
                type(transition_error).__name__,
            )
        raise
    finally:
        repository.close()
