"""Dashboard 只读数据装配测试。"""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from tests.data.helpers import make_bar
from trading_assistant.dashboard.data import (
    load_audit_views,
    load_backtest_reports,
    mask_account_id,
    signal_review_frame,
)
from trading_assistant.data.catalog import CatalogRepository
from trading_assistant.storage.repository import (
    PositionSnapshotInput,
    SignalReview,
    TradingRepository,
)


def test_calculates_forward_returns_by_available_catalog_sessions(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog"
    catalog = CatalogRepository(catalog_path)
    first_day = date(2026, 1, 1)
    bars = [
        make_bar(
            first_day + timedelta(days=index),
            open_price=100.0 + index,
            high=101.0 + index,
            low=99.0 + index,
            close=100.0 + index,
        )
        for index in range(25)
    ]
    assert catalog.append_new_bars(bars) == 25
    review = SignalReview(
        event_id="event-1",
        timestamp_utc=datetime(2026, 1, 1, 23, 59, 59, tzinfo=UTC),
        strategy_name="dual_momentum",
        instrument_id="SPY.ARCA",
        direction="LONG",
        target_weight=0.5,
        reason="momentum",
        status="DENIED",
        planned_orders=(),
        risk_summary="manual denial",
    )

    frame = signal_review_frame(
        (review,),
        catalog_path=catalog_path,
        bar_type_suffix="1-DAY-LAST-EXTERNAL",
    )

    assert frame.loc[0, "forward_5_sessions"] == pytest.approx(0.05)
    assert frame.loc[0, "forward_10_sessions"] == pytest.approx(0.10)
    assert frame.loc[0, "forward_20_sessions"] == pytest.approx(0.20)


def test_missing_database_is_empty_and_not_created(tmp_path: Path) -> None:
    database = tmp_path / "missing.db"
    views = load_audit_views(
        database_url=f"sqlite:///{database}",
        account_id="IB-DU123",
        scope="paper:DU123",
    )
    assert views.portfolio is None
    assert views.signals == ()
    assert not database.exists()
    assert mask_account_id("IB-DU123456") == "IB-*****456"


def test_loads_backtest_report_files_read_only(tmp_path: Path) -> None:
    report = tmp_path / "run-1"
    report.mkdir()
    (report / "summary.json").write_text(json.dumps({"sharpe_ratio": 1.2}), encoding="utf-8")
    (report / "returns.csv").write_text(
        "timestamp_utc,equity,cash\n2026-01-01,10000,10000\n", encoding="utf-8"
    )
    (report / "fills.csv").write_text("trade_id\n", encoding="utf-8")

    reports = load_backtest_reports(tmp_path)

    assert reports[0].run_id == "run-1"
    assert reports[0].summary["sharpe_ratio"] == 1.2
    assert reports[0].returns.iloc[0]["equity"] == 10000


def test_loads_initialized_audit_database(tmp_path: Path) -> None:
    database = tmp_path / "audit.db"
    repository = TradingRepository(f"sqlite:///{database}")
    repository.create_schema()
    repository.record_portfolio_snapshot(
        timestamp_ns=1,
        account_id="IB-DU123",
        currency="USD",
        net_liquidation=100.0,
        free_cash=90.0,
        locked_cash=10.0,
        positions=(
            PositionSnapshotInput(
                instrument_id="SPY.ARCA",
                signed_quantity=1.0,
                side="LONG",
                avg_open_price=100.0,
                realized_pnl=None,
            ),
        ),
    )
    repository.close()

    views = load_audit_views(
        database_url=f"sqlite:///{database}",
        account_id="IB-DU123",
        scope="paper:DU123",
    )

    assert views.portfolio is not None
    assert views.portfolio.free_cash == 90.0
    assert views.decisions.empty
    assert views.orders.empty
    assert mask_account_id("DU123") == "*****"


def test_empty_catalog_horizons_and_missing_report_root(tmp_path: Path) -> None:
    review = SignalReview(
        event_id="event-cash",
        timestamp_utc=datetime(2026, 1, 1, tzinfo=UTC),
        strategy_name="dual_momentum",
        instrument_id="CASH.USD",
        direction="FLAT",
        target_weight=0.0,
        reason="fallback",
        status="RISK_REJECTED",
        planned_orders=(),
        risk_summary="missing price",
    )

    frame = signal_review_frame(
        (review,),
        catalog_path=tmp_path / "catalog",
        bar_type_suffix="1-DAY-LAST-EXTERNAL",
    )

    assert frame.loc[0, "forward_5_sessions"] is None
    assert load_backtest_reports(tmp_path / "missing") == ()
