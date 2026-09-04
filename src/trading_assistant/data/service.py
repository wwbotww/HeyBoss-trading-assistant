"""供 CLI 与 live node 共同调用的历史同步服务。"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from trading_assistant.data.catalog import CatalogRepository
from trading_assistant.data.config import InstrumentSpec, load_data_config, load_instruments
from trading_assistant.data.corporate_actions import (
    CorporateActionRepository,
    corporate_action_path,
)
from trading_assistant.data.eodhd import EodhdHistoricalBarSource
from trading_assistant.data.ibkr import IbkrHistoricalBarSource
from trading_assistant.data.pipeline import (
    CatalogWriteMode,
    HistoricalDataPipeline,
    PipelineSummary,
)
from trading_assistant.data.source import HistoricalBarSource


def select_instruments(
    configured: tuple[InstrumentSpec, ...],
    selected_ids: tuple[str, ...],
) -> tuple[InstrumentSpec, ...]:
    """按 ID 筛选配置标的并拒绝未知值。"""
    if not selected_ids:
        return configured
    configured_by_id = {spec.instrument_id: spec for spec in configured}
    unknown = sorted(set(selected_ids) - configured_by_id.keys())
    if unknown:
        raise ValueError(f"unknown instrument IDs: {', '.join(unknown)}")
    return tuple(configured_by_id[value] for value in dict.fromkeys(selected_ids))


async def sync_historical_data(
    *,
    project_root: Path,
    catalog_path: Path,
    instruments_config_path: Path,
    data_config_path: Path,
    start_date: date | None,
    end_date: date | None,
    selected_ids: tuple[str, ...] = (),
    validate_only: bool = False,
    ib_host: str = "127.0.0.1",
    ib_port: int = 4002,
    ib_client_id: int = 1201,
    eodhd_api_token: str | None = None,
    log_level: str = "INFO",
) -> PipelineSummary:
    """组装并运行唯一的同步或离线校验管道。"""
    configured = load_instruments(instruments_config_path)
    instruments = select_instruments(configured, selected_ids)
    return await sync_historical_specs(
        catalog_path=catalog_path,
        report_directory=project_root / "reports" / "data-quality",
        data_config_path=data_config_path,
        instruments=instruments,
        start_date=start_date,
        end_date=end_date,
        validate_only=validate_only,
        ib_host=ib_host,
        ib_port=ib_port,
        ib_client_id=ib_client_id,
        eodhd_api_token=eodhd_api_token,
        log_level=log_level,
    )


async def sync_historical_specs(
    *,
    catalog_path: Path,
    report_directory: Path,
    data_config_path: Path,
    instruments: tuple[InstrumentSpec, ...],
    start_date: date | None,
    end_date: date | None,
    validate_only: bool = False,
    ib_host: str = "127.0.0.1",
    ib_port: int = 4002,
    ib_client_id: int = 1201,
    eodhd_api_token: str | None = None,
    log_level: str = "INFO",
    write_mode: CatalogWriteMode | None = None,
    require_start_coverage: bool = True,
) -> PipelineSummary:
    """同步调用方已解析的标的, 并复用唯一的供应商与 NT Catalog 管道。"""
    if not instruments:
        raise ValueError("at least one instrument is required")
    data_config = load_data_config(data_config_path)
    catalog = CatalogRepository(catalog_path)
    if validate_only:
        pipeline = HistoricalDataPipeline(
            config=data_config,
            catalog=catalog,
            report_directory=report_directory,
        )
        return pipeline.validate_catalog(instruments)

    corporate_actions = CorporateActionRepository(corporate_action_path(catalog_path))
    today = datetime.now(tz=UTC).date()
    resolved_end = end_date or today
    resolved_start = start_date or resolved_end - timedelta(
        days=365 * data_config.historical_data.history_years,
    )
    if resolved_start >= resolved_end:
        raise ValueError("start date must be earlier than end date")
    source: HistoricalBarSource
    if data_config.historical_data.provider == "eodhd":
        source = EodhdHistoricalBarSource(
            api_token=eodhd_api_token or "",
            request_timeout_seconds=data_config.historical_data.request_timeout_seconds,
            max_concurrent_requests=data_config.historical_data.max_concurrent_requests,
        )
    else:
        source = IbkrHistoricalBarSource(
            host=ib_host,
            port=ib_port,
            client_id=ib_client_id,
            use_regular_trading_hours=data_config.historical_data.use_regular_trading_hours,
            request_timeout_seconds=data_config.historical_data.request_timeout_seconds,
            log_level=log_level,
        )
    pipeline = HistoricalDataPipeline(
        config=data_config,
        catalog=catalog,
        report_directory=report_directory,
        source=source,
        corporate_actions=corporate_actions,
    )
    return await pipeline.sync(
        instruments,
        start=datetime.combine(resolved_start, time.min),
        end=datetime.combine(resolved_end, time.max),
        write_mode=write_mode,
        require_start_coverage=require_start_coverage,
    )
