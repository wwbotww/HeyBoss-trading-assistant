"""交易领域事件测试。"""

import pytest
from nautilus_trader.core.message import Event

from trading_assistant.execution.events import TradeSignalEvent


def test_trade_signal_is_native_event_with_immutable_payload() -> None:
    event = TradeSignalEvent(
        strategy_name="dual_momentum",
        target_weights=(("SPY.ARCA", 0.25),),
        rebalance_key="2026-06",
        reason="test",
        expires_at_ns=20,
        ts_event=10,
        ts_init=11,
    )
    assert isinstance(event, Event)
    assert event.target_weights == (("SPY.ARCA", 0.25),)
    assert event.rebalance_key == "2026-06"
    assert event.ts_event == 10
    assert event.ts_init == 11
    with pytest.raises(AttributeError, match="immutable"):
        event._reason = "changed"
