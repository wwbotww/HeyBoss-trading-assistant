"""NT 状态权益曲线与指标测试。"""

from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest

from trading_assistant.backtest.reporting import (
    calculate_summary,
    load_nt_equity_curve,
    write_backtest_report,
)
from trading_assistant.storage.repository import FillAudit


def _fill(direction: str, *, day: int, price: float) -> FillAudit:
    return FillAudit(
        trade_id=f"trade-{direction}-{day}",
        timestamp_utc=datetime(2025, 1, day, tzinfo=UTC),
        instrument_id="SPY.US",
        client_order_id=f"order-{direction}-{day}",
        direction=direction,
        quantity=1,
        price=price,
        commission=0.01,
    )


def test_loads_evaluation_window_and_calculates_summary(tmp_path: Path) -> None:
    snapshot = tmp_path / "nt-equity.json"
    snapshot.write_text(
        """
[
  {"timestamp_ns": 1, "session_date": "2024-12-31", "equity": 990,
   "cash": 990, "market_value": 0, "dividend_cashflow": 0},
  {"timestamp_ns": 2, "session_date": "2025-01-01", "equity": 1000,
   "cash": 900, "market_value": 100, "dividend_cashflow": 0},
  {"timestamp_ns": 3, "session_date": "2025-01-02", "equity": 1010,
   "cash": 1010, "market_value": 0, "dividend_cashflow": 1}
]
""",
        encoding="utf-8",
    )
    curve = load_nt_equity_curve(
        snapshot,
        evaluation_start=date(2025, 1, 1),
        end=date(2025, 1, 2),
    )
    fills = (_fill("BUY", day=1, price=100), _fill("SELL", day=2, price=110))
    summary = calculate_summary(
        curve,
        fills,
        trading_days_per_year=252,
        risk_free_rate=0,
    )

    assert list(curve.index.date) == [date(2025, 1, 1), date(2025, 1, 2)]
    assert summary["fill_count"] == 2
    assert summary["initial_equity_usd"] == 1000
    assert summary["final_equity_usd"] == 1010
    assert summary["dividend_income_usd"] == 1


@pytest.mark.parametrize(
    "payload",
    [
        "[]",
        "[1]",
        '[{"session_date":1}]',
        '[{"session_date":"bad"}]',
        '[{"session_date":"2025-01-01"}]',
    ],
)
def test_rejects_empty_or_malformed_snapshots(tmp_path: Path, payload: str) -> None:
    snapshot = tmp_path / "nt-equity.json"
    snapshot.write_text(payload, encoding="utf-8")
    with pytest.raises(ValueError, match=r"快照|字段"):
        load_nt_equity_curve(
            snapshot,
            evaluation_start=date(2025, 1, 1),
            end=None,
        )


def test_rejects_window_without_snapshots(tmp_path: Path) -> None:
    snapshot = tmp_path / "nt-equity.json"
    snapshot.write_text(
        """
[
  {"timestamp_ns": 1, "session_date": "2024-12-31", "equity": 1000,
   "cash": 1000, "market_value": 0, "dividend_cashflow": 0}
]
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="正式评估区间"):
        load_nt_equity_curve(
            snapshot,
            evaluation_start=date(2025, 1, 1),
            end=None,
        )


def test_writes_complete_nt_report_bundle(tmp_path: Path) -> None:
    curve = pd.DataFrame(
        {
            "equity": [1000.0],
            "cash": [1000.0],
            "market_value": [0.0],
            "dividend_cashflow": [0.0],
            "daily_return": [0.0],
        },
        index=pd.DatetimeIndex(["2025-01-01"], tz="UTC", name="timestamp_utc"),
    )
    report = write_backtest_report(
        report_directory=tmp_path / "report",
        curve=curve,
        fills=(),
        summary={"fill_count": 0},
        orders=pd.DataFrame(),
        positions=pd.DataFrame(),
        account=pd.DataFrame(),
    )
    assert report.summary == {"fill_count": 0}
    assert {
        "summary.json",
        "fills.csv",
        "returns.csv",
        "orders.csv",
        "positions.csv",
        "account.csv",
    } <= {path.name for path in report.report_directory.iterdir()}


def test_rejects_empty_or_non_positive_equity() -> None:
    with pytest.raises(ValueError, match="不能为空"):
        calculate_summary(
            pd.DataFrame(),
            (),
            trading_days_per_year=252,
            risk_free_rate=0,
        )
    curve = pd.DataFrame(
        {
            "equity": [0.0],
            "cash": [0.0],
            "dividend_cashflow": [0.0],
            "daily_return": [0.0],
        },
        index=pd.date_range("2025-01-01", periods=1, tz="UTC"),
    )
    with pytest.raises(ValueError, match="初始权益"):
        calculate_summary(
            curve,
            (),
            trading_days_per_year=252,
            risk_free_rate=0,
        )
