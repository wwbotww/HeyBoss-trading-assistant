"""统一 NT 原生链路的离线端到端测试。"""

from __future__ import annotations

import multiprocessing
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from nautilus_trader.model.data import CustomData
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from sqlalchemy import create_engine, text

from tests.data.helpers import make_bar, utc_ns
from trading_assistant.backtest.runner import _validate_backtest_catalog, run_backtest
from trading_assistant.data.catalog import CatalogRepository
from trading_assistant.data.config import InstrumentSpec
from trading_assistant.data.corporate_actions import (
    CorporateActionRepository,
    CorporateActions,
    DividendAction,
)
from trading_assistant.data.factor import FACTOR_DATA_TYPE, FactorScoreData


def _write_yaml(path: Path, value: object) -> None:
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def _spec(
    instrument_id: str = "SPY.US",
    *,
    first_trading_date: date | None = None,
) -> InstrumentSpec:
    symbol = instrument_id.split(".", maxsplit=1)[0]
    return InstrumentSpec(
        symbol=symbol,
        instrument_id=instrument_id,
        data_symbol=instrument_id,
        exchange="SMART",
        primary_exchange="ARCA",
        currency="USD",
        price_precision=2,
        price_increment="0.01",
        lot_size=1,
        first_trading_date=first_trading_date,
    )


def _write_test_config(project_root: Path) -> None:
    config = project_root / "config"
    config.mkdir()
    instruments = [
        {
            "symbol": symbol,
            "instrument_id": f"{symbol}.US",
            "live_instrument_id": f"{symbol}.ARCA",
            "data_symbol": f"{symbol}.US",
            "exchange": "SMART",
            "primary_exchange": "ARCA",
            "currency": "USD",
            "price_precision": 2,
            "price_increment": "0.01",
            "lot_size": 1,
        }
        for symbol in ("SPY", "BIL")
    ]
    _write_yaml(config / "instruments.yaml", {"instruments": instruments})
    _write_yaml(
        config / "data.yaml",
        {
            "historical_data": {
                "provider": "eodhd",
                "price_basis": "total_return_adjusted",
                "refresh_mode": "replace",
                "history_years": 5,
                "signal_bar_type_suffix": "1-DAY-LAST-INTERNAL",
                "execution_bar_type_suffix": "1-DAY-LAST-EXTERNAL",
                "use_regular_trading_hours": True,
                "request_window_days": None,
                "request_interval_seconds": 0,
                "max_attempts": 1,
                "retry_backoff_seconds": [],
                "live_sync_delay_minutes": 30,
                "overlap_days": 10,
                "request_timeout_seconds": 120,
                "max_concurrent_requests": 2,
            },
            "quality": {"max_absolute_daily_return": 0.25, "stale_after_days": 5},
        },
    )
    _write_yaml(
        config / "strategies.yaml",
        {
            "active_strategy": "dual_momentum",
            "strategies": {
                "dual_momentum": {
                    "approval_mode": "manual",
                    "signal_expiry_hours": 4,
                    "parameters": {
                        "lookback_months": 6,
                        "top_n": 1,
                        "rebalance_frequency": "month_end",
                        "fallback_instrument": "BIL.US",
                    },
                }
            },
        },
    )
    _write_yaml(
        config / "risk.yaml",
        {
            "risk": {
                "strategy_capital_usd": 10000,
                "max_order_notional_usd": 5000,
                "max_instrument_weight": 0.25,
                "max_daily_new_positions": 1,
                "max_gross_exposure": 0.80,
            }
        },
    )
    _write_yaml(
        config / "backtest.yaml",
        {
            "backtest": {
                "starting_balance_usd": 10000,
                "commission_per_share_usd": 0.005,
                "slippage_ticks": 1,
                "bar_availability_delay_ns": 86399999999999,
                "trading_days_per_year": 252,
                "risk_free_rate": 0.0,
                "report_root": "reports/backtests",
                "data_start": "2025-01-01",
                "evaluation_start": "2025-01-01",
                "end": None,
            }
        },
    )


def _write_test_catalog(path: Path) -> None:
    catalog = CatalogRepository(path)
    catalog.write_instruments(
        [
            TestInstrumentProvider.equity("SPY", "US"),
            TestInstrumentProvider.equity("BIL", "US"),
        ]
    )
    _write_market_data(path)


