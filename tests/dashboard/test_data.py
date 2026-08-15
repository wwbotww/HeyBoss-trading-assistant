"""Dashboard 只读数据装配测试。"""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
from nautilus_trader.model.data import CustomData

from tests.data.helpers import make_bar
from trading_assistant.dashboard.data import (
    load_audit_views,
    load_backtest_reports,
    load_catalog_overview,
    load_factor_snapshot,
    load_latest_data_quality,
    mask_account_id,
    signal_review_frame,
    workflow_summary_frame,
    workflow_timeline,
)
from trading_assistant.data.catalog import CatalogRepository
from trading_assistant.data.factor import FACTOR_DATA_TYPE, FactorScoreData
from trading_assistant.execution.events import TradeSignalEvent
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
    assert views.workflows == ()
    assert views.fills.empty
    assert views.account_history.empty
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
    (report / "orders.csv").write_text("client_order_id,status\nO-1,FILLED\n", encoding="utf-8")
    (report / "positions.csv").write_text(
        "instrument_id,signed_quantity\nSPY.ARCA,1\n", encoding="utf-8"
    )
    (report / "account.csv").write_text("currency,balance\nUSD,10000\n", encoding="utf-8")

    reports = load_backtest_reports(tmp_path)

    assert reports[0].run_id == "run-1"
    assert reports[0].summary["sharpe_ratio"] == 1.2
    assert reports[0].returns.iloc[0]["equity"] == 10000
    assert reports[0].orders.iloc[0]["status"] == "FILLED"
    assert reports[0].positions.iloc[0]["instrument_id"] == "SPY.ARCA"
    assert reports[0].account.iloc[0]["currency"] == "USD"
    assert reports[0].load_error is None


def test_backtest_report_missing_optional_details_is_readable(tmp_path: Path) -> None:
    report = tmp_path / "legacy-run"
    report.mkdir()
    (report / "summary.json").write_text("{}", encoding="utf-8")

    loaded = load_backtest_reports(tmp_path)[0]

    assert loaded.returns.empty
    assert loaded.fills.empty
    assert loaded.orders.empty
    assert loaded.positions.empty
    assert loaded.account.empty


def test_backtest_zero_byte_csv_is_empty_but_valid(tmp_path: Path) -> None:
    report = tmp_path / "zero-fill-run"
    report.mkdir()
    (report / "summary.json").write_text('{"fill_count": 0}', encoding="utf-8")
    (report / "fills.csv").write_text("\n", encoding="utf-8")
    (report / "positions.csv").write_text('""\n', encoding="utf-8")

    loaded = load_backtest_reports(tmp_path)[0]

    assert loaded.load_error is None
    assert loaded.fills.empty
    assert loaded.positions.empty


def test_corrupt_backtest_report_is_listed_as_unreadable(tmp_path: Path) -> None:
    report = tmp_path / "corrupt-run"
    report.mkdir()
    (report / "summary.json").write_text("{not-json", encoding="utf-8")

    loaded = load_backtest_reports(tmp_path)[0]

    assert loaded.run_id == "corrupt-run"
    assert loaded.load_error is not None
    assert loaded.summary == {}


