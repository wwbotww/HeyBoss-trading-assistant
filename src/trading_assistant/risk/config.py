"""下单前置风控配置。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class RiskLimits:
    """应用风控阈值。"""

    strategy_capital_usd: float
    max_order_notional_usd: float
    max_instrument_weight: float
    max_daily_new_positions: int
    max_gross_exposure: float
    max_factor_unscorable_fraction: float = 0.0
    max_factor_preserved_price_age_sessions: int = 0


def load_risk_limits(path: Path) -> RiskLimits:
    """从 risk.yaml 加载并校验全部风控阈值。"""
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict) or not isinstance(loaded.get("risk"), dict):
        raise ValueError(f"配置项 'risk' 必须是映射: {path}")
    risk: dict[str, Any] = loaded["risk"]
    try:
        limits = RiskLimits(
            strategy_capital_usd=float(risk["strategy_capital_usd"]),
            max_order_notional_usd=float(risk["max_order_notional_usd"]),
            max_instrument_weight=float(risk["max_instrument_weight"]),
            max_daily_new_positions=int(risk["max_daily_new_positions"]),
            max_gross_exposure=float(risk["max_gross_exposure"]),
            max_factor_unscorable_fraction=float(risk.get("max_factor_unscorable_fraction", 0)),
            max_factor_preserved_price_age_sessions=int(
                risk.get("max_factor_preserved_price_age_sessions", 0)
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"风控配置字段无效: {path}: {exc}") from exc

    if limits.strategy_capital_usd <= 0:
        raise ValueError("strategy_capital_usd 必须为正数")
    if limits.max_order_notional_usd <= 0:
        raise ValueError("max_order_notional_usd 必须为正数")
    if not 0 < limits.max_instrument_weight <= 1:
        raise ValueError("max_instrument_weight 必须在 (0, 1] 范围内")
    if limits.max_daily_new_positions < 1:
        raise ValueError("max_daily_new_positions 必须大于等于 1")
    if not 0 < limits.max_gross_exposure <= 1:
        raise ValueError("max_gross_exposure 必须在 (0, 1] 范围内")
    if not 0 <= limits.max_factor_unscorable_fraction <= 1:
        raise ValueError("max_factor_unscorable_fraction 必须在 [0, 1] 范围内")
    if limits.max_factor_preserved_price_age_sessions < 0:
        raise ValueError("max_factor_preserved_price_age_sessions 不得为负")
    return limits
