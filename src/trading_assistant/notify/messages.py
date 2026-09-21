"""Telegram 审批卡片与订单回报文本。"""

from __future__ import annotations

from datetime import UTC, datetime

from trading_assistant.storage.repository import (
    FactorDecisionAudit,
    OrderNotification,
    SignalWorkflow,
)


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
    factor = workflow.factor_context
    opening = datetime.fromtimestamp(workflow.not_before_ns / 1e9, tz=UTC).isoformat()
    extra = (
        ""
        if factor is None
        else (
            f"因子日期: {factor.asof_date}\n"
            f"模型发布: {factor.model_release_id}\n"
            f"保护持仓: {', '.join(workflow.preserve_positions) or '无'}\n"
            f"最早执行: {opening}\n"
            "保护标的按执行时数量保持; 未持有则不开仓\n"
        )
    )
    return (
        ("📊 因子调仓审批\n" if factor is not None else "📊 双动量调仓审批\n")
        + extra
        + f"周期: {workflow.rebalance_key}\n"
        f"信号: {workflow.reason}\n"
        f"计划:\n{orders}\n"
        f"风控: {workflow.risk_summary or '未提供'}\n"
        f"到期: {workflow.expires_at_utc.isoformat()}\n"
        f"事件: {workflow.event_id}"
    )


def factor_alert(decision: FactorDecisionAudit) -> str:
    """缺分或缺批次也有通知依据, 无需伪造交易信号。"""
    heading = "✅ 因子输入已恢复" if decision.recovered_at is not None else "⏸ 因子调仓已跳过"
    release = "未接收到批次" if decision.context is None else decision.context.model_release_id
    return (
        f"{heading}\n日期: {decision.asof_date}\n原因: {decision.reason}\n"
        f"模型发布: {release}\n"
        f"保护标的: {', '.join(decision.preserve_positions) or '无'}\n"
        "仅此条状态变化不会产生订单"
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
