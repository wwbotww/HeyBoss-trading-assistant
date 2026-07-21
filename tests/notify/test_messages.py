"""Telegram 卡片文本测试。"""

from datetime import UTC, datetime

from trading_assistant.notify.messages import (
    approval_card,
    decided_card,
    order_notification,
    workflow_alert,
)
from trading_assistant.storage.repository import OrderNotification, SignalWorkflow


def _workflow() -> SignalWorkflow:
    return SignalWorkflow(
        event_id="event-1",
        scope="paper:DU123",
        strategy_name="dual_momentum",
        rebalance_key="2026-06",
        target_weights=(("SPY.ARCA", 0.25),),
        reason="momentum",
        signal_timestamp_utc=datetime(2026, 7, 1, tzinfo=UTC),
        expires_at_utc=datetime(2026, 7, 1, 4, tzinfo=UTC),
        status="PENDING",
        planned_orders=(
            {
                "instrument_id": "SPY.ARCA",
                "side": "BUY",
                "quantity": 2,
                "price": 600.0,
            },
        ),
        risk_summary="risk passed",
        telegram_chat_id=None,
        telegram_message_id=None,
    )


def test_formats_approval_and_decision_cards() -> None:
    card = approval_card(_workflow())
    assert "BUY SPY.ARCA 2 股" in card
    assert "risk passed" in card
    assert "等待执行网关再次风控" in decided_card(_workflow(), approved=True)
    assert "不会下单" in decided_card(_workflow(), approved=False)


def test_formats_empty_plan_and_order_terminal() -> None:
    workflow = _workflow()
    empty = SignalWorkflow(**{**workflow.__dict__, "planned_orders": ()})
    assert "无需调仓" in approval_card(empty)
    notification = OrderNotification(
        source_key="order:1",
        event_id="event-1",
        timestamp_utc=datetime(2026, 7, 1, tzinfo=UTC),
        instrument_id="SPY.ARCA",
        client_order_id="O-1",
        status="REJECTED",
        direction="BUY",
        quantity=2,
        reason="rejected",
    )
    assert "⚠️ 订单更新: REJECTED" in order_notification(notification)


def test_formats_workflow_risk_alert() -> None:
    workflow = _workflow()
    rejected = SignalWorkflow(
        **{
            **workflow.__dict__,
            "status": "RISK_REJECTED",
            "risk_summary": "max_order_notional_usd exceeded",
        }
    )
    message = workflow_alert(rejected)
    assert "风控拒绝" in message
    assert "max_order_notional_usd exceeded" in message
    assert "不会下单" in message
