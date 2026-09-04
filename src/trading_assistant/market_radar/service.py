"""市场雷达价格同步与运行状态编排。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import uuid4

from trading_assistant.data.config import load_data_config, load_instruments
from trading_assistant.data.eodhd_http import HttpTransport, download
from trading_assistant.data.pipeline import CatalogWriteMode
from trading_assistant.data.service import (
    select_instruments,
    sync_historical_specs,
)
from trading_assistant.market_radar.config import load_market_radar_config
from trading_assistant.market_radar.earnings import (
    EarningsRevisionValidity,
    calculate_earnings_revision_snapshot,
)
from trading_assistant.market_radar.eodhd_calendar import EodhdCalendarSource
from trading_assistant.market_radar.fred import FredApiObservationSource, FredObservationSource
from trading_assistant.market_radar.macro import calculate_risk_appetite_snapshot
from trading_assistant.market_radar.membership import (
    CurrentMarketMembershipSource,
    CurrentMarketSectorClassificationSource,
)
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


@dataclass(frozen=True)
class MarketEarningsSyncSummary:
    """一次当前市场与 watchlist 盈利预期同步结果。"""

    run_id: str
    status: SyncRunStatus
    instrument_count: int
    membership_source: str
    membership_date: date
    market_member_count: int
    classification_source: str
    classification_record_count: int
    classified_member_count: int
    unclassified_member_count: int
    unused_classification_count: int
    classification_validity: EarningsRevisionValidity
    classification_coverage_ratio: float
    trend_batch_count: int
    trend_records_fetched: int
    trends_selected: int
    events_fetched: int
    snapshot_date: date | None
    watchlist_revision_validity: EarningsRevisionValidity
    watchlist_revision_coverage_ratio: float
    market_revision_validity: EarningsRevisionValidity
    market_revision_coverage_ratio: float

    @property
    def has_errors(self) -> bool:
        """返回本次运行是否失败关闭。"""
        return self.status == "FAILED"


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _run_id() -> str:
    return uuid4().hex


async def sync_market_earnings(
    *,
    membership_source: CurrentMarketMembershipSource,
    classification_source: CurrentMarketSectorClassificationSource,
    database_url: str,
    market_config_path: Path,
    data_config_path: Path,
    eodhd_api_token: str | None = None,
    eodhd_transport: HttpTransport | None = None,
    clock: Callable[[], datetime] = _utc_now,
    run_id_factory: Callable[[], str] = _run_id,
) -> MarketEarningsSyncSummary:
    """采集当前市场 Trends 和 watchlist 财报事件并原子发布统一快照。"""
    market_config = load_market_radar_config(market_config_path)
    data_config = load_data_config(data_config_path)
    source = EodhdCalendarSource(
        api_token=eodhd_api_token or "",
        request_timeout_seconds=data_config.historical_data.request_timeout_seconds,
        max_attempts=data_config.historical_data.max_attempts,
        retry_backoff_seconds=data_config.historical_data.retry_backoff_seconds,
        transport=eodhd_transport or download,
    )
    started_at = clock()
    if started_at.tzinfo is None or started_at.utcoffset() is None:
        raise ValueError("market radar clock must return a timezone-aware timestamp")
    capture_date = started_at.astimezone(UTC).date()
    membership = await membership_source.fetch_current_membership()
    if membership.membership_date > capture_date:
        raise ValueError("market membership date cannot be later than the capture date")
    if (capture_date - membership.membership_date).days > 7:
        raise ValueError("market membership snapshot is stale")
    earnings_start = capture_date - timedelta(days=365)
    earnings_end = capture_date + timedelta(days=60)
    data_symbol_to_instrument_id = {
        member.data_symbol: member.instrument_id for member in membership.members
    }
    for watchlist_id in market_config.watchlist:
        mapped_id = data_symbol_to_instrument_id.get(watchlist_id)
        if mapped_id is not None and mapped_id != watchlist_id:
            raise ValueError("watchlist data symbol conflicts with current membership")
        data_symbol_to_instrument_id[watchlist_id] = watchlist_id
    trend_symbols = tuple(data_symbol_to_instrument_id)

    repository = MarketRadarRepository(database_url)
    repository.create_schema()
    run_id = run_id_factory()
    repository.start_sync_run(
        run_id=run_id,
        source="eodhd_earnings",
        started_at_utc=started_at,
        requested_start_date=earnings_start,
        requested_end_date=earnings_end,
        instrument_count=len(trend_symbols),
    )
    instruments_processed = 0
    try:
        classification = await classification_source.fetch_current_sector_classification(membership)
        trend_batch = await source.request_latest_fy1_trends_batched(trend_symbols)
        event_batch = await source.request_earnings(
            market_config.watchlist,
            start=earnings_start,
            end=earnings_end,
        )
        trends = tuple(
            replace(
                trend,
                instrument_id=data_symbol_to_instrument_id[trend.instrument_id],
            )
            for trend in trend_batch.trends
        )
        instruments_processed = len(trend_symbols)
        completed_at = clock()
        if completed_at.tzinfo is None or completed_at.utcoffset() is None:
            raise ValueError("market radar clock must return a timezone-aware timestamp")
        if completed_at.astimezone(UTC).date() != capture_date:
            raise RuntimeError("market earnings sync crossed a UTC capture-date boundary")
        snapshot = calculate_earnings_revision_snapshot(
            as_of_date=capture_date,
            calculated_at_utc=completed_at,
            watchlist=market_config.watchlist,
            membership=membership,
            classification=classification,
            sector_ids=tuple(sector for sector, _ in market_config.sector_etfs),
            trends=trends,
        )
        repository.publish_earnings_bundle_and_complete(
            run_id,
            membership=membership,
            classification=classification,
            trends=trends,
            events=event_batch.events,
            snapshot=snapshot,
            ingested_at_utc=completed_at,
            completed_at_utc=completed_at,
            instruments_processed=instruments_processed,
        )
        return MarketEarningsSyncSummary(
            run_id=run_id,
            status="COMPLETE",
            instrument_count=len(trend_symbols),
            membership_source=membership.source,
            membership_date=membership.membership_date,
            market_member_count=len(membership.members),
            classification_source=classification.source,
            classification_record_count=classification.source_record_count,
            classified_member_count=classification.classified_member_count,
            unclassified_member_count=classification.unclassified_member_count,
            unused_classification_count=classification.unused_source_record_count,
            classification_validity=snapshot.membership.classification_validity,
            classification_coverage_ratio=(snapshot.membership.classification_coverage_ratio),
            trend_batch_count=trend_batch.batch_count,
            trend_records_fetched=trend_batch.raw_record_count,
            trends_selected=len(trend_batch.trends),
            events_fetched=event_batch.raw_record_count,
            snapshot_date=snapshot.as_of_date,
            watchlist_revision_validity=snapshot.watchlist.validity,
            watchlist_revision_coverage_ratio=snapshot.watchlist.coverage_ratio,
            market_revision_validity=snapshot.market.validity,
            market_revision_coverage_ratio=snapshot.market.coverage_ratio,
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
                )
        except Exception as transition_error:
            LOGGER.error(
                "Failed to persist market earnings sync failure: %s",
                type(transition_error).__name__,
            )
        raise
    finally:
        repository.close()


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
