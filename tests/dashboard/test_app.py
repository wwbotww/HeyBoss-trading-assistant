"""Streamlit 看板结构与空数据启动测试。"""

import ast
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest
from nautilus_trader.model.data import CustomData
from streamlit.testing.v1 import AppTest

from trading_assistant.dashboard import app as dashboard_app
from trading_assistant.dashboard.data import (
    AuditViews,
    BacktestReportView,
    DataQualityReportView,
    FactorSnapshotView,
)
from trading_assistant.data.catalog import CatalogRepository
from trading_assistant.data.factor import FACTOR_DATA_TYPE, FactorScoreData
from trading_assistant.storage.repository import (
    PortfolioSnapshot,
    PositionSnapshot,
    SignalWorkflow,
)


def test_dashboard_starts_with_empty_local_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("LIVE_DATABASE_URL", f"sqlite:///{tmp_path}/missing.db")
    monkeypatch.setenv("CATALOG_PATH", str(tmp_path / "missing-catalog"))
    monkeypatch.setenv("REPORT_ROOT", str(tmp_path / "missing-reports"))
    monkeypatch.setenv("DATA_QUALITY_REPORT_ROOT", str(tmp_path / "missing-quality"))
    monkeypatch.delenv("TWS_ACCOUNT", raising=False)

    app = AppTest.from_file(str(project_root / "src/trading_assistant/dashboard/app.py")).run()

    assert not app.exception
    assert app.title[0].value == "HeyBoss"
    assert app.sidebar.radio[0].options == list(dashboard_app.PAGES)
    assert app.sidebar.radio[0].value == "总览"


def test_dashboard_has_no_trading_actions() -> None:
    project_root = Path(__file__).resolve().parents[2]
    path = project_root / "src/trading_assistant/dashboard/app.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    called_attributes = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "submit_order" not in called_attributes
    assert "cancel_order" not in called_attributes
    assert "button" not in called_attributes
    assert "tabs" not in called_attributes


def test_dashboard_theme_is_local_and_dark() -> None:
    project_root = Path(__file__).resolve().parents[2]
    with (project_root / ".streamlit/config.toml").open("rb") as file:
        config = tomllib.load(file)

    assert config["theme"]["base"] == "dark"
    assert config["theme"]["primaryColor"] == "#2DD4BF"
    assert config["browser"]["gatherUsageStats"] is False


def test_status_and_backtest_details_have_safe_business_labels() -> None:
    assert dashboard_app._status_display("FILLED") == "🟢 正常 · FILLED"
    assert dashboard_app._status_display("PENDING") == "🟡 等待 · PENDING"
    assert dashboard_app._status_display("DENIED") == "🔴 异常 · DENIED"
    assert dashboard_app._status_display("EXPIRED") == "⚪ 已结束 · EXPIRED"
    assert dashboard_app._status_display("UNKNOWN") == "记录 · UNKNOWN"
    assert dashboard_app._beijing_time(None) == "—"

    frame = pd.DataFrame.from_records(
        [
            {
                "Unnamed: 0": 0,
                "account_id": "DU123456789",
                "instrument_id": "AAPL.US",
                "status": "FILLED",
                "empty": None,
            }
        ]
    )

    display = dashboard_app._backtest_detail_frame(frame)

    assert display.columns.tolist() == ["账户", "标的", "状态"]
    assert display.iloc[0]["账户"] == "DU1*****789"
    assert display.iloc[0]["状态"] == "🟢 正常 · FILLED"


