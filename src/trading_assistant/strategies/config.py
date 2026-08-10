"""活动策略 YAML 配置加载与校验。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

import yaml

ApprovalMode = Literal["manual", "auto"]


@dataclass(frozen=True)
class DualMomentumSettings:
    """双动量策略运行参数。"""

    approval_mode: ApprovalMode
    signal_expiry_hours: int
    lookback_months: int
    top_n: int
    rebalance_frequency: str
    fallback_instrument: str


@dataclass(frozen=True)
class ConfiguredStrategy:
    """一次运行中唯一启用的策略及其已校验参数。"""

    name: str
    settings: DualMomentumSettings


def _mapping(value: object, *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"配置项 {name!r} 必须是映射")
    return value


def load_active_strategy(path: Path) -> ConfiguredStrategy:
    """加载唯一活动策略, 当前只实现双动量。"""
    root = _mapping(yaml.safe_load(path.read_text(encoding="utf-8")), name="root")
    active_strategy = root.get("active_strategy")
    if not isinstance(active_strategy, str) or not active_strategy.strip():
        raise ValueError("配置项 'active_strategy' 必须是非空字符串")
    active_strategy = active_strategy.strip()
    strategies = _mapping(root.get("strategies"), name="strategies")
    if active_strategy not in strategies:
        raise ValueError(f"活动策略配置不存在: {active_strategy}")
    if active_strategy != "dual_momentum":
        raise ValueError(f"不支持的活动策略: {active_strategy}")
    strategy = _mapping(strategies.get(active_strategy), name=active_strategy)
    parameters = _mapping(strategy.get("parameters"), name="parameters")
    try:
        approval_mode = str(strategy["approval_mode"])
        if approval_mode not in {"manual", "auto"}:
            raise ValueError("approval_mode 必须是 manual 或 auto")
        settings = DualMomentumSettings(
            approval_mode=cast("ApprovalMode", approval_mode),
            signal_expiry_hours=int(strategy["signal_expiry_hours"]),
            lookback_months=int(parameters["lookback_months"]),
            top_n=int(parameters["top_n"]),
            rebalance_frequency=str(parameters["rebalance_frequency"]),
            fallback_instrument=str(parameters["fallback_instrument"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"双动量策略配置字段无效: {path}: {exc}") from exc

    if settings.signal_expiry_hours < 1:
        raise ValueError("signal_expiry_hours 必须大于等于 1")
    if settings.lookback_months < 1:
        raise ValueError("lookback_months 必须大于等于 1")
    if settings.top_n < 1:
        raise ValueError("top_n 必须大于等于 1")
    if settings.rebalance_frequency != "month_end":
        raise ValueError("当前策略只支持 rebalance_frequency=month_end")
    return ConfiguredStrategy(name=active_strategy, settings=settings)
