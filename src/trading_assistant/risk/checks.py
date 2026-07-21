"""与运行环境无关的风控计算。"""

from __future__ import annotations

from trading_assistant.risk.config import RiskLimits


def effective_strategy_equity(
    *,
    account_equity_usd: float,
    strategy_capital_usd: float,
) -> float:
    """用实际账户权益与策略资金上限中的较小值作为统一仓位基数。"""
    return min(account_equity_usd, strategy_capital_usd)


def apply_weight_limits(
    requested_weights: dict[str, float],
    limits: RiskLimits,
) -> dict[str, float]:
    """依次应用单标的与总仓位上限并保留现金。不重新放大权重。"""
    capped = {
        instrument_id: min(max(weight, 0.0), limits.max_instrument_weight)
        for instrument_id, weight in requested_weights.items()
        if weight > 0
    }
    gross = sum(capped.values())
    if gross <= limits.max_gross_exposure:
        return capped
    scale = limits.max_gross_exposure / gross
    return {instrument_id: weight * scale for instrument_id, weight in capped.items()}


def order_rejection_reason(
    *,
    notional_usd: float,
    target_weight: float,
    target_gross_exposure: float,
    daily_new_positions: int,
    limits: RiskLimits,
) -> str | None:
    """按配置检查单笔订单并返回首个拒绝理由。"""
    if notional_usd > limits.max_order_notional_usd:
        return "max_order_notional_usd exceeded"
    if target_weight > limits.max_instrument_weight:
        return "max_instrument_weight exceeded"
    if daily_new_positions > limits.max_daily_new_positions:
        return "max_daily_new_positions exceeded"
    if target_gross_exposure > limits.max_gross_exposure:
        return "max_gross_exposure exceeded"
    return None
