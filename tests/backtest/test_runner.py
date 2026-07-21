"""统一 NT 原生链路的离线端到端测试。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from sqlalchemy import create_engine, text

from tests.data.helpers import make_bar
from trading_assistant.backtest.runner import run_backtest
from trading_assistant.data.catalog import CatalogRepository


def _write_yaml(path: Path, value: object) -> None:
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def _write_test_config(project_root: Path) -> None:
    config = project_root / "config"
    config.mkdir()
    instruments = [
        {
            "symbol": symbol,
            "instrument_id": f"{symbol}.ARCA",
            "exchange": "SMART",
            "primary_exchange": "ARCA",
            "currency": "USD",
        }
        for symbol in ("SPY", "BIL")
    ]
    _write_yaml(config / "instruments.yaml", {"instruments": instruments})
    _write_yaml(
        config / "data.yaml",
        {
            "historical_data": {
                "history_years": 5,
                "bar_type_suffix": "1-DAY-LAST-EXTERNAL",
                "use_regular_trading_hours": True,
                "chunk_days": 365,
                "request_interval_seconds": 0,
                "max_attempts": 1,
                "retry_backoff_seconds": [],
                "live_sync_delay_minutes": 30,
                "overlap_days": 10,
                "request_timeout_seconds": 120,
            },
            "quality": {"max_absolute_daily_return": 0.25, "stale_after_days": 5},
        },
    )
    _write_yaml(
        config / "strategies.yaml",
        {
            "strategies": {
                "dual_momentum": {
                    "enabled": True,
                    "approval_mode": "manual",
                    "signal_expiry_hours": 4,
                    "parameters": {
                        "lookback_months": 6,
                        "top_n": 1,
                        "rebalance_frequency": "month_end",
                        "fallback_instrument": "BIL.ARCA",
                    },
                }
            }
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
            }
        },
    )


def _write_test_catalog(path: Path) -> None:
    catalog = CatalogRepository(path)
    catalog.write_instruments(
        [
            TestInstrumentProvider.equity("SPY", "ARCA"),
            TestInstrumentProvider.equity("BIL", "ARCA"),
        ]
    )
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
        bars.append(
            make_bar(
                session,
                instrument_id="SPY.ARCA",
                open_price=spy_close - 1,
                high=spy_close + 1,
                low=spy_close - 2,
                close=spy_close,
            )
        )
        bars.append(
            make_bar(
                session,
                instrument_id="BIL.ARCA",
                open_price=90,
                high=91,
                low=89,
                close=90,
            )
        )
    assert catalog.append_new_bars(bars) == len(bars)


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
    assert report.summary["commission_per_share_usd"] == 0.005
    assert (report.report_directory / "summary.json").is_file()
    assert (report.report_directory / "fills.csv").is_file()
    assert (report.report_directory / "returns.csv").is_file()
    with create_engine(database_url).connect() as connection:
        signal_time, fill_time = connection.execute(
            text(
                "SELECT s.timestamp_utc, f.timestamp_utc "
                "FROM signals s JOIN fills f ON f.event_id = s.event_id "
                "ORDER BY f.timestamp_utc LIMIT 1"
            )
        ).one()
    assert fill_time > signal_time