def test_loads_catalog_coverage_by_nt_bar_type(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog"
    catalog = CatalogRepository(catalog_path)
    bars = [
        make_bar(date(2026, 1, 1), bar_type_suffix="1-DAY-LAST-INTERNAL"),
        make_bar(date(2026, 1, 2), bar_type_suffix="1-DAY-LAST-INTERNAL"),
        make_bar(date(2026, 1, 1), bar_type_suffix="1-DAY-LAST-EXTERNAL"),
    ]
    assert catalog.append_new_bars(bars) == 3

    overview = load_catalog_overview(catalog_path)

    assert len(overview) == 2
    assert set(overview["source"]) == {"INTERNAL", "EXTERNAL"}
    internal = overview[overview["source"] == "INTERNAL"].iloc[0]
    assert internal["instrument_id"] == "SPY.ARCA"
    assert internal["bar_count"] == 2
    assert pd.Timestamp(internal["first_timestamp_utc"]).date() == date(2026, 1, 1)
    assert pd.Timestamp(internal["last_timestamp_utc"]).date() == date(2026, 1, 2)


def test_missing_catalog_overview_is_empty_and_not_created(tmp_path: Path) -> None:
    missing = tmp_path / "missing-catalog"

    assert load_catalog_overview(missing).empty
    assert not missing.exists()


def test_loads_and_aggregates_latest_data_quality_report(tmp_path: Path) -> None:
    older = tmp_path / "data-quality-20260101T000000Z.json"
    latest = tmp_path / "data-quality-20260102T000000Z.json"
    older.write_text("{}", encoding="utf-8")
    latest.write_text(
        json.dumps(
            {
                "mode": "sync",
                "generated_at_utc": "2026-01-02T00:00:00Z",
                "bars_fetched": 12,
                "bars_written": 2,
                "corporate_actions_written": 1,
                "issues": [
                    {
                        "code": "fetch_failed",
                        "severity": "error",
                        "instrument_id": "AAPL.US",
                        "timestamp_ns": None,
                        "message": "failed",
                    }
                ],
                "instruments": [
                    {
                        "instrument_id": "MSFT.US",
                        "bar_count": 10,
                        "first_timestamp_ns": 1,
                        "last_timestamp_ns": 2,
                        "issues": [
                            {
                                "code": "missing_business_day_candidate",
                                "severity": "warning",
                                "instrument_id": "MSFT.US",
                                "timestamp_ns": None,
                                "message": "missing",
                            },
                            {
                                "code": "missing_business_day_candidate",
                                "severity": "warning",
                                "instrument_id": "MSFT.US",
                                "timestamp_ns": None,
                                "message": "missing",
                            },
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    loaded = load_latest_data_quality(tmp_path)

    assert loaded is not None
    assert loaded.report_name == latest.name
    assert loaded.load_error is None
    assert loaded.error_count == 1
    assert loaded.warning_count == 2
    assert loaded.report_entry_count == 1
    assert loaded.bars_fetched == 12
    warning = loaded.issue_counts[loaded.issue_counts["severity"] == "warning"].iloc[0]
    assert warning["code"] == "missing_business_day_candidate"
    assert warning["count"] == 2


def test_corrupt_latest_quality_report_does_not_raise(tmp_path: Path) -> None:
    path = tmp_path / "data-quality-20260102T000000Z.json"
    path.write_text("[]", encoding="utf-8")

    loaded = load_latest_data_quality(tmp_path)

    assert loaded is not None
    assert loaded.load_error is not None
    assert loaded.issues.empty
    assert load_latest_data_quality(tmp_path / "missing") is None


@pytest.mark.parametrize(
    "invalid_fields",
    [
        {"issues": {}},
        {"issues": [None]},
        {"issues": [{"code": 1, "severity": "warning", "message": "bad"}]},
        {
            "issues": [
                {
                    "code": "bad",
                    "severity": "warning",
                    "message": "bad",
                    "instrument_id": 1,
                }
            ]
        },
        {
            "issues": [
                {
                    "code": "bad",
                    "severity": "warning",
                    "message": "bad",
                    "timestamp_ns": "bad",
                }
            ]
        },
        {"instruments": [None]},
        {"instruments": [{"instrument_id": 1, "issues": []}]},
        {"bars_fetched": "12"},
    ],
)
def test_invalid_quality_report_fields_are_isolated(
    tmp_path: Path, invalid_fields: dict[str, object]
) -> None:
    payload: dict[str, object] = {
        "mode": "sync",
        "generated_at_utc": "2026-01-02T00:00:00Z",
        "bars_fetched": 0,
        "bars_written": 0,
        "corporate_actions_written": 0,
        "issues": [],
        "instruments": [],
    }
    payload.update(invalid_fields)
    path = tmp_path / "data-quality-20260102T000000Z.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_latest_data_quality(tmp_path)

    assert loaded is not None
    assert loaded.load_error is not None


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
    event = TradeSignalEvent(
        strategy_name="dual_momentum",
        target_weights=(("SPY.ARCA", 0.5),),
        rebalance_key="2026-06",
        reason="momentum",
        expires_at_ns=20_000_000_000,
        ts_event=10_000_000_000,
        ts_init=10_000_000_000,
    )
    workflow, _ = repository.register_signal_workflow(event, scope="paper:DU123")
    repository.record_signal(event)
    repository.record_approval(
        event,
        approval_mode="manual",
        decision="APPROVED",
        reason="operator approved",
        timestamp_ns=11_000_000_000,
    )
    repository.record_order_event(
        signal_event_id=workflow.event_id,
        order_event_id="order-event-1",
        timestamp_ns=12_000_000_000,
        strategy_name=event.strategy_name,
        instrument_id="SPY.ARCA",
        client_order_id="O-1",
        status="ACCEPTED",
        direction="BUY",
        quantity=1.0,
        reason="accepted",
    )
    repository.record_fill(
        run_id=None,
        signal_event_id=workflow.event_id,
        trade_id="paper-trade-1",
        timestamp_ns=13_000_000_000,
        strategy_name=event.strategy_name,
        instrument_id="SPY.ARCA",
        client_order_id="O-1",
        direction="BUY",
        quantity=1.0,
        price=100.0,
        commission=0.1,
    )
    repository.close()

    views = load_audit_views(
        database_url=f"sqlite:///{database}",
        account_id="IB-DU123",
        scope="paper:DU123",
    )

    assert views.portfolio is not None
    assert views.portfolio.free_cash == 90.0
    assert views.workflows[0].event_id == workflow.event_id
    assert views.signals[0].event_id == workflow.event_id
    assert views.fills.iloc[0]["event_id"] == workflow.event_id
    assert views.account_history.iloc[0]["net_liquidation"] == 100.0
    assert views.decisions.iloc[0]["decision"] == "APPROVED"
    assert views.orders.iloc[0]["status"] == "ACCEPTED"
    summary = workflow_summary_frame(views.workflows)
    assert summary.iloc[0]["target_count"] == 1
    timeline = workflow_timeline(views, event_id=workflow.event_id)
    assert timeline["event_type"].tolist() == ["信号", "审批/风控", "订单", "成交"]
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


def test_loads_latest_complete_factor_cross_section(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog"
    catalog = CatalogRepository(catalog_path)
    scores = [
        FactorScoreData(
            canonical_id=f"S{index}.US",
            security_id=f"security-{index}",
            asof_date="2026-08-12",
            score=float(10 - index),
            eligible=index < 8,
            batch_id="delivery:2026-08-12",
            batch_size=10,
            delivery_id="delivery",
            model_release_id="release",
            source_kind="signal_inference",
            ts_event=100,
            ts_init=100,
        )
        for index in range(10)
    ]
    catalog.catalog.write_data([CustomData(FACTOR_DATA_TYPE, score) for score in scores])

    snapshot = load_factor_snapshot(catalog_path)

    assert snapshot is not None
    assert snapshot.asof_date == "2026-08-12"
    assert snapshot.batch_size == 10
    assert len(snapshot.scores) == 10
    assert int(snapshot.scores["eligible"].sum()) == 8
    assert snapshot.scores.loc[0, "canonical_id"] == "S0.US"
    assert snapshot.scores.loc[0, "rank"] == 1
    assert pd.isna(snapshot.scores.loc[8, "score"])


def test_missing_factor_catalog_is_empty_and_not_created(tmp_path: Path) -> None:
    missing = tmp_path / "missing-catalog"

    assert load_factor_snapshot(missing) is None
    assert not missing.exists()
