"""回测成交重放和指标测试。"""

from datetime import UTC, datetime

import pandas as pd
import pytest

from trading_assistant.backtest.reporting import build_equity_curve, calculate_summary
from trading_assistant.storage.repository import FillAudit


def _fill(direction: str, *, day: int, price: float) -> FillAudit:
    return FillAudit(
        trade_id=f"trade-{direction}-{day}",
        timestamp_utc=datetime(2025, 1, day, tzinfo=UTC),
        instrument_id="SPY.ARCA",
        client_order_id=f"order-{direction}-{day}",
        direction=direction,
        quantity=1,
        price=price,
        commission=0.01,
    )


def test_replays_buy_and_sell_and_calculates_flat_volatility() -> None:
    closes = pd.DataFrame(
        {"SPY.ARCA": [100.0, 110.0, 110.0]},
        index=pd.date_range("2025-01-01", periods=3, tz="UTC"),
    )
    fills = (_fill("BUY", day=1, price=100), _fill("SELL", day=2, price=110))
    curve = build_equity_curve(closes, fills, starting_balance_usd=1000)
    summary = calculate_summary(
        curve,
        fills,
        starting_balance_usd=1000,
        trading_days_per_year=252,
        risk_free_rate=0,
    )
    assert curve["cash"].iloc[-1] == pytest.approx(1009.98)
    assert summary["fill_count"] == 2
    assert summary["final_equity_usd"] == pytest.approx(1009.98)


def test_rejects_unknown_fill_direction_and_empty_curve() -> None:
    closes = pd.DataFrame(
        {"SPY.ARCA": [100.0]},
        index=pd.date_range("2025-01-01", periods=1, tz="UTC"),
    )
    with pytest.raises(ValueError, match="未知成交方向"):
        build_equity_curve(
            closes,
            (_fill("UNKNOWN", day=1, price=100),),
            starting_balance_usd=1000,
        )
    with pytest.raises(ValueError, match="不能为空"):
        calculate_summary(
            pd.DataFrame(),
            (),
            starting_balance_usd=1000,
            trading_days_per_year=252,
            risk_free_rate=0,
        )
