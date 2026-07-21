"""供 CLI 与 live node 共同调用的 M1 历史同步服务。"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from trading_assistant.data.catalog import CatalogRepository
from trading_assistant.data.config import InstrumentSpec, load_data_config, load_instruments
from trading_assistant.data.ibkr import IbkrHistoricalBarSource
from trading_assistant.data.pipeline import HistoricalDataPipeline, PipelineSummary


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
    log_level: str = "INFO",
) -> PipelineSummary:
    """组装并运行唯一的 M1 同步或离线校验管道。"""
    data_config = load_data_config(data_config_path)
    configured = load_instruments(instruments_config_path)
    instruments = select_instruments(configured, selected_ids)
    catalog = CatalogRepository(catalog_path)
    report_directory = project_root / "reports" / "data-quality"
    if validate_only:
        pipeline = HistoricalDataPipeline(
            config=data_config,
            catalog=catalog,
            report_directory=report_directory,
        )
        return pipeline.validate_catalog(instruments)

    today = datetime.now(tz=UTC).date()
    resolved_end = end_date or today
    resolved_start = start_date or resolved_end - timedelta(
        days=365 * data_config.historical_data.history_years,
    )
    if resolved_start >= resolved_end:
        raise ValueError("start date must be earlier than end date")
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
    )
    return await pipeline.sync(
        instruments,
        start=datetime.combine(resolved_start, time.min),
        end=datetime.combine(resolved_end, time.max),
    )
