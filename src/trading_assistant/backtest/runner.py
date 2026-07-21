"""使用高层 BacktestNode 组装并运行统一 NT 链路。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

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

from trading_assistant.backtest.config import BacktestSettings, load_backtest_settings
from trading_assistant.backtest.reporting import (
    BacktestReport,
    build_equity_curve,
    calculate_summary,
    load_close_prices,
    write_backtest_report,
)
from trading_assistant.data.catalog import CatalogRepository
from trading_assistant.data.config import load_data_config, load_instruments
from trading_assistant.risk.config import load_risk_limits
from trading_assistant.storage.repository import TradingRepository
from trading_assistant.strategies.config import load_dual_momentum_settings


def _new_run_id(now: datetime) -> str:
    return f"{now.strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"


def _build_run_config(
    *,
    catalog_path: Path,
    database_url: str,
    run_id: str,
    instrument_ids: tuple[str, ...],
    bar_type_suffix: str,
    settings: BacktestSettings,
    strategy_path: Path,
    risk_path: Path,
) -> BacktestRunConfig:
    strategy = load_dual_momentum_settings(strategy_path)
    risk = load_risk_limits(risk_path)
    bar_types = tuple(f"{instrument_id}-{bar_type_suffix}" for instrument_id in instrument_ids)
    venues = sorted({instrument_id.rsplit(".", maxsplit=1)[1] for instrument_id in instrument_ids})
    actor = ImportableActorConfig(
        actor_path="trading_assistant.strategies.dual_momentum:DualMomentumActor",
        config_path="trading_assistant.strategies.dual_momentum:DualMomentumActorConfig",
        config={
            "bar_types": list(bar_types),
            "instrument_ids": list(instrument_ids),
            "lookback_months": strategy.lookback_months,
            "top_n": strategy.top_n,
            "fallback_instrument": strategy.fallback_instrument,
            "signal_expiry_hours": strategy.signal_expiry_hours,
            "database_url": database_url,
            "signal_scope": f"backtest:{run_id}",
        },
    )
    execution = ImportableStrategyConfig(
        strategy_path="trading_assistant.execution.gateway:ExecutionGatewayStrategy",
        config_path="trading_assistant.execution.gateway:ExecutionGatewayConfig",
        config={
            "instrument_ids": list(instrument_ids),
            "bar_type_suffix": bar_type_suffix,
            "approval_mode": "auto",
            "database_url": database_url,
            "strategy_capital_usd": risk.strategy_capital_usd,
            "max_order_notional_usd": risk.max_order_notional_usd,
            "max_instrument_weight": risk.max_instrument_weight,
            "max_daily_new_positions": risk.max_daily_new_positions,
            "max_gross_exposure": risk.max_gross_exposure,
            "cash_adjustment_usd": settings.starting_balance_usd * (len(venues) - 1),
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
            name=venue,
            oms_type="NETTING",
            account_type="CASH",
            starting_balances=[f"{settings.starting_balance_usd:.2f} USD"],
            base_currency="USD",
            fee_model=fee_model,
            fill_model=fill_model,
            latency_model=latency_model,
            allow_cash_borrowing=False,
        )
        for venue in venues
    ]
    data = BacktestDataConfig(
        catalog_path=str(catalog_path),
        data_cls="nautilus_trader.model.data:Bar",
        instrument_ids=list(instrument_ids),
        bar_types=list(bar_types),
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
        dispose_on_completion=True,
    )


def run_backtest(
    *,
    project_root: Path,
    catalog_path: Path,
    database_url: str,
) -> BacktestReport:
    """执行 M2 回测并写出审计记录与报告。"""
    now = datetime.now(UTC)
    run_id = _new_run_id(now)
    instruments = load_instruments(project_root / "config" / "instruments.yaml")
    instrument_ids = tuple(sorted(item.instrument_id for item in instruments))
    data_config = load_data_config(project_root / "config" / "data.yaml")
    settings = load_backtest_settings(
        project_root / "config" / "backtest.yaml", project_root=project_root
    )
    repository = TradingRepository(database_url)
    repository.create_schema()
    repository.start_backtest_run(run_id, now)
    run_config = _build_run_config(
        catalog_path=catalog_path.resolve(),
        database_url=database_url,
        run_id=run_id,
        instrument_ids=instrument_ids,
        bar_type_suffix=data_config.historical_data.bar_type_suffix,
        settings=settings,
        strategy_path=project_root / "config" / "strategies.yaml",
        risk_path=project_root / "config" / "risk.yaml",
    )
    node = BacktestNode([run_config])
    try:
        node.run()
        fills = repository.list_fills(run_id)
        bar_types = tuple(
            f"{instrument_id}-{data_config.historical_data.bar_type_suffix}"
            for instrument_id in instrument_ids
        )
        closes = load_close_prices(CatalogRepository(catalog_path), bar_types=bar_types)
        curve = build_equity_curve(
            closes,
            fills,
            starting_balance_usd=settings.starting_balance_usd,
        )
        summary = calculate_summary(
            curve,
            fills,
            starting_balance_usd=settings.starting_balance_usd,
            trading_days_per_year=settings.trading_days_per_year,
            risk_free_rate=settings.risk_free_rate,
        )
        summary.update(
            {
                "run_id": run_id,
                "strategy": "dual_momentum",
                "bar_type_suffix": data_config.historical_data.bar_type_suffix,
                "commission_per_share_usd": float(settings.commission_per_share_usd),
                "slippage_ticks": settings.slippage_ticks,
                "bar_availability_delay_ns": settings.bar_availability_delay_ns,
                "starting_balance_usd": settings.starting_balance_usd,
            }
        )
        report = write_backtest_report(
            report_directory=settings.report_root / run_id,
            curve=curve,
            fills=fills,
            summary=summary,
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