def _write_factor_test_config(project_root: Path) -> None:
    _write_test_config(project_root)
    instruments = [
        {
            "symbol": symbol,
            "instrument_id": f"{symbol}.US",
            "live_instrument_id": f"{symbol}.ARCA",
            "data_symbol": f"{symbol}.US",
            "exchange": "SMART",
            "primary_exchange": "ARCA",
            "currency": "USD",
            "price_precision": 2,
            "price_increment": "0.01",
            "lot_size": 1,
            "factor_security_id": f"isin:{symbol}",
        }
        for symbol in ("SPY", "BIL")
    ]
    _write_yaml(project_root / "config" / "instruments.yaml", {"instruments": instruments})
    _write_yaml(
        project_root / "config" / "strategies.yaml",
        {
            "active_strategy": "patchtst_e3",
            "strategies": {
                "patchtst_e3": {
                    "approval_mode": "manual",
                    "signal_expiry_hours": 24,
                    "parameters": {
                        "top_n": 1,
                        "target_gross_exposure": 0.25,
                        "rebalance_frequency": "daily",
                        "allow_evaluation_predictions": False,
                    },
                }
            },
        },
    )


def _write_factor_scores(path: Path) -> None:
    catalog = CatalogRepository(path)
    sessions = [date(2025, 1, 31), date(2025, 2, 28), date(2025, 3, 31)]
    values: list[CustomData] = []
    for index, session in enumerate(sessions):
        timestamp_ns = utc_ns(session + timedelta(days=1)) - 1
        for symbol, score in (("SPY", 2.0 + index), ("BIL", 1.0)):
            values.append(
                CustomData(
                    FACTOR_DATA_TYPE,
                    FactorScoreData(
                        canonical_id=f"{symbol}.US",
                        security_id=f"isin:{symbol}",
                        asof_date=session.isoformat(),
                        score=score,
                        eligible=True,
                        batch_id=f"delivery:{session}",
                        batch_size=2,
                        delivery_id="d" * 64,
                        model_release_id="r" * 64,
                        source_kind="signal_inference",
                        ts_event=timestamp_ns,
                        ts_init=timestamp_ns,
                    ),
                )
            )
    catalog.catalog.write_data(values)


def _write_market_data(path: Path) -> None:
    catalog = CatalogRepository(path)
    sessions = [
        date(2025, 1, 31),
        date(2025, 2, 28),
        date(2025, 3, 31),
        date(2025, 4, 30),
        date(2025, 5, 30),
        date(2025, 6, 30),
        date(2025, 7, 31),
        date(2025, 8, 29),
        date(2025, 9, 30),
        date(2025, 10, 31),
    ]
    bars = []
    for index, session in enumerate(sessions):
        spy_close = 100.0 + index * 5
        execution_ts = utc_ns(session + timedelta(days=1)) - 2
        bars.append(
            make_bar(
                session,
                instrument_id="SPY.US",
                open_price=spy_close - 1,
                high=spy_close + 1,
                low=spy_close - 2,
                close=spy_close,
                ts_init=execution_ts,
            )
        )
        bars.append(
            make_bar(
                session,
                instrument_id="BIL.US",
                open_price=90,
                high=91,
                low=89,
                close=90,
                ts_init=execution_ts,
            )
        )
        bars.append(
            make_bar(
                session,
                instrument_id="SPY.US",
                bar_type_suffix="1-DAY-LAST-INTERNAL",
                open_price=spy_close - 1,
                high=spy_close + 1,
                low=spy_close - 2,
                close=spy_close,
            )
        )
        bars.append(
            make_bar(
                session,
                instrument_id="BIL.US",
                bar_type_suffix="1-DAY-LAST-INTERNAL",
                open_price=90,
                high=91,
                low=89,
                close=90,
            )
        )
    assert catalog.append_new_bars(bars) == len(bars)
    actions = CorporateActionRepository(path.with_name(f"{path.name}-actions"))
    assert actions.write(
        CorporateActions(
            instrument_id="SPY.US",
            dividends=(
                DividendAction(
                    ex_date=date(2025, 10, 31),
                    value=Decimal("1.00"),
                    unadjusted_value=Decimal("1.00"),
                    currency="USD",
                ),
            ),
            splits=(),
        )
    )


def _run_factor_backtest_process(
    project_root: str,
    catalog_path: str,
    database_url: str,
) -> None:
    """在独立进程运行第二个 NT BacktestNode。"""
    run_backtest(
        project_root=Path(project_root),
        catalog_path=Path(catalog_path),
        database_url=database_url,
        end=date(2025, 10, 31),
    )


