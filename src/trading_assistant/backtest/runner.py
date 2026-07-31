"""使用高层 BacktestNode 组装并运行统一 NT 链路。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from uuid import uuid4

from nautilus_trader.analysis.reporter import ReportProvider
from nautilus_trader.backtest.config import (
    BacktestDataConfig,
    BacktestEngineConfig,
    BacktestRunConfig,
    BacktestVenueConfig,
    ImportableFeeModelConfig,
    ImportableFillModelConfig,
    ImportableLatencyModelConfig,
)
from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.common.config import LoggingConfig
from nautilus_trader.config import ImportableActorConfig, ImportableStrategyConfig
from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import AccountId

from trading_assistant.backtest.config import BacktestSettings, load_backtest_settings
from trading_assistant.backtest.reporting import (
    BacktestReport,
    calculate_summary,
    load_nt_equity_curve,
    write_backtest_report,
)
from trading_assistant.data.catalog import CatalogRepository
from trading_assistant.data.config import load_data_config, load_instruments
from trading_assistant.data.corporate_actions import corporate_action_path
from trading_assistant.data.service import select_instruments
from trading_assistant.risk.config import load_risk_limits
from trading_assistant.storage.repository import TradingRepository
from trading_assistant.strategies.config import load_dual_momentum_settings


def _new_run_id(now: datetime) -> str:
    return f"{now.strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"


def _validate_backtest_catalog(
    *,
    catalog_path: Path,
    instrument_ids: tuple[str, ...],
    bar_type_suffixes: tuple[str, ...],
    data_start: date,
    end: date | None,
) -> None:
    """在启动 NT 引擎前验证标的定义和双价格序列均存在。"""
    catalog = CatalogRepository(catalog_path)
    resolved = {
        instrument.id.value
        for instrument in catalog.catalog.instruments(instrument_ids=list(instrument_ids))
    }
    missing_instruments = sorted(set(instrument_ids) - resolved)
    if missing_instruments:
        raise ValueError(f"Catalog 缺少标的定义: {', '.join(missing_instruments)}")
    start_ns = int(datetime.combine(data_start, time.min, tzinfo=UTC).timestamp() * 1_000_000_000)
    end_ns = (
        None
        if end is None
        else int(
            datetime.combine(end + timedelta(days=1), time.min, tzinfo=UTC).timestamp()
            * 1_000_000_000
        )
    )
    missing_bars: list[str] = []
    for instrument_id in instrument_ids:
        for suffix in bar_type_suffixes:
            bar_type = BarType.from_str(f"{instrument_id}-{suffix}")
            if not catalog.read_bars(bar_type, start_ns=start_ns, end_ns=end_ns):
                missing_bars.append(str(bar_type))
    if missing_bars:
        raise ValueError(f"Catalog 在回测区间缺少 BarType: {', '.join(missing_bars)}")


def _build_run_config(
    *,
    catalog_path: Path,
    database_url: str,
    run_id: str,
    instrument_ids: tuple[str, ...],
    signal_bar_type_suffix: str,
    execution_bar_type_suffix: str,
    data_start: date,
    evaluation_start: date,
    end: date | None,
    snapshot_path: Path,
    corporate_action_directory: Path,
    settings: BacktestSettings,
    strategy_path: Path,
    risk_path: Path,
) -> BacktestRunConfig:
    strategy = load_dual_momentum_settings(strategy_path)
    risk = load_risk_limits(risk_path)
    signal_bar_types = tuple(
        f"{instrument_id}-{signal_bar_type_suffix}" for instrument_id in instrument_ids
    )
    execution_bar_types = {
        instrument_id: f"{instrument_id}-{execution_bar_type_suffix}"
        for instrument_id in instrument_ids
    }
    all_bar_types = tuple(dict.fromkeys((*signal_bar_types, *execution_bar_types.values())))
    if any(not instrument_id.endswith(".US") for instrument_id in instrument_ids):
        raise ValueError("回测 canonical instrument_id 必须统一使用 US venue")
    actor = ImportableActorConfig(
        actor_path="trading_assistant.strategies.dual_momentum:DualMomentumActor",
        config_path="trading_assistant.strategies.dual_momentum:DualMomentumActorConfig",
        config={
            "bar_types": list(signal_bar_types),
            "instrument_ids": list(instrument_ids),
            "lookback_months": strategy.lookback_months,
            "top_n": strategy.top_n,
            "fallback_instrument": strategy.fallback_instrument,
            "signal_expiry_hours": strategy.signal_expiry_hours,
            "database_url": database_url,
            "signal_scope": f"backtest:{run_id}",
            "publish_after_ns": int(
                datetime.combine(evaluation_start, time.min, tzinfo=UTC).timestamp() * 1_000_000_000
            ),
        },
    )
    execution = ImportableStrategyConfig(
        strategy_path="trading_assistant.execution.gateway:ExecutionGatewayStrategy",
        config_path="trading_assistant.execution.gateway:ExecutionGatewayConfig",
        config={
            "instrument_routes": {instrument_id: instrument_id for instrument_id in instrument_ids},
            "execution_bar_types": execution_bar_types,
            "approval_mode": "auto",
            "database_url": database_url,
            "account_id": "US-001",
            "strategy_capital_usd": risk.strategy_capital_usd,
            "max_order_notional_usd": risk.max_order_notional_usd,
            "max_instrument_weight": risk.max_instrument_weight,
            "max_daily_new_positions": risk.max_daily_new_positions,
            "max_gross_exposure": risk.max_gross_exposure,
            "backtest_run_id": run_id,
        },
    )
    fee_model = ImportableFeeModelConfig(
        fee_model_path="trading_assistant.backtest.fees:PerShareFeeModel",
        config_path="trading_assistant.backtest.fees:PerShareFeeModelConfig",
        config={"commission_per_share_usd": settings.commission_per_share_usd},
    )
    fill_model = ImportableFillModelConfig(
        fill_model_path="nautilus_trader.backtest.models:OneTickSlippageFillModel",
        config_path="nautilus_trader.backtest.config:FillModelConfig",
        config={},
    )
    latency_model = ImportableLatencyModelConfig(
        latency_model_path="nautilus_trader.backtest.models:LatencyModel",
        config_path="nautilus_trader.backtest.config:LatencyModelConfig",
        config={
            "base_latency_nanos": 0,
            "insert_latency_nanos": settings.bar_availability_delay_ns,
            "update_latency_nanos": 0,
            "cancel_latency_nanos": 0,
        },
    )
    venue_configs = [
        BacktestVenueConfig(
            name="US",
            oms_type="NETTING",
            account_type="CASH",
            starting_balances=[f"{settings.starting_balance_usd:.2f} USD"],
            base_currency="USD",
            fee_model=fee_model,
            fill_model=fill_model,
            latency_model=latency_model,
            allow_cash_borrowing=False,
            modules=[
                ImportableActorConfig(
                    actor_path=("trading_assistant.backtest.dividends:DividendSimulationModule"),
                    config_path=("trading_assistant.backtest.dividends:DividendSimulationConfig"),
                    config={
                        "corporate_action_directory": str(corporate_action_directory),
                        "instrument_ids": list(instrument_ids),
                        "execution_bar_types": execution_bar_types,
                        "account_id": "US-001",
                        "snapshot_path": str(snapshot_path),
                        "currency": "USD",
                    },
                )
            ],
        )
    ]
    data = BacktestDataConfig(
        catalog_path=str(catalog_path),
        data_cls="nautilus_trader.model.data:Bar",
        instrument_ids=list(instrument_ids),
        bar_types=list(all_bar_types),
        start_time=data_start.isoformat(),
        end_time=None if end is None else (end + timedelta(days=1)).isoformat(),
        optimize_file_loading=True,
    )
    engine = BacktestEngineConfig(
        actors=[actor],
        strategies=[execution],
        logging=LoggingConfig(log_level="ERROR"),
    )
    return BacktestRunConfig(
        venues=venue_configs,
        data=[data],
        engine=engine,
        raise_exception=True,
        dispose_on_completion=False,
    )


def run_backtest(
    *,
    project_root: Path,
    catalog_path: Path,
    database_url: str,
    selected_ids: Sequence[str] = (),
    data_start: date | None = None,
    evaluation_start: date | None = None,
    end: date | None = None,
) -> BacktestReport:
    """执行 M2 回测并写出审计记录与报告。"""
    now = datetime.now(UTC)
    run_id = _new_run_id(now)
    configured = load_instruments(project_root / "config" / "instruments.yaml")
    instruments = select_instruments(configured, tuple(selected_ids))
    instrument_ids = tuple(item.canonical_id for item in instruments)
    data_config = load_data_config(project_root / "config" / "data.yaml")
    settings = load_backtest_settings(
        project_root / "config" / "backtest.yaml", project_root=project_root
    )
    effective_data_start = data_start or settings.data_start
    effective_evaluation_start = evaluation_start or settings.evaluation_start
    effective_end = end if end is not None else settings.end
    if effective_evaluation_start < effective_data_start:
        raise ValueError("evaluation_start 不得早于 data_start")
    if effective_end is not None and effective_end < effective_evaluation_start:
        raise ValueError("end 不得早于 evaluation_start")
    _validate_backtest_catalog(
        catalog_path=catalog_path,
        instrument_ids=instrument_ids,
        bar_type_suffixes=(
            data_config.historical_data.signal_bar_type_suffix,
            data_config.historical_data.execution_bar_type_suffix,
        ),
        data_start=effective_data_start,
        end=effective_end,
    )
    repository = TradingRepository(database_url)
    repository.create_schema()
    repository.start_backtest_run(run_id, now)
    report_directory = settings.report_root / run_id
    snapshot_path = report_directory / "nt-equity.json"
    run_config = _build_run_config(
        catalog_path=catalog_path.resolve(),
        database_url=database_url,
        run_id=run_id,
        instrument_ids=instrument_ids,
        signal_bar_type_suffix=data_config.historical_data.signal_bar_type_suffix,
        execution_bar_type_suffix=data_config.historical_data.execution_bar_type_suffix,
        data_start=effective_data_start,
        evaluation_start=effective_evaluation_start,
        end=effective_end,
        snapshot_path=snapshot_path,
        corporate_action_directory=corporate_action_path(catalog_path),
        settings=settings,
        strategy_path=project_root / "config" / "strategies.yaml",
        risk_path=project_root / "config" / "risk.yaml",
    )
    node = BacktestNode([run_config])
    try:
        node.run()
        all_fills = repository.list_fills(run_id)
        fills = tuple(
            fill
            for fill in all_fills
            if fill.timestamp_utc.date() >= effective_evaluation_start
            and (effective_end is None or fill.timestamp_utc.date() <= effective_end)
        )
        curve = load_nt_equity_curve(
            snapshot_path,
            evaluation_start=effective_evaluation_start,
            end=effective_end,
        )
        summary = calculate_summary(
            curve,
            fills,
            trading_days_per_year=settings.trading_days_per_year,
            risk_free_rate=settings.risk_free_rate,
        )
        engines = node.get_engines()
        if len(engines) != 1:
            raise RuntimeError(f"Expected one backtest engine, received {len(engines)}")
        engine = engines[0]
        account = engine.cache.account(AccountId("US-001"))
        if account is None:
            raise RuntimeError("Backtest account US-001 was not found")
        result = engine.get_result()
        summary.update(
            {
                "run_id": run_id,
                "strategy": "dual_momentum",
                "signal_bar_type_suffix": (data_config.historical_data.signal_bar_type_suffix),
                "execution_bar_type_suffix": (
                    data_config.historical_data.execution_bar_type_suffix
                ),
                "data_start": effective_data_start.isoformat(),
                "evaluation_start": effective_evaluation_start.isoformat(),
                "end": None if effective_end is None else effective_end.isoformat(),
                "commission_per_share_usd": float(settings.commission_per_share_usd),
                "slippage_ticks": settings.slippage_ticks,
                "bar_availability_delay_ns": settings.bar_availability_delay_ns,
                "starting_balance_usd": settings.starting_balance_usd,
                "nt_stats_returns": result.stats_returns,
                "nt_stats_pnls": result.stats_pnls,
            }
        )
        report = write_backtest_report(
            report_directory=report_directory,
            curve=curve,
            fills=fills,
            summary=summary,
            orders=ReportProvider.generate_orders_report(engine.cache.orders()),
            positions=ReportProvider.generate_positions_report(
                engine.cache.positions(),
                engine.cache.position_snapshots(),
            ),
            account=ReportProvider.generate_account_report(account),
        )
        repository.complete_backtest_run(
            run_id,
            completed_at=datetime.now(UTC),
            status="COMPLETED",
            summary=summary,
        )
        return report
    except Exception as exc:
        repository.complete_backtest_run(
            run_id,
            completed_at=datetime.now(UTC),
            status="FAILED",
            summary={"error": str(exc)},
        )
        raise
    finally:
        node.dispose()  # type: ignore[no-untyped-call]
        repository.close()