@pytest.mark.parametrize("page", dashboard_app.PAGES)
def test_each_navigation_page_starts_with_empty_state(
    page: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("LIVE_DATABASE_URL", f"sqlite:///{tmp_path}/missing.db")
    monkeypatch.setenv("CATALOG_PATH", str(tmp_path / "missing-catalog"))
    monkeypatch.setenv("REPORT_ROOT", str(tmp_path / "missing-reports"))
    monkeypatch.setenv("DATA_QUALITY_REPORT_ROOT", str(tmp_path / "missing-quality"))
    monkeypatch.delenv("TWS_ACCOUNT", raising=False)

    app = AppTest.from_file(str(project_root / "src/trading_assistant/dashboard/app.py")).run()
    app.sidebar.radio[0].set_value(page)
    app.run()

    assert not app.exception


def test_strategy_page_renders_nt_factor_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    catalog_path = tmp_path / "catalog"
    catalog = CatalogRepository(catalog_path)
    scores = [
        FactorScoreData(
            canonical_id=f"S{index}.US",
            security_id=f"security-{index}",
            asof_date="2026-08-12",
            score=float(2 - index),
            eligible=True,
            batch_id="delivery:2026-08-12",
            batch_size=2,
            delivery_id="delivery",
            model_release_id="release",
            source_kind="signal_inference",
            ts_event=100,
            ts_init=100,
        )
        for index in range(2)
    ]
    catalog.catalog.write_data([CustomData(FACTOR_DATA_TYPE, score) for score in scores])
    monkeypatch.setenv("LIVE_DATABASE_URL", f"sqlite:///{tmp_path}/missing.db")
    monkeypatch.setenv("CATALOG_PATH", str(catalog_path))
    monkeypatch.setenv("REPORT_ROOT", str(tmp_path / "missing-reports"))
    monkeypatch.delenv("TWS_ACCOUNT", raising=False)

    app = AppTest.from_file(str(project_root / "src/trading_assistant/dashboard/app.py")).run()
    app.sidebar.radio[0].set_value("策略与信号")
    app.run()

    assert not app.exception
    assert len(app.get("vega_lite_chart")) == 1


def test_renders_populated_read_only_views(monkeypatch: pytest.MonkeyPatch) -> None:
    streamlit = MagicMock()
    streamlit.columns.return_value = [MagicMock() for _ in range(4)]
    streamlit.multiselect.side_effect = lambda *args, **kwargs: kwargs["default"]
    streamlit.checkbox.return_value = False
    streamlit.selectbox.return_value = "run-1"
    monkeypatch.setattr(dashboard_app, "st", streamlit)
    snapshot = PortfolioSnapshot(
        timestamp_utc=datetime.now(UTC) - timedelta(minutes=5),
        account_id="IB-DU123456",
        currency="USD",
        net_liquidation=10_000.0,
        free_cash=7_000.0,
        locked_cash=3_000.0,
        positions=(
            PositionSnapshot(
                instrument_id="SPY.ARCA",
                signed_quantity=5.0,
                side="LONG",
                avg_open_price=600.0,
                realized_pnl=1.0,
            ),
        ),
    )
    signals = pd.DataFrame.from_records(
        [
            {
                "timestamp_utc": datetime.now(UTC),
                "event_id": "event-1",
                "strategy_name": "patchtst_e3",
                "instrument_id": "AAPL.US",
                "direction": "LONG",
                "target_weight": 0.5,
                "status": "EXPIRED",
                "reason": "factor signal",
                "risk_summary": "signal expired",
                "planned_orders": "[]",
                "forward_5_sessions": 0.1,
                "forward_10_sessions": 0.2,
                "forward_20_sessions": 0.3,
            }
        ]
    )
    workflow = SignalWorkflow(
        event_id="event-1",
        scope="paper:DU123456",
        strategy_name="patchtst_e3",
        rebalance_key="release:2026-08-12",
        target_weights=(("AAPL.US", 0.25), ("MSFT.US", 0.25), ("NVDA.US", 0.25)),
        reason="factor signal",
        signal_timestamp_utc=datetime.now(UTC) - timedelta(days=1),
        expires_at_utc=datetime.now(UTC),
        status="EXPIRED",
        planned_orders=(),
        risk_summary="signal expired",
        telegram_chat_id=None,
        telegram_message_id=None,
    )
    decisions = pd.DataFrame.from_records(
        [
            {
                "timestamp_utc": datetime.now(UTC),
                "event_id": "event-1",
                "decision": "EXPIRED",
                "reason": "signal expired",
            }
        ]
    )
    orders = pd.DataFrame(
        columns=(
            "timestamp_utc",
            "event_id",
            "instrument_id",
            "client_order_id",
            "status",
            "direction",
            "quantity",
            "reason",
        )
    )
    audit = AuditViews(
        portfolio=snapshot,
        signals=(),
        workflows=(workflow,),
        decisions=decisions,
        orders=orders,
        fills=pd.DataFrame(
            columns=(
                "timestamp_utc",
                "event_id",
                "instrument_id",
                "client_order_id",
                "direction",
                "quantity",
                "price",
                "commission",
            )
        ),
        account_history=pd.DataFrame.from_records(
            [
                {
                    "timestamp_utc": datetime.now(UTC),
                    "net_liquidation": 10_000.0,
                    "free_cash": 7_000.0,
                    "locked_cash": 3_000.0,
                }
            ]
        ),
    )
    factor = FactorSnapshotView(
        asof_date="2026-08-12",
        available_at_utc=datetime.now(UTC),
        batch_id="delivery:2026-08-12",
        batch_size=10,
        delivery_id="delivery",
        model_release_id="release",
        source_kind="signal_inference",
        scores=pd.DataFrame.from_records(
            [
                {
                    "rank": index + 1 if index < 8 else None,
                    "canonical_id": value,
                    "security_id": f"security-{index}",
                    "score": float(10 - index) if index < 8 else None,
                    "eligible": index < 8,
                }
                for index, value in enumerate(
                    (
                        "AAPL.US",
                        "MSFT.US",
                        "NVDA.US",
                        "AMZN.US",
                        "GOOGL.US",
                        "META.US",
                        "JPM.US",
                        "XOM.US",
                        "JNJ.US",
                        "TSLA.US",
                    )
                )
            ]
        ),
    )
    report = BacktestReportView(
        run_id="run-1",
        summary={
            "sharpe_ratio": 1.0,
            "start_date": "2026-01-01",
            "end_date": "2026-01-02",
        },
        returns=pd.DataFrame.from_records(
            [
                {
                    "timestamp_utc": "2026-01-01",
                    "equity": 10_000.0,
                    "cash": 7_000.0,
                    "market_value": 3_000.0,
                },
                {
                    "timestamp_utc": "2026-01-02",
                    "equity": 10_100.0,
                    "cash": 7_000.0,
                    "market_value": 3_100.0,
                },
            ]
        ),
        fills=pd.DataFrame.from_records([{"trade_id": "trade-1"}]),
        orders=pd.DataFrame.from_records([{"client_order_id": "O-1"}]),
        positions=pd.DataFrame.from_records([{"instrument_id": "SPY.ARCA"}]),
        account=pd.DataFrame.from_records([{"currency": "USD"}]),
    )
    corrupt_report = BacktestReportView(
        run_id="corrupt-run",
        summary={},
        returns=pd.DataFrame(),
        fills=pd.DataFrame(),
        orders=pd.DataFrame(),
        positions=pd.DataFrame(),
        account=pd.DataFrame(),
        load_error="invalid summary",
    )
    catalog_overview = pd.DataFrame.from_records(
        [
            {
                "bar_type": "AAPL.US-1-DAY-LAST-INTERNAL",
                "instrument_id": "AAPL.US",
                "source": "INTERNAL",
                "bar_count": 100,
                "first_timestamp_utc": pd.Timestamp("2025-01-01", tz="UTC"),
                "last_timestamp_utc": pd.Timestamp("2026-01-01", tz="UTC"),
            },
            {
                "bar_type": "AAPL.US-1-DAY-LAST-EXTERNAL",
                "instrument_id": "AAPL.US",
                "source": "EXTERNAL",
                "bar_count": 100,
                "first_timestamp_utc": pd.Timestamp("2025-01-01", tz="UTC"),
                "last_timestamp_utc": pd.Timestamp("2026-01-01", tz="UTC"),
            },
        ]
    )
    quality_issues = pd.DataFrame.from_records(
        [
            {
                "timestamp_utc": None,
                "severity": "warning",
                "code": "missing_business_day_candidate",
                "instrument_id": "AAPL.US",
                "message": "missing",
            }
        ]
    )
    quality = DataQualityReportView(
        report_name="data-quality.json",
        generated_at_utc=datetime.now(UTC),
        mode="sync",
        bars_fetched=100,
        bars_written=0,
        corporate_actions_written=0,
        report_entry_count=2,
        error_count=0,
        warning_count=1,
        issues=quality_issues,
        issue_counts=pd.DataFrame.from_records(
            [{"severity": "warning", "code": "missing_business_day_candidate", "count": 1}]
        ),
        instrument_issue_counts=pd.DataFrame.from_records(
            [{"severity": "warning", "instrument_id": "AAPL.US", "count": 1}]
        ),
    )

    dashboard_app._render_overview(audit, factor, stale_seconds=90)
    dashboard_app._render_paper_trading(audit, stale_seconds=90)
    dashboard_app._render_strategy_and_signals(audit, signals, factor)
    dashboard_app._render_backtests((report,))
    dashboard_app._render_backtests((corrupt_report,))
    dashboard_app._render_data_and_system(catalog_overview, factor, quality)

    assert streamlit.warning.called
    assert streamlit.vega_lite_chart.called
    assert streamlit.area_chart.called
    factor_chart = streamlit.vega_lite_chart.call_args.args[0]
    assert len(factor_chart) == 8
    assert (factor_chart["组合状态"] == "目标组合").sum() == 3
    metric_calls = [
        call.args[:2]
        for column in streamlit.columns.return_value
        for call in column.metric.call_args_list
    ]
    assert ("订单事件", 0) in metric_calls
    assert ("成交", 0) in metric_calls
    assert streamlit.dataframe.call_count >= 7
    rendered_frames = [
        call.args[0]
        for call in streamlit.dataframe.call_args_list
        if call.args and isinstance(call.args[0], pd.DataFrame)
    ]
    assert any("问题代码" in frame for frame in rendered_frames)
    assert all("message" not in frame for frame in rendered_frames)


def test_renders_each_empty_state(monkeypatch: pytest.MonkeyPatch) -> None:
    streamlit = MagicMock()
    monkeypatch.setattr(dashboard_app, "st", streamlit)
    empty = AuditViews(
        portfolio=None,
        signals=(),
        workflows=(),
        decisions=pd.DataFrame(),
        orders=pd.DataFrame(),
        fills=pd.DataFrame(),
        account_history=pd.DataFrame(),
    )

    dashboard_app._render_overview(empty, None, stale_seconds=90)
    dashboard_app._render_paper_trading(empty, stale_seconds=90)
    dashboard_app._render_strategy_and_signals(empty, pd.DataFrame(), None)
    dashboard_app._render_backtests(())
    dashboard_app._render_data_and_system(pd.DataFrame(), None, None)

    assert streamlit.info.call_count >= 8