def test_backtest_node_runs_unified_actor_event_strategy_chain(tmp_path: Path) -> None:
    _write_test_config(tmp_path)
    catalog_path = tmp_path / "catalog"
    _write_test_catalog(catalog_path)
    database_url = f"sqlite:///{tmp_path}/audit.db"

    report = run_backtest(
        project_root=tmp_path,
        catalog_path=catalog_path,
        database_url=database_url,
    )

    assert report.summary["fill_count"] >= 1
    assert report.summary["total_commission_usd"] > 0
    assert report.summary["dividend_income_usd"] > 0
    assert report.summary["commission_per_share_usd"] == 0.005
    assert (report.report_directory / "summary.json").is_file()
    assert (report.report_directory / "fills.csv").is_file()
    assert (report.report_directory / "returns.csv").is_file()
    assert (report.report_directory / "account.csv").is_file()
    assert (report.report_directory / "orders.csv").is_file()
    assert (report.report_directory / "positions.csv").is_file()
    with create_engine(database_url).connect() as connection:
        signal_time, fill_time = connection.execute(
            text(
                "SELECT s.timestamp_utc, f.timestamp_utc "
                "FROM signals s JOIN fills f ON f.event_id = s.event_id "
                "ORDER BY f.timestamp_utc LIMIT 1"
            )
        ).one()
    assert fill_time > signal_time


def test_factor_backtest_runs_custom_data_actor_gateway_chain(tmp_path: Path) -> None:
    """FactorScoreData 必须由 BacktestNode 驱动同一个 Actor 与执行网关。"""
    _write_factor_test_config(tmp_path)
    catalog_path = tmp_path / "catalog"
    _write_test_catalog(catalog_path)
    _write_factor_scores(catalog_path)
    database_url = f"sqlite:///{tmp_path}/factor-audit.db"

    process = multiprocessing.get_context("spawn").Process(
        target=_run_factor_backtest_process,
        args=(str(tmp_path), str(catalog_path), database_url),
    )
    process.start()
    process.join(timeout=60)
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)
        raise AssertionError("factor backtest process timed out")
    assert process.exitcode == 0
    with create_engine(database_url).connect() as connection:
        strategy, signal_time, fill_time = connection.execute(
            text(
                "SELECT s.strategy_name, s.timestamp_utc, f.timestamp_utc "
                "FROM signals s JOIN fills f ON f.event_id = s.event_id "
                "ORDER BY f.timestamp_utc LIMIT 1"
            )
        ).one()
    assert strategy == "patchtst_e3"
    assert fill_time > signal_time


def test_backtest_preflight_rejects_missing_instrument_and_bars(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog"
    with pytest.raises(ValueError, match="缺少标的定义"):
        _validate_backtest_catalog(
            catalog_path=catalog_path,
            instruments=(_spec(),),
            bar_type_suffixes=(
                "1-DAY-LAST-INTERNAL",
                "1-DAY-LAST-EXTERNAL",
            ),
            data_start=date(2025, 1, 1),
            end=None,
        )
    catalog = CatalogRepository(catalog_path)
    catalog.write_instruments([TestInstrumentProvider.equity("SPY", "US")])
    with pytest.raises(ValueError, match="缺少 BarType"):
        _validate_backtest_catalog(
            catalog_path=catalog_path,
            instruments=(_spec(),),
            bar_type_suffixes=(
                "1-DAY-LAST-INTERNAL",
                "1-DAY-LAST-EXTERNAL",
            ),
            data_start=date(2025, 1, 1),
            end=date(2025, 12, 31),
        )

    _validate_backtest_catalog(
        catalog_path=catalog_path,
        instruments=(_spec(first_trading_date=date(2026, 1, 1)),),
        bar_type_suffixes=(
            "1-DAY-LAST-INTERNAL",
            "1-DAY-LAST-EXTERNAL",
        ),
        data_start=date(2025, 1, 1),
        end=date(2025, 12, 31),
    )


def test_backtest_rejects_invalid_runtime_window_before_catalog_access(
    tmp_path: Path,
) -> None:
    _write_test_config(tmp_path)
    catalog_path = tmp_path / "missing-catalog"
    database_url = f"sqlite:///{tmp_path}/audit.db"
    with pytest.raises(ValueError, match="evaluation_start"):
        run_backtest(
            project_root=tmp_path,
            catalog_path=catalog_path,
            database_url=database_url,
            data_start=date(2025, 2, 1),
            evaluation_start=date(2025, 1, 1),
        )
    with pytest.raises(ValueError, match="end"):
        run_backtest(
            project_root=tmp_path,
            catalog_path=catalog_path,
            database_url=database_url,
            data_start=date(2025, 1, 1),
            evaluation_start=date(2025, 2, 1),
            end=date(2025, 1, 31),
        )
