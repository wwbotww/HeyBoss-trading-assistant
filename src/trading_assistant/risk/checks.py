"""与运行环境无关的风控计算。"""

from __future__ import annotations

import math
from collections.abc import Mapping

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
    *,
    preserved_weights: Mapping[str, float] | None = None,
) -> dict[str, float]:
    """依次应用单标的与总仓位上限并保留现金。不重新放大权重。"""
    preserved = preserved_weights or {}
    if any(
        not math.isfinite(weight) or weight < 0 or weight > limits.max_instrument_weight
        for weight in preserved.values()
    ):
        raise ValueError("preserved position exceeds instrument risk limit")
    preserved_gross = sum(preserved.values())
    if preserved_gross > limits.max_gross_exposure:
        raise ValueError("preserved positions exceed gross risk limit")
    budget = max(
        0.0,
        min(
            sum(max(weight, 0.0) for weight in requested_weights.values()),
            limits.max_gross_exposure,
        )
        - preserved_gross,
    )
    capped = {
        instrument_id: min(max(weight, 0.0), limits.max_instrument_weight)
        for instrument_id, weight in requested_weights.items()
        if weight > 0 and instrument_id not in preserved
    }
    gross = sum(capped.values())
    if gross <= budget:
        return capped
    scale = budget / gross
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
