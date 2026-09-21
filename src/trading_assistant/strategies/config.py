"""活动策略 YAML 配置加载与校验。"""

from __future__ import annotations

import re
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
class PatchTSTFactorSettings:
    """PatchTST 因子策略运行参数。"""

    approval_mode: ApprovalMode
    signal_expiry_hours: int
    top_n: int
    target_gross_exposure: float
    rebalance_frequency: str
    allow_evaluation_predictions: bool
    model_release_id: str | None = None


@dataclass(frozen=True)
class ConfiguredStrategy:
    """一次运行中唯一启用的策略及其已校验参数。"""

    name: str
    settings: DualMomentumSettings | PatchTSTFactorSettings


def _mapping(value: object, *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"配置项 {name!r} 必须是映射")
    return value


def _approval_mode(strategy: dict[str, Any]) -> ApprovalMode:
    value = str(strategy["approval_mode"])
    if value not in {"manual", "auto"}:
        raise ValueError("approval_mode 必须是 manual 或 auto")
    return cast(ApprovalMode, value)


def _load_dual_momentum(
    strategy: dict[str, Any], parameters: dict[str, Any]
) -> DualMomentumSettings:
    try:
        settings = DualMomentumSettings(
            approval_mode=_approval_mode(strategy),
            signal_expiry_hours=int(strategy["signal_expiry_hours"]),
            lookback_months=int(parameters["lookback_months"]),
            top_n=int(parameters["top_n"]),
            rebalance_frequency=str(parameters["rebalance_frequency"]),
            fallback_instrument=str(parameters["fallback_instrument"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"双动量策略配置字段无效: {exc}") from exc
    if settings.signal_expiry_hours < 1:
        raise ValueError("signal_expiry_hours 必须大于等于 1")
    if settings.lookback_months < 1:
        raise ValueError("lookback_months 必须大于等于 1")
    if settings.top_n < 1:
        raise ValueError("top_n 必须大于等于 1")
    if settings.rebalance_frequency != "month_end":
        raise ValueError("双动量只支持 rebalance_frequency=month_end")
    return settings


def _load_patchtst_factor(
    strategy: dict[str, Any], parameters: dict[str, Any]
) -> PatchTSTFactorSettings:
    try:
        allow_evaluation_predictions = parameters["allow_evaluation_predictions"]
        if not isinstance(allow_evaluation_predictions, bool):
            raise ValueError("allow_evaluation_predictions 必须是布尔值")
        settings = PatchTSTFactorSettings(
            approval_mode=_approval_mode(strategy),
            signal_expiry_hours=int(strategy["signal_expiry_hours"]),
            top_n=int(parameters["top_n"]),
            target_gross_exposure=float(parameters["target_gross_exposure"]),
            rebalance_frequency=str(parameters["rebalance_frequency"]),
            allow_evaluation_predictions=allow_evaluation_predictions,
            model_release_id=parameters.get("model_release_id"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"PatchTST 因子策略配置字段无效: {exc}") from exc
    if settings.signal_expiry_hours < 1:
        raise ValueError("signal_expiry_hours 必须大于等于 1")
    if settings.top_n < 1:
        raise ValueError("top_n 必须大于等于 1")
    if not 0 < settings.target_gross_exposure <= 1:
        raise ValueError("target_gross_exposure 必须在 (0, 1] 范围内")
    if settings.rebalance_frequency != "daily":
        raise ValueError("PatchTST 因子策略只支持 rebalance_frequency=daily")
    if settings.model_release_id is not None and (
        not isinstance(settings.model_release_id, str)
        or re.fullmatch(r"[0-9a-f]{64}", settings.model_release_id) is None
    ):
        raise ValueError("model_release_id 必须是固定发布的 SHA-256 标识")
    return settings


def load_active_strategy(path: Path) -> ConfiguredStrategy:
    """加载一次运行中唯一活动策略。"""
    root = _mapping(yaml.safe_load(path.read_text(encoding="utf-8")), name="root")
    active_strategy = root.get("active_strategy")
    if not isinstance(active_strategy, str) or not active_strategy.strip():
        raise ValueError("配置项 'active_strategy' 必须是非空字符串")
    active_strategy = active_strategy.strip()
    strategies = _mapping(root.get("strategies"), name="strategies")
    if active_strategy not in strategies:
        raise ValueError(f"活动策略配置不存在: {active_strategy}")
    if active_strategy not in {"dual_momentum", "patchtst_e3"}:
        raise ValueError(f"不支持的活动策略: {active_strategy}")
    strategy = _mapping(strategies.get(active_strategy), name=active_strategy)
    parameters = _mapping(strategy.get("parameters"), name="parameters")
    settings: DualMomentumSettings | PatchTSTFactorSettings
    try:
        if active_strategy == "dual_momentum":
            settings = _load_dual_momentum(strategy, parameters)
        else:
            settings = _load_patchtst_factor(strategy, parameters)
    except ValueError as exc:
        raise ValueError(f"活动策略配置字段无效: {path}: {exc}") from exc
    return ConfiguredStrategy(name=active_strategy, settings=settings)
