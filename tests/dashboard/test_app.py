"""Streamlit 看板结构与空数据启动测试。"""

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from trading_assistant.dashboard import app as dashboard_app
from trading_assistant.dashboard.data import AuditViews, BacktestReportView
from trading_assistant.storage.repository import PortfolioSnapshot, PositionSnapshot


def test_dashboard_starts_with_empty_local_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("LIVE_DATABASE_URL", f"sqlite:///{tmp_path}/missing.db")
    monkeypatch.setenv("CATALOG_PATH", str(tmp_path / "missing-catalog"))
    monkeypatch.setenv("REPORT_ROOT", str(tmp_path / "missing-reports"))
    monkeypatch.delenv("TWS_ACCOUNT", raising=False)

    app = AppTest.from_file(str(project_root / "src/trading_assistant/dashboard/app.py")).run()

    assert not app.exception
    assert app.title[0].value == "Trading Assistant"


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


def test_renders_populated_read_only_views(monkeypatch: pytest.MonkeyPatch) -> None:
    streamlit = MagicMock()
    streamlit.columns.return_value = [MagicMock() for _ in range(4)]
    streamlit.multiselect.return_value = ["DENIED"]
    streamlit.checkbox.return_value = True
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
                "status": "DENIED",
                "target_weight": 0.5,
                "forward_5_sessions": 0.1,
                "forward_10_sessions": 0.2,
                "forward_20_sessions": 0.3,
            }
        ]
    )
    decisions = pd.DataFrame.from_records([{"decision": "DENIED"}])
    orders = pd.DataFrame.from_records([{"status": "REJECTED"}])
    audit = AuditViews(portfolio=snapshot, signals=(), decisions=decisions, orders=orders)
    report = BacktestReportView(
        run_id="run-1",
        summary={"sharpe_ratio": 1.0},
        returns=pd.DataFrame.from_records(
            [{"timestamp_utc": "2026-01-01", "equity": 10_000.0, "cash": 7_000.0}]
        ),
        fills=pd.DataFrame.from_records([{"trade_id": "trade-1"}]),
    )

    dashboard_app._render_account(audit, stale_seconds=90)
    dashboard_app._render_signals(signals)
    dashboard_app._render_backtests((report,))
    dashboard_app._render_risk_and_orders(audit)

    assert streamlit.warning.called
    assert streamlit.dataframe.call_count >= 5


def test_renders_each_empty_state(monkeypatch: pytest.MonkeyPatch) -> None:
    streamlit = MagicMock()
    monkeypatch.setattr(dashboard_app, "st", streamlit)
    empty = AuditViews(
        portfolio=None,
        signals=(),
        decisions=pd.DataFrame(),
        orders=pd.DataFrame(),
    )

    dashboard_app._render_account(empty, stale_seconds=90)
    dashboard_app._render_signals(pd.DataFrame())
    dashboard_app._render_backtests(())
    dashboard_app._render_risk_and_orders(empty)

    assert streamlit.info.call_count == 5
