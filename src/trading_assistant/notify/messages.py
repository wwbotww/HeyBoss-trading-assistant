"""Telegram 审批卡片与订单回报文本。"""

from __future__ import annotations

from trading_assistant.storage.repository import OrderNotification, SignalWorkflow


def approval_card(workflow: SignalWorkflow) -> str:
    """生成包含计划订单和首次风控结果的审批卡片。"""
    orders = "\n".join(
        (
            f"• {item['side']} {item['instrument_id']} "
            f"{item['quantity']} 股 @ 参考价 {float(item['price']):.2f}"
        )
        for item in workflow.planned_orders
    )
    if not orders:
        orders = "• 无需调仓"
    return (
        "📊 双动量调仓审批\n"
        f"周期: {workflow.rebalance_key}\n"
        f"信号: {workflow.reason}\n"
        f"计划:\n{orders}\n"
        f"风控: {workflow.risk_summary or '未提供'}\n"
        f"到期: {workflow.expires_at_utc.isoformat()}\n"
        f"事件: {workflow.event_id}"
    )


def decided_card(workflow: SignalWorkflow, *, approved: bool) -> str:
    """生成按钮处理后的终态说明。"""
    result = "✅ 已确认, 等待执行网关再次风控" if approved else "❌ 已否决, 不会下单"
    return f"{approval_card(workflow)}\n\n{result}"


def workflow_alert(workflow: SignalWorkflow) -> str:
    """生成风控拒绝或过期工作流提醒。"""
    heading = "⛔ 信号被风控拒绝" if workflow.status == "RISK_REJECTED" else "⌛ 信号已过期"
    return (
        f"{heading}\n"
        f"周期: {workflow.rebalance_key}\n"
        f"信号: {workflow.reason}\n"
        f"说明: {workflow.risk_summary or workflow.status}\n"
        f"事件: {workflow.event_id}\n"
        "不会下单"
    )


def order_notification(notification: OrderNotification) -> str:
    """生成成交或拒单通知。"""
    icon = "✅" if notification.status == "FILLED" else "⚠️"
    return (
        f"{icon} 订单更新: {notification.status}\n"
        f"{notification.direction} {notification.instrument_id} "
        f"{notification.quantity:g} 股\n"
        f"订单: {notification.client_order_id}\n"
        f"说明: {notification.reason}"
    )
